#!/usr/bin/env python3
"""Outcome-blind WHEN readiness for the prospective symmetric 2CQ cohort.

This materializes the selected mint's ordered, pre-entry event states for the
current development prefix. It never reads validation or economic outcomes.
Before N350 the artifact is explicitly a mutable readiness preview; at N350 it
can be frozen against the immutable development choice-set membership.
"""

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
import sqlite3
from typing import Any, Iterable

import twoCqDecisionSystemDecompositionV1 as decomposition
import twoCqProspectiveSymmetricChoiceMaterializerV1 as choice_v1
import twoCqTransitionHazardFreezeV1 as transition
from exactCurveEconomicsV1 import CurveState


SCHEMA = "2CQ_PROSPECTIVE_WHEN_READINESS_V1"
DEVELOPMENT_N = 350


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
    os.replace(temporary, path)


def write_jsonl_gz(path: pathlib.Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            for row in rows:
                stream.write(canonical(row) + b"\n")
    os.replace(temporary, path)


def state(prefix: str, row: sqlite3.Row) -> CurveState:
    return CurveState(
        virtual_sol=int(row[f"{prefix}_virtual_sol"]),
        virtual_token=int(row[f"{prefix}_virtual_token"]),
        real_sol=int(row[f"{prefix}_real_sol"]),
        real_token=int(row[f"{prefix}_real_token"]),
    )


def load_development_prefix(path: pathlib.Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        raw = list(connection.execute(
            """SELECT signature,mint,block_time_ms,slot,transaction_index,choice_json
               FROM current_decisions
               WHERE status='ONLINE_ELIGIBLE' AND choice_json IS NOT NULL
               ORDER BY block_time_ms,slot,transaction_index,signature LIMIT ?""",
            (DEVELOPMENT_N,),
        ))
    finally:
        connection.close()
    rows = []
    for ordinal, item in enumerate(raw, 1):
        choice = json.loads(str(item["choice_json"]))
        choice_v1.assert_outcome_blind(choice)
        selected = [row for row in choice.get("states") or [] if row.get("label")]
        if len(selected) != 1:
            raise RuntimeError(f"SELECTED_STATE_CARDINALITY:{item['signature']}")
        rows.append({
            "eligibleOrdinal": ordinal,
            "signature": str(item["signature"]),
            "mint": str(item["mint"]),
            "blockTimeMs": int(item["block_time_ms"]),
            "slot": int(item["slot"]),
            "transactionIndex": int(item["transaction_index"]),
            "choice": choice,
            "selectedState": selected[0],
        })
    return rows


def load_events(
    path: pathlib.Path, decisions: list[dict[str, Any]],
) -> tuple[dict[str, list[sqlite3.Row]], dict[str, list[tuple[tuple[int, int, int, str], str]]]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    events: dict[str, list[sqlite3.Row]] = {}
    incomplete: dict[str, list[tuple[tuple[int, int, int, str], str]]] = {}
    windows: dict[str, tuple[int, int]] = {}
    for decision in decisions:
        mint = decision["mint"]
        create_ns = int(decision["selectedState"]["createReceivedAtUnixNs"])
        boundary_ns = int(decision["choice"]["decisionBoundary"]["arrivalUnixNs"])
        lower_s = create_ns // 1_000_000_000 - 2
        upper_s = boundary_ns // 1_000_000_000 + 2
        previous = windows.get(mint)
        windows[mint] = (
            min(previous[0], lower_s) if previous else lower_s,
            max(previous[1], upper_s) if previous else upper_s,
        )
    try:
        for mint, (lower_s, upper_s) in windows.items():
            events[mint] = list(connection.execute(
                """SELECT e.*,t.slot,t.tx_index,t.block_time_s
                   FROM causal_events e JOIN transactions t USING(signature)
                   WHERE e.mint=? AND t.block_time_s BETWEEN ? AND ?
                   ORDER BY t.slot,t.tx_index,e.instruction_index,e.signature""",
                (mint, lower_s, upper_s),
            ))
            incomplete[mint] = [
                (
                    (int(row["slot"]), int(row["tx_index"]), -1, str(row["signature"])),
                    str(row["reason"]),
                )
                for row in connection.execute(
                    """SELECT i.signature,i.reason,t.slot,t.tx_index
                       FROM incomplete_events i JOIN transactions t USING(signature)
                       WHERE i.mint=? AND t.block_time_s BETWEEN ? AND ?""",
                    (mint, lower_s, upper_s),
                )
            ]
    finally:
        connection.close()
    return events, incomplete


def causal_events(
    raw_by_mint: dict[str, list[sqlite3.Row]], arrivals: dict[bytes, tuple[int, int]],
) -> tuple[dict[str, list[decomposition.CausalEvent]], set[str]]:
    result: dict[str, list[decomposition.CausalEvent]] = {}
    missing_arrivals: set[str] = set()
    for mint, rows in raw_by_mint.items():
        converted = []
        for row in rows:
            signature = str(row["signature"])
            arrival = arrivals.get(decomposition.tier_a.signature_digest(signature))
            if arrival is None:
                missing_arrivals.add(signature)
                continue
            converted.append(decomposition.CausalEvent(
                mint=mint,
                signature=signature,
                slot=int(row["slot"]),
                transaction_index=int(row["tx_index"]),
                instruction_index=int(row["instruction_index"]),
                received_ns=int(arrival[0]),
                is_buy=bool(row["is_buy"]),
                user=str(row["user"]),
                creator=str(row["creator"]),
                quote_lamports=int(row["quote_lamports"]),
                token_raw=int(row["token_raw"]),
                pre_state=state("pre", row),
                post_state=state("post", row),
            ))
        result[mint] = sorted(converted, key=lambda event: event.order)
    return result, missing_arrivals


def build_temporal_row(
    decision: dict[str, Any], events: list[decomposition.CausalEvent],
    incomplete: list[tuple[tuple[int, int, int, str], str]],
) -> tuple[dict[str, Any] | None, list[str]]:
    choice = decision["choice"]
    selected_state = decision["selectedState"]
    boundary = choice["decisionBoundary"]
    boundary_ns = int(boundary["arrivalUnixNs"])
    boundary_order = (
        int(boundary["slot"]), int(boundary["transactionIndex"]), -1, ""
    )
    create_ns = int(selected_state["createReceivedAtUnixNs"])
    prior = [
        event for event in events
        if event.order < boundary_order and event.received_ns < boundary_ns
    ]
    blockers = sorted({reason for order, reason in incomplete if order < boundary_order})
    if blockers:
        return None, [f"INCOMPLETE_CANONICAL_HISTORY:{reason}" for reason in blockers]
    if not choice_v1.reserve_continuous(prior):
        return None, ["RESERVE_CONTINUITY_INCOMPLETE"]
    external = [event for event in prior if event.user != decomposition.WALLET]
    if not external:
        return None, ["NO_PREENTRY_EXTERNAL_STATE"]
    expected = (
        str(selected_state["stateSignature"]),
        int(selected_state["stateInstructionIndex"]),
    )
    actual = (external[-1].signature, external[-1].instruction_index)
    if actual != expected:
        return None, ["FINAL_SELECTED_STATE_MISMATCH"]

    states = []
    for event in external:
        through = [
            prior_event for prior_event in prior
            if prior_event.order <= event.order and prior_event.received_ns <= event.received_ns
        ]
        features = decomposition.state_features(
            event.post_state, event.received_ns, decision["mint"],
            event.creator, through, create_ns,
        )
        missing = [name for name in decomposition.timing.FULL_FEATURES if name not in features]
        invalid = [
            name for name in decomposition.timing.FULL_FEATURES
            if name in features and (
                isinstance(features[name], bool) or not math.isfinite(float(features[name]))
            )
        ]
        if missing or invalid:
            return None, ["WHEN_FEATURES_MISSING_OR_INVALID"]
        states.append({
            "label": event is external[-1],
            "signature": event.signature,
            "receivedNs": event.received_ns,
            "semantics": (
                "POST_FINAL_EXTERNAL_EVENT_BEFORE_TEACHER_ENTRY"
                if event is external[-1] else "POST_EXTERNAL_EVENT"
            ),
            "features": {name: float(features[name]) for name in decomposition.timing.FULL_FEATURES},
        })
    row = {
        "schema": SCHEMA + "_TEMPORAL_EXAMPLE",
        "episodeId": decision["signature"],
        "eligibleOrdinal": decision["eligibleOrdinal"],
        "mint": decision["mint"],
        "entrySignature": decision["signature"],
        "entryReceivedAtUnixNs": boundary_ns,
        "controlN": len(states) - 1,
        "states": states,
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "submissions": 0,
    }
    choice_v1.assert_outcome_blind(row)
    return row, []


def run(args: argparse.Namespace) -> dict[str, Any]:
    decisions = load_development_prefix(args.choice_state)
    raw_events, incomplete = load_events(args.causal_state, decisions)
    signatures = {
        str(row["signature"])
        for rows in raw_events.values() for row in rows
    }
    arrivals = decomposition.targeted_compact_arrivals(
        args.compact_arrivals,
        {decomposition.tier_a.signature_digest(signature) for signature in signatures},
    )
    events, missing_arrivals = causal_events(raw_events, arrivals)
    temporal = []
    exclusions: collections.Counter[str] = collections.Counter()
    excluded_rows = []
    for decision in decisions:
        row, reasons = build_temporal_row(
            decision, events.get(decision["mint"], []), incomplete.get(decision["mint"], [])
        )
        if row is not None:
            temporal.append(row)
        else:
            exclusions.update(reasons)
            excluded_rows.append({
                "eligibleOrdinal": decision["eligibleOrdinal"],
                "signature": decision["signature"],
                "mint": decision["mint"],
                "reasons": reasons,
            })

    enriched = transition.add_transition_features(temporal)
    timing_identifiable = [row for row in enriched if int(row["controlN"]) > 0]
    expected_transition_features = set(transition.TRANSITION_FEATURES)
    transition_missing = 0
    for row in enriched:
        for item in row["states"]:
            if not expected_transition_features.issubset(item["features"]):
                transition_missing += 1

    args.output.mkdir(parents=True, exist_ok=True)
    preview_path = args.output / "same-mint-temporal-preview.jsonl.gz"
    write_jsonl_gz(preview_path, enriched)
    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": now_utc(),
        "developmentEligibleInputN": len(decisions),
        "targetN": DEVELOPMENT_N,
        "remainingToN350": max(0, DEVELOPMENT_N - len(decisions)),
        "temporalExamplesBuiltN": len(enriched),
        "timingIdentifiableN": len(timing_identifiable),
        "timingIdentifiablePct": 100 * len(timing_identifiable) / len(decisions) if decisions else 0,
        "zeroEarlierControlN": sum(int(row["controlN"]) == 0 for row in enriched),
        "missingAllenHarkTransactionSignaturesInQueriedHistoryN": len(missing_arrivals),
        "excludedEntryN": len(excluded_rows),
        "exclusionsByReason": dict(sorted(exclusions.items())),
        "excludedEntries": excluded_rows,
        "baseFeatureN": len(decomposition.timing.FULL_FEATURES),
        "transitionFeatureN": len(transition.TRANSITION_FEATURES),
        "transitionStatesWithMissingFeatureN": transition_missing,
        "previewPath": str(preview_path),
        "previewSha256": sha256_file(preview_path),
        "source": {
            "choiceState": str(args.choice_state),
            "choiceStateSha256": sha256_file(args.choice_state),
            "causalState": str(args.causal_state),
            "compactArrivals": str(args.compact_arrivals),
        },
        "integrity": {
            "developmentOrdinalsOnly": [1, min(DEVELOPMENT_N, len(decisions))],
            "validationRowsRead": 0,
            "outcomesRead": 0,
            "modelsFit": 0,
            "thresholdsTuned": 0,
            "submissions": 0,
        },
        "readyToFreezeWhenAtN350": (
            len(decisions) == DEVELOPMENT_N
            and len(timing_identifiable) == DEVELOPMENT_N
            and not excluded_rows
            and transition_missing == 0
        ),
        "verdict": (
            "WHEN_DEVELOPMENT_TABLE_READY_TO_FREEZE"
            if len(decisions) == DEVELOPMENT_N and len(timing_identifiable) == DEVELOPMENT_N
            and not excluded_rows and transition_missing == 0
            else "WHEN_DEVELOPMENT_TABLE_COLLECTING_OR_ATTRITION_PRESENT"
        ),
    }
    atomic_json(args.output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--choice-state", type=pathlib.Path, required=True)
    parser.add_argument("--causal-state", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
