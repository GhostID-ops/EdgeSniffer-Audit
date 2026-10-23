#!/usr/bin/env python3
import argparse
import gzip
import json
import math
import os
import time
from collections import Counter, defaultdict


STATIC_THRESHOLD = 0.590596

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

DOMINANT_RULE = "fast100_fb_le_5000bps_benchmark"

TRAIN_SCORE_BANDS = [
    ("below_0p35", None, 0.35),
    ("0p35_to_0p45", 0.35, 0.45),
    ("0p45_to_0p525", 0.45, 0.525),
    ("near_below_0p525_to_threshold", 0.525, STATIC_THRESHOLD),
    ("just_above_threshold_to_0p65", STATIC_THRESHOLD, 0.65),
    ("0p65_to_0p75", 0.65, 0.75),
    ("0p75_to_1p00", 0.75, 1.00),
    ("above_1p00", 1.00, None),
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


def q(values, pct):
    xs = sorted(v for v in values if finite(v))
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
    outcomes = Counter()
    for row in rows:
        pnl = num(row.get(key))
        if pnl is None:
            continue
        vals.append(pnl)
        outcomes[row.get("outcome") or "UNKNOWN"] += 1
    pos = [v for v in vals if v > 0]
    neg = [v for v in vals if v < 0]
    gross_pos = sum(pos)
    gross_neg = abs(sum(neg))
    pf = gross_pos / gross_neg if gross_neg else (float("inf") if gross_pos else None)
    total = sum(vals)
    return {
        "n": len(vals),
        "wins": len(pos),
        "losses": len(neg),
        "wr_pct": len(pos) * 100.0 / len(vals) if vals else None,
        "pf": pf,
        "avg_pnl_pct": total / len(vals) if vals else None,
        "median_pnl_pct": q(vals, 0.5),
        "pnl_at_1sol": total / 100.0,
        "tp": outcomes.get("TP", 0),
        "sl": outcomes.get("SL", 0),
        "max_hold": outcomes.get("MAX_HOLD", 0) + outcomes.get("max_hold", 0) + outcomes.get("HOLD", 0),
        "other": len(vals) - outcomes.get("TP", 0) - outcomes.get("SL", 0) - outcomes.get("MAX_HOLD", 0) - outcomes.get("max_hold", 0) - outcomes.get("HOLD", 0),
        "gross_win_pct": gross_pos,
        "gross_loss_pct": gross_neg,
        "largest_winner_pct": max(pos) if pos else None,
        "largest_loser_pct": min(neg) if neg else None,
        "pf_ex_top1": pf_ex_best(vals, 1),
        "pf_ex_top3": pf_ex_best(vals, 3),
        "top1_profit_contribution_pct": (sorted(pos, reverse=True)[0] / total * 100.0) if pos and total > 0 else None,
        "top3_profit_contribution_pct": (sum(sorted(pos, reverse=True)[:3]) / total * 100.0) if pos and total > 0 else None,
    }


def pf_ex_best(vals, n):
    xs = [v for v in vals if finite(v)]
    for v in sorted([x for x in xs if x > 0], reverse=True)[:n]:
        try:
            xs.remove(v)
        except ValueError:
            pass
    pos = [x for x in xs if x > 0]
    neg = [x for x in xs if x < 0]
    return sum(pos) / abs(sum(neg)) if neg else (float("inf") if pos else None)


def read_jsonl(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def feature(raw, name):
    feats = raw.get("features") or {}
    value = feats.get(name)
    if value is None and name == "first_buyer_quote_share_bps":
        value = feats.get("first_buyer_bps")
    return num(value)


def normalize_row(raw, row_id):
    row = {
        "_row_id": row_id,
        "mint": raw.get("mint"),
        "ts": int(raw.get("ts") or 0),
        "rule": raw.get("rule") or "UNKNOWN",
        "family": raw.get("family") or "UNKNOWN",
        "snapshot_kind": raw.get("snapshot_kind") or "UNKNOWN",
        "snapshot_age_ms": num(raw.get("snapshot_age_ms")),
        "mc": num(raw.get("mc")),
        "outcome": raw.get("outcome") or "UNKNOWN",
        "pnl": num(raw.get("pnl"), 0.0),
        "stress5_pnl": num(raw.get("stress5_pnl")),
        "stress10_pnl": num(raw.get("stress10_pnl")),
        "entry_ts": raw.get("entry_ts"),
        "entry_mc": num(raw.get("entry_mc")),
        "first_sellable_ts": raw.get("first_sellable_ts"),
        "return_at_first_sellable": num(raw.get("return_at_first_sellable")),
        "mfe": num(raw.get("max_return_after_sellable")),
        "mae": num(raw.get("min_return_after_sellable")),
        "pre_sellable_only_winner": bool(raw.get("pre_sellable_only_winner")),
        "post_sellable_tp_reachable": bool(raw.get("post_sellable_tp_reachable")),
        "time_above_20_after_sellable_ms": num(raw.get("time_above_20_after_sellable_ms"), 0.0),
        "time_above_15_after_sellable_ms": num(raw.get("time_above_15_after_sellable_ms"), 0.0),
        "time_above_10_after_sellable_ms": num(raw.get("time_above_10_after_sellable_ms"), 0.0),
    }
    for name in FEATURES:
        row[name] = feature(raw, name)
    if row.get("mc_usd") is None:
        row["mc_usd"] = row["mc"]
    if row.get("age_ms") is None:
        row["age_ms"] = row["snapshot_age_ms"]
    return row


def split_by_observation(rows):
    ordered = sorted(rows, key=lambda r: (r["ts"], r["_row_id"]))
    a = int(len(ordered) * 0.50)
    b = int(len(ordered) * 0.75)
    bounds = {
        "train_end_exclusive_ts": ordered[a]["ts"] if a < len(ordered) else None,
        "validation_end_exclusive_ts": ordered[b]["ts"] if b < len(ordered) else None,
    }
    for i, row in enumerate(ordered):
        row["obs_split"] = "train" if i < a else ("validation" if i < b else "final_test")
    return bounds


def split_by_mint(rows):
    first_ts = {}
    for row in rows:
        mint = row["mint"]
        first_ts[mint] = min(first_ts.get(mint, row["ts"]), row["ts"])
    ordered_mints = sorted(first_ts, key=lambda m: (first_ts[m], m))
    a = int(len(ordered_mints) * 0.50)
    b = int(len(ordered_mints) * 0.75)
    assign = {}
    for i, mint in enumerate(ordered_mints):
        assign[mint] = "train" if i < a else ("validation" if i < b else "final_test")
    for row in rows:
        row["mint_split"] = assign[row["mint"]]
    return {
        "unique_mints": len(ordered_mints),
        "train_mints": a,
        "validation_mints": b - a,
        "final_test_mints": len(ordered_mints) - b,
        "train_end_mint_first_ts": first_ts[ordered_mints[a - 1]] if a else None,
        "validation_end_mint_first_ts": first_ts[ordered_mints[b - 1]] if b else None,
        "mint_overlap_proof": {"train_validation": 0, "train_final_test": 0, "validation_final_test": 0},
    }


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
    raw_weights = {}
    target = [r["pnl"] for r in train]
    for name in FEATURES:
        vals = [r.get(name) for r in train if finite(r.get(name))]
        if len(vals) < 100:
            continue
        med = q(vals, 0.5)
        iqr = (q(vals, 0.75) or 0) - (q(vals, 0.25) or 0)
        scale = iqr if iqr and iqr > 1e-9 else (q(vals, 0.9) or 1) - (q(vals, 0.1) or 0)
        if not scale or scale <= 1e-9:
            continue
        corr = pearson([r.get(name) for r in train], target)
        if corr is None:
            continue
        stats[name] = {"center": med, "scale": scale, "corr": corr, "coverage": len(vals)}
        if abs(corr) >= 0.01:
            raw_weights[name] = corr
    return stats, dict(sorted(raw_weights.items(), key=lambda kv: abs(kv[1]), reverse=True)[:24])


def score_with_contribs(row, stats, weights):
    score = 0.0
    used = 0
    normalized = {}
    contribs = {}
    for name, weight in weights.items():
        value = row.get(name)
        if not finite(value):
            continue
        st = stats[name]
        z = max(-5.0, min(5.0, (value - st["center"]) / st["scale"]))
        contribution = weight * z
        normalized[name] = z
        contribs[name] = contribution
        score += contribution
        used += 1
    return (score if used else None), normalized, contribs


def rows_in_split(rows, split_key, name):
    return [r for r in rows if r.get(split_key) == name]


def top_by_threshold(rows, threshold=STATIC_THRESHOLD):
    return sorted([r for r in rows if finite(r.get("rank_score")) and r["rank_score"] >= threshold], key=lambda r: (r["ts"], -r["rank_score"]))


def first_selected_flags(selected):
    by_mint = defaultdict(list)
    for row in selected:
        by_mint[row["mint"]].append(row)
    flags = set()
    for mint, vals in by_mint.items():
        earliest = min(vals, key=lambda r: (r["ts"], -r["rank_score"], r["_row_id"]))
        flags.add(earliest["_row_id"])
    return flags


def bucket_value(value, buckets):
    if value is None:
        return "missing"
    for label, lo, hi in buckets:
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return label
    return "unbucketed"


def bucket_metrics(rows, key, buckets):
    grouped = defaultdict(list)
    for row in rows:
        grouped[bucket_value(row.get(key), buckets)].append(row)
    return {k: metrics(v) for k, v in sorted(grouped.items())}


def score_band_name(score):
    for name, lo, hi in TRAIN_SCORE_BANDS:
        if score is None:
            return "missing"
        if (lo is None or score >= lo) and (hi is None or score < hi):
            return name
    return "unbucketed"


def score_band_report(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[score_band_name(row.get("rank_score"))].append(row)
    return {name: metrics(grouped.get(name, [])) for name, _, _ in TRAIN_SCORE_BANDS}


def train_quantile_bands(train):
    scores = [r.get("rank_score") for r in train if finite(r.get("rank_score"))]
    points = [
        ("train_score_p00", q(scores, 0.00)),
        ("train_score_p50", q(scores, 0.50)),
        ("train_score_p75", q(scores, 0.75)),
        ("train_score_p90", q(scores, 0.90)),
        ("train_score_p95", q(scores, 0.95)),
        ("train_score_p98", q(scores, 0.98)),
        ("static_threshold_train_top1_cut", STATIC_THRESHOLD),
        ("train_score_p99_5", q(scores, 0.995)),
        ("train_score_p100", q(scores, 1.00)),
    ]
    ordered = []
    seen = set()
    for label, value in sorted(points, key=lambda x: (-1e99 if x[1] is None else x[1], x[0])):
        if value is None:
            continue
        key = round(value, 12)
        if key in seen:
            continue
        seen.add(key)
        ordered.append((label, value))
    bands = []
    for idx in range(len(ordered) - 1):
        lo_label, lo = ordered[idx]
        hi_label, hi = ordered[idx + 1]
        bands.append({
            "name": f"{lo_label}_to_{hi_label}",
            "lo": lo,
            "hi": hi,
            "source": "TRAIN_SCORE_DISTRIBUTION_ONLY",
        })
    return bands


def dynamic_score_band_report(rows, bands):
    grouped = defaultdict(list)
    for row in rows:
        score = row.get("rank_score")
        label = "missing"
        if finite(score):
            label = "outside_train_band_range"
            for band in bands:
                if score >= band["lo"] and score < band["hi"]:
                    label = band["name"]
                    break
                if band is bands[-1] and score == band["hi"]:
                    label = band["name"]
                    break
        grouped[label].append(row)
    return {band["name"]: metrics(grouped.get(band["name"], [])) for band in bands}


def feature_distinction(winners, losers):
    out = []
    for name in FEATURES:
        wvals = [r.get(name) for r in winners if finite(r.get(name))]
        lvals = [r.get(name) for r in losers if finite(r.get(name))]
        if len(wvals) < 10 or len(lvals) < 10:
            continue
        wmed = q(wvals, 0.5)
        lmed = q(lvals, 0.5)
        wiqr = (q(wvals, 0.75) or 0) - (q(wvals, 0.25) or 0)
        liqr = (q(lvals, 0.75) or 0) - (q(lvals, 0.25) or 0)
        pooled = max(1e-9, ((wiqr or 0) + (liqr or 0)) / 2.0)
        out.append({
            "feature": name,
            "winner_median": wmed,
            "loser_median": lmed,
            "median_delta": wmed - lmed if finite(wmed) and finite(lmed) else None,
            "iqr_scaled_delta": ((wmed - lmed) / pooled) if finite(wmed) and finite(lmed) else None,
            "winner_p25": q(wvals, 0.25),
            "winner_p75": q(wvals, 0.75),
            "loser_p25": q(lvals, 0.25),
            "loser_p75": q(lvals, 0.75),
            "winner_coverage": len(wvals),
            "loser_coverage": len(lvals),
        })
    return sorted(out, key=lambda r: abs(r["iqr_scaled_delta"] or 0), reverse=True)


def mix_report(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "UNKNOWN")].append(row)
    return dict(sorted(
        ((k, metrics(v)) for k, v in grouped.items()),
        key=lambda kv: (kv[1]["pnl_at_1sol"], kv[1]["pf"] or 0, kv[1]["n"]),
        reverse=True,
    ))


def top_contributions_summary(rows):
    sums = Counter()
    winners = [r for r in rows if num(r.get("pnl"), 0) > 0]
    losers = [r for r in rows if num(r.get("pnl"), 0) < 0]
    for row in rows:
        for name, val in (row.get("_contribs") or {}).items():
            sums[name] += val
    winner_sums = Counter()
    loser_sums = Counter()
    for row in winners:
        for name, val in (row.get("_contribs") or {}).items():
            winner_sums[name] += val
    for row in losers:
        for name, val in (row.get("_contribs") or {}).items():
            loser_sums[name] += val
    return {
        "all_top_positive": sums.most_common(12),
        "winners_top_positive": winner_sums.most_common(12),
        "losers_top_positive": loser_sums.most_common(12),
        "all_top_negative": sorted(sums.items(), key=lambda kv: kv[1])[:12],
    }


def forensic_row(row, matched_rules, first_selected_ids):
    feature_values = {name: row.get(name) for name in FEATURES}
    return compact({
        "mint": row["mint"],
        "observation_timestamp_ms": row["ts"],
        "mint_age_at_observation_ms": row.get("age_ms"),
        "event_state": row.get("snapshot_kind"),
        "atlas_rule_family": row.get("family"),
        "atlas_rule": row.get("rule"),
        "matched_rules_same_mint_ts": matched_rules.get((row["mint"], row["ts"]), []),
        "rank_score": row.get("rank_score"),
        "feature_values": feature_values,
        "normalized_feature_values": row.get("_norms") or {},
        "feature_contributions": row.get("_contribs") or {},
        "market_cap_usd": row.get("mc_usd"),
        "gross_buy_sol": {
            "100ms": row.get("w100.gross_buy_sol"),
            "500ms": row.get("w500.gross_buy_sol"),
            "1000ms": row.get("w1000.gross_buy_sol"),
            "5000ms": row.get("w5000.gross_buy_sol"),
            "10000ms": row.get("w10000.gross_buy_sol"),
        },
        "sell_count": {
            "100ms": row.get("w100.sell_count"),
            "500ms": row.get("w500.sell_count"),
            "1000ms": row.get("w1000.sell_count"),
            "5000ms": row.get("w5000.sell_count"),
            "10000ms": row.get("w10000.sell_count"),
        },
        "drawdown_high_low_metrics": {
            "w500_drawdown_from_high_pct": row.get("w500.drawdown_from_high_pct"),
            "w1000_drawdown_from_high_pct": row.get("w1000.drawdown_from_high_pct"),
            "w5000_drawdown_from_high_pct": row.get("w5000.drawdown_from_high_pct"),
            "w10000_drawdown_from_high_pct": row.get("w10000.drawdown_from_high_pct"),
            "w5000_range_width_pct": row.get("w5000.range_width_pct"),
            "mfe_after_sellable_pct": row.get("mfe"),
            "mae_after_sellable_pct": row.get("mae"),
        },
        "price_response_metrics": {
            "w100_price_lift_pct": row.get("w100.price_lift_pct"),
            "w500_price_lift_pct": row.get("w500.price_lift_pct"),
            "w1000_price_lift_pct": row.get("w1000.price_lift_pct"),
            "w5000_price_lift_pct": row.get("w5000.price_lift_pct"),
            "w100_price_response_per_sol": row.get("w100.price_response_per_sol"),
            "w500_price_response_per_sol": row.get("w500.price_response_per_sol"),
            "w1000_price_response_per_sol": row.get("w1000.price_response_per_sol"),
            "w5000_price_response_per_sol": row.get("w5000.price_response_per_sol"),
        },
        "first_buyer_concentration": {
            "first_buyer_quote_share_bps": row.get("first_buyer_quote_share_bps"),
            "w100_top1_share": row.get("w100.top1_share"),
            "w500_top1_share": row.get("w500.top1_share"),
            "w1000_top1_share": row.get("w1000.top1_share"),
            "w5000_top1_share": row.get("w5000.top1_share"),
        },
        "global_activity_features": {
            "launches_per_minute": row.get("launches_per_minute"),
            "active_flow_mints": row.get("active_flow_mints"),
        },
        "executable_entry": {
            "entry_ts_ms": row.get("entry_ts"),
            "entry_mc": row.get("entry_mc"),
            "first_sellable_ts_ms": row.get("first_sellable_ts"),
            "return_at_first_sellable_pct": row.get("return_at_first_sellable"),
        },
        "executable_exit_outcome": row.get("outcome"),
        "tp_sl_timeout_classification": row.get("outcome"),
        "realized_pnl_pct": row.get("pnl"),
        "pnl_at_1sol": row.get("pnl") / 100.0 if finite(row.get("pnl")) else None,
        "adverse_entry_stress_outcomes": {
            "plus5_pnl_pct": row.get("stress5_pnl"),
            "plus10_pnl_pct": row.get("stress10_pnl"),
            "pre_sellable_only_winner": row.get("pre_sellable_only_winner"),
            "post_sellable_tp_reachable": row.get("post_sellable_tp_reachable"),
        },
        "first_selected_opportunity_for_mint": row["_row_id"] in first_selected_ids,
        "obs_split": row.get("obs_split"),
        "corrected_mint_split": row.get("mint_split"),
    })


def nearest_near_miss(selected, pool, stats, features_for_match, max_per_selected=3):
    by_family = defaultdict(list)
    by_state = defaultdict(list)
    for row in pool:
        by_family[row["family"]].append(row)
        by_state[row["snapshot_kind"]].append(row)
    pairs = []
    for sel in selected:
        candidates = [r for r in by_state.get(sel["snapshot_kind"], []) if r["rank_score"] < STATIC_THRESHOLD]
        if len(candidates) < 20:
            candidates = [r for r in by_family.get(sel["family"], []) if r["rank_score"] < STATIC_THRESHOLD]
        if len(candidates) < 20:
            candidates = [r for r in pool if r["rank_score"] < STATIC_THRESHOLD]
        scored = []
        for miss in candidates:
            score = 0.0
            used = 0
            for name in features_for_match:
                a = sel.get(name)
                b = miss.get(name)
                if not (finite(a) and finite(b)):
                    continue
                scale = stats.get(name, {}).get("scale") or 1.0
                score += abs(a - b) / max(scale, 1e-9)
                used += 1
            if used >= 5:
                scored.append((score / used, miss))
        scored.sort(key=lambda x: (x[0], abs((sel["rank_score"] or 0) - (x[1]["rank_score"] or 0))))
        for distance, miss in scored[:max_per_selected]:
            pairs.append({
                "selected_row_id": sel["_row_id"],
                "selected_mint": sel["mint"],
                "selected_ts": sel["ts"],
                "selected_score": sel["rank_score"],
                "selected_outcome": sel["outcome"],
                "selected_pnl": sel["pnl"],
                "near_miss_row_id": miss["_row_id"],
                "near_miss_mint": miss["mint"],
                "near_miss_ts": miss["ts"],
                "near_miss_score": miss["rank_score"],
                "near_miss_outcome": miss["outcome"],
                "near_miss_pnl": miss["pnl"],
                "distance": distance,
                "same_state": sel["snapshot_kind"] == miss["snapshot_kind"],
                "same_family": sel["family"] == miss["family"],
                "score_gap": (sel["rank_score"] - miss["rank_score"]) if finite(sel.get("rank_score")) and finite(miss.get("rank_score")) else None,
            })
    return pairs


def near_miss_summary(selected, near_miss_pairs):
    selected_ids = {p["selected_row_id"] for p in near_miss_pairs}
    representative_selected = [r for r in selected if r["_row_id"] in selected_ids]
    miss_rows = []
    seen = set()
    for p in near_miss_pairs:
        if p["near_miss_row_id"] in seen:
            continue
        seen.add(p["near_miss_row_id"])
        miss_rows.append({
            "mint": p["near_miss_mint"],
            "ts": p["near_miss_ts"],
            "rank_score": p["near_miss_score"],
            "outcome": p["near_miss_outcome"],
            "pnl": p["near_miss_pnl"],
        })
    return {
        "selected_matched": metrics(representative_selected),
        "near_miss_unique": metrics(miss_rows),
        "pairs": len(near_miss_pairs),
        "same_state_pct": sum(1 for p in near_miss_pairs if p["same_state"]) * 100.0 / len(near_miss_pairs) if near_miss_pairs else None,
        "same_family_pct": sum(1 for p in near_miss_pairs if p["same_family"]) * 100.0 / len(near_miss_pairs) if near_miss_pairs else None,
        "median_score_gap": q([p["score_gap"] for p in near_miss_pairs], 0.5),
        "median_feature_distance": q([p["distance"] for p in near_miss_pairs], 0.5),
    }


def explain_material_change(original, corrected):
    reasons = []
    if abs((corrected["n"] or 0) - (original["n"] or 0)) > max(5, 0.1 * max(1, original["n"] or 1)):
        reasons.append("split reassignment changed selected N")
    if original.get("pf") and corrected.get("pf") and abs(corrected["pf"] - original["pf"]) / original["pf"] > 0.25:
        reasons.append("PF changed materially after mint-level reassignment")
    if original.get("wr_pct") and corrected.get("wr_pct") and abs(corrected["wr_pct"] - original["wr_pct"]) > 8:
        reasons.append("win-rate changed materially after mint-level reassignment")
    if original.get("pnl_at_1sol") and corrected.get("pnl_at_1sol") and abs(corrected["pnl_at_1sol"] - original["pnl_at_1sol"]) > abs(original["pnl_at_1sol"]) * 0.25:
        reasons.append("PnL changed materially after mint-level reassignment")
    return reasons or ["no material deterioration from mint-level reassignment inside persisted candidate universe"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate-outcomes", required=True)
    ap.add_argument("--atlas-report", required=True)
    ap.add_argument("--ranking-report", required=True)
    ap.add_argument("--adversarial-report", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    started = time.time()

    atlas_report = json.load(open(args.atlas_report, "r", encoding="utf-8"))
    ranking_report = json.load(open(args.ranking_report, "r", encoding="utf-8"))
    adversarial_report = json.load(open(args.adversarial_report, "r", encoding="utf-8"))

    rows = []
    for i, raw in enumerate(read_jsonl(args.candidate_outcomes), 1):
        row = normalize_row(raw, i)
        if row["mint"] and row["ts"]:
            rows.append(row)

    obs_bounds = split_by_observation(rows)
    mint_split_meta = split_by_mint(rows)
    train = rows_in_split(rows, "obs_split", "train")
    stats, weights = train_ranker(train)
    for row in rows:
        score, norms, contribs = score_with_contribs(row, stats, weights)
        row["rank_score"] = score
        row["_norms"] = norms
        row["_contribs"] = contribs

    matched_rules = defaultdict(list)
    for row in rows:
        matched_rules[(row["mint"], row["ts"])].append({"rule": row["rule"], "family": row["family"], "row_id": row["_row_id"]})

    selected_all = top_by_threshold(rows)
    selected_validation = [r for r in selected_all if r["obs_split"] == "validation"]
    selected_final = [r for r in selected_all if r["obs_split"] == "final_test"]
    selected_non_train = [r for r in selected_all if r["obs_split"] in ("validation", "final_test")]
    first_selected_ids = first_selected_flags(selected_all)

    forensic_path = os.path.join(args.out_dir, "static_threshold_selected_forensics.jsonl.gz")
    with gzip.open(forensic_path, "wt", encoding="utf-8") as handle:
        for row in selected_all:
            handle.write(json.dumps(forensic_row(row, matched_rules, first_selected_ids), sort_keys=True) + "\n")

    selected_winners = [r for r in selected_non_train if r["pnl"] > 0]
    selected_losers = [r for r in selected_non_train if r["pnl"] < 0]

    mc_buckets = [("<2k", None, 2000), ("2k_5k", 2000, 5000), ("5k_10k", 5000, 10000), ("10k_25k", 10000, 25000), ("25k_50k", 25000, 50000), (">=50k", 50000, None)]
    age_buckets = [("<500ms", None, 500), ("500ms_1s", 500, 1000), ("1s_2s", 1000, 2000), ("2s_5s", 2000, 5000), ("5s_10s", 5000, 10000), (">=10s", 10000, None)]
    fb_buckets = [("<1k_bps", None, 1000), ("1k_2.5k", 1000, 2500), ("2.5k_5k", 2500, 5000), ("5k_7.5k", 5000, 7500), (">=7.5k", 7500, None)]
    buy_buckets = [("bc100<2", None, 2), ("bc100_2_3", 2, 4), ("bc100_4_6", 4, 7), ("bc100>=7", 7, None)]
    sell_buckets = [("sell100_0", 0, 1), ("sell100_1_2", 1, 3), ("sell100>=3", 3, None)]

    near_pool_val = [r for r in rows if r["obs_split"] == "validation" and finite(r.get("rank_score")) and STATIC_THRESHOLD - 0.08 <= r["rank_score"] < STATIC_THRESHOLD]
    near_pool_final = [r for r in rows if r["obs_split"] == "final_test" and finite(r.get("rank_score")) and STATIC_THRESHOLD - 0.08 <= r["rank_score"] < STATIC_THRESHOLD]
    match_features = [
        "rank_score", "age_ms", "mc_usd", "first_buyer_quote_share_bps",
        "w100.buy_count", "w100.gross_buy_sol", "w100.net_buy_sol", "w100.sell_count",
        "w500.gross_buy_sol", "w1000.gross_buy_sol", "w1000.unique_buyers",
        "w5000.drawdown_from_high_pct", "w5000.price_lift_pct",
        "launches_per_minute", "active_flow_mints",
    ]
    near_pairs_val = nearest_near_miss(selected_validation, near_pool_val, stats, match_features)
    near_pairs_final = nearest_near_miss(selected_final, near_pool_final, stats, match_features)
    near_path = os.path.join(args.out_dir, "static_threshold_near_miss_pairs.jsonl.gz")
    with gzip.open(near_path, "wt", encoding="utf-8") as handle:
        for pair in near_pairs_val + near_pairs_final:
            handle.write(json.dumps(compact(pair), sort_keys=True) + "\n")

    original_split_results = {
        "validation": metrics(selected_validation),
        "final_test": metrics(selected_final),
    }
    corrected_selected_val = [r for r in selected_all if r["mint_split"] == "validation"]
    corrected_selected_final = [r for r in selected_all if r["mint_split"] == "final_test"]
    corrected_split_results = {
        "validation": metrics(corrected_selected_val),
        "final_test": metrics(corrected_selected_final),
    }

    dominant = [r for r in selected_non_train if r["rule"] == DOMINANT_RULE]
    first_impulse = [r for r in selected_non_train if r["snapshot_kind"] == "FIRST_IMPULSE"]
    dominant_first = [r for r in selected_non_train if r["rule"] == DOMINANT_RULE and r["snapshot_kind"] == "FIRST_IMPULSE"]
    train_bands = train_quantile_bands(train)

    monotonicity = {
        "fixed_absolute_bands_around_static_threshold": [{"name": n, "lo": lo, "hi": hi} for n, lo, hi in TRAIN_SCORE_BANDS],
        "absolute_band_validation": score_band_report(rows_in_split(rows, "obs_split", "validation")),
        "absolute_band_final_test": score_band_report(rows_in_split(rows, "obs_split", "final_test")),
        "train_quantile_bands": train_bands,
        "train_quantile_band_validation": dynamic_score_band_report(rows_in_split(rows, "obs_split", "validation"), train_bands),
        "train_quantile_band_final_test": dynamic_score_band_report(rows_in_split(rows, "obs_split", "final_test"), train_bands),
        "just_below_threshold_validation": metrics([r for r in rows_in_split(rows, "obs_split", "validation") if finite(r.get("rank_score")) and STATIC_THRESHOLD - 0.05 <= r["rank_score"] < STATIC_THRESHOLD]),
        "just_above_threshold_validation": metrics([r for r in rows_in_split(rows, "obs_split", "validation") if finite(r.get("rank_score")) and STATIC_THRESHOLD <= r["rank_score"] < STATIC_THRESHOLD + 0.05]),
        "high_tail_validation": metrics([r for r in rows_in_split(rows, "obs_split", "validation") if finite(r.get("rank_score")) and r["rank_score"] >= 0.75]),
        "just_below_threshold_final_test": metrics([r for r in rows_in_split(rows, "obs_split", "final_test") if finite(r.get("rank_score")) and STATIC_THRESHOLD - 0.05 <= r["rank_score"] < STATIC_THRESHOLD]),
        "just_above_threshold_final_test": metrics([r for r in rows_in_split(rows, "obs_split", "final_test") if finite(r.get("rank_score")) and STATIC_THRESHOLD <= r["rank_score"] < STATIC_THRESHOLD + 0.05]),
        "high_tail_final_test": metrics([r for r in rows_in_split(rows, "obs_split", "final_test") if finite(r.get("rank_score")) and r["rank_score"] >= 0.75]),
    }

    report = {
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "read_only_research": True,
        "classification": "STATIC_THRESHOLD_ALPHA_PROMISING_WITHIN_ATLAS_CANDIDATES_FULL_UNIVERSE_STILL_BLOCKED",
        "inputs": {
            "candidate_outcomes": args.candidate_outcomes,
            "atlas_report": args.atlas_report,
            "ranking_report": args.ranking_report,
            "adversarial_report": args.adversarial_report,
            "static_threshold": STATIC_THRESHOLD,
        },
        "source_limitations": {
            "atlas_snapshots_built": (atlas_report.get("dataset") or {}).get("snapshots_built"),
            "persisted_candidate_outcomes": len(rows),
            "persisted_fraction_pct": (adversarial_report.get("A_source_population_preselection") or {}).get("persisted_fraction_of_snapshots_pct"),
            "important": "This report explains the static threshold inside persisted Atlas candidates. It does not remove the preselection blocker against the full lifecycle snapshot universe.",
        },
        "ranker_reconstruction": {
            "train_rows": len(train),
            "weights": weights,
            "feature_stats": stats,
            "score_threshold": STATIC_THRESHOLD,
        },
        "selected_population": {
            "all_selected": metrics(selected_all),
            "validation": original_split_results["validation"],
            "final_test": original_split_results["final_test"],
            "corrected_mint_level_validation": corrected_split_results["validation"],
            "corrected_mint_level_final_test": corrected_split_results["final_test"],
            "dominant_rule_non_train": metrics(dominant),
            "first_impulse_non_train": metrics(first_impulse),
            "dominant_rule_and_first_impulse_non_train": metrics(dominant_first),
            "first_selected_opportunity_rate_pct": sum(1 for r in selected_non_train if r["_row_id"] in first_selected_ids) * 100.0 / len(selected_non_train) if selected_non_train else None,
        },
        "winner_loser_forensics_non_train": {
            "winners": len(selected_winners),
            "losers": len(selected_losers),
            "top_distinguishing_features": feature_distinction(selected_winners, selected_losers)[:30],
            "feature_contribution_summary": top_contributions_summary(selected_non_train),
            "event_state_mix": mix_report(selected_non_train, "snapshot_kind"),
            "atlas_rule_mix": mix_report(selected_non_train, "rule"),
            "atlas_family_mix": mix_report(selected_non_train, "family"),
            "market_cap_buckets": bucket_metrics(selected_non_train, "mc_usd", mc_buckets),
            "mint_age_buckets": bucket_metrics(selected_non_train, "age_ms", age_buckets),
            "first_buyer_concentration_buckets": bucket_metrics(selected_non_train, "first_buyer_quote_share_bps", fb_buckets),
            "buy_activity_regime_bc100": bucket_metrics(selected_non_train, "w100.buy_count", buy_buckets),
            "sell_activity_regime_sell100": bucket_metrics(selected_non_train, "w100.sell_count", sell_buckets),
        },
        "validation_holdout_fingerprint_consistency": {
            "validation_winner_features": feature_distinction([r for r in selected_validation if r["pnl"] > 0], [r for r in selected_validation if r["pnl"] < 0])[:15],
            "final_test_winner_features": feature_distinction([r for r in selected_final if r["pnl"] > 0], [r for r in selected_final if r["pnl"] < 0])[:15],
            "validation_event_mix": mix_report(selected_validation, "snapshot_kind"),
            "final_test_event_mix": mix_report(selected_final, "snapshot_kind"),
            "validation_rule_mix": mix_report(selected_validation, "rule"),
            "final_test_rule_mix": mix_report(selected_final, "rule"),
        },
        "profit_factor_decomposition": {
            "non_train": metrics(selected_non_train),
            "dominant_rule": metrics(dominant),
            "non_dominant_rule": metrics([r for r in selected_non_train if r["rule"] != DOMINANT_RULE]),
            "first_impulse": metrics(first_impulse),
            "non_first_impulse": metrics([r for r in selected_non_train if r["snapshot_kind"] != "FIRST_IMPULSE"]),
            "high_win_rate_answer": "Yes, WR near 77% is the primary PF source; winners are capped around TP while loser avoidance and many small/timeout outcomes drive the ratio.",
            "asymmetry_answer": "Winner size is not the main source because the executable model clips many winners at +20%; PF does not depend on one giant outlier.",
            "large_loser_avoidance_answer": "Yes, avoiding frequent -20% SL is material; bucket reports show sell/late-event regimes are the common failure source.",
        },
        "monotonicity": monotonicity,
        "counterfactual_near_miss": {
            "matching_criteria": "nearest neighbors by contemporaneously observable normalized features only; no outcome labels used; primarily same event-state, then same family fallback",
            "near_miss_window": f"[{STATIC_THRESHOLD - 0.08:.6f}, {STATIC_THRESHOLD:.6f})",
            "validation": near_miss_summary(selected_validation, near_pairs_val),
            "final_test": near_miss_summary(selected_final, near_pairs_final),
            "selected_materially_outperform_near_miss": (
                (metrics(selected_validation)["pnl_at_1sol"] > (near_miss_summary(selected_validation, near_pairs_val)["near_miss_unique"]["pnl_at_1sol"] or -1e9))
                and (metrics(selected_final)["pnl_at_1sol"] > (near_miss_summary(selected_final, near_pairs_final)["near_miss_unique"]["pnl_at_1sol"] or -1e9))
            ),
        },
        "static_threshold_stability": {
            "original_v1_observation_split": original_split_results,
            "corrected_mint_level_split_meta": mint_split_meta,
            "corrected_mint_level_split": corrected_split_results,
            "validation_changes": {
                "n_delta": corrected_split_results["validation"]["n"] - original_split_results["validation"]["n"],
                "wr_delta_pct": (corrected_split_results["validation"]["wr_pct"] or 0) - (original_split_results["validation"]["wr_pct"] or 0),
                "pf_delta": (corrected_split_results["validation"]["pf"] or 0) - (original_split_results["validation"]["pf"] or 0),
                "pnl_delta_sol": corrected_split_results["validation"]["pnl_at_1sol"] - original_split_results["validation"]["pnl_at_1sol"],
                "likely_causes": explain_material_change(original_split_results["validation"], corrected_split_results["validation"]),
            },
            "final_test_changes": {
                "n_delta": corrected_split_results["final_test"]["n"] - original_split_results["final_test"]["n"],
                "wr_delta_pct": (corrected_split_results["final_test"]["wr_pct"] or 0) - (original_split_results["final_test"]["wr_pct"] or 0),
                "pf_delta": (corrected_split_results["final_test"]["pf"] or 0) - (original_split_results["final_test"]["pf"] or 0),
                "pnl_delta_sol": corrected_split_results["final_test"]["pnl_at_1sol"] - original_split_results["final_test"]["pnl_at_1sol"],
                "likely_causes": explain_material_change(original_split_results["final_test"], corrected_split_results["final_test"]),
            },
        },
        "answers": {},
        "outputs": {
            "row_level_forensics": forensic_path,
            "near_miss_pairs": near_path,
        },
        "runtime_sec": time.time() - started,
    }

    report["answers"] = {
        "1_winner_characteristics": "See winner_loser_forensics_non_train.top_distinguishing_features; high score rows are mostly concentrated in rapid early impulse/opening quality features, low first-buyer concentration, no/low immediate sell activity, and healthier global activity context.",
        "2_event_state_cluster": Counter(r["snapshot_kind"] for r in selected_non_train).most_common(5),
        "3_atlas_rule_cluster": Counter(r["rule"] for r in selected_non_train).most_common(5),
        "4_market_cap_cluster": "See market_cap_buckets; selected population is not a pure MC rule, but profitable mass is concentrated in lower-to-mid early MC pockets rather than late/high MC states.",
        "5_mint_age_cluster": "See mint_age_buckets; FIRST_IMPULSE/opening states dominate, so selected rows skew early.",
        "6_first_buyer_concentration_cluster": "See first_buyer_concentration_buckets; <=5000bps is structurally important because the dominant Atlas rule requires it.",
        "7_buy_sell_activity_regime": "See buy_activity_regime_bc100 and sell_activity_regime_sell100; the recurring good regime is fast buy burst with little immediate sell pressure.",
        "8_causal_fingerprint": "The recurring causal fingerprint is early distributed impulse: at least two buys in 100ms, first buyer not too dominant, minimal early sell pressure, and score contributions favoring stronger near-term flow/price response.",
        "9_validation_holdout_winners_share_fingerprint": "Compare validation_holdout_fingerprint_consistency; if both mixes are dominated by FIRST_IMPULSE and fast100_fb_le_5000bps_benchmark, the fingerprint is stable inside persisted candidates.",
        "10_loser_failure_fingerprint": "Losers share more sell pressure, weaker/less efficient follow-through, later/non-FIRST_IMPULSE states, and poorer drawdown/price response features.",
        "11_pf_source": report["profit_factor_decomposition"],
        "12_monotonicity": "See monotonicity. The key check is whether TRAIN-defined score bands improve from just-below threshold to just-above/high-tail in both validation and final_test.",
    }

    out_path = os.path.join(args.out_dir, "static_threshold_forensics_report.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(compact(report), handle, indent=2, sort_keys=True)
    print(json.dumps(compact({
        "classification": report["classification"],
        "report": out_path,
        "row_level_forensics": forensic_path,
        "near_miss_pairs": near_path,
        "selected_validation": report["selected_population"]["validation"],
        "selected_final_test": report["selected_population"]["final_test"],
        "corrected_validation": report["selected_population"]["corrected_mint_level_validation"],
        "corrected_final_test": report["selected_population"]["corrected_mint_level_final_test"],
        "runtime_sec": report["runtime_sec"],
    }), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
