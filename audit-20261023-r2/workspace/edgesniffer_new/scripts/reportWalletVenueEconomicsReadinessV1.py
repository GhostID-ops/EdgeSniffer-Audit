#!/usr/bin/env python3
"""Emit a non-ranking, venue-separated economics readiness report."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path: Path) -> dict:
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return {"records": rows, "controls": sum(1 for row in rows if str(row.get("record_type") or "").startswith("CONTROL_"))}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--pump-curve-surface", type=Path, required=True)
    parser.add_argument("--pump-curve-controls", type=Path, required=True)
    parser.add_argument("--pumpswap-surface", type=Path, required=True)
    parser.add_argument("--pumpswap-controls", type=Path, required=True)
    parser.add_argument("--pumpswap-repair", type=Path, required=True)
    parser.add_argument("--raydium-surface", type=Path, required=True)
    parser.add_argument("--raydium-controls", type=Path, required=True)
    parser.add_argument("--meteora-surface", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    census = load(args.census)
    route_rows = {row["route"]: row for row in census.get("routes") or []}
    pump_surface = load(args.pump_curve_surface)
    pump_controls = load(args.pump_curve_controls)
    swap_surface = load(args.pumpswap_surface)
    swap_controls = load(args.pumpswap_controls)
    swap_repair = load(args.pumpswap_repair)
    raydium_surface = load(args.raydium_surface)
    raydium_controls = load(args.raydium_controls)
    meteora_surface = load(args.meteora_surface)
    pump_buy_count = sum(route_rows.get(route, {}).get("genuine_buys", 0) for route in ("PUMP_BONDING_CURVE_TOP_LEVEL", "PUMP_BONDING_CURVE_CPI"))
    pump_windows_loaded = pump_surface.get("windows_loaded", 0)
    pump_complete = bool(pump_buy_count and pump_windows_loaded >= pump_buy_count)
    surfaces = {
        "PUMP_BONDING_CURVE": {
            "status": "CAUSAL_SURFACE_DISCOVERY_COMPLETE_NO_POLICY_FROZEN" if pump_complete else "CAUSAL_SURFACE_PARTIAL_UNTIL_BACKFILL_COMPLETE",
            "evaluator": pump_surface.get("evaluator"),
            "buy_count": pump_buy_count,
            "windows_loaded": pump_windows_loaded,
            "controls": pump_controls.get("controls"),
            "surface": pump_surface.get("surface"),
            "promotion_eligible": False,
        },
        "PUMPSWAP": {
            "status": "CAUSAL_SURFACE_DISCOVERY_ONLY",
            "evaluator": "EXACT_PUMPSWAP_CASH_FLOW",
            "buy_count": sum(route_rows.get(route, {}).get("genuine_buys", 0) for route in ("PUMPSWAP_DIRECT", "PUMPSWAP_CPI")),
            "windows_loaded": swap_surface.get("windows_loaded"),
            "controls": swap_controls.get("controls", sum(1 for row in swap_controls.get("records", []) if row.get("record_type") == "CONTROL_SAME_SLOT")),
            "control_scope": "bounded same-slot PumpSwap controls; no cross-venue controls",
            "surface": swap_surface.get("surface"),
            "rejected_buy_audit": {"count": swap_repair.get("rejected_buy_count"), "statuses": swap_repair.get("statuses")},
            "promotion_eligible": False,
        },
        "RAYDIUM": {
            "status": "NATIVE_WINDOWS_CAPTURED_BUT_QUOTE_MODEL_BLOCKED",
            "evaluator": "RAYDIUM_CLMM_EXACT_TRANSITION_NO_PRE_ENTRY_QUOTE_STATE",
            "buy_count": sum(row.get("genuine_buys", 0) for name, row in route_rows.items() if name.startswith("RAYDIUM_")),
            "windows_loaded": raydium_surface.get("windows_loaded", 0),
            "market_transactions_loaded": raydium_surface.get("market_transactions_loaded", 0),
            "controls": raydium_controls.get("controls", 0),
            "surface": raydium_surface.get("surface"),
            "promotion_eligible": False,
        },
        "METEORA": {
            "status": meteora_surface.get("blocker", "BLOCKED_NO_CANONICAL_METEORA_DLMM_BUY"),
            "evaluator": "METEORA_DLMM_SWAP2_EXACT_DECODER_REQUIRES_CANONICAL_BUY_ORIENTATION",
            "buy_count": sum(row.get("genuine_buys", 0) for name, row in route_rows.items() if name.startswith("METEORA_")),
            "windows_loaded": meteora_surface.get("windows_loaded", 0),
            "controls": 0,
            "surface": meteora_surface.get("surface"),
            "promotion_eligible": False,
        },
    }
    result = {
        "schema": "WALLET_VENUE_SEPARATED_ECONOMICS_READINESS_V1",
        "read_only": True,
        "policy_frozen": False,
        "trading_enabled": False,
        "cross_venue_ranking": False,
        "inputs": {key: {"path": str(path), "sha256": sha256_file(path)} for key, path in {
            "census": args.census,
            "pump_curve_surface": args.pump_curve_surface,
            "pump_curve_controls": args.pump_curve_controls,
            "pumpswap_surface": args.pumpswap_surface,
            "pumpswap_controls": args.pumpswap_controls,
            "pumpswap_repair": args.pumpswap_repair,
            "raydium_surface": args.raydium_surface,
            "raydium_controls": args.raydium_controls,
            "meteora_surface": args.meteora_surface,
        }.items()},
        "venues": surfaces,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "VENUE_SEPARATED_READINESS_COMPLETE", "venues": {key: value["status"] for key, value in surfaces.items()}}, sort_keys=True))


if __name__ == "__main__":
    main()
