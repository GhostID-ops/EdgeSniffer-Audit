#!/usr/bin/env python3
"""Run a bounded, pre-registered 2CQ strategy discovery matrix offline.

This runner evaluates many entry/exit/size/cost hypotheses against the same
causal PumpSwap tape in one pass. It is deliberately discovery-only: it never
freezes a policy, starts a service, or submits a transaction.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from evaluateWalletCausalEconomicsV1 import (
    LAMPORTS_PER_SOL,
    NETWORK_FEE_SOL,
    buy_tokens,
    fee_bps,
    sell_quote,
    transitions_for_block,
    transitions_for_transaction,
    exact_pumpswap_buy,
    load_windows,
)
from evaluateWalletCausalEconomicsV1 import load_anchors as load_evaluator_anchors


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_anchors_safe(path: Path) -> tuple[dict[str, dict[str, Any]], int]:
    """Load source anchors without mutating evidence; malformed lines are skipped."""
    try:
        return load_evaluator_anchors(path), 0
    except json.JSONDecodeError:
        anchors: dict[str, dict[str, Any]] = {}
        malformed = 0
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if row.get("role") == "ANCHOR":
                    signature = str(row.get("anchor_signature") or "")
                    if signature:
                        anchors[signature] = row
                elif row.get("schema") == "WALLET_FINALIZED_ROUTE_RAW_TX_V1":
                    signature = str(row.get("signature") or "")
                    if signature:
                        anchors[signature] = {
                            "role": "ANCHOR", "anchor_signature": signature,
                            "anchor_slot": row.get("slot"), "block_time": row.get("block_time"),
                            "raw_transaction": row.get("transaction") or {}, "route": {},
                        }
        return anchors, malformed


def effective_quote_sol(state: dict[str, Any]) -> float:
    return (int(state.get("quote") or 0) + int(state.get("virtual_quote") or 0)) / LAMPORTS_PER_SOL


def entry_impact_bps(notional_sol: float, state: dict[str, Any], fee_bps_value: int, stress_bps: int) -> float | None:
    base = int(state.get("base") or 0)
    quote = int(state.get("quote") or 0) + int(state.get("virtual_quote") or 0)
    if base <= 0 or quote <= 0:
        return None
    tokens = buy_tokens(int(notional_sol * LAMPORTS_PER_SOL), state, fee_bps_value, stress_bps)
    if tokens <= 0:
        return None
    spot = quote / base
    average = (notional_sol * LAMPORTS_PER_SOL) / tokens
    return max(0.0, ((average / spot) - 1.0) * 10_000)


def route_matches(transition: dict[str, Any], wanted: str) -> bool:
    route = transition.get("research_route")
    if wanted == "ANY_EXACT_PUMPSWAP":
        return route in {"PUMPSWAP_DIRECT", "PUMPSWAP_CPI"}
    return route == wanted


def collect_marks(anchor: dict[str, Any], transition: dict[str, Any], window: dict[str, Any], blocks: dict[int, dict[str, Any]], market_transactions: dict[str, dict[str, Any]], wallet: str) -> list[dict[str, Any]]:
    pool, mint = str(transition["pool"]), str(transition["baseMint"])
    anchor_slot = int(anchor.get("anchor_slot") or anchor.get("slot") or 0)
    anchor_time = int(anchor.get("block_time") or 0)
    rows: list[dict[str, Any]] = []
    for slot in window.get("window_slots") or []:
        for mark in transitions_for_block(blocks.get(int(slot), {}), pool, mint):
            if int(mark.get("slot") or 0) == anchor_slot and str(mark.get("user")) == wallet:
                continue
            rows.append(mark)
    for signature in window.get("window_signatures") or []:
        source = market_transactions.get(str(signature))
        if not source:
            continue
        for mark in transitions_for_transaction(source, pool, mint):
            mark["signature"] = signature
            rows.append(mark)
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for mark in rows:
        block_time = mark.get("block_time")
        if block_time is not None and anchor_time and int(block_time) <= anchor_time + 1:
            continue
        key = (str(mark.get("signature") or ""), int(mark.get("slot") or 0), str(mark.get("side") or ""), int(mark.get("baseAmount") or 0))
        unique[key] = mark
    return sorted(unique.values(), key=lambda row: (int(row.get("block_time") or 0), int(row.get("slot") or 0), str(row.get("signature") or "")))


def mark_pnl(tokens: int, notional_sol: float, mark: dict[str, Any], stress_bps: int) -> float:
    quote_out = sell_quote(tokens, mark.get("postState") or {}, fee_bps(mark), stress_bps)
    return quote_out / LAMPORTS_PER_SOL - notional_sol - NETWORK_FEE_SOL


def pnl_pct(pnl_sol: float, notional_sol: float) -> float:
    return pnl_sol / notional_sol if notional_sol else -1.0


def exit_simulation(marks: list[dict[str, Any]], tokens: int, notional_sol: float, stress_bps: int, rule: dict[str, Any], anchor_time: int) -> dict[str, Any]:
    horizon = int(rule["horizon_seconds"])
    start = anchor_time + 1
    deadline = start + horizon
    path = [m for m in marks if m.get("block_time") is not None and start <= int(m["block_time"]) <= deadline]
    mfe = None
    mae = None
    for mark in path:
        value = mark_pnl(tokens, notional_sol, mark, stress_bps)
        mfe = value if mfe is None else max(mfe, value)
        mae = value if mae is None else min(mae, value)

    selected: dict[str, Any] | None = None
    reason = ""
    kind = rule["kind"]
    if kind == "FIXED":
        selected = next((m for m in marks if m.get("block_time") is not None and int(m["block_time"]) >= deadline), None)
        reason = "FIXED_HORIZON"
    elif kind in {"TP_SL", "TRAILING"}:
        high = None
        for mark in path:
            value = mark_pnl(tokens, notional_sol, mark, stress_bps)
            pct = pnl_pct(value, notional_sol)
            high = pct if high is None else max(high, pct)
            if kind == "TP_SL" and pct >= float(rule["take_profit_pct"]):
                selected, reason = mark, "TAKE_PROFIT"
                break
            if kind == "TP_SL" and pct <= -float(rule["stop_loss_pct"]):
                selected, reason = mark, "STOP_LOSS"
                break
            if kind == "TRAILING" and high >= float(rule["activation_pct"]) and pct <= high - float(rule["trail_pct"]):
                selected, reason = mark, "TRAILING_STOP"
                break
        if selected is None:
            selected = next((m for m in marks if m.get("block_time") is not None and int(m["block_time"]) >= deadline), None)
            reason = "HORIZON_FALLBACK"
    elif kind == "PARTIAL_TP":
        trigger = next((m for m in path if pnl_pct(mark_pnl(tokens, notional_sol, m, stress_bps), notional_sol) >= float(rule["take_profit_pct"])), None)
        final = next((m for m in marks if m.get("block_time") is not None and int(m["block_time"]) >= deadline), None)
        if trigger and final:
            fraction = float(rule["partial_fraction"])
            partial_tokens = int(tokens * fraction)
            remaining = tokens - partial_tokens
            partial_quote = sell_quote(partial_tokens, trigger.get("postState") or {}, fee_bps(trigger), stress_bps)
            final_quote = sell_quote(remaining, final.get("postState") or {}, fee_bps(final), stress_bps)
            pnl = (partial_quote + final_quote) / LAMPORTS_PER_SOL - notional_sol - 2 * NETWORK_FEE_SOL
            return {"status": "ACTIONABLE", "pnl_sol": pnl, "exit_mark": final, "exit_reason": "PARTIAL_TAKE_PROFIT", "mfe_sol": mfe, "mae_sol": mae, "partial_mark": trigger}
        selected = final
        reason = "HORIZON_FALLBACK"
    if selected is None:
        return {"status": "MISSED_EXIT_MARK", "pnl_sol": None, "exit_reason": reason, "mfe_sol": mfe, "mae_sol": mae}
    return {"status": "ACTIONABLE", "pnl_sol": mark_pnl(tokens, notional_sol, selected, stress_bps), "exit_mark": selected, "exit_reason": reason, "mfe_sol": mfe, "mae_sol": mae}


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actionable = [row for row in rows if row["status"] == "ACTIONABLE"]
    pnls = [float(row["pnl_sol"]) for row in actionable]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    curve = peak = drawdown = 0.0
    for value in pnls:
        curve += value
        peak = max(peak, curve)
        drawdown = max(drawdown, peak - curve)
    gross_loss = abs(sum(losses))
    profit_factor = sum(wins) / gross_loss if gross_loss else None
    return {
        "attempted": len(rows),
        "actionable": len(actionable),
        "actionable_rate": len(actionable) / len(rows) if rows else None,
        "missed_or_failed": len(rows) - len(actionable),
        "wins": len(wins),
        "win_rate": len(wins) / len(pnls) if pnls else None,
        "pnl_sol": sum(pnls),
        "roi": sum(pnls) / sum(float(row["notional_sol"]) for row in actionable) if actionable else None,
        "profit_factor": profit_factor,
        "profit_factor_unbounded": bool(wins) and gross_loss == 0,
        "max_drawdown_sol": drawdown,
        "mean_mfe_sol": sum(float(row["mfe_sol"]) for row in rows if row.get("mfe_sol") is not None) / len([row for row in rows if row.get("mfe_sol") is not None]) if any(row.get("mfe_sol") is not None for row in rows) else None,
        "mean_mae_sol": sum(float(row["mae_sol"]) for row in rows if row.get("mae_sol") is not None) / len([row for row in rows if row.get("mae_sol") is not None]) if any(row.get("mae_sol") is not None for row in rows) else None,
    }


def evaluate(anchors_path: Path, windows_path: Path, matrix_path: Path, wallet: str, controls_path: Path | None = None) -> dict[str, Any]:
    matrix = load_json(matrix_path)
    matrix_bytes = matrix_path.read_bytes()
    anchors, malformed = load_anchors_safe(anchors_path)
    windows, blocks, market_transactions = load_windows(windows_path)
    prepared: list[dict[str, Any]] = []
    for signature, anchor in anchors.items():
        transition = exact_pumpswap_buy(anchor, wallet)
        if not transition or not route_matches(transition, "ANY_EXACT_PUMPSWAP"):
            continue
        window = windows.get(signature)
        if not window:
            continue
        marks = collect_marks(anchor, transition, window, blocks, market_transactions, wallet)
        prepared.append({"signature": signature, "anchor": anchor, "transition": transition, "marks": marks})

    outcomes: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for entry in prepared:
        anchor = entry["anchor"]
        transition = entry["transition"]
        state = transition.get("preState") or {}
        route = transition.get("research_route")
        entry_fee = fee_bps(transition)
        anchor_time = int(anchor.get("block_time") or 0)
        for entry_filter in matrix["entry_filters"]:
            if not route_matches(transition, entry_filter["route"]):
                continue
            for notional in matrix["notionals_sol"]:
                for stress in matrix["stress_bps"]:
                    impact = entry_impact_bps(float(notional), state, entry_fee, int(stress))
                    reserve = effective_quote_sol(state)
                    if entry_filter.get("min_effective_quote_reserve_sol") is not None and reserve < float(entry_filter["min_effective_quote_reserve_sol"]):
                        continue
                    if entry_filter.get("max_entry_impact_bps") is not None and (impact is None or impact > float(entry_filter["max_entry_impact_bps"])):
                        continue
                    tokens = buy_tokens(int(float(notional) * LAMPORTS_PER_SOL), state, entry_fee, int(stress))
                    for exit_rule in matrix["exit_rules"]:
                        key = "|".join([entry_filter["id"], exit_rule["id"], str(notional), str(stress)])
                        base = {"strategy_id": key, "entry_filter": entry_filter["id"], "exit_rule": exit_rule["id"], "route": route, "anchor_signature": entry["signature"], "notional_sol": float(notional), "stress_bps": int(stress), "entry_reserve_sol": reserve, "entry_impact_bps": impact}
                        if tokens <= 0:
                            row = base | {"status": "FAILED_ENTRY", "pnl_sol": None, "mfe_sol": None, "mae_sol": None}
                        else:
                            simulation = exit_simulation(entry["marks"], tokens, float(notional), int(stress), exit_rule, anchor_time)
                            row = base | {"status": simulation["status"], "pnl_sol": simulation["pnl_sol"], "mfe_sol": simulation.get("mfe_sol"), "mae_sol": simulation.get("mae_sol"), "exit_reason": simulation.get("exit_reason")}
                        outcomes.append(row)
                        grouped[key].append(row)

    controls_loaded = 0
    if controls_path:
        with controls_path.open(encoding="utf-8") as handle:
            controls_loaded = sum(1 for line in handle if line.strip())
    surface = []
    minimums = matrix.get("minimums_for_exploratory_rank") or {}
    for strategy_id, rows in grouped.items():
        stats = metrics(rows)
        eligible = stats["attempted"] >= int(minimums.get("attempted", 0)) and stats["actionable"] >= int(minimums.get("actionable", 0)) and controls_loaded >= int(minimums.get("controls", 0))
        surface.append({"strategy_id": strategy_id, "eligible_for_exploratory_rank": eligible, "metrics": stats})
    def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        pf = row["metrics"].get("profit_factor")
        pf_score = pf if pf is not None else (1e99 if row["metrics"].get("profit_factor_unbounded") else -1.0)
        return (not row["eligible_for_exploratory_rank"], -(row["metrics"]["pnl_sol"] or 0.0), -pf_score)

    surface.sort(key=sort_key)
    return {
        "schema": "2CQ_NEW_WALLET_STRATEGY_MATRIX_RESULT_V1",
        "wallet": wallet,
        "matrix_sha256": sha256_bytes(matrix_bytes),
        "anchors_sha256": sha256_file(anchors_path),
        "windows_sha256": sha256_file(windows_path),
        "anchors_path": str(anchors_path),
        "windows_path": str(windows_path),
        "anchors_loaded": len(anchors),
        "malformed_anchor_lines_skipped": malformed,
        "windows_loaded": len(windows),
        "market_transactions_loaded": len(market_transactions),
        "exact_pumpswap_anchor_windows_prepared": len(prepared),
        "controls_loaded": controls_loaded,
        "outcomes": outcomes,
        "surface": surface,
        "policy_frozen": False,
        "selection_performed": False,
        "promotion_blocked": True,
        "trading_enabled": False,
        "notes": [
            "This is a discovery matrix, not validation.",
            "Unresolved routes and unavailable marks are excluded or recorded as misses; no prices are imputed.",
            "A candidate must be frozen before fresh prospective validation; the current result cannot perform that selection.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchors", type=Path, required=True)
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--controls", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.anchors, args.windows, args.matrix, args.wallet, args.controls)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("matrix_sha256", "anchors_loaded", "windows_loaded", "exact_pumpswap_anchor_windows_prepared", "controls_loaded", "policy_frozen", "selection_performed", "promotion_blocked", "trading_enabled")}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
