#!/usr/bin/env python3
"""Merge audited PumpSwap tape segments into one deterministic mark tape.

The receive-only collector writes a new file when its sealed source segment
changes. Economics must consume the union of those exact segments, not just
the newest rolling slice. Rows retain their source identity and sequence;
deduplication is by immutable event identity and conflicting duplicates fail.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "KEFI_13_ARM_PUMPSWAP_CUMULATIVE_TAPE_V1"
REQUIRED = {"schema", "source_frame_sha256", "event_index", "received_at_unix_ns"}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def identity(row: dict[str, Any]) -> tuple[str, int, int]:
    return (str(row["source_frame_sha256"]), int(row["event_index"]), int(row["received_at_unix_ns"]))


def merge(paths: list[Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: dict[tuple[str, int, int], dict[str, Any]] = {}
    source_segments: set[tuple[str, int]] = set()
    errors: list[dict[str, Any]] = []
    duplicate_rows = 0
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not REQUIRED.issubset(row):
                    raise ValueError("MISSING_TAPE_IDENTITY_FIELDS")
                if row.get("schema") != "KEFI_13_ARM_PUMPSWAP_EXACT_TAPE_V1":
                    raise ValueError("INVALID_TAPE_SCHEMA")
                key = identity(row)
                if {"source_instance_id", "continuity_epoch", "durable_output_sequence"}.issubset(row):
                    source_segments.add((str(row["source_instance_id"]), int(row["continuity_epoch"])))
                else:
                    source_segments.add(("legacy:" + path.name, 0))
                prior = rows.get(key)
                if prior is not None:
                    if canonical(prior) != canonical(row):
                        errors.append({"path": str(path), "line": line_number, "reason": "CONFLICTING_DUPLICATE", "identity": list(key)})
                    else:
                        duplicate_rows += 1
                    continue
                rows[key] = row
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                errors.append({"path": str(path), "line": line_number, "reason": str(exc)})
    ordered = sorted(rows.values(), key=lambda row: (int(row["received_at_unix_ns"]), identity(row)))
    report = {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_files": [str(path) for path in paths],
        "input_file_count": len(paths),
        "source_segment_count": len(source_segments),
        "source_segments": [{"source_instance_id": instance, "continuity_epoch": epoch} for instance, epoch in sorted(source_segments)],
        "legacy_rows_without_parity_fields": sum(1 for row in ordered if "source_instance_id" not in row),
        "input_rows": sum(sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) for path in paths),
        "merged_rows": len(ordered),
        "identical_duplicate_rows_dropped": duplicate_rows,
        "errors": errors,
        "economics_ready": not errors and bool(ordered),
        "status": "PASS_CUMULATIVE_TAPE_MERGE" if not errors else "FAIL_CUMULATIVE_TAPE_MERGE",
    }
    return ordered, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    rows, report = merge(args.tape)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "input_rows": report["input_rows"], "merged_rows": report["merged_rows"], "segments": report["source_segment_count"]}, sort_keys=True))
    if report["errors"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
