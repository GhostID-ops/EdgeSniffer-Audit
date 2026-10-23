#!/usr/bin/env python3
"""Emit a venue-separated Raydium CLMM economics surface.

The current CLMM adapter proves exact transitions and post-swap price state,
but it does not have the pre-entry tick-array/liquidity snapshot needed to
quote a hypothetical 0.1/0.5/1.0 SOL order after the one-second delay.  This
script therefore emits explicit blocked surface rows rather than inventing
PnL from an observed trade size.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

NOTIONALS = (0.1, 0.5, 1.0)
HORIZONS = (5, 10, 15, 30, 60, 120, 300)
STRESSES = (0, 25, 50)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path: Path) -> tuple[list[dict], dict[str, dict]]:
    anchors, markets = [], {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("record_type") == "ANCHOR_WINDOW":
            anchors.append(row)
        elif row.get("record_type") == "MARKET_TRANSACTION":
            markets[str(row.get("signature"))] = row
    return anchors, markets


def evaluate(path: Path) -> dict:
    anchors, markets = load(path)
    observed = []
    for anchor in anchors:
        transition = anchor.get("anchor_transition") or {}
        observed.append({
            "anchor_id": anchor.get("anchor_id"),
            "signature": anchor.get("anchor_signature"),
            "pool": anchor.get("pool"),
            "mint": anchor.get("mint"),
            "quote_mint": anchor.get("quote_mint"),
            "observed_input_raw": transition.get("inputAmountRaw"),
            "observed_output_raw": transition.get("outputAmountRaw"),
            "observed_trade_fee_raw": transition.get("tradeFeeInputRaw"),
            "post_state": transition.get("postState"),
        })
    surface = []
    for notional in NOTIONALS:
        for stress in STRESSES:
            for horizon in HORIZONS:
                surface.append({
                    "venue": "RAYDIUM_CLMM",
                    "notional_sol": notional,
                    "stress_bps": stress,
                    "horizon_seconds": horizon,
                    "entry_delay_seconds": 1,
                    "status": "BLOCKED_NO_CLMM_PRE_ENTRY_QUOTE_STATE",
                    "pnl_sol": None,
                    "roi": None,
                    "sample_size": len(anchors),
                    "controls_attached": False,
                    "reason": "Exact CLMM transitions have post-state only; hypothetical fixed-notional quote requires pre-entry tick arrays/liquidity and route-specific exit quoting.",
                })
    return {
        "schema": "WALLET_RAYDIUM_CLMM_CAUSAL_ECONOMICS_SURFACE_V1",
        "venue": "RAYDIUM_CLMM",
        "read_only": True,
        "entry_delay_seconds": 1,
        "notionals_sol": list(NOTIONALS),
        "horizons_seconds": list(HORIZONS),
        "stress_bps": list(STRESSES),
        "windows_loaded": len(anchors),
        "market_transactions_loaded": len(markets),
        "observed_transition_rows": observed,
        "surface": surface,
        "controls_attached": False,
        "policy_frozen": False,
        "trading_enabled": False,
        "promotion_eligible": False,
        "windows_sha256": sha256_file(path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.windows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "RAYDIUM_CLMM_CAUSAL_SURFACE_COMPLETE", "windows_loaded": result["windows_loaded"], "blocked_rows": len(result["surface"])}, sort_keys=True))


if __name__ == "__main__":
    main()
