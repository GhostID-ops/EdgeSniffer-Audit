#!/usr/bin/env python3
"""Decompose 2CQ alpha into entry timing, exit schedule, and launch selection."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pathlib
import statistics
from typing import Any

from buildPumpExactAlphaResearchV1 import (
    NETWORK_FEE,
    apply_group,
    pair_events,
    paths_for_report,
    retain_pump_trade_arrival,
    retain_target_arrival,
)
from exactCurveEconomicsV1 import quote_exact_quote_in_buy, quote_exact_token_sell
from reconcilePumpExactEvidenceLedgerV1 import iter_frames
from sandboxPumpExactFollowThroughAtlasV1 import (
    SOL_QUOTES,
    TWO_CQ,
    causal_controls,
    div,
    load_prefix,
    load_table_controls,
    overlap_coefficient,
    q,
    standardized_effect,
    stats,
    transition_features,
)
from validatePumpExactEvidenceLedgerV1 import event_fees


SCHEMA = "TWO_CQ_ALPHA_DECOMPOSITION_V1"
SCAN_CACHE_SCHEMA = SCHEMA + "_SCAN_CACHE_V4_SIGNATURE_FALLBACK"
TIMING_AGES = (25, 42, 50, 75, 100, 150, 250)
SIZES = (0.5, 1.0)
FIRST_SELLABLE_MS = 500
MAX_HOLD_MS = 120_000


def event_key(row: Any) -> tuple[int, int, int, str, int]:
    return row.arrival_ms, row.slot, row.tx_index, row.signature, row.instruction_index


def collect_partition(evidence_root: pathlib.Path, report: dict[str, Any], targets: set[str]) -> tuple[dict[str, list[Any]], dict[str, int], dict[str, int]]:
    raw_arrival: dict[str, int] = {}
    raw_evidence: dict[str, str] = {}
    signature_arrival: dict[str, int] = {}
    signature_evidence: dict[str, str] = {}
    by_mint: dict[str, list[Any]] = collections.defaultdict(list)
    transaction_fees: dict[str, int] = {}
    finalized: dict[tuple[str, str], tuple[dict[str, Any], bytes]] = {}
    counters = collections.Counter()
    for path in paths_for_report(evidence_root, report):
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            kind, signature = metadata.get("kind"), metadata.get("signature")
            if kind == "transaction" and signature:
                observed_ms = int(metadata.get("receivedAtUnixNs") or 0) // 1_000_000
                evidence_id = str(metadata.get("evidenceId") or "")
                if observed_ms > 0 and evidence_id:
                    signature_arrival.setdefault(signature, observed_ms)
                    signature_evidence.setdefault(signature, evidence_id)
                retain_pump_trade_arrival(raw_arrival, raw_evidence, signature, observed_ms, evidence_id, set(metadata.get("pumpInstructionKinds") or []))
                retain_target_arrival(raw_arrival, raw_evidence, signature, observed_ms, evidence_id, targets.intersection(metadata.get("discoveredMints") or []))
            elif kind == "rpc_finalized_transaction" and signature and targets.intersection(metadata.get("discoveredMints") or []):
                finalized[(signature, str(metadata.get("evidenceId") or ""))] = (metadata, raw)
    # Frame order is not semantic. Resolve observations first, then decode every
    # canonical transaction after all source paths have been indexed.
    for (signature, canonical_evidence_id), (metadata, raw) in finalized.items():
        arrival = raw_arrival.get(signature) or signature_arrival.get(signature)
        if arrival is None:
            counters["missingRawArrival"] += 1
            continue
        try:
            transaction = json.loads(raw).get("result")
            if not transaction or (transaction.get("meta") or {}).get("err") is not None:
                counters["failedOrMissingFinalized"] += 1
                continue
            transaction_fees[signature] = int((transaction.get("meta") or {}).get("fee") or 0)
            observation_evidence_id = raw_evidence.get(signature) or signature_evidence.get(signature, "")
            if signature not in raw_evidence:
                counters["signatureArrivalFallback"] += 1
            decoded = pair_events(transaction, signature, observation_evidence_id, canonical_evidence_id, arrival, int(metadata.get("slot") or 0), int(metadata.get("transactionIndex") or 0), targets)
        except Exception:
            counters["decodeFailure"] += 1
            continue
        for row in decoded:
            by_mint[row.event.mint].append(row)
    for mint in list(by_mint):
        deduped = {(row.signature, row.instruction_index): row for row in by_mint[mint]}
        by_mint[mint] = sorted(deduped.values(), key=event_key)
    return by_mint, transaction_fees, dict(counters)


def exact_roi(state: Any, inventory: int, fee_event: Any, cost: int) -> tuple[float, Any]:
    sell = quote_exact_token_sell(state, inventory, 1, event_fees(fee_event), cashback_bps=fee_event.cashback_fee_basis_points, buyback_share_bps=fee_event.buyback_fee_basis_points)
    proceeds = sell.immediate_quote_out - NETWORK_FEE
    return (proceeds - cost) * 100 / cost, sell


def frozen_policy(events: list[Any], entry_ms: int, size: float, *, anchor: Any | None = None) -> dict[str, Any]:
    if anchor is None:
        prior = [row for row in events if row.arrival_ms <= entry_ms]
        if not prior:
            return {"status": "ENTRY_UNRESOLVED"}
        base_state, fee_event, start_key = prior[-1].event.post_state, prior[-1].event, None
        future = [row for row in events if row.arrival_ms > entry_ms]
    else:
        base_state, fee_event, start_key = anchor.event.pre_state, anchor.event, event_key(anchor)
        # The hypothetical order is inserted immediately before 2CQ's transaction.
        future = [row for row in events if event_key(row) >= start_key]
    gross = int(round(size * 1e9))
    try:
        own_buy = quote_exact_quote_in_buy(base_state, gross, event_fees(fee_event))
    except Exception as error:
        return {"status": "ENTRY_UNSUPPORTED", "reason": str(error)}
    inventory, state, cost = own_buy.token_out, own_buy.post_state, gross + NETWORK_FEE
    grouped: dict[tuple[int, int, int, str], list[Any]] = collections.defaultdict(list)
    for row in future:
        if row.arrival_ms <= entry_ms + MAX_HOLD_MS:
            grouped[(row.arrival_ms, row.slot, row.tx_index, row.signature)].append(row)
    first_sellable = entry_ms + FIRST_SELLABLE_MS
    timeline: list[tuple[int, Any, Any]] = [(entry_ms, state, fee_event)]
    failed = 0
    for key, group in sorted(grouped.items()):
        try:
            state = apply_group(state, sorted(group, key=lambda row: row.instruction_index))
            fee_event = group[-1].event
            timeline.append((key[0], state, fee_event))
        except Exception:
            failed += 1
    sample_times = sorted({first_sellable, entry_ms + MAX_HOLD_MS, *(item[0] for item in timeline if item[0] >= first_sellable)})
    samples: list[tuple[int, float, Any]] = []
    for sample_ms in sample_times:
        point = max((item for item in timeline if item[0] <= sample_ms), key=lambda item: item[0])
        try:
            roi, quote = exact_roi(point[1], inventory, point[2], cost)
            samples.append((sample_ms, roi, quote))
        except Exception:
            pass
    if not samples:
        return {"status": "EXIT_UNRESOLVED"}
    samples.sort(key=lambda item: item[0])
    outcome, selected = "MAX_HOLD", samples[-1]
    for sample in samples:
        if sample[1] >= 20 or sample[1] <= -20:
            outcome, selected = ("TP" if sample[1] >= 20 else "SL"), sample
            break
    return {
        "status": "EXACT", "entryUnixMs": entry_ms, "tokensReceivedRaw": inventory,
        "entryVirtualSol": base_state.virtual_sol, "entryVirtualToken": base_state.virtual_token,
        "ownImpactPct": (own_buy.post_state.virtual_sol / base_state.virtual_sol - 1) * 100,
        "outcome": outcome, "returnPct": selected[1],
        "pnlSol": (selected[2].immediate_quote_out - NETWORK_FEE - cost) / 1e9,
        "exitUnixMs": selected[0], "MFE": max(item[1] for item in samples),
        "MAE": min(item[1] for item in samples), "counterfactualFailedTransactions": failed,
    }


def actual_wallet_profile(events: list[Any], create_ms: int, transaction_fees: dict[str, int]) -> dict[str, Any] | None:
    wallet_events = [row for row in events if row.event.user == TWO_CQ]
    buys = [row for row in wallet_events if row.event.is_buy]
    sells = [row for row in wallet_events if not row.event.is_buy]
    if not buys:
        return None
    first = buys[0]
    buys_ahead = [row for row in events if row.event.is_buy and event_key(row) < event_key(first)]
    bought_tokens = sum(row.event.token_amount for row in buys)
    sold_tokens = sum(row.event.token_amount for row in sells)
    buy_signatures = {row.signature for row in buys}
    sell_signatures = {row.signature for row in sells}
    buy_cost = sum(row.event.immediate_quote_amount for row in buys) + sum(transaction_fees.get(signature, 0) for signature in buy_signatures)
    sell_proceeds = sum(row.event.immediate_quote_amount for row in sells) - sum(transaction_fees.get(signature, 0) for signature in sell_signatures)
    flat = sold_tokens >= bought_tokens * 0.999 if bought_tokens else False
    inventory = 0
    cost = 0
    mfe = mae = None
    first_sell_key = event_key(sells[0]) if sells else None
    for row in events:
        if event_key(row) < event_key(first):
            continue
        if row.event.user == TWO_CQ:
            if row.event.is_buy:
                inventory += row.event.token_amount
                cost += row.event.immediate_quote_amount + (transaction_fees.get(row.signature, 0) if row.signature not in {x.signature for x in buys if event_key(x) < event_key(row)} else 0)
            else:
                inventory = max(0, inventory - row.event.token_amount)
        if first_sell_key is not None and event_key(row) >= first_sell_key:
            break
        if inventory > 0 and cost > 0:
            try:
                roi, _ = exact_roi(row.event.post_state, inventory, row.event, cost)
                mfe = roi if mfe is None else max(mfe, roi)
                mae = roi if mae is None else min(mae, roi)
            except Exception:
                pass
    sell_fractions = []
    running = 0
    wallet_order = sorted(wallet_events, key=event_key)
    for row in wallet_order:
        if row.event.is_buy:
            running += row.event.token_amount
        else:
            before = running
            fraction = min(1.0, div(row.event.token_amount, before)) if before else 0.0
            sell_fractions.append({"unixMs": row.arrival_ms, "fractionOfThenInventory": fraction, "tokensRaw": row.event.token_amount, "signature": row.signature})
            running = max(0, running - row.event.token_amount)
    return {
        "firstBuyUnixMs": first.arrival_ms, "entryAgeMs": first.arrival_ms - create_ms,
        "canonicalEntryRank": len(buys_ahead) + 1, "successfulBuysAhead": len(buys_ahead),
        "solAhead": sum(row.event.immediate_quote_amount for row in buys_ahead) / 1e9,
        "buyCount": len(buys), "sellCount": len(sells),
        "buySol": buy_cost / 1e9, "curveGrossBuySol": sum(row.event.immediate_quote_amount for row in buys) / 1e9,
        "tokensBoughtRaw": bought_tokens, "tokensSoldRaw": sold_tokens,
        "firstBuyTokensRaw": first.event.token_amount,
        "ownImpactPct": (first.event.post_state.virtual_sol / first.event.pre_state.virtual_sol - 1) * 100,
        "capacityUsedPct": div(first.event.token_amount * 100, first.event.pre_state.real_token),
        "firstSellLatencyMs": sells[0].arrival_ms - first.arrival_ms if sells else None,
        "fullExitLatencyMs": sells[-1].arrival_ms - first.arrival_ms if flat and sells else None,
        "sellFractions": sell_fractions, "flatWithinEvidence": flat,
        "actualRealizedPnlSol": (sell_proceeds - buy_cost) / 1e9 if flat else None,
        "actualRealizedRoiPct": (sell_proceeds - buy_cost) * 100 / buy_cost if flat and buy_cost else None,
        "mfeBeforeFirstSellPct": mfe, "maeBeforeFirstSellPct": mae,
        "firstBuySignature": first.signature, "firstBuySlot": first.slot,
    }


def replay_wallet_sell_schedule(events: list[Any], first_buy: Any, profile: dict[str, Any], size: float) -> dict[str, Any]:
    if not profile["sellFractions"]:
        return {"status": "NO_OBSERVED_SELL"}
    gross = int(round(size * 1e9))
    try:
        own_buy = quote_exact_quote_in_buy(first_buy.event.pre_state, gross, event_fees(first_buy.event))
    except Exception as error:
        return {"status": "ENTRY_UNSUPPORTED", "reason": str(error)}
    state, fee_event = own_buy.post_state, first_buy.event
    remaining, cost, proceeds = own_buy.token_out, gross + NETWORK_FEE, 0
    grouped: dict[tuple[int, int, int, str], list[Any]] = collections.defaultdict(list)
    for row in events:
        if event_key(row) >= event_key(first_buy):
            grouped[(row.arrival_ms, row.slot, row.tx_index, row.signature)].append(row)
    schedule_by_signature = collections.defaultdict(list)
    for item in profile["sellFractions"]:
        schedule_by_signature[item["signature"]].append(item)
    failed, executed = 0, []
    for key, group in sorted(grouped.items()):
        for item in schedule_by_signature.get(key[3], []):
            amount = min(remaining, max(1, int(remaining * float(item["fractionOfThenInventory"]))))
            try:
                quote = quote_exact_token_sell(state, amount, 1, event_fees(fee_event), cashback_bps=fee_event.cashback_fee_basis_points, buyback_share_bps=fee_event.buyback_fee_basis_points)
                state = quote.post_state
                proceeds += quote.immediate_quote_out - NETWORK_FEE
                remaining -= amount
                executed.append({"unixMs": key[0], "fraction": item["fractionOfThenInventory"], "amountRaw": amount, "proceedsLamports": quote.immediate_quote_out - NETWORK_FEE})
            except Exception:
                return {"status": "HYPOTHETICAL_SELL_FAILED", "executed": executed}
        try:
            state = apply_group(state, sorted(group, key=lambda row: row.instruction_index))
            fee_event = group[-1].event
        except Exception:
            failed += 1
    if remaining > max(1, int(own_buy.token_out * 0.001)):
        return {"status": "PARTIAL_SCHEDULE", "remainingRaw": remaining, "executed": executed, "counterfactualFailedTransactions": failed}
    return {"status": "EXACT", "returnPct": (proceeds - cost) * 100 / cost, "pnlSol": (proceeds - cost) / 1e9, "sellCount": len(executed), "finalExitUnixMs": executed[-1]["unixMs"], "counterfactualFailedTransactions": failed}


def aggregate(cells: list[dict[str, Any]]) -> dict[str, Any]:
    exact = [cell for cell in cells if cell.get("status") == "EXACT"]
    returns = [float(cell["returnPct"]) / 100 for cell in exact]
    wins, losses = [x for x in returns if x > 0], [x for x in returns if x <= 0]
    return {
        "eligibleN": len(cells), "capacityPassN": len(exact), "exactN": len(exact),
        "statusCounts": dict(collections.Counter(cell.get("status") for cell in cells)),
        "outcomeCounts": dict(collections.Counter(cell.get("outcome") for cell in exact)),
        "WR": div(len(wins), len(exact)), "PF": div(sum(wins), abs(sum(losses))) if losses else None,
        "expectancy": statistics.mean(returns) if returns else None,
        "pnlSol": sum(float(cell["pnlSol"]) for cell in exact),
        "medianRoiPct": statistics.median([cell["returnPct"] for cell in exact]) if exact else None,
        "worstRoiPct": min((cell["returnPct"] for cell in exact), default=None),
    }


def selector_effects(taken: list[dict[str, Any]], ignored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    excluded = {"twoCqBuySeen", "twoCqFirstEntryAgeMs", "curveVirtualSol", "curveVirtualToken", "curveRealSol", "curveRealToken", "grossBuySol", "buyCount", "globalTrades60sPrior", "globalLaunches60sPrior"}
    keys = sorted({
        key
        for row in taken
        for key, value in row.items()
        if key not in excluded
        and isinstance(value, (int, float))
        and all(isinstance(other.get(key), (int, float)) for other in taken)
        and all(isinstance(other.get(key), (int, float)) for other in ignored)
    })
    output = []
    for key in keys:
        left, right = [float(row[key]) for row in taken], [float(row[key]) for row in ignored]
        effect = standardized_effect(left, right)
        chronology = []
        for block in range(4):
            indexes = [i for i in range(len(left)) if min(3, i * 4 // max(len(left), 1)) == block]
            chronology.append(statistics.mean(left[i] - right[i] for i in indexes) if indexes else None)
        nonzero = [value for value in chronology if value not in (None, 0)]
        output.append({"feature": key, "taken": stats(left), "ignored": stats(right), "effect": effect, "overlap": overlap_coefficient(left, right), "chronologicalDifferences": chronology, "consistentBlocks": max(sum(value > 0 for value in nonzero), sum(value < 0 for value in nonzero))})
    return sorted(output, key=lambda item: (item["consistentBlocks"], abs(item["effect"] or 0)), reverse=True)


def selector_feature(events: list[Any], create_ms: int, age: int, controls: dict[tuple[str, int], dict[str, float]], mint: str, *, pre_entry_anchor: Any | None = None) -> dict[str, Any]:
    feature = transition_features(events, create_ms, age)
    feature.update(causal_controls(controls, mint, age))
    if pre_entry_anchor is not None:
        state = pre_entry_anchor.event.pre_state
    else:
        prior = [row for row in events if row.arrival_ms <= create_ms + age]
        state = prior[-1].event.post_state if prior else None
    if state is not None:
        feature.update({"curveVirtualSol": state.virtual_sol / 1e9, "curveVirtualToken": float(state.virtual_token), "curveRealSol": state.real_sol / 1e9, "curveRealToken": float(state.real_token)})
    return feature


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=pathlib.Path)
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--max-ordinal", type=int, required=True)
    parser.add_argument("--cohort-manifest", type=pathlib.Path)
    args = parser.parse_args()
    prefix, ordinals = load_prefix(args.research_root, args.max_ordinal)
    frozen_cohort = None
    if args.cohort_manifest:
        frozen_manifest = json.loads(args.cohort_manifest.read_text())
        frozen_cohort = {str(row["mint"]) for row in frozen_manifest["rows"]}
        if len(frozen_cohort) != len(frozen_manifest["rows"]):
            raise RuntimeError("duplicate mint in frozen cohort manifest")
        prefix = [row for row in prefix if str(row["mint"]) in frozen_cohort]
        missing = frozen_cohort - {str(row["mint"]) for row in prefix}
        if missing:
            raise RuntimeError(f"frozen cohort mints missing from research prefix: {sorted(missing)}")
    selected = set(ordinals)
    controls = load_table_controls(args.research_root, selected)
    targets_by_partition: dict[str, set[str]] = collections.defaultdict(set)
    for row in prefix:
        targets_by_partition[str(row["partitionKey"])].add(str(row["mint"]))
    reports = []
    for path in sorted((args.evidence_root / "readiness-v1/partitions").glob("*-v4.json")):
        report = json.loads(path.read_text())
        targets = targets_by_partition.get(str(report["partitionKey"]), set())
        if targets:
            reports.append((report, targets))
    records, taken_features, matched_taken_features, ignored_features, manifests = [], [], [], [], []
    for report, targets in reports:
        cache_path = args.output / "scan-cache" / f'{report["partitionKey"].replace("/", "-")}.json'
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            if cached.get("schema") == SCAN_CACHE_SCHEMA and cached.get("boundaryOrdinal") == args.max_ordinal and set(cached.get("targets") or []) == targets:
                records.extend(cached["records"])
                taken_features.extend(cached["takenFeatures"])
                matched_taken_features.extend(cached["matchedTakenFeatures"])
                ignored_features.extend(cached["ignoredFeatures"])
                manifests.append(cached["manifest"])
                print(json.dumps({"partition": report["partitionKey"], "taken": len(cached["records"]), "cache": True}, sort_keys=True), flush=True)
                continue
        record_start = len(records)
        taken_start = len(taken_features)
        matched_start = len(matched_taken_features)
        ignored_start = len(ignored_features)
        events_by_mint, transaction_fees, counters = collect_partition(args.evidence_root, report, targets)
        creates = {row["mint"]: int(row.get("createReceivedAtUnixNs") or 0) // 1_000_000 for row in report.get("mints", []) if row.get("mint") in targets}
        taken_mints = []
        for mint in targets:
            events = [row for row in events_by_mint.get(mint, []) if row.event.quote_mint in SOL_QUOTES and not row.event.mayhem_mode]
            profile = actual_wallet_profile(events, creates[mint], transaction_fees)
            if not profile:
                continue
            taken_mints.append(mint)
            first_buy = next(row for row in events if row.event.user == TWO_CQ and row.event.is_buy)
            timing = {str(age): {str(size): frozen_policy(events, creates[mint] + age, size) for size in SIZES} for age in TIMING_AGES}
            actual_time = {str(size): frozen_policy(events, profile["firstBuyUnixMs"], size, anchor=first_buy) for size in SIZES}
            wallet_schedule = {str(size): replay_wallet_sell_schedule(events, first_buy, profile, size) for size in SIZES}
            feature = selector_feature(events, creates[mint], max(1, profile["entryAgeMs"] - 1), controls, mint, pre_entry_anchor=first_buy)
            taken_features.append(feature)
            records.append({"mint": mint, "ordinal": ordinals[mint], "createUnixMs": creates[mint], "actual2Cq": profile, "timingFrozenExit": timing, "actualTimeFrozenExit": actual_time, "actualTimeWalletSchedule": wallet_schedule, "selectorFeaturesPreEntry": feature})
        # One contemporary ignored launch per taken launch, without outcome-based matching.
        for mint in taken_mints:
            record = next(item for item in records if item["mint"] == mint)
            age = max(1, record["actual2Cq"]["entryAgeMs"] - 1)
            candidates = []
            for other in targets - set(taken_mints):
                if abs(creates[other] - creates[mint]) > 15 * 60_000:
                    continue
                other_events = [row for row in events_by_mint.get(other, []) if row.event.quote_mint in SOL_QUOTES and not row.event.mayhem_mode]
                if not other_events:
                    continue
                feature = selector_feature(other_events, creates[other], age, controls, other)
                candidates.append((other, feature))
            if not candidates:
                continue
            control_keys = ("curveVirtualSol", "grossBuySol", "buyCount", "curveRealToken", "globalTrades60sPrior", "globalLaunches60sPrior")
            source = record["selectorFeaturesPreEntry"]
            def distance(item: tuple[str, dict[str, Any]]) -> float:
                feature = item[1]
                return abs(creates[item[0]] - creates[mint]) / 60_000 + sum((math.log1p(max(float(source.get(key) or 0), 0)) - math.log1p(max(float(feature.get(key) or 0), 0))) ** 2 for key in control_keys)
            chosen, feature = min(candidates, key=distance)
            matched_taken_features.append(source)
            ignored_features.append(feature)
            record["matchedIgnoredMint"] = chosen
            record["matchedIgnoredFeatures"] = feature
        manifest = {"partitionKey": report["partitionKey"], "targets": len(targets), "taken": len(taken_mints), "counters": counters}
        manifests.append(manifest)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({
            "schema": SCAN_CACHE_SCHEMA, "boundaryOrdinal": args.max_ordinal,
            "targets": sorted(targets), "records": records[record_start:],
            "takenFeatures": taken_features[taken_start:],
            "matchedTakenFeatures": matched_taken_features[matched_start:],
            "ignoredFeatures": ignored_features[ignored_start:],
            "manifest": manifest,
        }, sort_keys=True))
        print(json.dumps({"partition": report["partitionKey"], "taken": len(taken_mints)}, sort_keys=True), flush=True)
    # The atlas exposure definition is bounded by its latest causal checkpoint.
    # Later historical wallet buys are useful context, but are not part of the
    # reported exact-compatible 2CQ overlay.
    records = [row for row in records if row["actual2Cq"]["entryAgeMs"] <= 5_000]
    records.sort(key=lambda row: row["ordinal"])
    if frozen_cohort is not None and {row["mint"] for row in records} != frozen_cohort:
        missing = frozen_cohort - {row["mint"] for row in records}
        extra = {row["mint"] for row in records} - frozen_cohort
        raise RuntimeError(f"frozen cohort integrity failure: missing={sorted(missing)} extra={sorted(extra)}")
    matched_taken_features = [row["selectorFeaturesPreEntry"] for row in records if row.get("matchedIgnoredFeatures")]
    ignored_features = [row["matchedIgnoredFeatures"] for row in records if row.get("matchedIgnoredFeatures")]
    timing = {str(size): {str(age): aggregate([row["timingFrozenExit"][str(age)][str(size)] for row in records]) for age in TIMING_AGES} for size in SIZES}
    actual_timing = {str(size): aggregate([row["actualTimeFrozenExit"][str(size)] for row in records]) for size in SIZES}
    schedule = {str(size): aggregate([row["actualTimeWalletSchedule"][str(size)] for row in records]) for size in SIZES}
    entry_ages = [row["actual2Cq"]["entryAgeMs"] for row in records]
    ranks = [row["actual2Cq"]["canonicalEntryRank"] for row in records]
    actual_closed = [row for row in records if row["actual2Cq"]["actualRealizedRoiPct"] is not None]
    classifications = collections.Counter()
    for row in records:
        base = row["timingFrozenExit"]["100"]["0.5"]
        early = row["actualTimeFrozenExit"]["0.5"]
        exit_like = row["actualTimeWalletSchedule"]["0.5"]
        if not all(cell.get("status") == "EXACT" for cell in (base, early, exit_like)):
            kind = "INSUFFICIENT_EVIDENCE"
        else:
            timing_gain = early["returnPct"] - base["returnPct"]
            exit_gain = exit_like["returnPct"] - early["returnPct"]
            if timing_gain > 2 and exit_gain > 2:
                kind = "BOTH"
            elif timing_gain > max(2, exit_gain * 1.5):
                kind = "TIMING_DOMINANT"
            elif exit_gain > max(2, timing_gain * 1.5):
                kind = "EXIT_DOMINANT"
            else:
                kind = "NEITHER_SELECTOR_LIKELY"
        row["attribution"] = kind
        classifications[kind] += 1
    selector = selector_effects(matched_taken_features, ignored_features) if ignored_features else []
    report = {
        "schema": SCHEMA, "classification": "2CQ_ALPHA_NOT_REPRODUCIBLE_FROM_AVAILABLE_DATA",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(), "boundaryOrdinal": args.max_ordinal,
        "identity": {"wallet": TWO_CQ, "sourceArtifacts": ["data/wallet-audit-wallets.json", "data/wallet-ledger-audit-2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG.json", "data/path-wallet-audit-2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG.json"]},
        "exactCorpus": {"scope": "2CQ buy at or before the atlas final 5000ms causal checkpoint", "uniqueMints": len(records), "actualClosedN": len(actual_closed), "entryAge": stats([float(x) for x in entry_ages]), "canonicalRank": stats([float(x) for x in ranks]), "successfulEntries": len(records), "successfulBuysAhead": stats([float(row["actual2Cq"]["successfulBuysAhead"]) for row in records]), "solAhead": stats([row["actual2Cq"]["solAhead"] for row in records]), "actualBuySol": stats([row["actual2Cq"]["buySol"] for row in records]), "curveGrossBuySol": stats([row["actual2Cq"]["curveGrossBuySol"] for row in records]), "ownImpactPct": stats([row["actual2Cq"]["ownImpactPct"] for row in records]), "capacityUsedPct": stats([row["actual2Cq"]["capacityUsedPct"] for row in records]), "buyCount": stats([float(row["actual2Cq"]["buyCount"]) for row in records]), "sellCount": stats([float(row["actual2Cq"]["sellCount"]) for row in records]), "firstSellLatencyMs": stats([float(row["actual2Cq"]["firstSellLatencyMs"]) for row in records if row["actual2Cq"]["firstSellLatencyMs"] is not None]), "fullExitLatencyMs": stats([float(row["actual2Cq"]["fullExitLatencyMs"]) for row in records if row["actual2Cq"]["fullExitLatencyMs"] is not None]), "actualRealizedRoiPct": stats([row["actual2Cq"]["actualRealizedRoiPct"] for row in actual_closed]), "actualRealizedPnlSol": sum(row["actual2Cq"]["actualRealizedPnlSol"] for row in actual_closed)},
        "historicalContext": {"walletLedger5000": {"positions": 904, "realizedTrades": 3416, "realizedPnlSol": 201.84268416148322, "winRatePct": 54.18618266978923, "profitFactor": 2.2894026158385126}, "pathAudit50": {"measuredEntries": 50, "mirrorSourceExitN": 47, "mirrorSourceExitPf": 3.4395232179069932, "mirrorSourceExitAveragePct": 28.04599817087952}, "compatibility": "AUTHORITATIVE_HISTORICAL_BUT_NOT_EXACT_CURVE_COMPATIBLE"},
        "entryTimingSensitivity": timing, "actual2CqTimeFrozenExit": actual_timing,
        "actual2CqTimeWalletSchedule": schedule, "attributionCounts": dict(classifications),
        "selectorMatchedN": len(ignored_features), "selectorEffects": selector,
        "negativeControl": "NO_EXACT_COMPATIBLE_MEDIOCRE_EARLY_WALLET_PRELABELED_IN_THIS_FROZEN_WINDOW",
        "records": records, "manifests": manifests,
        "integrity": {"validationRowsInspected": 0, "holdoutRowsInspected": 0, "officialV1Modified": False, "liveSubmissions": 0, "rankerBuilt": False},
    }
    # Choose only from observed evidence; insufficient exact exits must remain explicit.
    if len(records) and schedule["0.5"]["exactN"] >= max(10, len(records) // 2):
        t100 = timing["0.5"]["100"]["expectancy"]
        tearly = actual_timing["0.5"]["expectancy"]
        texit = schedule["0.5"]["expectancy"]
        if texit is not None and tearly is not None and t100 is not None:
            timing_gain, exit_gain = tearly - t100, texit - tearly
            if timing_gain > 0.02 and timing_gain > exit_gain * 1.5:
                report["classification"] = "2CQ_ALPHA_PRIMARILY_ENTRY_TIMING"
            elif exit_gain > 0.02 and exit_gain > timing_gain * 1.5:
                report["classification"] = "2CQ_ALPHA_PRIMARILY_EXIT_BEHAVIOR"
            elif timing_gain > 0.01 and exit_gain > 0.01:
                report["classification"] = "2CQ_ALPHA_IS_MIXED"
            elif selector and abs(selector[0].get("effect") or 0) >= 0.5 and selector[0]["consistentBlocks"] >= 3:
                report["classification"] = "2CQ_ALPHA_PRIMARILY_LAUNCH_SELECTION"
    args.output.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (args.output / "report.json").write_text(payload)
    with gzip.open(args.output / "decomposition-rows.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in records:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    provenance = {"schema": SCHEMA + "_PROVENANCE", "boundaryOrdinal": args.max_ordinal, "indexPrefixSha256": hashlib.sha256("\n".join(row["mint"] for row in prefix).encode()).hexdigest(), "scriptSha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(), "reportSha256": hashlib.sha256(payload.encode()).hexdigest(), "validationRowsInspected": 0, "holdoutRowsInspected": 0}
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"classification": report["classification"], "mints": len(records), "actualClosed": len(actual_closed), "scheduleExact": schedule["0.5"]["exactN"], "selectorMatched": len(ignored_features)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
