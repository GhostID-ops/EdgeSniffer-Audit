#!/usr/bin/env python3
"""Evidence coverage census and fixed-cohort exact economic bridge."""

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

from buildPumpExactAlphaResearchV1 import (
    NETWORK_FEE, TRADE_KINDS, apply_group, pair_events, paths_for_report,
    retain_pump_trade_arrival, retain_target_arrival,
)
from exactCurveEconomicsV1 import Fees, quote_exact_quote_in_buy, quote_exact_token_sell
from executableOpportunityCensusV1 import group_events, latest_actual_base, state_dict
from reconcilePumpExactEvidenceLedgerV1 import iter_frames
from sandboxPumpExactFollowThroughAtlasV1 import SOL_QUOTES, load_prefix
from validatePumpExactEvidenceLedgerV1 import event_fees


SCHEMA = "EVIDENCE_UNIVERSE_AND_STATIC_COST_BRIDGE_V1"
LAMPORTS = 1_000_000_000
CATEGORIES = (
    "INERT_OR_NO_EXTERNAL_ACTIVITY",
    "ACTIVITY_EXISTED_BUT_NO_ELIGIBLE_DECISION_STATE",
    "RELEVANT_ACTIVITY_BUT_SOURCE_DATA_INCOMPLETE",
    "EXACT_REPLAY_OR_DIVERGENCE_FAILURE",
    "UNSUPPORTED_PROGRAM_TRANSACTION_OR_PAIR_FORMAT",
    "PARSER_COLLECTOR_OR_SOURCE_GAP",
    "OTHER_DOCUMENTED_REASON",
)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_event_mints(root: pathlib.Path) -> dict[str, dict[str, Any]]:
    output = {}
    for path in sorted((root / "partitions").glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            next(handle)
            for line in handle:
                row = json.loads(line)
                if int(row["ordinal"]) > 6848:
                    raise RuntimeError("FORBIDDEN_EVENT_ORDINAL")
                output[row["mint"]] = row
    return output


def read_selected(path: pathlib.Path) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["policy"] in ("top10pct", "top5pct"):
                if int(row["ordinal"]) > 6848:
                    raise RuntimeError("FORBIDDEN_SELECTED_ORDINAL")
                output[row["mint"]].append(row)
    return output


def block_for(ordinal: int, blocks: list[list[int]]) -> int:
    return next(index for index, (start, end) in enumerate(blocks, 1) if start <= ordinal <= end)


def scan_partition(evidence_root: pathlib.Path, readiness: dict[str, Any], targets: set[str]) -> tuple[dict[str, list[Any]], dict[str, dict[str, Any]], int]:
    raw_arrival: dict[str, int] = {}
    raw_evidence: dict[str, str] = {}
    events: dict[str, list[Any]] = collections.defaultdict(list)
    facts = {mint: {
        "rawTargetTradeN": 0, "rawSuccessfulTargetTradeN": 0, "finalizedTargetTradeN": 0,
        "successfulFinalizedTradeN": 0, "failedFinalizedTradeN": 0,
        "explicitGapN": 0, "missingArrivalN": 0, "decodeFailureN": 0,
        "missingArrivalExternalEventN": 0, "missingArrivalExternalSolEventN": 0,
        "missingArrivalExternalSolMinBlockTimeMs": None,
        "missingArrivalExternalSolUnknownBlockTimeN": 0,
        "hasTradeEventN": 0, "instructionKinds": collections.Counter(),
    } for mint in targets}
    transaction_frames = 0
    for path in paths_for_report(evidence_root, readiness):
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            kind = metadata.get("kind")
            signature = str(metadata.get("signature") or "")
            discovered = targets.intersection(metadata.get("discoveredMints") or [])
            kinds = set(metadata.get("pumpInstructionKinds") or [])
            is_trade = bool(kinds & TRADE_KINDS)
            if kind == "transaction":
                transaction_frames += 1
                if signature:
                    observed_ms = int(metadata.get("receivedAtUnixNs") or 0) // 1_000_000
                    evidence_id = str(metadata.get("evidenceId") or "")
                    retain_pump_trade_arrival(raw_arrival, raw_evidence, signature, observed_ms, evidence_id, kinds)
                    retain_target_arrival(raw_arrival, raw_evidence, signature, observed_ms, evidence_id, discovered)
                if is_trade:
                    for mint in discovered:
                        facts[mint]["rawTargetTradeN"] += 1
                        facts[mint]["rawSuccessfulTargetTradeN"] += int(metadata.get("successful") is not False and not metadata.get("failed"))
                        facts[mint]["instructionKinds"].update(kinds)
            elif kind == "rpc_finalized_transaction" and signature and discovered and is_trade:
                for mint in discovered:
                    facts[mint]["finalizedTargetTradeN"] += 1
                    facts[mint]["instructionKinds"].update(kinds)
                    facts[mint]["hasTradeEventN"] += int(bool(metadata.get("hasTradeEvent")))
                    explicit = any(bool(metadata.get(key)) for key in (
                        "decodeFailed", "missingCanonicalOrdering", "missingFeeEvidence",
                        "missingRawTx", "missingReserveTransition", "missingTradeEvent", "unresolved",
                    ))
                    facts[mint]["explicitGapN"] += int(explicit)
                try:
                    transaction = json.loads(raw).get("result")
                    success = bool(transaction) and (transaction.get("meta") or {}).get("err") is None
                    for mint in discovered:
                        facts[mint]["successfulFinalizedTradeN"] += int(success)
                        facts[mint]["failedFinalizedTradeN"] += int(not success)
                    if not success:
                        continue
                    arrival = raw_arrival.get(signature)
                    if arrival is None:
                        classification_arrival = int(metadata.get("receivedAtUnixNs") or 0) // 1_000_000
                        decoded_without_causal_arrival = pair_events(
                            transaction, signature, "", str(metadata.get("evidenceId") or ""),
                            classification_arrival, int(metadata.get("slot") or 0),
                            int(metadata.get("transactionIndex") or 0), targets,
                        )
                        for mint in discovered:
                            facts[mint]["missingArrivalN"] += 1
                            related = [row for row in decoded_without_causal_arrival if row.event.mint == mint and row.event.user != row.event.creator]
                            related_sol = [row for row in related if row.event.quote_mint in SOL_QUOTES]
                            facts[mint]["missingArrivalExternalEventN"] += len(related)
                            facts[mint]["missingArrivalExternalSolEventN"] += len(related_sol)
                            if related_sol:
                                block_time = transaction.get("blockTime")
                                if block_time is None:
                                    facts[mint]["missingArrivalExternalSolUnknownBlockTimeN"] += len(related_sol)
                                else:
                                    block_ms = int(block_time) * 1000
                                    current = facts[mint]["missingArrivalExternalSolMinBlockTimeMs"]
                                    facts[mint]["missingArrivalExternalSolMinBlockTimeMs"] = block_ms if current is None else min(current, block_ms)
                        continue
                    decoded = pair_events(
                        transaction, signature, raw_evidence.get(signature, ""),
                        str(metadata.get("evidenceId") or ""), arrival,
                        int(metadata.get("slot") or 0), int(metadata.get("transactionIndex") or 0), targets,
                    )
                except Exception:
                    for mint in discovered:
                        facts[mint]["decodeFailureN"] += 1
                    continue
                for event in decoded:
                    events[event.event.mint].append(event)
    for mint in events:
        deduped = {(row.signature, row.instruction_index): row for row in events[mint]}
        events[mint] = sorted(deduped.values(), key=lambda row: (row.arrival_ms, row.slot, row.tx_index, row.instruction_index))
    for mint in facts:
        facts[mint]["instructionKinds"] = dict(facts[mint]["instructionKinds"])
    return events, facts, transaction_frames


def classify_mint(index: dict[str, Any], event_row: dict[str, Any], events: list[Any], facts: dict[str, Any], max_age_ms: int) -> tuple[str, dict[str, Any]]:
    create_ms = int(index["createReceivedAtUnixNs"]) // 1_000_000
    external = [row for row in events if row.event.user != row.event.creator]
    external_sol = [row for row in external if row.event.quote_mint in SOL_QUOTES]
    eligible = [row for row in external_sol if row.arrival_ms <= create_ms + max_age_ms]
    missing_block_ms = facts["missingArrivalExternalSolMinBlockTimeMs"]
    missing_could_affect_window = bool(
        facts["missingArrivalExternalSolEventN"]
        and (
            facts["missingArrivalExternalSolUnknownBlockTimeN"]
            or missing_block_ms is None
            # Block time has one-second precision. Retain the boundary second as
            # potentially eligible instead of manufacturing false certainty.
            or int(missing_block_ms) <= create_ms + max_age_ms + 999
        )
    )
    all_activity = len(events)
    details = {
        "decodedEconomicEventN": all_activity,
        "decodedExternalEventN": len(external),
        "decodedExternalSolEventN": len(external_sol),
        "eligibleExternalSolEventN": len(eligible),
        "missingArrivalCouldAffectEligibility": missing_could_affect_window,
        **facts,
    }
    if event_row.get("status") == "EXACT_EVALUABLE":
        return "EXACT_EVALUABLE", details
    if event_row.get("decisionStates"):
        return "EXACT_REPLAY_OR_DIVERGENCE_FAILURE", details
    if facts["explicitGapN"] or facts["decodeFailureN"]:
        return "PARSER_COLLECTOR_OR_SOURCE_GAP", details
    if missing_could_affect_window:
        return "RELEVANT_ACTIVITY_BUT_SOURCE_DATA_INCOMPLETE", details
    if facts["missingArrivalExternalEventN"] and not facts["missingArrivalExternalSolEventN"]:
        return "UNSUPPORTED_PROGRAM_TRANSACTION_OR_PAIR_FORMAT", details
    # A successful Pump instruction with a TradeEvent but no decodable economic
    # row is an unsupported evidence shape. Creator-only rows that did decode
    # are handled by the benign activity branch below.
    if facts["successfulFinalizedTradeN"] and facts["hasTradeEventN"] and not events:
        return "UNSUPPORTED_PROGRAM_TRANSACTION_OR_PAIR_FORMAT", details
    if external or facts["missingArrivalExternalSolEventN"]:
        if not external_sol:
            return "ACTIVITY_EXISTED_BUT_NO_ELIGIBLE_DECISION_STATE", details
        return "ACTIVITY_EXISTED_BUT_NO_ELIGIBLE_DECISION_STATE", details
    if all_activity or facts["failedFinalizedTradeN"] or facts["successfulFinalizedTradeN"]:
        return "INERT_OR_NO_EXTERNAL_ACTIVITY", details
    if facts["rawSuccessfulTargetTradeN"]:
        return "OTHER_DOCUMENTED_REASON", details
    return "INERT_OR_NO_EXTERNAL_ACTIVITY", details


def historical_state_at(groups: list[dict[str, Any]], timestamp: int, fallback: Any) -> Any:
    state = fallback
    for group in groups:
        if int(group["arrivalMs"]) > timestamp:
            break
        state = group["postState"]
    return state


def zero_fee_sell(state: Any, inventory: int) -> Any:
    return quote_exact_token_sell(state, inventory, 1, Fees(0, 0, 0), cashback_bps=0, buyback_share_bps=0)


def decompose_selected(selected: dict[str, Any], events: list[Any], create_ms: int, economics: dict[str, Any]) -> dict[str, Any]:
    sol_events = [row for row in events if row.event.quote_mint in SOL_QUOTES and row.arrival_ms <= create_ms + int(economics["maximumHorizonMsFromDecision"]) + 30000 + 1000]
    groups = group_events(sol_events)
    external_history = []
    decision = None
    future_index = None
    for group_index, group in enumerate(groups):
        rows = [row for row in group["rows"] if row.event.user != row.event.creator]
        if not rows:
            continue
        external_history.append(group)
        if len(external_history) == int(selected["stateIndex"]):
            decision = {"decisionMs": int(group["arrivalMs"]), "baseState": group["postState"], "feeEvent": group["feeEvent"], "futureIndex": group_index + 1}
            future_index = group_index + 1
            break
    if decision is None or int(decision["decisionMs"]) != int(selected["decisionUnixMs"]):
        return {"status": "STATE_ALIGNMENT_FAILURE"}
    decision_ms = int(decision["decisionMs"])
    entry_ms = decision_ms + int(economics["entryOffsetMs"])
    exit_ms = decision_ms + int(selected["timeToOutcomeMs"])
    gross = int(round(float(economics["positionSizeSol"]) * LAMPORTS))
    delivery = int(round(float(economics["standardizedDeliveryOverlaySol"]) * LAMPORTS))
    try:
        entry_state, entry_fee_event, replay_index, _ = latest_actual_base(decision, groups, entry_ms)
        actual_buy = quote_exact_quote_in_buy(entry_state, gross, event_fees(entry_fee_event))
        no_fee_buy = quote_exact_quote_in_buy(entry_state, gross, Fees(0, 0, 0))
    except Exception as error:
        return {"status": "ENTRY_FAILURE", "reason": str(error)}
    counterfactual_state = actual_buy.post_state
    counterfactual_fee_event = entry_fee_event
    attempted = failed = 0
    for index in range(replay_index, len(groups)):
        group = groups[index]
        if int(group["arrivalMs"]) <= entry_ms:
            continue
        if int(group["arrivalMs"]) > exit_ms:
            break
        attempted += 1
        try:
            counterfactual_state = apply_group(counterfactual_state, group["rows"])
            counterfactual_fee_event = group["feeEvent"]
        except Exception:
            failed += 1
    historical_exit_state = historical_state_at(groups, exit_ms, decision["baseState"])
    decision_state = decision["baseState"]
    decision_spot_tokens = gross * decision_state.virtual_token // decision_state.virtual_sol
    entry_spot_tokens = gross * entry_state.virtual_token // entry_state.virtual_sol

    def spot_value(inventory: int, state: Any) -> int:
        return inventory * state.virtual_sol // state.virtual_token

    try:
        no_fee_exit = zero_fee_sell(counterfactual_state, actual_buy.token_out)
        actual_exit = quote_exact_token_sell(
            counterfactual_state, actual_buy.token_out, 1, event_fees(counterfactual_fee_event),
            cashback_bps=int(counterfactual_fee_event.cashback_fee_basis_points or 0),
            buyback_share_bps=int(counterfactual_fee_event.buyback_fee_basis_points or 0),
        )
    except Exception as error:
        return {"status": "EXIT_FAILURE", "reason": str(error)}
    stages = collections.OrderedDict()
    stages["idealized_external_market_opportunity"] = spot_value(decision_spot_tokens, historical_exit_state) - gross
    stages["decision_to_landing_movement"] = spot_value(entry_spot_tokens, historical_exit_state) - gross
    stages["own_entry_impact"] = spot_value(no_fee_buy.token_out, historical_exit_state) - gross
    stages["buy_pump_fees"] = spot_value(actual_buy.token_out, historical_exit_state) - gross
    stages["counterfactual_path_interaction"] = spot_value(actual_buy.token_out, counterfactual_state) - gross
    stages["own_exit_impact"] = no_fee_exit.immediate_quote_out - gross
    stages["sell_pump_fees"] = actual_exit.immediate_quote_out - gross
    stages["base_transaction_fees"] = stages["sell_pump_fees"] - 2 * NETWORK_FEE
    stages["delivery_overlay"] = stages["base_transaction_fees"] - delivery
    names = list(stages)
    components = {names[0]: stages[names[0]] / LAMPORTS}
    for previous, current in zip(names, names[1:]):
        components[current] = (stages[current] - stages[previous]) / LAMPORTS
    final_pnl = stages["delivery_overlay"] / LAMPORTS
    return {
        "status": "EXACT",
        "policy": selected["policy"], "mint": selected["mint"], "ordinal": selected["ordinal"],
        "stateIndex": selected["stateIndex"], "decisionUnixMs": decision_ms, "entryUnixMs": entry_ms,
        "exitUnixMs": exit_ms, "outcome": selected["outcome"], "recordedPnlSol": selected["pnlSol"],
        "bridgeFinalPnlSol": final_pnl, "reconciliationDifferenceSol": final_pnl - float(selected["pnlSol"]),
        "stageValuesSol": {name: value / LAMPORTS for name, value in stages.items()},
        "componentsSol": components,
        "rawFeesLamports": {
            "buyProtocol": actual_buy.protocol_fee, "buyCreator": actual_buy.creator_fee, "buyLp": actual_buy.lp_fee,
            "sellProtocol": actual_exit.protocol_fee, "sellCreator": actual_exit.creator_fee, "sellLp": actual_exit.lp_fee,
            "baseTransactions": 2 * NETWORK_FEE, "priorityTip": 0, "deliveryOverlay": delivery,
        },
        "counterfactualAttemptedTransactions": attempted,
        "counterfactualFailedTransactions": failed,
        "decisionState": state_dict(decision_state), "entryState": state_dict(entry_state),
        "historicalExitState": state_dict(historical_exit_state), "counterfactualExitState": state_dict(counterfactual_state),
    }


def aggregate_bridge(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exact = [row for row in rows if row["status"] == "EXACT"]
    component_names = list(exact[0]["componentsSol"]) if exact else []
    components = {name: {
        "meanSolPerTrade": statistics.mean(row["componentsSol"][name] for row in exact),
        "medianSolPerTrade": statistics.median(row["componentsSol"][name] for row in exact),
        "totalSol": sum(row["componentsSol"][name] for row in exact),
    } for name in component_names}
    fee_names = list(exact[0]["rawFeesLamports"]) if exact else []
    fees = {name: {
        "meanSolPerTrade": statistics.mean(row["rawFeesLamports"][name] for row in exact) / LAMPORTS,
        "totalSol": sum(row["rawFeesLamports"][name] for row in exact) / LAMPORTS,
    } for name in fee_names}
    return {
        "requestedN": len(rows), "exactN": len(exact),
        "failures": dict(collections.Counter(row["status"] for row in rows if row["status"] != "EXACT")),
        "components": components, "rawFees": fees,
        "meanIdealizedOpportunitySol": components.get("idealized_external_market_opportunity", {}).get("meanSolPerTrade"),
        "meanFinalPnlSol": statistics.mean(row["bridgeFinalPnlSol"] for row in exact) if exact else None,
        "totalFinalPnlSol": sum(row["bridgeFinalPnlSol"] for row in exact),
        "maximumAbsoluteReconciliationDifferenceSol": max((abs(row["reconciliationDifferenceSol"]) for row in exact), default=None),
        "divergentN": sum(row["counterfactualFailedTransactions"] > 0 for row in exact),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=pathlib.Path)
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("event_build", type=pathlib.Path)
    parser.add_argument("selected_rows", type=pathlib.Path)
    parser.add_argument("contract", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema") != SCHEMA + "_CONTRACT":
        raise RuntimeError("CONTRACT_SCHEMA_MISMATCH")
    prefix, _ = load_prefix(args.research_root, 6848)
    prefix_sha = hashlib.sha256("\n".join(f"{row['ordinal']}:{row['mint']}" for row in prefix).encode()).hexdigest()
    if prefix_sha != contract["parents"]["discoveryPrefixSha256"]:
        raise RuntimeError("DISCOVERY_PREFIX_HASH_MISMATCH")
    event_mints = read_event_mints(args.event_build)
    selected = read_selected(args.selected_rows)
    blocks = contract["population"]["chronologicalBlocks"]
    by_partition: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in prefix:
        by_partition[row["partitionKey"]].append(row)
    census_rows = []
    bridge_rows = []
    partition_activity = {}
    for partition, index_rows in sorted(by_partition.items()):
        key = partition.replace("/", "-")
        readiness_path = args.evidence_root / "readiness-v1" / "partitions" / f"{key}-v4.json"
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        targets = {row["mint"] for row in index_rows}
        events_by_mint, facts_by_mint, transaction_frames = scan_partition(args.evidence_root, readiness, targets)
        partition_activity[partition] = transaction_frames
        for index in index_rows:
            mint = index["mint"]
            category, details = classify_mint(index, event_mints[mint], events_by_mint.get(mint, []), facts_by_mint[mint], int(contract["stateEligibility"]["maximumMintAgeMs"]))
            census_rows.append({
                "mint": mint, "ordinal": int(index["ordinal"]), "partitionKey": partition,
                "block": block_for(int(index["ordinal"]), blocks), "category": category, **details,
            })
            if mint in selected:
                create_ms = int(index["createReceivedAtUnixNs"]) // 1_000_000
                for selected_row in selected[mint]:
                    bridge_rows.append(decompose_selected(selected_row, events_by_mint.get(mint, []), create_ms, contract["economicBridge"]))
        print(json.dumps({"partition": partition, "mints": len(index_rows), "transactions": transaction_frames}), flush=True)

    non_evaluable = [row for row in census_rows if row["category"] != "EXACT_EVALUABLE"]
    category_report = {}
    benign = {"INERT_OR_NO_EXTERNAL_ACTIVITY"}
    for category in CATEGORIES:
        subset = [row for row in non_evaluable if row["category"] == category]
        category_report[category] = {
            "N": len(subset), "pctAll": len(subset) / len(census_rows),
            "pctNonEvaluable": len(subset) / len(non_evaluable),
            "blocks": {str(block): sum(row["block"] == block for row in subset) for block in range(1, 6)},
            "risk": "ECONOMICALLY_BENIGN" if category in benign else "SELECTION_BIAS_DANGEROUS",
        }

    any_external = [row for row in census_rows if row["decodedExternalEventN"] > 0 or row["missingArrivalExternalEventN"] > 0]
    eligible_activity = [row for row in census_rows if row["eligibleExternalSolEventN"] > 0 or row["missingArrivalCouldAffectEligibility"]]
    source_incomplete_categories = {
        "RELEVANT_ACTIVITY_BUT_SOURCE_DATA_INCOMPLETE",
        "UNSUPPORTED_PROGRAM_TRANSACTION_OR_PAIR_FORMAT",
        "PARSER_COLLECTOR_OR_SOURCE_GAP",
        "OTHER_DOCUMENTED_REASON",
    }
    source_complete = [row for row in census_rows if row["category"] not in source_incomplete_categories]
    populations = {}
    for name, rows in (
        ("allLaunches", census_rows),
        ("anySuccessfulExternalPumpEconomicActivity", any_external),
        ("sufficientExistingCandidateStateActivity", eligible_activity),
        ("requiredSourceEvidencePresentBeforeReplay", source_complete),
    ):
        exact_n = sum(row["category"] == "EXACT_EVALUABLE" for row in rows)
        populations[name] = {"N": len(rows), "exactEvaluableN": exact_n, "coverage": exact_n / len(rows) if rows else None}

    block_report = []
    dangerous_categories = set(CATEGORIES) - benign
    for block in range(1, 6):
        rows = [row for row in census_rows if row["block"] == block]
        active = [row for row in rows if row["decodedExternalEventN"] > 0 or row["missingArrivalExternalEventN"] > 0]
        block_report.append({
            "block": block, "ordinals": blocks[block - 1], "N": len(rows),
            "exactN": sum(row["category"] == "EXACT_EVALUABLE" for row in rows),
            "rawCoverage": sum(row["category"] == "EXACT_EVALUABLE" for row in rows) / len(rows),
            "activeN": len(active),
            "activeCoverage": sum(row["category"] == "EXACT_EVALUABLE" for row in active) / len(active) if active else None,
            "dangerousN": sum(row["category"] in dangerous_categories for row in rows),
        })
    activity_cut = statistics.median(partition_activity.values())
    high_partitions = {key for key, value in partition_activity.items() if value > activity_cut}
    congestion = {}
    for name, rows in (
        ("higherActivityPartitions", [row for row in census_rows if row["partitionKey"] in high_partitions]),
        ("lowerActivityPartitions", [row for row in census_rows if row["partitionKey"] not in high_partitions]),
    ):
        active = [row for row in rows if row["decodedExternalEventN"] > 0 or row["missingArrivalExternalEventN"] > 0]
        congestion[name] = {
            "N": len(rows), "activeN": len(active),
            "activeCoverage": sum(row["category"] == "EXACT_EVALUABLE" for row in active) / len(active) if active else None,
            "dangerousN": sum(row["category"] in dangerous_categories for row in rows),
        }

    gate = contract["coverageVerdictGate"]
    active_coverage = populations["anySuccessfulExternalPumpEconomicActivity"]["coverage"] or 0
    relevant = [row for row in census_rows if row["eligibleExternalSolEventN"] > 0 or row["missingArrivalCouldAffectEligibility"]]
    dangerous_relevant = sum(row["category"] in dangerous_categories for row in relevant) / len(relevant) if relevant else 1
    worst_block = min((row["activeCoverage"] for row in block_report if row["activeCoverage"] is not None), default=0)
    if active_coverage >= gate["healthyMinimumCoverageAmongSuccessfulExternalActivity"] and dangerous_relevant <= gate["healthyMaximumDangerousLossAmongRelevantActivity"]:
        coverage_verdict = "EVIDENCE_UNIVERSE_HEALTHY_FOR_ALPHA_RESEARCH"
    elif active_coverage >= gate["partialMinimumCoverageAmongSuccessfulExternalActivity"] and worst_block >= gate["partialMinimumWorstChronologicalBlockCoverage"]:
        coverage_verdict = "EVIDENCE_UNIVERSE_PARTIAL_BUT_USABLE"
    else:
        coverage_verdict = "EVIDENCE_UNIVERSE_SELECTION_BIAS_BLOCKER"

    bridges = {policy: aggregate_bridge([row for row in bridge_rows if row.get("policy") == policy]) for policy in ("top10pct", "top5pct")}
    ideal = bridges["top10pct"]["meanIdealizedOpportunitySol"]
    diagnosis_gate = contract["economicDiagnosisGate"]
    if ideal is None or ideal <= diagnosis_gate["marketEdgeInsufficientWhenIdealizedExpectancyAtMost"]:
        economic_diagnosis = "MARKET_EDGE_INSUFFICIENT"
    elif ideal >= diagnosis_gate["executionCostsDominantWhenIdealizedExpectancyAtLeast"]:
        economic_diagnosis = "EXECUTION_COSTS_DOMINANT"
    else:
        economic_diagnosis = "MIXED_ECONOMIC_DEFICIT"

    next_branch = "evidence-pipeline repair" if coverage_verdict == "EVIDENCE_UNIVERSE_SELECTION_BIAS_BLOCKER" else "execution-path research" if economic_diagnosis == "EXECUTION_COSTS_DOMINANT" else "inventory sell-hazard loss-veto"
    report = {
        "schema": SCHEMA + "_REPORT", "coverageVerdict": coverage_verdict,
        "economicDiagnosis": economic_diagnosis, "appropriateNextResearchBranch": next_branch,
        "population": {"total": len(census_rows), "exactEvaluable": sum(row["category"] == "EXACT_EVALUABLE" for row in census_rows), "nonEvaluable": len(non_evaluable)},
        "categories": category_report, "coveragePopulations": populations,
        "dangerousLossAmongRelevantActivity": dangerous_relevant,
        "chronology": block_report, "congestion": congestion,
        "instructionKindsByNonEvaluableCategory": {
            category: dict(sum((collections.Counter(row["instructionKinds"]) for row in non_evaluable if row["category"] == category), collections.Counter()))
            for category in CATEGORIES
        },
        "bridges": bridges,
        "bridgeCaveat": "Idealized market movement uses the historical no-own-order exit state. Counterfactual path interaction separately captures the reserve/path change after inserting our buy, including failed historical transaction effects. Protocol/creator fee economic effects are combined, with raw fee totals reported separately.",
        "validationRowsInspected": 0, "holdoutRowsInspected": 0, "ordinalsAfter6848Inspected": 0,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with gzip.open(args.output / "census-rows.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in census_rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    with gzip.open(args.output / "bridge-rows.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in bridge_rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    provenance = {
        "schema": SCHEMA + "_PROVENANCE", "contractSha256": sha256_file(args.contract),
        "scriptSha256": sha256_file(pathlib.Path(__file__)), "eventBuildProvenanceSha256": sha256_file(args.event_build / "build-provenance.json"),
        "selectedRowsSha256": sha256_file(args.selected_rows), "discoveryPrefixSha256": prefix_sha,
        "reportSha256": sha256_file(args.output / "report.json"),
        "validationRowsInspected": 0, "holdoutRowsInspected": 0, "ordinalsAfter6848Inspected": 0,
    }
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"coverageVerdict": coverage_verdict, "economicDiagnosis": economic_diagnosis, "nextBranch": next_branch}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
