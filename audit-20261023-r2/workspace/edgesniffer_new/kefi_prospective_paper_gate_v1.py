#!/usr/bin/env python3
"""Fail-closed prospective kEFi paper gate.

Scores fresh BUY candidates with the sealed entry fingerprint and joins only
explicitly complete 120-second replay windows. It never reads outcomes/PnL,
submits transactions, or changes the frozen model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


FEATURES = [
    "log_base_amount",
    "log_pre_pool_quote",
    "log_pool_quote_delta",
    "relative_pool_impulse",
    "relative_user_quote",
    "amm_fee_bps",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def feature_row(row: dict) -> list[float]:
    base = float(row["base_amount"])
    pre_base = float(row["pre_pool_base"])
    quote_delta = float(row["pool_quote_delta"])
    pre_quote = float(row["pre_pool_quote"])
    user_quote = float(row["user_quote_amount"])
    if min(base, pre_base, quote_delta, pre_quote, user_quote) <= 0:
        raise ValueError("non-positive required entry field")
    return [
        math.log(base),
        math.log(pre_quote),
        math.log(quote_delta),
        base / pre_base,
        user_quote / pre_quote,
        float(row["amm_fee_bps"]),
    ]


def score(row: dict, model: dict) -> tuple[float, bool]:
    values = feature_row(row)
    means = model["standardization_means"]
    scales = model["standardization_scales"]
    z = [(x - m) / s for x, m, s in zip(values, means, scales)]
    linear = model["intercept"] + sum(model["coefficients"][name] * value for name, value in zip(FEATURES, z))
    probability = 1.0 / (1.0 + math.exp(-max(-700.0, min(700.0, linear))))
    return probability, probability >= 0.5


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--fresh-candidates", type=Path, required=True)
    ap.add_argument("--replay-windows", type=Path, required=True)
    ap.add_argument("--output-signals", type=Path, required=True)
    ap.add_argument("--output-summary", type=Path, required=True)
    ap.add_argument("--expected-model-sha256")
    args = ap.parse_args()

    model = json.loads(args.model.read_text(encoding="utf-8"))
    actual_model_sha256 = sha256(args.model)
    if args.expected_model_sha256 and actual_model_sha256 != args.expected_model_sha256:
        raise SystemExit(f"frozen model hash mismatch: {actual_model_sha256}")
    candidates = load_jsonl(args.fresh_candidates)
    windows = load_jsonl(args.replay_windows)
    if model.get("schema") != "KEFI_ENTRY_FINGERPRINT_DISCOVERY_V1":
        raise SystemExit("wrong or unsealed model schema")
    if model.get("outcome_blind") is not True:
        raise SystemExit("model is not outcome-blind")
    if model.get("features") != FEATURES:
        raise SystemExit("model feature schema mismatch")

    by_key = {}
    for window in windows:
        key = (window.get("target_signature"), window.get("target_event_index", 0))
        if key in by_key:
            raise SystemExit(f"duplicate replay window identity: {key}")
        by_key[key] = window

    signals = []
    rejects = Counter()
    seen = set()
    for row in candidates:
        key = (row.get("signature"), row.get("event_index", 0))
        if key in seen:
            rejects["duplicate_candidate"] += 1
            continue
        seen.add(key)
        if row.get("side") != "BUY":
            rejects["non_buy"] += 1
            continue
        window = by_key.get(key)
        if window is None:
            rejects["missing_replay_window"] += 1
            continue
        if window.get("window_complete") is not True:
            rejects["window_not_explicitly_complete"] += 1
            continue
        if window.get("fail_closed") is True:
            rejects["replay_fail_closed"] += 1
            continue
        try:
            probability, match = score(row, model)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            rejects["invalid_entry_context"] += 1
            continue
        signals.append({
            "signature": row["signature"],
            "event_index": row.get("event_index", 0),
            "selected_mint": row.get("selected_mint"),
            "pool": row.get("pool"),
            "source_partition": row.get("source_partition"),
            "received_at_unix_ns": row.get("received_at_unix_ns"),
            "fingerprint_probability": probability,
            "paper_fingerprint_match": match,
            "replay_window_identity": window.get("window_id", key[0]),
            "window_complete": True,
            "outcome": None,
            "pnl": None,
            "execution_enabled": False,
        })

    args.output_signals.parent.mkdir(parents=True, exist_ok=True)
    args.output_summary.parent.mkdir(parents=True, exist_ok=True)
    with args.output_signals.open("w", encoding="utf-8") as f:
        for row in signals:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    summary = {
        "schema": "KEFI_PROSPECTIVE_PAPER_GATE_V1",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "READY_WITH_SIGNALS" if signals else "WAITING_FOR_COMPLETE_FRESH_WINDOWS",
        "model_sha256": actual_model_sha256,
        "fresh_candidates_sha256": sha256(args.fresh_candidates),
        "replay_windows_sha256": sha256(args.replay_windows),
        "candidate_rows": len(candidates),
        "complete_windows": len(signals),
        "paper_matches": sum(1 for row in signals if row["paper_fingerprint_match"]),
        "paper_non_matches": sum(1 for row in signals if not row["paper_fingerprint_match"]),
        "rejects": dict(sorted(rejects.items())),
        "scientific_guards": {
            "model_modified": False,
            "outcomes_accessed": False,
            "pnl_accessed": False,
            "execution_enabled": False,
            "missing_windows_fail_closed": True,
            "cross_partition_window_bridging": False,
        },
    }
    args.output_summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
