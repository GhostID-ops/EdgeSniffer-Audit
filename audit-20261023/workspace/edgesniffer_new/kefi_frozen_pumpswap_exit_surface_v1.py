#!/usr/bin/env python3
"""Build an outcome-complete fixed-horizon surface for the sealed PumpSwap cohort.

This is a discovery report.  It consumes outcome-blind actionability assignments
for several predeclared horizons and applies the same exact fixed-notional
PumpSwap cash-flow model to targets and deterministic controls.  It does not
select or promote a winning horizon.

MFE/MAE are calculated as the best/worst mark-to-market liquidation ROI seen
between the simulated entry event and the assigned exit mark, inclusive.  The
position is the exact token amount produced by the fixed-notional entry quote.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from kefi_frozen_pumpswap_selection_economics_v1 import (
    quote_buy_exact_quote_in,
    quote_sell_exact_base,
    read_tape,
    row_key,
    sha256,
)


SCHEMA = "KEFI_FROZEN_PUMPSWAP_EXIT_SURFACE_V1"


def parse_case(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("case must be LABEL=ASSIGNMENTS_PATH")
    label, path = value.split("=", 1)
    if not label or not path:
        raise argparse.ArgumentTypeError("case must be LABEL=ASSIGNMENTS_PATH")
    return label, Path(path)


def grouped_rows(rows: list[dict]) -> dict[tuple[str, str, int], list[dict]]:
    grouped: dict[tuple[str, str, int], list[dict]] = {}
    for row in rows:
        key = (str(row["mint"]), str(row["pool"]), int(row["continuity_epoch"]))
        grouped.setdefault(key, []).append(row)
    return grouped


def path_marks(
    entry_assignment: dict,
    exit_assignment: dict,
    identity: dict,
    by_key: dict[tuple, dict],
    groups: dict[tuple[str, str, int], list[dict]],
    quote_in: int,
) -> dict:
    entry_key = tuple(entry_assignment["entry_event_key"])
    exit_key = tuple(exit_assignment["mark_event_key"])
    entry_row = by_key[entry_key]
    exit_row = by_key[exit_key]
    if entry_row["mint"] != identity["mint"] or entry_row["pool"] != identity["pool"]:
        raise ValueError("ENTRY_IDENTITY_MISMATCH")
    if exit_row["mint"] != identity["mint"] or exit_row["pool"] != identity["pool"]:
        raise ValueError("EXIT_IDENTITY_MISMATCH")

    buy = quote_buy_exact_quote_in(quote_in, entry_assignment["pre_state"], entry_row)
    group_key = (str(identity["mint"]), str(identity["pool"]), int(entry_row["continuity_epoch"]))
    path = groups[group_key]
    entry_position = next(index for index, row in enumerate(path) if row_key(row) == entry_key)
    exit_position = next(index for index, row in enumerate(path) if row_key(row) == exit_key)
    if exit_position < entry_position:
        raise ValueError("EXIT_PRECEDES_ENTRY")

    marks = []
    for row in path[entry_position : exit_position + 1]:
        sell = quote_sell_exact_base(buy["token_out"], row_state(row), row)
        pnl = int(sell["net_quote_out"]) - quote_in
        marks.append({
            "event_key": list(row_key(row)),
            "received_at_unix_ns": int(row["received_at_unix_ns"]),
            "pnl_quote_lamports": pnl,
            "roi_bps": pnl * 10_000 / quote_in,
        })
    if not marks:
        raise ValueError("EMPTY_MARK_PATH")
    best = max(marks, key=lambda mark: (mark["pnl_quote_lamports"], -mark["received_at_unix_ns"]))
    worst = min(marks, key=lambda mark: (mark["pnl_quote_lamports"], mark["received_at_unix_ns"]))
    exit_sell = quote_sell_exact_base(buy["token_out"], exit_assignment["pre_state"], exit_row)
    exit_pnl = int(exit_sell["net_quote_out"]) - quote_in
    return {
        "entry_event_key": list(entry_key),
        "exit_event_key": list(exit_key),
        "entry_received_at_unix_ns": int(entry_row["received_at_unix_ns"]),
        "exit_received_at_unix_ns": int(exit_row["received_at_unix_ns"]),
        "entry_observation_side": entry_row["side"],
        "exit_observation_side": exit_row["side"],
        "entry": buy,
        "exit": exit_sell,
        "pnl_quote_lamports": exit_pnl,
        "roi_bps": exit_pnl * 10_000 / quote_in,
        "mfe_quote_lamports": int(best["pnl_quote_lamports"]),
        "mfe_roi_bps": best["roi_bps"],
        "mfe_event_key": best["event_key"],
        "mae_quote_lamports": int(worst["pnl_quote_lamports"]),
        "mae_roi_bps": worst["roi_bps"],
        "mae_event_key": worst["event_key"],
        "mark_count": len(marks),
    }


def row_state(row: dict) -> dict:
    return {
        "pool_base_raw": row["pool_base_raw"],
        "pool_quote_raw": row["pool_quote_raw"],
    }


def summary(values: list[dict]) -> dict:
    pnls = [int(value["pnl_quote_lamports"]) for value in values]
    positive = sum(value > 0 for value in pnls)
    negative = sum(value < 0 for value in pnls)
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = -sum(value for value in pnls if value < 0)
    cumulative = 0
    peak = 0
    max_drawdown = 0
    for value in pnls:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    rois = sorted(float(value["roi_bps"]) for value in values)
    median = rois[len(rois) // 2] if rois else None
    return {
        "sample": len(values),
        "positive": positive,
        "negative": negative,
        "zero": len(values) - positive - negative,
        "win_rate_pct": 100 * positive / len(values) if values else None,
        "total_pnl_sol": sum(pnls) / 1_000_000_000,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "median_roi_pct": median / 100 if median is not None else None,
        "mean_mfe_roi_pct": (
            sum(float(value["mfe_roi_bps"]) for value in values) / len(values) / 100
            if values else None
        ),
        "mean_mae_roi_pct": (
            sum(float(value["mae_roi_bps"]) for value in values) / len(values) / 100
            if values else None
        ),
        "max_mfe_roi_pct": max((float(value["mfe_roi_bps"]) for value in values), default=None) / 100
        if values else None,
        "min_mae_roi_pct": min((float(value["mae_roi_bps"]) for value in values), default=None) / 100
        if values else None,
        "max_cumulative_drawdown_sol": max_drawdown / 1_000_000_000,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", type=parse_case, required=True,
                        help="LABEL=sealed actionability assignments JSON")
    parser.add_argument("--tape", type=Path, required=True)
    parser.add_argument("--quote-in-lamports", type=int, default=100_000_000)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit("FAIL_CLOSED: surface output already exists")
    if args.quote_in_lamports <= 0:
        raise SystemExit("FAIL_CLOSED: quote notional must be positive")

    rows, checkpoint_hash = read_tape(args.tape)
    by_key = {row_key(row): row for row in rows}
    if len(by_key) != len(rows):
        raise SystemExit("FAIL_CLOSED: duplicate PumpSwap transition key")
    groups = grouped_rows(rows)
    cases = []

    for label, assignment_path in args.case:
        assignments = json.loads(assignment_path.read_text(encoding="utf-8"))
        if assignments.get("venue") != "pumpswap" or assignments.get("pnl_accessed") is not False:
            raise SystemExit(f"FAIL_CLOSED: {label} is not outcome-blind PumpSwap actionability")
        target_results = []
        control_results = []
        failures = Counter()
        for block in assignments.get("assignments", []):
            if block.get("target_status") == "MARK_AVAILABLE":
                try:
                    target_results.append(path_marks(
                        block["target_entry"],
                        block["target_exit"],
                        {"mint": block["mint"], "pool": block["pool"]},
                        by_key, groups, args.quote_in_lamports,
                    ) | {"label": "target", "target_id": block["target_id"]})
                except (KeyError, TypeError, ValueError) as error:
                    failures[f"target:{error}"] += 1
            for index, control in enumerate(block.get("controls", [])):
                if not control.get("complete_actionable"):
                    continue
                try:
                    control_identity = {"mint": control["row"]["mint"], "pool": control["row"]["pool"]}
                    control_results.append(path_marks(
                        control["entry"], control["exit"], control_identity,
                        by_key, groups, args.quote_in_lamports
                    ) | {"label": f"control_{index + 1}", "mint": control_identity["mint"],
                        "pool": control_identity["pool"]})
                except (KeyError, TypeError, ValueError) as error:
                    failures[f"control:{error}"] += 1
        cases.append({
            "label": label,
            "exit_after_ns": assignments.get("exit_after_ns"),
            "assignment_sha256": sha256(assignment_path),
            "target_summary": summary(target_results),
            "control_summary": summary(control_results),
            "target_results": target_results,
            "control_results": control_results,
            "failure_reasons": dict(failures),
        })

    payload = {
        "schema": SCHEMA,
        "venue": "PUMPSWAP",
        "paper_quote_in_lamports": args.quote_in_lamports,
        "entry_policy": "first_tradeable_same_epoch_state_at_or_after_target_plus_1s",
        "exit_policy": "first_tradeable_same_epoch_observed_state_at_or_after_entry_plus_fixed_horizon",
        "mfe_mae_policy": "exact_fixed_position_liquidation_roi_at_each_observed_state_from_entry_through_exit",
        "tape_checkpoint_sha256": checkpoint_hash,
        "pnl_accessed": True,
        "cases": cases,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, args.out)
    print(json.dumps({
        "status": "EXIT_SURFACE_SEALED",
        "cases": [
            {"label": case["label"], "target": case["target_summary"], "control": case["control_summary"]}
            for case in cases
        ],
        "output_sha256": sha256(args.out),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
