#!/usr/bin/env python3
"""Read-only ARM07 notional surface for 0.1, 0.5, and 1 SOL.

This study is intentionally separate from the frozen 0.1 SOL policy.  It
reuses the causal timestamp/pool fill tier, then quotes each notional against
the same observed entry and exit states.  It is discovery evidence only: no
size or exit policy is selected from these cohorts.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kefi_frozen_pumpswap_selection_economics_v1 import quote_buy_exact_quote_in, quote_sell_exact_base
from studyKefiArm07CausalQuoteParityV1 import (
    BASE_HORIZON_NS,
    ENTRY_DELAY_NS,
    MAX_HORIZON_NS,
    WSOL,
    load_json,
    load_jsonl,
    sha256_file,
    selected_observations,
    usable_tape_row,
    source_key,
    pre_state,
    quote_row,
    path_for_entry,
    first_at_or_after,
)

SIZES = (100_000_000, 500_000_000, 1_000_000_000)


def stress_token_out(token_out: int, slippage_bps: int) -> int:
    return token_out * (10_000 - slippage_bps) // 10_000


def build_trades(observations: list[dict[str, Any]], tape: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    usable = sorted([row for row in tape if usable_tape_row(row)], key=source_key)
    trades: list[dict[str, Any]] = []
    failures = {"NO_USABLE_POST_DELAY_ENTRY_STATE": 0, "NO_USABLE_30S_EXIT_MARK": 0}
    for observation in observations:
        event = observation["state"]["decoded_event"]
        signal_time = int(event["received_at_unix_ns"])
        candidates = [
            row for row in usable
            if row["pool"] == event["pool"] and row["mint"] == event["mint"]
            and int(row["received_at_unix_ns"]) >= signal_time + ENTRY_DELAY_NS
        ]
        if not candidates:
            failures["NO_USABLE_POST_DELAY_ENTRY_STATE"] += 1
            continue
        entry = candidates[0]
        path = path_for_entry(usable, entry)
        exit_row = first_at_or_after(path, int(entry["received_at_unix_ns"]) + BASE_HORIZON_NS)
        if exit_row is None:
            failures["NO_USABLE_30S_EXIT_MARK"] += 1
            continue
        trades.append({
            "observation_id": observation["observation_id"],
            "entry": entry,
            "exit": exit_row,
            "path": path,
            "entry_delay_seconds": (int(entry["received_at_unix_ns"]) - signal_time) / 1_000_000_000,
            "exit_delay_seconds": (int(exit_row["received_at_unix_ns"]) - int(entry["received_at_unix_ns"])) / 1_000_000_000,
        })
    return trades, failures


def quote_sell_pnl(token_out: int, row: dict[str, Any], notional: int, slippage_bps: int) -> tuple[int, int, int]:
    stressed_token = stress_token_out(token_out, slippage_bps)
    if stressed_token <= 0:
        return -notional, 0, 0
    sell = quote_sell_exact_base(stressed_token, pre_state(row), quote_row(row))
    return int(sell["net_quote_out"]) - notional, int(sell["fee_total"]), int(sell["net_quote_out"])


def size_trade(trade: dict[str, Any], notional: int, slippage_bps: int) -> dict[str, Any]:
    entry = trade["entry"]
    exit_row = trade["exit"]
    buy = quote_buy_exact_quote_in(notional, pre_state(entry), quote_row(entry))
    pnl, exit_fee, net_out = quote_sell_pnl(int(buy["token_out"]), exit_row, notional, slippage_bps)
    spot_curve_price = int(buy["effective_quote_reserve"]) / int(buy["pre_pool_base"])
    execution_curve_price = int(buy["curve_quote_in"]) / int(buy["token_out"])
    return {
        "pnl_lamports": pnl,
        "roi_bps": pnl * 10_000 / notional,
        "entry_fee_lamports": int(buy["fee_total"]),
        "exit_fee_lamports": exit_fee,
        "entry_fee_bps": int(buy["fee_total"]) * 10_000 / notional,
        "exit_net_quote_lamports": net_out,
        "entry_effective_quote_sol": int(buy["effective_quote_reserve"]) / 1_000_000_000,
        "entry_notional_to_liquidity_bps": notional * 10_000 / int(buy["effective_quote_reserve"]),
        "entry_amm_price_impact_bps": (execution_curve_price / spot_curve_price - 1) * 10_000,
        "entry_delay_seconds": trade["entry_delay_seconds"],
        "exit_delay_seconds": trade["exit_delay_seconds"],
    }


def summary(rows: list[dict[str, Any]], notional: int, failed: int = 0) -> dict[str, Any]:
    pnls = [int(row["pnl_lamports"]) for row in rows]
    gains = sum(value for value in pnls if value > 0)
    losses = -sum(value for value in pnls if value < 0)
    return {
        "notional_sol": notional / 1_000_000_000,
        "n": len(rows),
        "failed_or_unavailable": failed,
        "wins": sum(value > 0 for value in pnls),
        "win_rate_pct": 100 * sum(value > 0 for value in pnls) / len(pnls) if pnls else None,
        "pnl_sol": sum(pnls) / 1_000_000_000,
        "mean_pnl_sol": statistics.fmean(pnls) / 1_000_000_000 if pnls else None,
        "profit_factor": gains / losses if losses else None,
        "mean_roi_bps": statistics.fmean(float(row["roi_bps"]) for row in rows) if rows else None,
        "mean_total_pool_fees_sol": statistics.fmean(int(row["entry_fee_lamports"]) + int(row["exit_fee_lamports"]) for row in rows) / 1_000_000_000 if rows else None,
        "mean_entry_fee_bps": statistics.fmean(float(row["entry_fee_bps"]) for row in rows) if rows else None,
        "mean_entry_effective_quote_sol": statistics.fmean(float(row["entry_effective_quote_sol"]) for row in rows) if rows else None,
        "mean_entry_notional_to_liquidity_bps": statistics.fmean(float(row["entry_notional_to_liquidity_bps"]) for row in rows) if rows else None,
        "mean_entry_amm_price_impact_bps": statistics.fmean(float(row["entry_amm_price_impact_bps"]) for row in rows) if rows else None,
        "mean_entry_delay_seconds": statistics.fmean(float(row["entry_delay_seconds"]) for row in rows) if rows else None,
        "mean_exit_delay_seconds": statistics.fmean(float(row["exit_delay_seconds"]) for row in rows) if rows else None,
    }


def exit_policy(trade: dict[str, Any], notional: int, policy: str, slippage_bps: int) -> tuple[int, int] | None:
    entry = trade["entry"]
    path = trade["path"]
    entry_time = int(entry["received_at_unix_ns"])
    dynamic_path = [row for row in path if int(row["received_at_unix_ns"]) <= entry_time + MAX_HORIZON_NS]
    fixed_exit_time = int(trade["exit"]["received_at_unix_ns"])
    if policy == "fixed30":
        row = trade["exit"]
        pnl, fee, _ = quote_sell_pnl(int(quote_buy_exact_quote_in(notional, pre_state(entry), quote_row(entry))["token_out"]), row, notional, slippage_bps)
        return pnl, fee
    buy = quote_buy_exact_quote_in(notional, pre_state(entry), quote_row(entry))
    token_out = int(buy["token_out"])
    def mark(row: dict[str, Any]) -> tuple[int, int]:
        pnl, fee, _ = quote_sell_pnl(token_out, row, notional, slippage_bps)
        return pnl, fee
    if policy.startswith("tp_"):
        threshold = int(policy.split("_")[1])
        for row in dynamic_path:
            pnl, fee = mark(row)
            if pnl * 10_000 / notional >= threshold:
                return pnl, fee
        fallback = first_at_or_after(dynamic_path, entry_time + MAX_HORIZON_NS)
        return mark(fallback) if fallback else None
    if policy == "protect_100_0":
        armed = False
        for row in dynamic_path:
            pnl, fee = mark(row)
            if pnl * 10_000 / notional >= 100:
                armed = True
            if armed and pnl * 10_000 / notional <= 0:
                return pnl, fee
        fallback = first_at_or_after(dynamic_path, entry_time + MAX_HORIZON_NS)
        return mark(fallback) if fallback else None
    if policy.startswith("partial_"):
        _, fraction_text, threshold_text = policy.split("_")
        fraction_bps = int(fraction_text)
        threshold = int(threshold_text)
        trigger = None
        for row in dynamic_path:
            if int(row["received_at_unix_ns"]) > fixed_exit_time:
                break
            pnl, _ = mark(row)
            if pnl * 10_000 / notional >= threshold:
                trigger = row
                break
        if trigger is None:
            return mark(trade["exit"])
        first_tokens = token_out * fraction_bps // 10_000
        second_tokens = token_out - first_tokens
        first_pnl, first_fee, first_net = quote_sell_pnl(first_tokens, trigger, notional, slippage_bps)
        second_pnl, second_fee, second_net = quote_sell_pnl(second_tokens, trade["exit"], notional, slippage_bps)
        return first_net + second_net - notional, first_fee + second_fee
    raise ValueError(policy)


def policy_surface(trades: list[dict[str, Any]], notional: int, slippage_bps: int) -> list[dict[str, Any]]:
    policies = ("fixed30", "tp_100", "tp_200", "protect_100_0", "partial_500_100", "partial_500_200")
    output = []
    for policy in policies:
        rows = []
        failed = 0
        for trade in trades:
            result = exit_policy(trade, notional, policy, slippage_bps)
            if result is None:
                failed += 1
                continue
            pnl, fee = result
            base = size_trade(trade, notional, slippage_bps)
            rows.append({**base, "pnl_lamports": pnl, "roi_bps": pnl * 10_000 / notional, "exit_fee_lamports": fee})
        output.append({"policy": policy, "slippage_bps": slippage_bps, **summary(rows, notional, failed)})
    return output


def analyze(manifest_path: Path, report_path: Path, observations_path: Path, tape_path: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    report = load_json(report_path)
    observations = selected_observations(load_jsonl(observations_path), report)
    tape = load_jsonl(tape_path)
    trades, failures = build_trades(observations, tape)
    surfaces = {}
    for size in SIZES:
        size_label = f"{size / 1_000_000_000:.1f}"
        surfaces[size_label] = {
            "fixed30_slippage_surface": [],
            "exit_policy_surface": [],
        }
        for slippage_bps in (0, 25, 50):
            fixed_rows = [size_trade(trade, size, slippage_bps) for trade in trades]
            surfaces[size_label]["fixed30_slippage_surface"].append({"slippage_bps": slippage_bps, **summary(fixed_rows, size)})
            surfaces[size_label]["exit_policy_surface"].extend(policy_surface(trades, size, slippage_bps))
    return {
        "cohort_id": manifest["cohort_id"],
        "cohort_started_at_utc": manifest["cohort_started_at_utc"],
        "registry_sha256": manifest["registry_sha256"],
        "policies_sha256": manifest["policies_sha256"],
        "manifest_sha256": sha256_file(manifest_path),
        "report_sha256": sha256_file(report_path),
        "observations_sha256": sha256_file(observations_path),
        "tape_sha256": sha256_file(tape_path),
        "sealed_selected_observations": len(observations),
        "causal_complete_trades": len(trades),
        "entry_exit_failures": failures,
        "tape_rows_total": len(tape),
        "tape_rows_usable": sum(usable_tape_row(row) for row in tape),
        "size_surface": surfaces,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    for prefix in ("old", "new"):
        parser.add_argument(f"--{prefix}-manifest", type=Path, required=True)
        parser.add_argument(f"--{prefix}-report", type=Path, required=True)
        parser.add_argument(f"--{prefix}-observations", type=Path, required=True)
        parser.add_argument(f"--{prefix}-tape", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = {
        "schema": "KEFI_ARM07_NOTIONAL_SURFACE_V1",
        "read_only": True,
        "notionals_sol": [0.1, 0.5, 1.0],
        "entry_delay_seconds": 1,
        "base_exit_horizon_seconds": 30,
        "maximum_dynamic_horizon_seconds": 300,
        "policy_status": "hypotheses_only; no size or exit policy selected",
        "cost_model": {
            "pool_fees": "exact observed LP/protocol/creator fees",
            "amm_price_impact": "constant-product quote impact at each notional",
            "extra_slippage": "0/25/50 bps token-output stress",
            "network_priority_fees": "not available in evidence; excluded",
            "fills": "observed-state quote only, not submitted transactions",
        },
        "implementation_hashes": {
            relative: sha256_file(Path(__file__).resolve().parents[1] / relative)
            for relative in (
                "scripts/studyKefiArm07NotionalSurfaceV1.py",
                "scripts/studyKefiArm07CausalQuoteParityV1.py",
                "kefi_frozen_pumpswap_selection_economics_v1.py",
            )
        },
        "cohorts": {
            "old": analyze(args.old_manifest, args.old_report, args.old_observations, args.old_tape),
            "new": analyze(args.new_manifest, args.new_report, args.new_observations, args.new_tape),
        },
    }
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "NOTIONAL_SURFACE_COMPLETE_READ_ONLY",
        "old_trades": output["cohorts"]["old"]["causal_complete_trades"],
        "new_trades": output["cohorts"]["new"]["causal_complete_trades"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
