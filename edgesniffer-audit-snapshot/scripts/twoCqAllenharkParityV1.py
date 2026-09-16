#!/usr/bin/env python3
"""Bounded, outcome-blind AllenHark versus Yellowstone arrival-time parity."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import statistics
from typing import Any

from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "2CQ_ALLENHARK_ARRIVAL_PARITY_V1"
TRADE_KINDS = {"BUY", "BUY_EXACT_SOL_IN", "BUY_V2", "BUY_EXACT_QUOTE_IN_V2", "SELL", "SELL_V2"}
CREATE_KINDS = {"CREATE", "CREATE_V2"}


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quantile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("rb") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def partition_paths(root: pathlib.Path, start: dt.datetime, end: dt.datetime) -> list[pathlib.Path]:
    cursor = start.replace(minute=0, second=0, microsecond=0)
    paths: list[pathlib.Path] = []
    while cursor <= end:
        directory = root / "partitions" / cursor.strftime("%Y/%m/%d/%H")
        for name in ("evidence.frames.partial", "evidence.frames.zst"):
            path = directory / name
            if path.exists():
                paths.append(path)
                break
        cursor += dt.timedelta(hours=1)
    return paths


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    contract_path = args.output / "contract.json"
    if contract_path.exists():
        raise RuntimeError("PARITY_CONTRACT_ALREADY_EXISTS")
    start = now()
    contract = {
        "schema": SCHEMA + "_CONTRACT",
        "createdAtUtc": iso(start),
        "startUtc": iso(start),
        "startUnixNs": int(start.timestamp() * 1_000_000_000),
        "durationSeconds": args.duration_seconds,
        "endUtc": iso(start + dt.timedelta(seconds=args.duration_seconds)),
        "endUnixNs": int((start + dt.timedelta(seconds=args.duration_seconds)).timestamp() * 1_000_000_000),
        "joinKey": "exact transaction signature",
        "population": "Pump-program transactions received by either source inside the frozen wall-clock window",
        "deltaDefinition": "AllenHark receipt minus RabbitStream/Yellowstone receipt in milliseconds; negative means AllenHark first",
        "passCriteriaFrozenBeforeData": {
            "matchedSignaturesMin": 500,
            "allenharkCoverageOfRabbitPctMin": 90.0,
            "rabbitCoverageOfAllenharkPctMin": 90.0,
            "observerReconnectsDuringWindowMax": 0,
            "observerDecodeFailuresDuringWindowMax": 0,
            "requiredKinds": ["Pump buy", "Pump sell", "CREATE"],
            "latencyWinnerRequired": False,
        },
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(contract_path, contract)
    atomic_json(args.output / "freeze.json", {
        "schema": SCHEMA + "_FREEZE",
        "contractSha256": sha256(contract_path),
        "createdAtUtc": contract["createdAtUtc"],
    })
    return contract


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    contract_path = args.output / "contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    frozen = json.loads((args.output / "freeze.json").read_text(encoding="utf-8"))
    if sha256(contract_path) != frozen["contractSha256"]:
        raise RuntimeError("PARITY_CONTRACT_CHANGED_FAIL_CLOSED")
    start_ns = int(contract["startUnixNs"])
    end_ns = int(contract["endUnixNs"])
    if int(now().timestamp() * 1_000_000_000) < end_ns:
        raise RuntimeError("PARITY_WINDOW_NOT_COMPLETE")
    start = dt.datetime.fromtimestamp(start_ns / 1_000_000_000, dt.timezone.utc)
    end = dt.datetime.fromtimestamp(end_ns / 1_000_000_000, dt.timezone.utc)

    allen: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(args.allenhark_arrivals):
        timestamp_ns = int(row.get("receivedAtUnixNs") or 0)
        signature = str(row.get("signature") or "")
        if signature and start_ns <= timestamp_ns <= end_ns:
            allen.setdefault(signature, row)

    rabbit: dict[str, dict[str, Any]] = {}
    malformed = 0
    evidence_paths = partition_paths(args.exact_root, start, end)
    for path in evidence_paths:
        for metadata, _ in iter_frames(path, path.suffix == ".partial"):
            if metadata.get("kind") != "transaction":
                continue
            timestamp_ns = int(metadata.get("receivedAtUnixNs") or 0)
            signature = str(metadata.get("signature") or "")
            kinds = set(metadata.get("pumpInstructionKinds") or [])
            if not signature or not kinds or not (start_ns <= timestamp_ns <= end_ns):
                continue
            current = rabbit.get(signature)
            if current is None or timestamp_ns < int(current.get("receivedAtUnixNs") or 0):
                rabbit[signature] = metadata

    matched = sorted(set(allen) & set(rabbit))
    allen_only_signatures = set(allen) - set(rabbit)
    rabbit_only_signatures = set(rabbit) - set(allen)
    allen_slots = {int(row.get("slot") or 0) for row in allen.values() if int(row.get("slot") or 0) > 0}
    rabbit_slots = {int(row.get("slot") or 0) for row in rabbit.values() if int(row.get("slot") or 0) > 0}
    shared_slots = allen_slots & rabbit_slots
    allen_shared_slot = {signature for signature, row in allen.items() if int(row.get("slot") or 0) in shared_slots}
    rabbit_shared_slot = {signature for signature, row in rabbit.items() if int(row.get("slot") or 0) in shared_slots}
    allen_only_kinds: collections.Counter[str] = collections.Counter()
    rabbit_only_kinds: collections.Counter[str] = collections.Counter()
    for signature in allen_only_signatures:
        allen_only_kinds.update(allen[signature].get("pumpInstructionKinds") or [])
    for signature in rabbit_only_signatures:
        rabbit_only_kinds.update(rabbit[signature].get("pumpInstructionKinds") or [])
    rows: list[dict[str, Any]] = []
    deltas: list[float] = []
    allen_first = rabbit_first = ties = 0
    represented: set[str] = set()
    for signature in matched:
        a = allen[signature]
        r = rabbit[signature]
        delta = (int(a["receivedAtUnixNs"]) - int(r["receivedAtUnixNs"])) / 1_000_000
        deltas.append(delta)
        if delta < 0:
            allen_first += 1
        elif delta > 0:
            rabbit_first += 1
        else:
            ties += 1
        kinds = sorted(set(a.get("pumpInstructionKinds") or []) | set(r.get("pumpInstructionKinds") or []))
        represented.update(kinds)
        rows.append({
            "signature": signature,
            "slot": int(a.get("slot") or r.get("slot") or 0),
            "allenharkReceivedAtUtc": a.get("receivedAtUtc"),
            "allenharkReceivedAtUnixNs": int(a["receivedAtUnixNs"]),
            "rabbitReceivedAtUtc": r.get("receivedAtUtc"),
            "rabbitReceivedAtUnixNs": int(r["receivedAtUnixNs"]),
            "allenMinusRabbitMs": delta,
            "pumpInstructionKinds": kinds,
        })

    # Compare only adjacent transactions whose timestamps are distinct at both sources.
    by_allen = sorted(rows, key=lambda row: (row["allenharkReceivedAtUnixNs"], row["signature"]))
    comparable_pairs = disagreements = 0
    for left, right in zip(by_allen, by_allen[1:]):
        a_order = left["allenharkReceivedAtUnixNs"] < right["allenharkReceivedAtUnixNs"]
        if not a_order or left["rabbitReceivedAtUnixNs"] == right["rabbitReceivedAtUnixNs"]:
            continue
        comparable_pairs += 1
        if left["rabbitReceivedAtUnixNs"] > right["rabbitReceivedAtUnixNs"]:
            disagreements += 1

    continuity = [
        row for row in read_jsonl(args.continuity)
        if start_ns <= int(row.get("timestampUnixNs") or 0) <= end_ns
    ]
    disconnects = [row for row in continuity if str(row.get("event") or "").startswith("DISCONNECTED")]
    health = json.loads(args.allenhark_health.read_text(encoding="utf-8"))
    kinds = {
        "pumpBuysRepresented": bool(represented & TRADE_KINDS & {"BUY", "BUY_EXACT_SOL_IN", "BUY_V2", "BUY_EXACT_QUOTE_IN_V2"}),
        "pumpSellsRepresented": bool(represented & {"SELL", "SELL_V2"}),
        "createTransactionsRepresented": bool(represented & CREATE_KINDS),
    }
    allen_coverage = 100.0 * len(matched) / len(rabbit) if rabbit else 0.0
    rabbit_coverage = 100.0 * len(matched) / len(allen) if allen else 0.0
    criteria = contract["passCriteriaFrozenBeforeData"]
    passed = (
        len(matched) >= criteria["matchedSignaturesMin"]
        and allen_coverage >= criteria["allenharkCoverageOfRabbitPctMin"]
        and rabbit_coverage >= criteria["rabbitCoverageOfAllenharkPctMin"]
        and len(disconnects) <= criteria["observerReconnectsDuringWindowMax"]
        and int(health.get("decodeFailures") or 0) <= criteria["observerDecodeFailuresDuringWindowMax"]
        and all(kinds.values())
        and health.get("connected") is True
        and int(health.get("lastSlot") or 0) > 0
    )
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "matched-signatures.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": iso(now()),
        "contractSha256": frozen["contractSha256"],
        "window": {"startUtc": contract["startUtc"], "endUtc": contract["endUtc"], "seconds": contract["durationSeconds"]},
        "matchedSignatures": len(matched),
        "allenharkOnly": len(allen_only_signatures),
        "rabbitOnly": len(rabbit_only_signatures),
        "unmatchedKinds": {
            "allenharkOnly": dict(sorted(allen_only_kinds.items())),
            "rabbitOnly": dict(sorted(rabbit_only_kinds.items())),
        },
        "slotCoverage": {
            "allenharkSlots": len(allen_slots),
            "rabbitSlots": len(rabbit_slots),
            "sharedSlots": len(shared_slots),
            "allenharkOnlySlots": len(allen_slots - rabbit_slots),
            "rabbitOnlySlots": len(rabbit_slots - allen_slots),
            "allenharkSharedSlotTransactions": len(allen_shared_slot),
            "rabbitSharedSlotTransactions": len(rabbit_shared_slot),
            "matchedWithinAllenharkSharedSlotsPct": 100.0 * len(matched) / len(allen_shared_slot) if allen_shared_slot else 0.0,
            "matchedWithinRabbitSharedSlotsPct": 100.0 * len(matched) / len(rabbit_shared_slot) if rabbit_shared_slot else 0.0,
        },
        "allenharkCoverageOfRabbitPct": allen_coverage,
        "rabbitCoverageOfAllenharkPct": rabbit_coverage,
        "deltaAllenMinusRabbitMs": {
            "p10": quantile(deltas, 0.10), "p50": quantile(deltas, 0.50),
            "p75": quantile(deltas, 0.75), "p90": quantile(deltas, 0.90),
            "p99": quantile(deltas, 0.99), "min": min(deltas) if deltas else None,
            "max": max(deltas) if deltas else None,
        },
        "sourceFirst": {
            "allenharkN": allen_first, "allenharkPct": 100.0 * allen_first / len(matched) if matched else 0.0,
            "rabbitN": rabbit_first, "rabbitPct": 100.0 * rabbit_first / len(matched) if matched else 0.0,
            "tiesN": ties,
        },
        "nearbyOrdering": {
            "comparableAdjacentPairs": comparable_pairs,
            "disagreements": disagreements,
            "disagreementPct": 100.0 * disagreements / comparable_pairs if comparable_pairs else None,
        },
        "representation": kinds,
        "allenharkFeed": {
            "connected": health.get("connected"), "lastSlot": health.get("lastSlot"),
            "lastReceiptAtUtc": health.get("lastReceiptAtUtc"), "reconnectsTotal": health.get("reconnects"),
            "disconnectsDuringWindow": len(disconnects), "decodeFailuresTotal": health.get("decodeFailures"),
            "slotAndObservedOrderingPresent": all(int(row.get("slot") or 0) > 0 for row in allen.values()),
            "localMillisecondReceiptPresent": all(int(row.get("receivedAtUnixNs") or 0) > 0 for row in allen.values()),
            "continuousAdvancing": health.get("connected") is True and len(disconnects) == 0,
        },
        "evidencePaths": [str(path) for path in evidence_paths],
        "malformed": malformed,
        "parityPassed": passed,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(args.output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--allenhark-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--continuity", type=pathlib.Path, required=True)
    parser.add_argument("--allenhark-health", type=pathlib.Path, required=True)
    parser.add_argument("--duration-seconds", type=int, default=120)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    result = freeze(args) if args.freeze else finalize(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
