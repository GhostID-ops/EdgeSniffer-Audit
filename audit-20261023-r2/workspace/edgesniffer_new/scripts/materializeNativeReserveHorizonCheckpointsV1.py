#!/usr/bin/env python3
"""Materialize native Pump reserve checkpoints for fixed exit horizons.

This is a receive-only research lane.  It joins prospective market windows to
the native Pump account-state stream by bonding curve and emits the first
complete native reserve update at or after each requested exit deadline.

The output is deliberately separate from ``marks`` in the strict market
windows.  A checkpoint is an account-state observation with its own receive
timestamp, not a transaction event at the exact deadline and not a
carry-forward quote.  Missing account updates remain missing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


DEFAULT_HORIZONS = (5, 10, 15, 30, 60, 120, 300)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def curve_of(row: dict[str, Any]) -> str | None:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    for value in (
        row.get("bondingCurve"), row.get("bonding_curve"),
        raw.get("bondingCurve"), raw.get("bonding_curve"),
        extra.get("bondingCurve"), extra.get("bonding_curve"),
    ):
        if value:
            return str(value)
    for mark in row.get("marks") or []:
        if isinstance(mark, dict):
            raw_mark = mark.get("raw_record") if isinstance(mark.get("raw_record"), dict) else {}
            extra_mark = mark.get("extra") if isinstance(mark.get("extra"), dict) else {}
            value = raw_mark.get("bondingCurve") or extra_mark.get("bondingCurve")
            if value:
                return str(value)
    return None


def window_id(row: dict[str, Any]) -> str:
    identity = {
        "fingerprint_id": row.get("fingerprint_id"),
        "mint": row.get("mint"),
        "signal_receive_ts_ms": as_int(row.get("signal_receive_ts_ms")),
        "signal_signature": row.get("signal_signature"),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def timestamp_of_seed(row: dict[str, Any]) -> int | None:
    native_ns = as_int(row.get("native_receive_unix_ns"))
    if native_ns and native_ns > 0:
        return native_ns // 1_000_000
    return as_int(row.get("observedAtMs") or row.get("observed_at_ms"))


def complete_seed(row: dict[str, Any]) -> bool:
    required = (
        row.get("virtualSolReserves", row.get("virtual_sol_reserves")),
        row.get("virtualTokenReserves", row.get("virtual_token_reserves")),
        row.get("realSolReserves", row.get("real_sol_reserves")),
        row.get("realTokenReserves", row.get("real_token_reserves")),
    )
    return all(as_int(value) is not None and as_int(value) >= 0 for value in required)


def seed_state(row: dict[str, Any]) -> dict[str, int] | None:
    if not complete_seed(row):
        return None
    values = {
        "virtual_sol": as_int(row.get("virtualSolReserves", row.get("virtual_sol_reserves"))),
        "virtual_token": as_int(row.get("virtualTokenReserves", row.get("virtual_token_reserves"))),
        "real_sol": as_int(row.get("realSolReserves", row.get("real_sol_reserves"))),
        "real_token": as_int(row.get("realTokenReserves", row.get("real_token_reserves"))),
    }
    if any(value is None for value in values.values()):
        return None
    return {key: int(value) for key, value in values.items()}


def load_windows(path: Path, start_at_receive_ms: int | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return result
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("record_type") not in {
                "INDEPENDENT_MARKET_WINDOW", "MARKET_WINDOW"
            }:
                continue
            signal = as_int(row.get("signal_receive_ts_ms"))
            if signal is None or (start_at_receive_ms is not None and signal < start_at_receive_ms):
                continue
            curve = curve_of(row)
            if not curve:
                # A window without a curve identity cannot be joined to an
                # account update and must remain explicitly unmarked.
                continue
            item = dict(row)
            item["_checkpoint_curve"] = curve
            item["_window_id"] = window_id(row)
            result[item["_window_id"]] = item
    return result


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seed_offset": 0, "last_seed_ts_ms": 0, "stats": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"seed_offset": 0}
    except (OSError, json.JSONDecodeError):
        return {"seed_offset": 0, "last_seed_ts_ms": 0, "stats": {}}


def read_emitted(path: Path) -> set[str]:
    emitted: set[str] = set()
    if not path.exists():
        return emitted
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            identity = row.get("checkpoint_id")
            if identity:
                emitted.add(str(identity))
    return emitted


def checkpoint_id(window: dict[str, Any], horizon: int) -> str:
    return f"{window['_window_id']}:{horizon}s"


def build_pending(windows: dict[str, dict[str, Any]], horizons: tuple[int, ...], emitted: set[str]) -> dict[str, dict[str, dict[int, dict[str, Any]]]]:
    pending: dict[str, dict[str, dict[int, dict[str, Any]]]] = {}
    for window in windows.values():
        signal = as_int(window.get("signal_receive_ts_ms"))
        if signal is None:
            continue
        entry = signal + 1_000
        curve = str(window["_checkpoint_curve"])
        for horizon in horizons:
            if checkpoint_id(window, horizon) in emitted:
                continue
            pending.setdefault(curve, {}).setdefault(str(window["_window_id"]), {})[horizon] = {
                "window": window,
                "deadline_ts_ms": entry + horizon * 1_000,
            }
    return pending


def make_checkpoint(window: dict[str, Any], horizon: int, seed: dict[str, Any], state: dict[str, int], deadline: int) -> dict[str, Any]:
    observed = timestamp_of_seed(seed)
    assert observed is not None
    return {
        "schema": "EDGESNIFFER_NATIVE_RESERVE_HORIZON_CHECKPOINT_V1",
        "record_type": "NATIVE_RESERVE_HORIZON_CHECKPOINT",
        "checkpoint_id": checkpoint_id(window, horizon),
        "window_id": window["_window_id"],
        "mint": window.get("mint"),
        "bonding_curve": window["_checkpoint_curve"],
        "fingerprint_id": window.get("fingerprint_id"),
        "signal_receive_ts_ms": as_int(window.get("signal_receive_ts_ms")),
        "entry_boundary_ts_ms": as_int(window.get("signal_receive_ts_ms")) + 1_000,
        "horizon_seconds": horizon,
        "deadline_ts_ms": deadline,
        "checkpoint_receive_ts_ms": observed,
        "delay_after_deadline_ms": observed - deadline,
        "state": state,
        "virtual_sol_reserves": state["virtual_sol"],
        "virtual_token_reserves": state["virtual_token"],
        "real_sol_reserves": state["real_sol"],
        "real_token_reserves": state["real_token"],
        "fee_basis_points": as_int(seed.get("fee_basis_points")) or 125,
        "fee_fields": {
            "fee_basis_points": as_int(seed.get("fee_basis_points")) or 125,
            "source": "native_pump_account_state_stream",
            "explicit_in_seed": all(key in seed for key in ("fee_basis_points",)),
        },
        "native_lineage": {
            "source": "native-account-state",
            "native_receive_unix_ns": as_int(seed.get("native_receive_unix_ns")),
            "native_receive_source": seed.get("native_receive_source"),
            "slot": as_int(seed.get("slot")),
            "write_version": as_int(seed.get("write_version")),
            "account_txn_signature": seed.get("account_txn_signature"),
            "observed_at_ms": observed,
        },
        "mark_semantics": "first_complete_native_account_update_at_or_after_deadline",
        "strict_mark": False,
        "carry_forward": False,
        "read_only": True,
        "trading_enabled": False,
    }


def process_once(args: argparse.Namespace, state_doc: dict[str, Any], emitted: set[str]) -> dict[str, Any]:
    windows = load_windows(args.windows, args.start_at_receive_ms)
    horizons = tuple(args.horizons)
    pending = build_pending(windows, horizons, emitted)
    stats = {
        "windows_seen": len(windows),
        "curves_joinable": len(pending),
        "seed_rows_read": 0,
        "checkpoints_written": 0,
        "incomplete_native_seeds": 0,
        "unmatched_seed_rows": 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    offset = int(state_doc.get("seed_offset") or 0)
    if args.seeds.exists() and args.seeds.stat().st_size < offset:
        offset = 0
    with args.seeds.open("r", encoding="utf-8", errors="replace") as source, args.output.open("a", encoding="utf-8") as output:
        source.seek(offset)
        while True:
            line = source.readline()
            if not line:
                offset = source.tell()
                break
            stats["seed_rows_read"] += 1
            try:
                seed = json.loads(line)
            except json.JSONDecodeError:
                offset = source.tell()
                continue
            curve = str(seed.get("bondingCurve") or seed.get("bonding_curve") or "")
            if not curve or curve not in pending:
                stats["unmatched_seed_rows"] += 1
                offset = source.tell()
                continue
            observed = timestamp_of_seed(seed)
            if observed is None:
                offset = source.tell()
                continue
            native_state = seed_state(seed)
            if native_state is None:
                stats["incomplete_native_seeds"] += 1
                offset = source.tell()
                continue
            for window_key, horizons_for_window in list(pending[curve].items()):
                for horizon, item in list(horizons_for_window.items()):
                    deadline = int(item["deadline_ts_ms"])
                    if observed < deadline:
                        continue
                    row = make_checkpoint(item["window"], horizon, seed, native_state, deadline)
                    if row["checkpoint_id"] in emitted:
                        del horizons_for_window[horizon]
                        continue
                    output.write(json.dumps(row, sort_keys=True) + "\n")
                    output.flush()
                    emitted.add(row["checkpoint_id"])
                    del horizons_for_window[horizon]
                    stats["checkpoints_written"] += 1
                if not horizons_for_window:
                    del pending[curve][window_key]
            if not pending[curve]:
                del pending[curve]
            offset = source.tell()
    state_doc.update({
        "schema": "EDGESNIFFER_NATIVE_RESERVE_HORIZON_CHECKPOINT_STATE_V1",
        "seed_offset": offset,
        "last_run_at_ms": int(time.time() * 1000),
        "stats": stats,
    })
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(state_doc, sort_keys=True) + "\n", encoding="utf-8")
    stats["checkpoints_total"] = len(emitted)
    stats["pending_horizons"] = sum(
        len(horizons_for_window)
        for windows_for_curve in pending.values()
        for horizons_for_window in windows_for_curve.values()
    )
    return stats


def run(args: argparse.Namespace) -> None:
    state_doc = load_state(args.state)
    emitted = read_emitted(args.output)
    while True:
        result = process_once(args, state_doc, emitted)
        result.update({
            "schema": "EDGESNIFFER_NATIVE_RESERVE_HORIZON_CHECKPOINT_RUN_V1",
            "windows_sha256": sha256_file(args.windows) if args.windows.exists() else None,
            "seed_input_sha256": sha256_file(args.seeds) if args.seeds.exists() else None,
            "read_only": True,
            "trading_enabled": False,
        })
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(max(1.0, args.poll_sec))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--seeds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--start-at-receive-ms", type=int)
    parser.add_argument("--horizons", default=",".join(map(str, DEFAULT_HORIZONS)))
    parser.add_argument("--poll-sec", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    args.horizons = tuple(sorted({int(value) for value in str(args.horizons).split(",") if value.strip()}))
    if not args.horizons or any(value <= 0 for value in args.horizons):
        parser.error("--horizons must contain positive seconds")
    run(args)


if __name__ == "__main__":
    main()
