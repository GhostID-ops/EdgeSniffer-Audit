#!/usr/bin/env python3
"""Join exact frozen-scorer events to native causal Pump windows.

This is deliberately separate from signal scoring.  The scorer decides which
native event crossed the preserved threshold; this module only reconstructs
what was available one second later and attaches native marks.  It never uses
the teacher label to admit a signal and it emits controls from the same tape.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import runIndependentStrategyLabV1 as lab
from materialize2CQEventDrivenIndependentWindowsV1 import side_of, sort_key, timestamp


ENTRY_DELAY_MS = 1_000
PUMP_ROUTES = {"PUMP_CURVE", "PUMP_BONDING_CURVE_TOP_LEVEL"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def state_complete(row: dict[str, Any]) -> bool:
    return row.get("native_economics_state_complete") is True and lab.event_state(row) is not None and all(
        isinstance(row.get(name), (int, float)) and not isinstance(row.get(name), bool)
        for name in ("fee_basis_points", "protocol_fee_basis_points", "creator_fee_basis_points")
    )


def signature_of(row: dict[str, Any]) -> str:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    return str(row.get("transaction_signature") or row.get("signature") or raw.get("signature") or row.get("observation_id") or "")


def quote_sol(row: dict[str, Any]) -> float:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    value = row.get("quote_lamports")
    if value is None:
        value = raw.get("quoteLamports")
    try:
        return abs(float(value)) / 1_000_000_000 if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def build_window(event: dict[str, Any], source_rows: list[dict[str, Any]], as_of: int) -> tuple[dict[str, Any] | None, str | None]:
    mint = str(event.get("mint") or "")
    signal_ts = int(event.get("signal_receive_ts_ms"))
    signal_sig = str(event.get("signal_signature") or "")
    mint_rows = [row for row in source_rows if str(row.get("mint") or "") == mint]
    signal_rows = [row for row in mint_rows if signature_of(row) == signal_sig]
    if not signal_rows:
        signal_rows = [row for row in mint_rows if timestamp(row) == signal_ts]
    if not signal_rows:
        return None, "signal_source_row_not_found"
    entry_ts = signal_ts + ENTRY_DELAY_MS
    usable = [row for row in mint_rows if (timestamp(row) or -1) <= entry_ts and state_complete(row)]
    if not usable:
        return None, "missing_native_entry_state_or_fee"
    entry = max(usable, key=sort_key)
    marks = [row for row in mint_rows if (timestamp(row) or -1) >= entry_ts and lab.event_state(row) is not None]
    route = lab.route_of(entry)
    if route not in PUMP_ROUTES:
        return None, "missing_exact_pump_route"
    signal_state = lab.event_state(signal_rows[0])
    entry_state = lab.event_state(entry)
    if signal_state is None or entry_state is None:
        return None, "missing_signal_or_entry_state"
    signal_price = signal_state.virtual_sol / max(1, signal_state.virtual_token)
    entry_price = entry_state.virtual_sol / max(1, entry_state.virtual_token)
    delay_rows = [row for row in mint_rows if signal_ts <= (timestamp(row) or -1) < entry_ts]
    record_type = "INDEPENDENT_MARKET_CONTROL_WINDOW" if event.get("record_type") == "INDEPENDENT_EVENT_DRIVEN_CONTROL_EVENT" else "INDEPENDENT_EVENT_DRIVEN_MARKET_WINDOW"
    window = {
        "record_type": record_type,
        "fingerprint_id": "2CQ_FROZEN_SCORER_V1",
        "fingerprint_source": "preserved_2cq_event_driven_fingerprint_freeze",
        "mint": mint,
        "route": route,
        "signal_receive_ts_ms": signal_ts,
        "signal_signature": signal_sig,
        "signal_wallet": event.get("signal_wallet"),
        "teacher_wallet_match": False,
        "control_group": event.get("control_group"),
        "score": event.get("score"),
        "threshold": event.get("threshold"),
        "scorer_features": event.get("features"),
        "entry_state": {
            "virtual_sol": entry_state.virtual_sol, "virtual_token": entry_state.virtual_token,
            "real_sol": entry_state.real_sol, "real_token": entry_state.real_token,
        },
        "fee_basis_points": entry.get("fee_basis_points"),
        "protocol_fee_basis_points": entry.get("protocol_fee_basis_points"),
        "creator_fee_basis_points": entry.get("creator_fee_basis_points"),
        "entry_quote_lamports": entry.get("quote_lamports"),
        "effective_quote_reserve_sol": entry_state.virtual_sol / 1_000_000_000,
        "net_flow_sol": sum(quote_sol(row) if side_of(row) == "buy" else -quote_sol(row) for row in delay_rows),
        "price_change_bps": (entry_price / signal_price - 1.0) * 10_000 if signal_price > 0 else None,
        "entry_impact_bps": None,
        "marks": marks,
        "observation_as_of_ts_ms": as_of,
        "native_economics_state_complete": True,
        "lineage": {
            "source_receive_clock": True,
            "entry_state_source": entry.get("observation_id") or signature_of(entry),
            "entry_state_receive_ts_ms": timestamp(entry),
            "entry_delay_ms": ENTRY_DELAY_MS,
            "native_state_source": "native_replay",
            "teacher_label_used_for_admission": False,
            "exact_scorer_event_source": "frozen_scorer_adapter",
        },
    }
    return window, None


def materialize(input_path: Path, signals_path: Path, controls_path: Path,
                output_path: Path, controls_output: Path, report_path: Path) -> dict[str, Any]:
    rows = sorted(read_jsonl(input_path), key=sort_key)
    signals = read_jsonl(signals_path)
    controls = read_jsonl(controls_path)
    as_of = max((timestamp(row) or 0 for row in rows), default=0)
    counts: dict[str, int] = {}
    signal_windows: list[dict[str, Any]] = []
    control_windows: list[dict[str, Any]] = []
    for source, output in ((signals, signal_windows), (controls, control_windows)):
        for event in source:
            window, reason = build_window(event, rows, as_of)
            if window is None:
                key = reason or "unknown_rejection"
                counts[key] = counts.get(key, 0) + 1
            else:
                output.append(window)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    controls_output.parent.mkdir(parents=True, exist_ok=True)
    for path, values in ((output_path, signal_windows), (controls_output, control_windows)):
        with path.open("w", encoding="utf-8") as handle:
            for value in values:
                handle.write(json.dumps(value, sort_keys=True) + "\n")
    report = {
        "schema": "EDGESNIFFER_2CQ_FROZEN_SCORER_NATIVE_WINDOWS_V1",
        "input_rows": len(rows), "exact_signal_events": len(signals), "exact_control_events": len(controls),
        "signal_windows": len(signal_windows), "control_windows": len(control_windows),
        "missing_or_rejected": counts, "observation_as_of_ts_ms": as_of,
        "source_sha256": sha256_file(input_path), "signals_sha256": sha256_file(signals_path),
        "controls_sha256": sha256_file(controls_path), "entry_delay_ms": ENTRY_DELAY_MS,
        "read_only": True, "trading_enabled": False, "policy_frozen": False,
        "promotion_blocked": True,
        "notes": [
            "Signals and controls are joined to the same native source tape.",
            "No first-seen or proxy reserve state is substituted.",
            "Empty marks are retained so the causal evaluator can report missed exits after maturity.",
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--controls", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--controls-output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(args.input, args.signals, args.controls, args.output, args.controls_output, args.report), sort_keys=True))


if __name__ == "__main__":
    main()
