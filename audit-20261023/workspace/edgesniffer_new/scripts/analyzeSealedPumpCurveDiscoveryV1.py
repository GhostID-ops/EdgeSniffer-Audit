#!/usr/bin/env python3
"""Summarize a sealed Pump-curve surface for hypothesis generation only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sealed-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--minimum-actionable", type=int, default=30)
    args = parser.parse_args()
    manifest = json.loads((args.sealed_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    surface = json.loads(next(args.sealed_dir.glob("*economics-surface*.json")).read_text(encoding="utf-8"))
    controls = json.loads(next(args.sealed_dir.glob("*controls*.json")).read_text(encoding="utf-8"))
    rows = []
    for key, value in surface.get("surface", {}).items():
        if "|" not in key or not isinstance(value, dict):
            continue
        route, notional, stress, horizon = key.split("|")
        rows.append({
            "route": route, "notional_sol": float(notional), "stress_bps": int(stress), "horizon_seconds": int(horizon),
            "actionable": int(value.get("actionable") or 0), "attempted": int(value.get("attempted") or 0),
            "pnl_sol": value.get("pnl_sol"), "roi_on_actionable": value.get("roi_on_actionable"),
            "profit_factor": value.get("profit_factor"), "win_rate": value.get("win_rate"),
            "max_drawdown_sol": value.get("max_drawdown_sol"), "missed_or_failed": int(value.get("missed_or_failed") or 0),
        })
    eligible = [row for row in rows if row["actionable"] >= args.minimum_actionable and row["pnl_sol"] is not None]
    descriptive = sorted(eligible, key=lambda row: (float(row["pnl_sol"]), float(row.get("profit_factor") or -1)), reverse=True)[:20]
    by_route = {}
    for route in sorted({row["route"] for row in rows}):
        route_rows = [row for row in rows if row["route"] == route]
        by_route[route] = {
            "surface_rows": len(route_rows),
            "max_actionable": max((row["actionable"] for row in route_rows), default=0),
            "best_descriptive_row": next((row for row in descriptive if row["route"] == route), None),
        }
    result = {
        "schema": "WALLET_PUMP_CURVE_SEALED_DISCOVERY_ANALYSIS_V1",
        "sealed_manifest_sha256": sha256(args.sealed_dir / "MANIFEST.json"),
        "source_surface_sha256": sha256(next(args.sealed_dir.glob("*economics-surface*.json"))),
        "read_only": True, "exploratory_only": True, "policy_frozen": False, "trading_enabled": False,
        "promotion_eligible": False, "minimum_actionable_for_descriptive_table": args.minimum_actionable,
        "controls": {"count": int(controls.get("controls") or 0), "status": controls.get("status"), "attached_to_surface": False},
        "by_route": by_route,
        "descriptive_top_rows_not_a_policy_selection": descriptive,
        "next_gate": "Pre-register one route-specific hypothesis, then collect a fresh prospective cohort with unchanged rules and independently attached controls.",
        "manifest": manifest,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SEALED_PUMP_CURVE_DISCOVERY_ANALYSIS_COMPLETE", "surface_rows": len(rows), "descriptive_rows": len(descriptive), "policy_frozen": False}, sort_keys=True))


if __name__ == "__main__":
    main()
