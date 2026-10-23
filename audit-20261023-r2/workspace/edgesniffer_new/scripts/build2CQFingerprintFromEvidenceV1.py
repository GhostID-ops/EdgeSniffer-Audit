#!/usr/bin/env python3
"""Derive an outcome-blind 2CQ structural fingerprint from preserved evidence.

This file deliberately does not read PnL, exits, controls, or market outcomes.
It describes what the confirmed wallet actually did: venue/route, quote asset,
curve phase, and order-size/timing distributions.  A separate detector may use
the resulting contract to find structurally similar market-wide buys.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SOL_MINTS = {
    "11111111111111111111111111111111",
    "So11111111111111111111111111111111111111112",
}
TARGET_ROUTE = "PUMP_BONDING_CURVE_TOP_LEVEL"


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p05": None, "p25": None, "median": None, "p75": None, "p95": None}
    values = sorted(values)
    def at(p: float) -> float:
        return values[min(len(values) - 1, max(0, math.ceil(p * len(values)) - 1))]
    return {"p05": at(.05), "p25": at(.25), "median": statistics.median(values),
            "p75": at(.75), "p95": at(.95)}


def quote_asset(row: dict[str, Any]) -> str | None:
    value = row.get("quote_mint")
    if value:
        return str(value)
    return None


def buy_size_sol(row: dict[str, Any]) -> float | None:
    asset = quote_asset(row)
    if asset and asset not in SOL_MINTS:
        return None
    for key in ("sol_amount_raw", "quote_amount_raw"):
        value = row.get(key)
        try:
            if value is not None and int(value) > 0:
                return int(value) / 1_000_000_000
        except (TypeError, ValueError):
            pass
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wallet", required=True)
    args = parser.parse_args()

    rows = load_rows(args.events)
    buys = [row for row in rows if str(row.get("side")) == "BUY"]
    route_counts = Counter(str(row.get("route") or "UNKNOWN") for row in buys)
    quote_counts = Counter(str(row.get("quote_mint") or "UNKNOWN") for row in buys)
    economic = [row for row in buys if row.get("route") == TARGET_ROUTE and quote_asset(row) in SOL_MINTS]

    per_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in economic:
        if row.get("mint"):
            per_mint[str(row["mint"])].append(row)
    inter_buy_seconds: list[float] = []
    for mint_rows in per_mint.values():
        mint_rows.sort(key=lambda row: (int(row.get("block_time") or 0), int(row.get("slot") or 0), int(row.get("event_index") or 0)))
        for first, second in zip(mint_rows, mint_rows[1:]):
            if first.get("block_time") is not None and second.get("block_time") is not None:
                delta = int(second["block_time"]) - int(first["block_time"])
                if delta >= 0:
                    inter_buy_seconds.append(float(delta))

    virtual_sol = []
    virtual_token = []
    curve_price = []
    for row in economic:
        state = row.get("pre_state") or {}
        try:
            vs = int(state["virtual_sol"])
            vt = int(state["virtual_token"])
            if vs > 0 and vt > 0:
                virtual_sol.append(vs / 1_000_000_000)
                virtual_token.append(vt / 1_000_000)
                curve_price.append(vs / vt)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue

    size_values = [value for value in (buy_size_sol(row) for row in economic) if value is not None]
    route_total = max(1, len(buys))
    target_route_share = route_counts[TARGET_ROUTE] / route_total
    contract = {
        "schema": "EDGESNIFFER_2CQ_INDEPENDENT_FINGERPRINT_V1",
        "generated_at_ms": int(time.time() * 1000),
        "read_only": True,
        "outcome_blind": True,
        "selection_performed": False,
        "policy_frozen": False,
        "trading_enabled": False,
        "tracked_wallet": args.wallet,
        "source": {"path": str(args.events), "sha256": sha256_file(args.events)},
        "evidence": {
            "buy_rows": len(buys),
            "economic_buy_rows": len(economic),
            "unique_economic_mints": len(per_mint),
            "route_counts": dict(route_counts),
            "quote_asset_counts": dict(quote_counts),
            "unsupported_quote_buy_rows": len(buys) - sum(quote_counts[mint] for mint in SOL_MINTS),
        },
        "derived_fingerprint": {
            "name": "2CQ_PUMP_CURVE_SOL_TOP_LEVEL_STRUCTURAL_IMPULSE_V1",
            "description": "An exact Pump bonding-curve buy in the dominant top-level route, using SOL/WSOL and an early native curve state. This is structural similarity, not proof of profitability.",
            "route": TARGET_ROUTE,
            "route_share_of_all_buys": target_route_share,
            "quote_assets": sorted(SOL_MINTS),
            "required_state": ["pre_state", "post_state", "real_sol", "real_token", "virtual_sol", "virtual_token"],
            "curve_phase_observations": {
                "virtual_sol_sol": quantiles(virtual_sol),
                "virtual_token_millions": quantiles(virtual_token),
                "price_quote_per_token_raw": quantiles(curve_price),
            },
            "buy_size_sol": quantiles(size_values),
            "same_mint_inter_buy_seconds": quantiles(inter_buy_seconds),
            "independent_admission_rule": {
                "signal": "market-wide native Pump buy; teacher wallet label ignored",
                "route": TARGET_ROUTE,
                "quote_asset": "SOL_OR_WRAPPED_SOL_ONLY",
                "early_curve_threshold": "virtual_sol_sol <= p75 of preserved SOL-quoted target buys",
                "size_range": "not an admission gate; reported for sizing research only",
                "deduplication": "one signal per mint per 300-second research window",
            },
        },
        "limitations": [
            "Pump quote-asset buys are preserved but excluded from the SOL executable fingerprint; they require a separate quote-asset evaluator.",
            "This contract derives structural behavior only. It does not select an exit, notional, fee stress, or policy.",
            "External-flow thresholds are not inferred from the teacher wallet's own transactions; they may be tested as pre-registered hypotheses only.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"schema": contract["schema"], "buy_rows": len(buys), "economic_buy_rows": len(economic), "route": TARGET_ROUTE}, sort_keys=True))


if __name__ == "__main__":
    main()
