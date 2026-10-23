#!/usr/bin/env python3
"""Maintain one immutable-cutoff prospective causal strategy cohort.

The source windows and controls are rolling receive-only artifacts. This
process creates a separate cohort view containing only rows whose signal
receive time is at or after the fixed cutoff. It reruns the independent lab
against that view as new windows arrive. It never selects, freezes, promotes,
or executes a policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from runIndependentStrategyLabV1 import evaluate


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return digest.hexdigest()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                result.append(value)
    return result


def signal_time(row: dict[str, Any]) -> int | None:
    for key in ("signal_receive_ts_ms", "receive_ts_ms", "local_receive_ts_ms"):
        value = row.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def run_once(
    input_path: Path,
    controls_path: Path,
    output_windows: Path,
    output_controls: Path,
    output_report: Path,
    output_lab: Path,
    cohort_id: str,
    cutoff_ms: int,
) -> dict[str, Any]:
    source_windows = rows(input_path)
    source_controls = rows(controls_path)
    windows = [row for row in source_windows if (signal_time(row) or -1) >= cutoff_ms]
    controls = [row for row in source_controls if (signal_time(row) or -1) >= cutoff_ms]
    windows.sort(key=lambda row: (signal_time(row) or 0, str(row.get("mint") or ""), str(row.get("signal_signature") or "")))
    controls.sort(key=lambda row: (signal_time(row) or 0, str(row.get("mint") or ""), str(row.get("signal_signature") or "")))
    write_jsonl(output_windows, windows)
    write_jsonl(output_controls, controls)

    lab = evaluate(output_windows, output_controls, require_native_receipt_boundary=True)
    lab.update({
        "cohort_id": cohort_id,
        "cohort_cutoff_ms": cutoff_ms,
        "cohort_cutoff_iso_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff_ms / 1000)),
        "prospective": True,
        "fresh_cutoff_enforced": True,
        "policy_frozen": False,
        "selection_performed": False,
        "promotion_blocked": True,
        "read_only": True,
        "trading_enabled": False,
        "source_window_rows": len(source_windows),
        "source_control_rows": len(source_controls),
        "prospective_window_rows": len(windows),
        "prospective_control_rows": len(controls),
        "source_windows_sha256": sha256(input_path),
        "source_controls_sha256": sha256(controls_path),
        "cohort_windows_sha256": sha256(output_windows),
        "cohort_controls_sha256": sha256(output_controls),
        "cohort_manifest": {
            "cohort_id": cohort_id,
            "cutoff_ms": cutoff_ms,
            "selection_rule": "signal_receive_ts_ms >= fixed cutoff",
            "teacher_trigger_used": False,
            "native_boundary_required": True,
        },
    })
    output_lab.parent.mkdir(parents=True, exist_ok=True)
    temporary_lab = output_lab.with_suffix(output_lab.suffix + ".tmp")
    temporary_lab.write_text(json.dumps(lab, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary_lab, output_lab)
    report = {
        "schema": "EDGESNIFFER_PROSPECTIVE_CAUSAL_COHORT_V1",
        "generated_at_ms": int(time.time() * 1000),
        "cohort_id": cohort_id,
        "cutoff_ms": cutoff_ms,
        "cutoff_iso_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff_ms / 1000)),
        "read_only": True,
        "trading_enabled": False,
        "policy_frozen": False,
        "promotion_blocked": True,
        "source_windows": len(source_windows),
        "source_controls": len(source_controls),
        "prospective_windows": len(windows),
        "prospective_controls": len(controls),
        "boundary_rejections": lab.get("boundary_rejections", 0),
        "control_boundary_rejections": lab.get("control_boundary_rejections", 0),
        "lab_admitted_windows": lab.get("admitted_windows", 0),
        "lab_control_windows_loaded": lab.get("control_windows_loaded", 0),
        "inputs": {"windows": str(input_path), "controls": str(controls_path)},
        "outputs": {"windows": str(output_windows), "controls": str(output_controls), "lab": str(output_lab)},
        "last_lab_generated_at_ms": lab.get("generated_at_ms"),
    }
    output_report.parent.mkdir(parents=True, exist_ok=True)
    temporary_report = output_report.with_suffix(output_report.suffix + ".tmp")
    temporary_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary_report, output_report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--controls", type=Path, required=True)
    parser.add_argument("--output-windows", type=Path, required=True)
    parser.add_argument("--output-controls", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--output-lab", type=Path, required=True)
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--cutoff-ms", type=int, required=True)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        report = run_once(args.input, args.controls, args.output_windows, args.output_controls, args.output_report, args.output_lab, args.cohort_id, args.cutoff_ms)
        print(json.dumps(report, sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
