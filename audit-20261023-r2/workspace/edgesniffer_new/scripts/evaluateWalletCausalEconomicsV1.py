#!/usr/bin/env python3
"""Causal PumpSwap economics surface over wallet anchors and post-entry windows.

Only exact PumpSwap buy transitions are currently economically eligible. Other
routes are retained as explicit unsupported/coverage outcomes; they are never
silently converted into prices.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path
from typing import Any

import pumpSwapExactEconomicsV1 as pumpswap
from materializeWalletRouteCensusV1 import pumpswap_tx_with_extension
import twoCqRouteEvidenceAdaptersV1 as route_adapters

LAMPORTS_PER_SOL = 1_000_000_000
HORIZONS = (5, 10, 15, 30, 60, 120, 300)
NOTIONALS = (0.1, 0.5, 1.0)
STRESSES_BPS = (0, 25, 50)
NETWORK_FEE_SOL = 0.000005


def fee_bps(row: dict[str, Any]) -> int:
    return sum(int(row.get(key) or 0) for key in ("lpFeeBps", "protocolFeeBps", "coinCreatorBps", "cashbackBps", "buybackBps"))


def buy_tokens(quote_in: int, state: dict[str, Any], fees: int, stress: int) -> int:
    base = int(state.get("base") or 0)
    quote = int(state.get("quote") or 0) + int(state.get("virtual_quote") or 0)
    if quote_in <= 0 or base <= 0 or quote <= 0:
        return 0
    net = quote_in * max(0, 10_000 - fees - stress) // 10_000
    return max(0, base - (base * quote) // (quote + net))


def sell_quote(base_in: int, state: dict[str, Any], fees: int, stress: int) -> int:
    base = int(state.get("base") or 0)
    quote = int(state.get("quote") or 0) + int(state.get("virtual_quote") or 0)
    if base_in <= 0 or base <= 0 or quote <= 0:
        return 0
    gross = quote * base_in // (base + base_in)
    return gross * max(0, 10_000 - fees - stress) // 10_000


def load_anchors(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("role") == "ANCHOR":
                result[str(row.get("anchor_signature"))] = row
            elif row.get("schema") == "WALLET_FINALIZED_ROUTE_RAW_TX_V1":
                signature = str(row.get("signature") or "")
                if signature:
                    result[signature] = {
                        "role": "ANCHOR", "anchor_signature": signature,
                        "anchor_slot": row.get("slot"), "block_time": row.get("block_time"),
                        "raw_transaction": row.get("transaction") or {}, "route": {},
                    }
    return result


def load_windows(path: Path) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    windows: dict[str, dict[str, Any]] = {}
    blocks: dict[int, dict[str, Any]] = {}
    market_transactions: dict[str, dict[str, Any]] = {}
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
            if row.get("record_type") == "ANCHOR_WINDOW":
                windows[str(row.get("anchor_signature"))] = row
            elif row.get("record_type") == "FINALIZED_BLOCK":
                blocks[int(row.get("slot"))] = row
            elif row.get("record_type") == "MARKET_TRANSACTION":
                market_transactions[str(row.get("signature"))] = row
    return windows, blocks, market_transactions


def exact_pumpswap_buy(anchor: dict[str, Any], wallet: str) -> dict[str, Any] | None:
    try:
        tx, _variant = pumpswap_tx_with_extension(anchor.get("raw_transaction") or {})
        rows = pumpswap.validate_transaction(tx)
    except Exception:
        return None
    matches = [row for row in rows if row.get("user") == wallet and row.get("side") == "BUY"]
    if len(matches) != 1:
        return None
    transition = matches[0]
    resolved = route_adapters.resolve_route(anchor.get("raw_transaction") or {})
    venues = resolved.get("underlyingVenues") or []
    if len(venues) == 1 and venues[0].get("venue") == "PUMPSWAP":
        transition["research_route"] = "PUMPSWAP_CPI" if venues[0].get("invocation") == "CPI" else "PUMPSWAP_DIRECT"
    else:
        transition["research_route"] = "PUMPSWAP_UNRESOLVED_ROUTE"
    return transition


def transitions_for_block(block: dict[str, Any], pool: str, mint: str, wallet: str | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    raw = block.get("raw_block") or {}
    for entry in raw.get("transactions") or []:
        try:
            tx = entry
            tx_for_decode, _variant = pumpswap_tx_with_extension(tx)
            for row in pumpswap.validate_transaction(tx_for_decode):
                if row.get("pool") == pool and row.get("baseMint") == mint and (wallet is None or row.get("user") == wallet):
                    row["block_time"] = block.get("block_time")
                    row["slot"] = block.get("slot")
                    result.append(row)
        except Exception:
            continue
    return result


def transitions_for_transaction(row: dict[str, Any], pool: str, mint: str) -> list[dict[str, Any]]:
    tx = row.get("raw_transaction") or {}
    try:
        tx_for_decode, _variant = pumpswap_tx_with_extension(tx)
        transitions = pumpswap.validate_transaction(tx_for_decode)
    except Exception:
        return []
    result = []
    for transition in transitions:
        if transition.get("pool") == pool and transition.get("baseMint") == mint:
            transition["block_time"] = row.get("block_time")
            transition["slot"] = row.get("slot")
            result.append(transition)
    return result


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(row["pnl_sol"]) for row in rows if row.get("status") == "ACTIONABLE"]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    curve = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in pnls:
        curve += value
        peak = max(peak, curve)
        drawdown = max(drawdown, peak - curve)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "attempted": len(rows), "actionable": len(pnls),
        "wins": len(wins), "win_rate": len(wins) / len(pnls) if pnls else None,
        "pnl_sol": sum(pnls), "roi": sum(pnls) / sum(float(row["notional_sol"]) for row in rows if row.get("status") == "ACTIONABLE") if pnls else None,
        "profit_factor": gross_win / gross_loss if gross_loss else (math.inf if gross_win else None),
        "max_drawdown_sol": drawdown, "missed_or_failed": len(rows) - len(pnls),
    }


def evaluate(anchors_path: Path, windows_path: Path, wallet: str) -> dict[str, Any]:
    anchors = load_anchors(anchors_path)
    windows, blocks, market_transactions = load_windows(windows_path)
    outcomes: list[dict[str, Any]] = []
    # This evaluator receives the complete raw wallet tape, but it only
    # evaluates exact PumpSwap admissions.  Counting routes before the exact
    # admission check made the published summary report thousands of unrelated
    # raw rows as UNRESOLVED, even when the outcome rows were exact.
    route_counts: collections.Counter[str] = collections.Counter()
    raw_route_counts: collections.Counter[str] = collections.Counter()
    for signature, anchor in anchors.items():
        raw_route = (anchor.get("route") or {}).get("repaired_route") or (anchor.get("route") or {}).get("route_resolution")
        raw_route_counts[str(raw_route or "UNRESOLVED")] += 1
        transition = exact_pumpswap_buy(anchor, wallet)
        if not transition:
            continue
        route = str(transition.get("research_route") or "PUMPSWAP_UNRESOLVED_ROUTE")
        route_counts[route] += 1
        pool, mint = str(transition["pool"]), str(transition["baseMint"])
        anchor_time = int(anchor.get("block_time") or 0)
        window = windows.get(signature) or {}
        mark_rows: list[dict[str, Any]] = []
        for slot in window.get("window_slots") or []:
            for mark in transitions_for_block(blocks.get(int(slot), {}), pool, mint):
                if int(mark.get("slot") or 0) == int(anchor.get("anchor_slot") or anchor.get("slot") or 0) and str(mark.get("user")) == wallet:
                    continue
                if anchor_time and mark.get("block_time") is not None and int(mark["block_time"]) <= anchor_time + 1:
                    continue
                mark_rows.append(mark)
        for market_signature in window.get("window_signatures") or []:
            for mark in transitions_for_transaction(market_transactions.get(str(market_signature), {}), pool, mint):
                if int(mark.get("slot") or 0) == int(anchor.get("anchor_slot") or anchor.get("slot") or 0) and str(mark.get("user")) == wallet:
                    continue
                if anchor_time and mark.get("block_time") is not None and int(mark["block_time"]) <= anchor_time + 1:
                    continue
                mark_rows.append(mark)
        mark_rows.sort(key=lambda row: (int(row.get("block_time") or 0), int(row.get("slot") or 0)))
        entry_state = transition.get("preState") or {}
        actual_fee = fee_bps(transition)
        for notional in NOTIONALS:
            for stress in STRESSES_BPS:
                tokens = buy_tokens(int(notional * LAMPORTS_PER_SOL), entry_state, actual_fee, stress)
                for horizon in HORIZONS:
                    target = anchor_time + 1 + horizon
                    mark = next((row for row in mark_rows if int(row.get("block_time") or 0) >= target), None)
                    base = {"anchor_signature": signature, "route": route, "pool": pool, "mint": mint, "notional_sol": notional, "horizon_seconds": horizon, "stress_bps": stress, "entry_delay_seconds": 1, "venue_fee_bps": actual_fee}
                    if tokens <= 0:
                        outcomes.append(base | {"status": "FAILED_ENTRY", "pnl_sol": None})
                        continue
                    if not mark:
                        outcomes.append(base | {"status": "MISSED_EXIT_MARK", "pnl_sol": None, "tokens": tokens})
                        continue
                    exit_fees = fee_bps(mark)
                    quote_out = sell_quote(tokens, mark.get("postState") or {}, exit_fees, stress)
                    pnl = quote_out / LAMPORTS_PER_SOL - notional - NETWORK_FEE_SOL
                    outcomes.append(base | {"status": "ACTIONABLE", "pnl_sol": pnl, "tokens": tokens, "exit_slot": mark.get("slot"), "exit_block_time": mark.get("block_time"), "exit_fee_bps": exit_fees})
    grouped: dict[str, dict[str, Any]] = {}
    for row in outcomes:
        key = json.dumps([row["route"], row["notional_sol"], row["horizon_seconds"], row["stress_bps"]])
        grouped[key] = metrics([item for item in outcomes if json.dumps([item["route"], item["notional_sol"], item["horizon_seconds"], item["stress_bps"]]) == key])
    return {
        "schema": "WALLET_CAUSAL_ECONOMICS_SURFACE_V1", "wallet": wallet,
        "entry_delay_seconds": 1, "notionals_sol": list(NOTIONALS), "horizons_seconds": list(HORIZONS), "stress_bps": list(STRESSES_BPS),
        "network_fee_sol_per_round_trip": NETWORK_FEE_SOL,
        "route_counts": dict(route_counts),
        "raw_source_route_counts": dict(raw_route_counts),
        "anchors_loaded": len(anchors), "windows_loaded": len(windows), "blocks_loaded": len(blocks),
        "eligible_pumpswap_anchors": len({row["anchor_signature"] for row in outcomes}),
        "outcomes": outcomes, "surface": grouped, "policy_frozen": False, "trading_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchors", type=Path, required=True)
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.anchors, args.windows, args.wallet)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("anchors_loaded", "windows_loaded", "blocks_loaded", "eligible_pumpswap_anchors", "policy_frozen", "trading_enabled")}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
