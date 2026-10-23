#!/usr/bin/env python3
"""Search preregistered KEFI route filters over the discovery cohort only.

This is deliberately a hypothesis report.  It may rank candidates for a later
freeze review, but it never writes a frozen policy and never reads the fresh
prospective validation collector.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from studyKefiRouteNotionalSurfaceV1 import (
    ENTRY_DELAY_NS,
    WSOL,
    event_signal_time,
    grouped_tape,
    load_jsonl,
    matched_control_events,
    run_surface,
    row_time,
)


FILTER_NAMES = (
    "BASELINE",
    "MIN_PRE_LIQUIDITY_25_SOL",
    "MIN_PRE_LIQUIDITY_50_SOL",
    "MIN_PRE_LIQUIDITY_100_SOL",
    "MAX_ENTRY_NOTIONAL_LIQUIDITY_25_BPS",
    "MAX_ENTRY_NOTIONAL_LIQUIDITY_50_BPS",
    "MAX_ENTRY_NOTIONAL_LIQUIDITY_100_BPS",
    "NONNEGATIVE_PRE_30S_NET_BUY_FLOW",
    "PRE_30S_NET_BUY_FLOW_AT_LEAST_1_SOL",
)


def enrich_events(events: list[dict[str, Any]], tape: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = grouped_tape(tape)
    enriched: list[dict[str, Any]] = []
    for event in events:
        if event.get("side") != "BUY":
            continue
        rows = groups.get((str(event.get("pool") or ""), str(event.get("mint") or "")), [])
        signal = event_signal_time(event)
        prior = [row for row in rows if row_time(row) < signal and row_time(row) >= signal - 30_000_000_000]
        liquidity_raw = int((event.get("pre_state") or {}).get("quote") or 0)
        notional_raw = int(event.get("user_quote_amount_raw") or event.get("curve_quote_amount_raw") or 0)
        flow_raw = 0
        for row in prior:
            delta = int(row.get("pool_quote_delta") or 0)
            flow_raw += delta if row.get("side") == "BUY" else -delta
        value = dict(event)
        value["features"] = {
            "pre_liquidity_sol": liquidity_raw / 1_000_000_000,
            "entry_notional_liquidity_bps": notional_raw * 10_000 / liquidity_raw if liquidity_raw else None,
            "pre_30s_net_buy_flow_sol": flow_raw / 1_000_000_000,
            "pre_30s_observed_events": len(prior),
            "entry_delay_contract_seconds": ENTRY_DELAY_NS / 1_000_000_000,
        }
        enriched.append(value)
    return enriched


def passes(event: dict[str, Any], filter_name: str) -> bool:
    features = event.get("features") or {}
    if filter_name == "BASELINE":
        return True
    if filter_name.startswith("MIN_PRE_LIQUIDITY_"):
        threshold = float(filter_name.split("_")[-2])
        return float(features.get("pre_liquidity_sol") or 0) >= threshold
    if filter_name.startswith("MAX_ENTRY_NOTIONAL_LIQUIDITY_"):
        threshold = float(filter_name.split("_")[-2])
        value = features.get("entry_notional_liquidity_bps")
        return value is not None and float(value) <= threshold
    if filter_name == "NONNEGATIVE_PRE_30S_NET_BUY_FLOW":
        return float(features.get("pre_30s_net_buy_flow_sol") or 0) >= 0
    if filter_name == "PRE_30S_NET_BUY_FLOW_AT_LEAST_1_SOL":
        return float(features.get("pre_30s_net_buy_flow_sol") or 0) >= 1
    raise ValueError(filter_name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--tape", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    events = enrich_events(load_jsonl(args.events), load_jsonl(args.tape))
    tape = load_jsonl(args.tape)
    routes = sorted({str(row.get("route")) for row in events})
    report: dict[str, Any] = {
        "schema": "KEFI_ROUTE_ENTRY_EXIT_HYPOTHESIS_SEARCH_V1",
        "read_only": True,
        "outcome_source": "historical_discovery_only",
        "filters_preregistered": list(FILTER_NAMES),
        "policy_status": "hypotheses_only; no policy selected or frozen",
        "selection_rule": "results are ranked descriptively; no candidate is promoted by this report",
        "routes": {},
    }
    for route in routes:
        report["routes"][route] = {}
        for filter_name in FILTER_NAMES:
            selected = [row for row in events if row.get("route") == route and passes(row, filter_name)]
            controls = matched_control_events(selected, tape)
            report["routes"][route][filter_name] = {
                "selected_events": len(selected),
                "feature_summary": {
                    "min_pre_liquidity_sol": min((float(row["features"]["pre_liquidity_sol"]) for row in selected), default=None),
                    "median_pre_liquidity_sol": sorted(float(row["features"]["pre_liquidity_sol"]) for row in selected)[len(selected) // 2] if selected else None,
                    "min_pre_30s_net_buy_flow_sol": min((float(row["features"]["pre_30s_net_buy_flow_sol"]) for row in selected), default=None),
                },
                "selected_surface": run_surface(events, tape, route, selected),
                "matched_venue_level_controls": len(controls),
                "controls_route_certainty": "VENUE_ONLY_NOT_ROUTE_PROVEN",
                "control_surface": run_surface(controls, tape, "VENUE_LEVEL_CONTROL", controls),
            }
    ranked: list[dict[str, Any]] = []
    for route, filters in report["routes"].items():
        for filter_name, result in filters.items():
            candidate = next((row for row in result["selected_surface"]["sizes"]["0.5"]["fixed"] if row["policy"] == "FIXED_30S" and row["slippage_bps"] == 25), None)
            if candidate:
                ranked.append({
                    "route": route,
                    "filter": filter_name,
                    "selected_events": result["selected_events"],
                    "actionable": candidate["actionable"],
                    "roi_bps_mean": candidate["roi_bps_mean"],
                    "profit_factor": candidate["profit_factor"],
                    "pnl_sol": candidate["pnl_sol"],
                    "controls": result["matched_venue_level_controls"],
                })
    ranked.sort(key=lambda row: (row["roi_bps_mean"] is None, -(row["roi_bps_mean"] or -1e18)))
    report["descriptive_rank_fixed30_0.5sol_25bps"] = ranked
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "KEFI_ENTRY_EXIT_HYPOTHESIS_SEARCH_COMPLETE", "routes": routes, "filters": len(FILTER_NAMES)}, sort_keys=True))


if __name__ == "__main__":
    main()
