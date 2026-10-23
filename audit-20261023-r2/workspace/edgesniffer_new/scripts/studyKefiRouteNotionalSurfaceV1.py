#!/usr/bin/env python3
"""Discovery-only KEFI route economics at realistic notionals.

This study consumes only KEFI-proven route events and an exact PumpSwap tape.
It uses a causal +1 second entry delay, exact pool fees, constant-product
notional impact, explicit extra slippage stress, missing-entry/exit failures,
fixed exits, and preregistered dynamic-exit hypotheses.  It never freezes a
policy and never uses the fresh validation collector for selection.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kefi_frozen_pumpswap_selection_economics_v1 import quote_buy_exact_quote_in, quote_sell_exact_base


WSOL = "So11111111111111111111111111111111111111112"
ENTRY_DELAY_NS = 1_000_000_000
HORIZONS_SECONDS = (5, 10, 15, 30, 60, 120, 300)
SIZES = (100_000_000, 500_000_000, 1_000_000_000)
SLIPPAGE_BPS = (0, 25, 50)
LOOKBACK_NS = 30_000_000_000
DISCOVERY_POLICIES = (
    "FIXED_5S", "FIXED_10S", "FIXED_15S", "FIXED_30S", "FIXED_60S", "FIXED_120S", "FIXED_300S",
    "TP100", "TP200", "PROTECT100_0", "PARTIAL50_TP100", "PARTIAL50_TP200",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def row_time(row: dict[str, Any]) -> int:
    return int(row.get("received_at_unix_ns") or 0)


def row_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("pool") or ""), str(row.get("mint") or "")


def usable(row: dict[str, Any]) -> bool:
    return bool(
        row.get("exact_transition_validated") is True
        and row.get("quote_mint") == WSOL
        and row.get("pool")
        and row.get("mint")
        and row_time(row) > 0
    )


def pre_state(row: dict[str, Any]) -> dict[str, int]:
    return {
        "pool_base_raw": int(row["pool_base_raw"]) + int(row["base_amount"]),
        "pool_quote_raw": int(row["pool_quote_raw"]) - int(row["pool_quote_delta"]),
        "virtual_quote_reserves_raw": int(row.get("virtual_quote_reserves") or 0),
    }


def mark_state(row: dict[str, Any]) -> dict[str, int]:
    return {
        "pool_base_raw": int(row["pool_base_raw"]),
        "pool_quote_raw": int(row["pool_quote_raw"]),
        "virtual_quote_reserves_raw": int(row.get("virtual_quote_reserves") or 0),
    }


def evaluator_row(row: dict[str, Any]) -> dict[str, Any]:
    value = dict(row)
    value["virtual_quote_reserves_raw"] = int(row.get("virtual_quote_reserves") or 0)
    return value


def stress_tokens(tokens: int, slippage_bps: int) -> int:
    return tokens * (10_000 - slippage_bps) // 10_000


def quote_mark(token_out: int, row: dict[str, Any], notional: int, slippage_bps: int) -> dict[str, Any]:
    stressed = stress_tokens(token_out, slippage_bps)
    if stressed <= 0:
        return {"pnl_lamports": -notional, "fee_lamports": 0, "net_out_lamports": 0, "roi_bps": -10_000.0}
    sell = quote_sell_exact_base(stressed, mark_state(row), evaluator_row(row))
    net = int(sell["net_quote_out"])
    pnl = net - notional
    return {
        "pnl_lamports": pnl,
        "fee_lamports": int(sell["fee_total"]),
        "net_out_lamports": net,
        "roi_bps": pnl * 10_000 / notional,
    }


def grouped_tape(tape: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in tape:
        if usable(row):
            groups[row_key(row)].append(row)
    for rows in groups.values():
        rows.sort(key=lambda row: (row_time(row), str(row.get("source_frame_sha256")), int(row.get("event_index") or 0)))
    return groups


def event_signal_time(event: dict[str, Any]) -> int:
    if event.get("signal_time_ns") is not None:
        return int(event["signal_time_ns"])
    return int(event.get("block_time") or 0) * 1_000_000_000 + int(event.get("event_index") or 0)


def first_at_or_after(rows: list[dict[str, Any]], target: int) -> dict[str, Any] | None:
    times = [row_time(row) for row in rows]
    index = bisect.bisect_left(times, target)
    return rows[index] if index < len(rows) else None


def points_between(rows: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    times = [row_time(row) for row in rows]
    left = bisect.bisect_left(times, start)
    right = bisect.bisect_right(times, end)
    return rows[left:right]


def entry_for_event(event: dict[str, Any], groups: dict[tuple[str, str], list[dict[str, Any]]]) -> tuple[dict[str, Any] | None, str | None]:
    rows = groups.get((str(event.get("pool") or ""), str(event.get("mint") or "")), [])
    if not rows:
        return None, "NO_TAPE_PATH"
    entry = first_at_or_after(rows, event_signal_time(event) + ENTRY_DELAY_NS)
    return (entry, None) if entry else (None, "NO_POST_DELAY_ENTRY_STATE")


def buy_quote(entry: dict[str, Any], notional: int, slippage_bps: int) -> dict[str, Any]:
    buy = quote_buy_exact_quote_in(notional, pre_state(entry), evaluator_row(entry))
    spot_price = float(buy["effective_quote_reserve"]) / float(buy["pre_pool_base"])
    execution_price = float(buy["curve_quote_in"]) / float(buy["token_out"])
    return {
        "token_out": stress_tokens(int(buy["token_out"]), slippage_bps),
        "entry_fee_lamports": int(buy["fee_total"]),
        "entry_price_impact_bps": (execution_price / spot_price - 1) * 10_000 if spot_price else None,
        "entry_liquidity_quote_lamports": int(buy["effective_quote_reserve"]),
    }


def build_trade(event: dict[str, Any], groups: dict[tuple[str, str], list[dict[str, Any]]], notional: int, slippage_bps: int) -> dict[str, Any]:
    entry, failure = entry_for_event(event, groups)
    base = {"signature": event.get("signature"), "route": event.get("route"), "mint": event.get("mint"), "pool": event.get("pool"), "failure": failure}
    if entry is None:
        return base
    try:
        buy = buy_quote(entry, notional, slippage_bps)
    except Exception as error:
        return {**base, "failure": f"ENTRY_QUOTE:{type(error).__name__}:{error}"}
    rows = groups[(str(event.get("pool")), str(event.get("mint")))]
    entry_ns = row_time(entry)
    marks = points_between(rows, entry_ns, entry_ns + 300 * 1_000_000_000)
    if not marks:
        return {**base, "failure": "NO_POST_ENTRY_MARKS", "entry_delay_seconds": (entry_ns - event_signal_time(event)) / 1_000_000_000}
    mark_results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for mark in marks:
        try:
            mark_results.append((mark, quote_mark(int(buy["token_out"]), mark, notional, slippage_bps)))
        except Exception:
            continue
    if not mark_results:
        return {**base, "failure": "NO_QUOTABLE_MARKS", "entry_delay_seconds": (entry_ns - event_signal_time(event)) / 1_000_000_000}
    return {
        **base,
        "failure": None,
        "entry_delay_seconds": (entry_ns - event_signal_time(event)) / 1_000_000_000,
        "entry_time_ns": entry_ns,
        "entry": entry,
        "buy": buy,
        "marks": mark_results,
    }


def fixed_result(trade: dict[str, Any], seconds: int) -> dict[str, Any] | None:
    if trade.get("failure"):
        return None
    target = int(trade["entry_time_ns"]) + seconds * 1_000_000_000
    candidates = [(row, result) for row, result in trade["marks"] if row_time(row) >= target]
    if not candidates:
        return None
    row, result = candidates[0]
    window = [value for mark, value in trade["marks"] if row_time(mark) <= row_time(row)]
    return {
        **result,
        "exit_time_ns": row_time(row),
        "exit_horizon_seconds": seconds,
        "mfe_lamports": max(int(value["pnl_lamports"]) for value in window),
        "mae_lamports": min(int(value["pnl_lamports"]) for value in window),
    }


def dynamic_result(trade: dict[str, Any], policy: str) -> dict[str, Any] | None:
    if trade.get("failure"):
        return None
    marks = trade["marks"]
    if policy.startswith("TP"):
        threshold = int(policy[2:])
        for row, result in marks:
            if result["roi_bps"] >= threshold:
                return {**result, "exit_time_ns": row_time(row), "dynamic_trigger": policy}
        return fixed_result(trade, 300)
    if policy == "PROTECT100_0":
        armed = False
        for row, result in marks:
            if result["roi_bps"] >= 100:
                armed = True
            if armed and result["roi_bps"] <= 0:
                return {**result, "exit_time_ns": row_time(row), "dynamic_trigger": policy}
        return fixed_result(trade, 300)
    if policy.startswith("PARTIAL50_TP"):
        threshold = int(policy.replace("PARTIAL50_TP", ""))
        trigger = next(((row, result) for row, result in marks if result["roi_bps"] >= threshold), None)
        fallback = fixed_result(trade, 30)
        if trigger is None:
            return fallback
        row, first = trigger
        half = int(trade["buy"]["token_out"]) // 2
        try:
            first_half = quote_mark(half, row, trade["notional"], trade["slippage_bps"])
            second_half = quote_mark(int(trade["buy"]["token_out"]) - half, next((m for m, _ in marks if row_time(m) >= int(trade["entry_time_ns"]) + 30_000_000_000), row), trade["notional"], trade["slippage_bps"])
        except Exception:
            return fallback
        combined = {
            "pnl_lamports": int(first_half["net_out_lamports"]) + int(second_half["net_out_lamports"]) - trade["notional"],
            "fee_lamports": int(first_half["fee_lamports"]) + int(second_half["fee_lamports"]),
            "net_out_lamports": int(first_half["net_out_lamports"]) + int(second_half["net_out_lamports"]),
            "roi_bps": (int(first_half["net_out_lamports"]) + int(second_half["net_out_lamports"]) - trade["notional"]) * 10_000 / trade["notional"],
            "exit_time_ns": row_time(row),
            "dynamic_trigger": policy,
        }
        return combined
    raise ValueError(policy)


def decorate_result(trade: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    exit_time = int(result.get("exit_time_ns") or 0)
    window = [value for mark, value in trade["marks"] if row_time(mark) <= exit_time]
    return {
        **result,
        "entry_delay_seconds": trade.get("entry_delay_seconds"),
        "entry_fee_lamports": trade.get("buy", {}).get("entry_fee_lamports", 0),
        "entry_price_impact_bps": trade.get("buy", {}).get("entry_price_impact_bps"),
        "mfe_lamports": max(int(value["pnl_lamports"]) for value in window) if window else int(result["pnl_lamports"]),
        "mae_lamports": min(int(value["pnl_lamports"]) for value in window) if window else int(result["pnl_lamports"]),
    }


def summarize(results: list[dict[str, Any]], attempted: int, notional: int) -> dict[str, Any]:
    pnls = [int(row["pnl_lamports"]) for row in results]
    gains = sum(value for value in pnls if value > 0)
    losses = -sum(value for value in pnls if value < 0)
    running = peak = max_drawdown = 0
    for value in pnls:
        running += value
        peak = max(peak, running)
        max_drawdown = min(max_drawdown, running - peak)
    return {
        "attempted": attempted,
        "actionable": len(results),
        "failed_or_missed": attempted - len(results),
        "notional_sol": notional / 1_000_000_000,
        "pnl_sol": sum(pnls) / 1_000_000_000,
        "roi_bps_mean": statistics.fmean(float(row["roi_bps"]) for row in results) if results else None,
        "profit_factor": gains / losses if losses else None,
        "win_rate_pct": 100 * sum(value > 0 for value in pnls) / len(pnls) if pnls else None,
        "max_drawdown_sol": max_drawdown / 1_000_000_000,
        "mfe_sol": sum(float(row.get("mfe_lamports", 0)) for row in results) / 1_000_000_000 if results else None,
        "mae_sol": sum(float(row.get("mae_lamports", 0)) for row in results) / 1_000_000_000 if results else None,
        "mean_total_pool_fees_sol": statistics.fmean(int(row.get("fee_lamports", 0)) + int(row.get("entry_fee_lamports", 0)) for row in results) / 1_000_000_000 if results else None,
        "mean_entry_delay_seconds": statistics.fmean(float(row["entry_delay_seconds"]) for row in results) if results else None,
        "mean_entry_price_impact_bps": statistics.fmean(float(row["entry_price_impact_bps"]) for row in results if row.get("entry_price_impact_bps") is not None) if any(row.get("entry_price_impact_bps") is not None for row in results) else None,
    }


def matched_control_events(selected: list[dict[str, Any]], tape: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = grouped_tape(tape)
    used: set[tuple[str, int]] = set()
    controls: list[dict[str, Any]] = []
    for event in sorted(selected, key=event_signal_time):
        rows = groups.get((str(event.get("pool") or ""), str(event.get("mint") or "")), [])
        times = [row_time(row) for row in rows]
        signal = event_signal_time(event)
        left = bisect.bisect_left(times, signal - LOOKBACK_NS)
        right = bisect.bisect_left(times, signal)
        candidates = [
            row for row in rows[left:right]
            if not row.get("is_tracked_teacher") and row.get("side") == "BUY"
        ]
        if not candidates:
            continue
        control = max(candidates, key=row_time)
        identity = (str(control.get("source_frame_sha256")), int(control.get("event_index") or 0))
        if identity in used:
            continue
        used.add(identity)
        controls.append({
            "signature": control.get("candidate_signature"),
            "block_time": row_time(control) // 1_000_000_000,
            "signal_time_ns": row_time(control),
            "event_index": int(control.get("event_index") or 0),
            "route": "VENUE_LEVEL_CONTROL",
            "route_certainty": "EXACT_PUMPSWAP_VENUE_ONLY",
            "side": "BUY",
            "mint": control.get("mint"),
            "pool": control.get("pool"),
            "source_frame_sha256": control.get("source_frame_sha256"),
        })
    return controls


def run_surface(events: list[dict[str, Any]], tape: list[dict[str, Any]], route_name: str, selected_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    selected = selected_events if selected_events is not None else [event for event in events if event.get("route") == route_name and event.get("side") == "BUY"]
    groups = grouped_tape(tape)
    output: dict[str, Any] = {"route": route_name, "selected_buy_events": len(selected), "sizes": {}}
    for notional in SIZES:
        size_key = f"{notional / 1_000_000_000:.1f}"
        output["sizes"][size_key] = {"fixed": [], "dynamic": []}
        for slippage in SLIPPAGE_BPS:
            trades = []
            for event in selected:
                trade = build_trade(event, groups, notional, slippage)
                trade["notional"] = notional
                trade["slippage_bps"] = slippage
                trades.append(trade)
            for horizon in HORIZONS_SECONDS:
                results = []
                for trade in trades:
                    result = fixed_result(trade, horizon)
                    if result is None:
                        continue
                    results.append(decorate_result(trade, result))
                output["sizes"][size_key]["fixed"].append({"policy": f"FIXED_{horizon}S", "slippage_bps": slippage, **summarize(results, len(trades), notional)})
            for policy in DISCOVERY_POLICIES[7:]:
                results = []
                for trade in trades:
                    result = dynamic_result(trade, policy)
                    if result is None:
                        continue
                    results.append(decorate_result(trade, result))
                output["sizes"][size_key]["dynamic"].append({"policy": policy, "slippage_bps": slippage, **summarize(results, len(trades), notional)})
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--tape", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    events = load_jsonl(args.events)
    tape = load_jsonl(args.tape)
    routes = sorted({str(row.get("route")) for row in events if row.get("side") == "BUY"})
    report = {
        "schema": "KEFI_ROUTE_NOTIONAL_SURFACE_DISCOVERY_V1",
        "read_only": True,
        "outcome_source": "historical_discovery_only",
        "policy_status": "hypotheses_only; no policy selected or frozen",
        "entry_delay_seconds": 1,
        "notionals_sol": [0.1, 0.5, 1.0],
        "fixed_exit_seconds": list(HORIZONS_SECONDS),
        "dynamic_hypotheses": list(DISCOVERY_POLICIES[7:]),
        "slippage_stress_bps": list(SLIPPAGE_BPS),
        "cost_model": {
            "pool_fees": "exact PumpSwap observed fee fields",
            "price_impact": "exact constant-product quote at each notional",
            "extra_slippage": "token-output stress at 0/25/50 bps",
            "failed_trades": "missing post-delay entry or horizon mark counted as failed/missed",
            "network_priority_fees": "not available in sealed evidence; excluded and reported",
            "route_controls": "not selected from this report; venue-level non-KEFI controls are descriptive only",
        },
        "inputs": {
            "events_path": str(args.events),
            "events_sha256": sha256_file(args.events),
            "tape_path": str(args.tape),
            "tape_sha256": sha256_file(args.tape),
            "tape_rows": len(tape),
            "event_rows": len(events),
        },
        "routes": {},
    }
    for route in routes:
        selected = [event for event in events if event.get("route") == route and event.get("side") == "BUY"]
        controls = matched_control_events(selected, tape)
        report["routes"][route] = {
            "selected": run_surface(events, tape, route, selected),
            "matched_venue_level_controls": len(controls),
            "controls_route_certainty": "VENUE_ONLY_NOT_ROUTE_PROVEN",
            "controls": run_surface(controls, tape, "VENUE_LEVEL_CONTROL", controls),
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "KEFI_ROUTE_NOTIONAL_SURFACE_COMPLETE_DISCOVERY_ONLY", "routes": routes}, sort_keys=True))


if __name__ == "__main__":
    main()
