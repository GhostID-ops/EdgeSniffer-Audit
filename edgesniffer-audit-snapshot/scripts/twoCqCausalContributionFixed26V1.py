#!/usr/bin/env python3
"""Fixed-cohort counterfactual attribution of 2CQ's own market impact."""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import math
import pathlib
import statistics
from typing import Any

from buildPumpExactAlphaResearchV1 import NETWORK_FEE, apply_group
from exactCurveEconomicsV1 import quote_exact_quote_in_buy, quote_exact_token_sell
from sandboxTwoCqAlphaDecompositionV1 import (
    FIRST_SELLABLE_MS,
    MAX_HOLD_MS,
    SOL_QUOTES,
    TWO_CQ,
    aggregate,
    collect_partition,
    event_key,
)
from sandboxPumpExactFollowThroughAtlasV1 import load_prefix
from validatePumpExactEvidenceLedgerV1 import event_fees


SCHEMA = "TWO_CQ_CAUSAL_CONTRIBUTION_FIXED26_V1"
SIZES = (0.5, 1.0)
SCENARIOS = ("A", "B", "C", "D")


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def include_market_event(row: Any, first_buy: Any, scenario: str) -> bool:
    key = event_key(row)
    first_key = event_key(first_buy)
    if scenario == "A":
        return key >= first_key
    if scenario == "B":
        return key >= first_key and key != first_key
    if scenario == "C":
        return key >= first_key and row.event.user != TWO_CQ
    if scenario == "D":
        return key > first_key
    raise ValueError(f"unknown scenario {scenario}")


def grouped_future(events: list[Any], first_buy: Any, scenario: str) -> dict[tuple[int, int, int, str], list[Any]]:
    grouped: dict[tuple[int, int, int, str], list[Any]] = collections.defaultdict(list)
    for row in events:
        if include_market_event(row, first_buy, scenario):
            grouped[(row.arrival_ms, row.slot, row.tx_index, row.signature)].append(row)
    return grouped


def entry_quote(first_buy: Any, size: float, scenario: str) -> tuple[Any, Any, int, int]:
    base = first_buy.event.post_state if scenario == "D" else first_buy.event.pre_state
    gross = int(round(size * 1e9))
    quote = quote_exact_quote_in_buy(base, gross, event_fees(first_buy.event))
    return base, quote, quote.token_out, gross + NETWORK_FEE


def exact_roi(state: Any, inventory: int, fee_event: Any, cost: int) -> tuple[float, Any]:
    sell = quote_exact_token_sell(
        state,
        inventory,
        1,
        event_fees(fee_event),
        cashback_bps=fee_event.cashback_fee_basis_points,
        buyback_share_bps=fee_event.buyback_fee_basis_points,
    )
    proceeds = sell.immediate_quote_out - NETWORK_FEE
    return (proceeds - cost) * 100 / cost, sell


def simulate_frozen_exit(events: list[Any], first_buy: Any, size: float, scenario: str) -> dict[str, Any]:
    try:
        base, own_buy, inventory, cost = entry_quote(first_buy, size, scenario)
    except Exception as error:
        return {"status": "ENTRY_UNSUPPORTED", "reason": str(error)}
    entry_ms = first_buy.arrival_ms
    state, fee_event = own_buy.post_state, first_buy.event
    timeline: list[tuple[int, Any, Any]] = [(entry_ms, state, fee_event)]
    failed = 0
    for key, group in sorted(grouped_future(events, first_buy, scenario).items()):
        if key[0] > entry_ms + MAX_HOLD_MS:
            continue
        try:
            state = apply_group(state, sorted(group, key=lambda row: row.instruction_index))
            fee_event = group[-1].event
            timeline.append((key[0], state, fee_event))
        except Exception:
            failed += 1
    sample_times = sorted({entry_ms + FIRST_SELLABLE_MS, entry_ms + MAX_HOLD_MS, *(x[0] for x in timeline if x[0] >= entry_ms + FIRST_SELLABLE_MS)})
    samples = []
    for sample_ms in sample_times:
        point = max((item for item in timeline if item[0] <= sample_ms), key=lambda item: item[0])
        try:
            roi, quote = exact_roi(point[1], inventory, point[2], cost)
            samples.append((sample_ms, roi, quote))
        except Exception:
            pass
    if not samples:
        return {"status": "EXIT_UNRESOLVED", "counterfactualFailedTransactions": failed}
    outcome, selected = "MAX_HOLD", samples[-1]
    for sample in samples:
        if sample[1] >= 20 or sample[1] <= -20:
            outcome, selected = ("TP" if sample[1] >= 20 else "SL"), sample
            break
    return {
        "status": "EXACT",
        "scenario": scenario,
        "entryUnixMs": entry_ms,
        "tokensReceivedRaw": inventory,
        "entryVirtualSol": base.virtual_sol,
        "entryVirtualToken": base.virtual_token,
        "outcome": outcome,
        "returnPct": selected[1],
        "pnlSol": (selected[2].immediate_quote_out - NETWORK_FEE - cost) / 1e9,
        "exitUnixMs": selected[0],
        "MFE": max(item[1] for item in samples),
        "MAE": min(item[1] for item in samples),
        "counterfactualFailedTransactions": failed,
    }


def simulate_observed_schedule(events: list[Any], first_buy: Any, profile: dict[str, Any], size: float, scenario: str) -> dict[str, Any]:
    try:
        base, own_buy, inventory, cost = entry_quote(first_buy, size, scenario)
    except Exception as error:
        return {"status": "ENTRY_UNSUPPORTED", "reason": str(error)}
    state, fee_event = own_buy.post_state, first_buy.event
    remaining, proceeds, failed = inventory, 0, 0
    grouped = grouped_future(events, first_buy, scenario)
    schedule: dict[tuple[int, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for item in profile["sellFractions"]:
        schedule[(int(item["unixMs"]), str(item["signature"]))].append(item)
    keys = set(grouped)
    keyed_schedule: dict[tuple[int, int, int, str], list[dict[str, Any]]] = collections.defaultdict(list)
    original_keys = {(row.arrival_ms, row.signature): (row.arrival_ms, row.slot, row.tx_index, row.signature) for row in events}
    for schedule_key, items in schedule.items():
        tx_key = original_keys[schedule_key]
        keys.add(tx_key)
        keyed_schedule[tx_key].extend(items)
    executed = []
    for key in sorted(keys):
        for item in keyed_schedule.get(key, []):
            amount = min(remaining, max(1, int(remaining * float(item["fractionOfThenInventory"]))))
            try:
                quote = quote_exact_token_sell(
                    state,
                    amount,
                    1,
                    event_fees(fee_event),
                    cashback_bps=fee_event.cashback_fee_basis_points,
                    buyback_share_bps=fee_event.buyback_fee_basis_points,
                )
            except Exception as error:
                return {"status": "HYPOTHETICAL_SELL_FAILED", "reason": str(error), "executed": executed}
            state = quote.post_state
            proceeds += quote.immediate_quote_out - NETWORK_FEE
            remaining -= amount
            executed.append({"unixMs": key[0], "fraction": item["fractionOfThenInventory"], "amountRaw": amount})
        group = grouped.get(key, [])
        if group:
            try:
                state = apply_group(state, sorted(group, key=lambda row: row.instruction_index))
                fee_event = group[-1].event
            except Exception:
                failed += 1
    if remaining > max(1, int(inventory * 0.001)):
        return {"status": "PARTIAL_SCHEDULE", "remainingRaw": remaining, "executed": executed, "counterfactualFailedTransactions": failed}
    return {
        "status": "EXACT",
        "scenario": scenario,
        "entryVirtualSol": base.virtual_sol,
        "entryVirtualToken": base.virtual_token,
        "tokensReceivedRaw": inventory,
        "returnPct": (proceeds - cost) * 100 / cost,
        "pnlSol": (proceeds - cost) / 1e9,
        "sellCount": len(executed),
        "finalExitUnixMs": executed[-1]["unixMs"],
        "counterfactualFailedTransactions": failed,
    }


def actual_profile(events: list[Any], first_buy: Any) -> dict[str, Any]:
    wallet = [row for row in events if row.event.user == TWO_CQ and event_key(row) >= event_key(first_buy)]
    running = 0
    schedule = []
    for row in wallet:
        if row.event.is_buy:
            running += row.event.token_amount
        else:
            fraction = min(1.0, row.event.token_amount / running) if running else 0.0
            schedule.append({"unixMs": row.arrival_ms, "signature": row.signature, "fractionOfThenInventory": fraction})
            running = max(0, running - row.event.token_amount)
    return {"sellFractions": schedule}


def organic_follow(events: list[Any], first_buy: Any) -> dict[str, Any]:
    windows = ((0, 250), (250, 500), (500, 1000), (1000, 2000))
    output = {}
    for lo, hi in windows:
        rows = [row for row in events if row.event.user != TWO_CQ and lo < row.arrival_ms - first_buy.arrival_ms <= hi]
        buys = [row for row in rows if row.event.is_buy]
        sells = [row for row in rows if not row.event.is_buy]
        buy_sol = sum(row.event.immediate_quote_amount for row in buys) / 1e9
        sell_sol = sum(row.event.immediate_quote_amount for row in sells) / 1e9
        output[f"{lo}_{hi}ms"] = {
            "grossBuySol": buy_sol,
            "grossSellSol": sell_sol,
            "netBuySol": buy_sol - sell_sol,
            "uniqueBuyers": len({row.event.user for row in buys}),
            "sellPressure": sell_sol / (buy_sol + sell_sol) if buy_sol + sell_sol else 0.0,
        }
    return output


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2:
        return None
    ml, mr = statistics.mean(left), statistics.mean(right)
    numerator = sum((x - ml) * (y - mr) for x, y in zip(left, right))
    denominator = math.sqrt(sum((x - ml) ** 2 for x in left) * sum((y - mr) ** 2 for y in right))
    return numerator / denominator if denominator else None


def deltas(rows: list[dict[str, Any]], policy: str, size: str, left: str, right: str) -> dict[str, Any]:
    pairs = []
    for row in rows:
        a, b = row["scenarios"][left][policy][size], row["scenarios"][right][policy][size]
        if a.get("status") == b.get("status") == "EXACT":
            pairs.append((a["returnPct"], b["returnPct"], a["pnlSol"], b["pnlSol"]))
    return {
        "N": len(pairs),
        "meanRoiDeltaPct": statistics.mean(a - b for a, b, _, _ in pairs) if pairs else None,
        "medianRoiDeltaPct": statistics.median(a - b for a, b, _, _ in pairs) if pairs else None,
        "totalPnlDeltaSol": sum(a - b for _, _, a, b in pairs),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=pathlib.Path)
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("manifest", type=pathlib.Path)
    parser.add_argument("baseline_report", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--max-ordinal", type=int, default=3920)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    baseline = json.loads(args.baseline_report.read_text())
    frozen = {row["mint"]: row for row in manifest["rows"]}
    if len(frozen) != 26 or set(frozen) != {row["mint"] for row in baseline["records"]}:
        raise RuntimeError("immutable cohort mismatch")
    prefix, _ = load_prefix(args.research_root, args.max_ordinal)
    partition_by_mint = {row["mint"]: row["partitionKey"] for row in prefix if row["mint"] in frozen}
    reports = {json.loads(path.read_text())["partitionKey"]: json.loads(path.read_text()) for path in (args.evidence_root / "readiness-v1/partitions").glob("*-v4.json")}
    targets_by_partition: dict[str, set[str]] = collections.defaultdict(set)
    for mint, partition in partition_by_mint.items():
        targets_by_partition[partition].add(mint)
    rows = []
    scan = []
    baseline_by_mint = {row["mint"]: row for row in baseline["records"]}
    for partition, targets in sorted(targets_by_partition.items()):
        events_by_mint, _, counters = collect_partition(args.evidence_root, reports[partition], targets)
        scan.append({"partition": partition, "targets": len(targets), "counters": counters})
        for mint in sorted(targets, key=lambda item: frozen[item]["ordinal"]):
            events = [row for row in events_by_mint[mint] if row.event.quote_mint in SOL_QUOTES and not row.event.mayhem_mode]
            first_buy = next(row for row in events if row.signature == frozen[mint]["buySignature"] and row.event.user == TWO_CQ and row.event.is_buy)
            profile = actual_profile(events, first_buy)
            scenarios = {}
            for scenario in SCENARIOS:
                scenarios[scenario] = {
                    "EXIT_A": {str(size): simulate_frozen_exit(events, first_buy, size, scenario) for size in SIZES},
                    "EXIT_B": {str(size): simulate_observed_schedule(events, first_buy, profile, size, scenario) for size in SIZES},
                }
            baseline_row = baseline_by_mint[mint]
            fixed42 = baseline_row["timingFrozenExit"]["42"]
            between = [row for row in events if min(first_buy.arrival_ms, baseline_row["createUnixMs"] + 42) < row.arrival_ms <= max(first_buy.arrival_ms, baseline_row["createUnixMs"] + 42)]
            rows.append({
                "mint": mint,
                "ordinal": frozen[mint]["ordinal"],
                "actual2CqBuySol": first_buy.event.immediate_quote_amount / 1e9,
                "actual2CqEntryAgeMs": first_buy.arrival_ms - baseline_row["createUnixMs"],
                "pre2CqCanonicalRank": baseline_row["actual2Cq"]["canonicalEntryRank"],
                "scenarioDRank": baseline_row["actual2Cq"]["canonicalEntryRank"] + 1,
                "post2CqEntryDeteriorationPct": {
                    str(size): (scenarios["A"]["EXIT_A"][str(size)]["tokensReceivedRaw"] / scenarios["D"]["EXIT_A"][str(size)]["tokensReceivedRaw"] - 1) * 100
                    for size in SIZES if scenarios["A"]["EXIT_A"][str(size)].get("status") == scenarios["D"]["EXIT_A"][str(size)].get("status") == "EXACT"
                },
                "fixed42": fixed42,
                "fixed42ToPre2CqInterveningTransactions": len({row.signature for row in between}),
                "fixed42ToPre2CqInterveningGrossSol": sum(row.event.immediate_quote_amount for row in between) / 1e9,
                "organicFollowThrough": organic_follow(events, first_buy),
                "scenarios": scenarios,
            })
    rows.sort(key=lambda row: row["ordinal"])
    if {row["mint"] for row in rows} != set(frozen):
        raise RuntimeError("replay cohort drift")

    aggregates = {
        scenario: {
            policy: {str(size): aggregate([row["scenarios"][scenario][policy][str(size)] for row in rows]) for size in SIZES}
            for policy in ("EXIT_A", "EXIT_B")
        }
        for scenario in SCENARIOS
    }
    expected = {
        "EXIT_A": baseline["actual2CqTimeFrozenExit"],
        "EXIT_B": baseline["actual2CqTimeWalletSchedule"],
    }
    mismatches = []
    for policy in expected:
        for size in map(str, SIZES):
            for key in ("exactN", "PF", "WR", "expectancy", "pnlSol"):
                actual, prior = aggregates["A"][policy][size][key], expected[policy][size][key]
                if actual is None or prior is None:
                    equal = actual is prior
                else:
                    equal = abs(float(actual) - float(prior)) <= 1e-12
                if not equal:
                    mismatches.append({"policy": policy, "size": size, "metric": key, "actual": actual, "expected": prior})
    if mismatches:
        raise RuntimeError(f"Scenario A baseline reproduction failed: {mismatches}")

    attribution = {
        policy: {
            size: {
                "A_minus_B": deltas(rows, policy, size, "A", "B"),
                "A_minus_C": deltas(rows, policy, size, "A", "C"),
                "A_minus_D": deltas(rows, policy, size, "A", "D"),
            }
            for size in map(str, SIZES)
        }
        for policy in ("EXIT_A", "EXIT_B")
    }
    correlations = {}
    for policy in ("EXIT_A", "EXIT_B"):
        correlations[policy] = {}
        for size in map(str, SIZES):
            usable = [row for row in rows if row["scenarios"]["A"][policy][size].get("status") == row["scenarios"]["B"][policy][size].get("status") == "EXACT"]
            benefit = [row["scenarios"]["A"][policy][size]["returnPct"] - row["scenarios"]["B"][policy][size]["returnPct"] for row in usable]
            buy_size = [row["actual2CqBuySol"] for row in usable]
            ordered = sorted(zip(buy_size, benefit))
            bins = []
            for index in range(4):
                block = ordered[index * len(ordered) // 4:(index + 1) * len(ordered) // 4]
                bins.append({"N": len(block), "mean2CqBuySol": statistics.mean(x for x, _ in block), "meanBenefitRoiPct": statistics.mean(y for _, y in block)})
            correlations[policy][size] = {"N": len(usable), "pearsonBuySizeVsBenefit": pearson(buy_size, benefit), "descriptiveQuartiles": bins}

    profitable_without = []
    for scenario in ("B", "C"):
        for policy in ("EXIT_A", "EXIT_B"):
            for size in map(str, SIZES):
                cell = aggregates[scenario][policy][size]
                profitable_without.append(cell["PF"] is not None and cell["PF"] > 1 and cell["expectancy"] > 0)
    if all(profitable_without):
        verdict = "PRE_2CQ_ALPHA_SURVIVES_WITHOUT_2CQ_IMPACT"
    else:
        shares = []
        for policy in ("EXIT_A", "EXIT_B"):
            for size in map(str, SIZES):
                a = aggregates["A"][policy][size]["pnlSol"]
                c = aggregates["C"][policy][size]["pnlSol"]
                shares.append((a - c) / a if a else 0)
        verdict = "PRE_2CQ_ALPHA_IS_PRIMARILY_2CQ_FRONTRUN_IMPACT" if statistics.median(shares) > 0.75 else "PRE_2CQ_ALPHA_DEPENDS_MATERIALLY_ON_2CQ_BUY"

    report = {
        "schema": SCHEMA,
        "classification": verdict,
        "cohortManifestSha256": sha256(args.manifest),
        "baselineReportSha256": sha256(args.baseline_report),
        "cohortN": 26,
        "scenarioDefinitions": {
            "A": "pre-2CQ entry; complete canonical future",
            "B": "pre-2CQ entry; real initial 2CQ buy removed",
            "C": "pre-2CQ entry; all real 2CQ economic activity removed",
            "D": "entry immediately after real 2CQ buy",
        },
        "baselineReproduction": {"pass": True, "mismatches": []},
        "aggregates": aggregates,
        "attribution": attribution,
        "buySizeDependence": correlations,
        "counterfactualSemantics": "Every retained transaction is re-evaluated atomically with original instruction constraints using the unchanged exact Pump integer engine. A failed counterfactual transaction mutates zero state. The model cannot represent whether a historically absent replacement transaction would have been submitted.",
        "integrity": {"validationRowsInspected": 0, "holdoutRowsInspected": 0, "officialV1Modified": False, "collectorModified": False, "submissions": 0},
        "scan": scan,
        "rows": rows,
    }
    args.output.mkdir(parents=True, exist_ok=False)
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    with gzip.open(args.output / "rows.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    provenance = {
        "schema": SCHEMA + "_PROVENANCE",
        "cohortManifestSha256": sha256(args.manifest),
        "baselineReportSha256": sha256(args.baseline_report),
        "reportSha256": sha256(report_path),
        "validationRowsInspected": 0,
        "holdoutRowsInspected": 0,
    }
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"classification": verdict, "cohortN": 26, "baselineReproduction": "PASS"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
