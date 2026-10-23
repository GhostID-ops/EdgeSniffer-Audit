#!/usr/bin/env python3
"""Read-only Pump curve parity-v2 replay with timing and exit bounds.

Ambiguous finalized-second ordering is retained as an interval. No row is
silently dropped or assigned a false exact entry state. This is diagnostic;
it does not change the frozen policy or enable execution.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import exactCurveEconomicsV1 as curve
from auditPumpCurveLiveParityV1 import events_for, fees, load, pre_state, state

LAMPORTS_PER_SOL = 1_000_000_000
NOTIONAL_SOL = 0.5
STRESS_BPS = 50
ENTRY_DELAY_SECONDS = 1
EXIT_HORIZON_SECONDS = 5
EXIT_INCLUSION_BAND_SECONDS = 1


def quote(entry_state: curve.CurveState, entry_event: dict[str, Any], mark: dict[str, Any] | None) -> tuple[str, float | None, int | None]:
    try:
        buy = curve.quote_exact_quote_in_buy(entry_state, int(NOTIONAL_SOL * LAMPORTS_PER_SOL), fees(entry_event))
    except Exception as error:
        return f"FAILED_ENTRY:{type(error).__name__}:{error}", None, None
    if not mark:
        return "MISSED_EXIT_MARK", None, buy.token_out
    try:
        sell = curve.quote_exact_token_sell(state(mark["event"].get("post_state") or {}), buy.token_out, 0, fees(mark["event"]))
        pnl = sell.immediate_quote_out / LAMPORTS_PER_SOL - NOTIONAL_SOL - 0.00001
        return "ACTIONABLE", pnl, buy.token_out
    except Exception as error:
        return f"FAILED_EXIT:{type(error).__name__}:{error}", None, buy.token_out


def summary(values: list[float]) -> dict[str, Any]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_loss = abs(sum(losses))
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "actionable": len(values),
        "pnl_sol": sum(values),
        "profit_factor": sum(wins) / gross_loss if gross_loss else None,
        "win_rate": len(wins) / len(values) if values else None,
        "max_drawdown_sol": drawdown,
        "gross_loss_sol": gross_loss,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    anchors, markets = load(args.windows)
    rows: list[dict[str, Any]] = []
    for anchor_id, window in anchors.items():
        if window.get("route") != "PUMP_BONDING_CURVE_TOP_LEVEL":
            continue
        if str(window.get("quote_mint") or "") not in curve.SOL_QUOTE_MINTS:
            continue
        event = window.get("anchor_event") or {}
        anchor_time = int(window.get("anchor_block_time") or 0)
        anchor_slot = int(window.get("anchor_slot") or 0)
        anchor_signature = str(window.get("anchor_signature") or "")
        marks = events_for(window, markets)
        safe = [row for row in marks if row["signature"] != anchor_signature and row["slot"] > anchor_slot and row["block_time"] == anchor_time]
        target_second = [row for row in marks if row["signature"] != anchor_signature and row["slot"] > anchor_slot and row["block_time"] == anchor_time + ENTRY_DELAY_SECONDS]
        anchor_state = pre_state(event)
        safe_state = state(safe[-1]["event"].get("post_state") or {}) if safe else anchor_state
        target_state = state(target_second[-1]["event"].get("post_state") or {}) if target_second else safe_state
        target = anchor_time + ENTRY_DELAY_SECONDS + EXIT_HORIZON_SECONDS
        first_exit = next((row for row in marks if row["block_time"] >= target), None)
        delayed_exit = next((row for row in marks if row["block_time"] >= target + EXIT_INCLUSION_BAND_SECONDS), None)
        current_status, current_pnl, _ = quote(anchor_state, event, first_exit)
        safe_status, safe_pnl, _ = quote(safe_state, event, first_exit)
        bound_values = []
        for entry_state in (safe_state, target_state) if target_second else (safe_state,):
            for exit_mark in (first_exit, delayed_exit) if delayed_exit else (first_exit,):
                status, value, _ = quote(entry_state, event, exit_mark)
                if status == "ACTIONABLE" and value is not None:
                    bound_values.append(value)
        rows.append({
            "anchor_id": anchor_id,
            "timing_class": "AMBIGUOUS_TARGET_SECOND" if target_second else ("SAFE_SAME_SECOND_STATE" if safe else "NO_OBSERVED_STATE_CHANGE"),
            "safe_intervening_events": len(safe),
            "target_second_events": len(target_second),
            "current_model_status": current_status,
            "current_model_pnl_sol": current_pnl,
            "safe_delayed_state_status": safe_status,
            "safe_delayed_state_pnl_sol": safe_pnl,
            "bounded_pnl_low_sol": min(bound_values) if bound_values else None,
            "bounded_pnl_high_sol": max(bound_values) if bound_values else None,
            "first_exit_signature": first_exit["signature"] if first_exit else None,
            "delayed_exit_signature": delayed_exit["signature"] if delayed_exit else None,
        })
    current = [row["current_model_pnl_sol"] for row in rows if row["current_model_pnl_sol"] is not None]
    safe_values = [row["safe_delayed_state_pnl_sol"] for row in rows if row["safe_delayed_state_pnl_sol"] is not None]
    bounded_low = [row["bounded_pnl_low_sol"] for row in rows if row["bounded_pnl_low_sol"] is not None]
    bounded_high = [row["bounded_pnl_high_sol"] for row in rows if row["bounded_pnl_high_sol"] is not None]
    result = {
        "schema": "PUMP_CURVE_LIVE_PARITY_V2_BOUNDED_REPLAY_V1",
        "read_only": True,
        "policy_unchanged": True,
        "trading_enabled": False,
        "notional_sol": NOTIONAL_SOL,
        "stress_bps": STRESS_BPS,
        "entry_delay_seconds": ENTRY_DELAY_SECONDS,
        "exit_horizon_seconds": EXIT_HORIZON_SECONDS,
        "exit_inclusion_band_seconds": EXIT_INCLUSION_BAND_SECONDS,
        "windows_loaded": len(rows),
        "timing_classes": {
            "ambiguous_target_second": sum(row["timing_class"] == "AMBIGUOUS_TARGET_SECOND" for row in rows),
            "safe_same_second_state": sum(row["timing_class"] == "SAFE_SAME_SECOND_STATE" for row in rows),
            "no_observed_state_change": sum(row["timing_class"] == "NO_OBSERVED_STATE_CHANGE" for row in rows),
        },
        "current_model": summary(current),
        "safe_delayed_state_proxy": summary(safe_values),
        "bounded_low": summary(bounded_low),
        "bounded_high": summary(bounded_high),
        "interpretation": "Ambiguous finalized-second rows are retained as a PnL interval. The interval is not a proof of live execution; exact receive-clock and inclusion timing still require native ingestion telemetry.",
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ["windows_loaded", "timing_classes", "current_model", "safe_delayed_state_proxy", "bounded_low", "bounded_high"]}, sort_keys=True))


if __name__ == "__main__":
    main()
