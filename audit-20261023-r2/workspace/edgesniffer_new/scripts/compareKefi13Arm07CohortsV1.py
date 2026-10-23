#!/usr/bin/env python3
"""Read-only comparison of the preserved ARM07 PumpSwap cohorts.

The command consumes sealed report files and their explicitly supplied tape
snapshots.  It never changes a cohort, policy, registry, collector, or
evaluator.  The report replays the selected observation IDs with the current
evaluator and then compares entry-state, fee, reserve, path, and control
features between cohorts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from evaluateKefi13Arm07FreshEconomicsV1 import (
    evaluator_row,
    evaluate,
    mark_state,
    pre_entry_state,
)
from kefi_frozen_pumpswap_selection_economics_v1 import (
    quote_buy_exact_quote_in,
    quote_sell_exact_base,
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metric_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report["metrics"]
    controls = report["control_comparison"]
    return {
        "observations_n": metrics["observations_n"],
        "actionable_n": metrics["actionable_n"],
        "pnl_sol": metrics["pnl_quote"],
        "roi_mean_bps": metrics["roi_mean_bps"],
        "profit_factor": metrics["profit_factor"],
        "win_rate_pct": metrics["win_rate_pct"],
        "mfe_sol": metrics["mfe_quote"],
        "mae_sol": metrics["mae_quote"],
        "max_drawdown_sol": metrics["max_drawdown_quote"],
        "controls_n": metrics["controls_n"],
        "controls_actionable_n": metrics["controls_actionable_n"],
        "controls_pnl_sol": controls["pnl_quote"],
        "controls_profit_factor": controls["profit_factor"],
        "controls_win_rate_pct": controls["win_rate_pct"],
        "controls_outperformed": metrics["controls_outperformed"],
    }


def selected_observations(
    observations: list[dict[str, Any]], report: dict[str, Any]
) -> list[dict[str, Any]]:
    ids = {
        row["observation_id"]
        for row in report["results"]
        if row.get("result_type") == "SELECTED"
    }
    selected = [row for row in observations if row.get("observation_id") in ids]
    if len(selected) != len(ids):
        missing = sorted(ids - {row.get("observation_id") for row in selected})
        raise ValueError(f"sealed report observation IDs missing from source: {missing[:3]}")
    return sorted(selected, key=lambda row: int(row["state"]["decoded_event"]["received_at_unix_ns"]))


def tape_rows(tape: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dedup: dict[tuple[str, int, int], dict[str, Any]] = {}
    for row in tape:
        key = (
            str(row.get("source_frame_sha256")),
            int(row.get("event_index", 0)),
            int(row["received_at_unix_ns"]),
        )
        dedup[key] = row
    return sorted(dedup.values(), key=lambda row: (int(row["received_at_unix_ns"]), str(row.get("source_frame_sha256")), int(row.get("event_index", 0))))


def mark_path(event: dict[str, Any], rows: list[dict[str, Any]], quote_in: int) -> list[dict[str, Any]]:
    same_pool = [
        row for row in rows
        if row.get("mint") == event.get("mint") and row.get("pool") == event.get("pool")
    ]
    buy = quote_buy_exact_quote_in(quote_in, pre_entry_state(event), evaluator_row(event))
    path: list[dict[str, Any]] = []
    for row in same_pool:
        if int(row["received_at_unix_ns"]) < int(event["received_at_unix_ns"]):
            continue
        sell = quote_sell_exact_base(buy["token_out"], mark_state(row), evaluator_row(row))
        path.append({
            "received_at_unix_ns": int(row["received_at_unix_ns"]),
            "pnl_lamports": int(sell["net_quote_out"]) - quote_in,
        })
    return path


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "p25": None, "p75": None}
    ordered = sorted(values)
    return {
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p25": ordered[max(0, int((len(ordered) - 1) * 0.25))],
        "p75": ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.75))],
    }


def feature_rows(
    observations: list[dict[str, Any]],
    report: dict[str, Any],
    tape: list[dict[str, Any]],
    quote_in: int,
) -> list[dict[str, Any]]:
    result_by_id = {
        row["observation_id"]: row
        for row in report["results"]
        if row.get("result_type") == "SELECTED"
        and row.get("status") == "ECONOMICS_AVAILABLE"
    }
    rows = tape_rows(tape)
    output: list[dict[str, Any]] = []
    for observation in observations:
        observation_id = observation["observation_id"]
        result = result_by_id.get(observation_id)
        if result is None:
            continue
        event = observation["state"]["decoded_event"]
        pre = pre_entry_state(event)
        pre_effective_quote = int(pre["pool_quote_raw"]) + int(pre.get("virtual_quote_reserves_raw", 0))
        pre_base = int(pre["pool_base_raw"])
        path = mark_path(event, rows, quote_in)
        future_path = [item for item in path if item["received_at_unix_ns"] > int(event["received_at_unix_ns"])]
        first_future = future_path[0] if future_path else None
        entry_ns = int(event["received_at_unix_ns"])
        same_pool_rows = [
            row for row in rows
            if row.get("mint") == event.get("mint") and row.get("pool") == event.get("pool")
            and int(row["received_at_unix_ns"]) < entry_ns
        ]
        def flow(window_ns: int) -> tuple[int, int, int, int]:
            window = [row for row in same_pool_rows if entry_ns - window_ns <= int(row["received_at_unix_ns"])]
            buys = sum(int(row["pool_quote_delta"]) for row in window if row.get("side") == "BUY")
            sells = sum(int(row["pool_quote_delta"]) for row in window if row.get("side") == "SELL")
            return buys, sells, buys - sells, len(window)
        buy5, sell5, net5, count5 = flow(5_000_000_000)
        buy30, sell30, net30, count30 = flow(30_000_000_000)
        pnl = int(result["pnl_quote"])
        mfe = int(result["mfe_quote"])
        mae = int(result["mae_quote"])
        entry = result["entry"]
        exit_row = result["exit"]
        output.append({
            "observation_id": observation_id,
            "pnl_sol": pnl / 1_000_000_000,
            "win": pnl > 0,
            "pre_effective_quote_sol": pre_effective_quote / 1_000_000_000,
            "pre_pool_base_raw": pre_base,
            "entry_pool_quote_delta_sol": int(event["pool_quote_delta"]) / 1_000_000_000,
            "entry_base_fraction_bps": int(event["base_amount"]) * 10_000 / pre_base if pre_base else None,
            "entry_quote_fraction_bps": int(event["pool_quote_delta"]) * 10_000 / pre_effective_quote if pre_effective_quote else None,
            "entry_virtual_quote_sol": int(event.get("virtual_quote_reserves", 0)) / 1_000_000_000,
            "entry_fee_sol": int(entry["fee_total"]) / 1_000_000_000,
            "entry_fee_bps": int(entry["fee_total"]) * 10_000 / quote_in,
            "entry_lp_fee_bps": int(event["lp_fee_bps"]),
            "entry_protocol_fee_bps": int(event["protocol_fee_bps"]),
            "entry_creator_fee_bps": int(event["coin_creator_bps"]),
            "exit_fee_sol": int(exit_row["fee_total"]) / 1_000_000_000,
            "exit_fee_bps": int(exit_row["fee_total"]) * 10_000 / max(int(exit_row["gross_quote_out"]), 1),
            "exit_lag_seconds": (int(result["exit_received_at_unix_ns"]) - int(result["entry_received_at_unix_ns"])) / 1_000_000_000,
            "first_future_lag_seconds": (first_future["received_at_unix_ns"] - entry_ns) / 1_000_000_000 if first_future else None,
            "exit_effective_quote_sol": int(exit_row["effective_quote_reserve"]) / 1_000_000_000,
            "prior_5s_buy_quote_sol": buy5 / 1_000_000_000,
            "prior_5s_sell_quote_sol": sell5 / 1_000_000_000,
            "prior_5s_net_quote_sol": net5 / 1_000_000_000,
            "prior_5s_trade_count": count5,
            "prior_30s_buy_quote_sol": buy30 / 1_000_000_000,
            "prior_30s_sell_quote_sol": sell30 / 1_000_000_000,
            "prior_30s_net_quote_sol": net30 / 1_000_000_000,
            "prior_30s_trade_count": count30,
            "mfe_sol": mfe / 1_000_000_000,
            "mae_sol": mae / 1_000_000_000,
            "mark_count": int(result["mark_count"]),
            "first_future_pnl_sol": first_future["pnl_lamports"] / 1_000_000_000 if first_future else None,
            "mfe_positive": mfe > 0,
            "mfe_ge_001_sol": mfe >= 10_000_000,
            "mfe_ge_0025_sol": mfe >= 25_000_000,
            "negative_fixed_but_positive_mfe": pnl < 0 and mfe > 0,
            "negative_fixed_but_mfe_ge_001": pnl < 0 and mfe >= 10_000_000,
        })
    return output


def summarize_features(rows: list[dict[str, Any]]) -> dict[str, Any]:
    numeric = (
        "pnl_sol", "pre_effective_quote_sol", "entry_pool_quote_delta_sol",
        "entry_base_fraction_bps", "entry_quote_fraction_bps", "entry_virtual_quote_sol",
        "entry_fee_sol", "entry_fee_bps", "entry_lp_fee_bps", "entry_protocol_fee_bps",
        "entry_creator_fee_bps", "exit_fee_sol", "exit_fee_bps", "exit_lag_seconds",
        "first_future_lag_seconds", "exit_effective_quote_sol", "prior_5s_buy_quote_sol",
        "prior_5s_sell_quote_sol", "prior_5s_net_quote_sol", "prior_5s_trade_count",
        "prior_30s_buy_quote_sol", "prior_30s_sell_quote_sol", "prior_30s_net_quote_sol",
        "prior_30s_trade_count", "mfe_sol", "mae_sol", "mark_count", "first_future_pnl_sol",
    )
    result: dict[str, Any] = {name: quantiles([float(row[name]) for row in rows if row[name] is not None]) for name in numeric}
    result["n"] = len(rows)
    result["wins_n"] = sum(bool(row["win"]) for row in rows)
    result["mfe_positive_n"] = sum(bool(row["mfe_positive"]) for row in rows)
    result["mfe_ge_001_n"] = sum(bool(row["mfe_ge_001_sol"]) for row in rows)
    result["mfe_ge_0025_n"] = sum(bool(row["mfe_ge_0025_sol"]) for row in rows)
    result["negative_fixed_but_positive_mfe_n"] = sum(bool(row["negative_fixed_but_positive_mfe"]) for row in rows)
    result["negative_fixed_but_mfe_ge_001_n"] = sum(bool(row["negative_fixed_but_mfe_ge_001"]) for row in rows)
    return result


def summarize_cohort(
    *,
    manifest_path: Path,
    report_path: Path,
    observations_path: Path,
    tape_path: Path,
    registry_sha256: str,
    policies_sha256: str,
    quote_in: int,
) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    report = load_json(report_path)
    observations = selected_observations(load_jsonl(observations_path), report)
    tape = load_jsonl(tape_path)
    replay = evaluate(observations, tape, quote_in, 30_000_000_000)
    sealed = metric_snapshot(report)
    replayed = metric_snapshot(replay)
    features = feature_rows(observations, report, tape, quote_in)
    positive = [row for row in features if row["win"]]
    negative = [row for row in features if not row["win"]]
    return {
        "cohort_id": manifest["cohort_id"],
        "cohort_started_at_utc": manifest["cohort_started_at_utc"],
        "manifest_sha256": sha256_file(manifest_path),
        "report_path": str(report_path),
        "observations_path": str(observations_path),
        "tape_path": str(tape_path),
        "report_sha256": sha256_file(report_path),
        "observations_sha256": sha256_file(observations_path),
        "tape_sha256": sha256_file(tape_path),
        "registry_sha256": registry_sha256,
        "policies_sha256": policies_sha256,
        "policy_id": report["policy_id"],
        "notional_lamports": report["paper_quote_in_lamports"],
        "fixed_exit_seconds": report["fixed_exit_seconds"],
        "sealed_metrics": sealed,
        "current_evaluator_replay_metrics": replayed,
        "current_evaluator_replay_exact": sealed == replayed,
        "feature_summary": summarize_features(features),
        "positive_trade_features": summarize_features(positive),
        "negative_trade_features": summarize_features(negative),
        "selected_feature_rows": features,
        "control_metrics": report["control_comparison"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-manifest", type=Path, required=True)
    parser.add_argument("--old-report", type=Path, required=True)
    parser.add_argument("--old-observations", type=Path, required=True)
    parser.add_argument("--old-tape", type=Path, required=True)
    parser.add_argument("--new-manifest", type=Path, required=True)
    parser.add_argument("--new-report", type=Path, required=True)
    parser.add_argument("--new-observations", type=Path, required=True)
    parser.add_argument("--new-tape", type=Path, required=True)
    parser.add_argument("--old-registry-sha256", required=True)
    parser.add_argument("--new-registry-sha256", required=True)
    parser.add_argument("--policies-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    old = summarize_cohort(
        manifest_path=args.old_manifest,
        report_path=args.old_report, observations_path=args.old_observations,
        tape_path=args.old_tape, registry_sha256=args.old_registry_sha256,
        policies_sha256=args.policies_sha256, quote_in=100_000_000,
    )
    new = summarize_cohort(
        manifest_path=args.new_manifest,
        report_path=args.new_report, observations_path=args.new_observations,
        tape_path=args.new_tape, registry_sha256=args.new_registry_sha256,
        policies_sha256=args.policies_sha256, quote_in=100_000_000,
    )
    output = {
        "schema": "KEFI_ARM07_COHORT_COMPARISON_V1",
        "read_only": True,
        "strategy_contract": {
            "policy_id": old["policy_id"],
            "notional_lamports": 100_000_000,
            "fixed_exit_seconds": 30,
            "entry_contract": "canonical PumpSwap pool-state identity; +1s entry delay",
            "fallback": "Jupiter disabled",
        },
        "implementation_comparison": {
            "policies_same": old["policies_sha256"] == new["policies_sha256"],
            "policy_ids_same": old["policy_id"] == new["policy_id"],
            "registry_changed": old["registry_sha256"] != new["registry_sha256"],
            "current_decoder_evaluator_replays_both_exactly": old["current_evaluator_replay_exact"] and new["current_evaluator_replay_exact"],
            "registry_change_scope": "ARM13 fail-closed resolver / registry binding; ARM07 policy binding unchanged",
            "current_code_sha256": {
                relative: sha256_file(Path(__file__).resolve().parents[1] / relative)
                for relative in (
                    "scripts/pumpSwapExactEconomicsV1.py",
                    "scripts/pumpSwapInitDecoderV1.py",
                    "kefi_frozen_pumpswap_exit_surface_v1.py",
                    "kefi_frozen_selection_actionability_evaluator_v1.py",
                    "scripts/evaluateKefi13Arm07FreshEconomicsV1.py",
                )
            },
        },
        "cohorts": {"old": old, "new": new},
        "read_only_research_hypotheses": {
            "dynamic_selling": "Evaluate a preregistered trailing-stop or time-plus-drawdown rule on both frozen cohorts; do not replace the fixed-30s result.",
            "partial_take_profits": "Evaluate fixed partial exits at preregistered MFE thresholds with residual time exit; use mark paths as an offline counterfactual only.",
            "trade_filtering": "Evaluate entry filters using only pre-entry fields (effective quote reserve, trade-size fraction, fee burden, and recent same-pool pressure); freeze before any new prospective sample.",
        },
    }
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS_CURRENT_REPLAY" if output["implementation_comparison"]["current_decoder_evaluator_replays_both_exactly"] else "FAIL_CURRENT_REPLAY",
        "old_pnl_sol": old["sealed_metrics"]["pnl_sol"],
        "new_pnl_sol": new["sealed_metrics"]["pnl_sol"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
