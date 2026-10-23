#!/usr/bin/env python3
"""Audit Pump-curve economics against the causal tape without changing policy.

This audit isolates two execution-parity questions:
1. Does the reserve state change between the observed wallet buy and the
   one-second delayed entry?
2. How much does the current anchor-state quote differ from a conservative
   tape-derived pre-entry state quote?

It does not claim receive-clock or transaction-inclusion parity. It is a
read-only diagnostic for the existing prospective cohort.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
from pathlib import Path
from typing import Any

import exactCurveEconomicsV1 as curve
from materializeWalletRouteCensusV1 import curve_events_with_extension

LAMPORTS_PER_SOL = 1_000_000_000
NOTIONAL_SOL = 0.5
STRESS_BPS = 50
DELAY_SECONDS = 1
EXIT_SECONDS = 5


def load(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    anchors: dict[str, dict[str, Any]] = {}
    markets: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return anchors, markets
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("record_type") == "ANCHOR_WINDOW":
            anchors[str(row.get("anchor_id"))] = row
        elif row.get("record_type") == "MARKET_TRANSACTION":
            markets[str(row.get("signature"))] = row
    return anchors, markets


def state(value: dict[str, Any]) -> curve.CurveState:
    return curve.CurveState(
        virtual_sol=int(value.get("virtual_sol") or 0),
        virtual_token=int(value.get("virtual_token") or 0),
        real_sol=int(value.get("real_sol") or 0),
        real_token=int(value.get("real_token") or 0),
    )


def pre_state(event: dict[str, Any]) -> curve.CurveState:
    post = state(event.get("post_state") or {})
    amount = int(event.get("sol_amount") or 0)
    return curve.CurveState(
        virtual_sol=post.virtual_sol - amount,
        virtual_token=post.virtual_token + int(event.get("token_amount") or 0),
        real_sol=post.real_sol - amount,
        real_token=post.real_token + int(event.get("token_amount") or 0),
    )


def fees(event: dict[str, Any]) -> curve.Fees:
    return curve.Fees(
        protocol_bps=int(event.get("fee_basis_points") or 0),
        creator_bps=int(event.get("creator_fee_basis_points") or 0),
        lp_bps=STRESS_BPS,
    )


def events_for(window: dict[str, Any], markets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    mint = str(window.get("mint") or "")
    quote = str(window.get("quote_mint") or "")
    for signature in window.get("window_signatures") or []:
        row = markets.get(str(signature))
        if not row:
            continue
        try:
            events, _variant = curve_events_with_extension(row.get("raw_transaction") or {})
        except Exception:
            continue
        for index, event in enumerate(events):
            if str(event.mint) != mint or str(event.quote_mint) != quote:
                continue
            result.append({
                "signature": row.get("signature"),
                "slot": int(row.get("slot") or 0),
                "block_time": int(row.get("block_time") or event.timestamp or 0),
                "event_index": index,
                "event": dataclasses.asdict(event),
            })
    result.sort(key=lambda row: (row["block_time"], row["slot"], str(row["signature"]), row["event_index"]))
    return result


def pnl(entry_state: curve.CurveState, entry_event: dict[str, Any], marks: list[dict[str, Any]], anchor_time: int) -> tuple[str, float | None, int | None]:
    try:
        buy = curve.quote_exact_quote_in_buy(entry_state, int(NOTIONAL_SOL * LAMPORTS_PER_SOL), fees(entry_event))
    except Exception as error:
        return f"FAILED_ENTRY:{type(error).__name__}:{error}", None, None
    target = anchor_time + DELAY_SECONDS + EXIT_SECONDS
    mark = next((row for row in marks if row["block_time"] >= target), None)
    if not mark:
        return "MISSED_EXIT_MARK", None, buy.token_out
    try:
        sell = curve.quote_exact_token_sell(state(mark["event"].get("post_state") or {}), buy.token_out, 0, fees(mark["event"]))
        value = sell.immediate_quote_out / LAMPORTS_PER_SOL - NOTIONAL_SOL - 0.00001
        return "ACTIONABLE", value, buy.token_out
    except Exception as error:
        return f"FAILED_EXIT:{type(error).__name__}:{error}", None, buy.token_out


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
        anchor_event = window.get("anchor_event") or {}
        anchor_time = int(window.get("anchor_block_time") or 0)
        anchor_slot = int(window.get("anchor_slot") or 0)
        anchor_sig = str(window.get("anchor_signature") or "")
        marks = events_for(window, markets)
        safe_intervening = [
            row for row in marks
            if row["signature"] != anchor_sig and row["slot"] > anchor_slot and row["block_time"] == anchor_time
        ]
        ambiguous_target_second = [
            row for row in marks
            if row["signature"] != anchor_sig and row["slot"] > anchor_slot and row["block_time"] == anchor_time + DELAY_SECONDS
        ]
        current_state = pre_state(anchor_event)
        delayed_state = state(safe_intervening[-1]["event"].get("post_state") or {}) if safe_intervening else current_state
        current_status, current_pnl, current_tokens = pnl(current_state, anchor_event, marks, anchor_time)
        delayed_status, delayed_pnl, delayed_tokens = pnl(delayed_state, anchor_event, marks, anchor_time)
        rows.append({
            "anchor_id": anchor_id,
            "anchor_signature": anchor_sig,
            "anchor_block_time": anchor_time,
            "safe_intervening_events_before_entry": len(safe_intervening),
            "ambiguous_events_in_entry_second": len(ambiguous_target_second),
            "current_model_status": current_status,
            "delayed_state_proxy_status": delayed_status,
            "current_model_pnl_sol": current_pnl,
            "delayed_state_proxy_pnl_sol": delayed_pnl,
            "pnl_delta_delayed_minus_current_sol": (delayed_pnl - current_pnl) if delayed_pnl is not None and current_pnl is not None else None,
            "current_tokens": current_tokens,
            "delayed_state_proxy_tokens": delayed_tokens,
        })
    current = [row["current_model_pnl_sol"] for row in rows if row["current_model_pnl_sol"] is not None]
    delayed = [row["delayed_state_proxy_pnl_sol"] for row in rows if row["delayed_state_proxy_pnl_sol"] is not None]
    deltas = [row["pnl_delta_delayed_minus_current_sol"] for row in rows if row["pnl_delta_delayed_minus_current_sol"] is not None]
    result = {
        "schema": "PUMP_CURVE_LIVE_PARITY_AUDIT_V1",
        "read_only": True,
        "policy_unchanged": True,
        "trading_enabled": False,
        "notional_sol": NOTIONAL_SOL,
        "stress_bps": STRESS_BPS,
        "entry_delay_seconds": DELAY_SECONDS,
        "exit_horizon_seconds": EXIT_SECONDS,
        "windows_loaded": len(rows),
        "current_model_actionable": len(current),
        "delayed_state_proxy_actionable": len(delayed),
        "anchors_with_safe_same_second_state_evidence": sum(bool(row["safe_intervening_events_before_entry"]) for row in rows),
        "anchors_with_entry_second_timing_ambiguity": sum(bool(row["ambiguous_events_in_entry_second"]) for row in rows),
        "current_model_pnl_sol": sum(current),
        "delayed_state_proxy_pnl_sol": sum(delayed),
        "delayed_minus_current_pnl_sol": sum(deltas),
        "mean_delayed_minus_current_pnl_sol": sum(deltas) / len(deltas) if deltas else None,
        "live_parity_status": "BLOCKED_UNTIL_DELAYED_ENTRY_STATE_AND_EXIT_INCLUSION_ARE_MODELED",
        "known_limitations": [
            "Finalized block_time has one-second granularity and cannot prove receive-clock ordering inside a second.",
            "The current evaluator quotes entry from the anchor pre-state; this audit supplies only a conservative tape-derived delayed-state proxy.",
            "The current evaluator marks an exit at the first finalized event at or after the target and assumes immediate sell inclusion.",
            "Actual priority fees, transaction inclusion failures, RPC latency, and submitted-transaction slippage are not observed in receive-only mode.",
        ],
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ["status", "windows_loaded", "current_model_actionable", "delayed_state_proxy_actionable", "anchors_with_safe_same_second_state_evidence", "anchors_with_entry_second_timing_ambiguity", "current_model_pnl_sol", "delayed_state_proxy_pnl_sol", "delayed_minus_current_pnl_sol", "live_parity_status"]}, sort_keys=True) if False else json.dumps({k: result[k] for k in ["windows_loaded", "current_model_actionable", "delayed_state_proxy_actionable", "anchors_with_safe_same_second_state_evidence", "anchors_with_entry_second_timing_ambiguity", "current_model_pnl_sol", "delayed_state_proxy_pnl_sol", "delayed_minus_current_pnl_sol", "live_parity_status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
