#!/usr/bin/env python3
"""One-shot outcome-blind evaluation of sealed kEFi selection snapshots."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import median

EXPECTED_FINGERPRINT_SHA256 = "db08899d92f4d69d8c0234cfff9cbd05bd27dfb1365aed88d08b85f88cfc1b4a"
FEATURES = (
    "log_pre_pool_quote", "log_pre_pool_base", "amm_fee_bps", "observed_pool_age_seconds",
    "seconds_since_previous_observed_buy", "prior_pre_pool_quote_log_change", "prior_buy_count_5s",
    "prior_buy_count_20s", "prior_buy_count_60s", "prior_buy_quote_5s", "prior_buy_quote_20s",
    "prior_buy_quote_60s", "no_prior_observed_buy",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def q(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def fee_bps(candidate: dict) -> float:
    value = candidate.get("fee_tuple", 0)
    if isinstance(value, dict):
        return sum(q(value.get(key)) for key in (
            "lp_fee_bps", "protocol_fee_bps", "coin_creator_bps", "cashback_bps", "buyback_bps",
        ))
    if isinstance(value, (list, tuple)):
        return sum(q(item) for item in value)
    return q(value)


def feature_vector(candidate: dict) -> dict[str, float]:
    quote = q(candidate.get("quote_reserve_raw"))
    base = q(candidate.get("base_reserve_raw"))
    prior = q(candidate.get("prior_pre_pool_quote_raw"))
    b5, b20, b60 = (q(candidate.get(key)) for key in (
        "buy_count_5s", "buy_count_20s", "buy_count_60s",
    ))
    f5, f20, f60 = (q(candidate.get(key)) for key in (
        "buy_quote_flow_5s", "buy_quote_flow_20s", "buy_quote_flow_60s",
    ))
    return {
        "log_pre_pool_quote": math.log1p(max(quote, 0)),
        "log_pre_pool_base": math.log1p(max(base, 0)),
        "amm_fee_bps": fee_bps(candidate),
        "observed_pool_age_seconds": math.log1p(min(max(q(candidate.get("observed_pool_age_seconds")), 0), 86400)),
        "seconds_since_previous_observed_buy": math.log1p(min(max(q(candidate.get("seconds_since_prior_observed_buy")), 0), 86400)),
        "prior_pre_pool_quote_log_change": 0.0 if quote <= 0 or prior <= 0 else math.log1p(quote) - math.log1p(prior),
        "prior_buy_count_5s": math.log1p(max(b5, 0)),
        "prior_buy_count_20s": math.log1p(max(b20, 0)),
        "prior_buy_count_60s": math.log1p(max(b60, 0)),
        "prior_buy_quote_5s": math.log1p(max(f5, 0)),
        "prior_buy_quote_20s": math.log1p(max(f20, 0)),
        "prior_buy_quote_60s": math.log1p(max(f60, 0)),
        "no_prior_observed_buy": 1.0 if bool(candidate.get("no_prior_buy")) else 0.0,
    }


def score(values: dict[str, float], model: dict) -> float:
    standardized = [
        (values[name] - q(mean)) / (q(scale) or 1.0)
        for name, mean, scale in zip(
            model["features"], model["standardization_means"], model["standardization_scales"],
        )
    ]
    linear = q(model["intercept"]) + sum(
        q(model["coefficients"][name]) * value
        for name, value in zip(model["features"], standardized)
    )
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, linear))))


def rank_desc(scores: list[float], chosen_index: int) -> tuple[int, float]:
    chosen = scores[chosen_index]
    return 1 + sum(value > chosen for value in scores), sum(value <= chosen for value in scores) / len(scores)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--fingerprint", type=Path, required=True)
    parser.add_argument("--opened-marker", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.opened_marker.exists() or args.output.exists():
        raise SystemExit("FAIL_CLOSED: prospective evaluation already opened or written")
    if sha256(args.fingerprint) != EXPECTED_FINGERPRINT_SHA256:
        raise SystemExit("FAIL_CLOSED: frozen fingerprint hash mismatch")
    model = json.loads(args.fingerprint.read_text())
    if (model.get("schema") != "KEFI_RICH_PREENTRY_FINGERPRINT_V2"
            or model.get("status") != "FROZEN_FOR_PROSPECTIVE_OUTCOME_BLIND_SHADOW"
            or model.get("strict_preentry") is not True
            or tuple(model.get("features", ())) != FEATURES):
        raise SystemExit("FAIL_CLOSED: frozen fingerprint contract mismatch")

    receipt_paths = sorted((args.snapshot_root / "snapshot-receipts").glob("*.json"))
    if not receipt_paths:
        raise SystemExit("FAIL_CLOSED: no sealed snapshot receipts")
    atomic(args.opened_marker, {
        "schema": "KEFI_FROZEN_SELECTION_PROSPECTIVE_EVALUATION_OPENED_V1",
        "fingerprint_sha256": EXPECTED_FINGERPRINT_SHA256,
        "evaluator_sha256": sha256(Path(__file__)),
        "receipt_count": len(receipt_paths),
        "outcomes_or_pnl_accessed": False,
    })

    seen_mints: set[str] = set()
    rows: list[dict] = []
    source_binding: tuple[str, int] | None = None
    previous_sequence = -1
    for receipt_path in receipt_paths:
        receipt = json.loads(receipt_path.read_text())
        snapshot_path = Path(receipt["snapshot_path"])
        if not snapshot_path.is_file() or sha256(snapshot_path) != receipt.get("snapshot_sha256"):
            raise RuntimeError("FAIL_CLOSED: snapshot receipt hash mismatch")
        if not all(receipt.get(key) is True for key in (
            "pre_action_only", "teacher_event_excluded_from_state", "authoritative_pool_identity",
        )):
            raise RuntimeError("FAIL_CLOSED: snapshot receipt integrity contract mismatch")
        snapshot = json.loads(snapshot_path.read_text())
        binding = (snapshot.get("source_instance_id"), snapshot.get("continuity_epoch"))
        if source_binding is None:
            source_binding = binding
        elif source_binding != binding:
            raise RuntimeError("FAIL_CLOSED: source epoch bridge")
        sequence = int(snapshot["teacher_durable_output_sequence"])
        if sequence <= previous_sequence:
            raise RuntimeError("FAIL_CLOSED: snapshot order or duplicate")
        previous_sequence = sequence
        if tuple(snapshot.get("frozen_feature_schema", ())) != FEATURES:
            raise RuntimeError("FAIL_CLOSED: snapshot feature schema mismatch")
        universe = [candidate for candidate in snapshot["candidates"] if str(candidate.get("mint")) not in seen_mints]
        chosen = next((index for index, candidate in enumerate(universe)
                       if candidate.get("pool") == snapshot.get("chosen_pool")
                       and candidate.get("mint") == snapshot.get("chosen_mint")), None)
        if chosen is None or len(universe) < 2:
            raise RuntimeError("FAIL_CLOSED: chosen absent or invalid prospective choice set")
        chosen_rank, percentile = rank_desc([score(feature_vector(candidate), model) for candidate in universe], chosen)
        rows.append({
            "ordinal": len(rows) + 1,
            "teacher_durable_output_sequence": sequence,
            "candidate_count": len(universe),
            "chosen_rank": chosen_rank,
            "chosen_percentile": percentile,
            "top1": chosen_rank <= 1,
            "top3": chosen_rank <= 3,
            "top5": chosen_rank <= 5,
            "top10": chosen_rank <= 10,
            "mrr": 1.0 / chosen_rank,
        })
        seen_mints.add(str(snapshot["chosen_mint"]))

    n = len(rows)
    output = {
        "schema": "KEFI_FROZEN_SELECTION_PROSPECTIVE_EVALUATION_V1",
        "fingerprint_sha256": EXPECTED_FINGERPRINT_SHA256,
        "evaluator_sha256": sha256(Path(__file__)),
        "source_instance_id": source_binding[0] if source_binding else None,
        "continuity_epoch": source_binding[1] if source_binding else None,
        "selection_snapshots": n,
        "primary_universe": "NEW_TO_OBSERVED_WALLET",
        "metrics": {
            "top1": sum(row["top1"] for row in rows) / n,
            "top3": sum(row["top3"] for row in rows) / n,
            "top5": sum(row["top5"] for row in rows) / n,
            "top10": sum(row["top10"] for row in rows) / n,
            "mrr": sum(row["mrr"] for row in rows) / n,
            "median_chosen_percentile": median(row["chosen_percentile"] for row in rows),
            "candidate_count_min": min(row["candidate_count"] for row in rows),
            "candidate_count_median": median(row["candidate_count"] for row in rows),
            "candidate_count_max": max(row["candidate_count"] for row in rows),
        },
        "per_selection": rows,
        "outcomes_or_pnl_accessed": False,
        "interpretation": "Frozen selection-fingerprint result only; not profitability, full-market completeness, or execution validation.",
    }
    atomic(args.output, output)
    print(sha256(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
