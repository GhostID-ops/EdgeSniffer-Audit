#!/usr/bin/env python3
"""Derive a KEFI-only route registry from the immutable route census."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def route_registry_row(row: dict[str, Any]) -> dict[str, Any]:
    route = str(row["route"])
    identifier = "KEFI_ROUTE_" + route.replace("+", "_").replace("-", "_")
    populated = int(row["genuine_buys"]) > 0
    exact_economics = route in {"PUMPSWAP_DIRECT", "PUMPSWAP_CPI"} and bool(row["liquidity_and_fee_fields"])
    if exact_economics and populated:
        readiness = "DISCOVERY_READY"
    elif int(row["raw_transactions"]) > 0:
        readiness = "COVERAGE_OR_ADAPTER_GAP"
    else:
        readiness = "NO_KEFI_ACTIVITY"
    return {
        "id": identifier,
        "route": route,
        "route_hypothesis": f"KEFI wallet activity on {route}",
        "readiness": readiness,
        "genuine_buys": row["genuine_buys"],
        "genuine_sells": row["genuine_sells"],
        "raw_transactions": row["raw_transactions"],
        "route_certainty": row["route_certainty"],
        "available_pre_entry_state": row["available_pre_entry_state"],
        "liquidity_and_fee_fields": row["liquidity_and_fee_fields"],
        "controls": row["controls"],
        "decoder": "EXACT_CANONICAL_PUMPSWAP_STATE" if "PUMPSWAP" in route else "NOT_CERTIFIED_FOR_KEFI_STRATEGY",
        "evaluator": "EXACT_PUMPSWAP_CASH_FLOW" if exact_economics else "NOT_CERTIFIED_FOR_KEFI_STRATEGY",
        "policy": {
            "status": "NOT_FROZEN",
            "selection_allowed": False,
            "promotion_allowed": False,
        },
        "strategy_search_eligible": bool(row["eligible_for_strategy_search"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    census = json.loads(args.census.read_text(encoding="utf-8"))
    routes = [route_registry_row(row) for row in census["routes"]]
    output = {
        "schema": "KEFI_ONLY_ROUTE_REGISTRY_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "research_only": True,
        "live_trading_enabled": False,
        "source_census_sha256": sha256_file(args.census),
        "wallet": census["tracked_wallet"],
        "legacy_registry_policy": "The mixed-source 13-arm registry is retained only for historical artifact compatibility.",
        "route_count": len(routes),
        "routes": routes,
        "policy_selection_performed": False,
        "promotion_performed": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "KEFI_ONLY_ROUTE_REGISTRY_COMPLETE", "routes": len(routes)}, sort_keys=True))


if __name__ == "__main__":
    main()
