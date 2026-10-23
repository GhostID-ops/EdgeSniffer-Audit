#!/usr/bin/env python3
"""Freeze exactly twenty replacement-holdout snapshots without scoring them."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def write_once(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical(value))
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--boundary", type=Path, required=True)
    parser.add_argument("--sealed-observer-root", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    boundary = json.loads(args.boundary.read_text(encoding="utf-8"))
    manifest = json.loads((args.sealed_observer_root / "manifest.json").read_text(encoding="utf-8"))
    validation = json.loads((args.sealed_observer_root / "validation.json").read_text(encoding="utf-8"))
    snapshots = sorted((args.sealed_observer_root / "durable-snapshots").glob("*.json"))
    receipts = sorted((args.sealed_observer_root / "snapshot-receipts").glob("*.json"))
    if boundary.get("status") != "OPEN" or manifest.get("validation_status") != "SNAPSHOT_TARGET_REACHED" or validation.get("status") != "SNAPSHOT_TARGET_REACHED" or len(snapshots) != 20 or len(receipts) != 20:
        raise SystemExit("FAIL_CLOSED: replacement cohort did not reach exactly 20 valid snapshots")
    ledger_rows = {json.loads(line)["record_sha256"]: json.loads(line) for line in args.ledger.read_text(encoding="utf-8").splitlines() if line.strip()}
    entries = []
    for snapshot_path, receipt_path in zip(snapshots, receipts, strict=True):
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        record = ledger_rows.get(snapshot.get("wallet_ledger_record_sha256"))
        if record is None or record.get("mint") != snapshot.get("chosen_mint") or record.get("sequence") != snapshot.get("wallet_ledger_sequence_before", -1) + 1 or receipt.get("snapshot_sha256") != sha256_file(snapshot_path):
            raise SystemExit("FAIL_CLOSED: snapshot is not bound to the durable observed-wallet ledger")
        entries.append({"snapshot": snapshot_path.name, "snapshot_sha256": sha256_file(snapshot_path), "receipt": receipt_path.name, "receipt_sha256": sha256_file(receipt_path), "teacher_signature": snapshot["teacher_signature"], "chosen_mint": snapshot["chosen_mint"], "candidate_count": len(snapshot["candidates"]), "wallet_ledger_record_sha256": snapshot["wallet_ledger_record_sha256"]})
    output = args.sealed_observer_root / "replacement-holdout-manifest.json"
    write_once(output, {"schema": "KEFI_REPLACEMENT_UNTOUCHED_SELECTION_HOLDOUT_V1_MANIFEST", "boundary_sha256": sha256_file(args.boundary), "observer_manifest_sha256": sha256_file(args.sealed_observer_root / "manifest.json"), "ledger_sha256": sha256_file(args.ledger), "selection_count": 20, "entries": entries, "outcomes_or_pnl_accessed": False, "evaluation_status": "SEALED_UNOPENED"})
    print(json.dumps({"manifest": str(output), "sha256": sha256_file(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
