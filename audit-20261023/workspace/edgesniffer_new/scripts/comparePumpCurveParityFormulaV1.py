#!/usr/bin/env python3
"""Compare Pump-curve parity timing with the original economics formula.

This is read-only.  It deliberately keeps the original quote functions,
fee inputs, notional, network fee, and post-state exit valuation.  Only event
ordering and the entry-state timing are varied, so the result can identify
whether a PnL change came from formula changes or timing/order changes.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import exactCurveEconomicsV1 as curve
from auditPumpCurveNativeParityV2 import (
    anchor_pre_state,
    curve_state,
    load_source_orders,
    load_windows,
    marks_for,
    order_key,
    state_after,
    transaction_index,
)

LAMPORTS_PER_SOL = 1_000_000_000
NOTIONAL_SOL = 0.5
STRESS_BPS = 50
ENTRY_DELAY_SECONDS = 1
EXIT_HORIZON_SECONDS = 5
NETWORK_FEE_SOL = 0.00001


def fees(event: dict[str, Any]) -> curve.Fees:
    return curve.Fees(
        protocol_bps=int(event.get("fee_basis_points") or 0),
        creator_bps=int(event.get("creator_fee_basis_points") or 0),
        lp_bps=STRESS_BPS,
    )


def pnl(entry_state: curve.CurveState, entry_event: dict[str, Any], mark: dict[str, Any] | None) -> tuple[str, float | None]:
    try:
        buy = curve.quote_exact_quote_in_buy(entry_state, int(NOTIONAL_SOL * LAMPORTS_PER_SOL), fees(entry_event))
    except Exception as error:
        return f"FAILED_ENTRY:{type(error).__name__}", None
    if mark is None:
        return "MISSED_EXIT_MARK", None
    try:
        # This is intentionally the original evaluator's exit convention:
        # quote from the selected mark's post_state.
        exit_state = curve_state(mark["event"].get("post_state") or {})
        sell = curve.quote_exact_token_sell(exit_state, buy.token_out, 0, fees(mark["event"]))
        return "ACTIONABLE", sell.immediate_quote_out / LAMPORTS_PER_SOL - NOTIONAL_SOL - NETWORK_FEE_SOL
    except Exception as error:
        return f"FAILED_EXIT:{type(error).__name__}", None


def summarize(values: list[float]) -> dict[str, Any]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_loss = abs(sum(losses))
    return {
        "actionable": len(values),
        "pnl_sol": sum(values),
        "win_rate": len(wins) / len(values) if values else None,
        "profit_factor": sum(wins) / gross_loss if gross_loss else (math.inf if wins else None),
    }


def evaluate(windows_path: Path, source_path: Path) -> dict[str, Any]:
    anchors, markets = load_windows(windows_path)
    source_orders = load_source_orders(source_path)
    historical_values: list[float] = []
    native_order_values: list[float] = []
    delayed_values: list[float] = []
    strict_values: list[float] = []
    rows: list[dict[str, Any]] = []

    for anchor_id, window in anchors.items():
        if window.get("route") != "PUMP_BONDING_CURVE_TOP_LEVEL":
            continue
        event = window.get("anchor_event") or {}
        if str(window.get("quote_mint") or event.get("quote_mint") or "") not in curve.SOL_QUOTE_MINTS:
            continue
        marks_native = marks_for(window, markets)
        # Recreate the earlier signature-text order for the historical-formula
        # reference; the native list is ordered by slot/transactionIndex.
        marks_historical = sorted(marks_native, key=lambda row: (row["block_time"], row["slot"], str(row["signature"]), row["event_index"]))
        anchor_time = int(window.get("anchor_block_time") or 0)
        anchor_slot = int(window.get("anchor_slot") or 0)
        anchor_signature = str(window.get("anchor_signature") or "")
        order = source_orders.get(anchor_signature)
        anchor_index = order[1] if order and order[0] == anchor_slot else None
        if anchor_index is None:
            after_native = [row for row in marks_native if row["slot"] > anchor_slot]
        else:
            after_native = [row for row in marks_native if order_key(row) > (anchor_slot, anchor_index, anchor_signature, -1)]
        after_historical = [row for row in marks_historical if row["slot"] > anchor_slot]
        target_entry = anchor_time + ENTRY_DELAY_SECONDS
        target_exit = target_entry + EXIT_HORIZON_SECONDS
        entry_boundary = [row for row in after_native if row["block_time"] == target_entry]
        before_entry = [row for row in after_native if row["block_time"] < target_entry]
        delayed_state = state_after(curve_state(event.get("post_state") or {}), before_entry)
        anchor_state = anchor_pre_state(event)
        historical_mark = next((row for row in after_historical if row["block_time"] >= target_exit), None)
        native_mark = next((row for row in after_native if row["block_time"] >= target_exit), None)
        historical_status, historical_pnl = pnl(anchor_state, event, historical_mark)
        native_status, native_pnl = pnl(anchor_state, event, native_mark)
        delayed_status, delayed_pnl = pnl(delayed_state, event, native_mark)
        if historical_pnl is not None:
            historical_values.append(historical_pnl)
        if native_pnl is not None:
            native_order_values.append(native_pnl)
        if delayed_pnl is not None:
            delayed_values.append(delayed_pnl)
        if not entry_boundary and delayed_pnl is not None:
            strict_values.append(delayed_pnl)
        rows.append({
            "anchor_id": anchor_id,
            "anchor_signature": anchor_signature,
            "entry_boundary_events": len(entry_boundary),
            "historical_formula_pnl_sol": historical_pnl,
            "native_order_same_formula_pnl_sol": native_pnl,
            "native_order_delayed_entry_same_formula_pnl_sol": delayed_pnl,
            "historical_status": historical_status,
            "native_status": native_status,
            "delayed_status": delayed_status,
        })

    return {
        "schema": "PUMP_CURVE_PARITY_FORMULA_COMPARISON_V1",
        "read_only": True,
        "policy_unchanged": True,
        "trading_enabled": False,
        "notional_sol": NOTIONAL_SOL,
        "stress_bps": STRESS_BPS,
        "network_fee_sol": NETWORK_FEE_SOL,
        "entry_delay_seconds": ENTRY_DELAY_SECONDS,
        "exit_horizon_seconds": EXIT_HORIZON_SECONDS,
        "windows_loaded": len(rows),
        "historical_formula_signature_order": summarize(historical_values),
        "native_order_same_formula": summarize(native_order_values),
        "native_order_delayed_entry_same_formula": summarize(delayed_values),
        "strict_no_entry_boundary_delayed_entry_same_formula": summarize(strict_values),
        "interpretation": "The four summaries retain the original quote/fee/post-state exit formula. Differences are caused by ordering and entry-state timing, not by switching to a pre-boundary exit formula.",
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.windows, args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in result if key.endswith("formula") or "same_formula" in key or key == "windows_loaded"}, sort_keys=True))


if __name__ == "__main__":
    main()
