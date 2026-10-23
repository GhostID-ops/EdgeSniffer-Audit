#!/usr/bin/env python3
"""Discovery-only Pump-curve strategy lab with delayed entry state.

The original positive evaluator moved the exit clock by one second but still
quoted the buy from the wallet anchor's pre-buy state.  This lab keeps the
same exact curve/fee/selected-mark formula while quoting the buy from the
state available at the delayed entry boundary.  It is hypothesis generation
only: it cannot freeze or promote a policy and it never submits a transaction.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
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
)

LAMPORTS_PER_SOL = 1_000_000_000
NETWORK_FEE_SOL = 0.00001
ENTRY_DELAY_SECONDS = 1
STRESS_BPS = 50
NOTIONALS_SOL = (0.1, 0.5, 1.0)
HORIZONS_SECONDS = (5, 10, 15, 30, 60)

FILTERS: tuple[dict[str, Any], ...] = (
    {"id": "ALL_DELAYED", "min_net_flow_sol": None, "min_price_change_bps": None},
    {"id": "POSITIVE_FLOW", "min_net_flow_sol": 0.0, "min_price_change_bps": None},
    {"id": "NONNEGATIVE_PRICE_CONTINUATION", "min_net_flow_sol": None, "min_price_change_bps": 0.0},
    {"id": "FLOW_AND_PRICE_CONTINUATION", "min_net_flow_sol": 0.0, "min_price_change_bps": 0.0},
    {"id": "FLOW_005_AND_PRICE_10BPS", "min_net_flow_sol": 0.05, "min_price_change_bps": 10.0},
)

EXIT_RULES: tuple[dict[str, Any], ...] = tuple(
    [{"id": f"FIXED_{h}S", "kind": "FIXED", "horizon_seconds": h} for h in HORIZONS_SECONDS]
    + [
        {"id": f"TP_SL_5_5_{h}S", "kind": "TP_SL", "horizon_seconds": h, "take_profit_pct": 0.05, "stop_loss_pct": 0.05}
        for h in (15, 30, 60)
    ]
    + [
        {"id": f"TRAIL_5_3_{h}S", "kind": "TRAILING", "horizon_seconds": h, "activation_pct": 0.05, "trail_pct": 0.03}
        for h in (30, 60)
    ]
    + [
        {"id": f"PARTIAL_5_50_{h}S", "kind": "PARTIAL_TP", "horizon_seconds": h, "take_profit_pct": 0.05, "partial_fraction": 0.5}
        for h in (30, 60)
    ]
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def price(state: curve.CurveState) -> float | None:
    if state.virtual_sol <= 0 or state.virtual_token <= 0:
        return None
    return state.virtual_sol / state.virtual_token


def fee_for(event: dict[str, Any]) -> curve.Fees:
    return curve.Fees(
        protocol_bps=int(event.get("fee_basis_points") or 0),
        creator_bps=int(event.get("creator_fee_basis_points") or 0),
        lp_bps=STRESS_BPS,
    )


def quote_buy(state: curve.CurveState, event: dict[str, Any], notional_sol: float) -> curve.BuyQuote | None:
    try:
        return curve.quote_exact_quote_in_buy(state, int(notional_sol * LAMPORTS_PER_SOL), fee_for(event))
    except Exception:
        return None


def quote_sell(tokens: int, mark: dict[str, Any], notional_sol: float) -> float | None:
    try:
        state = curve_state(mark["event"].get("post_state") or {})
        sell = curve.quote_exact_token_sell(state, tokens, 0, fee_for(mark["event"]))
        return sell.immediate_quote_out / LAMPORTS_PER_SOL - notional_sol - NETWORK_FEE_SOL
    except Exception:
        return None


def pnl_pct(value: float, notional_sol: float) -> float:
    return value / notional_sol if notional_sol else -1.0


def exit_simulation(marks: list[dict[str, Any]], tokens: int, notional_sol: float, rule: dict[str, Any], entry_time: int) -> dict[str, Any]:
    horizon = int(rule["horizon_seconds"])
    deadline = entry_time + horizon
    path = [mark for mark in marks if mark.get("block_time") is not None and entry_time <= int(mark["block_time"]) <= deadline]
    path_values: list[tuple[dict[str, Any], float]] = []
    for mark in path:
        value = quote_sell(tokens, mark, notional_sol)
        if value is not None:
            path_values.append((mark, value))
    mfe = max((value for _, value in path_values), default=None)
    mae = min((value for _, value in path_values), default=None)
    selected: dict[str, Any] | None = None
    reason = ""
    kind = rule["kind"]
    if kind == "FIXED":
        selected = next((mark for mark in marks if mark.get("block_time") is not None and int(mark["block_time"]) >= deadline), None)
        reason = "FIXED_HORIZON"
    elif kind in {"TP_SL", "TRAILING"}:
        high_pct: float | None = None
        for mark, value in path_values:
            current_pct = pnl_pct(value, notional_sol)
            high_pct = current_pct if high_pct is None else max(high_pct, current_pct)
            if kind == "TP_SL" and current_pct >= float(rule["take_profit_pct"]):
                selected, reason = mark, "TAKE_PROFIT"
                break
            if kind == "TP_SL" and current_pct <= -float(rule["stop_loss_pct"]):
                selected, reason = mark, "STOP_LOSS"
                break
            if kind == "TRAILING" and high_pct >= float(rule["activation_pct"]) and current_pct <= high_pct - float(rule["trail_pct"]):
                selected, reason = mark, "TRAILING_STOP"
                break
        if selected is None:
            selected = next((mark for mark in marks if mark.get("block_time") is not None and int(mark["block_time"]) >= deadline), None)
            reason = "HORIZON_FALLBACK"
    elif kind == "PARTIAL_TP":
        trigger = next(((mark, value) for mark, value in path_values if pnl_pct(value, notional_sol) >= float(rule["take_profit_pct"])), None)
        final = next((mark for mark in marks if mark.get("block_time") is not None and int(mark["block_time"]) >= deadline), None)
        if trigger and final:
            fraction = float(rule["partial_fraction"])
            partial_tokens = int(tokens * fraction)
            remaining_tokens = tokens - partial_tokens
            partial_value = quote_sell(partial_tokens, trigger[0], notional_sol * fraction)
            final_value = quote_sell(remaining_tokens, final, notional_sol * (1.0 - fraction))
            if partial_value is not None and final_value is not None:
                return {
                    "status": "ACTIONABLE",
                    "pnl_sol": partial_value + final_value,
                    "exit_reason": "PARTIAL_TAKE_PROFIT",
                    "mfe_sol": mfe,
                    "mae_sol": mae,
                }
        selected, reason = final, "HORIZON_FALLBACK"
    if selected is None:
        return {"status": "MISSED_EXIT_MARK", "pnl_sol": None, "exit_reason": reason, "mfe_sol": mfe, "mae_sol": mae}
    value = quote_sell(tokens, selected, notional_sol)
    if value is None:
        return {"status": "FAILED_EXIT_QUOTE", "pnl_sol": None, "exit_reason": reason, "mfe_sol": mfe, "mae_sol": mae}
    return {"status": "ACTIONABLE", "pnl_sol": value, "exit_reason": reason, "mfe_sol": mfe, "mae_sol": mae}


def filter_passes(filter_spec: dict[str, Any], net_flow_sol: float, price_change_bps: float | None) -> bool:
    minimum_flow = filter_spec.get("min_net_flow_sol")
    minimum_price = filter_spec.get("min_price_change_bps")
    if minimum_flow is not None and net_flow_sol < float(minimum_flow):
        return False
    if minimum_price is not None and (price_change_bps is None or price_change_bps < float(minimum_price)):
        return False
    return True


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actionable = [row for row in rows if row.get("status") == "ACTIONABLE" and row.get("pnl_sol") is not None]
    values = [float(row["pnl_sol"]) for row in actionable]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_loss = abs(sum(losses))
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    mfe = [float(row["mfe_sol"]) for row in rows if row.get("mfe_sol") is not None]
    mae = [float(row["mae_sol"]) for row in rows if row.get("mae_sol") is not None]
    ambiguous = [row for row in rows if row.get("delayed_boundary_ambiguous")]
    actionable_ambiguous = [row for row in actionable if row.get("delayed_boundary_ambiguous")]
    return {
        "attempted": len(rows),
        "actionable": len(values),
        "missed_or_failed": len(rows) - len(values),
        "delayed_entry_boundary_ambiguous": len(ambiguous),
        "actionable_boundary_ambiguous": len(actionable_ambiguous),
        "actionable_boundary_unambiguous": len(values) - len(actionable_ambiguous),
        "wins": len(wins),
        "win_rate": len(wins) / len(values) if values else None,
        "pnl_sol": sum(values),
        "roi_on_actionable": sum(values) / (float(rows[0]["notional_sol"]) * len(values)) if values else None,
        "profit_factor": sum(wins) / gross_loss if gross_loss else None,
        "max_drawdown_sol": drawdown,
        "mean_mfe_sol": sum(mfe) / len(mfe) if mfe else None,
        "mean_mae_sol": sum(mae) / len(mae) if mae else None,
    }


def evaluate(windows_path: Path, source_path: Path, wallet: str) -> dict[str, Any]:
    anchors, markets = load_windows(windows_path)
    source_orders = load_source_orders(source_path)
    prepared: list[dict[str, Any]] = []
    for anchor_id, window in anchors.items():
        if window.get("route") != "PUMP_BONDING_CURVE_TOP_LEVEL":
            continue
        event = window.get("anchor_event") or {}
        if str(window.get("quote_mint") or event.get("quote_mint") or "") not in curve.SOL_QUOTE_MINTS:
            continue
        marks = marks_for(window, markets)
        anchor_time = int(window.get("anchor_block_time") or 0)
        anchor_slot = int(window.get("anchor_slot") or 0)
        anchor_signature = str(window.get("anchor_signature") or anchor_id.split(":", 1)[0])
        order = source_orders.get(anchor_signature)
        if order and order[0] == anchor_slot and order[1] is not None:
            after_anchor = [row for row in marks if order_key(row) > (anchor_slot, order[1], anchor_signature, -1)]
        else:
            after_anchor = [row for row in marks if row["slot"] > anchor_slot]
        anchor_post = curve_state(event.get("post_state") or {})
        entry_target = anchor_time + ENTRY_DELAY_SECONDS
        before_entry = [row for row in after_anchor if int(row.get("block_time") or 0) < entry_target]
        boundary_entry = [row for row in after_anchor if int(row.get("block_time") or 0) == entry_target]
        delayed_state = state_after(anchor_post, before_entry)
        anchor_price = price(anchor_post)
        delayed_price = price(delayed_state)
        price_change_bps = ((delayed_price / anchor_price) - 1.0) * 10_000 if anchor_price and delayed_price else None
        net_flow_sol = sum((1.0 if bool(row["event"].get("is_buy")) else -1.0) * int(row["event"].get("sol_amount") or 0) / LAMPORTS_PER_SOL for row in before_entry)
        prepared.append({
            "anchor_id": anchor_id,
            "anchor_signature": anchor_signature,
            "anchor_block_time": anchor_time,
            "anchor_event": event,
            "delayed_state": delayed_state,
            "delayed_boundary_ambiguous": bool(boundary_entry),
            "net_flow_sol": net_flow_sol,
            "price_change_bps": price_change_bps,
            "marks": after_anchor,
        })

    outcomes: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in prepared:
        for filter_spec in FILTERS:
            if not filter_passes(filter_spec, entry["net_flow_sol"], entry["price_change_bps"]):
                continue
            for notional in NOTIONALS_SOL:
                buy = quote_buy(entry["delayed_state"], entry["anchor_event"], notional)
                for rule in EXIT_RULES:
                    strategy_id = "|".join([filter_spec["id"], rule["id"], str(notional), str(STRESS_BPS)])
                    base = {
                        "strategy_id": strategy_id,
                        "entry_filter": filter_spec["id"],
                        "exit_rule": rule["id"],
                        "notional_sol": notional,
                        "stress_bps": STRESS_BPS,
                        "anchor_id": entry["anchor_id"],
                        "anchor_signature": entry["anchor_signature"],
                        "delayed_boundary_ambiguous": entry["delayed_boundary_ambiguous"],
                        "net_flow_sol": entry["net_flow_sol"],
                        "price_change_bps": entry["price_change_bps"],
                    }
                    if buy is None:
                        row = base | {"status": "FAILED_ENTRY", "pnl_sol": None, "mfe_sol": None, "mae_sol": None}
                    else:
                        result = exit_simulation(entry["marks"], buy.token_out, notional, rule, entry["anchor_block_time"] + ENTRY_DELAY_SECONDS)
                        row = base | result
                    outcomes.append(row)
                    grouped.setdefault(strategy_id, []).append(row)

    surface = []
    for strategy_id, rows in grouped.items():
        stat = metrics(rows)
        surface.append({"strategy_id": strategy_id, "metrics": stat})
    surface.sort(key=lambda row: ((row["metrics"].get("pnl_sol") or 0.0) * -1, -(row["metrics"].get("profit_factor") or -1.0)))
    return {
        "schema": "PUMP_CURVE_DELAYED_ENTRY_STRATEGY_LAB_V1",
        "wallet": wallet,
        "read_only": True,
        "trading_enabled": False,
        "policy_frozen": False,
        "selection_performed": False,
        "promotion_blocked": True,
        "entry_delay_seconds": ENTRY_DELAY_SECONDS,
        "entry_state_model": "state_after_native_ordered_events_before_anchor_block_time_plus_one_second",
        "exit_state_model": "selected_mark_post_state_same_as_original_evaluator",
        "notional_sol": list(NOTIONALS_SOL),
        "stress_bps": STRESS_BPS,
        "network_fee_sol_round_trip": NETWORK_FEE_SOL,
        "horizons_seconds": list(HORIZONS_SECONDS),
        "filters": list(FILTERS),
        "exit_rules": list(EXIT_RULES),
        "anchors_loaded": len(anchors),
        "prepared_anchor_windows": len(prepared),
        "outcomes": outcomes,
        "surface": surface,
        "source_sha256": sha256_file(source_path),
        "windows_sha256": sha256_file(windows_path),
        "notes": [
            "Discovery-only hypothesis ranking; no policy was selected or frozen.",
            "Entry uses delayed reserve state but block-time boundary ambiguity remains until native receive telemetry exists.",
            "All exit rows use the original selected-mark post_state formula for comparability.",
            "A positive surface row is not validation; it must be pre-registered and tested on a fresh prospective cohort.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.windows, args.source, args.wallet)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    top = [{"strategy_id": row["strategy_id"], "metrics": row["metrics"]} for row in result["surface"][:15]]
    print(json.dumps({"prepared_anchor_windows": result["prepared_anchor_windows"], "surface_rows": len(result["surface"]), "top": top, "policy_frozen": False, "promotion_blocked": True}, sort_keys=True))


if __name__ == "__main__":
    main()
