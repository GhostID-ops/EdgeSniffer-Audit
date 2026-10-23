#!/usr/bin/env python3
"""Frozen-split, outcome-blind initial kEfi token-selection audit.

The development stage reads only the split's first forty snapshot files.  It
cannot access the listed holdout paths.  It verifies source hashes before
parsing and produces a machine-readable development audit only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import median

EXPECTED_MASTER = "aa287917553c2035f46378bc72f3de6dd5d76e7fa0f0677c3e96022e64a2e106"
EXPECTED_FINGERPRINT = "db08899d92f4d69d8c0234cfff9cbd05bd27dfb1365aed88d08b85f88cfc1b4a"
FEATURES = (
    "log_pre_pool_quote", "log_pre_pool_base", "amm_fee_bps", "observed_pool_age_seconds",
    "seconds_since_previous_observed_buy", "prior_pre_pool_quote_log_change", "prior_buy_count_5s",
    "prior_buy_count_20s", "prior_buy_count_60s", "prior_buy_quote_5s", "prior_buy_quote_20s",
    "prior_buy_quote_60s", "no_prior_observed_buy",
)
# All are calculated solely from a candidate's snapshot-time state.
EXTRA_FEATURES = (
    "log_sell_count_60s", "log_sell_quote_flow_60s", "net_count_60s", "net_quote_flow_60s",
    "buy_count_acceleration_5s_over_60s", "buy_quote_acceleration_5s_over_60s",
    "seconds_since_last_public_event", "quote_reserve_log_change",
)
SELECTED_EXTRAS = (
    "log_sell_quote_flow_60s",
    "seconds_since_last_public_event",
    "buy_count_acceleration_5s_over_60s",
    "buy_quote_acceleration_5s_over_60s",
)
EXPANDED_L2 = 8.0
EXPANDED_LEARNING_RATE = 0.08
EXPANDED_EPOCHS = 80


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, sort_keys=True, separators=(",", ":")); f.write("\n")
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def q(v: object) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def fee_bps(candidate: dict) -> float:
    value = candidate.get("fee_tuple", 0)
    if isinstance(value, dict):
        return sum(q(value.get(k)) for k in ("lp_fee_bps", "protocol_fee_bps", "coin_creator_bps", "cashback_bps", "buyback_bps"))
    if isinstance(value, (list, tuple)):
        return sum(q(x) for x in value)
    return q(value)


def feature_vector(c: dict, teacher_received_ns: int) -> dict[str, float]:
    quote = q(c.get("quote_reserve_raw")); base = q(c.get("base_reserve_raw")); prior = q(c.get("prior_pre_pool_quote_raw"))
    b5, b20, b60 = (q(c.get(k)) for k in ("buy_count_5s", "buy_count_20s", "buy_count_60s"))
    f5, f20, f60 = (q(c.get(k)) for k in ("buy_quote_flow_5s", "buy_quote_flow_20s", "buy_quote_flow_60s"))
    s60, sf60 = q(c.get("sell_count_60s")), q(c.get("sell_quote_flow_60s"))
    return {
        "log_pre_pool_quote": math.log1p(max(quote, 0)),
        "log_pre_pool_base": math.log1p(max(base, 0)),
        "amm_fee_bps": fee_bps(c),
        "observed_pool_age_seconds": math.log1p(min(max(q(c.get("observed_pool_age_seconds")), 0), 86400)),
        "seconds_since_previous_observed_buy": math.log1p(min(max(q(c.get("seconds_since_prior_observed_buy")), 0), 86400)),
        "prior_pre_pool_quote_log_change": 0.0 if quote <= 0 or prior <= 0 else math.log1p(quote) - math.log1p(prior),
        "prior_buy_count_5s": math.log1p(max(b5, 0)), "prior_buy_count_20s": math.log1p(max(b20, 0)),
        "prior_buy_count_60s": math.log1p(max(b60, 0)), "prior_buy_quote_5s": math.log1p(max(f5, 0)),
        "prior_buy_quote_20s": math.log1p(max(f20, 0)), "prior_buy_quote_60s": math.log1p(max(f60, 0)),
        "no_prior_observed_buy": 1.0 if bool(c.get("no_prior_buy")) else 0.0,
        "log_sell_count_60s": math.log1p(max(s60, 0)), "log_sell_quote_flow_60s": math.log1p(max(sf60, 0)),
        "net_count_60s": b60 - s60, "net_quote_flow_60s": f60 - sf60,
        "buy_count_acceleration_5s_over_60s": math.log1p(max(b5, 0)) - math.log1p(max(b60 / 12, 0)),
        "buy_quote_acceleration_5s_over_60s": math.log1p(max(f5, 0)) - math.log1p(max(f60 / 12, 0)),
        "seconds_since_last_public_event": max(0.0, (teacher_received_ns - q(c.get("last_event_unix_ns"))) / 1e9),
        "quote_reserve_log_change": 0.0 if quote <= 0 or prior <= 0 else math.log1p(quote) - math.log1p(prior),
    }


def score(values: dict[str, float], model: dict) -> float:
    z = [(values[name] - q(mean)) / q(scale) for name, mean, scale in zip(model["features"], model["standardization_means"], model["standardization_scales"])]
    linear = q(model["intercept"]) + sum(q(model["coefficients"][name]) * value for name, value in zip(model["features"], z))
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, linear))))


def rank_desc(values: list[float], chosen_index: int) -> tuple[int, float]:
    chosen = values[chosen_index]
    rank = 1 + sum(v > chosen for v in values)
    percentile = sum(v <= chosen for v in values) / len(values)
    return rank, percentile


def quantile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered: return float("nan")
    return ordered[round((len(ordered) - 1) * p)]


def verify_entry(entry: dict) -> None:
    snapshot, receipt = Path(entry["snapshot_path"]), Path(entry["receipt_path"])
    if sha256(snapshot) != entry["snapshot_sha256"] or sha256(receipt) != entry["receipt_sha256"]:
        raise RuntimeError("FAIL_CLOSED: split artifact hash mismatch")
    r = json.loads(receipt.read_text())
    required = ("warmup_complete", "pre_action_only", "teacher_event_excluded_from_state", "authoritative_pool_identity", "chosen_pool_previously_eligible")
    if not all(r.get(k) is True for k in required) or r.get("outcomes_or_pnl_accessed") is not False:
        raise RuntimeError("FAIL_CLOSED: snapshot receipt violates integrity contract")


def development(split_path: Path, fingerprint: Path, output: Path) -> None:
    split = json.loads(split_path.read_text())
    if split.get("master_manifest_sha256") != EXPECTED_MASTER or split.get("holdout_opened") is not False:
        raise RuntimeError("FAIL_CLOSED: invalid or opened split")
    dev, hold = split["development_snapshots"], split["unopened_selection_holdout_snapshots"]
    if len(dev) != 40 or len(hold) != 20:
        raise RuntimeError("FAIL_CLOSED: split must be exactly 40/20")
    if sha256(fingerprint) != EXPECTED_FINGERPRINT:
        raise RuntimeError("FAIL_CLOSED: frozen fingerprint SHA mismatch")
    model = json.loads(fingerprint.read_text())
    if tuple(model.get("features", ())) != FEATURES or not model.get("strict_preentry"):
        raise RuntimeError("FAIL_CLOSED: unexpected frozen feature schema")

    seen_mints: set[str] = set(); decisions = []; per_feature: dict[str, list[dict]] = {name: [] for name in EXTRA_FEATURES}
    for entry in dev:
        verify_entry(entry)
        snapshot = json.loads(Path(entry["snapshot_path"]).read_text())
        candidates = snapshot.get("candidates")
        chosen_mint, chosen_pool = snapshot.get("chosen_mint"), snapshot.get("chosen_pool")
        if not isinstance(candidates, list) or not candidates:
            raise RuntimeError("FAIL_CLOSED: invalid candidate set")
        # Primary NEW_TO_OBSERVED_WALLET universe: only pools not previously selected by the observed wallet.
        universe = [c for c in candidates if str(c.get("mint")) not in seen_mints]
        chosen_idx = next((i for i, c in enumerate(universe) if c.get("mint") == chosen_mint and c.get("pool") == chosen_pool), None)
        if chosen_idx is None:
            raise RuntimeError("FAIL_CLOSED: chosen mint absent from new-to-observed universe")
        vectors = [feature_vector(c, int(snapshot["teacher_received_at_unix_ns"])) for c in universe]
        scores = [score(v, model) for v in vectors]
        rank, percentile = rank_desc(scores, chosen_idx)
        record = {"teacher_slot": entry["teacher_slot"], "candidate_count": len(universe), "chosen_rank": rank, "chosen_percentile": percentile,
                  "top1": rank <= 1, "top3": rank <= 3, "top5": rank <= 5, "top10": rank <= 10, "mrr": 1 / rank,
                  "random_top1": 1 / len(universe), "random_top3": min(3 / len(universe), 1.0), "random_top5": min(5 / len(universe), 1.0), "random_top10": min(10 / len(universe), 1.0)}
        decisions.append(record)
        for name in EXTRA_FEATURES:
            vals = [v[name] for v in vectors]; r, pct = rank_desc(vals, chosen_idx)
            per_feature[name].append({"rank": r, "percentile": pct, "chosen_minus_set_median": vals[chosen_idx] - median(vals), "candidate_count": len(vals)})
        seen_mints.add(str(chosen_mint))

    def avg(name: str) -> float: return sum(q(d[name]) for d in decisions) / len(decisions)
    baseline = {key: avg(key) for key in ("top1", "top3", "top5", "top10", "mrr", "random_top1", "random_top3", "random_top5", "random_top10")}
    baseline.update({"median_chosen_percentile": median(d["chosen_percentile"] for d in decisions), "p25_chosen_percentile": quantile([d["chosen_percentile"] for d in decisions], .25), "p75_chosen_percentile": quantile([d["chosen_percentile"] for d in decisions], .75)})
    clues = {name: {"mean_chosen_percentile": sum(x["percentile"] for x in rows) / len(rows), "median_chosen_percentile": median(x["percentile"] for x in rows), "median_chosen_minus_set_median": median(x["chosen_minus_set_median"] for x in rows), "top_decile_rate": sum(x["percentile"] >= .9 for x in rows) / len(rows), "directionally_high": sum(x["chosen_minus_set_median"] > 0 for x in rows) / len(rows)} for name, rows in per_feature.items()}
    result = {"schema":"KEFI_INITIAL_SELECTION_DEVELOPMENT_AUDIT_V1", "split_sha256":sha256(split_path), "master_manifest_sha256":EXPECTED_MASTER, "frozen_fingerprint_sha256":EXPECTED_FINGERPRINT, "frozen_feature_schema":list(FEATURES), "primary_universe":"NEW_TO_OBSERVED_WALLET", "development_decisions":len(decisions), "unopened_holdout_decisions":20, "holdout_opened":False, "outcomes_or_pnl_accessed":False, "baseline":baseline, "per_decision":decisions, "additional_variables_investigated":list(EXTRA_FEATURES), "additional_variable_within_choice_audit":clues}
    atomic(output, result)


def load_development_groups(split: dict) -> tuple[list[list[dict[str, float]]], list[int]]:
    seen_mints: set[str] = set(); groups: list[list[dict[str, float]]] = []; chosen_indexes: list[int] = []
    for entry in split["development_snapshots"]:
        verify_entry(entry)
        snapshot = json.loads(Path(entry["snapshot_path"]).read_text())
        candidates = snapshot["candidates"]
        universe = [c for c in candidates if str(c.get("mint")) not in seen_mints]
        chosen = next((i for i, c in enumerate(universe) if c.get("mint") == snapshot["chosen_mint"] and c.get("pool") == snapshot["chosen_pool"]), None)
        if chosen is None: raise RuntimeError("FAIL_CLOSED: chosen absent while building development groups")
        stamp = int(snapshot["teacher_received_at_unix_ns"])
        groups.append([feature_vector(c, stamp) for c in universe]); chosen_indexes.append(chosen)
        seen_mints.add(str(snapshot["chosen_mint"]))
    return groups, chosen_indexes


def fit_expanded(split_path: Path, fingerprint: Path, audit_path: Path, output: Path, receipt: Path, tool: Path) -> None:
    split = json.loads(split_path.read_text())
    if split.get("holdout_opened") is not False or len(split.get("development_snapshots", [])) != 40:
        raise RuntimeError("FAIL_CLOSED: unavailable development split")
    if sha256(fingerprint) != EXPECTED_FINGERPRINT: raise RuntimeError("FAIL_CLOSED: fingerprint changed")
    audit = json.loads(audit_path.read_text())
    if audit.get("holdout_opened") is not False or audit.get("development_decisions") != 40:
        raise RuntimeError("FAIL_CLOSED: invalid development audit")
    names = list(FEATURES + SELECTED_EXTRAS)
    groups_dict, chosen = load_development_groups(split)
    all_rows = [row for group in groups_dict for row in group]
    means = [sum(row[name] for row in all_rows) / len(all_rows) for name in names]
    scales = [math.sqrt(sum((row[name] - mean) ** 2 for row in all_rows) / len(all_rows)) or 1.0 for name, mean in zip(names, means)]
    groups = [[[ (row[name] - means[index]) / scales[index] for index, name in enumerate(names)] for row in group] for group in groups_dict]
    beta = [0.0] * len(names); intercept = 0.0
    for _epoch in range(EXPANDED_EPOCHS):
        gradient = [0.0] * len(names); intercept_grad = 0.0
        for group, selected in zip(groups, chosen):
            logits = [intercept + sum(weight * value for weight, value in zip(beta, row)) for row in group]
            maximum = max(logits); exp = [math.exp(min(60.0, value - maximum)) for value in logits]; total = sum(exp)
            for index, (probability, row) in enumerate(zip((value / total for value in exp), group)):
                delta = probability - (1.0 if index == selected else 0.0)
                intercept_grad += delta
                for feature, value in enumerate(row): gradient[feature] += delta * value
        divisor = len(groups)
        intercept -= EXPANDED_LEARNING_RATE * intercept_grad / divisor
        for index in range(len(beta)):
            beta[index] -= EXPANDED_LEARNING_RATE * ((gradient[index] / divisor) + EXPANDED_L2 * beta[index])
    def ranks() -> dict[str, float]:
        result = {k: 0.0 for k in ("top1", "top3", "top5", "top10", "mrr")}
        for group, selected in zip(groups, chosen):
            score_values = [intercept + sum(weight * value for weight, value in zip(beta, row)) for row in group]
            rank, _ = rank_desc(score_values, selected)
            result["top1"] += rank <= 1; result["top3"] += rank <= 3; result["top5"] += rank <= 5; result["top10"] += rank <= 10; result["mrr"] += 1 / rank
        return {key: value / len(groups) for key, value in result.items()}
    model = {"schema":"KEFI_INITIAL_SELECTION_EXPANDED_CHOICE_MODEL_V1", "feature_names":names, "selected_additional_features":list(SELECTED_EXTRAS), "interactions":[], "standardization_means":means, "standardization_scales":scales, "coefficients":dict(zip(names, beta)), "intercept":intercept, "regularization":{"type":"l2","strength":EXPANDED_L2}, "optimizer":{"type":"full_batch_gradient_descent","learning_rate":EXPANDED_LEARNING_RATE,"epochs":EXPANDED_EPOCHS}, "primary_universe":"NEW_TO_OBSERVED_WALLET", "training_decisions":40, "holdout_opened":False, "outcomes_or_pnl_accessed":False}
    atomic(output, model)
    frozen = {"schema":"KEFI_INITIAL_SELECTION_DEVELOPMENT_FREEZE_V1", "split_path":str(split_path), "split_sha256":sha256(split_path), "development_audit_path":str(audit_path), "development_audit_sha256":sha256(audit_path), "frozen_fingerprint_sha256":EXPECTED_FINGERPRINT, "baseline_feature_schema":list(FEATURES), "investigated_additional_variables":list(EXTRA_FEATURES), "selected_additional_variables":list(SELECTED_EXTRAS), "selected_variable_reason":"development-only within-choice discrimination, pre-action observability, and nonredundancy; no economics accessed", "rejected_interactions":"none used; baseline development ranking saturated, so no interaction search was performed", "expanded_model_path":str(output), "expanded_model_sha256":sha256(output), "expanded_development_metrics":ranks(), "tool_path":str(tool), "tool_sha256":sha256(tool), "holdout_opened":False, "outcomes_or_pnl_accessed":False, "pnl_accessed":False}
    atomic(receipt, frozen)


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--development", action="store_true"); p.add_argument("--fit-expanded", action="store_true"); p.add_argument("--split", type=Path, required=True); p.add_argument("--fingerprint", type=Path, required=True); p.add_argument("--output", type=Path, required=True); p.add_argument("--development-audit", type=Path); p.add_argument("--freeze-receipt", type=Path)
    ns = p.parse_args()
    if ns.development == ns.fit_expanded: raise SystemExit("FAIL_CLOSED: choose exactly one stage")
    if ns.development: development(ns.split, ns.fingerprint, ns.output)
    else:
        if not ns.development_audit or not ns.freeze_receipt: raise SystemExit("FAIL_CLOSED: fit stage requires audit and freeze receipt")
        fit_expanded(ns.split, ns.fingerprint, ns.development_audit, ns.output, ns.freeze_receipt, Path(__file__))
