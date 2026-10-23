#!/usr/bin/env python3
"""Correlate a frozen canonical Pump census with AllenHark proxy instrumentation."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import re
import struct
from typing import Any

from twoCqAllenharkParityV1 import atomic_json, iso, now, sha256
from twoCqPostFixCanonicalCoverageV1 import signature_digest


SCHEMA = "ALLENHARK_RECONSTRUCTION_INSTRUMENTATION_V1"
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
}

METRIC_RE = re.compile(r"([a-z_]+)=(\d+)")


def read_event_summary(
    path: pathlib.Path,
    start_slot: int,
    end_slot: int,
    target_digests: set[str],
    target_slots: set[int],
) -> tuple[collections.Counter[str], dict[str, list[dict[str, Any]]]]:
    counts: collections.Counter[str] = collections.Counter()
    relevant: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
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
            if slot and not start_slot <= slot <= end_slot:
                continue
            name = KINDS.get(kind, f"UNKNOWN_{kind}")
            counts[name] += 1
            row = {
                "kind": KINDS.get(kind, f"UNKNOWN_{kind}"),
                "atUnixNs": at_ns,
                "slot": slot,
                "a": a,
                "b": b,
                "c": c,
                "digest": digest.hex(),
            }
            if (name == "DISPATCH_TRANSACTION" and row["digest"] in target_digests) or (
                name != "DISPATCH_TRANSACTION" and slot in target_slots
            ):
                relevant[name].append(row)
    return counts, relevant


def read_metrics(path: pathlib.Path) -> dict[str, int]:
    sums: collections.Counter[str] = collections.Counter()
    high_water = 0
    current = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "ALLENHARK_INSTRUMENTATION " not in line:
            continue
        values = {key: int(value) for key, value in METRIC_RE.findall(line)}
        for key, value in values.items():
            if key not in {"queue_high_water", "queue_current"}:
                sums[key] += value
        high_water = max(high_water, values.get("queue_high_water", 0))
        current = values.get("queue_current", current)
    result = dict(sums)
    result["queue_high_water"] = high_water
    result["queue_current_last"] = current
    return result


def load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def classify_miss(
    row: dict[str, Any],
    dispatch_digests: set[str],
    queue_slots: set[int],
    terminal_slots: set[int],
    recovery_error_slots: set[int],
    grpc_slots: set[int],
) -> str:
    digest = signature_digest(row["signature"]).hex()
    slot = int(row["slot"])
    if digest in dispatch_digests:
        return "PROXY_RECONSTRUCTED_OBSERVER_JOURNAL_MISS"
    if slot in queue_slots:
        return "RECONSTRUCTION_QUEUE_FULL_ALIGNED"
    if slot in terminal_slots:
        return "TERMINAL_UNRECOVERED_FEC_ALIGNED"
    if slot in recovery_error_slots:
        return "RECONSTRUCTION_ERROR_SLOT_ALIGNED"
    if slot in grpc_slots:
        return "UPSTREAM_OR_RECONSTRUCTION_UNLOCALIZED"
    return "UPSTREAM_OR_RECONSTRUCTION_UNLOCALIZED"


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    coverage = json.loads(args.coverage_report.read_text(encoding="utf-8"))
    canonical_rows = load_jsonl(args.canonical_rows)
    start_slot = int(coverage["slotRange"]["start"])
    end_slot = int(coverage["slotRange"]["end"])
    slot_count = end_slot - start_slot + 1
    if slot_count < 512:
        raise RuntimeError("FROZEN_WINDOW_BELOW_512_SLOTS")
    if len(canonical_rows) < 10_000:
        raise RuntimeError("FROZEN_WINDOW_BELOW_10000_CANONICAL_PUMP")

    misses = [row for row in canonical_rows if not row.get("allenharkArrival")]
    target_digests = {signature_digest(row["signature"]).hex() for row in misses}
    target_slots = {int(row["slot"]) for row in misses}
    event_counts, by_kind = read_event_summary(
        args.instrumentation, start_slot, end_slot, target_digests, target_slots
    )
    metrics = read_metrics(args.metrics_log)
    dispatch_digests = {row["digest"] for row in by_kind["DISPATCH_TRANSACTION"]}
    queue_slots = {int(row["slot"]) for row in by_kind["QUEUE_FULL"]}
    terminal_slots = {int(row["slot"]) for row in by_kind["FEC_TERMINAL"]}
    recovery_error_slots = {int(row["slot"]) for row in by_kind["FEC_RECOVERY_ERROR"]}
    grpc_slots = {int(row["slot"]) for row in by_kind["GRPC_DISPATCH"]}

    mapped = []
    causes: collections.Counter[str] = collections.Counter()
    for row in misses:
        cause = classify_miss(
            row,
            dispatch_digests,
            queue_slots,
            terminal_slots,
            recovery_error_slots,
            grpc_slots,
        )
        causes[cause] += 1
        slot = int(row["slot"])
        mapped.append({
            "signature": row["signature"],
            "slot": slot,
            "canonicalTransactionIndex": row["transactionIndex"],
            "cause": cause,
            "queueFullFecCoordinates": [
                {"fec": event["a"], "lastShredIndex": event["b"], "batchPackets": event["c"]}
                for event in by_kind["QUEUE_FULL"] if int(event["slot"]) == slot
            ],
            "terminalFecCoordinates": [
                {"fec": event["a"], "expectedDataShreds": event["b"], "receivedShreds": event["c"]}
                for event in by_kind["FEC_TERMINAL"] if int(event["slot"]) == slot
            ],
            "recoveryErrors": sum(int(event["slot"]) == slot for event in by_kind["FEC_RECOVERY_ERROR"]),
            "dispatchDigestPresent": signature_digest(row["signature"]).hex() in dispatch_digests,
            "grpcDispatchPresentForSlot": slot in grpc_slots,
        })
    with (args.output / "canonical-miss-instrumentation-map.jsonl").open("w", encoding="utf-8") as stream:
        for row in mapped:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    queue_aligned = causes["RECONSTRUCTION_QUEUE_FULL_ALIGNED"]
    fec_confirmed = causes["TERMINAL_UNRECOVERED_FEC_ALIGNED"]
    observer_misses = causes["PROXY_RECONSTRUCTED_OBSERVER_JOURNAL_MISS"]
    unlocalized = (
        causes["RECONSTRUCTION_ERROR_SLOT_ALIGNED"]
        + causes["UPSTREAM_OR_RECONSTRUCTION_UNLOCALIZED"]
    )
    if misses and queue_aligned:
        verdict = "ALLENHARK_RECONSTRUCTION_QUEUE_LOSS_CONFIRMED"
        next_action = "Replace reconstruction try_send with a lossless bounded handoff or explicit backpressure policy in a separately authorized repair."
    elif misses and fec_confirmed:
        verdict = "ALLENHARK_FEC_RECONSTRUCTION_LOSS_CONFIRMED"
        next_action = "Repair the specific terminal FEC reconstruction failure mode shown by the correlated slot/FEC records."
    elif not misses and not any((metrics.get("queue_full", 0), metrics.get("queue_disconnected", 0), metrics.get("broadcast_failures", 0), metrics.get("receiver_lagged", 0), metrics.get("instrumentation_dropped", 0))):
        verdict = "ALLENHARK_INSTRUMENTED_GATE_CLEAN"
        next_action = "No repair is justified; continue fail-closed prospective collection and repeat only at a later predeclared gate."
    else:
        verdict = "ALLENHARK_INTERNAL_LOSS_STILL_UNRESOLVED"
        next_action = "Add the narrowest evidence counter at the first still-unobserved boundary identified in the miss map; do not alter transport behavior yet."

    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": iso(now()),
        "coverageReportSha256": sha256(args.coverage_report),
        "instrumentationSha256": sha256(args.instrumentation),
        "window": {"startSlot": start_slot, "endSlot": end_slot, "finalizedSlots": slot_count},
        "canonicalSuccessfulPump": len(canonical_rows),
        "allenharkCaptured": len(canonical_rows) - len(misses),
        "missing": len(misses),
        "coveragePct": 100.0 * (len(canonical_rows) - len(misses)) / len(canonical_rows),
        "udpDrops": {"before": args.drops_start, "after": args.drops_end, "delta": max(0, args.drops_end - args.drops_start)},
        "trySendSuccess": metrics.get("queue_success", 0),
        "trySendFull": metrics.get("queue_full", 0),
        "trySendDisconnected": metrics.get("queue_disconnected", 0),
        "queueHighWater": metrics.get("queue_high_water", 0),
        "queueCurrentAtLastReport": metrics.get("queue_current_last", 0),
        "fecTerminalFailures": event_counts["FEC_TERMINAL"],
        "fecRecoveryErrors": event_counts["FEC_RECOVERY_ERROR"],
        "broadcastSendFailures": metrics.get("broadcast_failures", 0),
        "receiverLagged": metrics.get("receiver_lagged", 0),
        "lossLocalization": {
            "udp": 0,
            "reconstructionConfirmed": fec_confirmed,
            "queue": queue_aligned,
            "grpcDispatchConfirmed": 0,
            "observerOrJournal": observer_misses,
            "unlocalized": unlocalized,
        },
        "grpcDispatchGaps": 0,
        "instrumentationDropped": metrics.get("instrumentation_dropped", 0),
        "missesByCause": dict(sorted(causes.items())),
        "exactLedgerReconciliationCoveragePct": coverage["exactLedger"]["coverageAfterReconciliationPct"],
        "verdict": verdict,
        "singleNextAction": next_action,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(args.output / "report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--coverage-report", type=pathlib.Path, required=True)
    parser.add_argument("--canonical-rows", type=pathlib.Path, required=True)
    parser.add_argument("--instrumentation", type=pathlib.Path, required=True)
    parser.add_argument("--metrics-log", type=pathlib.Path, required=True)
    parser.add_argument("--drops-start", type=int, required=True)
    parser.add_argument("--drops-end", type=int, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
