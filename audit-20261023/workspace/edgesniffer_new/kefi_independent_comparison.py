#!/usr/bin/env python3
"""Outcome-blind selected-vs-independent-control comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path


def jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {key: None for key in ("min", "p25", "median", "p75", "p90", "max")}
    values = sorted(values)
    def at(p: float) -> float:
        return values[min(len(values) - 1, int((len(values) - 1) * p))]
    return {"min": values[0], "p25": at(.25), "median": at(.5), "p75": at(.75), "p90": at(.9), "max": values[-1]}


def profile(rows: list[dict]) -> dict:
    def vals(key: str) -> list[float]:
        return [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
    fee = [
        float(row["lp_fee_bps"] + row["protocol_fee_bps"] + row["coin_creator_bps"])
        for row in rows
        if all(isinstance(row.get(k), int) for k in ("lp_fee_bps", "protocol_fee_bps", "coin_creator_bps"))
    ]
    return {
        "rows": len(rows),
        "distinct_mints": len({row.get("selected_mint") for row in rows}),
        "distinct_pools": len({row.get("pool") for row in rows}),
        "base_amount": quantiles(vals("base_amount")),
        "pre_pool_base": quantiles(vals("pre_pool_base")),
        "pre_pool_quote": quantiles(vals("pre_pool_quote")),
        "pool_quote_delta": quantiles(vals("pool_quote_delta")),
        "user_quote_amount": quantiles(vals("user_quote_amount")),
        "amm_fee_bps": quantiles(fee),
        "quote_delta_over_pre_quote": quantiles(
            [float(row["pool_quote_delta"]) / float(row["pre_pool_quote"])
             for row in rows if row.get("pre_pool_quote")]
        ),
    }


def exit_profile(path: Path) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    results = report.get("results", {})
    returns = [
        float(item["best_exit"]["net_return_bps"])
        for item in results.values()
        if item.get("best_exit") and item["best_exit"].get("net_return_bps") is not None and not item.get("invalid")
    ]
    return {
        "entries": report.get("entries"),
        "entries_with_valid_exit_candidates": report.get("entries_with_valid_exit_candidates"),
        "entries_without_valid_exit_candidates": report.get("entries_without_valid_exit_candidates"),
        "fail_closed": report.get("fail_closed"),
        "best_exit_net_return_bps_hindsight_selected": quantiles(returns),
        "warning": "best exit is selected after observing the 120s replay window; descriptive only, not live-tradable performance",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selected", required=True, type=Path)
    ap.add_argument("--controls", required=True, type=Path)
    ap.add_argument("--selected-exits", required=True, type=Path)
    ap.add_argument("--control-exits", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ns = ap.parse_args()
    selected = list(jsonl(ns.selected))
    controls = list(jsonl(ns.controls))
    matched = Counter(row.get("matched_bucket") for row in controls)
    result = {
        "schema": "KEFI_INDEPENDENT_CONTEXT_COMPARISON_V1",
        "status": "DESCRIPTIVE_ONLY",
        "outcome_blind": True,
        "economic_outcomes_read": False,
        "selected_profile": profile(selected),
        "independent_control_profile": profile(controls),
        "matched_control_buckets": dict(sorted((str(k), v) for k, v in matched.items())),
        "selected_exit_profile": exit_profile(ns.selected_exits),
        "control_exit_profile": exit_profile(ns.control_exits),
        "control_contract": {
            "rule": "same closed source partition and UTC hour; deterministic SHA-256 rank; 3 controls per selected row",
            "selected_signatures_excluded": True,
            "selection_uses_replay_or_outcomes": False,
        },
        "interpretation": [
            "This is the first independent context contrast; it can show whether kEFi entry context is distinctive.",
            "It does not establish profitability, causal alpha, or a deployable trading rule.",
            "Hindsight-selected 120s exits are descriptive diagnostics only.",
        ],
        "input_sha256": {
            "selected": sha256(ns.selected),
            "controls": sha256(ns.controls),
            "selected_exits": sha256(ns.selected_exits),
            "control_exits": sha256(ns.control_exits),
        },
    }
    ns.output.parent.mkdir(parents=True, exist_ok=True)
    ns.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "selected": len(selected), "controls": len(controls)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
