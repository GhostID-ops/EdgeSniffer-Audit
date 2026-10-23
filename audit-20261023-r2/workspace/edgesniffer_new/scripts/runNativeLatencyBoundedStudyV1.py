#!/usr/bin/env python3
"""Evaluate native checkpoints with explicit exit-horizon and latency bounds.

This is a discovery-only report.  It does not alter the strict transaction
mark report, the carry-forward report, the checkpoint report, or any policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import runIndependentStrategyLabV1 as lab


HORIZONS = (15, 30, 60)
LATENCY_BUCKETS: tuple[tuple[str, int | None], ...] = (
    ("lte1s", 1_000), ("lte5s", 5_000), ("lte15s", 15_000),
    ("lte30s", 30_000), ("lte60s", 60_000), ("lte120s", 120_000),
    ("unbounded", None),
)
FILTER_IDS = ("FLOW_005_AND_PRICE_10BPS", "FLOW_AND_PRICE_CONTINUATION", "RESERVE_GE_2_SOL")
NOTIONALS = (0.1, 0.5, 1.0)
STRESS_BPS = (0, 25, 50)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def checkpoint_for(window: dict[str, Any], horizon: int) -> dict[str, Any] | None:
    rows = [row for row in window.get("native_checkpoint_marks") or []
            if int(row.get("horizon_seconds") or 0) == horizon]
    return min(rows, key=lambda row: int(row.get("checkpoint_receive_ts_ms") or 2**63)) if rows else None


def metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["pnl_sol"]) for row in rows if row.get("status") == "ACTIONABLE" and row.get("pnl_sol") is not None]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    immature = sum(row.get("status") == "IMMATURE_WINDOW" for row in rows)
    missed = sum(row.get("status") in {"MISSED_EXIT_MARK", "FAILED_EXIT_QUOTE"} for row in rows)
    return {
        "attempted": len(rows),
        "matured": len(rows) - immature,
        "immature": immature,
        "actionable": len(values),
        "missed_or_failed": missed,
        "wins": len(wins),
        "win_rate": len(wins) / len(values) if values else None,
        "pnl_sol": sum(values),
        "roi_on_actionable": sum(values) / (rows[0]["notional_sol"] * len(values)) if values else None,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else (None if not wins else None),
        "max_drawdown_sol": drawdown,
        "late_checkpoint_count": sum(row.get("exit_reason") == "NATIVE_CHECKPOINT_DELAY_EXCEEDED" for row in rows),
        "missing_checkpoint_count": sum(row.get("exit_reason") == "MISSING_NATIVE_CHECKPOINT" for row in rows),
    }


def evaluate(path: Path, contract_path: Path) -> dict[str, Any]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    windows = load_jsonl(path)
    filters = {spec["id"]: spec for spec in lab.FILTERS if spec["id"] in FILTER_IDS}
    outcomes: list[dict[str, Any]] = []
    surface: list[dict[str, Any]] = []
    for filter_id in FILTER_IDS:
        filter_spec = filters[filter_id]
        for notional in NOTIONALS:
            for stress in STRESS_BPS:
                for horizon in HORIZONS:
                    for latency_id, latency_cap in LATENCY_BUCKETS:
                        strategy_id = f"{filter_id}|FIXED_{horizon}S|{notional:g}|stress{stress}|maxdelay_{latency_id}"
                        rows: list[dict[str, Any]] = []
                        for opportunity in windows:
                            if opportunity.get("record_type") not in {"INDEPENDENT_MARKET_WINDOW", "MARKET_WINDOW"}:
                                continue
                            signal = lab.timestamp_ms(opportunity)
                            if signal is None or lab.route_of(opportunity) not in {"PUMP_CURVE", "PUMP_BONDING_CURVE_TOP_LEVEL"}:
                                continue
                            entry_time = signal + lab.ENTRY_DELAY_MS
                            buy = lab.quote_buy(opportunity, notional, stress)
                            if not lab.eligible(filter_spec, opportunity, buy, notional):
                                continue
                            base = {
                                "strategy_id": strategy_id,
                                "mint": lab.mint_of(opportunity),
                                "filter": filter_id,
                                "exit_rule": f"FIXED_{horizon}S",
                                "notional_sol": notional,
                                "stress_bps": stress,
                                "max_checkpoint_delay_ms": latency_cap,
                                "signal_receive_ts_ms": signal,
                                "entry_ts_ms": entry_time,
                            }
                            deadline = entry_time + horizon * 1_000
                            checkpoint = checkpoint_for(opportunity, horizon)
                            observation_as_of = opportunity.get("observation_as_of_ts_ms")
                            if observation_as_of is not None and int(observation_as_of) < deadline:
                                rows.append(base | {"status": "IMMATURE_WINDOW", "pnl_sol": None,
                                                     "exit_reason": "HORIZON_NOT_REACHED", "mfe_sol": None, "mae_sol": None})
                                continue
                            if checkpoint is None:
                                rows.append(base | {"status": "MISSED_EXIT_MARK", "pnl_sol": None,
                                                     "exit_reason": "MISSING_NATIVE_CHECKPOINT", "mfe_sol": None, "mae_sol": None})
                                continue
                            delay = int(checkpoint.get("delay_after_deadline_ms") or 0)
                            if latency_cap is not None and delay > latency_cap:
                                rows.append(base | {"status": "MISSED_EXIT_MARK", "pnl_sol": None,
                                                     "exit_reason": "NATIVE_CHECKPOINT_DELAY_EXCEEDED", "checkpoint_delay_ms": delay,
                                                     "mfe_sol": None, "mae_sol": None})
                                continue
                            value = lab.quote_sell(checkpoint, buy.token_out, notional, stress) if buy is not None else None
                            if value is None:
                                rows.append(base | {"status": "FAILED_EXIT_QUOTE", "pnl_sol": None,
                                                     "exit_reason": "FAILED_NATIVE_CHECKPOINT_QUOTE", "checkpoint_delay_ms": delay,
                                                     "mfe_sol": None, "mae_sol": None})
                                continue
                            rows.append(base | {"status": "ACTIONABLE", "pnl_sol": value,
                                                 "exit_reason": "NATIVE_CHECKPOINT_FIXED_HORIZON", "checkpoint_delay_ms": delay,
                                                 "mfe_sol": value, "mae_sol": value})
                        outcomes.extend(rows)
                        surface.append({"strategy_id": strategy_id, "metrics": metric(rows)})
    surface.sort(key=lambda item: (-(item["metrics"].get("pnl_sol") or 0.0), item["strategy_id"]))
    return {
        "schema": "EDGESNIFFER_NATIVE_LATENCY_BOUNDED_STUDY_V1",
        "study_contract": contract,
        "source_sha256": sha256_file(path),
        "contract_sha256": sha256_file(contract_path),
        "admitted_windows": len(windows),
        "outcomes": outcomes,
        "surface": surface,
        "read_only": True,
        "trading_enabled": False,
        "policy_frozen": False,
        "promotion_blocked": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.input, args.contract)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"admitted_windows": result["admitted_windows"], "outcomes": len(result["outcomes"]),
                      "surface": len(result["surface"]), "policy_frozen": False,
                      "promotion_blocked": True}, sort_keys=True))


if __name__ == "__main__":
    main()
