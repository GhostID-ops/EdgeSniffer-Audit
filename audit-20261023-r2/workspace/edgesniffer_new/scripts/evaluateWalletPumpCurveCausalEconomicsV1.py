#!/usr/bin/env python3
"""Causal, venue-native Pump bonding-curve economics surface.

This evaluator consumes only Pump-curve window records.  It uses the exact
integer curve evaluator, a one-second entry delay, realized fee fields from
the anchor/mark events, explicit missed exits, and separate SOL notionals.
It never imports PumpSwap, Raydium, or Meteora pricing logic.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import exactCurveEconomicsV1 as curve
from materializeWalletRouteCensusV1 import curve_events_with_extension

LAMPORTS_PER_SOL = 1_000_000_000
HORIZONS = (5, 10, 15, 30, 60, 120, 300)
NOTIONALS = (0.1, 0.5, 1.0)
STRESSES_BPS = (0, 25, 50)
NETWORK_FEE_SOL = 0.000005


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_windows(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    anchors: dict[str, dict[str, Any]] = {}
    markets: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("record_type") == "ANCHOR_WINDOW":
                anchors[str(row.get("anchor_id"))] = row
            elif row.get("record_type") == "MARKET_TRANSACTION":
                markets[str(row.get("signature"))] = row
    return anchors, markets


def curve_state(value: dict[str, Any]) -> curve.CurveState:
    return curve.CurveState(
        virtual_sol=int(value.get("virtual_sol") or 0),
        virtual_token=int(value.get("virtual_token") or 0),
        real_sol=int(value.get("real_sol") or 0),
        real_token=int(value.get("real_token") or 0),
    )


def anchor_pre_state(event: dict[str, Any]) -> curve.CurveState:
    post = curve_state(event.get("post_state") or {})
    return curve.CurveState(
        virtual_sol=post.virtual_sol - int(event.get("sol_amount") or 0),
        virtual_token=post.virtual_token + int(event.get("token_amount") or 0),
        real_sol=post.real_sol - int(event.get("sol_amount") or 0),
        real_token=post.real_token + int(event.get("token_amount") or 0),
    )


def fees(event: dict[str, Any], stress_bps: int) -> curve.Fees:
    return curve.Fees(
        protocol_bps=int(event.get("fee_basis_points") or 0),
        creator_bps=int(event.get("creator_fee_basis_points") or 0),
        lp_bps=int(stress_bps),
    )


def market_marks(window: dict[str, Any], markets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    mint = str(window.get("mint") or "")
    quote_mint = str(window.get("quote_mint") or "")
    marks: list[dict[str, Any]] = []
    for signature in window.get("window_signatures") or []:
        row = markets.get(str(signature))
        if not row:
            continue
        try:
            events, _variant = curve_events_with_extension(row.get("raw_transaction") or {})
        except Exception:
            continue
        for event_index, event in enumerate(events):
            if event.mint != mint or event.quote_mint != quote_mint:
                continue
            marks.append({
                "signature": row.get("signature"),
                "event_index": event_index,
                "block_time": int(row.get("block_time") or event.timestamp or 0),
                "event": event,
            })
    marks.sort(key=lambda row: (row["block_time"], str(row["signature"]), row["event_index"]))
    return marks


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actionable = [float(row["pnl_sol"]) for row in rows if row.get("status") == "ACTIONABLE"]
    wins = [value for value in actionable if value > 0]
    losses = [value for value in actionable if value < 0]
    equity = peak = drawdown = 0.0
    for value in actionable:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "attempted": len(rows),
        "actionable": len(actionable),
        "missed_or_failed": len(rows) - len(actionable),
        "wins": len(wins),
        "win_rate": len(wins) / len(actionable) if actionable else None,
        "pnl_sol": sum(actionable),
        "roi_on_actionable": sum(actionable) / sum(float(row["notional_sol"]) for row in rows if row.get("status") == "ACTIONABLE") if actionable else None,
        # Keep the report strict JSON.  An all-win sample has no finite
        # denominator; represent PF as null rather than emitting Infinity.
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "max_drawdown_sol": drawdown,
        "max_loss_sol": min(actionable) if actionable else None,
    }


def evaluate(windows_path: Path) -> dict[str, Any]:
    anchors, markets = load_windows(windows_path)
    outcomes: list[dict[str, Any]] = []
    anchor_status = collections.Counter()
    for anchor_id, window in anchors.items():
        event = window.get("anchor_event") or {}
        quote_mint = str(window.get("quote_mint") or event.get("quote_mint") or "")
        base = {
            "anchor_id": anchor_id,
            "anchor_signature": window.get("anchor_signature"),
            "route": window.get("route"),
            "mint": window.get("mint"),
            "quote_mint": quote_mint,
        }
        if quote_mint not in curve.SOL_QUOTE_MINTS:
            anchor_status["NON_SOL_QUOTE"] += 1
            for notional in NOTIONALS:
                for stress in STRESSES_BPS:
                    for horizon in HORIZONS:
                        outcomes.append(base | {"notional_sol": notional, "stress_bps": stress, "horizon_seconds": horizon, "entry_delay_seconds": 1, "status": "NON_SOL_QUOTE", "pnl_sol": None})
            continue
        try:
            pre = anchor_pre_state(event)
            marks = market_marks(window, markets)
        except Exception as error:
            anchor_status["INVALID_ANCHOR"] += 1
            for notional in NOTIONALS:
                for stress in STRESSES_BPS:
                    for horizon in HORIZONS:
                        outcomes.append(base | {"notional_sol": notional, "stress_bps": stress, "horizon_seconds": horizon, "entry_delay_seconds": 1, "status": "FAILED_ENTRY", "reason": type(error).__name__ + ":" + str(error), "pnl_sol": None})
            continue
        anchor_time = int(window.get("anchor_block_time") or 0)
        for notional in NOTIONALS:
            for stress in STRESSES_BPS:
                try:
                    entry = curve.quote_exact_quote_in_buy(pre, int(notional * LAMPORTS_PER_SOL), fees(event, stress))
                except Exception as error:
                    for horizon in HORIZONS:
                        outcomes.append(base | {"notional_sol": notional, "stress_bps": stress, "horizon_seconds": horizon, "entry_delay_seconds": 1, "status": "FAILED_ENTRY", "reason": type(error).__name__ + ":" + str(error), "pnl_sol": None})
                    continue
                for horizon in HORIZONS:
                    target = anchor_time + 1 + horizon
                    mark = next((row for row in marks if row["block_time"] >= target), None)
                    item = base | {"notional_sol": notional, "stress_bps": stress, "horizon_seconds": horizon, "entry_delay_seconds": 1, "tokens": entry.token_out}
                    if not mark:
                        anchor_status["MISSED_EXIT_MARK"] += 1
                        outcomes.append(item | {"status": "MISSED_EXIT_MARK", "pnl_sol": None})
                        continue
                    mark_event = mark["event"]
                    try:
                        exit_quote = curve.quote_exact_token_sell(curve_state(dataclasses.asdict(mark_event.post_state)), entry.token_out, 0, fees(dataclasses.asdict(mark_event), stress))
                        pnl = exit_quote.immediate_quote_out / LAMPORTS_PER_SOL - notional - NETWORK_FEE_SOL
                        anchor_status["ACTIONABLE"] += 1
                        outcomes.append(item | {"status": "ACTIONABLE", "pnl_sol": pnl, "exit_signature": mark["signature"], "exit_event_index": mark["event_index"], "exit_block_time": mark["block_time"]})
                    except Exception as error:
                        anchor_status["FAILED_EXIT_QUOTE"] += 1
                        outcomes.append(item | {"status": "FAILED_EXIT_QUOTE", "reason": type(error).__name__ + ":" + str(error), "pnl_sol": None})
    surface: dict[str, Any] = {}
    for route in sorted({str(row.get("route")) for row in anchors.values()}):
        surface[route] = {}
        for notional in NOTIONALS:
            for stress in STRESSES_BPS:
                for horizon in HORIZONS:
                    key = f"{route}|{notional}|{stress}|{horizon}"
                    surface[key] = summarize([row for row in outcomes if row.get("route") == route and row.get("notional_sol") == notional and row.get("stress_bps") == stress and row.get("horizon_seconds") == horizon])
    return {
        "schema": "WALLET_PUMP_CURVE_CAUSAL_ECONOMICS_SURFACE_V1",
        "evaluator": "EXACT_CURVE_ECONOMICS_V1",
        "read_only": True,
        "outcome_source": "FINALIZED_MINT_KEYED_POST_ENTRY_WINDOWS",
        "entry_delay_seconds": 1,
        "timing_basis": "finalized block_time seconds; receive-clock parity not claimed",
        "notionals_sol": list(NOTIONALS),
        "horizons_seconds": list(HORIZONS),
        "stress_bps": list(STRESSES_BPS),
        "network_fee_sol_per_round_trip": NETWORK_FEE_SOL,
        "windows_loaded": len(anchors),
        "market_transactions_loaded": len(markets),
        "anchor_status_counts": dict(anchor_status),
        "outcomes": outcomes,
        "surface": surface,
        "controls": {"status": "NOT_ATTACHED", "promotion_eligible": False},
        "policy_frozen": False,
        "trading_enabled": False,
        "windows_sha256": sha256_file(windows_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.windows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PUMP_CURVE_CAUSAL_ECONOMICS_COMPLETE", "windows_loaded": result["windows_loaded"], "market_transactions_loaded": result["market_transactions_loaded"], "anchor_status_counts": result["anchor_status_counts"]}, sort_keys=True))


if __name__ == "__main__":
    main()
