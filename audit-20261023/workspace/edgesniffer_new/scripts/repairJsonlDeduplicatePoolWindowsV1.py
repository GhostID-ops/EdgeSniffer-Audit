#!/usr/bin/env python3
"""Create a deduplicated PumpSwap pool-window file without discarding evidence.

The input is never modified. Duplicate anchor envelopes are keyed by
anchor_signature and market rows by signature. Conflicting duplicate payloads
fail closed instead of silently selecting one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def canonical(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def comparable(row: dict[str, Any]) -> str:
    """Compare semantic evidence, excluding append-envelope metadata."""
    kind = str(row.get("record_type") or "")
    if kind == "MARKET_TRANSACTION":
        value = {
            "schema": row.get("schema"),
            "record_type": kind,
            "cohort_id": row.get("cohort_id"),
            "boundary_utc": row.get("boundary_utc"),
            "signature": row.get("signature"),
            "slot": row.get("slot"),
            "block_time": row.get("block_time"),
            "transaction_sha256": row.get("transaction_sha256"),
            "raw_transaction": row.get("raw_transaction"),
        }
        return canonical(value)
    if kind == "ANCHOR_WINDOW":
        value = {
            "schema": row.get("schema"),
            "record_type": kind,
            "cohort_id": row.get("cohort_id"),
            "boundary_utc": row.get("boundary_utc"),
            "anchor_signature": row.get("anchor_signature"),
            "anchor_slot": row.get("anchor_slot"),
            "anchor_block_time": row.get("anchor_block_time"),
            "entry_delay_seconds": row.get("entry_delay_seconds"),
            "exit_horizon_seconds": row.get("exit_horizon_seconds"),
            "pool": row.get("pool"),
            "window_signatures": sorted(set(row.get("window_signatures") or [])),
        }
        return canonical(value)
    return canonical(row)


def merge_anchor_window(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """Merge a later, more complete signature list for the same anchor."""
    if old.get("record_type") != "ANCHOR_WINDOW" or new.get("record_type") != "ANCHOR_WINDOW":
        return False
    old_base = dict(old)
    new_base = dict(new)
    old_base.pop("window_signatures", None)
    new_base.pop("window_signatures", None)
    for value in (old_base, new_base):
        value.pop("captured_at_utc", None)
    if canonical(old_base) != canonical(new_base):
        return False
    old["window_signatures"] = sorted(set(old.get("window_signatures") or []) | set(new.get("window_signatures") or []))
    return True


def key_for(row: dict[str, Any]) -> tuple[str, str] | None:
    kind = str(row.get("record_type") or "")
    if kind == "ANCHOR_WINDOW" and row.get("anchor_signature"):
        return kind, str(row["anchor_signature"])
    if kind == "MARKET_TRANSACTION" and row.get("signature"):
        return kind, str(row["signature"])
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repair(input_path: Path, output_path: Path, report_path: Path) -> dict[str, Any]:
    input_sha256 = sha256_file(input_path)
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str] | tuple[str, int]] = []
    malformed = 0
    duplicate_rows = 0
    conflicts: list[dict[str, Any]] = []
    total = 0
    with input_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            total += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                malformed += 1
                if len(conflicts) < 20:
                    conflicts.append({"line": line_number, "kind": "MALFORMED_JSON", "error": str(error)})
                continue
            if not isinstance(row, dict):
                malformed += 1
                continue
            key = key_for(row)
            if key is None:
                fallback = ("UNKEYED", len(order))
                order.append(fallback)
                unique[fallback] = row
                continue
            if key in unique:
                duplicate_rows += 1
                if merge_anchor_window(unique[key], row):
                    continue
                if comparable(unique[key]) != comparable(row):
                    if len(conflicts) < 20:
                        conflicts.append({"key": list(key), "kind": "CONFLICTING_DUPLICATE", "line": line_number})
                continue
            unique[key] = row
            order.append(key)
    if malformed or any(item.get("kind") == "CONFLICTING_DUPLICATE" for item in conflicts):
        raise RuntimeError(f"FAIL_CLOSED: malformed={malformed} conflicts={sum(item.get('kind') == 'CONFLICTING_DUPLICATE' for item in conflicts)}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp = output_path.with_name(output_path.name + ".tmp-dedup")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for key in order:
            handle.write(canonical(unique[key]) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, output_path)
    result = {
        "schema": "POOL_WINDOWS_DEDUP_REPAIR_REPORT_V1",
        "input": str(input_path),
        "input_sha256": input_sha256,
        "output": str(output_path),
        "output_sha256": sha256_file(output_path),
        "input_rows": total,
        "output_rows": len(order),
        "duplicate_rows_removed": duplicate_rows,
        "malformed_rows": malformed,
        "conflicting_duplicates": sum(item.get("kind") == "CONFLICTING_DUPLICATE" for item in conflicts),
        "conflict_samples": conflicts,
        "read_only_source_preserved": True,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(repair(args.input, args.output, args.report), sort_keys=True))


if __name__ == "__main__":
    main()
