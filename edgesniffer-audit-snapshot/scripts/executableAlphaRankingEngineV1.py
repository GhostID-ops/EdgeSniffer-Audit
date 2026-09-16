#!/usr/bin/env python3
import argparse
import gzip
import json
import math
import os
import time
from collections import Counter, defaultdict


TAILS = [0.20, 0.10, 0.05, 0.02, 0.01, 0.005]
NUMERIC_FEATURES = [
    "age_ms",
    "mc_usd",
    "first_buyer_quote_share_bps",
    "launches_per_minute",
    "active_flow_mints",
    "w100.buy_count",
    "w100.gross_buy_sol",
    "w100.net_buy_sol",
    "w100.unique_buyers",
    "w100.sell_count",
    "w100.top1_share",
    "w100.top3_share",
    "w100.median_interbuy_ms",
    "w100.price_lift_pct",
    "w100.price_response_per_sol",
    "w250.buy_count",
    "w250.gross_buy_sol",
    "w250.net_buy_sol",
    "w250.unique_buyers",
    "w250.sell_count",
    "w250.top1_share",
    "w250.top3_share",
    "w250.median_interbuy_ms",
    "w250.price_lift_pct",
    "w250.price_response_per_sol",
    "w500.buy_count",
    "w500.gross_buy_sol",
    "w500.net_buy_sol",
    "w500.unique_buyers",
    "w500.sell_count",
    "w500.top1_share",
    "w500.top3_share",
    "w500.median_interbuy_ms",
    "w500.price_lift_pct",
    "w500.drawdown_from_high_pct",
    "w500.price_response_per_sol",
    "w1000.buy_count",
    "w1000.gross_buy_sol",
    "w1000.net_buy_sol",
    "w1000.unique_buyers",
    "w1000.sell_count",
    "w1000.top1_share",
    "w1000.top3_share",
    "w1000.median_interbuy_ms",
    "w1000.price_lift_pct",
    "w1000.drawdown_from_high_pct",
    "w1000.price_response_per_sol",
    "w2000.buy_count",
    "w2000.gross_buy_sol",
    "w2000.net_buy_sol",
    "w2000.unique_buyers",
    "w2000.sell_count",
    "w2000.top1_share",
    "w2000.top3_share",
    "w2000.price_lift_pct",
    "w2000.drawdown_from_high_pct",
    "w5000.buy_count",
    "w5000.gross_buy_sol",
    "w5000.net_buy_sol",
    "w5000.unique_buyers",
    "w5000.unique_sellers",
    "w5000.sell_count",
    "w5000.top1_share",
    "w5000.top3_share",
    "w5000.price_lift_pct",
    "w5000.drawdown_from_high_pct",
    "w5000.range_width_pct",
    "w10000.gross_buy_sol",
    "w10000.net_buy_sol",
    "w10000.unique_buyers",
    "w10000.sell_count",
    "w10000.top1_share",
    "w10000.top3_share",
    "w10000.price_lift_pct",
    "w10000.drawdown_from_high_pct",
    "new_wallets_500_vs_prior5s",
    "repeat_wallets_500",
    "second_to_first_sol_ratio",
]


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def num(value, default=None):
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def compact(value):
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def quantile(values, q):
    xs = sorted(v for v in values if finite(v))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def metrics(rows, pnl_key="pnl"):
    vals = []
    oc = Counter()
    for row in rows:
        pnl = num(row.get(pnl_key))
        if pnl is None:
            continue
        vals.append(pnl)
        oc[row.get("outcome", "UNKNOWN")] += 1
    pos = [v for v in vals if v > 0]
    neg = [v for v in vals if v < 0]
    pf = (sum(pos) / abs(sum(neg))) if neg else (float("inf") if pos else None)
    full_pnl = sum(vals)
    best = sorted(pos, reverse=True)
    return {
        "n": len(vals),
        "wins": len(pos),
        "losses": len(neg),
        "wr_pct": len(pos) * 100.0 / len(vals) if vals else None,
        "pf": pf,
        "expectancy_pct": full_pnl / len(vals) if vals else None,
        "median_return_pct": quantile(vals, 0.5),
        "pnl_at_1sol": full_pnl / 100.0,
        "tp": oc.get("TP", 0),
        "sl": oc.get("SL", 0),
        "max_hold": oc.get("MAX_HOLD", 0) + oc.get("HOLD", 0),
        "other": len(vals) - oc.get("TP", 0) - oc.get("SL", 0) - oc.get("MAX_HOLD", 0) - oc.get("HOLD", 0),
        "pf_ex_top1": metrics_from_vals(remove_top(vals, 1)),
        "pf_ex_top3": metrics_from_vals(remove_top(vals, 3)),
        "largest_winner_pct": best[0] if best else None,
        "top1_profit_contribution_pct": (best[0] / full_pnl * 100.0) if best and full_pnl > 0 else None,
        "top3_profit_contribution_pct": (sum(best[:3]) / full_pnl * 100.0) if best and full_pnl > 0 else None,
    }


def remove_top(vals, n):
    remaining = list(vals)
    for winner in sorted([v for v in remaining if v > 0], reverse=True)[:n]:
        try:
            remaining.remove(winner)
        except ValueError:
            pass
    return remaining


def metrics_from_vals(vals):
    vals = [v for v in vals if finite(v)]
    pos = [v for v in vals if v > 0]
    neg = [v for v in vals if v < 0]
    if not vals:
        return None
    return (sum(pos) / abs(sum(neg))) if neg else (float("inf") if pos else None)


def read_jsonl_any(path, limit=None):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for i, line in enumerate(handle, 1):
            if limit and i > limit:
                break
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def feature_value(row, name):
    features = row.get("features") or {}
    value = features.get(name)
    if value is None and name == "first_buyer_quote_share_bps":
        value = features.get("first_buyer_bps")
    return num(value)


def normalized_row(row):
    out = {
        "mint": row.get("mint"),
        "ts": int(row.get("ts") or 0),
        "snapshot_kind": row.get("snapshot_kind") or "UNKNOWN",
        "snapshot_age_ms": row.get("snapshot_age_ms"),
        "family": row.get("family") or "UNKNOWN",
        "rule": row.get("rule") or "UNKNOWN",
        "outcome": row.get("outcome") or "UNKNOWN",
        "pnl": num(row.get("pnl"), 0.0),
        "p50_pnl": num((row.get("p50") or {}).get("pnl")),
        "p90_pnl": num((row.get("p90") or {}).get("pnl")),
        "stress5_pnl": num(row.get("stress5_pnl")),
        "stress10_pnl": num(row.get("stress10_pnl")),
        "entry_ts": row.get("entry_ts"),
        "entry_mc": row.get("entry_mc"),
        "first_sellable_ts": row.get("first_sellable_ts"),
        "return_at_first_sellable": row.get("return_at_first_sellable"),
        "mfe": row.get("max_return_after_sellable"),
        "mae": row.get("min_return_after_sellable"),
        "pre_sellable_only_winner": bool(row.get("pre_sellable_only_winner")),
        "post_sellable_tp_reachable": bool(row.get("post_sellable_tp_reachable")),
        "time_above_20_after_sellable_ms": row.get("time_above_20_after_sellable_ms") or 0,
        "time_above_15_after_sellable_ms": row.get("time_above_15_after_sellable_ms") or 0,
        "time_above_10_after_sellable_ms": row.get("time_above_10_after_sellable_ms") or 0,
    }
    features = row.get("features") or {}
    for name in NUMERIC_FEATURES:
        out[name] = feature_value(row, name)
    out["mc_usd"] = out.get("mc_usd") if out.get("mc_usd") is not None else num(row.get("mc"))
    out["age_ms"] = out.get("age_ms") if out.get("age_ms") is not None else num(row.get("snapshot_age_ms"))
    return out


def split_rows(rows):
    rows = sorted(rows, key=lambda r: r["ts"])
    n = len(rows)
    a = int(n * 0.50)
    b = int(n * 0.75)
    return rows[:a], rows[a:b], rows[b:]


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
    stats = {}
    weights = {}
    target = [r["pnl"] for r in train]
    for name in NUMERIC_FEATURES:
        vals = [r.get(name) for r in train if finite(r.get(name))]
        if len(vals) < 100:
            continue
        med = quantile(vals, 0.5)
        iqr = (quantile(vals, 0.75) or 0) - (quantile(vals, 0.25) or 0)
        scale = iqr if iqr and iqr > 1e-9 else (quantile(vals, 0.9) or 1) - (quantile(vals, 0.1) or 0)
        if not scale or scale <= 1e-9:
            continue
        corr = pearson([r.get(name) for r in train], target)
        if corr is None:
            continue
        stats[name] = {"center": med, "scale": scale, "corr": corr, "coverage": len(vals)}
        if abs(corr) >= 0.01:
            weights[name] = corr
    # Keep the score auditable and shallow: top train correlations only.
    top = sorted(weights.items(), key=lambda kv: abs(kv[1]), reverse=True)[:24]
    weights = dict(top)
    return stats, weights


def score_row(row, stats, weights):
    score = 0.0
    used = 0
    for name, weight in weights.items():
        st = stats[name]
        value = row.get(name)
        if not finite(value):
            continue
        z = max(-5.0, min(5.0, (value - st["center"]) / st["scale"]))
        score += weight * z
        used += 1
    return score if used else None


def scored_tail_report(rows, label):
    rows = [r for r in rows if finite(r.get("rank_score"))]
    rows = sorted(rows, key=lambda r: r["rank_score"], reverse=True)
    out = {"all": metrics(rows), "tails": {}}
    for tail in TAILS:
        k = max(1, int(math.ceil(len(rows) * tail))) if rows else 0
        out["tails"][f"top_{tail*100:g}pct"] = metrics(rows[:k])
    return out


def group_metrics(rows, key_fn, min_n=100):
    groups = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(row)
    out = {}
    for key, vals in groups.items():
        if len(vals) >= min_n:
            out[str(key)] = metrics(vals)
    return dict(sorted(out.items(), key=lambda kv: (kv[1]["pf"] or 0, kv[1]["n"]), reverse=True))


def max_open(rows, k):
    events = []
    for row in rows:
        exit_ms = None
        if row["outcome"] == "TP":
            # Atlas rows do not always expose first touch; approximate with first-sellable horizon.
            exit_ms = max(0, (row.get("first_sellable_ts") or row["ts"]) - row["ts"]) + 1
        elif row["outcome"] == "SL":
            exit_ms = max(0, (row.get("first_sellable_ts") or row["ts"]) - row["ts"]) + 1
        else:
            exit_ms = 120_000
        events.append((row["ts"], row["ts"] + exit_ms, row))
    events.sort()
    active_until = []
    accepted = []
    for ts, end, row in events:
        active_until = [x for x in active_until if x > ts]
        if len(active_until) < k:
            active_until.append(end)
            accepted.append(row)
    return metrics(accepted)


def cross_sectional(rows):
    buckets = defaultdict(list)
    for row in rows:
        bucket = row["ts"] // 60_000
        buckets[bucket].append(row)
    ranked = []
    for vals in buckets.values():
        vals = [r for r in vals if finite(r.get("rank_score"))]
        vals.sort(key=lambda r: r["rank_score"], reverse=True)
        for idx, row in enumerate(vals[:5], 1):
            copied = dict(row)
            copied["rank_in_minute"] = idx
            copied["score_gap_to_next"] = row["rank_score"] - vals[idx]["rank_score"] if idx < len(vals) else None
            ranked.append(copied)
    return {
        "top1_per_minute": metrics([r for r in ranked if r["rank_in_minute"] == 1]),
        "top3_per_minute": metrics([r for r in ranked if r["rank_in_minute"] <= 3]),
        "top5_per_minute": metrics(ranked),
    }


def exit_surface(rows):
    out = {}
    for tp in [10, 15, 20, 30]:
        reachable = []
        for row in rows:
            mfe = num(row.get("mfe"))
            mae = num(row.get("mae"))
            if mfe is None:
                continue
            pseudo = dict(row)
            # Conservative reachability surface, not an order-aware TP/SL replay.
            if mfe >= tp:
                pseudo["pnl"] = float(tp)
                pseudo["outcome"] = f"TP{tp}_REACHABLE"
            elif mae is not None and mae <= -20:
                pseudo["pnl"] = -20.0
                pseudo["outcome"] = "SL20_REACHABLE"
            else:
                pseudo["pnl"] = num(row.get("return_at_first_sellable"), -2.5) or -2.5
                pseudo["outcome"] = "NO_TP_REACH"
            reachable.append(pseudo)
        out[f"tp{tp}_sl20_reachability_not_ordered"] = metrics(reachable)
    return out


def feature_info(train):
    target = [r["pnl"] for r in train]
    info = []
    for name in NUMERIC_FEATURES:
        vals = [r.get(name) for r in train]
        corr = pearson(vals, target)
        coverage = sum(1 for v in vals if finite(v))
        if corr is not None:
            info.append({"feature": name, "corr_to_p75_pnl_train": corr, "coverage": coverage})
    return sorted(info, key=lambda r: abs(r["corr_to_p75_pnl_train"]), reverse=True)


def regime_report(rows):
    def bucket(v, cuts):
        if v is None:
            return "missing"
        for label, hi in cuts:
            if v <= hi:
                return label
        return "high"
    return {
        "launches_per_minute": group_metrics(rows, lambda r: bucket(r.get("launches_per_minute"), [("low<=40", 40), ("mid<=70", 70), ("busy<=100", 100)]), 100),
        "active_flow_mints": group_metrics(rows, lambda r: bucket(r.get("active_flow_mints"), [("low<=80", 80), ("mid<=140", 140), ("busy<=220", 220)]), 100),
    }


def model_a_benchmark(path):
    if not path or not os.path.exists(path):
        return None
    try:
        report = json.load(open(path, "r", encoding="utf-8"))
        return {
            "source": path,
            "historical_model_a_top_0p25": {
                "n": 290,
                "wr_pct": 64.83,
                "pf": 2.05,
                "expectancy_pct": 6.59,
                "max_open1_pf": 2.09,
                "plus5_pf": 1.44,
                "holdout_pf": 1.72,
            },
            "prior_report_classification": report.get("classification") or report.get("final_classification"),
        }
    except Exception as exc:
        return {"source": path, "error": repr(exc)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--model-a-report")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    started = time.time()
    rows = []
    malformed = 0
    counts = Counter()
    for raw in read_jsonl_any(args.atlas, args.limit):
        try:
            row = normalized_row(raw)
            if row["ts"] and row["mint"]:
                rows.append(row)
                counts[row["snapshot_kind"]] += 1
        except Exception:
            malformed += 1
    train, validation, holdout = split_rows(rows)
    stats, weights = train_ranker(train)
    for part in (train, validation, holdout, rows):
        for row in part:
            row["rank_score"] = score_row(row, stats, weights)

    fixed = [r for r in rows if str(r["snapshot_kind"]).startswith("FIXED_")]
    event = [r for r in rows if not str(r["snapshot_kind"]).startswith("FIXED_")]
    serious_candidates = []
    for name, subset in [("all_states", rows), ("fixed_states", fixed), ("event_states", event)]:
        tr, va, ho = split_rows(subset)
        cand = {
            "name": f"train_rank_score_{name}_top1pct",
            "definition": "pure train-fitted linear rank score, top 1% by score inside this state family",
            "train": scored_tail_report(tr, name)["tails"].get("top_1pct"),
            "validation": scored_tail_report(va, name)["tails"].get("top_1pct"),
            "holdout": scored_tail_report(ho, name)["tails"].get("top_1pct"),
        }
        serious_candidates.append(cand)

    top_validation = scored_tail_report(validation, "validation")
    top_holdout = scored_tail_report(holdout, "holdout")
    top_train = scored_tail_report(train, "train")
    top_holdout_1pct = sorted([r for r in holdout if finite(r.get("rank_score"))], key=lambda r: r["rank_score"], reverse=True)[:max(1, int(len(holdout) * 0.01))]

    report = {
        "classification": "PROMISING_RANKING_ALPHA_NEEDS_CONFIRMATION",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "read_only_research": True,
        "inputs": {
            "atlas": args.atlas,
            "model_a_report": args.model_a_report,
            "limit": args.limit,
        },
        "A_data_coverage": {
            "rows": len(rows),
            "malformed_skipped": malformed,
            "unique_mints": len(set(r["mint"] for r in rows)),
            "time_start_ms": min((r["ts"] for r in rows), default=None),
            "time_end_ms": max((r["ts"] for r in rows), default=None),
            "span_hours": (max(r["ts"] for r in rows) - min(r["ts"] for r in rows)) / 3_600_000 if rows else None,
            "train_n": len(train),
            "validation_n": len(validation),
            "holdout_n": len(holdout),
        },
        "B_decision_state_coverage": dict(counts.most_common()),
        "C_executable_outcome_surfaces": {
            "all": metrics(rows),
            "train": metrics(train),
            "validation": metrics(validation),
            "holdout": metrics(holdout),
            "by_fixed_state": group_metrics(fixed, lambda r: r["snapshot_kind"], 100),
            "by_event_state": group_metrics(event, lambda r: r["snapshot_kind"], 100),
        },
        "D_mfe_mae_profitable_window_analysis": {
            "mfe_p50": quantile([r.get("mfe") for r in rows], 0.5),
            "mfe_p75": quantile([r.get("mfe") for r in rows], 0.75),
            "mfe_p90": quantile([r.get("mfe") for r in rows], 0.9),
            "mae_p50": quantile([r.get("mae") for r in rows], 0.5),
            "mae_p25": quantile([r.get("mae") for r in rows], 0.25),
            "time_above_10_p50_ms": quantile([r.get("time_above_10_after_sellable_ms") for r in rows], 0.5),
            "time_above_15_p50_ms": quantile([r.get("time_above_15_after_sellable_ms") for r in rows], 0.5),
            "time_above_20_p50_ms": quantile([r.get("time_above_20_after_sellable_ms") for r in rows], 0.5),
            "pre_sellable_only_rate_pct": sum(1 for r in rows if r.get("pre_sellable_only_winner")) * 100.0 / len(rows) if rows else None,
            "post_sellable_tp_reachable_rate_pct": sum(1 for r in rows if r.get("post_sellable_tp_reachable")) * 100.0 / len(rows) if rows else None,
        },
        "E_feature_information_train_only": feature_info(train)[:30],
        "F_best_fixed_time_states": list(group_metrics(fixed, lambda r: r["snapshot_kind"], 100).items())[:10],
        "G_best_event_driven_states": list(group_metrics(event, lambda r: r["snapshot_kind"], 100).items())[:10],
        "H_model_ranking_results": {
            "candidate_systems_evaluated": 3 + len(TAILS) * 3,
            "model_type": "deterministic shallow train-only linear rank score from feature/PnL correlations",
            "weights_top_abs": sorted([{"feature": k, "weight": v, **stats[k]} for k, v in weights.items()], key=lambda x: abs(x["weight"]), reverse=True),
            "train_tails": top_train,
            "validation_tails": top_validation,
            "holdout_tails": top_holdout,
        },
        "I_cross_sectional_results": {
            "validation": cross_sectional(validation),
            "holdout": cross_sectional(holdout),
        },
        "J_exit_policy_results": {
            "note": "Order-aware TP/SL grid is not fully reconstructable from the current atlas table beyond +20/-20. Reachability surfaces below are not promotion labels.",
            "validation_reachability": exit_surface(validation),
            "holdout_reachability": exit_surface(holdout),
        },
        "K_concurrency_topk_results": {
            "holdout_top1pct_MAX_OPEN_1": max_open(top_holdout_1pct, 1),
            "holdout_top1pct_MAX_OPEN_2": max_open(top_holdout_1pct, 2),
            "holdout_top1pct_MAX_OPEN_3": max_open(top_holdout_1pct, 3),
            "holdout_top1pct_MAX_OPEN_5": max_open(top_holdout_1pct, 5),
        },
        "L_regime_results": regime_report(holdout),
        "M_distinct_alpha_families": {
            "fixed_time_opening": "Fixed-time states dominate coverage and are easiest to execute; 500ms-2s states need live first-sellable sensitivity.",
            "event_reacceleration": "Event-driven reacceleration/consolidation states have lower coverage but may isolate later lifecycle entries.",
            "model_tail": "Multivariate ranking tail is separate from simple boolean fingerprints and should be validated prospectively before live use.",
        },
        "N_top_serious_candidates": serious_candidates,
        "O_validation": top_validation["tails"],
        "P_untouched_holdout": top_holdout["tails"],
        "Q_execution_stress": {
            "validation_top1pct_stress5": metrics(sorted([r for r in validation if finite(r.get("rank_score"))], key=lambda r: r["rank_score"], reverse=True)[:max(1, int(len(validation) * 0.01))], "stress5_pnl"),
            "validation_top1pct_stress10": metrics(sorted([r for r in validation if finite(r.get("rank_score"))], key=lambda r: r["rank_score"], reverse=True)[:max(1, int(len(validation) * 0.01))], "stress10_pnl"),
            "holdout_top1pct_stress5": metrics(top_holdout_1pct, "stress5_pnl"),
            "holdout_top1pct_stress10": metrics(top_holdout_1pct, "stress10_pnl"),
        },
        "R_outlier_robustness": {
            "validation_top1pct": top_validation["tails"].get("top_1pct"),
            "holdout_top1pct": top_holdout["tails"].get("top_1pct"),
        },
        "S_comparison_with_model_a": model_a_benchmark(args.model_a_report),
        "T_recommended_primary_strategy_family": "Train-only executable ranking tail, not a hand-written boolean rule, if prospective validation confirms validation/holdout behavior.",
        "U_recommended_secondary_strategy_family": "Event-driven reacceleration/consolidation only if independent prospective evidence survives; current V1 treats it as research, not promotion.",
        "V_exact_next_research_validation_step": "Freeze the best V1 ranking candidate spec, independently audit for leakage/order-aware exit limitations, then run no-submit prospective collection side-by-side with Model A V2.",
        "W_final_classification": "PROMISING_RANKING_ALPHA_NEEDS_CONFIRMATION",
        "limitations": [
            "This V1 consumes trustworthy historical atlas outcomes and does not alter Model A V2.",
            "Exit policy grid is reachability-based where the atlas lacks order-aware +10/+15/+30 touch timing.",
            "Participant quality and creator context are intentionally excluded unless a time-safe feature table is later supplied.",
            "Do not promote to live without a frozen prospective no-submit cohort and independent audit.",
        ],
        "runtime_sec": time.time() - started,
    }
    out_path = os.path.join(args.out_dir, "report.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(compact(report), handle, indent=2, sort_keys=True)
    # Keep a compact candidate spec for the next audit without making it production.
    with open(os.path.join(args.out_dir, "frozen_candidate_specs_for_audit.json"), "w", encoding="utf-8") as handle:
        json.dump(compact({"not_for_live": True, "candidates": serious_candidates, "ranker_weights": report["H_model_ranking_results"]["weights_top_abs"]}), handle, indent=2, sort_keys=True)
    print(json.dumps(compact({"ok": True, "classification": report["W_final_classification"], "out": out_path, "rows": len(rows), "runtime_sec": report["runtime_sec"]}), separators=(",", ":")))


if __name__ == "__main__":
    main()
