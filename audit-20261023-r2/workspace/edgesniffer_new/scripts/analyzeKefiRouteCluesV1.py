#!/usr/bin/env python3
"""Descriptive, outcome-blind KEFI route clue audit.

This report is exploratory only.  It describes the sealed KEFI route event
inventory and execution context; it does not search thresholds, select a
policy, freeze a rule, or read the prospective validation lane.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "KEFI_ROUTE_CLUE_AUDIT_V1"
LAMPORTS_PER_SOL = 1_000_000_000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def event_time_ns(row: dict[str, Any]) -> int:
    return int(row.get("block_time") or 0) * 1_000_000_000 + int(row.get("event_index") or 0)


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "p25": None, "median": None, "p75": None, "p90": None, "max": None}
    values = sorted(values)
    def at(percentile: float) -> float:
        index = min(len(values) - 1, int((len(values) - 1) * percentile))
        return values[index]
    return {"min": values[0], "p25": at(0.25), "median": statistics.median(values), "p75": at(0.75), "p90": at(0.90), "max": values[-1]}


def route_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("route"))].append(row)
    output: dict[str, Any] = {}
    for route, items in sorted(grouped.items()):
        items.sort(key=event_time_ns)
        buys = [row for row in items if row.get("side") == "BUY"]
        sells = [row for row in items if row.get("side") == "SELL"]
        buy_sizes = [int(row.get("user_quote_amount_raw") or 0) / LAMPORTS_PER_SOL for row in buys]
        buy_liquidity = [int((row.get("pre_state") or {}).get("quote") or 0) / LAMPORTS_PER_SOL for row in buys]
        buy_virtual_liquidity = [int((row.get("pre_state") or {}).get("virtual_quote") or 0) / LAMPORTS_PER_SOL for row in buys]
        fee_bps = []
        for row in buys:
            fees = row.get("fees") or {}
            fee_bps.append(sum(int(fees.get(key) or 0) for key in ("lp_bps", "protocol_bps", "creator_bps")))
        holdings: list[float] = []
        completed = 0
        per_asset: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in items:
            per_asset[(str(row.get("pool")), str(row.get("mint")))].append(row)
        for buy in buys:
            later_sells = [
                row for row in per_asset[(str(buy.get("pool")), str(buy.get("mint")))]
                if row.get("side") == "SELL" and event_time_ns(row) > event_time_ns(buy)
            ]
            if later_sells:
                holdings.append((event_time_ns(later_sells[0]) - event_time_ns(buy)) / 1_000_000_000)
                completed += 1
        output[route] = {
            "events": len(items),
            "buys": len(buys),
            "sells": len(sells),
            "unique_pools": len({str(row.get("pool")) for row in items}),
            "unique_mints": len({str(row.get("mint")) for row in items}),
            "buy_quote_size_sol": quantiles(buy_sizes),
            "buy_pre_pool_quote_liquidity_sol": quantiles(buy_liquidity),
            "buy_pre_virtual_quote_liquidity_sol": quantiles(buy_virtual_liquidity),
            "buy_total_fee_bps": quantiles([float(value) for value in fee_bps]),
            "buy_to_first_sell": {
                "buys_with_later_sell": completed,
                "completion_rate": completed / len(buys) if buys else 0.0,
                "holding_seconds": quantiles(holdings),
            },
            "buy_size_as_percent_of_pre_pool_quote": quantiles([
                size / liquidity * 100.0 for size, liquidity in zip(buy_sizes, buy_liquidity) if liquidity > 0
            ]),
            "hour_utc_counts": dict(Counter(datetime.fromtimestamp(event_time_ns(row) / 1_000_000_000, timezone.utc).hour for row in buys)),
        }
    return output


def route_sequence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buys = sorted((row for row in rows if row.get("side") == "BUY"), key=event_time_ns)
    transitions = Counter()
    for previous, current in zip(buys, buys[1:]):
        transitions[f"{previous.get('route')}->{current.get('route')}"] += 1
    return {
        "chronological_buy_events": len(buys),
        "adjacent_route_transitions": dict(transitions),
        "route_switches": sum(value for key, value in transitions.items() if key.split("->")[0] != key.split("->")[1]),
        "interpretation": "Descriptive route/regime sequencing only; no transition is a trading rule.",
    }


def build_report(rows: list[dict[str, Any]], source: Path) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "exploratory_only": True,
        "outcome_blind": True,
        "policy_selection_performed": False,
        "policy_frozen": False,
        "validation_read": False,
        "trading_enabled": False,
        "source_path": str(source),
        "source_sha256": sha256_file(source) if source.exists() else None,
        "input_rows": len(rows),
        "route_stats": route_stats(rows),
        "chronological_route_sequence": route_sequence(rows),
        "clue_interpretation": [
            "Liquidity, fee, size, holding-time, and route-sequencing distributions are descriptive context for later preregistered hypotheses.",
            "A distribution is not evidence that a threshold or exit rule should be selected.",
            "Any formula suggested by this report must be preregistered before a separate discovery run and validated on a fresh prospective cohort.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(load_jsonl(args.events), args.events)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "KEFI_ROUTE_CLUE_AUDIT_COMPLETE", "rows": report["input_rows"], "routes": len(report["route_stats"])}, sort_keys=True))


if __name__ == "__main__":
    main()
