#!/usr/bin/env python3
"""Capture-first AllenHark parity against finalized successful Pump evidence."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import os
import pathlib
import struct
from typing import Any, Iterator

from exactCurveEconomicsV1 import base58_encode
from reconcilePumpExactEvidenceLedgerV1 import iter_frames
from twoCqAllenharkParityV1 import atomic_json, iso, now, partition_paths, sha256


SCHEMA = "2CQ_ALLENHARK_CAPTURE_FIRST_FINALIZED_PARITY_V1"
MAGIC = b"AHATX01\n"
RECORD = struct.Struct("<64sqQQIIQQQQ")
BUY_KINDS = {"BUY", "BUY_EXACT_SOL_IN", "BUY_V2", "BUY_EXACT_QUOTE_IN_V2"}
SELL_KINDS = {"SELL", "SELL_V2"}
CREATE_KINDS = {"CREATE", "CREATE_V2"}


def quantile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def binary_paths(root: pathlib.Path, start: dt.datetime, end: dt.datetime) -> list[pathlib.Path]:
    cursor = start.replace(minute=0, second=0, microsecond=0)
    paths: list[pathlib.Path] = []
    while cursor <= end:
        path = root / cursor.strftime("%Y/%m/%d/%H") / "arrivals.bin.partial"
        if path.exists():
            paths.append(path)
        cursor += dt.timedelta(hours=1)
    return paths


def binary_arrivals(paths: list[pathlib.Path], start_ns: int, end_ns: int) -> tuple[dict[str, dict[str, Any]], int]:
    arrivals: dict[str, dict[str, Any]] = {}
    malformed = 0
    for path in paths:
        with path.open("rb") as stream:
            if stream.read(len(MAGIC)) != MAGIC:
                raise RuntimeError(f"ALLENHARK_BINARY_MAGIC_MISMATCH:{path}")
            while True:
                raw = stream.read(RECORD.size)
                if not raw:
                    break
                if len(raw) != RECORD.size:
                    malformed += 1
                    break
                signature_raw, receipt_ns, slot, sequence, entry_index, tx_index, ordinal, epoch, process_hash, connected = RECORD.unpack(raw)
                if not (start_ns <= receipt_ns <= end_ns):
                    continue
                signature = base58_encode(signature_raw)
                row = {
                    "signature": signature, "receivedAtUnixNs": receipt_ns, "slot": slot,
                    "streamSequence": sequence, "entryIndex": entry_index,
                    "transactionIndexWithinEntry": tx_index,
                    "observedSlotTransactionOrdinal": ordinal,
                    "connectionEpoch": epoch, "processInstanceHash": process_hash,
                    "connectedAtCapture": connected == 1,
                }
                current = arrivals.get(signature)
                if current is None or receipt_ns < current["receivedAtUnixNs"]:
                    arrivals[signature] = row
    return arrivals, malformed


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    contract_path = args.output / "contract.json"
    if contract_path.exists():
        raise RuntimeError("PARITY_CONTRACT_ALREADY_EXISTS")
    health = json.loads(args.allenhark_health.read_text(encoding="utf-8"))
    start = now()
    end = start + dt.timedelta(seconds=args.duration_seconds)
    contract = {
        "schema": SCHEMA + "_CONTRACT", "createdAtUtc": iso(start),
        "startUtc": iso(start), "startUnixNs": int(start.timestamp() * 1_000_000_000),
        "endUtc": iso(end), "endUnixNs": int(end.timestamp() * 1_000_000_000),
        "durationSeconds": args.duration_seconds,
        "primaryDenominator": "finalized successful canonically decoded Pump transactions whose Rabbit raw receipt is inside the frozen window",
        "allenharkProcessInstance": health.get("processInstance"),
        "allenharkReconnectsAtStart": int(health.get("reconnects") or 0),
        "allenharkDecodeFailuresAtStart": int(health.get("decodeFailures") or 0),
        "passCriteriaFrozenBeforeData": {
            "finalizedSuccessfulPumpMin": 500,
            "allenharkArrivalCoveragePctMin": 99.0,
            "unexplainedAllenharkMissPctMax": 1.0,
            "disconnectsMax": 0,
            "decodeFailuresMax": 0,
            "requiredKinds": ["buy", "sell", "create"],
            "latencyWinnerRequired": False,
        },
        "outcomesRead": 0, "submissions": 0,
    }
    atomic_json(contract_path, contract)
    atomic_json(args.output / "freeze.json", {
        "schema": SCHEMA + "_FREEZE", "createdAtUtc": contract["createdAtUtc"],
        "contractSha256": sha256(contract_path),
    })
    return contract


def scan_exact(
    paths: list[pathlib.Path], start_ns: int, end_ns: int
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], int]:
    rabbit: dict[str, dict[str, Any]] = {}
    finalized: dict[str, dict[str, Any]] = {}
    malformed = 0
    for path in paths:
        for metadata, _ in iter_frames(path, path.suffix == ".partial"):
            signature = str(metadata.get("signature") or "")
            if not signature:
                continue
            if metadata.get("kind") == "transaction":
                receipt_ns = int(metadata.get("receivedAtUnixNs") or 0)
                kinds = list(metadata.get("pumpInstructionKinds") or [])
                if kinds and start_ns <= receipt_ns <= end_ns:
                    current = rabbit.get(signature)
                    if current is None or receipt_ns < int(current.get("receivedAtUnixNs") or 0):
                        rabbit[signature] = metadata
            elif metadata.get("kind") == "rpc_finalized_transaction":
                if metadata.get("finalized") is True:
                    finalized[signature] = metadata
    return rabbit, finalized, malformed


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    contract_path = args.output / "contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    frozen = json.loads((args.output / "freeze.json").read_text(encoding="utf-8"))
    if sha256(contract_path) != frozen["contractSha256"]:
        raise RuntimeError("PARITY_CONTRACT_CHANGED_FAIL_CLOSED")
    start_ns, end_ns = int(contract["startUnixNs"]), int(contract["endUnixNs"])
    if int(now().timestamp() * 1_000_000_000) < end_ns + args.finality_grace_seconds * 1_000_000_000:
        raise RuntimeError("FINALITY_GRACE_NOT_COMPLETE")
    start = dt.datetime.fromtimestamp(start_ns / 1_000_000_000, dt.timezone.utc)
    end = dt.datetime.fromtimestamp(end_ns / 1_000_000_000, dt.timezone.utc)
    # Finality records may be appended after the receipt window but stay in the same UTC partition.
    exact_paths = partition_paths(args.exact_root, start - dt.timedelta(hours=1), now())
    rabbit, finalized, exact_malformed = scan_exact(exact_paths, start_ns, end_ns)
    allen_paths = binary_paths(args.all_transaction_root, start, end)
    allen, binary_malformed = binary_arrivals(allen_paths, start_ns, end_ns)

    denominator: dict[str, dict[str, Any]] = {}
    pending_finality = 0
    finalized_failed = 0
    for signature, raw in rabbit.items():
        final = finalized.get(signature)
        if final is None:
            pending_finality += 1
            continue
        if final.get("successful") is not True:
            finalized_failed += 1
            continue
        kinds = list(final.get("pumpInstructionKinds") or raw.get("pumpInstructionKinds") or [])
        if not kinds:
            continue
        denominator[signature] = {"raw": raw, "final": final, "kinds": kinds}

    matched = sorted(set(denominator) & set(allen))
    misses = sorted(set(denominator) - set(allen))
    deltas = [
        (int(allen[s]["receivedAtUnixNs"]) - int(denominator[s]["raw"]["receivedAtUnixNs"])) / 1_000_000
        for s in matched
    ]
    health = json.loads(args.allenhark_health.read_text(encoding="utf-8"))
    reconnect_delta = int(health.get("reconnects") or 0) - int(contract["allenharkReconnectsAtStart"])
    decode_delta = int(health.get("decodeFailures") or 0) - int(contract["allenharkDecodeFailuresAtStart"])
    process_changed = health.get("processInstance") != contract.get("allenharkProcessInstance")

    allen_slots = {int(row["slot"]) for row in allen.values()}
    miss_reasons: collections.Counter[str] = collections.Counter()
    miss_rows: list[dict[str, Any]] = []
    for signature in misses:
        item = denominator[signature]
        slot = int(item["final"].get("slot") or item["raw"].get("slot") or 0)
        if decode_delta > 0:
            reason = "DECODE_ISSUE_POSSIBLE"
        elif reconnect_delta > 0 or process_changed:
            reason = "OBSERVER_CONTINUITY_GAP_POSSIBLE"
        elif slot not in allen_slots:
            reason = "ACTUAL_ALLENHARK_SLOT_DELIVERY_GAP"
        else:
            reason = "ACTUAL_ALLENHARK_TRANSACTION_DELIVERY_GAP"
        miss_reasons[reason] += 1
        miss_rows.append({"signature": signature, "slot": slot, "kinds": item["kinds"], "reason": reason})

    represented = collections.Counter[str]()
    matched_represented = collections.Counter[str]()
    for item in denominator.values():
        represented.update(item["kinds"])
    for signature in matched:
        matched_represented.update(denominator[signature]["kinds"])
    kind_coverage = {}
    for label, family in (("buy", BUY_KINDS), ("sell", SELL_KINDS), ("create", CREATE_KINDS)):
        total = sum(represented[kind] for kind in family)
        covered = sum(matched_represented[kind] for kind in family)
        kind_coverage[label] = {"N": total, "matchedN": covered, "coveragePct": 100.0 * covered / total if total else None}

    comparable = disagreements = 0
    ordered = sorted(matched, key=lambda s: (allen[s]["receivedAtUnixNs"], s))
    for left, right in zip(ordered, ordered[1:]):
        ar_left, ar_right = allen[left]["receivedAtUnixNs"], allen[right]["receivedAtUnixNs"]
        rb_left = int(denominator[left]["raw"]["receivedAtUnixNs"])
        rb_right = int(denominator[right]["raw"]["receivedAtUnixNs"])
        if ar_left == ar_right or rb_left == rb_right:
            continue
        comparable += 1
        if (ar_left < ar_right) != (rb_left < rb_right):
            disagreements += 1

    total = len(denominator)
    coverage = 100.0 * len(matched) / total if total else 0.0
    miss_pct = 100.0 * len(misses) / total if total else 100.0
    criteria = contract["passCriteriaFrozenBeforeData"]
    all_kinds_present = all(kind_coverage[name]["N"] > 0 for name in criteria["requiredKinds"])
    passed = (
        total >= criteria["finalizedSuccessfulPumpMin"]
        and coverage >= criteria["allenharkArrivalCoveragePctMin"]
        and miss_pct <= criteria["unexplainedAllenharkMissPctMax"]
        and reconnect_delta <= criteria["disconnectsMax"]
        and decode_delta <= criteria["decodeFailuresMax"]
        and not process_changed and all_kinds_present
        and pending_finality == 0
        and binary_malformed == 0 and exact_malformed == 0
    )
    with (args.output / "finalized-pump-row-audit.jsonl").open("w", encoding="utf-8") as stream:
        for signature, item in sorted(denominator.items()):
            a = allen.get(signature)
            stream.write(json.dumps({
                "signature": signature, "slot": item["final"].get("slot"), "kinds": item["kinds"],
                "rabbitArrivalUnixNs": int(item["raw"]["receivedAtUnixNs"]),
                "allenharkArrivalUnixNs": a["receivedAtUnixNs"] if a else None,
                "matched": a is not None,
            }, sort_keys=True, separators=(",", ":")) + "\n")
    with (args.output / "allenhark-misses.jsonl").open("w", encoding="utf-8") as stream:
        for row in miss_rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    report = {
        "schema": SCHEMA + "_REPORT", "generatedAtUtc": iso(now()),
        "contractSha256": frozen["contractSha256"],
        "window": {"startUtc": contract["startUtc"], "endUtc": contract["endUtc"], "seconds": contract["durationSeconds"]},
        "finalizedSuccessfulPumpN": total, "matchedN": len(matched),
        "allenharkArrivalCoveragePct": coverage, "rabbitArrivalCoveragePct": 100.0 if total else 0.0,
        "pendingFinalityN": pending_finality, "finalizedFailedExcludedN": finalized_failed,
        "actualUnexplainedAllenharkMisses": len(misses), "missesByReason": dict(sorted(miss_reasons.items())),
        "kindCoverage": kind_coverage,
        "arrivalDeltaAllenMinusRabbitMs": {
            "p10": quantile(deltas, .10), "p50": quantile(deltas, .50), "p75": quantile(deltas, .75),
            "p90": quantile(deltas, .90), "p99": quantile(deltas, .99),
            "min": min(deltas) if deltas else None, "max": max(deltas) if deltas else None,
        },
        "orderingAgreement": {
            "comparableAdjacentPairs": comparable, "disagreements": disagreements,
            "agreementPct": 100.0 * (comparable - disagreements) / comparable if comparable else None,
        },
        "rawSourcePopulation": {
            "rabbitPumpReceiptsInWindow": len(rabbit), "allenharkAllTransactionsInWindow": len(allen),
            "allenharkObservedSlots": len(allen_slots),
            "note": "Raw universes are reported separately; only finalized successful Pump transactions form the primary denominator.",
        },
        "observer": {"processChanged": process_changed, "reconnectDelta": reconnect_delta, "decodeFailureDelta": decode_delta, "binaryMalformedTailN": binary_malformed},
        "parityPassed": passed, "outcomesRead": 0, "submissions": 0,
    }
    atomic_json(args.output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--all-transaction-root", type=pathlib.Path, required=True)
    parser.add_argument("--allenhark-health", type=pathlib.Path, required=True)
    parser.add_argument("--duration-seconds", type=int, default=120)
    parser.add_argument("--finality-grace-seconds", type=int, default=45)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    result = freeze(args) if args.freeze else finalize(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
