#!/usr/bin/env python3
"""Freeze a reference-only catalog for repeatable kEfi hypothesis research.

This tool never decodes source frames, fetches outcomes, or copies the market
tape.  It proves that a materialized market snapshot and its per-window
receipts are intact, then publishes a small immutable catalog that subsequent
hypotheses can reference by hash.  New rules therefore reuse the same sealed
evidence instead of starting another raw capture lane.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path


SCHEMA = "KEFI_REFERENCE_EVIDENCE_CATALOG_V1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def line_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def publish_immutable(path: Path, payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = sha256(path)
        if existing != digest:
            raise RuntimeError(f"FAIL_CLOSED: catalog already exists with different content: {path}")
        return digest
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("x") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o444)
    return digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--catalog-root", type=Path, required=True)
    parser.add_argument("--catalog-id", required=True)
    parser.add_argument("--outcome-open-cutoff-utc", required=True)
    parser.add_argument("--verify-market-rows", action="store_true")
    args = parser.parse_args()

    control_root = args.control_root.resolve()
    market = control_root / "market-deduplicated.jsonl"
    checkpoint_path = control_root / "checkpoint.json"
    state_path = control_root / "state.json"
    receipts_root = control_root / "receipts"
    if not market.is_file() or not checkpoint_path.is_file() or not state_path.is_file() or not receipts_root.is_dir():
        raise SystemExit("FAIL_CLOSED: incomplete materialized market surface")

    checkpoint = json.loads(checkpoint_path.read_text())
    state = json.loads(state_path.read_text())
    market_hash = sha256(market)
    if checkpoint.get("deduplicated_market_sha256") != market_hash:
        raise SystemExit("FAIL_CLOSED: checkpoint does not authenticate deduplicated market")

    windows = []
    for receipt_path in sorted(receipts_root.glob("*.json")):
        receipt = json.loads(receipt_path.read_text())
        rows_path = Path(receipt["market_rows_path"])
        if not rows_path.is_file():
            raise SystemExit(f"FAIL_CLOSED: missing market rows {rows_path}")
        expected = str(receipt["market_rows_sha256"])
        actual = sha256(rows_path) if args.verify_market_rows else expected
        if actual != expected:
            raise SystemExit(f"FAIL_CLOSED: market row hash mismatch {rows_path}")
        windows.append({
            "window_id": receipt["window_id"],
            "window_manifest_sha256": receipt["window_manifest_sha256"],
            "market_rows_path": str(rows_path),
            "market_rows_sha256": expected,
            "candidate_signatures": receipt["candidate_signatures"],
        })
    if not windows:
        raise SystemExit("FAIL_CLOSED: no authenticated materialized windows")

    boundary_path = args.source_root / "scientific-boundaries" / "paper-boundary-v1.json"
    if not boundary_path.is_file():
        raise SystemExit("FAIL_CLOSED: missing source boundary")
    catalog = {
        "schema": SCHEMA,
        "catalog_id": args.catalog_id,
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_boundary_path": str(boundary_path),
        "source_boundary_sha256": sha256(boundary_path),
        "control_state_path": str(state_path),
        "control_state_sha256": sha256(state_path),
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": sha256(checkpoint_path),
        "market": {
            "path": str(market),
            "sha256": market_hash,
            "rows": line_count(market),
            "unique_rows": checkpoint["deduplicated_market_rows"],
            "identical_overlaps": checkpoint["overlap_duplicates_verified_identical"],
        },
        "windows": windows,
        "outcome_open_cutoff_utc": args.outcome_open_cutoff_utc,
        "research_contract": {
            "reference_only": True,
            "raw_capture_copy_created": False,
            "outcomes_accessed": False,
            "pnl_accessed": False,
            "allowed_use": "Outcome-blind hypothesis construction and replay by immutable hash reference.",
            "restriction": "Any economic result after the stated cutoff is exploratory unless its hypothesis was preregistered before outcome access.",
        },
    }
    path = args.catalog_root / f"{args.catalog_id}.json"
    print(json.dumps({"catalog_path": str(path), "catalog_sha256": publish_immutable(path, catalog), "windows": len(windows), "market_rows": catalog["market"]["rows"]}, sort_keys=True))


if __name__ == "__main__":
    main()
