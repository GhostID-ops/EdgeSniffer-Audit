#!/usr/bin/env python3
"""Compare sealed PumpSwap selections with their deterministic controls."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from kefi_frozen_pumpswap_selection_economics_v1 import (
    quote_buy_exact_quote_in,
    quote_sell_exact_base,
    read_tape,
    row_key,
    sha256,
)


SCHEMA = "KEFI_FROZEN_PUMPSWAP_SELECTION_BENCHMARK_V1"


def economics_for(label: str, identity: dict, entry: dict, exit_mark: dict,
                 by_key: dict, quote_in: int) -> dict:
    entry_key = tuple(entry["entry_event_key"])
    exit_key = tuple(exit_mark["mark_event_key"])
    entry_row = by_key[entry_key]
    exit_row = by_key[exit_key]
    if entry_row["mint"] != identity["mint"] or entry_row["pool"] != identity["pool"]:
        raise ValueError("ENTRY_IDENTITY_MISMATCH")
    if exit_row["mint"] != identity["mint"] or exit_row["pool"] != identity["pool"]:
        raise ValueError("EXIT_IDENTITY_MISMATCH")
    buy = quote_buy_exact_quote_in(quote_in, entry["pre_state"], entry_row)
    sell = quote_sell_exact_base(buy["token_out"], exit_mark["pre_state"], exit_row)
    pnl = sell["net_quote_out"] - quote_in
    return {
        "label": label,
        "mint": identity["mint"],
        "pool": identity["pool"],
        "entry_observation_side": entry_row["side"],
        "exit_observation_side": exit_row["side"],
        "entry_event_key": list(entry_key),
        "exit_event_key": list(exit_key),
        "pnl_quote_lamports": pnl,
        "roi_bps": pnl * 10_000 / quote_in,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assignments", type=Path, required=True)
    parser.add_argument("--tape", type=Path, required=True)
    parser.add_argument("--quote-in-lamports", type=int, default=100_000_000)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit("FAIL_CLOSED: benchmark output already exists")
    assignments = json.loads(args.assignments.read_text(encoding="utf-8"))
    rows, checkpoint_hash = read_tape(args.tape)
    by_key = {row_key(row): row for row in rows}
    if len(by_key) != len(rows):
        raise SystemExit("FAIL_CLOSED: duplicate tape key")

    target_results, control_results = [], []
    failures = Counter()
    for assignment in assignments["assignments"]:
        identity = {"mint": assignment["mint"], "pool": assignment["pool"]}
        if assignment["target_status"] == "MARK_AVAILABLE":
            try:
                target_results.append(economics_for(
                    "target", identity, assignment["target_entry"], assignment["target_exit"],
                    by_key, args.quote_in_lamports,
                ))
            except (KeyError, TypeError, ValueError) as error:
                failures[f"target:{error}"] += 1
        for index, control in enumerate(assignment.get("controls", [])):
            if not control.get("complete_actionable"):
                continue
            try:
                control_identity = {
                    "mint": control["row"]["mint"],
                    "pool": control["row"]["pool"],
                }
                control_results.append(economics_for(
                    f"control_{index + 1}", control_identity, control["entry"], control["exit"],
                    by_key, args.quote_in_lamports,
                ))
            except (KeyError, TypeError, ValueError) as error:
                failures[f"control:{error}"] += 1

    def summary(values: list[dict]) -> dict:
        pnls = [int(row["pnl_quote_lamports"]) for row in values]
        positive = sum(value > 0 for value in pnls)
        negative = sum(value < 0 for value in pnls)
        gross_profit = sum(value for value in pnls if value > 0)
        gross_loss = -sum(value for value in pnls if value < 0)
        return {
            "sample": len(pnls),
            "positive": positive,
            "negative": negative,
            "win_rate_pct": 100 * positive / len(pnls) if pnls else None,
            "total_pnl_sol": sum(pnls) / 1_000_000_000,
            "profit_factor": gross_profit / gross_loss if gross_loss else None,
            "median_roi_pct": sorted(row["roi_bps"] for row in values)[len(values) // 2] / 100 if values else None,
        }

    payload = {
        "schema": SCHEMA,
        "venue": "PUMPSWAP",
        "paper_quote_in_lamports": args.quote_in_lamports,
        "policy": "same sealed 1s entry / 120s first observed mark as target economics",
        "assignments_sha256": sha256(args.assignments),
        "tape_checkpoint_sha256": checkpoint_hash,
        "target_summary": summary(target_results),
        "control_summary": summary(control_results),
        "target_results": target_results,
        "control_results": control_results,
        "failure_reasons": dict(failures),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "BENCHMARK_SEALED", "target_summary": payload["target_summary"], "control_summary": payload["control_summary"]}, sort_keys=True))


if __name__ == "__main__":
    main()
