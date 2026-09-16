#!/usr/bin/env python3
"""Join capture-first AllenHark timestamps to finalized exact-ledger Pump classifications."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import struct
from typing import Any

from exactCurveEconomicsV1 import base58_decode
from buildPumpExactAlphaResearchV1 import paths_for_report
from reconcilePumpExactEvidenceLedgerV1 import iter_frames
from twoCqAllenharkParityV1 import atomic_json, iso, now


SCHEMA = "2CQ_ALLENHARK_ARRIVAL_POST_CLASSIFIER_V1"
SOURCE = "allenhark-jito-shreds-grpc-v1"
MAGIC = b"AHATX01\n"
RECORD = struct.Struct("<64sqQQIIQQQQ")


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("rb") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def target_arrivals(
    binary_root: pathlib.Path,
    signatures: set[str],
    start_ns: int,
) -> dict[str, dict[str, Any]]:
    by_raw = {base58_decode(signature): signature for signature in signatures}
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(binary_root.glob("*/*/*/*/arrivals.bin.partial")):
        with path.open("rb") as stream:
            if stream.read(len(MAGIC)) != MAGIC:
                raise RuntimeError(f"ALLENHARK_BINARY_MAGIC_MISMATCH:{path}")
            while True:
                raw = stream.read(RECORD.size)
                if not raw:
                    break
                if len(raw) != RECORD.size:
                    break
                values = RECORD.unpack(raw)
                signature = by_raw.get(values[0])
                if signature is None or values[1] < start_ns:
                    continue
                candidate = {
                    "signature": signature, "receivedAtUnixNs": values[1], "slot": values[2],
                    "streamSequence": values[3], "entryIndex": values[4],
                    "transactionIndexWithinEntry": values[5], "observedSlotTransactionOrdinal": values[6],
                    "connectionEpoch": values[7], "processInstanceHash": values[8],
                    "connectedAtCapture": values[9] == 1,
                }
                current = found.get(signature)
                if current is None or candidate["receivedAtUnixNs"] < current["receivedAtUnixNs"]:
                    found[signature] = candidate
    return found


def initialize(args: argparse.Namespace) -> dict[str, Any]:
    path = args.output_root / "post-classifier-contract.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    health = json.loads(args.allenhark_health.read_text(encoding="utf-8"))
    contract = {
        "schema": SCHEMA + "_CONTRACT", "createdAtUtc": iso(now()),
        "captureStartUtc": health["startedAtUtc"],
        "captureStartUnixNs": int(dt.datetime.fromisoformat(health["startedAtUtc"].replace("Z", "+00:00")).timestamp() * 1_000_000_000),
        "allenharkProcessInstance": health["processInstance"],
        "classificationAuthority": "PUMP_EXACT_EVIDENCE_LEDGER_V1 finalized successful canonical decoding",
        "arrivalAuthority": SOURCE,
        "timestampInferencePermitted": False,
        "outcomesRead": 0, "submissions": 0,
    }
    atomic_json(path, contract)
    atomic_json(args.output_root / "post-classifier-state.json", {
        "schema": SCHEMA + "_STATE", "processedReports": [], "updatedAtUtc": None,
    })
    return contract


def run(args: argparse.Namespace) -> dict[str, Any]:
    contract = initialize(args)
    state_path = args.output_root / "post-classifier-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    processed = set(state.get("processedReports") or [])
    reports = []
    for path in sorted((args.exact_root / "readiness-v1/partitions").glob("*-v4.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("sourceExactReplayComplete") is not True:
            continue
        if report.get("partitionKey") in processed:
            continue
        partition_hour = dt.datetime.fromisoformat(report["partitionHourUtc"].replace("Z", "+00:00"))
        if partition_hour + dt.timedelta(hours=1) < dt.datetime.fromisoformat(contract["captureStartUtc"].replace("Z", "+00:00")):
            continue
        reports.append(report)

    finalized: dict[str, dict[str, Any]] = {}
    report_signatures: dict[str, set[str]] = {}
    for report in reports:
        signatures: set[str] = set()
        for path in paths_for_report(args.exact_root, report):
            for metadata, _ in iter_frames(path, path.suffix == ".partial"):
                if (
                    metadata.get("kind") == "rpc_finalized_transaction"
                    and metadata.get("finalized") is True
                    and metadata.get("successful") is True
                    and metadata.get("pumpInstructionKinds")
                ):
                    signature = str(metadata.get("signature") or "")
                    if signature:
                        finalized[signature] = metadata
                        signatures.add(signature)
        report_signatures[str(report["partitionKey"])] = signatures

    existing = {row["signature"] for row in read_jsonl(args.output_root / "classified-finalized-pump-arrivals.jsonl")}
    arrivals = target_arrivals(args.all_transaction_root, set(finalized) - existing, int(contract["captureStartUnixNs"]))
    new_rows = []
    for signature, arrival in arrivals.items():
        final = finalized[signature]
        new_rows.append({
            "schema": SCHEMA + "_CLASSIFIED_ARRIVAL", "source": SOURCE,
            "signature": signature, "receivedAtUnixNs": arrival["receivedAtUnixNs"],
            "receivedAtUtc": iso(dt.datetime.fromtimestamp(arrival["receivedAtUnixNs"] / 1_000_000_000, dt.timezone.utc)),
            "allenharkSlot": arrival["slot"], "canonicalSlot": final.get("slot"),
            "canonicalTransactionIndex": final.get("transactionIndex"),
            "pumpInstructionKinds": final.get("pumpInstructionKinds"),
            "pumpInstructionCoordinates": final.get("pumpInstructionCoordinates"),
            "successful": True, "finalized": True,
            "classificationEvidenceId": final.get("evidenceId"),
            "observedOrdering": {
                "streamSequence": arrival["streamSequence"], "entryIndex": arrival["entryIndex"],
                "transactionIndexWithinEntry": arrival["transactionIndexWithinEntry"],
                "observedSlotTransactionOrdinal": arrival["observedSlotTransactionOrdinal"],
            },
        })
    append_jsonl(args.output_root / "classified-finalized-pump-arrivals.jsonl", new_rows)
    processed.update(report_signatures)
    state.update({"processedReports": sorted(processed), "updatedAtUtc": iso(now())})
    atomic_json(state_path, state)
    summary = {
        "schema": SCHEMA + "_STATUS", "atUtc": iso(now()),
        "newReports": len(reports), "finalizedSuccessfulPumpConsidered": len(finalized),
        "newClassifiedArrivals": len(new_rows), "missingAllenharkArrival": len(set(finalized) - existing - set(arrivals)),
        "totalClassifiedArrivals": len(existing) + len(new_rows),
        "outcomesRead": 0, "submissions": 0,
    }
    atomic_json(args.output_root / "post-classifier-status.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--all-transaction-root", type=pathlib.Path, required=True)
    parser.add_argument("--allenhark-health", type=pathlib.Path, required=True)
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
