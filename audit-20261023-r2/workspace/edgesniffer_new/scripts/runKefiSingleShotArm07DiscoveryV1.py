#!/usr/bin/env python3
"""Run the bounded single-shot discovery surface on ARM07 causal evidence.

This is a read-only discovery consumer. It does not select, freeze, or
promote a policy. The same contract can be applied to a future arm after its
venue-native causal adapter produces genuine admissions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kefi_frozen_pumpswap_selection_economics_v1 import quote_buy_exact_quote_in, quote_sell_exact_base
from studyKefiArm07CausalQuoteParityV1 import (
    BASE_HORIZON_NS,
    ENTRY_DELAY_NS,
    MAX_HORIZON_NS,
    first_at_or_after,
    load_json,
    load_jsonl,
    path_for_entry,
    pre_state,
    quote_row,
    selected_observations,
    sha256_file,
    source_key,
    stress_token_out,
    usable_tape_row,
)

SIZES = (100_000_000, 500_000_000, 1_000_000_000)
HORIZONS_SECONDS = (5, 10, 15, 30, 60, 120, 300)
POLICIES = ("TP100", "TP200", "PROTECT100_0", "PARTIAL50_TP100", "PARTIAL50_TP200")


def build_entries(observations: list[dict[str, Any]], tape: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    usable = sorted([row for row in tape if usable_tape_row(row)], key=source_key)
    entries: list[dict[str, Any]] = []
    failures: Counter[str] = Counter()
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
        entries.append({
            "observation_id": observation["observation_id"],
            "entry": entry,
            "path": path_for_entry(usable, entry),
            "entry_delay_seconds": (int(entry["received_at_unix_ns"]) - signal_time) / 1_000_000_000,
        })
    return entries, dict(failures)


def sell_mark(token_out: int, row: dict[str, Any], notional: int, slippage_bps: int) -> tuple[int, int]:
    stressed = stress_token_out(token_out, slippage_bps)
    if stressed <= 0:
        return -notional, 0
    sell = quote_sell_exact_base(stressed, pre_state(row), quote_row(row))
    return int(sell["net_quote_out"]) - notional, int(sell["fee_total"])


def entry_context(entry: dict[str, Any], notional: int) -> tuple[dict[str, Any], dict[str, Any]]:
    buy = quote_buy_exact_quote_in(notional, pre_state(entry), quote_row(entry))
    impact = (
        int(buy["curve_quote_in"]) / int(buy["token_out"])
    ) / (
        int(buy["effective_quote_reserve"]) / int(buy["pre_pool_base"])
    ) - 1
    return buy, {
        "entry_fee_bps": int(buy["fee_total"]) * 10_000 / notional,
        "entry_effective_quote_sol": int(buy["effective_quote_reserve"]) / 1_000_000_000,
        "entry_notional_to_liquidity_bps": notional * 10_000 / int(buy["effective_quote_reserve"]),
        "entry_amm_price_impact_bps": impact * 10_000,
    }


def summary(rows: list[dict[str, Any]], notional: int, failed: int, controls_available: bool = False) -> dict[str, Any]:
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
        "profit_factor": gains / losses if losses else None,
        "mean_roi_bps": statistics.fmean(float(row["roi_bps"]) for row in rows) if rows else None,
        "mean_total_pool_fees_sol": statistics.fmean(int(row["entry_fee_lamports"]) + int(row["exit_fee_lamports"]) for row in rows) / 1_000_000_000 if rows else None,
        "mean_entry_amm_price_impact_bps": statistics.fmean(float(row["entry_amm_price_impact_bps"]) for row in rows) if rows else None,
        "mean_entry_notional_to_liquidity_bps": statistics.fmean(float(row["entry_notional_to_liquidity_bps"]) for row in rows) if rows else None,
        "controls_available": controls_available,
    }


def fixed_surface(entries: list[dict[str, Any]], notional: int, horizon_seconds: int, slippage_bps: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    failed = 0
    for item in entries:
        entry = item["entry"]
        row = first_at_or_after(item["path"], int(entry["received_at_unix_ns"]) + horizon_seconds * 1_000_000_000)
        if row is None:
            failed += 1
            continue
        buy, context = entry_context(entry, notional)
        pnl, fee = sell_mark(int(buy["token_out"]), row, notional, slippage_bps)
        rows.append({
            **context,
            "pnl_lamports": pnl,
            "roi_bps": pnl * 10_000 / notional,
            "entry_fee_lamports": int(buy["fee_total"]),
            "exit_fee_lamports": fee,
        })
    return {"policy": f"FIXED_{horizon_seconds}S", "slippage_bps": slippage_bps, **summary(rows, notional, failed)}


def dynamic_surface(entries: list[dict[str, Any]], notional: int, policy: str, slippage_bps: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    failed = 0
    for item in entries:
        entry = item["entry"]
        path = item["path"]
        dynamic = [row for row in path if int(row["received_at_unix_ns"]) <= int(entry["received_at_unix_ns"]) + MAX_HORIZON_NS]
        buy, context = entry_context(entry, notional)
        token_out = int(buy["token_out"])
        selected: tuple[int, int] | None = None
        if policy in {"TP100", "TP200"}:
            threshold = 100 if policy == "TP100" else 200
            for mark in dynamic:
                pnl, fee = sell_mark(token_out, mark, notional, slippage_bps)
                if pnl * 10_000 / notional >= threshold:
                    selected = (pnl, fee)
                    break
        elif policy == "PROTECT100_0":
            armed = False
            for mark in dynamic:
                pnl, fee = sell_mark(token_out, mark, notional, slippage_bps)
                roi = pnl * 10_000 / notional
                if roi >= 100:
                    armed = True
                if armed and roi <= 0:
                    selected = (pnl, fee)
                    break
        else:
            threshold = 100 if policy == "PARTIAL50_TP100" else 200
            fixed = first_at_or_after(path, int(entry["received_at_unix_ns"]) + 30 * 1_000_000_000)
            if fixed is None:
                failed += 1
                continue
            trigger = None
            for mark in dynamic:
                if int(mark["received_at_unix_ns"]) > int(fixed["received_at_unix_ns"]):
                    break
                pnl, _ = sell_mark(token_out, mark, notional, slippage_bps)
                if pnl * 10_000 / notional >= threshold:
                    trigger = mark
                    break
            if trigger is None:
                selected = sell_mark(token_out, fixed, notional, slippage_bps)
            else:
                first_tokens = token_out // 2
                second_tokens = token_out - first_tokens
                first_pnl, first_fee = sell_mark(first_tokens, trigger, notional, slippage_bps)
                second_pnl, second_fee = sell_mark(second_tokens, fixed, notional, slippage_bps)
                selected = (first_pnl + second_pnl + notional, first_fee + second_fee)
        if selected is None:
            fallback = first_at_or_after(dynamic, int(entry["received_at_unix_ns"]) + MAX_HORIZON_NS)
            if fallback is None:
                failed += 1
                continue
            selected = sell_mark(token_out, fallback, notional, slippage_bps)
        pnl, fee = selected
        rows.append({
            **context,
            "pnl_lamports": pnl,
            "roi_bps": pnl * 10_000 / notional,
            "entry_fee_lamports": int(buy["fee_total"]),
            "exit_fee_lamports": fee,
        })
    return {"policy": policy, "slippage_bps": slippage_bps, **summary(rows, notional, failed)}


def gate(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "HOLD_NO_SELECTION",
        "reason": "Controls are not available in this causal ARM07 surface and prospective validation is required.",
        "minimum_actionable_trades": 50,
        "actionable_trades": result["n"],
        "controls_available": result["controls_available"],
    }


def analyze_cohort(manifest_path: Path, report_path: Path, observations_path: Path, tape_path: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    report = load_json(report_path)
    observations = selected_observations(load_jsonl(observations_path), report)
    entries, failures = build_entries(observations, load_jsonl(tape_path))
    surface: list[dict[str, Any]] = []
    for size in SIZES:
        for bps in (0, 50):
            for horizon in HORIZONS_SECONDS:
                result = fixed_surface(entries, size, horizon, bps)
                surface.append({**result, "selection_gate": gate(result)})
            for policy in POLICIES:
                result = dynamic_surface(entries, size, policy, bps)
                surface.append({**result, "selection_gate": gate(result)})
    return {
        "cohort_id": manifest["cohort_id"],
        "registry_sha256": manifest["registry_sha256"],
        "policies_sha256": manifest["policies_sha256"],
        "manifest_sha256": sha256_file(manifest_path),
        "report_sha256": sha256_file(report_path),
        "observations_sha256": sha256_file(observations_path),
        "tape_sha256": sha256_file(tape_path),
        "selected_observations": len(observations),
        "causal_entries": len(entries),
        "entry_failures": failures,
        "surface": surface,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    for prefix in ("old", "new"):
        parser.add_argument(f"--{prefix}-manifest", type=Path, required=True)
        parser.add_argument(f"--{prefix}-report", type=Path, required=True)
        parser.add_argument(f"--{prefix}-observations", type=Path, required=True)
        parser.add_argument(f"--{prefix}-tape", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    contract = load_json(args.contract)
    output = {
        "schema": "KEFI_SINGLE_SHOT_ARM07_DISCOVERY_SURFACE_V1",
        "read_only": True,
        "arm_id": "ARM07_PUMPSWAP_DIRECT",
        "contract_sha256": sha256_file(args.contract),
        "policy_selection": "DISABLED_DISCOVERY_ONLY",
        "prospective_validation": "REQUIRED",
        "contract_name": contract["hypothesis"]["name"],
        "cohorts": {
            "old": analyze_cohort(args.old_manifest, args.old_report, args.old_observations, args.old_tape),
            "new": analyze_cohort(args.new_manifest, args.new_report, args.new_observations, args.new_tape),
        },
    }
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "SINGLE_SHOT_ARM07_DISCOVERY_COMPLETE_READ_ONLY",
        "old_entries": output["cohorts"]["old"]["causal_entries"],
        "new_entries": output["cohorts"]["new"]["causal_entries"],
        "policy_selection": output["policy_selection"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
