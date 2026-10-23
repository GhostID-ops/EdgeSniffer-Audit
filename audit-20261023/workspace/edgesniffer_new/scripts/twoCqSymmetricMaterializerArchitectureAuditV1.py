#!/usr/bin/env python3
"""Outcome-blind attrition and architecture audit for the symmetric census."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import time
from typing import Any

import twoCqCaptureFirstDetectorRepairV1 as capture
import twoCqPostFixEndToEndCollectionDiagnosticV1 as tier_a
import twoCqProspectiveSymmetricChoiceMaterializerV1 as materializer
from exactCurveEconomicsV1 import decode_trade_events


SCHEMA = "2CQ_SYMMETRIC_MATERIALIZER_ARCHITECTURE_AUDIT_V1"
AUDITED_REASONS = {
    "SELECTED_ENTRY_EVENT_NOT_EXACTLY_JOINED",
    "SELECTED_MINT_ABSENT_FROM_SYMMETRIC_UNIVERSE",
}


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def counter(values: list[str]) -> dict[str, int]:
    return dict(sorted(collections.Counter(values).items()))


def interval_active_hours(start_ms: int, end_ms: int, intervals: list[tuple[int, int]]) -> float:
    total = max(0, end_ms - start_ms)
    invalid = sum(
        max(0, min(end_ms, right) - max(start_ms, left))
        for left, right in intervals
    )
    return max(0, total - invalid) / 3_600_000


def selected_decode_classification(tx: dict[str, Any], mint: str) -> dict[str, Any]:
    try:
        events = decode_trade_events(((tx.get("meta") or {}).get("logMessages") or []))
    except Exception as error:
        message = str(error)
        classification = (
            "UNSUPPORTED_ZERO_SOL_RESERVE_EVENT"
            if "zero virtual reserve" in message
            else "TRADE_LOG_DECODE_EXCEPTION"
        )
        return {"classification": classification, "errorType": type(error).__name__, "error": message}
    matching = [row for row in events if str(row.mint) == mint]
    wallet_matching = [
        row for row in matching
        if str(row.user) == materializer.decision.WALLET and bool(row.is_buy)
    ]
    if len(wallet_matching) == 1:
        classification = "SELECTED_EVENT_DECODABLE"
    elif not matching:
        classification = "NO_EVENT_FOR_SELECTED_MINT"
    elif not wallet_matching:
        classification = "SELECTED_EVENT_USER_OR_SIDE_MISMATCH"
    else:
        classification = "AMBIGUOUS_SELECTED_EVENT"
    return {
        "classification": classification,
        "tradeEventN": len(events),
        "selectedMintEventN": len(matching),
        "selectedWalletBuyEventN": len(wallet_matching),
    }


def result_summary(
    row: dict[str, Any] | None,
    reasons: list[str],
    detail: dict[str, Any],
) -> dict[str, Any]:
    return {
        "eligible": row is not None,
        "reasons": reasons,
        "controlN": int(row["controlN"]) if row is not None else int(detail.get("controlN") or 0),
        "candidateN": int(detail.get("eligibleCandidateN") or 0),
        "selectedExclusionReasons": list(detail.get("selectedExclusionReasons") or []),
        "selectedEntryEvidenceFailures": list(detail.get("selectedEntryEvidenceFailures") or []),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    state = json.loads(args.state.read_text(encoding="utf-8"))
    status = json.loads(args.status.read_text(encoding="utf-8"))
    if contract.get("integrity", {}).get("outcomesPermitted") is not False:
        raise RuntimeError("OUTCOME_BLIND_CONTRACT_INVALID")
    start_ms = int(tier_a.parse_utc(str(contract["cohortStartUtc"])).timestamp() * 1000)
    snapshot_ms = int(tier_a.parse_utc(str(status["atUtc"])).timestamp() * 1000)
    intervals = materializer.invalid_intervals(args.cohort)
    env = capture.denominator.load_env(args.env)
    signature_rows, transactions = capture.fetch_prospective_population(
        env["SECURE_RPC_URL"], start_ms, snapshot_ms,
        args.cohort / "wallet-rpc-cache",
    )
    classified, _ = capture.classify_successful_transactions(
        signature_rows, transactions, start_ms, snapshot_ms
    )
    episodes = [row for row in classified if row["isIndependentEntryEpisode"]]
    episode_by_signature = {str(row["signature"]): row for row in episodes}
    candidates = [
        row for row in materializer.read_jsonl(args.candidates)
        if start_ms <= int(row.get("receivedAtUnixNs") or 0) // 1_000_000 <= snapshot_ms
    ]
    candidate_by_signature = {str(row["signature"]): row for row in candidates}
    eligible_signatures = set((state.get("eligible") or {}).keys())
    terminal = state.get("terminalStatuses") or {}

    funnel_reasons: list[str] = []
    route_counts: collections.Counter[str] = collections.Counter()
    acquisition_counts: collections.Counter[str] = collections.Counter()
    for episode in episodes:
        signature = str(episode["signature"])
        route_counts[str(episode.get("route") or "UNKNOWN")] += 1
        acquisition_counts[str(episode.get("classification") or "UNKNOWN")] += 1
        if signature in eligible_signatures:
            funnel_reasons.append("MATERIALIZED_ELIGIBLE")
        elif signature in terminal:
            reasons = terminal[signature].get("reasons") or ["TERMINAL_UNKNOWN"]
            funnel_reasons.append(str(reasons[0]))
        else:
            funnel_reasons.append("DEFERRED_OR_NOT_YET_CLASSIFIED")

    audited_signatures = {
        signature for signature, row in terminal.items()
        if set(row.get("reasons") or []).intersection(AUDITED_REASONS)
    } | eligible_signatures
    audit_episodes = [
        episode_by_signature[signature]
        for signature in audited_signatures
        if signature in episode_by_signature and signature in candidate_by_signature
    ]
    ready_by_hour: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for episode in audit_episodes:
        candidate = candidate_by_signature[str(episode["signature"])]
        boundary = int(candidate["receivedAtUnixNs"])
        hour = dt.datetime.fromtimestamp(boundary / 1e9, dt.timezone.utc).strftime("%Y-%m-%dT%H")
        ready_by_hour[hour].append(episode)

    rows: list[dict[str, Any]] = []
    evidence_totals: collections.Counter[str] = collections.Counter()
    for hour in sorted(ready_by_hour):
        batch = sorted(
            ready_by_hour[hour],
            key=lambda row: (
                int(candidate_by_signature[str(row["signature"])]["receivedAtUnixNs"]),
                str(row["signature"]),
            ),
        )
        detector_boundaries = [
            int(candidate_by_signature[str(row["signature"])]["receivedAtUnixNs"])
            for row in batch
        ]
        paths, reconciliation_exceptions = materializer.partition_paths_for_boundaries(
            args.exact_root, detector_boundaries
        )
        events, creates, incomplete, evidence = materializer.scan_universe(
            paths, args.compact_arrivals, detector_boundaries
        )
        evidence_totals.update({key: value for key, value in evidence.items() if isinstance(value, int)})
        for episode in batch:
            signature = str(episode["signature"])
            mint = str(episode["mint"])
            candidate = candidate_by_signature[signature]
            base = {
                "signature": signature,
                "mint": mint,
                "route": str(episode["route"]),
                "acquisitionClass": str(episode["classification"]),
                "entryReceivedAtUnixNs": int(candidate["receivedAtUnixNs"]),
                "entrySlot": int(candidate["slot"]),
                "entryTransactionIndex": int(
                    episode["transactionIndex"] if episode.get("transactionIndex") is not None else -1
                ),
            }
            current_row, current_reasons, current_detail = materializer.build_choice(
                base, events, creates, incomplete
            )
            selected_events = [
                row for row in events.get(mint, [])
                if row.signature == signature
                and row.user == materializer.decision.WALLET
                and row.is_buy
            ]
            compact_boundary = int(selected_events[0].received_ns) if len(selected_events) == 1 else None
            if compact_boundary is not None:
                coherent = dict(base)
                coherent["entryReceivedAtUnixNs"] = compact_boundary
                coherent_row, coherent_reasons, coherent_detail = materializer.build_choice(
                    coherent, events, creates, incomplete
                )
            else:
                coherent_row, coherent_reasons, coherent_detail = (
                    None,
                    ["COMPACT_SELECTED_ENTRY_EVENT_UNAVAILABLE"],
                    {},
                )
            original = terminal.get(signature) or {}
            rows.append({
                "signature": signature,
                "mint": mint,
                "route": str(episode["route"]),
                "acquisitionClass": str(episode["classification"]),
                "originalMaterializerClassification": (
                    "MATERIALIZED_ELIGIBLE" if signature in eligible_signatures
                    else ",".join(original.get("reasons") or ["UNKNOWN"])
                ),
                "detectorReceiptUnixNs": int(base["entryReceivedAtUnixNs"]),
                "compactReceiptUnixNs": compact_boundary,
                "compactMinusDetectorMs": (
                    (compact_boundary - int(base["entryReceivedAtUnixNs"])) / 1_000_000
                    if compact_boundary is not None else None
                ),
                "selectedDecode": selected_decode_classification(transactions[signature], mint),
                "mixedClockReplay": result_summary(current_row, current_reasons, current_detail),
                "singleCompactClockReplay": result_summary(
                    coherent_row, coherent_reasons, coherent_detail
                ),
                "reconciliationExceptions": reconciliation_exceptions,
            })

    materializer.assert_outcome_blind(rows)
    originally_eligible = sum(row["mixedClockReplay"]["eligible"] for row in rows)
    coherent_eligible = sum(row["singleCompactClockReplay"]["eligible"] for row in rows)
    clock_recovered = sum(
        not row["mixedClockReplay"]["eligible"]
        and row["singleCompactClockReplay"]["eligible"]
        for row in rows
    )
    decode_classes = counter([row["selectedDecode"]["classification"] for row in rows])
    current_reasons = counter([
        reason
        for row in rows if not row["mixedClockReplay"]["eligible"]
        for reason in row["mixedClockReplay"]["reasons"]
    ])
    coherent_reasons = counter([
        reason
        for row in rows if not row["singleCompactClockReplay"]["eligible"]
        for reason in row["singleCompactClockReplay"]["reasons"]
    ])
    active_hours = interval_active_hours(start_ms, snapshot_ms, intervals)
    coherent_rate = coherent_eligible / active_hours if active_hours else 0.0
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": now_utc(),
        "snapshotAtUtc": status["atUtc"],
        "cohortStartUtc": contract["cohortStartUtc"],
        "activeCollectionHours": active_hours,
        "canonicalIndependentEntryEpisodes": len(episodes),
        "funnel": counter(funnel_reasons),
        "routes": dict(sorted(route_counts.items())),
        "acquisitionClasses": dict(sorted(acquisition_counts.items())),
        "auditedDecisionN": len(rows),
        "mixedClockEligibleN": originally_eligible,
        "singleCompactClockEligibleN": coherent_eligible,
        "recoveredBySingleClockN": clock_recovered,
        "selectedDecodeClassifications": decode_classes,
        "mixedClockFailureReasons": current_reasons,
        "singleCompactClockFailureReasons": coherent_reasons,
        "singleCompactClockEligiblePerActiveHour": coherent_rate,
        "estimatedActiveHoursFromCurrentNTo350": (
            max(0, 350 - coherent_eligible) / coherent_rate if coherent_rate else None
        ),
        "evidenceScanned": dict(sorted(evidence_totals.items())),
        "rows": rows,
        "integrity": {
            "validationRowsRead": 0,
            "outcomesRead": 0,
            "submissions": 0,
            "cohortModified": False,
        },
        "runtimeSeconds": time.monotonic() - started,
    }
    materializer.assert_outcome_blind(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    materializer.atomic_json(args.output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--state", type=pathlib.Path, required=True)
    parser.add_argument("--status", type=pathlib.Path, required=True)
    parser.add_argument("--cohort", type=pathlib.Path, required=True)
    parser.add_argument("--candidates", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    report = run(args)
    compact = {key: value for key, value in report.items() if key != "rows"}
    print(json.dumps(compact, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
