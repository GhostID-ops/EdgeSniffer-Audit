#!/usr/bin/env python3
"""Correlate the final AllenHark repair census without reading outcomes."""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import struct
from typing import Any

from twoCqAllenharkParityV1 import atomic_json, iso, now, sha256
from twoCqPostFixCanonicalCoverageV1 import signature_digest


SCHEMA = "ALLENHARK_REALTIME_COVERAGE_FINAL_REPAIR_V1"
MAGIC = b"AHIRV1\n\0"
RECORD = struct.Struct("<B7xQQQQQ32s")
KINDS = {
    1: "QUEUE_FULL",
    2: "QUEUE_DISCONNECTED",
    3: "FEC_RECOVERY_ATTEMPT",
    4: "FEC_RECOVERY_SUCCESS",
    5: "FEC_TERMINAL",
    6: "FEC_RECOVERY_ERROR",
    7: "DESHRED_SUCCESS",
    8: "DISPATCH_TRANSACTION",
    9: "BROADCAST_SEND_FAILURE",
    10: "RECEIVER_LAGGED",
    11: "GRPC_DISPATCH",
    12: "FEC_FINAL_RETRY_ATTEMPT",
    13: "FEC_FINAL_RETRY_RECOVERED",
    14: "FEC_FINAL_RETRY_FAILED",
}
METRIC_RE = re.compile(r"([a-z_]+)=(\d+)")


def load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def read_events(
    path: pathlib.Path, start_slot: int, end_slot: int
) -> tuple[collections.Counter[str], set[str], dict[int, list[dict[str, int]]]]:
    counts: collections.Counter[str] = collections.Counter()
    dispatch: set[str] = set()
    by_slot: dict[int, list[dict[str, int]]] = collections.defaultdict(list)
    with path.open("rb") as stream:
        if stream.read(len(MAGIC)) != MAGIC:
            raise RuntimeError("ALLENHARK_INSTRUMENTATION_MAGIC_MISMATCH")
        while True:
            raw = stream.read(RECORD.size)
            if not raw:
                break
            if len(raw) != RECORD.size:
                raise RuntimeError("ALLENHARK_INSTRUMENTATION_TRUNCATED_RECORD")
            kind, at_ns, slot, a, b, c, digest = RECORD.unpack(raw)
            if not start_slot <= slot <= end_slot:
                continue
            name = KINDS.get(kind, f"UNKNOWN_{kind}")
            counts[name] += 1
            if name == "DISPATCH_TRANSACTION":
                dispatch.add(digest.hex())
            else:
                by_slot[slot].append({
                    "kind": name,
                    "atUnixNs": at_ns,
                    "a": a,
                    "b": b,
                    "c": c,
                })
    return counts, dispatch, by_slot


def read_metrics(path: pathlib.Path) -> dict[str, int]:
    totals: collections.Counter[str] = collections.Counter()
    high_water = 0
    current = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "ALLENHARK_INSTRUMENTATION " not in line:
            continue
        values = {key: int(value) for key, value in METRIC_RE.findall(line)}
        for key, value in values.items():
            if key not in {"queue_high_water", "queue_current"}:
                totals[key] += value
        high_water = max(high_water, values.get("queue_high_water", 0))
        current = values.get("queue_current", current)
    totals["queue_high_water"] = high_water
    totals["queue_current_last"] = current
    return dict(totals)


def events_of(events: list[dict[str, int]], kind: str) -> list[dict[str, int]]:
    return [event for event in events if event["kind"] == kind]


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    coverage = json.loads(args.coverage_report.read_text(encoding="utf-8"))
    canonical = load_jsonl(args.canonical_rows)
    start_slot = int(coverage["slotRange"]["start"])
    end_slot = int(coverage["slotRange"]["end"])
    slots = end_slot - start_slot + 1
    if slots < 3072:
        raise RuntimeError("FINAL_WINDOW_BELOW_3072_SLOTS")
    if len(canonical) < 30_000:
        raise RuntimeError("FINAL_WINDOW_BELOW_30000_CANONICAL_PUMP")

    event_counts, dispatch_digests, by_slot = read_events(
        args.instrumentation, start_slot, end_slot
    )
    retry_failure_reasons: collections.Counter[str] = collections.Counter()
    retry_reason_names = {
        1: "REED_SOLOMON_RECOVERY_FAILED",
        2: "COMPLETE_DESHRED_RANGE_UNAVAILABLE",
        3: "DESHRED_FAILED",
        4: "ENTRY_DECODE_FAILED",
    }
    for slot_events in by_slot.values():
        for event in events_of(slot_events, "FEC_FINAL_RETRY_FAILED"):
            retry_failure_reasons[retry_reason_names.get(event["b"], f"UNKNOWN_{event['b']}")] += 1
    metrics = read_metrics(args.metrics_log)
    mapped = []
    localization: collections.Counter[str] = collections.Counter()
    proxy_missing = 0
    observer_missing = 0
    downstream_observer_missing = 0

    for row in canonical:
        digest = signature_digest(row["signature"]).hex()
        proxy_present = digest in dispatch_digests
        observer_present = bool(row.get("allenharkArrival"))
        if proxy_present and observer_present:
            continue
        if not proxy_present:
            proxy_missing += 1
        if not observer_present:
            observer_missing += 1
        slot = int(row["slot"])
        slot_events = by_slot.get(slot, [])
        terminal = events_of(slot_events, "FEC_TERMINAL")
        retry_attempt = events_of(slot_events, "FEC_FINAL_RETRY_ATTEMPT")
        retry_recovered = events_of(slot_events, "FEC_FINAL_RETRY_RECOVERED")
        retry_failed = events_of(slot_events, "FEC_FINAL_RETRY_FAILED")
        recovery_errors = events_of(slot_events, "FEC_RECOVERY_ERROR")
        queue_full = events_of(slot_events, "QUEUE_FULL")
        broadcast_failure = events_of(slot_events, "BROADCAST_SEND_FAILURE")
        receiver_lagged = events_of(slot_events, "RECEIVER_LAGGED")

        if proxy_present and not observer_present:
            cause = "POST_PROXY_OBSERVER_PATH_MISS"
            proven_boundary = "BETWEEN_PROXY_DISPATCH_AND_OBSERVER_JOURNAL"
            downstream_observer_missing += 1
        elif queue_full:
            cause = "RECONSTRUCTION_QUEUE_FULL"
            proven_boundary = "AT_OR_BEFORE_PROXY_DISPATCH"
        elif terminal and retry_failed:
            cause = "TERMINAL_FEC_FINAL_RETRY_FAILED_SLOT_ALIGNED"
            proven_boundary = "AT_OR_BEFORE_PROXY_DISPATCH"
        elif terminal:
            cause = "TERMINAL_FEC_LOSS_SLOT_ALIGNED"
            proven_boundary = "AT_OR_BEFORE_PROXY_DISPATCH"
        elif recovery_errors:
            cause = "RECONSTRUCTION_ERROR_SLOT_ALIGNED"
            proven_boundary = "AT_OR_BEFORE_PROXY_DISPATCH"
        elif broadcast_failure or receiver_lagged:
            cause = "GRPC_DISPATCH_PATH_ALIGNED"
            proven_boundary = "AT_OR_BEFORE_PROXY_DISPATCH"
        else:
            cause = "PRE_DISPATCH_UNLOCALIZED"
            proven_boundary = "AT_OR_BEFORE_PROXY_DISPATCH"
        localization[cause] += 1
        mapped.append({
            "signature": row["signature"],
            "slot": slot,
            "canonicalTransactionIndex": row["transactionIndex"],
            "proxyReconstructed": proxy_present,
            "compactObserverCaptured": observer_present,
            "provenLossBoundary": proven_boundary,
            "correlatedStageEvidence": cause,
            "terminalFec": terminal,
            "finalRetryAttempt": retry_attempt,
            "finalRetryRecovered": retry_recovered,
            "finalRetryFailed": retry_failed,
            "recoveryErrorsInSlot": len(recovery_errors),
            "queueFullInSlot": len(queue_full),
            "broadcastFailuresInSlot": len(broadcast_failure),
            "receiverLaggedInSlot": len(receiver_lagged),
        })

    miss_path = args.output / "canonical-miss-stage-map.jsonl"
    with miss_path.open("w", encoding="utf-8") as stream:
        for row in mapped:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    n = len(canonical)
    proxy_n = n - proxy_missing
    observer_n = n - observer_missing
    exact_pct = float(coverage["exactLedger"]["coverageAfterReconciliationPct"])
    critical_transport_errors = sum((
        metrics.get("queue_full", 0),
        metrics.get("queue_disconnected", 0),
        metrics.get("broadcast_failures", 0),
        metrics.get("receiver_lagged", 0),
        metrics.get("instrumentation_dropped", 0),
    ))
    pre_fix_proxy_miss_rate = args.pre_fix_proxy_missing / args.pre_fix_canonical
    post_fix_proxy_miss_rate = proxy_missing / n
    relative_proxy_miss_reduction = (
        1.0 - post_fix_proxy_miss_rate / pre_fix_proxy_miss_rate
        if pre_fix_proxy_miss_rate
        else 0.0
    )
    if proxy_missing == 0 and observer_missing == 0 and critical_transport_errors == 0:
        verdict = "ALLENHARK_REALTIME_COVERAGE_REPAIRED"
    elif (
        downstream_observer_missing == 0
        and event_counts["FEC_FINAL_RETRY_RECOVERED"] > 0
        and relative_proxy_miss_reduction >= 0.20
    ):
        verdict = "ALLENHARK_FEC_LOSS_REDUCED_BUT_REMAINS"
    else:
        verdict = "ALLENHARK_REALTIME_COVERAGE_BLOCKER_REMAINS"

    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": iso(now()),
        "predeclaredWindowSha256": sha256(args.predeclared_window),
        "coverageReportSha256": sha256(args.coverage_report),
        "instrumentationSha256": sha256(args.instrumentation),
        "window": {"startSlot": start_slot, "endSlot": end_slot, "finalizedSlots": slots},
        "canonical": n,
        "proxyReconstructed": proxy_n,
        "compactObserverCaptured": observer_n,
        "proxyMissing": proxy_missing,
        "observerMissing": observer_missing,
        "postProxyObserverMissing": downstream_observer_missing,
        "coveragePct": 100.0 * observer_n / n,
        "proxyCoveragePct": 100.0 * proxy_n / n,
        "preFixProxyMissRatePct": 100.0 * pre_fix_proxy_miss_rate,
        "postFixProxyMissRatePct": 100.0 * post_fix_proxy_miss_rate,
        "relativeProxyMissReductionPct": 100.0 * relative_proxy_miss_reduction,
        "exactLedgerReconciliationCoveragePct": exact_pct,
        "udpDrops": {
            "before": args.drops_start,
            "after": args.drops_end,
            "delta": max(0, args.drops_end - args.drops_start),
        },
        "trySendSuccess": metrics.get("queue_success", 0),
        "trySendFull": metrics.get("queue_full", 0),
        "trySendDisconnected": metrics.get("queue_disconnected", 0),
        "queueHighWater": metrics.get("queue_high_water", 0),
        "fecFinalRetryAttempts": event_counts["FEC_FINAL_RETRY_ATTEMPT"],
        "fecFinalRetryRecovered": event_counts["FEC_FINAL_RETRY_RECOVERED"],
        "fecFinalRetryFailed": event_counts["FEC_FINAL_RETRY_FAILED"],
        "fecFinalRetryFailureReasons": dict(sorted(retry_failure_reasons.items())),
        "fecTerminalFailures": event_counts["FEC_TERMINAL"],
        "fecRecoveryErrors": event_counts["FEC_RECOVERY_ERROR"],
        "broadcastSendFailures": metrics.get("broadcast_failures", 0),
        "receiverLagged": metrics.get("receiver_lagged", 0),
        "grpcGaps": metrics.get("broadcast_failures", 0) + metrics.get("receiver_lagged", 0),
        "instrumentationDropped": metrics.get("instrumentation_dropped", 0),
        "remainingMissesByStage": dict(sorted(localization.items())),
        "provenMissBoundary": {
            "atOrBeforeProxyDispatch": proxy_missing,
            "betweenProxyDispatchAndObserverJournal": downstream_observer_missing,
        },
        "missMap": str(miss_path),
        "observerRuntimeMaxSec": "infinity",
        "verdict": verdict,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(args.output / "report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--predeclared-window", type=pathlib.Path, required=True)
    parser.add_argument("--coverage-report", type=pathlib.Path, required=True)
    parser.add_argument("--canonical-rows", type=pathlib.Path, required=True)
    parser.add_argument("--instrumentation", type=pathlib.Path, required=True)
    parser.add_argument("--metrics-log", type=pathlib.Path, required=True)
    parser.add_argument("--drops-start", type=int, required=True)
    parser.add_argument("--drops-end", type=int, required=True)
    parser.add_argument("--pre-fix-proxy-missing", type=int, default=43)
    parser.add_argument("--pre-fix-canonical", type=int, default=40_597)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
