#!/usr/bin/env python3
"""Read-only native-order Pump-curve timing and exit-inclusion audit.

This is a versioned repair of the earlier parity diagnostic.  It does not
change the frozen policy, the collectors, or any execution path.

The repair has three important properties:

* Same-slot market transactions are ordered by the finalized transactionIndex
  carried in the raw transaction, not by signature text.
* Entry is quoted from the state at the one-second block-time boundary.  When
  transactions exist in the boundary second, the tape cannot tell whether the
  receive-side delay elapsed before or after them, so both boundary states are
  retained instead of choosing one silently.
* Exit marks are separated into the state immediately before the exit
  boundary, states after transactions in that boundary second, and the first
  state in the following second.  These are inclusion candidates, not claims
  that a sell was submitted or included.

The resulting report is an audit artifact only.  It is intentionally unable
to certify receive-clock parity without native arrival and submitted-transaction
telemetry.
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
from materializeWalletRouteCensusV1 import curve_events_with_extension

LAMPORTS_PER_SOL = 1_000_000_000
NETWORK_FEE_SOL = 0.000005
NOTIONAL_SOL = 0.5
STRESS_BPS = 50
ENTRY_DELAY_SECONDS = 1
EXIT_HORIZON_SECONDS = 5


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def load_windows(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    anchors: dict[str, dict[str, Any]] = {}
    markets: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(path):
        if row.get("record_type") == "ANCHOR_WINDOW":
            anchors[str(row.get("anchor_id"))] = row
        elif row.get("record_type") == "MARKET_TRANSACTION":
            markets[str(row.get("signature"))] = row
    return anchors, markets


def load_source_orders(path: Path | None) -> dict[str, tuple[int, int | None]]:
    """Return signature -> (slot, transactionIndex) for anchor ordering."""
    if path is None or not path.exists():
        return {}
    result: dict[str, tuple[int, int | None]] = {}
    for row in load_jsonl(path):
        signature = str(row.get("signature") or "")
        if not signature:
            continue
        transaction = row.get("transaction") or {}
        index = transaction.get("transactionIndex")
        result[signature] = (int(row.get("slot") or transaction.get("slot") or 0), int(index) if index is not None else None)
    return result


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


def fees(event: dict[str, Any], stress_bps: int = STRESS_BPS) -> curve.Fees:
    return curve.Fees(
        protocol_bps=int(event.get("fee_basis_points") or 0),
        creator_bps=int(event.get("creator_fee_basis_points") or 0),
        lp_bps=int(stress_bps),
    )


def transaction_index(row: dict[str, Any]) -> int | None:
    raw = row.get("raw_transaction") or {}
    transaction = raw.get("transaction") or {}
    value = transaction.get("transactionIndex", raw.get("transactionIndex"))
    return int(value) if value is not None else None


def order_key(row: dict[str, Any]) -> tuple[int, int, str, int]:
    index = transaction_index(row)
    # Missing transactionIndex is retained but sorts after known indexes in
    # the same slot.  Such rows remain explicitly marked as incomplete.
    return (int(row.get("slot") or 0), index if index is not None else 2**63 - 1, str(row.get("signature") or ""), int(row.get("event_index") or 0))


def marks_for(window: dict[str, Any], markets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    mint = str(window.get("mint") or "")
    quote_mint = str(window.get("quote_mint") or "")
    result: list[dict[str, Any]] = []
    for signature in window.get("window_signatures") or []:
        row = markets.get(str(signature))
        if not row:
            continue
        try:
            events, _variant = curve_events_with_extension(row.get("raw_transaction") or {})
        except Exception:
            continue
        for event_index, event in enumerate(events):
            if str(event.mint) != mint or str(event.quote_mint) != quote_mint:
                continue
            item = {
                "signature": row.get("signature"),
                "slot": int(row.get("slot") or 0),
                "transaction_index": transaction_index(row),
                "block_time": int(row.get("block_time") or event.timestamp or 0),
                "event_index": event_index,
                "event": dataclasses.asdict(event),
            }
            result.append(item)
    result.sort(key=order_key)
    return result


def state_after(anchor_post: curve.CurveState, rows: list[dict[str, Any]]) -> curve.CurveState:
    if not rows:
        return anchor_post
    return curve_state(rows[-1]["event"].get("post_state") or {})


def quote(entry_state: curve.CurveState, entry_event: dict[str, Any], exit_state: curve.CurveState, exit_event: dict[str, Any] | None) -> tuple[str, float | None, int | None]:
    try:
        buy = curve.quote_exact_quote_in_buy(entry_state, int(NOTIONAL_SOL * LAMPORTS_PER_SOL), fees(entry_event))
    except Exception as error:
        return f"FAILED_ENTRY:{type(error).__name__}:{error}", None, None
    if exit_event is None:
        return "MISSED_EXIT_INCLUSION_MARK", None, buy.token_out
    try:
        sell = curve.quote_exact_token_sell(exit_state, buy.token_out, 0, fees(exit_event))
        pnl = sell.immediate_quote_out / LAMPORTS_PER_SOL - NOTIONAL_SOL - NETWORK_FEE_SOL
        return "ACTIONABLE", pnl, buy.token_out
    except Exception as error:
        return f"FAILED_EXIT_QUOTE:{type(error).__name__}:{error}", None, buy.token_out


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
        "profit_factor": sum(wins) / gross_loss if gross_loss else (math.inf if wins else None),
        "win_rate": len(wins) / len(values) if values else None,
        "gross_win_sol": sum(wins),
        "gross_loss_sol": gross_loss,
        "max_drawdown_sol": drawdown,
    }


def evaluate(windows_path: Path, source_path: Path | None) -> dict[str, Any]:
    anchors, markets = load_windows(windows_path)
    source_orders = load_source_orders(source_path)
    rows: list[dict[str, Any]] = []
    exact_values: list[float] = []
    boundary_values: list[float] = []
    inclusion_values: list[float] = []
    for anchor_id, window in anchors.items():
        if window.get("route") != "PUMP_BONDING_CURVE_TOP_LEVEL":
            continue
        event = window.get("anchor_event") or {}
        quote_mint = str(window.get("quote_mint") or event.get("quote_mint") or "")
        if quote_mint not in curve.SOL_QUOTE_MINTS:
            continue
        marks = marks_for(window, markets)
        anchor_time = int(window.get("anchor_block_time") or 0)
        anchor_slot = int(window.get("anchor_slot") or 0)
        anchor_signature = str(window.get("anchor_signature") or "")
        anchor_order = source_orders.get(anchor_signature)
        anchor_tx_index = anchor_order[1] if anchor_order and anchor_order[0] == anchor_slot else None
        if anchor_tx_index is None:
            # The anchor is not present in the market tape.  Keep same-slot
            # rows visible, but do not pretend their relation is exact.
            after_anchor = [row for row in marks if row["slot"] > anchor_slot]
            order_confidence = "SLOT_ONLY_ANCHOR_ORDER"
        else:
            after_anchor = [row for row in marks if order_key(row) > (anchor_slot, anchor_tx_index, anchor_signature, -1)]
            order_confidence = "SLOT_AND_TRANSACTION_INDEX"
        anchor_post = curve_state(event.get("post_state") or {})
        entry_target = anchor_time + ENTRY_DELAY_SECONDS
        entry_before = [row for row in after_anchor if row["block_time"] < entry_target]
        entry_boundary = [row for row in after_anchor if row["block_time"] == entry_target]
        entry_before_state = state_after(anchor_post, entry_before)
        entry_after_state = state_after(anchor_post, entry_before + entry_boundary)
        entry_exact = not entry_boundary
        exit_target = anchor_time + ENTRY_DELAY_SECONDS + EXIT_HORIZON_SECONDS
        exit_before = [row for row in after_anchor if row["block_time"] < exit_target]
        exit_boundary = [row for row in after_anchor if row["block_time"] == exit_target]
        exit_after = [row for row in after_anchor if row["block_time"] > exit_target]
        exit_before_state = state_after(anchor_post, exit_before)
        exit_boundary_states = [curve_state(row["event"].get("post_state") or {}) for row in exit_boundary]
        exit_next = exit_after[0] if exit_after else None
        exit_next_state = curve_state(exit_next["event"].get("post_state") or {}) if exit_next else None
        # The boundary tape admits one state before same-second events and one
        # state after each ordered same-second event.  The first later event is
        # retained as a one-step inclusion-lag sensitivity.
        exit_candidates: list[tuple[str, curve.CurveState, dict[str, Any] | None]] = [("before_boundary", exit_before_state, exit_boundary[0]["event"] if exit_boundary else (exit_next["event"] if exit_next else None))]
        for index, boundary_row in enumerate(exit_boundary):
            exit_candidates.append((f"after_boundary_event_{index}", exit_boundary_states[index], boundary_row["event"]))
        if exit_next and exit_next_state is not None:
            exit_candidates.append(("first_next_second", exit_next_state, exit_next["event"]))
        entry_candidates = [("before_delay_boundary", entry_before_state), ("after_delay_boundary", entry_after_state)] if entry_boundary else [("exact_blocktime_state", entry_before_state)]
        candidate_values: list[float] = []
        exact_candidate_values: list[float] = []
        inclusion_candidate_values: list[float] = []
        candidate_statuses: list[str] = []
        for entry_label, entry_state in entry_candidates:
            for exit_label, exit_state, exit_event in exit_candidates:
                status, value, tokens = quote(entry_state, event, exit_state, exit_event)
                candidate_statuses.append(f"{entry_label}|{exit_label}|{status}")
                if value is not None:
                    candidate_values.append(value)
                    if entry_label == "exact_blocktime_state" and exit_label == "before_boundary" and not exit_boundary:
                        exact_candidate_values.append(value)
                    if exit_label != "before_boundary":
                        inclusion_candidate_values.append(value)
        exact_values.extend(exact_candidate_values)
        boundary_values.extend(candidate_values)
        inclusion_values.extend(inclusion_candidate_values)
        rows.append({
            "anchor_id": anchor_id,
            "anchor_signature": anchor_signature,
            "anchor_slot": anchor_slot,
            "anchor_transaction_index": anchor_tx_index,
            "order_confidence": order_confidence,
            "anchor_block_time": anchor_time,
            "entry_target_block_time": entry_target,
            "entry_boundary_event_count": len(entry_boundary),
            "entry_exact_blocktime_state": entry_exact,
            "exit_target_block_time": exit_target,
            "exit_boundary_event_count": len(exit_boundary),
            "exit_next_second_event": bool(exit_next),
            "candidate_statuses": candidate_statuses,
            "candidate_pnl_low_sol": min(candidate_values) if candidate_values else None,
            "candidate_pnl_high_sol": max(candidate_values) if candidate_values else None,
            "exact_blocktime_pnl_sol": exact_candidate_values[0] if len(exact_candidate_values) == 1 else None,
            "inclusion_candidate_pnl_low_sol": min(inclusion_candidate_values) if inclusion_candidate_values else None,
            "inclusion_candidate_pnl_high_sol": max(inclusion_candidate_values) if inclusion_candidate_values else None,
        })
    exact = [value for value in exact_values]
    boundary = [value for value in boundary_values]
    inclusion = [value for value in inclusion_values]
    return {
        "schema": "PUMP_CURVE_NATIVE_TIMING_EXIT_PARITY_V2",
        "read_only": True,
        "policy_unchanged": True,
        "trading_enabled": False,
        "notional_sol": NOTIONAL_SOL,
        "stress_bps": STRESS_BPS,
        "entry_delay_seconds": ENTRY_DELAY_SECONDS,
        "exit_horizon_seconds": EXIT_HORIZON_SECONDS,
        "windows_loaded": len(rows),
        "source_path": str(source_path) if source_path else None,
        "source_sha256": sha256_file(source_path) if source_path and source_path.exists() else None,
        "ordering": {
            "market_key": "slot,transactionIndex,signature,event_index",
            "anchor_key": "source slot,source transactionIndex,signature",
            "same_slot_transaction_index_used": sum(row["anchor_transaction_index"] is not None for row in rows),
            "slot_only_anchor_order": sum(row["anchor_transaction_index"] is None for row in rows),
        },
        "boundary_counts": {
            "entry_boundary_ambiguous": sum(row["entry_boundary_event_count"] > 0 for row in rows),
            "entry_exact_blocktime": sum(row["entry_exact_blocktime_state"] for row in rows),
            "exit_boundary_ambiguous": sum(row["exit_boundary_event_count"] > 0 for row in rows),
            "exit_with_next_second_event": sum(row["exit_next_second_event"] for row in rows),
        },
        "exact_blocktime_state": summary(exact),
        "all_entry_exit_boundary_candidates": summary(boundary),
        "exit_inclusion_candidates_only": summary(inclusion),
        "parity_status": "NATIVE_ORDER_REPAIRED_BLOCKTIME_BOUNDARY_RETAINED",
        "promotion_status": "BLOCKED_RECEIVE_CLOCK_AND_SUBMITTED_EXIT_INCLUSION_UNOBSERVED",
        "limitations": [
            "transactionIndex repairs finalized ordering inside a slot; block_time remains one-second granularity.",
            "Entry rows with transactions in the target second retain before/after boundary states rather than claiming an exact receive-time state.",
            "Exit candidates are tape states; no sell transaction was submitted or observed, so this is not execution certification.",
            "Network priority-fee selection, RPC latency, failed submissions, and token-account creation are not observed in receive-only mode.",
        ],
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.windows, args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("windows_loaded", "ordering", "boundary_counts", "exact_blocktime_state", "all_entry_exit_boundary_candidates", "exit_inclusion_candidates_only", "parity_status", "promotion_status")}, sort_keys=True))


if __name__ == "__main__":
    main()
