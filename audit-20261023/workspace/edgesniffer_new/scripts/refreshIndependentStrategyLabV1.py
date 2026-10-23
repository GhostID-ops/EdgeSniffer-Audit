#!/usr/bin/env python3
"""Continuously refresh the independent strategy lab from enriched native rows."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from buildIndependentMarketControlsV1 import build as build_controls
from materializeIndependentStrategyWindowsV1 import materialize_stream
from runIndependentStrategyLabV1 import event_state, evaluate, mint_of, route_of, timestamp_ms


def _fingerprint(path: Path | None) -> dict[str, Any]:
    """Return a cheap append-only source fingerprint.

    The active replay input is append-only.  Size, mtime and inode let the
    refresh loop detect new data without hashing or rereading the source on
    every polling interval.  A size decrease or inode change is treated as a
    new source state, so truncation/replacement cannot silently reuse an old
    report.
    """
    if path is None or not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {
        "exists": True,
        "inode": int(getattr(stat, "st_ino", 0)),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _default_state_path(report: Path) -> Path:
    return report.with_suffix(report.suffix + ".state.json")


def _load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _refresh_result_from_state(state: dict[str, Any]) -> dict[str, Any] | None:
    result = state.get("last_result")
    if not isinstance(result, dict):
        return None
    cached = dict(result)
    cached["skipped"] = True
    cached["refresh_mode"] = "unchanged_input_checkpoint"
    return cached


def _window_watermarks(path: Path) -> dict[str, int]:
    """Load only signal/cutoff timestamps needed for delta relevance checks."""
    watermarks: dict[str, int] = {}
    if not path.exists():
        return watermarks
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            mint = mint_of(row)
            signal = row.get("signal_receive_ts_ms")
            try:
                if mint and signal is not None:
                    watermarks[mint] = int(signal)
            except (TypeError, ValueError):
                continue
    return watermarks


def _side(row: dict[str, Any]) -> str:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    return str(row.get("side") or raw.get("side") or "").lower()


def _scan_appended_replay(path: Path, offset: int,
                          watermarks: dict[str, int]) -> tuple[int, bool, bool]:
    """Read only appended complete lines and determine whether a rebuild matters.

    The replay is receive-time ordered and append-only.  Rows outside the
    active 300-second windows cannot change a materialized window.  We still
    advance the durable offset for those rows, so an unchanged refresh never
    rescans the historical replay file.
    """
    if not path.exists():
        return offset, False, False
    size = path.stat().st_size
    if offset < 0 or offset > size:
        return 0, True, True
    next_offset = offset
    saw_complete_line = False
    needs_rebuild = False
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            line_start = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break
            if not raw_line.endswith(b"\n"):
                next_offset = line_start
                break
            next_offset = handle.tell()
            saw_complete_line = True
            try:
                row = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            mint = mint_of(row)
            if not mint or route_of(row) not in {"PUMP_BONDING_CURVE_TOP_LEVEL", "PUMP_CURVE"}:
                continue
            ts = timestamp_ms(row)
            if ts is None or event_state(row) is None:
                continue
            signal_ts = watermarks.get(mint)
            if signal_ts is None:
                # A new buy, or a previously rejected mint now gaining a
                # complete state row, can create a newly admitted window.
                needs_rebuild = True
                continue
            # Existing windows can change only through their signal-to-300s
            # post-entry evidence.  Later rows are irrelevant to this lab.
            if signal_ts <= ts <= signal_ts + 300_000:
                needs_rebuild = True
    return next_offset, needs_rebuild, saw_complete_line


def refresh(args: argparse.Namespace) -> dict:
    state_path = getattr(args, "state", None) or _default_state_path(args.report)
    source_fingerprint = _fingerprint(args.input)
    state = _load_state(state_path)
    previous_fingerprint = state.get("input_fingerprint")
    scan_offset = state.get("scan_offset")
    watermarks = state.get("window_watermarks")
    if not isinstance(watermarks, dict):
        watermarks = {}
    try:
        scan_offset = int(scan_offset)
    except (TypeError, ValueError):
        scan_offset = None

    if scan_offset is not None and args.report.exists() and args.windows.exists():
        next_offset, needs_rebuild, saw_complete_line = _scan_appended_replay(
            args.input, scan_offset, {str(k): int(v) for k, v in watermarks.items()}
        )
        if not needs_rebuild:
            cached = _refresh_result_from_state(state)
            if cached is not None:
                cached["refresh_mode"] = (
                    "delta_no_relevant_change" if saw_complete_line else "unchanged_input_checkpoint"
                )
                cached["scan_offset"] = next_offset
                state["scan_offset"] = next_offset
                state["input_fingerprint"] = _fingerprint(args.input)
                state["last_result"] = cached
                state["updated_at_ms"] = int(time.time() * 1000)
                _write_state(state_path, state)
                return cached

    if (
        args.report.exists()
        and args.windows.exists()
        and previous_fingerprint == source_fingerprint
        and scan_offset is not None
    ):
        cached = _refresh_result_from_state(state)
        if cached is not None:
            return cached

    args.windows.parent.mkdir(parents=True, exist_ok=True)
    temp_windows = args.windows.with_suffix(args.windows.suffix + ".tmp")
    count, rejected = materialize_stream(
        args.input,
        temp_windows,
        require_native_economics_state=bool(getattr(args, "require_native_economics_state", False)),
    )
    os.replace(temp_windows, args.windows)
    controls_report = None
    controls_path = getattr(args, "controls", None)
    controls_report_path = getattr(args, "controls_report", None)
    if controls_path and controls_report_path:
        temp_controls = controls_path.with_suffix(controls_path.suffix + ".tmp")
        temp_controls_report = controls_report_path.with_suffix(controls_report_path.suffix + ".tmp")
        controls_report = build_controls(args.windows, temp_controls, temp_controls_report)
        os.replace(temp_controls, controls_path)
        os.replace(temp_controls_report, controls_report_path)
    result = evaluate(
        args.windows,
        controls_path,
        require_native_receipt_boundary=bool(getattr(args, "require_native_receipt_boundary", False)),
    )
    temp_report = args.report.with_suffix(args.report.suffix + ".tmp")
    temp_report.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp_report, args.report)
    refresh_result = {
        "windows": count, "rejected": rejected, "outcomes": len(result["outcomes"]),
        "surface_rows": len(result["surface"]), "controls": result["control_windows_loaded"],
        "control_pairs": (controls_report or {}).get("matched_pairs", 0),
        "skipped": False,
        "refresh_mode": "source_changed_full_rebuild",
        "scan_offset": _fingerprint(args.input).get("size", 0),
    }
    refreshed_watermarks = _window_watermarks(args.windows)
    _write_state(state_path, {
        "schema": "EDGESNIFFER_INCREMENTAL_STRATEGY_REFRESH_STATE_V1",
        "input_fingerprint": _fingerprint(args.input),
        "scan_offset": refresh_result["scan_offset"],
        "window_watermarks": refreshed_watermarks,
        "last_result": refresh_result,
        "last_report": str(args.report),
        "last_windows": str(args.windows),
        "updated_at_ms": int(time.time() * 1000),
    })
    return refresh_result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--controls", type=Path)
    parser.add_argument("--controls-report", type=Path)
    parser.add_argument("--state", type=Path,
                        help="checkpoint path used to skip unchanged source refreshes")
    parser.add_argument("--require-native-economics-state", action="store_true")
    parser.add_argument("--require-native-receipt-boundary", action="store_true")
    parser.add_argument("--poll-sec", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        print(json.dumps(refresh(args), sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(args.poll_sec)


if __name__ == "__main__":
    main()
