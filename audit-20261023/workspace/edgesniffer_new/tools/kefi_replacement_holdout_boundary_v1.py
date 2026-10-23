#!/usr/bin/env python3
"""Create the single immutable replacement-selection holdout boundary.

This tool deliberately does not inspect choices, feature values, ranks, market
outcomes, or PRE_H3.  It only binds frozen inputs before the observer starts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path


SCHEMA = "KEFI_REPLACEMENT_UNTOUCHED_SELECTION_HOLDOUT_V1_BOUNDARY"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def atomic_create(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--development-validity", type=Path, required=True)
    parser.add_argument("--old-holdout-failure", type=Path, required=True)
    parser.add_argument("--ledger-manifest", type=Path, required=True)
    parser.add_argument("--frozen-baseline", type=Path, required=True)
    parser.add_argument("--expanded-model", type=Path, required=True)
    parser.add_argument("--development-freeze", type=Path, required=True)
    parser.add_argument("--observer-binary", type=Path, required=True)
    parser.add_argument("--source-health", type=Path, required=True)
    parser.add_argument("--boundary-utc", required=True)
    args = parser.parse_args()

    if args.root.exists():
        raise SystemExit("FAIL_CLOSED: replacement root already exists")
    inputs = [args.development_validity, args.old_holdout_failure, args.ledger_manifest,
              args.frozen_baseline, args.expanded_model, args.development_freeze, args.observer_binary,
              args.source_health]
    for path in inputs:
        if not path.is_file():
            raise SystemExit(f"FAIL_CLOSED: required frozen input missing: {path}")
    ledger = json.loads(args.ledger_manifest.read_text(encoding="utf-8"))
    if ledger.get("schema") != "KEFI_OBSERVED_WALLET_MINT_LEDGER_MANIFEST_V1" or ledger.get("boundary_ambiguous_actions") != 0:
        raise SystemExit("FAIL_CLOSED: ledger manifest is not a boundary-safe durable history")
    source = json.loads(args.source_health.read_text(encoding="utf-8"))
    if not source.get("sourceInstanceId") or any(int(source.get(field, -1)) != 0 for field in ("reconnects", "receiveErrors", "continuityGaps")):
        raise SystemExit("FAIL_CLOSED: source is not a clean fresh continuity epoch")

    boundary = dt.datetime.fromisoformat(args.boundary_utc.replace("Z", "+00:00"))
    if boundary.tzinfo is None:
        raise SystemExit("FAIL_CLOSED: boundary UTC timezone required")
    receipt = {
        "schema": SCHEMA,
        "boundary_utc": boundary.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "OPEN",
        "target_valid_first_observed_selections": 20,
        "warmup_seconds": 60,
        "primary_universe": "NEW_TO_OBSERVED_WALLET",
        "secondary_frozen_sensitivity": "ALL_ELIGIBLE_POOLS",
        "stop_conditions": ["EXACTLY_20_VALID_SELECTIONS", "STORAGE_CAP_REACHED", "SCIENTIFIC_HARD_WALL"],
        "failed_original_holdout_reuse": "PROHIBITED",
        "interim_analysis": "PROHIBITED",
        "outcomes_or_pnl_accessed": False,
        "source_instance_id": source["sourceInstanceId"],
        "source_continuity_epoch": source.get("currentEpoch"),
        "bindings": {str(path): sha256_file(path) for path in inputs},
    }
    args.root.mkdir(parents=True, exist_ok=False)
    path = args.root / "boundary.json"
    atomic_create(path, canonical(receipt))
    print(json.dumps({"boundary_path": str(path), "boundary_sha256": sha256_file(path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
