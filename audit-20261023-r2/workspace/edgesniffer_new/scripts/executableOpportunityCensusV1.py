#!/usr/bin/env python3
"""Run the frozen, descriptive executable-opportunity census over discovery mints."""

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
import random
import statistics
from typing import Any

from buildPumpExactAlphaResearchV1 import NETWORK_FEE, apply_group
from exactCurveEconomicsV1 import CurveState, quote_exact_quote_in_buy, quote_exact_token_sell
from sandboxPumpExactFollowThroughAtlasV1 import SOL_QUOTES, collect_partition, load_prefix
from validatePumpExactEvidenceLedgerV1 import event_fees


SCHEMA = "EXECUTABLE_OPPORTUNITY_CENSUS_V1"
IMPLEMENTATION_VERSION = 1
LAMPORTS = 1_000_000_000


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_ms(value: int) -> str:
    return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc).isoformat()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def state_dict(state: CurveState) -> dict[str, int]:
    return {
        "virtualSol": state.virtual_sol,
        "virtualToken": state.virtual_token,
        "realSol": state.real_sol,
        "realToken": state.real_token,
    }


def group_events(events: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[Any]] = collections.defaultdict(list)
    for row in events:
        grouped[(row.arrival_ms, row.slot, row.tx_index, row.signature)].append(row)
    output = []
    for key, rows in sorted(grouped.items()):
        ordered = sorted(rows, key=lambda row: row.instruction_index)
        output.append({
            "arrivalMs": int(key[0]),
            "slot": int(key[1]),
            "txIndex": int(key[2]),
            "signature": str(key[3]),
            "rows": ordered,
            "postState": ordered[-1].event.post_state,
            "feeEvent": ordered[-1].event,
            "canonicalEvidenceId": ordered[-1].canonical_evidence_id,
            "observationEvidenceIds": sorted({row.observation_evidence_id for row in ordered if row.observation_evidence_id}),
        })
    return output


def build_decisions(groups: list[dict[str, Any]], create_ms: int) -> list[dict[str, Any]]:
    if not groups:
        return []
    decisions: dict[tuple[int, str], dict[str, Any]] = {}

    def add(index: int, label: str, state: CurveState | None = None, future_index: int | None = None) -> None:
        group = groups[index]
        key = (future_index if future_index is not None else index + 1, group["signature"])
        decision = decisions.setdefault(key, {
            "decisionMs": group["arrivalMs"],
            "decisionUtc": iso_ms(group["arrivalMs"]),
            "mintAgeMs": group["arrivalMs"] - create_ms,
            "triggerSlot": group["slot"],
            "triggerSignature": group["signature"],
            "baseState": state or group["postState"],
            "feeEvent": group["feeEvent"],
            "futureIndex": future_index if future_index is not None else index + 1,
            "labels": [],
            "evidenceIds": group["observationEvidenceIds"] + ([group["canonicalEvidenceId"]] if group["canonicalEvidenceId"] else []),
        })
        decision["labels"].append(label)

    # Availability-honest initial state: the pre-state is first observable when
    # the first supported transaction arrives. Only the zero-delay oracle can
    # insert before that transaction; delayed scenarios naturally land after it.
    add(0, "INITIAL_PRE_FIRST_SUPPORTED_TRADE", groups[0]["rows"][0].event.pre_state, 0)

    external_buy_count = 0
    cumulative_external_buy = 0
    saw_external_sell = False
    post_sell_buy_recorded = False
    first_sell_recorded = False
    initial_real_token = groups[0]["rows"][0].event.pre_state.real_token
    reserve_thresholds = [90, 75, 50]
    crossed: set[int] = set()
    buy_ordinals = {1, 2, 3, 5}
    buy_sol_thresholds = [0.5, 1.0, 2.0, 5.0]
    sol_crossed: set[float] = set()

    for index, group in enumerate(groups):
        group_external_buy = False
        group_external_sell = False
        for row in group["rows"]:
            event = row.event
            is_external = event.user != event.creator
            if is_external and event.is_buy:
                external_buy_count += 1
                cumulative_external_buy += event.immediate_quote_amount
                group_external_buy = True
            elif is_external and not event.is_buy:
                group_external_sell = True

        if group_external_buy:
            for ordinal in sorted(buy_ordinals):
                if external_buy_count >= ordinal and not any(
                    label == f"AFTER_EXTERNAL_BUY_{ordinal}" for item in decisions.values() for label in item["labels"]
                ):
                    add(index, f"AFTER_EXTERNAL_BUY_{ordinal}")
            for threshold in buy_sol_thresholds:
                if cumulative_external_buy >= int(threshold * LAMPORTS) and threshold not in sol_crossed:
                    add(index, f"AFTER_EXTERNAL_GROSS_BUY_{str(threshold).replace('.', 'P')}_SOL")
                    sol_crossed.add(threshold)
            if saw_external_sell and not post_sell_buy_recorded:
                add(index, "AFTER_FIRST_SELL_THEN_EXTERNAL_BUY")
                post_sell_buy_recorded = True

        if group_external_sell:
            saw_external_sell = True
            if not first_sell_recorded:
                add(index, "AFTER_FIRST_EXTERNAL_SELL")
                first_sell_recorded = True

        if initial_real_token > 0:
            remaining_pct = group["postState"].real_token * 100 / initial_real_token
            for threshold in reserve_thresholds:
                if remaining_pct < threshold and threshold not in crossed:
                    add(index, f"AFTER_REAL_TOKEN_BELOW_{threshold}PCT")
                    crossed.add(threshold)

    return sorted(decisions.values(), key=lambda item: (item["decisionMs"], item["futureIndex"], item["triggerSignature"]))


def latest_actual_base(
    decision: dict[str, Any], groups: list[dict[str, Any]], entry_ms: int
) -> tuple[CurveState, Any, int, str]:
    state = decision["baseState"]
    fee_event = decision["feeEvent"]
    future_index = int(decision["futureIndex"])
    evidence_id = ""
    for index in range(future_index, len(groups)):
        group = groups[index]
        if group["arrivalMs"] > entry_ms:
            break
        state = group["postState"]
        fee_event = group["feeEvent"]
        future_index = index + 1
        evidence_id = group["canonicalEvidenceId"]
    return state, fee_event, future_index, evidence_id


def quote_sell(state: CurveState, inventory: int, fee_event: Any) -> Any:
    return quote_exact_token_sell(
        state,
        inventory,
        1,
        event_fees(fee_event),
        cashback_bps=fee_event.cashback_fee_basis_points,
        buyback_share_bps=fee_event.buyback_fee_basis_points,
    )


def roi_from_proceeds(proceeds_lamports: int, capital_lamports: int, delivery_cost_lamports: int = 0) -> float:
    return (proceeds_lamports - capital_lamports - delivery_cost_lamports) * 100 / capital_lamports


def point_at(timeline: list[dict[str, Any]], timestamp: int) -> dict[str, Any]:
    eligible = [point for point in timeline if point["timestamp"] <= timestamp]
    return eligible[-1] if eligible else timeline[0]


def policy_result(
    name: str,
    samples: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    entry_ms: int,
    first_sellable_ms: int,
    sell_landing_ms: int | None,
    inventory: int,
    capital: int,
) -> dict[str, Any]:
    if sell_landing_ms is None:
        return {"status": "SELL_LATENCY_UNAVAILABLE"}
    eligible = [sample for sample in samples if sample["timestamp"] >= first_sellable_ms]
    if not eligible:
        return {"status": "NO_EXECUTABLE_EXIT_SAMPLE"}
    trigger = eligible[-1]
    outcome = "MAX_HOLD"
    if name == "TP20_SL20_MAX120":
        for sample in eligible:
            if sample["roiPct"] >= 20 or sample["roiPct"] <= -20:
                trigger = sample
                outcome = "TP" if sample["roiPct"] >= 20 else "SL"
                break
    elif name == "FIXED_5S_SL20":
        fixed_ms = entry_ms + 5000
        trigger = min(eligible, key=lambda item: (abs(item["timestamp"] - fixed_ms), item["timestamp"] < fixed_ms))
        for sample in eligible:
            if sample["timestamp"] > fixed_ms:
                break
            if sample["roiPct"] <= -20:
                trigger = sample
                outcome = "SL"
                break
        if outcome != "SL":
            trigger = next((sample for sample in eligible if sample["timestamp"] >= fixed_ms), eligible[-1])
            outcome = "FIXED_EXIT"
    elif name == "TRAIL20_12_SL20_120S":
        armed = False
        high = -math.inf
        for sample in eligible:
            high = max(high, sample["roiPct"])
            if sample["roiPct"] <= -20:
                trigger = sample
                outcome = "SL"
                break
            if sample["roiPct"] >= 20:
                armed = True
            if armed and sample["roiPct"] <= high - 12:
                trigger = sample
                outcome = "TRAIL"
                break
    else:
        raise RuntimeError(f"unknown frozen policy {name}")

    landing_at = trigger["timestamp"] + sell_landing_ms
    landing_point = point_at(timeline, landing_at)
    try:
        sell = quote_sell(landing_point["state"], inventory, landing_point["feeEvent"])
    except Exception as error:
        return {
            "status": "EXIT_UNSUPPORTED",
            "reason": str(error),
            "triggerAtMs": trigger["timestamp"],
            "triggerRoiPct": trigger["roiPct"],
            "outcome": outcome,
        }
    proceeds = sell.immediate_quote_out - NETWORK_FEE
    return {
        "status": "EXACT",
        "outcome": outcome,
        "triggerAtMs": trigger["timestamp"],
        "triggerRoiPct": trigger["roiPct"],
        "landingAtMs": landing_at,
        "landingStateAtMs": landing_point["timestamp"],
        "returnPct": roi_from_proceeds(proceeds, capital),
        "pnlSol": (proceeds - capital) / LAMPORTS,
        "sellProceedsLamports": proceeds,
    }


def evaluate_cell(
    decision: dict[str, Any],
    groups: list[dict[str, Any]],
    size_sol: float,
    scenario: str,
    entry_offset_ms: int,
    first_sellable_offset_ms: int,
    sell_landing_offset_ms: int | None,
    costs_sol: list[float],
    roi_thresholds: list[float],
) -> dict[str, Any]:
    decision_ms = int(decision["decisionMs"])
    entry_ms = decision_ms + entry_offset_ms
    if scenario == "ZERO_DELAY_ORACLE":
        base_state = decision["baseState"]
        fee_event = decision["feeEvent"]
        future_index = int(decision["futureIndex"])
        evidence_id = ""
    else:
        base_state, fee_event, future_index, evidence_id = latest_actual_base(decision, groups, entry_ms)
    gross = int(round(size_sol * LAMPORTS))
    capital = gross + NETWORK_FEE
    try:
        buy = quote_exact_quote_in_buy(base_state, gross, event_fees(fee_event))
    except Exception as error:
        return {"status": "ENTRY_UNSUPPORTED", "reason": str(error)}

    immediate_inventory = None
    try:
        immediate_inventory = quote_exact_quote_in_buy(
            decision["baseState"], gross, event_fees(decision["feeEvent"])
        ).token_out
    except Exception:
        pass

    state = buy.post_state
    inventory = buy.token_out
    first_sellable_ms = max(entry_ms, decision_ms + first_sellable_offset_ms)
    horizon_ms = entry_ms + 120_000
    replay_end_ms = horizon_ms + (sell_landing_offset_ms or 0)
    timeline = [{
        "timestamp": entry_ms,
        "state": state,
        "feeEvent": fee_event,
        "status": "OWN_BUY",
        "evidenceId": evidence_id,
    }]
    failed = 0
    attempted = 0
    for index in range(future_index, len(groups)):
        group = groups[index]
        if group["arrivalMs"] <= entry_ms:
            continue
        if group["arrivalMs"] > replay_end_ms:
            break
        attempted += 1
        status = "APPLIED"
        try:
            state = apply_group(state, group["rows"])
            fee_event = group["feeEvent"]
        except Exception:
            failed += 1
            status = "COUNTERFACTUAL_CONSTRAINT_FAILURE"
        timeline.append({
            "timestamp": group["arrivalMs"],
            "state": state,
            "feeEvent": fee_event,
            "status": status,
            "evidenceId": group["canonicalEvidenceId"],
        })
    timeline.append({
        "timestamp": replay_end_ms,
        "state": state,
        "feeEvent": fee_event,
        "status": "ENDPOINT",
        "evidenceId": timeline[-1]["evidenceId"],
    })

    sample_times = sorted({
        first_sellable_ms,
        entry_ms + 5000,
        horizon_ms,
        *(point["timestamp"] for point in timeline if first_sellable_ms <= point["timestamp"] <= horizon_ms),
    })
    samples = []
    for timestamp in sample_times:
        point = point_at(timeline, timestamp)
        try:
            sell = quote_sell(point["state"], inventory, point["feeEvent"])
        except Exception:
            continue
        proceeds = sell.immediate_quote_out - NETWORK_FEE
        spot_gross = inventory * point["state"].virtual_sol // point["state"].virtual_token
        samples.append({
            "timestamp": timestamp,
            "roiPct": roi_from_proceeds(proceeds, capital),
            "proceedsLamports": proceeds,
            "exitImpactLamports": max(0, spot_gross - sell.gross_quote_out),
        })
    if not samples:
        return {"status": "EXIT_UNSUPPORTED", "reason": "no exact sell quote in horizon"}

    opportunity: dict[str, Any] = {}
    for cost_sol in costs_sol:
        cost_lamports = int(round(cost_sol * LAMPORTS))
        adjusted = [(sample["timestamp"], roi_from_proceeds(sample["proceedsLamports"], capital, cost_lamports)) for sample in samples]
        cost_key = f"{cost_sol:.4f}"
        threshold_rows: dict[str, Any] = {}
        for threshold in roi_thresholds:
            hits = [(timestamp, roi) for timestamp, roi in adjusted if roi >= threshold]
            total_duration = 0
            longest = 0
            run = 0
            for (left_ms, left_roi), (right_ms, _) in zip(adjusted, adjusted[1:]):
                duration = max(0, right_ms - left_ms)
                if left_roi >= threshold:
                    total_duration += duration
                    run += duration
                    longest = max(longest, run)
                else:
                    run = 0
            threshold_rows[str(int(threshold))] = {
                "reached": bool(hits),
                "firstHitAfterEntryMs": hits[0][0] - entry_ms if hits else None,
                "totalWindowMs": total_duration,
                "longestWindowMs": longest,
            }
        opportunity[cost_key] = {
            "mfePct": max(roi for _, roi in adjusted),
            "maePct": min(roi for _, roi in adjusted),
            "thresholds": threshold_rows,
        }

    policies = {
        name: policy_result(
            name, samples, timeline, entry_ms, first_sellable_ms,
            sell_landing_offset_ms, inventory, capital,
        )
        for name in ("TP20_SL20_MAX120", "FIXED_5S_SL20", "TRAIL20_12_SL20_120S")
    }
    entry_spot_cost = inventory * base_state.virtual_sol // base_state.virtual_token
    return {
        "status": "EXACT",
        "scenario": scenario,
        "sizeSol": size_sol,
        "entryMs": entry_ms,
        "firstSellableMs": first_sellable_ms,
        "horizonMs": horizon_ms,
        "entryState": state_dict(base_state),
        "ownBuyPostState": state_dict(buy.post_state),
        "tokensReceivedRaw": inventory,
        "immediateTokensRaw": immediate_inventory,
        "movementBeforeEntryInventoryDeteriorationPct": (
            (1 - inventory / immediate_inventory) * 100 if immediate_inventory else None
        ),
        "protocolFeeLamports": buy.protocol_fee,
        "creatorFeeLamports": buy.creator_fee,
        "entryOwnCurveImpactLamports": max(0, buy.curve_quote_in - entry_spot_cost),
        "medianExitImpactLamports": median([float(sample["exitImpactLamports"]) for sample in samples]),
        "firstSellableBaseRoiPct": next((sample["roiPct"] for sample in samples if sample["timestamp"] >= first_sellable_ms), None),
        "baseMfePct": max(sample["roiPct"] for sample in samples),
        "baseMaePct": min(sample["roiPct"] for sample in samples),
        "breakEvenAdditionalDeliveryCostSol": max(sample["proceedsLamports"] - capital for sample in samples) / LAMPORTS,
        "counterfactualAttemptedTransactions": attempted,
        "counterfactualFailedTransactions": failed,
        "counterfactualDivergenceRate": failed / attempted if attempted else 0.0,
        "opportunity": opportunity,
        "policies": policies,
    }


def latency_scenarios(contract: dict[str, Any]) -> list[tuple[str, int, int, int | None]]:
    source = contract["empiricalLatencySource"]
    entries = source["modeledIntegerEntryOffsetsMs"]
    sellable = source["modeledIntegerFirstSellableOffsetsMs"]
    landing = source["modeledIntegerSellLandingOffsetsMs"]
    return [
        ("ZERO_DELAY_ORACLE", 0, 0, 0),
        *( (tier, int(entries[tier]), int(sellable[tier]), None if landing[tier] is None else int(landing[tier])) for tier in ("P50", "P75", "P90") ),
    ]


def process_mint(
    mint: str,
    ordinal: int,
    create_ms: int,
    events: list[Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    sol_events = [row for row in events if row.event.quote_mint in SOL_QUOTES and row.arrival_ms <= create_ms + 121_000]
    if not sol_events:
        return {"mint": mint, "ordinal": ordinal, "status": "EXCLUDED", "reason": "NO_SOL_QUOTED_EXACT_EVENTS"}
    if any(row.event.mayhem_mode for row in sol_events):
        return {"mint": mint, "ordinal": ordinal, "status": "EXCLUDED", "reason": "MAYHEM_EXCLUDED"}
    groups = group_events(sol_events)
    decisions = build_decisions(groups, create_ms)
    if not decisions:
        return {"mint": mint, "ordinal": ordinal, "status": "EXCLUDED", "reason": "NO_EVENT_STATE"}
    rows = []
    for decision_index, decision in enumerate(decisions):
        cells: dict[str, Any] = {}
        for scenario, entry_offset, sellable_offset, sell_landing in latency_scenarios(contract):
            for size in contract["sizesSol"]:
                key = f"{scenario}|{float(size):.1f}"
                cells[key] = evaluate_cell(
                    decision, groups, float(size), scenario, entry_offset,
                    sellable_offset, sell_landing,
                    [float(value) for value in contract["configurableRoundTripDeliveryCostSol"]],
                    [float(value) for value in contract["opportunityNetRoiThresholdsPct"]],
                )
        rows.append({
            "stateId": f"{mint}:{decision_index}",
            "labels": sorted(set(decision["labels"])),
            "decisionUnixMs": decision["decisionMs"],
            "decisionUtc": decision["decisionUtc"],
            "mintAgeMs": decision["mintAgeMs"],
            "triggerSlot": decision["triggerSlot"],
            "triggerSignature": decision["triggerSignature"],
            "decisionState": state_dict(decision["baseState"]),
            "evidenceIds": sorted(set(decision["evidenceIds"])),
            "fixedAgeMetadataMs": {
                str(age): decision["decisionMs"] - (create_ms + int(age))
                for age in contract["eventStates"]["fixedAgeMetadataMs"]
            },
            "cells": cells,
        })
    return {
        "schema": SCHEMA + "_MINT",
        "mint": mint,
        "ordinal": ordinal,
        "createUnixMs": create_ms,
        "createUtc": iso_ms(create_ms),
        "status": "EXACT_EVALUABLE" if any(
            cell.get("status") == "EXACT" for row in rows for cell in row["cells"].values()
        ) else "EXCLUDED",
        "reason": None if any(cell.get("status") == "EXACT" for row in rows for cell in row["cells"].values()) else "NO_EXACT_EVALUABLE_CELL",
        "eventStates": rows,
    }


def write_partition(path: pathlib.Path, header: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"header": header}, sort_keys=True, separators=(",", ":")) + "\n")
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def iter_mint_rows(output: pathlib.Path):
    for path in sorted((output / "partitions").glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            next(handle)
            for line in handle:
                if line.strip():
                    yield json.loads(line)


def bootstrap_rate(values: list[int], replicates: int, seed: int) -> dict[str, float | int | None]:
    if not values:
        return {"N": 0, "rate": None, "ciLow": None, "ciHigh": None}
    rng = random.Random(seed)
    n = len(values)
    draws = [sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(replicates)]
    return {
        "N": n,
        "rate": sum(values) / n,
        "ciLow": percentile(draws, 0.025),
        "ciHigh": percentile(draws, 0.975),
    }


def aggregate(output: pathlib.Path, contract: dict[str, Any], population: dict[str, Any]) -> dict[str, Any]:
    rows = list(iter_mint_rows(output))
    evaluable = [row for row in rows if row["status"] == "EXACT_EVALUABLE"]
    excluded = collections.Counter(row.get("reason") or "UNKNOWN" for row in rows if row["status"] != "EXACT_EVALUABLE")
    sizes = [float(value) for value in contract["sizesSol"]]
    costs = [float(value) for value in contract["configurableRoundTripDeliveryCostSol"]]
    thresholds = [int(value) for value in contract["opportunityNetRoiThresholdsPct"]]
    scenarios = [item[0] for item in latency_scenarios(contract)]

    def cells_for(mint_row: dict[str, Any], scenario: str, size: float) -> list[dict[str, Any]]:
        key = f"{scenario}|{size:.1f}"
        return [state["cells"][key] for state in mint_row["eventStates"] if state["cells"].get(key, {}).get("status") == "EXACT"]

    opportunity = []
    policy_capture = []
    state_counts: dict[str, set[str]] = collections.defaultdict(set)
    attribution: dict[str, list[float]] = collections.defaultdict(list)
    divergent_states = 0
    exact_states = 0
    first_hit_reference: list[int] = []

    for row in evaluable:
        for state in row["eventStates"]:
            for label in state["labels"]:
                state_counts[label].add(row["mint"])
            for cell in state["cells"].values():
                if cell.get("status") != "EXACT":
                    continue
                exact_states += 1
                if cell["counterfactualFailedTransactions"] > 0:
                    divergent_states += 1
                for key in ("movementBeforeEntryInventoryDeteriorationPct", "protocolFeeLamports", "creatorFeeLamports", "entryOwnCurveImpactLamports", "medianExitImpactLamports"):
                    if cell.get(key) is not None:
                        attribution[key].append(float(cell[key]))

    for scenario in scenarios:
        for size in sizes:
            for cost in costs:
                for threshold in thresholds:
                    flags = []
                    first_hits = []
                    for row in evaluable:
                        cells = cells_for(row, scenario, size)
                        hit_rows = [
                            value["opportunity"][f"{cost:.4f}"]["thresholds"][str(threshold)]
                            for value in cells
                            if f"{cost:.4f}" in value["opportunity"]
                        ]
                        reached = any(item["reached"] for item in hit_rows)
                        flags.append(int(reached))
                        first_hits.extend(item["firstHitAfterEntryMs"] for item in hit_rows if item["reached"] and item["firstHitAfterEntryMs"] is not None)
                    result = {
                        "scenario": scenario,
                        "sizeSol": size,
                        "deliveryCostSol": cost,
                        "thresholdPct": threshold,
                        "evaluableMints": len(flags),
                        "opportunityMints": sum(flags),
                        "prevalence": sum(flags) / len(flags) if flags else None,
                        "medianFirstHitAfterEntryMs": median([float(value) for value in first_hits]),
                    }
                    opportunity.append(result)
                    if scenario == "P50" and size == 0.5 and cost == 0.001 and threshold == 10:
                        first_hit_reference = first_hits

                    for policy in ("TP20_SL20_MAX120", "FIXED_5S_SL20", "TRAIL20_12_SL20_120S"):
                        policy_flags = []
                        for row in evaluable:
                            values = []
                            for cell in cells_for(row, scenario, size):
                                policy_row = cell["policies"].get(policy, {})
                                if policy_row.get("status") == "EXACT":
                                    adjusted = policy_row["returnPct"] - cost * LAMPORTS * 100 / (int(round(size * LAMPORTS)) + NETWORK_FEE)
                                    values.append(adjusted)
                            policy_flags.append(int(any(value >= threshold for value in values)))
                        policy_capture.append({
                            "scenario": scenario,
                            "sizeSol": size,
                            "deliveryCostSol": cost,
                            "thresholdPct": threshold,
                            "policy": policy,
                            "evaluableMints": len(policy_flags),
                            "capturedMints": sum(policy_flags),
                            "prevalence": sum(policy_flags) / len(policy_flags) if policy_flags else None,
                        })

    def find_opportunity(scenario: str, size: float, cost: float, threshold: int) -> dict[str, Any]:
        return next(row for row in opportunity if row["scenario"] == scenario and row["sizeSol"] == size and row["deliveryCostSol"] == cost and row["thresholdPct"] == threshold)

    reference_half = find_opportunity("P50", 0.5, 0.001, 10)
    reference_one = find_opportunity("P50", 1.0, 0.001, 10)
    zero_half = find_opportunity("ZERO_DELAY_ORACLE", 0.5, 0.001, 10)
    policy_reference = [row for row in policy_capture if row["scenario"] == "P50" and row["sizeSol"] == 0.5 and row["deliveryCostSol"] == 0.001 and row["thresholdPct"] == 10]
    best_policy = max(policy_reference, key=lambda row: row["prevalence"] or 0)
    opportunity_n = reference_half["opportunityMints"]
    best_capture_ratio = best_policy["capturedMints"] / opportunity_n if opportunity_n else 0.0
    capacity_retention = (reference_one["prevalence"] or 0) / reference_half["prevalence"] if reference_half["prevalence"] else 0.0
    latency_retention = (reference_half["prevalence"] or 0) / zero_half["prevalence"] if zero_half["prevalence"] else 0.0
    later_share = sum(value > 2000 for value in first_hit_reference) / len(first_hit_reference) if first_hit_reference else 0.0
    evaluable_rate = len(evaluable) / len(rows) if rows else 0.0
    divergence_rate = divergent_states / exact_states if exact_states else 0.0

    rules = contract["diagnosis"]["frozenDecisionRules"]
    labels = []
    if evaluable_rate < rules["dataCoverageBlockerBelowExactEvaluableRate"]:
        labels.append("DATA_COVERAGE_BLOCKER")
    if divergence_rate > rules["counterfactualDivergenceBlockerAboveAffectedStateRate"]:
        labels.append("COUNTERFACTUAL_DIVERGENCE_BLOCKER")
    if (reference_half["prevalence"] or 0) < rules["opportunityTooScarceBelowPrevalence"]:
        labels.append("OPPORTUNITY_TOO_SCARCE")
    if (reference_half["prevalence"] or 0) >= rules["opportunityTooScarceBelowPrevalence"] and capacity_retention < rules["capacityProblemOneSolRetentionBelow"]:
        labels.append("CAPACITY_PROBLEM")
    if (zero_half["prevalence"] or 0) >= rules["opportunityTooScarceBelowPrevalence"] and latency_retention < rules["latencyOrderingProblemP50RetentionBelow"]:
        labels.append("LATENCY_ORDERING_PROBLEM")
    if later_share > rules["laterHorizonOpportunityShareAbove"]:
        labels.append("LATER_HORIZON_OPPORTUNITY")
    if (reference_half["prevalence"] or 0) >= rules["selectionProblemMinimumOpportunityPrevalence"]:
        if best_capture_ratio < rules["exitProblemBestFrozenPolicyCaptureBelow"]:
            labels.append("EXIT_PROBLEM")
        else:
            labels.append("SELECTION_PROBLEM")
    ordered_labels = [label for label in rules["priorityOrder"] if label in labels]

    bootstrap = rules["bootstrap"]
    reference_flags = []
    for row in evaluable:
        hits = []
        for cell in cells_for(row, "P50", 0.5):
            hits.append(cell["opportunity"]["0.0010"]["thresholds"]["10"]["reached"])
        reference_flags.append(int(any(hits)))

    return {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "contractSha256": population["contractSha256"],
        "implementationVersion": IMPLEMENTATION_VERSION,
        "population": {
            **population,
            "mintRows": len(rows),
            "exactEvaluableUniqueMints": len(evaluable),
            "excludedOrUnresolvedUniqueMints": len(rows) - len(evaluable),
            "exactEvaluableRateWithinAdmittedPrefix": evaluable_rate,
            "exclusionReasons": dict(excluded),
            "eventStateRows": sum(len(row.get("eventStates", [])) for row in rows),
        },
        "eventStateAvailability": {label: len(mints) for label, mints in sorted(state_counts.items())},
        "opportunityPrevalence": opportunity,
        "frozenPolicyCapture": policy_capture,
        "headline": {
            "referenceOpportunity": reference_half,
            "oneSolReferenceOpportunity": reference_one,
            "zeroDelayReferenceOpportunity": zero_half,
            "capacityRetention": capacity_retention,
            "latencyRetention": latency_retention,
            "bestFrozenPolicy": best_policy,
            "bestFrozenPolicyCaptureOfOpportunity": best_capture_ratio,
            "laterThan2sFirstHitShare": later_share,
            "mintClusteredReferencePrevalence95CI": bootstrap_rate(
                reference_flags, int(bootstrap["replicates"]), int(bootstrap["seed"])
            ),
        },
        "economicAttribution": {
            key: {"N": len(values), "median": median(values), "P75": percentile(values, 0.75), "P90": percentile(values, 0.90)}
            for key, values in attribution.items()
        },
        "counterfactualDivergence": {
            "exactStateCells": exact_states,
            "affectedStateCells": divergent_states,
            "affectedStateRate": divergence_rate,
            "resubmissionModeled": False,
        },
        "diagnosis": {
            "primary": ordered_labels[0] if ordered_labels else None,
            "secondary": ordered_labels[1] if len(ordered_labels) > 1 else None,
            "allTriggered": ordered_labels,
            "frozenInputs": {
                "exactEvaluableRate": evaluable_rate,
                "counterfactualDivergenceRate": divergence_rate,
                "referenceOpportunityPrevalence": reference_half["prevalence"],
                "oneSolCapacityRetention": capacity_retention,
                "P50LatencyRetention": latency_retention,
                "bestFrozenPolicyCaptureRatio": best_capture_ratio,
                "laterThan2sFirstHitShare": later_share,
            },
        },
        "integrity": {
            "primaryUnit": "unique_mint",
            "stateRowsTreatedAsIndependent": False,
            "validationRowsInspected": 0,
            "holdoutRowsInspected": 0,
            "liveServicesModified": False,
            "collectorModified": False,
            "fingerprintsSearched": False,
            "modelsTrained": False,
            "oracleReportedAsStrategyPnl": False,
        },
    }


def markdown(report: dict[str, Any]) -> str:
    pop = report["population"]
    head = report["headline"]
    diag = report["diagnosis"]
    lines = [
        "# EXECUTABLE_OPPORTUNITY_CENSUS_V1",
        "",
        f"Contract SHA256: `{report['contractSha256']}`",
        "",
        "## Verdict",
        "",
        f"- Primary bottleneck: **{diag['primary'] or 'NONE_TRIGGERED'}**",
        f"- Secondary bottleneck: **{diag['secondary'] or 'NONE'}**",
        "",
        "## Population",
        "",
        f"- Discovery ordinals: 1-{pop['discoveryOrdinalEnd']}",
        f"- Exact-admitted mints: {pop['exactAdmittedUniqueMints']}",
        f"- Exact-evaluable mints: {pop['exactEvaluableUniqueMints']}",
        f"- Evaluable rate: {pop['exactEvaluableRateWithinAdmittedPrefix']:.2%}",
        f"- All observed launches in covered time range: {pop['totalObservedLaunchesInCoveredTimeRange']}",
        "",
        "## Decision Table",
        "",
        "| Measure | Result |",
        "|---|---:|",
        f"| P50 0.5 SOL +10% opportunity prevalence | {(head['referenceOpportunity']['prevalence'] or 0):.2%} |",
        f"| P50 1.0 SOL +10% opportunity prevalence | {(head['oneSolReferenceOpportunity']['prevalence'] or 0):.2%} |",
        f"| 1 SOL capacity retention | {head['capacityRetention']:.2%} |",
        f"| P50 latency retention vs zero-delay | {head['latencyRetention']:.2%} |",
        f"| Best frozen policy | {head['bestFrozenPolicy']['policy']} |",
        f"| Best policy capture of opportunity | {head['bestFrozenPolicyCaptureOfOpportunity']:.2%} |",
        f"| Opportunity first hit after 2s | {head['laterThan2sFirstHitShare']:.2%} |",
        f"| Counterfactual divergence-affected state cells | {report['counterfactualDivergence']['affectedStateRate']:.2%} |",
        "",
        "## Cost And Latency",
        "",
        "The full JSON report contains every size, latency tier, delivery-cost overlay, ROI threshold, event-state availability count, and frozen-policy capture table. ZERO_DELAY_ORACLE is descriptive only and is not reported as strategy PnL.",
        "",
        "## Integrity",
        "",
        "- Validation rows inspected: 0",
        "- Holdout rows inspected: 0",
        "- Fingerprints searched: no",
        "- Models trained: no",
        "- Live or collector changes: no",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=pathlib.Path)
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("contract", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema") != "EXECUTABLE_OPPORTUNITY_CENSUS_V1_CONTRACT":
        raise RuntimeError("CONTRACT_SCHEMA_MISMATCH")
    contract_sha = sha256_file(args.contract)
    boundary = int(contract["population"]["discoveryOrdinalEnd"])
    prefix, ordinals = load_prefix(args.research_root, boundary)
    prefix_sha = hashlib.sha256("\n".join(f"{row['ordinal']}:{row['mint']}" for row in prefix).encode()).hexdigest()
    output_identity = args.output / "run-identity.json"
    identity = {
        "schema": SCHEMA + "_RUN_IDENTITY",
        "contractSha256": contract_sha,
        "discoveryPrefixSha256": prefix_sha,
        "boundaryOrdinal": boundary,
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
    }
    if output_identity.exists():
        existing = json.loads(output_identity.read_text(encoding="utf-8"))
        if existing != identity:
            raise RuntimeError("IMMUTABLE_RUN_IDENTITY_MISMATCH")
    else:
        args.output.mkdir(parents=True, exist_ok=True)
        output_identity.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (args.output / "contract.json").write_bytes(args.contract.read_bytes())

    selected = set(ordinals)
    reports = []
    observed_in_range = 0
    observed_exact_admitted = 0
    start_ms = min(int(row["createReceivedAtUnixNs"]) // 1_000_000 for row in prefix)
    end_ms = max(int(row["createReceivedAtUnixNs"]) // 1_000_000 for row in prefix)
    for path in sorted((args.evidence_root / "readiness-v1" / "partitions").glob("*-v4.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        targets = {
            row["mint"] for row in report.get("mints", [])
            if row.get("exactAdmitted") and row.get("mint") in selected
        }
        for row in report.get("mints", []):
            timestamp = int(row.get("createReceivedAtUnixNs") or 0) // 1_000_000
            if start_ms <= timestamp <= end_ms:
                observed_in_range += 1
                observed_exact_admitted += int(bool(row.get("exactAdmitted")))
        if targets:
            reports.append((report, targets))

    manifests = []
    built_mints: set[str] = set()
    for report, targets in reports:
        key = report["partitionKey"].replace("/", "-")
        destination = args.output / "partitions" / f"{key}.jsonl.gz"
        source_sha = str(report.get("sourceReconciliationManifestSha256") or "")
        if destination.exists():
            with gzip.open(destination, "rt", encoding="utf-8") as handle:
                existing_header = json.loads(next(handle))["header"]
            if existing_header.get("contractSha256") != contract_sha or existing_header.get("sourceSha256") != source_sha:
                raise RuntimeError(f"IMMUTABLE_PARTITION_MISMATCH:{key}")
            manifests.append(existing_header | {"reused": True, "path": str(destination), "sha256": sha256_file(destination)})
            built_mints.update(existing_header.get("mints", []))
            continue
        events_by_mint, counters = collect_partition(args.evidence_root, report, targets)
        create_by_mint = {
            row["mint"]: int(row.get("createReceivedAtUnixNs") or 0) // 1_000_000
            for row in report.get("mints", []) if row.get("mint") in targets
        }
        rows = []
        for mint in sorted(targets, key=lambda value: ordinals[value]):
            rows.append(process_mint(mint, ordinals[mint], create_by_mint[mint], events_by_mint.get(mint, []), contract))
            built_mints.add(mint)
        header = {
            "schema": SCHEMA + "_PARTITION",
            "partitionKey": report["partitionKey"],
            "sourceSha256": source_sha,
            "contractSha256": contract_sha,
            "boundaryOrdinal": boundary,
            "targetMintCount": len(targets),
            "rowCount": len(rows),
            "mints": sorted(targets),
            "collectionCounters": counters,
        }
        write_partition(destination, header, rows)
        manifests.append(header | {"reused": False, "path": str(destination), "sha256": sha256_file(destination)})
        print(json.dumps({"partition": report["partitionKey"], "targets": len(targets), "built": len(rows)}, sort_keys=True), flush=True)

    missing = selected - built_mints
    if missing:
        missing_rows = [{"mint": mint, "ordinal": ordinals[mint], "status": "EXCLUDED", "reason": "READINESS_PARTITION_NOT_FOUND", "eventStates": []} for mint in sorted(missing, key=lambda value: ordinals[value])]
        destination = args.output / "partitions" / "missing-readiness.jsonl.gz"
        header = {"schema": SCHEMA + "_PARTITION", "partitionKey": "MISSING", "sourceSha256": None, "contractSha256": contract_sha, "boundaryOrdinal": boundary, "targetMintCount": len(missing), "rowCount": len(missing_rows), "mints": sorted(missing)}
        write_partition(destination, header, missing_rows)
        manifests.append(header | {"reused": False, "path": str(destination), "sha256": sha256_file(destination)})

    population = {
        "contractSha256": contract_sha,
        "discoveryOrdinalStart": 1,
        "discoveryOrdinalEnd": boundary,
        "exactAdmittedUniqueMints": len(prefix),
        "discoveryPrefixSha256": prefix_sha,
        "coveredStartUtc": iso_ms(start_ms),
        "coveredEndUtc": iso_ms(end_ms),
        "totalObservedLaunchesInCoveredTimeRange": observed_in_range,
        "exactAdmittedLaunchesInCoveredTimeRange": observed_exact_admitted,
        "admissionRateInCoveredTimeRange": observed_exact_admitted / observed_in_range if observed_in_range else None,
    }
    report = aggregate(args.output, contract, population)
    report_payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (args.output / "report.json").write_text(report_payload, encoding="utf-8")
    (args.output / "report.md").write_text(markdown(report), encoding="utf-8")
    provenance = {
        **identity,
        "generatedAtUtc": report["generatedAtUtc"],
        "reportSha256": hashlib.sha256(report_payload.encode()).hexdigest(),
        "partitions": manifests,
        "validationRowsInspected": 0,
        "holdoutRowsInspected": 0,
    }
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "COMPLETE",
        "primary": report["diagnosis"]["primary"],
        "secondary": report["diagnosis"]["secondary"],
        "evaluableMints": report["population"]["exactEvaluableUniqueMints"],
        "output": str(args.output),
    }, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
