#!/usr/bin/env python3
"""Evaluate frozen ARM07 entries against later exact PumpSwap state marks.

This is an economics-stage consumer.  It runs only after the observation
partition has passed the fresh-cohort audit, uses the frozen 30-second policy,
and never changes selection or policy.  Controls are intentionally explicit:
until same-venue controls are supplied, the report cannot pass promotion.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kefi_frozen_pumpswap_selection_economics_v1 import quote_buy_exact_quote_in, quote_sell_exact_base


WSOL = "So11111111111111111111111111111111111111112"
HORIZON_NS = 30_000_000_000
CONTROL_LOOKBACK_NS = 30_000_000_000


def row_key(row: dict) -> tuple:
    return (str(row.get("source_frame_sha256")), int(row.get("event_index", 0)), int(row["received_at_unix_ns"]))


def pre_entry_state(row: dict) -> dict:
    return {
        "pool_base_raw": int(row["pool_base_raw"]) + int(row["base_amount"]),
        "pool_quote_raw": int(row["pool_quote_raw"]) - int(row["pool_quote_delta"]),
        "virtual_quote_reserves_raw": int(row.get("virtual_quote_reserves", 0)),
    }


def mark_state(row: dict) -> dict:
    return {
        "pool_base_raw": int(row["pool_base_raw"]),
        "pool_quote_raw": int(row["pool_quote_raw"]),
        "virtual_quote_reserves_raw": int(row.get("virtual_quote_reserves", 0)),
    }


def evaluator_row(row: dict) -> dict:
    value = dict(row)
    value["virtual_quote_reserves_raw"] = int(row.get("virtual_quote_reserves", 0))
    return value


def trade_result(event: dict, rows: list[dict], quote_in: int, horizon_ns: int, observation_id: str, result_type: str) -> dict:
    same_pool = [row for row in rows if row.get("mint") == event.get("mint") and row.get("pool") == event.get("pool")]
    future = [row for row in same_pool if int(row["received_at_unix_ns"]) >= int(event["received_at_unix_ns"]) + horizon_ns]
    result = {
        "observation_id": observation_id,
        "result_type": result_type,
        "mint": event["mint"],
        "pool": event["pool"],
        "entry_received_at_unix_ns": int(event["received_at_unix_ns"]),
        "status": "ECONOMICS_UNAVAILABLE",
        "reason": "NO_MARK_AT_FROZEN_HORIZON",
    }
    if not future:
        return result
    buy = quote_buy_exact_quote_in(quote_in, pre_entry_state(event), evaluator_row(event))
    marks = []
    for mark in same_pool:
        if int(mark["received_at_unix_ns"]) < int(event["received_at_unix_ns"]):
            continue
        sell = quote_sell_exact_base(buy["token_out"], mark_state(mark), evaluator_row(mark))
        pnl = int(sell["net_quote_out"]) - quote_in
        marks.append({"received_at_unix_ns": int(mark["received_at_unix_ns"]), "pnl_quote": pnl})
    exit_row = future[0]
    exit_sell = quote_sell_exact_base(buy["token_out"], mark_state(exit_row), evaluator_row(exit_row))
    pnl = int(exit_sell["net_quote_out"]) - quote_in
    result.update({
        "status": "ECONOMICS_AVAILABLE",
        "reason": "",
        "exit_received_at_unix_ns": int(exit_row["received_at_unix_ns"]),
        "entry": buy,
        "exit": exit_sell,
        "pnl_quote": pnl,
        "roi_bps": pnl * 10_000 / quote_in,
        "mfe_quote": max(mark["pnl_quote"] for mark in marks),
        "mae_quote": min(mark["pnl_quote"] for mark in marks),
        "mark_count": len(marks),
    })
    return result


def summarize(results: list[dict]) -> dict:
    available = [row for row in results if row["status"] == "ECONOMICS_AVAILABLE"]
    pnls = [int(row["pnl_quote"]) for row in available]
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = -sum(value for value in pnls if value < 0)
    running = peak = drawdown = 0
    for value in pnls:
        running += value
        peak = max(peak, running)
        drawdown = min(drawdown, running - peak)
    return {
        "actionable_n": len(available),
        "observations_n": len(results),
        "pnl_quote": sum(pnls) / 1_000_000_000,
        "roi_mean_bps": sum(float(row["roi_bps"]) for row in available) / len(available) if available else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "win_rate_pct": 100 * sum(value > 0 for value in pnls) / len(pnls) if pnls else None,
        "mfe_quote": sum(int(row["mfe_quote"]) for row in available) / 1_000_000_000 if available else None,
        "mae_quote": sum(int(row["mae_quote"]) for row in available) / 1_000_000_000 if available else None,
        "max_drawdown_quote": drawdown / 1_000_000_000,
    }


def matched_controls(entries: list[dict], rows: list[dict]) -> list[dict]:
    eligible = [row for row in rows if row.get("side") == "BUY"
                and row.get("quote_mint") == WSOL
                and row.get("exact_transition_validated") is True
                and not row.get("is_tracked_teacher")]
    used: set[tuple] = set()
    controls: list[dict] = []
    for observation in sorted(entries, key=lambda item: int(item["state"]["decoded_event"]["received_at_unix_ns"])):
        event = observation["state"]["decoded_event"]
        entry_ns = int(event["received_at_unix_ns"])
        candidates = [row for row in eligible
                      if row.get("mint") == event.get("mint") and row.get("pool") == event.get("pool")
                      and entry_ns - CONTROL_LOOKBACK_NS <= int(row["received_at_unix_ns"]) < entry_ns
                      and (str(row.get("source_frame_sha256")), int(row.get("event_index", 0))) not in used]
        if not candidates:
            continue
        control = max(candidates, key=lambda row: (int(row["received_at_unix_ns"]), str(row.get("source_frame_sha256")), int(row.get("event_index", 0))))
        identity = (str(control.get("source_frame_sha256")), int(control.get("event_index", 0)))
        used.add(identity)
        controls.append({"control_id": "control-" + identity[0][:32] + "-" + str(identity[1]), "target_observation_id": observation["observation_id"], "event": control})
    return controls


def evaluate(entries: list[dict], tape: list[dict], quote_in: int, horizon_ns: int) -> dict:
    dedup = {row_key(row): row for row in tape}
    rows = sorted(dedup.values(), key=lambda row: (int(row["received_at_unix_ns"]), row_key(row)))
    results: list[dict] = []
    failures: Counter[str] = Counter()
    for observation in entries:
        event = observation["state"]["decoded_event"]
        if observation.get("arm_id") != "ARM07_PUMPSWAP_DIRECT":
            failures["WRONG_ARM"] += 1
            continue
        if event.get("quote_mint") != WSOL or event.get("exact_transition_validated") is not True:
            failures["UNSUPPORTED_ENTRY_STATE"] += 1
            continue
        try:
            result = trade_result(event, rows, quote_in, horizon_ns, observation["observation_id"], "SELECTED")
            if result["status"] != "ECONOMICS_AVAILABLE":
                failures["NO_MARK_AT_FROZEN_HORIZON"] += 1
                results.append(result)
                continue
        except (KeyError, TypeError, ValueError) as error:
            result = {"observation_id": observation["observation_id"], "result_type": "SELECTED", "status": "ECONOMICS_UNAVAILABLE", "reason": type(error).__name__ + ":" + str(error)}
            result["reason"] = type(error).__name__ + ":" + str(error)
            failures[result["reason"]] += 1
        results.append(result)
    control_rows = matched_controls(entries, rows)
    control_results = []
    for control in control_rows:
        try:
            control_result = trade_result(control["event"], rows, quote_in, horizon_ns, control["control_id"], "MATCHED_CONTROL")
        except (KeyError, TypeError, ValueError) as error:
            control_result = {"observation_id": control["control_id"], "result_type": "MATCHED_CONTROL", "status": "ECONOMICS_UNAVAILABLE", "reason": type(error).__name__ + ":" + str(error)}
        control_result["target_observation_id"] = control["target_observation_id"]
        control_results.append(control_result)
    selected_metrics = summarize(results)
    control_metrics = summarize(control_results)
    comparison_fields = ("pnl_quote", "profit_factor", "win_rate_pct")
    controls_outperformed = None
    if control_metrics["actionable_n"] and selected_metrics["actionable_n"]:
        controls_outperformed = all(
            selected_metrics[field] is not None and control_metrics[field] is not None and selected_metrics[field] > control_metrics[field]
            for field in comparison_fields
        )
    metrics = {**selected_metrics, "controls_n": len(control_results), "controls_actionable_n": control_metrics["actionable_n"], "controls_outperformed": controls_outperformed}
    if metrics["actionable_n"] < 50:
        promotion_blocker = "SAMPLE_BELOW_GATE"
    elif controls_outperformed is None:
        promotion_blocker = "CONTROLS_NOT_COLLECTED"
    elif controls_outperformed is not True:
        promotion_blocker = "CONTROLS_NOT_OUTPERFORMED"
    else:
        promotion_blocker = "PROMOTION_GATES_PASS_TO_FINAL_REVIEW"
    return {
        "schema": "KEFI_ARM07_FRESH_PROSPECTIVE_ECONOMICS_V1",
        "arm_id": "ARM07_PUMPSWAP_DIRECT",
        "venue": "PUMPSWAP",
        "cohort": "FRESH_PROSPECTIVE",
        "outcome_source": "POST_FREEZE",
        "exact_evaluator": True,
        "policy_id": "kefi-pumpswap-fixed-30s-challenge-v1",
        "paper_quote_in_lamports": quote_in,
        "fixed_exit_seconds": horizon_ns / 1_000_000_000,
        "controls_contract": "same-venue controls required before promotion",
        "control_policy_id": "kefi-pumpswap-matched-nonteacher-buy-control-30s-v1",
        "control_comparison": control_metrics,
        "control_results": control_results,
        "metrics": metrics,
        "results": results,
        "failures": dict(failures),
        "promotion_eligible": False,
        "promotion_blocker": promotion_blocker,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--tape", type=Path, action="append", required=True)
    parser.add_argument("--quote-in-lamports", type=int, default=100_000_000)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    entries = [json.loads(line) for line in args.observations.read_text(encoding="utf-8").splitlines() if line.strip()]
    tape = [json.loads(line) for path in args.tape for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = evaluate(entries, tape, args.quote_in_lamports, HORIZON_NS)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"actionable_n": report["metrics"]["actionable_n"], "status": report["promotion_blocker"]}, sort_keys=True))


if __name__ == "__main__":
    main()
