#!/usr/bin/env python3
"""Build exact causal event-state rows for EVENT_FLOW_COMPETING_RISK_V1."""

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

from buildPumpExactAlphaResearchV1 import NETWORK_FEE, apply_group
from exactCurveEconomicsV1 import quote_exact_quote_in_buy, quote_exact_token_sell
from executableOpportunityCensusV1 import group_events, latest_actual_base, state_dict
from sandboxPumpExactFollowThroughAtlasV1 import SOL_QUOTES, collect_partition, load_prefix
from validatePumpExactEvidenceLedgerV1 import event_fees


SCHEMA = "EVENT_FLOW_COMPETING_RISK_V1"
LAMPORTS = 1_000_000_000


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_ms(value: int) -> str:
    return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc).isoformat()


def safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def external_group(group: dict[str, Any]) -> dict[str, Any] | None:
    rows = [row for row in group["rows"] if row.event.user != row.event.creator]
    if not rows:
        return None
    buy_rows = [row for row in rows if row.event.is_buy]
    sell_rows = [row for row in rows if not row.event.is_buy]
    buy = sum(int(row.event.immediate_quote_amount) for row in buy_rows)
    sell = sum(int(row.event.immediate_quote_amount) for row in sell_rows)
    return {
        "group": group,
        "arrivalMs": int(group["arrivalMs"]),
        "buyLamports": buy,
        "sellLamports": sell,
        "signedLamports": buy - sell,
        "buyEventN": len(buy_rows),
        "sellEventN": len(sell_rows),
        "buyUsers": [row.event.user for row in buy_rows],
    }


def feature_row(history: list[dict[str, Any]], create_ms: int, group: dict[str, Any], gross: int) -> tuple[dict[str, float | None], dict[str, Any]]:
    recent5 = history[-5:]
    recent3 = history[-3:]
    prior3 = history[-6:-3]
    signs = [1 if row["signedLamports"] > 0 else -1 if row["signedLamports"] < 0 else 0 for row in recent5]
    weights = list(range(1, len(signs) + 1))
    consecutive = 0
    for row in reversed(history):
        if row["buyLamports"] <= 0 or row["sellLamports"] > 0:
            break
        consecutive += 1
    gaps = [right["arrivalMs"] - left["arrivalMs"] for left, right in zip(history[-6:], history[-5:])]
    recent_gaps = gaps[-3:]
    prior_gaps = gaps[-6:-3]
    recent_rate = 1000 / statistics.mean(recent_gaps) if recent_gaps and statistics.mean(recent_gaps) > 0 else None
    prior_rate = 1000 / statistics.mean(prior_gaps) if prior_gaps and statistics.mean(prior_gaps) > 0 else None
    buys_recent = [row["buyLamports"] / LAMPORTS for row in recent5 if row["buyLamports"] > 0]
    buys_prior = [row["buyLamports"] / LAMPORTS for row in history[-10:-5] if row["buyLamports"] > 0]
    prior_buyers: set[str] = set()
    repeats = 0
    total_buys = 0
    for row in history:
        for user in row["buyUsers"]:
            if row in recent5:
                total_buys += 1
                repeats += int(user in prior_buyers)
            prior_buyers.add(user)
    before_window = {user for row in history[:-5] for user in row["buyUsers"]}
    recent_buyers = {user for row in recent5 for user in row["buyUsers"]}
    last_sell = next((row["arrivalMs"] for row in reversed(history) if row["sellEventN"] > 0), None)
    state = group["postState"]
    fees = event_fees(group["feeEvent"])
    own_buy = quote_exact_quote_in_buy(state, gross, fees)
    no_impact_tokens = own_buy.pricing_quote_in * state.virtual_token / state.virtual_sol
    features: dict[str, float | None] = {
        "signed_sequence_recency_last5": safe_ratio(sum(sign * weight for sign, weight in zip(signs, weights)), sum(weights)),
        "consecutive_pure_buy_groups": float(consecutive),
        "sell_event_count_last5": float(sum(row["sellEventN"] for row in recent5)),
        "time_since_previous_external_group_ms": float(history[-1]["arrivalMs"] - history[-2]["arrivalMs"]) if len(history) >= 2 else None,
        "median_interarrival_last5_ms": statistics.median(gaps) if gaps else None,
        "arrival_rate_change_ratio": safe_ratio(recent_rate, prior_rate) if recent_rate is not None and prior_rate is not None else None,
        "gross_buy_last5_sol": sum(row["buyLamports"] for row in recent5) / LAMPORTS,
        "gross_sell_last5_sol": sum(row["sellLamports"] for row in recent5) / LAMPORTS,
        "net_flow_last5_sol": sum(row["signedLamports"] for row in recent5) / LAMPORTS,
        "signed_flow_acceleration_sol": (sum(row["signedLamports"] for row in recent3) - sum(row["signedLamports"] for row in prior3)) / LAMPORTS,
        "latest_signed_size_sol": history[-1]["signedLamports"] / LAMPORTS,
        "median_abs_size_last5_sol": statistics.median(abs(row["signedLamports"]) / LAMPORTS for row in recent5),
        "buy_size_progression_ratio": safe_ratio(statistics.mean(buys_recent), statistics.mean(buys_prior)) if buys_recent and buys_prior else None,
        "new_unique_buyers_last5": float(len(recent_buyers - before_window)),
        "repeat_buyer_fraction_last5": safe_ratio(repeats, total_buys),
        "time_since_last_sell_ms": float(history[-1]["arrivalMs"] - last_sell) if last_sell is not None else None,
        "token_age_ms": float(history[-1]["arrivalMs"] - create_ms),
        "cumulative_net_external_flow_sol": sum(row["signedLamports"] for row in history) / LAMPORTS,
        "current_virtual_sol": state.virtual_sol / LAMPORTS,
        "hypothetical_entry_impact_bps": (1 - own_buy.token_out / no_impact_tokens) * 10_000 if no_impact_tokens else None,
    }
    context = {
        "decisionState": state_dict(state),
        "protocolFeeBps": fees.protocol_bps,
        "creatorFeeBps": fees.creator_bps,
        "externalGroupCount": len(history),
        "externalBuyEventCount": sum(row["buyEventN"] for row in history),
        "externalSellEventCount": sum(row["sellEventN"] for row in history),
    }
    return features, context


def point_at(timeline: list[dict[str, Any]], timestamp: int) -> dict[str, Any]:
    eligible = [point for point in timeline if point["timestamp"] <= timestamp]
    return eligible[-1] if eligible else timeline[0]


def sell_quote(state: Any, inventory: int, fee_event: Any) -> Any:
    return quote_exact_token_sell(
        state, inventory, 1, event_fees(fee_event),
        cashback_bps=int(fee_event.cashback_fee_basis_points or 0),
        buyback_share_bps=int(fee_event.buyback_fee_basis_points or 0),
    )


def evaluate_target(decision: dict[str, Any], groups: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    economics = contract["economics"]
    decision_ms = int(decision["decisionMs"])
    entry_ms = decision_ms + int(economics["entryOffsetMs"])
    first_sellable_ms = decision_ms + int(economics["firstSellableOffsetMs"])
    horizon_ms = decision_ms + int(economics["maximumHorizonMsFromDecision"])
    gross = int(round(float(economics["positionSizeSol"]) * LAMPORTS))
    capital = gross + NETWORK_FEE
    delivery = int(round(float(economics["standardizedDeliveryOverlaySol"]) * LAMPORTS))
    try:
        base_state, fee_event, future_index, evidence_id = latest_actual_base(decision, groups, entry_ms)
        buy = quote_exact_quote_in_buy(base_state, gross, event_fees(fee_event))
        immediate = quote_exact_quote_in_buy(decision["baseState"], gross, event_fees(decision["feeEvent"]))
    except Exception as error:
        return {"status": "ENTRY_UNSUPPORTED", "reason": str(error)}
    state = buy.post_state
    timeline = [{"timestamp": entry_ms, "state": state, "feeEvent": fee_event, "external": False, "evidenceId": evidence_id}]
    attempted = failed = 0
    for index in range(future_index, len(groups)):
        group = groups[index]
        if group["arrivalMs"] <= entry_ms:
            continue
        if group["arrivalMs"] > horizon_ms:
            break
        attempted += 1
        try:
            state = apply_group(state, group["rows"])
            fee_event = group["feeEvent"]
            status = "APPLIED"
        except Exception:
            failed += 1
            status = "COUNTERFACTUAL_CONSTRAINT_FAILURE"
        timeline.append({
            "timestamp": int(group["arrivalMs"]), "state": state, "feeEvent": fee_event,
            "external": external_group(group) is not None, "status": status,
            "evidenceId": group["canonicalEvidenceId"],
        })
    timeline.append({"timestamp": horizon_ms, "state": state, "feeEvent": fee_event, "external": False, "status": "ENDPOINT", "evidenceId": timeline[-1]["evidenceId"]})
    sample_times = [first_sellable_ms]
    sample_times.extend(point["timestamp"] for point in timeline if point.get("external") and first_sellable_ms <= point["timestamp"] <= horizon_ms)
    sample_times.append(horizon_ms)
    samples = []
    for timestamp in sorted(set(sample_times)):
        point = point_at(timeline, timestamp)
        try:
            sell = sell_quote(point["state"], buy.token_out, point["feeEvent"])
        except Exception:
            continue
        proceeds = sell.immediate_quote_out - NETWORK_FEE
        pnl = proceeds - capital - delivery
        samples.append({"timestamp": timestamp, "roiPct": pnl * 100 / capital, "pnlSol": pnl / LAMPORTS})
    if not samples:
        return {"status": "EXIT_UNSUPPORTED", "reason": "NO_EXACT_SELL_SAMPLE"}
    outcome = "FLOW_DECAY_TIMEOUT"
    selected = samples[-1]
    for sample in samples:
        if sample["roiPct"] >= float(economics["positiveBoundaryPct"]):
            outcome, selected = "PROFITABLE_CONTINUATION", sample
            break
        if sample["roiPct"] <= float(economics["adverseBoundaryPct"]):
            outcome, selected = "ADVERSE_FLOW", sample
            break
    return {
        "status": "EXACT",
        "outcome": outcome,
        "outcomeAtMs": selected["timestamp"],
        "timeToOutcomeMs": selected["timestamp"] - decision_ms,
        "returnPct": selected["roiPct"],
        "pnlSol": selected["pnlSol"],
        "entryMs": entry_ms,
        "firstSellableMs": first_sellable_ms,
        "tokensReceivedRaw": buy.token_out,
        "entryState": state_dict(base_state),
        "entryInventoryDeteriorationPct": (1 - buy.token_out / immediate.token_out) * 100 if immediate.token_out else None,
        "counterfactualAttemptedTransactions": attempted,
        "counterfactualFailedTransactions": failed,
    }


def process_mint(index_row: dict[str, Any], events: list[Any], contract: dict[str, Any]) -> dict[str, Any]:
    mint = index_row["mint"]
    create_ms = int(index_row["createReceivedAtUnixNs"]) // 1_000_000
    max_age = int(contract["decisionStates"]["maximumMintAgeMs"])
    horizon = int(contract["economics"]["maximumHorizonMsFromDecision"])
    events = [row for row in events if row.event.quote_mint in SOL_QUOTES and row.arrival_ms <= create_ms + max_age + horizon + 1000]
    if not events:
        return {"mint": mint, "ordinal": index_row["ordinal"], "status": "EXCLUDED", "reason": "NO_SOL_EVENTS"}
    if any(row.event.mayhem_mode for row in events):
        return {"mint": mint, "ordinal": index_row["ordinal"], "status": "EXCLUDED", "reason": "MAYHEM_EXCLUDED"}
    groups = group_events(events)
    external_history: list[dict[str, Any]] = []
    decisions = []
    maximum_groups = int(contract["decisionStates"]["maximumExternalTransactionGroupsPerMint"])
    gross = int(round(float(contract["economics"]["positionSizeSol"]) * LAMPORTS))
    for group_index, group in enumerate(groups):
        external = external_group(group)
        if external is None:
            continue
        age = int(group["arrivalMs"]) - create_ms
        if age > max_age or len(external_history) >= maximum_groups:
            break
        external_history.append(external)
        decision = {
            "decisionMs": int(group["arrivalMs"]), "baseState": group["postState"],
            "feeEvent": group["feeEvent"], "futureIndex": group_index + 1,
            "triggerSignature": group["signature"], "triggerSlot": group["slot"],
        }
        try:
            features, context = feature_row(external_history, create_ms, group, gross)
        except Exception as error:
            decisions.append({"stateIndex": len(external_history), "status": "FEATURE_UNSUPPORTED", "reason": str(error)})
            continue
        target = evaluate_target(decision, groups, contract)
        decisions.append({
            "stateIndex": len(external_history), "status": "EXACT" if target.get("status") == "EXACT" else "EXCLUDED",
            "decisionUnixMs": decision["decisionMs"], "decisionUtc": iso_ms(decision["decisionMs"]),
            "mintAgeMs": age, "triggerSignature": group["signature"], "triggerSlot": group["slot"],
            "features": features, "context": context, "target": target,
            "sourceEvidenceIds": sorted(set(group["observationEvidenceIds"] + ([group["canonicalEvidenceId"]] if group["canonicalEvidenceId"] else []))),
        })
    exact_n = sum(row.get("status") == "EXACT" for row in decisions)
    return {
        "schema": SCHEMA + "_MINT", "mint": mint, "ordinal": int(index_row["ordinal"]),
        "partitionKey": index_row["partitionKey"], "createUnixMs": create_ms, "createUtc": iso_ms(create_ms),
        "status": "EXACT_EVALUABLE" if exact_n else "EXCLUDED",
        "reason": None if exact_n else "NO_EXACT_EVENT_STATE", "exactStateN": exact_n, "decisionStates": decisions,
    }


def write_partition(path: pathlib.Path, header: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"header": header}, sort_keys=True, separators=(",", ":")) + "\n")
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=pathlib.Path)
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("contract", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema") != SCHEMA + "_CONTRACT":
        raise RuntimeError("CONTRACT_SCHEMA_MISMATCH")
    contract_sha = sha256_file(args.contract)
    script_sha = sha256_file(pathlib.Path(__file__))
    end = int(contract["population"]["ordinalEnd"])
    prefix, ordinals = load_prefix(args.research_root, end)
    prefix_sha = hashlib.sha256("\n".join(f"{row['ordinal']}:{row['mint']}" for row in prefix).encode()).hexdigest()
    if prefix_sha != contract["parents"]["discoveryPrefixSha256"]:
        raise RuntimeError("DISCOVERY_PREFIX_HASH_MISMATCH")
    args.output.mkdir(parents=True, exist_ok=True)
    identity = {
        "schema": SCHEMA + "_RUN_IDENTITY", "contractSha256": contract_sha,
        "scriptSha256": script_sha, "discoveryPrefixSha256": prefix_sha, "ordinalRange": [1, end],
    }
    identity_path = args.output / "run-identity.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise RuntimeError("IMMUTABLE_RUN_IDENTITY_MISMATCH")
    identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output / "contract.json").write_bytes(args.contract.read_bytes())

    by_partition: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in prefix:
        by_partition[row["partitionKey"]].append(row)
    manifests = []
    for partition, index_rows in sorted(by_partition.items()):
        key = partition.replace("/", "-")
        readiness_path = args.evidence_root / "readiness-v1" / "partitions" / f"{key}-v4.json"
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        output_path = args.output / "partitions" / f"{key}.jsonl.gz"
        source_identity = {
            "contractSha256": contract_sha, "scriptSha256": script_sha,
            "readinessSha256": sha256_file(readiness_path),
            "sourceReconciliationManifestSha256": readiness.get("sourceReconciliationManifestSha256"),
            "mints": [row["mint"] for row in index_rows],
        }
        source_sha = hashlib.sha256(json.dumps(source_identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if output_path.exists():
            with gzip.open(output_path, "rt", encoding="utf-8") as handle:
                header = json.loads(next(handle))["header"]
            if header.get("sourceIdentitySha256") != source_sha:
                raise RuntimeError(f"IMMUTABLE_PARTITION_MISMATCH:{partition}")
            manifests.append(header | {"path": str(output_path), "sha256": sha256_file(output_path), "reused": True})
            continue
        targets = {row["mint"] for row in index_rows}
        events_by_mint, counters = collect_partition(args.evidence_root, readiness, targets)
        rows = [process_mint(row, events_by_mint.get(row["mint"], []), contract) for row in index_rows]
        header = {
            "schema": SCHEMA + "_PARTITION", "partitionKey": partition,
            "sourceIdentitySha256": source_sha, "contractSha256": contract_sha,
            "scriptSha256": script_sha, "mintN": len(rows),
            "exactMintN": sum(row["status"] == "EXACT_EVALUABLE" for row in rows),
            "exactStateN": sum(row.get("exactStateN", 0) for row in rows), "collectionCounters": counters,
        }
        write_partition(output_path, header, rows)
        manifests.append(header | {"path": str(output_path), "sha256": sha256_file(output_path), "reused": False})
        print(json.dumps({"partition": partition, "mints": len(rows), "exactStates": header["exactStateN"]}), flush=True)
    provenance = {
        "schema": SCHEMA + "_BUILD_PROVENANCE", "contractSha256": contract_sha,
        "scriptSha256": script_sha, "discoveryPrefixSha256": prefix_sha,
        "partitions": manifests, "validationRowsInspected": 0, "holdoutRowsInspected": 0,
    }
    (args.output / "build-provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "BUILD_COMPLETE", "partitions": len(manifests), "exactStates": sum(row["exactStateN"] for row in manifests)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
