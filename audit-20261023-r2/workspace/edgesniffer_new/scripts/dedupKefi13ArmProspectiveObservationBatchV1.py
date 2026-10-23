#!/usr/bin/env python3
"""Remove only verified overlap from a new outcome-blind observation batch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA = "KEFI_13_ARM_PROSPECTIVE_BATCH_DEDUP_V1"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def overlap_canonical(row: dict[str, Any]) -> str:
    """Compare immutable event content while ignoring sealed-window provenance."""
    copy = json.loads(json.dumps(row))
    raw_evidence = ((copy.get("state") or {}).get("raw_evidence") or {})
    raw_evidence.pop("window_id", None)
    raw_evidence.pop("window_manifest_sha256", None)
    return canonical(copy)


def existing_rows(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for partition in manifest["partitions"]:
        path = Path(partition["raw_observations"])
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                result[str(row["observation_id"])] = row
    return result


def run(manifest: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    saved = existing_rows(manifest)
    seen: dict[str, dict[str, Any]] = {}
    fresh: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        observation_id = str(row.get("observation_id", ""))
        prior = saved.get(observation_id) or seen.get(observation_id)
        if prior is not None:
            if overlap_canonical(prior) != overlap_canonical(row):
                conflicts.append({"line": index, "observation_id": observation_id, "reason": "CONFLICTING_DUPLICATE"})
            else:
                dropped.append({"line": index, "observation_id": observation_id, "reason": "EQUIVALENT_OVERLAP_WINDOW"})
            continue
        seen[observation_id] = row
        fresh.append(row)
    return fresh, {
        "schema": SCHEMA,
        "input_rows": len(rows),
        "fresh_rows": len(fresh),
        "identical_overlaps_dropped": len(dropped),
        "conflicts": conflicts,
        "dropped": dropped,
        "status": "PASS" if not conflicts else "FAIL_CONFLICTING_DUPLICATE",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    fresh, report = run(manifest, rows)
    args.output.write_text("".join(canonical(row) + "\n" for row in fresh), encoding="utf-8")
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "input_rows": report["input_rows"], "fresh_rows": report["fresh_rows"], "overlaps": report["identical_overlaps_dropped"]}, sort_keys=True))
    if report["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
