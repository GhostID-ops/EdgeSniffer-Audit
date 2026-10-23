#!/usr/bin/env python3
"""Evaluate one preregistered Pump top-level policy on a fresh cohort."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import exactCurveEconomicsV1 as curve
from materializeWalletRouteCensusV1 import curve_events_with_extension

LAMPORTS_PER_SOL = 1_000_000_000
NOTIONAL_SOL = 0.5
STRESS_BPS = 50
HORIZON_SECONDS = 5
ENTRY_DELAY_SECONDS = 1
NETWORK_FEE_SOL = 0.00001
MIN_ACTIONABLE = 30


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_window_rows(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    anchors: dict[str, dict[str, Any]] = {}
    markets: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return anchors, markets
    with path.open(encoding="utf-8") as handle:
        for line in handle:
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


def load_controls(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    return list(value.get("records") or [])


def state(value: dict[str, Any]) -> curve.CurveState:
    return curve.CurveState(
        virtual_sol=int(value.get("virtual_sol") or 0),
        virtual_token=int(value.get("virtual_token") or 0),
        real_sol=int(value.get("real_sol") or 0),
        real_token=int(value.get("real_token") or 0),
    )


def pre_state(event: dict[str, Any]) -> curve.CurveState:
    post = state(event.get("post_state") or {})
    return curve.CurveState(
        virtual_sol=post.virtual_sol - int(event.get("sol_amount") or 0),
        virtual_token=post.virtual_token + int(event.get("token_amount") or 0),
        real_sol=post.real_sol - int(event.get("sol_amount") or 0),
        real_token=post.real_token + int(event.get("token_amount") or 0),
    )


def fees(event: dict[str, Any]) -> curve.Fees:
    return curve.Fees(
        protocol_bps=int(event.get("fee_basis_points") or 0),
        creator_bps=int(event.get("creator_fee_basis_points") or 0),
        lp_bps=STRESS_BPS,
    )


def marks(window: dict[str, Any], markets: dict[str, dict[str, Any]], mint: str, quote: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
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
                "event_index": index,
                "block_time": int(row.get("block_time") or event.timestamp or 0),
                "event": dataclasses.asdict(event),
            })
    result.sort(key=lambda item: (item["block_time"], str(item["signature"]), item["event_index"]))
    return result


def outcome(entry: dict[str, Any], window: dict[str, Any], markets: dict[str, dict[str, Any]], kind: str) -> dict[str, Any]:
    mint = str(window.get("mint") or "")
    quote = str(window.get("quote_mint") or entry.get("quote_mint") or "")
    base = {
        "kind": kind,
        "anchor_id": window.get("anchor_id"),
        "anchor_signature": window.get("anchor_signature"),
        "entry_signature": entry.get("signature") or entry.get("control_signature") or window.get("anchor_signature"),
        "entry_event_index": entry.get("event_index"),
        "mint": mint,
        "quote_mint": quote,
        "route": window.get("route"),
        "notional_sol": NOTIONAL_SOL,
        "stress_bps": STRESS_BPS,
        "entry_delay_seconds": ENTRY_DELAY_SECONDS,
        "horizon_seconds": HORIZON_SECONDS,
        "entry_block_time": int(entry.get("block_time") or entry.get("control_block_time") or window.get("anchor_block_time") or 0),
    }
    if quote not in curve.SOL_QUOTE_MINTS:
        return base | {"status": "NON_SOL_QUOTE", "pnl_sol": None}
    try:
        buy = curve.quote_exact_quote_in_buy(pre_state(entry), int(NOTIONAL_SOL * LAMPORTS_PER_SOL), fees(entry))
    except Exception as error:
        return base | {"status": "FAILED_ENTRY", "reason": f"{type(error).__name__}:{error}", "pnl_sol": None}
    entry_time = int(entry.get("block_time") or entry.get("control_block_time") or window.get("anchor_block_time") or 0)
    target = entry_time + ENTRY_DELAY_SECONDS + HORIZON_SECONDS
    mark = next((item for item in marks(window, markets, mint, quote) if item["block_time"] >= target), None)
    base["tokens"] = buy.token_out
    if not mark:
        return base | {"status": "MISSED_EXIT_MARK", "pnl_sol": None}
    try:
        mark_event = mark["event"]
        sell = curve.quote_exact_token_sell(state(mark_event.get("post_state") or {}), buy.token_out, 0, fees(mark_event))
        pnl = sell.immediate_quote_out / LAMPORTS_PER_SOL - NOTIONAL_SOL - NETWORK_FEE_SOL
        return base | {
            "status": "ACTIONABLE",
            "pnl_sol": pnl,
            "exit_signature": mark["signature"],
            "exit_event_index": mark["event_index"],
            "exit_block_time": mark["block_time"],
        }
    except Exception as error:
        return base | {"status": "FAILED_EXIT_QUOTE", "reason": f"{type(error).__name__}:{error}", "pnl_sol": None}


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actionable = [float(row["pnl_sol"]) for row in rows if row.get("status") == "ACTIONABLE"]
    wins = [value for value in actionable if value > 0]
    losses = [value for value in actionable if value < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    largest_loss = abs(min(losses)) if losses else 0.0
    equity = peak = drawdown = 0.0
    for value in actionable:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "attempted": len(rows),
        "actionable": len(actionable),
        "missed_or_failed": len(rows) - len(actionable),
        "wins": len(wins),
        "win_rate": len(wins) / len(actionable) if actionable else None,
        "pnl_sol": sum(actionable),
        "roi_on_actionable": sum(actionable) / (NOTIONAL_SOL * len(actionable)) if actionable else None,
        # Keep the validation artifact strict JSON.  An all-win sample has no
        # finite loss denominator, so represent PF as null rather than Infinity.
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "gross_win_sol": gross_win,
        "gross_loss_sol": gross_loss,
        "largest_loss_sol": largest_loss if losses else None,
        "largest_loss_share_of_gross_loss": largest_loss / gross_loss if gross_loss else None,
        "max_drawdown_sol": drawdown,
        "max_loss_sol": min(actionable) if actionable else None,
    }


def chronological_halves(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actionable = [row for row in rows if row.get("status") == "ACTIONABLE"]
    ordered = sorted(actionable, key=lambda row: (int(row.get("entry_block_time") or 0), str(row.get("anchor_id") or ""), int(row.get("entry_event_index") or 0)))
    midpoint = len(ordered) // 2
    return {"first": summary(ordered[:midpoint]), "second": summary(ordered[midpoint:])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--controls", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--comparison-protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    protocol = json.loads(args.comparison_protocol.read_text(encoding="utf-8"))
    anchors, markets = load_window_rows(args.windows)
    controls = load_controls(args.controls)
    wallet_rows = [
        outcome(window.get("anchor_event") or {}, window, markets, "WALLET")
        for window in anchors.values()
        if window.get("route") == policy["route"]
    ]
    control_rows: list[dict[str, Any]] = []
    for control in controls:
        if control.get("route") != policy["route"]:
            continue
        window = anchors.get(str(control.get("anchor_id")))
        if window:
            control_entry = dict(control.get("event") or {})
            control_entry["control_signature"] = control.get("control_signature")
            control_entry["control_block_time"] = control.get("control_block_time")
            control_entry["event_index"] = control.get("event_index")
            control_rows.append(outcome(control_entry, window, markets, "CONTROL"))
    wallet_summary = summary(wallet_rows)
    control_summary = summary(control_rows)
    wallet_summary["chronological_halves"] = chronological_halves(wallet_rows)
    control_summary["chronological_halves"] = chronological_halves(control_rows)
    actionable_wallet = wallet_summary["actionable"]
    actionable_control = control_summary["actionable"]
    ready = actionable_wallet >= MIN_ACTIONABLE and actionable_control >= MIN_ACTIONABLE
    target_wallet = int(protocol["target_actionable_wallet_trades"])
    target_control = int(protocol["target_actionable_control_trades"])
    ordered_wallet = sorted([row for row in wallet_rows if row.get("status") == "ACTIONABLE"], key=lambda row: (int(row.get("entry_block_time") or 0), str(row.get("anchor_id") or ""), int(row.get("entry_event_index") or 0)))
    ordered_control = sorted([row for row in control_rows if row.get("status") == "ACTIONABLE"], key=lambda row: (int(row.get("entry_block_time") or 0), str(row.get("anchor_id") or ""), int(row.get("entry_event_index") or 0)))
    equal_sample_ready = len(ordered_wallet) >= target_wallet and len(ordered_control) >= target_control
    result = {
        "schema": "PUMP_CURVE_PROSPECTIVE_POLICY_EVALUATION_V1",
        "policy_id": policy.get("policy_id"),
        "policy_sha256": sha256_file(args.policy),
        "comparison_protocol_sha256": sha256_file(args.comparison_protocol),
        "comparison_protocol": protocol,
        "policy": policy,
        "cohort_kind": "FRESH_PROSPECTIVE_VALIDATION",
        "read_only": True,
        "trading_enabled": False,
        "windows_sha256": sha256_file(args.windows) if args.windows.exists() else None,
        "controls_sha256": sha256_file(args.controls) if args.controls.exists() else None,
        "windows_loaded": len(anchors),
        "market_transactions_loaded": len(markets),
        "controls_loaded": len(controls),
        "wallet": wallet_summary,
        "controls": control_summary,
        "control_comparison": {
            "wallet_minus_control_pnl_sol": wallet_summary["pnl_sol"] - control_summary["pnl_sol"],
            "wallet_minus_control_roi": (wallet_summary["roi_on_actionable"] - control_summary["roi_on_actionable"]) if wallet_summary["roi_on_actionable"] is not None and control_summary["roi_on_actionable"] is not None else None,
        },
        "outcomes": wallet_rows + control_rows,
        "promotion_gate": {
            "minimum_actionable_wallet": MIN_ACTIONABLE,
            "minimum_actionable_control": MIN_ACTIONABLE,
            "actionable_wallet": actionable_wallet,
            "actionable_control": actionable_control,
            "sample_size_ready": ready,
            "decision": "REVIEW_AFTER_SAMPLE_GATE" if ready else "WAIT_FOR_FRESH_SAMPLE",
            "promotion_allowed": False,
        },
        "equal_sample_comparison": {
            "target_actionable_wallet_trades": target_wallet,
            "target_actionable_control_trades": target_control,
            "available_actionable_wallet_trades": len(ordered_wallet),
            "available_actionable_control_trades": len(ordered_control),
            "ready": equal_sample_ready,
            "decision": "READY_FOR_EQUAL_SAMPLE_REVIEW" if equal_sample_ready else "WAIT_FOR_EQUAL_SAMPLE",
            "wallet": summary(ordered_wallet[:target_wallet]) if equal_sample_ready else None,
            "controls": summary(ordered_control[:target_control]) if equal_sample_ready else None,
            "discovery_reference": protocol.get("discovery_reference"),
            "promotion_allowed": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PUMP_CURVE_PROSPECTIVE_POLICY_EVALUATED", "wallet": wallet_summary, "controls": control_summary, "promotion_gate": result["promotion_gate"]}, sort_keys=True))


if __name__ == "__main__":
    main()
