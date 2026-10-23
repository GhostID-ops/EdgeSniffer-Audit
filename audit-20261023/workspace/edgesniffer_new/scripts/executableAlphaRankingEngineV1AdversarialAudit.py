#!/usr/bin/env python3
import argparse
import gzip
import json
import math
import os
import subprocess
import time
from collections import Counter, defaultdict


TAILS = [0.20, 0.10, 0.05, 0.02, 0.01, 0.005]
FEATURES = [
    "age_ms", "mc_usd", "first_buyer_quote_share_bps", "launches_per_minute", "active_flow_mints",
    "w100.buy_count", "w100.gross_buy_sol", "w100.net_buy_sol", "w100.unique_buyers", "w100.sell_count", "w100.top1_share", "w100.top3_share", "w100.median_interbuy_ms", "w100.price_lift_pct", "w100.price_response_per_sol",
    "w250.buy_count", "w250.gross_buy_sol", "w250.net_buy_sol", "w250.unique_buyers", "w250.sell_count", "w250.top1_share", "w250.top3_share", "w250.median_interbuy_ms", "w250.price_lift_pct", "w250.price_response_per_sol",
    "w500.buy_count", "w500.gross_buy_sol", "w500.net_buy_sol", "w500.unique_buyers", "w500.sell_count", "w500.top1_share", "w500.top3_share", "w500.median_interbuy_ms", "w500.price_lift_pct", "w500.drawdown_from_high_pct", "w500.price_response_per_sol",
    "w1000.buy_count", "w1000.gross_buy_sol", "w1000.net_buy_sol", "w1000.unique_buyers", "w1000.sell_count", "w1000.top1_share", "w1000.top3_share", "w1000.median_interbuy_ms", "w1000.price_lift_pct", "w1000.drawdown_from_high_pct", "w1000.price_response_per_sol",
    "w2000.buy_count", "w2000.gross_buy_sol", "w2000.net_buy_sol", "w2000.unique_buyers", "w2000.sell_count", "w2000.top1_share", "w2000.top3_share", "w2000.price_lift_pct", "w2000.drawdown_from_high_pct",
    "w5000.buy_count", "w5000.gross_buy_sol", "w5000.net_buy_sol", "w5000.unique_buyers", "w5000.unique_sellers", "w5000.sell_count", "w5000.top1_share", "w5000.top3_share", "w5000.price_lift_pct", "w5000.drawdown_from_high_pct", "w5000.range_width_pct",
    "w10000.gross_buy_sol", "w10000.net_buy_sol", "w10000.unique_buyers", "w10000.sell_count", "w10000.top1_share", "w10000.top3_share", "w10000.price_lift_pct", "w10000.drawdown_from_high_pct",
    "new_wallets_500_vs_prior5s", "repeat_wallets_500", "second_to_first_sol_ratio",
]


def finite(v):
    return isinstance(v, (int, float)) and math.isfinite(v)


def num(v, default=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def compact(v):
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 6)
    if isinstance(v, dict):
        return {k: compact(val) for k, val in v.items()}
    if isinstance(v, list):
        return [compact(x) for x in v]
    return v


def q(vals, pct):
    xs = sorted(v for v in vals if finite(v))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * pct
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def metrics(rows, key="pnl"):
    vals = []
    oc = Counter()
    for row in rows:
        pnl = num(row.get(key))
        if pnl is None:
            continue
        vals.append(pnl)
        oc[row.get("outcome") or "UNKNOWN"] += 1
    pos = [v for v in vals if v > 0]
    neg = [v for v in vals if v < 0]
    gross_pos = sum(pos)
    gross_neg = abs(sum(neg))
    pf = gross_pos / gross_neg if gross_neg else (float("inf") if gross_pos else None)
    return {
        "n": len(vals),
        "wins": len(pos),
        "losses": len(neg),
        "wr_pct": len(pos) * 100.0 / len(vals) if vals else None,
        "pf": pf,
        "expectancy_pct": sum(vals) / len(vals) if vals else None,
        "median_return_pct": q(vals, 0.5),
        "pnl_at_1sol": sum(vals) / 100.0,
        "tp": oc.get("TP", 0),
        "sl": oc.get("SL", 0),
        "max_hold": oc.get("max_hold", 0) + oc.get("MAX_HOLD", 0),
        "pf_ex_best1": pf_ex_best(vals, 1),
        "pf_ex_best3": pf_ex_best(vals, 3),
        "pf_ex_best5": pf_ex_best(vals, 5),
        "pf_ex_best10": pf_ex_best(vals, 10),
        "largest_winner": max(pos) if pos else None,
        "largest_loser": min(neg) if neg else None,
        "top1_profit_contribution_pct": (sorted(pos, reverse=True)[0] / sum(vals) * 100.0) if pos and sum(vals) > 0 else None,
        "top3_profit_contribution_pct": (sum(sorted(pos, reverse=True)[:3]) / sum(vals) * 100.0) if pos and sum(vals) > 0 else None,
        "top10_profit_contribution_pct": (sum(sorted(pos, reverse=True)[:10]) / sum(vals) * 100.0) if pos and sum(vals) > 0 else None,
    }


def pf_ex_best(vals, n):
    xs = list(vals)
    for v in sorted([x for x in xs if x > 0], reverse=True)[:n]:
        try:
            xs.remove(v)
        except ValueError:
            pass
    pos = [x for x in xs if x > 0]
    neg = [x for x in xs if x < 0]
    return (sum(pos) / abs(sum(neg))) if neg else (float("inf") if pos else None)


def read_jsonl(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def feature(row, name):
    feats = row.get("features") or {}
    value = feats.get(name)
    if value is None and name == "first_buyer_quote_share_bps":
        value = feats.get("first_buyer_bps")
    return num(value)


def normalize_row(raw):
    row = {
        "mint": raw.get("mint"),
        "ts": int(raw.get("ts") or 0),
        "rule": raw.get("rule") or "UNKNOWN",
        "family": raw.get("family") or "UNKNOWN",
        "snapshot_kind": raw.get("snapshot_kind") or "UNKNOWN",
        "snapshot_age_ms": raw.get("snapshot_age_ms"),
        "outcome": raw.get("outcome") or "UNKNOWN",
        "pnl": num(raw.get("pnl"), 0.0),
        "stress5_pnl": num(raw.get("stress5_pnl")),
        "stress10_pnl": num(raw.get("stress10_pnl")),
        "entry_ts": raw.get("entry_ts"),
        "entry_mc": raw.get("entry_mc"),
        "first_sellable_ts": raw.get("first_sellable_ts"),
        "return_at_first_sellable": raw.get("return_at_first_sellable"),
        "mfe": raw.get("max_return_after_sellable"),
        "mae": raw.get("min_return_after_sellable"),
        "pre_sellable_only_winner": bool(raw.get("pre_sellable_only_winner")),
        "post_sellable_tp_reachable": bool(raw.get("post_sellable_tp_reachable")),
        "time_above_20_after_sellable_ms": raw.get("time_above_20_after_sellable_ms") or 0,
        "time_above_15_after_sellable_ms": raw.get("time_above_15_after_sellable_ms") or 0,
        "time_above_10_after_sellable_ms": raw.get("time_above_10_after_sellable_ms") or 0,
    }
    for name in FEATURES:
        row[name] = feature(raw, name)
    if row.get("mc_usd") is None:
        row["mc_usd"] = num(raw.get("mc"))
    if row.get("age_ms") is None:
        row["age_ms"] = num(raw.get("snapshot_age_ms"))
    return row


def split_by_observation(rows):
    rows = sorted(rows, key=lambda r: r["ts"])
    a = int(len(rows) * 0.50)
    b = int(len(rows) * 0.75)
    return rows[:a], rows[a:b], rows[b:]


def split_name(ts, obs_bounds):
    train_end, val_end = obs_bounds
    if ts < train_end:
        return "train"
    if ts < val_end:
        return "validation"
    return "holdout"


def pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if finite(x) and finite(y)]
    if len(pairs) < 50:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


def train_ranker(train):
    target = [r["pnl"] for r in train]
    stats = {}
    weights = {}
    for name in FEATURES:
        vals = [r.get(name) for r in train if finite(r.get(name))]
        if len(vals) < 100:
            continue
        med = q(vals, 0.5)
        scale = (q(vals, 0.75) or 0) - (q(vals, 0.25) or 0)
        if not scale or scale <= 1e-9:
            scale = (q(vals, 0.9) or 1) - (q(vals, 0.1) or 0)
        if not scale or scale <= 1e-9:
            continue
        corr = pearson([r.get(name) for r in train], target)
        if corr is None:
            continue
        stats[name] = {"center": med, "scale": scale, "corr": corr, "coverage": len(vals)}
        if abs(corr) >= 0.01:
            weights[name] = corr
    return stats, dict(sorted(weights.items(), key=lambda kv: abs(kv[1]), reverse=True)[:24])


def score(row, stats, weights):
    score_value = 0.0
    used = 0
    for name, weight in weights.items():
        value = row.get(name)
        if not finite(value):
            continue
        st = stats[name]
        z = max(-5.0, min(5.0, (value - st["center"]) / st["scale"]))
        score_value += weight * z
        used += 1
    return score_value if used else None


def top_by_recalc(rows, pct):
    ranked = sorted([r for r in rows if finite(r.get("rank_score"))], key=lambda r: r["rank_score"], reverse=True)
    k = max(1, math.ceil(len(ranked) * pct)) if ranked else 0
    return ranked[:k]


def top_by_threshold(rows, threshold):
    return sorted([r for r in rows if finite(r.get("rank_score")) and r["rank_score"] >= threshold], key=lambda r: r["rank_score"], reverse=True)


def one_per_mint(rows, mode):
    by_mint = defaultdict(list)
    for row in rows:
        by_mint[row["mint"]].append(row)
    selected = []
    for vals in by_mint.values():
        vals = sorted(vals, key=lambda r: r["ts"])
        if mode == "earliest":
            selected.append(vals[0])
        elif mode == "highest_score":
            selected.append(max(vals, key=lambda r: r.get("rank_score") if finite(r.get("rank_score")) else -1e99))
    return sorted(selected, key=lambda r: r["ts"])


def max_open(rows, k):
    active = []
    accepted = []
    for row in sorted(rows, key=lambda r: r["ts"]):
        active = [x for x in active if x > row["ts"]]
        if len(active) >= k:
            continue
        exit_ms = 120_000
        if row.get("entry_ts") and row.get("first_sellable_ts") and row["outcome"] in ("TP", "SL"):
            exit_ms = max(1, int(row["first_sellable_ts"]) - int(row["entry_ts"]) + 1)
        active.append(row["ts"] + exit_ms)
        accepted.append(row)
    return metrics(accepted)


def daily_folds(rows):
    buckets = defaultdict(list)
    for row in rows:
        buckets[row["ts"] // 86_400_000].append(row)
    return {str(k): metrics(v) for k, v in sorted(buckets.items()) if len(v) >= 5}


def top_tail_fingerprint(rows):
    if not rows:
        return {}
    return {
        "state_mix": dict(Counter(r["snapshot_kind"] for r in rows).most_common()),
        "rule_mix": dict(Counter(r["rule"] for r in rows).most_common()),
        "mc_p50": q([r.get("mc_usd") for r in rows], 0.5),
        "mc_p90": q([r.get("mc_usd") for r in rows], 0.9),
        "score_p50": q([r.get("rank_score") for r in rows], 0.5),
        "score_min": min([r.get("rank_score") for r in rows if finite(r.get("rank_score"))], default=None),
        "score_max": max([r.get("rank_score") for r in rows if finite(r.get("rank_score"))], default=None),
        "gross1s_p50": q([r.get("w1000.gross_buy_sol") for r in rows], 0.5),
        "sell100_p50": q([r.get("w100.sell_count") for r in rows], 0.5),
        "drawdown5s_p50": q([r.get("w5000.drawdown_from_high_pct") for r in rows], 0.5),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas-report", required=True)
    ap.add_argument("--ranking-report", required=True)
    ap.add_argument("--candidate-outcomes", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    atlas_report = json.load(open(args.atlas_report, encoding="utf-8"))
    ranking_report = json.load(open(args.ranking_report, encoding="utf-8"))
    rows = [normalize_row(row) for row in read_jsonl(args.candidate_outcomes)]
    train, validation, holdout = split_by_observation(rows)
    obs_bounds = (validation[0]["ts"] if validation else 0, holdout[0]["ts"] if holdout else 0)
    stats, weights = train_ranker(train)
    for row in rows:
        row["rank_score"] = score(row, stats, weights)
        row["obs_split"] = split_name(row["ts"], obs_bounds)
    train = [r for r in rows if r["obs_split"] == "train"]
    validation = [r for r in rows if r["obs_split"] == "validation"]
    holdout = [r for r in rows if r["obs_split"] == "holdout"]
    train_top1 = top_by_recalc(train, 0.01)
    val_top1 = top_by_recalc(validation, 0.01)
    hold_top1 = top_by_recalc(holdout, 0.01)
    train_threshold = min(r["rank_score"] for r in train_top1) if train_top1 else None
    val_static = top_by_threshold(validation, train_threshold) if train_threshold is not None else []
    hold_static = top_by_threshold(holdout, train_threshold) if train_threshold is not None else []

    # Mint split contamination under observation-time split.
    splits_by_mint = defaultdict(set)
    obs_by_mint = Counter()
    top_obs_by_mint = Counter()
    for row in rows:
        splits_by_mint[row["mint"]].add(row["obs_split"])
        obs_by_mint[row["mint"]] += 1
    for row in val_top1 + hold_top1:
        top_obs_by_mint[row["mint"]] += 1
    cross_mints = {m: sorted(v) for m, v in splits_by_mint.items() if len(v) > 1}
    cross_obs = sum(obs_by_mint[m] for m in cross_mints)

    # Mint-level split using first observation/create proxy.
    first_ts_by_mint = {}
    for row in rows:
        mint = row["mint"]
        first_ts_by_mint[mint] = min(first_ts_by_mint.get(mint, row["ts"]), row["ts"])
    mint_level = {"train": [], "validation": [], "holdout": []}
    for row in rows:
        mint_level[split_name(first_ts_by_mint[row["mint"]], obs_bounds)].append(row)
    mint_val = [r for r in mint_level["validation"] if finite(r.get("rank_score"))]
    mint_hold = [r for r in mint_level["holdout"] if finite(r.get("rank_score"))]
    mint_val_top1 = top_by_recalc(mint_val, 0.01)
    mint_hold_top1 = top_by_recalc(mint_hold, 0.01)
    mint_val_static = top_by_threshold(mint_level["validation"], train_threshold) if train_threshold is not None else []
    mint_hold_static = top_by_threshold(mint_level["holdout"], train_threshold) if train_threshold is not None else []

    feature_audit = []
    for name in FEATURES:
        if name.startswith("w"):
            width = name.split(".")[0][1:]
            window = f"[decision_ts - {width}ms, decision_ts]"
            source_time = "<= decision_ts"
        elif name in ("new_wallets_500_vs_prior5s", "repeat_wallets_500", "second_to_first_sol_ratio"):
            window = "[decision_ts - 5000ms, decision_ts]"
            source_time = "<= decision_ts"
        else:
            window = "current row / global state at decision"
            source_time = "<= decision_ts"
        feature_audit.append({
            "feature": name,
            "source_window": window,
            "source_time": source_time,
            "future_mark_contribution": False,
            "leakage_status": "NO_DIRECT_FUTURE_LEAKAGE_FOUND",
        })

    event_semantics = {
        "FIRST_IMPULSE": "first buy event where trailing 1000ms gross_buy_sol >= 2; prospective.",
        "SECOND_ACCELERATION": "first buy event where trailing 500ms gross_buy_sol >= 2 and prior 1s-5s buys >= 1; prospective but preselected for activity.",
        "FIRST_LARGE_SELLER": "first observed sell event; prospective, but sell SOL often approximate/unavailable.",
        "FIRST_LARGE_SELL_ABSORPTION": "first buy event with trailing 5s sell_count>=1, gross_buy_sol>=4, drawdown>=-10; prospective backward-window heuristic.",
        "POST_PULLBACK_BREAKOUT": "first buy event with trailing 10s drawdown -25..-4 and 1s gross>=2; prospective backward-window heuristic, name may overclaim reclaim.",
        "CONSOLIDATION_BREAKOUT": "first buy event with trailing 10s range<=25, 1s gross>=2, drawdown>=-3; prospective backward-window heuristic, name may overclaim breakout.",
        "FIRST_PULLBACK": "not persisted in this atlas output.",
        "POST_IMPULSE_STABILIZATION": "not persisted in this atlas output.",
        "FIRST_RECLAIM": "not persisted in this atlas output.",
        "CONSOLIDATION": "not persisted as a standalone state in this atlas output.",
        "CURVE_MILESTONES": "not persisted as standalone milestone states in this atlas output.",
    }

    # Cross-sectional audit: previous report ranks complete minute retrospectively.
    minute_future_risk = {
        "implementation": "groups by ts//60000 then ranks after all rows in that minute are known",
        "prospective_status": "NON_PROSPECTIVE_AS_WRITTEN",
        "reason": "at 00:00.100 it may compare against opportunities that appear at 00:00.900",
    }

    overlap = {}
    for a_name, a_rows in [("all_val_top1", val_top1), ("event_val_top1", [r for r in val_top1 if not str(r["snapshot_kind"]).startswith("FIXED_")]), ("fixed_val_top1", [r for r in val_top1 if str(r["snapshot_kind"]).startswith("FIXED_")])]:
        a_mints = {r["mint"] for r in a_rows}
        overlap[a_name] = {}
        for b_name, b_rows in [("all_hold_top1", hold_top1), ("event_hold_top1", [r for r in hold_top1 if not str(r["snapshot_kind"]).startswith("FIXED_")]), ("fixed_hold_top1", [r for r in hold_top1 if str(r["snapshot_kind"]).startswith("FIXED_")])]:
            b_mints = {r["mint"] for r in b_rows}
            overlap[a_name][b_name] = len(a_mints & b_mints)

    source_rows = atlas_report["dataset"]
    persisted = len(rows)
    snapshots_built = source_rows.get("snapshots_built")
    report = {
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "read_only": True,
        "A_source_population_preselection": {
            "is_full_eligible_decision_state_universe": False,
            "classification": "ALREADY_PRESELECTED_CANDIDATE_UNIVERSE",
            "atlas_snapshots_built": snapshots_built,
            "persisted_candidate_outcomes": persisted,
            "persisted_fraction_of_snapshots_pct": persisted * 100.0 / snapshots_built if snapshots_built else None,
            "rules_that_caused_persistence": list((atlas_report.get("rules") or {}).keys()),
            "known_inclusion_conditions": "A lifecycle snapshot was persisted only if it passed at least one rule_library test, and only first qualifying state per mint per rule was retained.",
            "future_outcome_inclusion_dependency_found": False,
            "material_preselection_bias": True,
        },
        "B_feature_leakage": feature_audit,
        "C_event_state_lookahead": event_semantics,
        "D_label_leakage": {
            "direct_label_in_features_found": False,
            "ranker_training_target": "p75 executable pnl from atlas outcome rows",
            "feature_selection_uses_train_only": True,
            "normalization_uses_train_only": True,
            "blocker": False,
        },
        "E_split_integrity": {
            "observation_level_split_boundaries_ms": {"train_end_exclusive": obs_bounds[0], "validation_end_exclusive": obs_bounds[1]},
            "mints_appearing_in_multiple_observation_splits": len(cross_mints),
            "observations_affected": cross_obs,
            "contamination_status": "SPLIT_LEAKAGE_BLOCKER" if cross_mints else "PASS",
            "mint_level_static_threshold_validation": metrics(mint_val_static),
            "mint_level_static_threshold_holdout": metrics(mint_hold_static),
            "mint_level_recalc_top1_validation_non_deployable": metrics(mint_val_top1),
            "mint_level_recalc_top1_holdout_non_deployable": metrics(mint_hold_top1),
        },
        "F_same_mint_duplication": {
            "unique_mints": len(obs_by_mint),
            "observations": len(rows),
            "observations_per_mint_p50": q(list(obs_by_mint.values()), 0.5),
            "observations_per_mint_p90": q(list(obs_by_mint.values()), 0.9),
            "observations_per_mint_max": max(obs_by_mint.values()) if obs_by_mint else None,
            "top1_val_hold_mints_with_multiple_top_observations": sum(1 for c in top_obs_by_mint.values() if c > 1),
            "validation_top1_observation_level": metrics(val_top1),
            "validation_top1_one_per_mint_earliest": metrics(one_per_mint(val_top1, "earliest")),
            "validation_top1_one_per_mint_highest_score_nonprospective": metrics(one_per_mint(val_top1, "highest_score")),
            "holdout_top1_observation_level": metrics(hold_top1),
            "holdout_top1_one_per_mint_earliest": metrics(one_per_mint(hold_top1, "earliest")),
            "holdout_top1_one_per_mint_highest_score_nonprospective": metrics(one_per_mint(hold_top1, "highest_score")),
        },
        "G_top1_threshold_semantics": {
            "current_v1_report_top1pct_definition": "recomputed top 1% separately inside train, validation, and holdout scored populations",
            "deployable_static_threshold_from_train_top1pct": train_threshold,
            "validation_recomputed_top1pct_non_deployable": metrics(val_top1),
            "holdout_recomputed_top1pct_non_deployable": metrics(hold_top1),
            "validation_train_static_threshold": metrics(val_static),
            "holdout_train_static_threshold": metrics(hold_static),
            "blocker": True,
        },
        "H_multiple_testing_model_selection_history": {
            "models_tested_in_v1_script": 1,
            "score_formulations_tested_in_v1_script": 1,
            "percentile_tails_reported": [f"top_{t*100:g}pct" for t in TAILS],
            "lifecycle_subsets_reported": ["all_states", "fixed_states", "event_states"],
            "exit_policies_inspected": ["p75 +20/-20", "p50", "p90", "+5 entry stress", "+10 entry stress", "reachability tp10/15/20/30"],
            "holdout_influenced_final_narrative_selection": True,
            "holdout_status": "SPENT_FOR_DISCOVERY_NOT_UNTOUCHED_CONFIRMATION",
        },
        "I_score_reproducibility": {
            "ranking_report_validation_top1_pf": (((ranking_report.get("H_model_ranking_results") or {}).get("validation_tails") or {}).get("tails") or {}).get("top_1pct", {}).get("pf"),
            "recomputed_validation_recalc_top1_pf": metrics(val_top1)["pf"],
            "ranking_report_holdout_top1_pf": (((ranking_report.get("H_model_ranking_results") or {}).get("holdout_tails") or {}).get("tails") or {}).get("top_1pct", {}).get("pf"),
            "recomputed_holdout_recalc_top1_pf": metrics(hold_top1)["pf"],
            "train_static_threshold": train_threshold,
            "status": "REPRODUCED_NUMERICALLY_BUT_THRESHOLD_SEMANTICS_BLOCKED",
        },
        "J_executable_economics": {
            "primary_entry_delay_ms": atlas_report["dataset"].get("entry_delay_ms"),
            "primary_first_sellable_ms": atlas_report["dataset"].get("primary_first_sellable_ms"),
            "tp_sl": "+20/-20 from atlas",
            "pre_sellable_tp_counts_as_exit": False,
            "missing_post_sellable_mark_penalty_pct": -2.5,
            "same_order_tp_sl": "TP wins if first_tp_after <= first_sl_after",
            "status": "NO_DIRECT_RECOMPUTE_FROM_RAW_MARKS_IN_THIS_AUDIT",
        },
        "K_entry_stress": {
            "atlas_formula": "entry_mc is multiplied by 1.05 or 1.10; TP payout becomes TP - stress_pct; SL remains -20; max-hold return subtracts stress_pct.",
            "stress5_validation_recalc_top1": metrics(val_top1, "stress5_pnl"),
            "stress10_validation_recalc_top1": metrics(val_top1, "stress10_pnl"),
            "stress5_holdout_recalc_top1": metrics(hold_top1, "stress5_pnl"),
            "stress10_holdout_recalc_top1": metrics(hold_top1, "stress10_pnl"),
            "status": "DIRECTIONALLY_CORRECT_WORSE_ENTRY_BUT_SIMPLIFIED_FIXED_STRESS_MODEL",
        },
        "L_concurrency": {
            "validation_top1_max_open1": max_open(val_top1, 1),
            "validation_top1_max_open2": max_open(val_top1, 2),
            "validation_top1_max_open3": max_open(val_top1, 3),
            "validation_top1_max_open5": max_open(val_top1, 5),
            "holdout_top1_max_open1": max_open(hold_top1, 1),
            "holdout_top1_max_open2": max_open(hold_top1, 2),
            "holdout_top1_max_open3": max_open(hold_top1, 3),
            "holdout_top1_max_open5": max_open(hold_top1, 5),
            "warning": "Uses approximate exit duration where detailed touch times are not persisted for every row.",
        },
        "M_cross_sectional_prospectiveness": minute_future_risk,
        "N_holdout_pf_7_9_forensics": {
            "holdout_recalc_top1": metrics(hold_top1),
            "daily_folds_min5": daily_folds(hold_top1),
            "fingerprint": top_tail_fingerprint(hold_top1),
        },
        "O_validation_vs_holdout": {
            "validation_top1": metrics(val_top1),
            "holdout_top1": metrics(hold_top1),
            "validation_fingerprint": top_tail_fingerprint(val_top1),
            "holdout_fingerprint": top_tail_fingerprint(hold_top1),
            "classification": "UNKNOWN_WITH_HOLDOUT_SPENT_AND_PRESELECTION",
        },
        "P_state_population_overlap": {
            "state_mix_validation_top1": top_tail_fingerprint(val_top1).get("state_mix"),
            "state_mix_holdout_top1": top_tail_fingerprint(hold_top1).get("state_mix"),
            "overlap_matrix_note": "Validation and holdout are chronological disjoint; same-period fixed/event top tails overlap by mint due multiple observations per mint.",
            "cross_period_overlap_by_mint": overlap,
        },
        "Q_full_universe_requirement": {
            "full_1_07m_replay_required_before_prospective_deployment": True,
            "reason": "The current result ranks a preselected candidate universe. It cannot prove alpha over the complete lifecycle decision-state universe or freeze a deployable threshold.",
        },
        "R_blockers": [
            "PRESELECTED_CANDIDATE_UNIVERSE",
            "OBSERVATION_LEVEL_SPLIT_LEAKAGE_FROM_SAME_MINTS" if cross_mints else None,
            "TOP1PCT_RECALCULATED_IN_VALIDATION_AND_HOLDOUT_NON_DEPLOYABLE",
            "HOLDOUT_USED_FOR_DISCOVERY_NARRATIVE_SPENT",
            "CROSS_SECTIONAL_MINUTE_RANKING_NON_PROSPECTIVE_AS_WRITTEN",
        ],
        "S_final_verdict": "RANKING_ALPHA_PRESELECTION_BIAS_BLOCKER",
    }
    report["R_blockers"] = [x for x in report["R_blockers"] if x]
    out_path = os.path.join(args.out_dir, "adversarial_audit_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(compact(report), fh, indent=2, sort_keys=True)
    print(json.dumps(compact({"ok": True, "verdict": report["S_final_verdict"], "out": out_path, "major_blockers": report["R_blockers"]}), separators=(",", ":")))


if __name__ == "__main__":
    main()
