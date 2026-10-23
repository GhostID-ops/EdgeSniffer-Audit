#!/usr/bin/env python3
import argparse
import gzip
import importlib.util
import json
import math
import os
import time
from collections import Counter, defaultdict, deque


STATIC_THRESHOLD = 0.590596
ENTRY_DELAY_MS = 25
SELLABLE_P50_MS = 964
SELLABLE_P75_MS = 1476
SELLABLE_P90_MS = 1989
TP = 20.0
SL = -20.0
HOLD_MS = 120_000


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    mints = set()
    for row in rows:
        pnl = num(row.get(key))
        if pnl is None:
            continue
        vals.append(pnl)
        outcomes[row.get("outcome") or "UNKNOWN"] += 1
        if row.get("mint"):
            mints.add(row["mint"])
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    total = sum(vals)
    pf = gross_win / gross_loss if gross_loss else (float("inf") if gross_win else None)
    return {
        "n": len(vals),
        "unique_mints": len(mints),
        "wins": len(wins),
        "losses": len(losses),
        "wr_pct": len(wins) * 100.0 / len(vals) if vals else None,
        "pf": pf,
        "expectancy_pct": total / len(vals) if vals else None,
        "median_return_pct": q(vals, 0.5),
        "pnl_at_1sol": total / 100.0,
        "tp": outcomes.get("TP", 0),
        "sl": outcomes.get("SL", 0),
        "max_hold": outcomes.get("max_hold", 0) + outcomes.get("MAX_HOLD", 0) + outcomes.get("HOLD", 0),
        "other": len(vals) - outcomes.get("TP", 0) - outcomes.get("SL", 0) - outcomes.get("max_hold", 0) - outcomes.get("MAX_HOLD", 0) - outcomes.get("HOLD", 0),
        "pf_ex_best1": pf_ex_best(vals, 1),
        "pf_ex_best3": pf_ex_best(vals, 3),
        "pf_ex_best5": pf_ex_best(vals, 5),
        "pf_ex_best10": pf_ex_best(vals, 10),
        "top1_profit_contribution_pct": (sorted(wins, reverse=True)[0] / total * 100.0) if wins and total > 0 else None,
        "top3_profit_contribution_pct": (sum(sorted(wins, reverse=True)[:3]) / total * 100.0) if wins and total > 0 else None,
        "top10_profit_contribution_pct": (sum(sorted(wins, reverse=True)[:10]) / total * 100.0) if wins and total > 0 else None,
    }


def pf_ex_best(vals, n):
    xs = [v for v in vals if finite(v)]
    for value in sorted([v for v in xs if v > 0], reverse=True)[:n]:
        try:
            xs.remove(value)
        except ValueError:
            pass
    wins = [v for v in xs if v > 0]
    losses = [v for v in xs if v < 0]
    return sum(wins) / abs(sum(losses)) if losses else (float("inf") if wins else None)


def score_distribution(rows):
    scores = [r.get("rank_score") for r in rows if finite(r.get("rank_score"))]
    selected = [r for r in rows if finite(r.get("rank_score")) and r["rank_score"] >= STATIC_THRESHOLD]
    span_hours = span_hours_of(rows)
    return {
        "n": len(scores),
        "median": q(scores, 0.5),
        "p90": q(scores, 0.90),
        "p95": q(scores, 0.95),
        "p99": q(scores, 0.99),
        "p99_5": q(scores, 0.995),
        "max": max(scores) if scores else None,
        "pct_gte_threshold": len(selected) * 100.0 / len(scores) if scores else None,
        "selected_observations_per_hour": len(selected) / span_hours if span_hours else None,
        "selected_unique_mints_per_hour": len({r["mint"] for r in selected}) / span_hours if span_hours else None,
    }


def span_hours_of(rows):
    times = [r["ts"] for r in rows if r.get("ts")]
    if len(times) < 2:
        return None
    return (max(times) - min(times)) / 3_600_000.0


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


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(compact(obj), handle, indent=2, sort_keys=True)


def candidate_train_rows(forensics, candidate_outcomes):
    rows = []
    for i, raw in enumerate(read_jsonl(candidate_outcomes), 1):
        row = forensics.normalize_row(raw, i)
        if row.get("mint") and row.get("ts"):
            rows.append(row)
    rows = sorted(rows, key=lambda r: (r["ts"], r["_row_id"]))
    a = int(len(rows) * 0.50)
    return rows, rows[:a]


def candidate_key_set(candidate_outcomes):
    keys = set()
    atlas_rows = []
    for raw in read_jsonl(candidate_outcomes):
        mint = raw.get("mint")
        ts = raw.get("ts")
        kind = raw.get("snapshot_kind")
        if mint and ts and kind:
            keys.add((mint, int(ts), str(kind)))
            atlas_rows.append({
                "mint": mint,
                "ts": int(ts),
                "snapshot_kind": str(kind),
                "pnl": num(raw.get("pnl")),
                "outcome": raw.get("outcome"),
                "rank_score": None,
            })
    return keys, atlas_rows


def first_seen_split(first_seen):
    ordered = sorted(first_seen, key=lambda mint: (first_seen[mint], mint))
    a = int(len(ordered) * 0.50)
    b = int(len(ordered) * 0.75)
    split = {}
    for i, mint in enumerate(ordered):
        split[mint] = "train" if i < a else ("validation" if i < b else "final_test")
    return split, {
        "unique_mints": len(ordered),
        "train_mints": a,
        "validation_mints": b - a,
        "final_test_mints": len(ordered) - b,
        "train_end_create_ts": first_seen[ordered[a - 1]] if a else None,
        "validation_end_create_ts": first_seen[ordered[b - 1]] if b else None,
        "mint_overlap": {"train_validation": 0, "train_final_test": 0, "validation_final_test": 0},
    }


def matched_rules_for_features(rules, feats):
    out = []
    for rule in rules:
        try:
            if rule["test"](feats):
                out.append({"rule": rule["name"], "family": rule["family"]})
        except Exception:
            continue
    return out


def generate_full_universe(atlas, forensics, capsules, out_tmp, candidate_keys, stats, weights, rules, limit_rows=0):
    flows = defaultdict(deque)
    first_seen = {}
    emitted_fixed = defaultdict(set)
    emitted_events = defaultdict(set)
    global_first_seen = deque()
    counters = Counter()
    snapshot_counts = Counter()
    exclusion = Counter()
    time_min = time_max = None
    rows_scanned = flow_rows = buy_rows = sell_rows = snapshots_built = scoreable = 0
    started = time.time()

    def emit(row, feats, source_row_id, out):
        nonlocal snapshots_built, scoreable
        snapshots_built += 1
        snapshot_counts[feats["snapshot_kind"]] += 1
        if not is_scoreable(feats):
            exclusion["unscoreable_missing_weighted_feature"] += 1
            return
        shell = {name: num(feats.get(name)) for name in forensics.FEATURES}
        rank_score, norms, contribs = forensics.score_with_contribs(shell, stats, weights)
        if not finite(rank_score):
            exclusion["rank_score_null"] += 1
            return
        matched_rules = matched_rules_for_features(rules, feats)
        record = {
            "mint": feats["mint"],
            "ts": int(feats["ts"]),
            "snapshot_kind": feats["snapshot_kind"],
            "snapshot_age_ms": feats.get("snapshot_age_ms"),
            "age_ms": num(feats.get("age_ms")),
            "mc_usd": num(feats.get("mc_usd")),
            "rank_score": rank_score,
            "is_original_atlas_candidate": (feats["mint"], int(feats["ts"]), feats["snapshot_kind"]) in candidate_keys,
            "matched_atlas_rules": matched_rules,
            "matched_atlas_rule_names": [r["rule"] for r in matched_rules],
            "matched_atlas_families": sorted({r["family"] for r in matched_rules}),
            "features": {name: num(feats.get(name)) for name in forensics.FEATURES},
            "normalized_feature_values": norms,
            "feature_contributions": contribs,
            "source_row_id": source_row_id,
        }
        scoreable += 1
        out.write(json.dumps(compact(record), separators=(",", ":")) + "\n")

    with gzip.open(out_tmp, "wt", encoding="utf-8") as out:
        for source_row_id, row in enumerate(atlas.read_jsonl(capsules), 1):
            rows_scanned += 1
            if limit_rows and rows_scanned > limit_rows:
                break
            mint = atlas.mint_of(row)
            ts = atlas.ts_of(row)
            mc = atlas.mc_of(row)
            side = atlas.side_of(row)
            if not mint or not finite(ts):
                exclusion["missing_mint_or_ts"] += 1
                continue
            ts = int(ts)
            time_min = ts if time_min is None else min(time_min, ts)
            time_max = ts if time_max is None else max(time_max, ts)
            if mint not in first_seen:
                first_seen[mint] = ts
                global_first_seen.append((ts, mint))
            while global_first_seen and global_first_seen[0][0] < ts - 60_000:
                global_first_seen.popleft()
            if not finite(mc) or not (0 < float(mc) <= atlas.MAX_MC_USD):
                exclusion["bad_or_out_of_range_mc"] += 1
                continue
            if side not in ("buy", "sell"):
                exclusion["not_buy_or_sell"] += 1
                continue
            event = atlas.build_event(row, ts, mc, side)
            dq = flows[mint]
            dq.append(event)
            while dq and dq[0]["ts"] < ts - atlas.HOLD_MS:
                dq.popleft()
            flow_rows += 1
            buy_rows += 1 if side == "buy" else 0
            sell_rows += 1 if side == "sell" else 0
            age = atlas.creation_age_ms(row, first_seen[mint], ts)
            global_state = {
                "launches_per_minute": len({m for _, m in global_first_seen}),
                "active_flow_mints": len(flows),
            }
            for age_ms in atlas.FIXED_AGES_MS:
                if age >= age_ms and age_ms not in emitted_fixed[mint]:
                    emitted_fixed[mint].add(age_ms)
                    feats = atlas.feature_row(dq, row, ts, mc, first_seen[mint], f"FIXED_{age_ms}MS", age_ms, global_state)
                    emit(row, feats, source_row_id, out)
            if side == "buy":
                event_kinds = []
                quick = atlas.window(dq, ts, 1000)
                w500 = atlas.window(dq, ts, 500)
                w5000 = atlas.window(dq, ts, 5000)
                w10000 = atlas.window(dq, ts, 10000)
                if "FIRST_IMPULSE" not in emitted_events[mint] and quick["gross_buy_sol"] >= 2:
                    event_kinds.append("FIRST_IMPULSE")
                if "SECOND_ACCELERATION" not in emitted_events[mint] and w500["gross_buy_sol"] >= 2 and sum(e["quote_sol"] for e in dq if ts - 5000 <= e["ts"] < ts - 1000 and e["side"] == "buy") >= 1:
                    event_kinds.append("SECOND_ACCELERATION")
                if "FIRST_LARGE_SELL_ABSORPTION" not in emitted_events[mint] and w5000["sell_count"] >= 1 and w5000["gross_buy_sol"] >= 4 and (w5000["drawdown_from_high_pct"] or 0) >= -10:
                    event_kinds.append("FIRST_LARGE_SELL_ABSORPTION")
                if "POST_PULLBACK_BREAKOUT" not in emitted_events[mint] and finite(w10000["drawdown_from_high_pct"]) and -25 <= w10000["drawdown_from_high_pct"] <= -4 and quick["gross_buy_sol"] >= 2:
                    event_kinds.append("POST_PULLBACK_BREAKOUT")
                if "CONSOLIDATION_BREAKOUT" not in emitted_events[mint] and finite(w10000["range_width_pct"]) and w10000["range_width_pct"] <= 25 and quick["gross_buy_sol"] >= 2 and (w10000["drawdown_from_high_pct"] or 0) >= -3:
                    event_kinds.append("CONSOLIDATION_BREAKOUT")
                for kind in event_kinds:
                    emitted_events[mint].add(kind)
                    feats = atlas.feature_row(dq, row, ts, mc, first_seen[mint], kind, age, global_state)
                    emit(row, feats, source_row_id, out)
            elif side == "sell" and "FIRST_LARGE_SELLER" not in emitted_events[mint]:
                emitted_events[mint].add("FIRST_LARGE_SELLER")
                feats = atlas.feature_row(dq, row, ts, mc, first_seen[mint], "FIRST_LARGE_SELLER", age, global_state)
                emit(row, feats, source_row_id, out)
            if rows_scanned % 1_000_000 == 0:
                stale = ts - atlas.HOLD_MS
                for stale_mint in [key for key, value in flows.items() if not value or value[-1]["ts"] < stale]:
                    del flows[stale_mint]
                print(json.dumps(compact({
                    "progress": "full_universe_pass1",
                    "rows_scanned": rows_scanned,
                    "snapshots_built": snapshots_built,
                    "scoreable": scoreable,
                    "selected_so_far": counters.get("selected", 0),
                    "active_flow_mints": len(flows),
                    "elapsed_sec": time.time() - started,
                })), flush=True)

    return {
        "rows_scanned": rows_scanned,
        "flow_rows": flow_rows,
        "buy_rows": buy_rows,
        "sell_rows": sell_rows,
        "unique_mints": len(first_seen),
        "time_start_ms": time_min,
        "time_end_ms": time_max,
        "span_hours": ((time_max - time_min) / 3_600_000) if time_min and time_max else None,
        "snapshots_built": snapshots_built,
        "scoreable_snapshots": scoreable,
        "snapshot_counts": dict(snapshot_counts),
        "excluded_rows": dict(exclusion),
        "first_seen": first_seen,
    }


def is_scoreable(feats):
    return feats.get("mint") and finite(feats.get("ts")) and finite(feats.get("mc_usd"))


def assign_and_select(score_tmp, score_out, split_by_mint_map):
    split_rows = defaultdict(list)
    selected = []
    all_rows_count = 0
    selected_counts = Counter()
    with gzip.open(score_tmp, "rt", encoding="utf-8") as src, gzip.open(score_out, "wt", encoding="utf-8") as dst:
        for line in src:
            row = json.loads(line)
            row["split"] = split_by_mint_map.get(row["mint"], "unknown")
            all_rows_count += 1
            split_rows[row["split"]].append({
                "mint": row["mint"],
                "ts": row["ts"],
                "rank_score": row["rank_score"],
                "is_original_atlas_candidate": row["is_original_atlas_candidate"],
            })
            if row["rank_score"] >= STATIC_THRESHOLD:
                selected.append(row)
                selected_counts[row["split"]] += 1
            dst.write(json.dumps(compact(row), separators=(",", ":")) + "\n")
    return all_rows_count, selected, split_rows, selected_counts


def mark_ranges(selected):
    ranges = {}
    for row in selected:
        mint = row["mint"]
        start, end = ranges.get(mint, (row["ts"], row["ts"]))
        ranges[mint] = (min(start, row["ts"]), max(end, row["ts"]))
    return {mint: (start, end + HOLD_MS + SELLABLE_P90_MS + 1000) for mint, (start, end) in ranges.items()}


def collect_marks(atlas, capsules, selected, limit_rows=0):
    ranges = mark_ranges(selected)
    marks = defaultdict(list)
    rows_scanned = marks_used = 0
    started = time.time()
    for row in atlas.read_jsonl(capsules):
        rows_scanned += 1
        if limit_rows and rows_scanned > limit_rows:
            break
        mint = atlas.mint_of(row)
        if mint not in ranges:
            continue
        ts = atlas.ts_of(row)
        mc = atlas.mc_of(row)
        if finite(ts) and finite(mc):
            ts = int(ts)
            start, end = ranges[mint]
            if start <= ts <= end and 0 < float(mc) <= atlas.MAX_MC_USD:
                marks[mint].append((ts, float(mc)))
                marks_used += 1
        if rows_scanned % 2_000_000 == 0:
            print(json.dumps(compact({"progress": "full_universe_marks_pass", "rows_scanned": rows_scanned, "marks_used": marks_used, "elapsed_sec": time.time() - started})), flush=True)
    for arr in marks.values():
        arr.sort()
    return marks, {"rows_scanned": rows_scanned, "marks_used": marks_used, "selected_mints": len(ranges)}


def score_selected_with_outcomes(atlas, selected, marks, out_path):
    out_rows = []
    with gzip.open(out_path, "wt", encoding="utf-8") as out:
        for row in sorted(selected, key=lambda r: (r["ts"], r["mint"], r["rank_score"])):
            mark_rows = marks.get(row["mint"], [])
            primary = atlas.score(row["ts"], row["mc_usd"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P75_MS, 0)
            p50 = atlas.score(row["ts"], row["mc_usd"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P50_MS, 0)
            p90 = atlas.score(row["ts"], row["mc_usd"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P90_MS, 0)
            stress5 = atlas.score(row["ts"], row["mc_usd"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P75_MS, 5)
            stress10 = atlas.score(row["ts"], row["mc_usd"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P75_MS, 10)
            enriched = {
                **row,
                **primary,
                "p50": p50,
                "p90": p90,
                "stress5": stress5,
                "stress10": stress10,
                "stress5_pnl": stress5.get("pnl"),
                "stress10_pnl": stress10.get("pnl"),
            }
            out_rows.append(enriched)
            out.write(json.dumps(compact(enriched), separators=(",", ":")) + "\n")
    return out_rows


def first_per_mint(rows):
    chosen = {}
    for row in sorted(rows, key=lambda r: (r["ts"], -r["rank_score"])):
        chosen.setdefault(row["mint"], row)
    return sorted(chosen.values(), key=lambda r: r["ts"])


def by_split(rows):
    return {name: [r for r in rows if r.get("split") == name] for name in ("train", "validation", "final_test")}


def atlas_nonatlas(rows):
    return {
        "ORIGINAL_ATLAS_CANDIDATE": metrics([r for r in rows if r.get("is_original_atlas_candidate")]),
        "FULL_UNIVERSE_NON_ATLAS": metrics([r for r in rows if not r.get("is_original_atlas_candidate")]),
    }


def membership_report(rows):
    def has_rule(row, rule):
        return rule in (row.get("matched_atlas_rule_names") or [])
    groups = {
        "FIRST_IMPULSE": [r for r in rows if r.get("snapshot_kind") == "FIRST_IMPULSE"],
        "fast100_fb_le_5000bps": [r for r in rows if has_rule(r, "fast100_fb_le_5000bps_benchmark")],
        "both": [r for r in rows if r.get("snapshot_kind") == "FIRST_IMPULSE" and has_rule(r, "fast100_fb_le_5000bps_benchmark")],
        "neither": [r for r in rows if r.get("snapshot_kind") != "FIRST_IMPULSE" and not has_rule(r, "fast100_fb_le_5000bps_benchmark")],
    }
    return {k: metrics(v) for k, v in groups.items()}


def bucket(value, buckets):
    if value is None:
        return "missing"
    for label, lo, hi in buckets:
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return label
    return "other"


def feature_buckets(rows):
    specs = {
        "buy_count_100ms": ("features.w100.buy_count", [("1", 1, 2), ("2-3", 2, 4), ("4-6", 4, 7), ("7+", 7, None)]),
        "first_buyer_bps": ("features.first_buyer_quote_share_bps", [("<1k", None, 1000), ("1k-2.5k", 1000, 2500), ("2.5k-5k", 2500, 5000), ("5k-7.5k", 5000, 7500), ("7.5k+", 7500, None)]),
        "mint_age_ms": ("age_ms", [("<500ms", None, 500), ("500ms-1s", 500, 1000), ("1s-2s", 1000, 2000), ("2s-5s", 2000, 5000), ("5s-10s", 5000, 10000), ("10s+", 10000, None)]),
        "mc_usd": ("mc_usd", [("<2k", None, 2000), ("2k-5k", 2000, 5000), ("5k-10k", 5000, 10000), ("10k-25k", 10000, 25000), ("25k-50k", 25000, 50000), ("50k+", 50000, None)]),
    }
    out = {}
    for name, (path, buckets) in specs.items():
        grouped = defaultdict(list)
        for row in rows:
            if path.startswith("features."):
                value = (row.get("features") or {}).get(path.split(".", 1)[1])
            else:
                value = row.get(path)
            grouped[bucket(num(value), buckets)].append(row)
        out[name] = {k: metrics(v) for k, v in sorted(grouped.items())}
    return out


def score_bands_from_train(full_train_rows):
    scores = [r.get("rank_score") for r in full_train_rows if finite(r.get("rank_score"))]
    points = [
        ("min", q(scores, 0.0)),
        ("p50", q(scores, 0.5)),
        ("p75", q(scores, 0.75)),
        ("p90", q(scores, 0.90)),
        ("p95", q(scores, 0.95)),
        ("p98", q(scores, 0.98)),
        ("threshold", STATIC_THRESHOLD),
        ("p99_5", q(scores, 0.995)),
        ("max", q(scores, 1.0)),
    ]
    ordered = []
    seen = set()
    for label, value in sorted(points, key=lambda x: -1e99 if x[1] is None else x[1]):
        if value is None:
            continue
        key = round(value, 12)
        if key not in seen:
            ordered.append((label, value))
            seen.add(key)
    bands = []
    for idx in range(len(ordered) - 1):
        bands.append({"name": f"train_{ordered[idx][0]}_to_{ordered[idx+1][0]}", "lo": ordered[idx][1], "hi": ordered[idx+1][1]})
    return bands


def band_metrics(scored_rows, selected_rows_with_outcomes, bands):
    outcome_by_key = {(r["mint"], r["ts"], r["snapshot_kind"]): r for r in selected_rows_with_outcomes}
    out = {}
    for band in bands:
        rows = []
        for row in scored_rows:
            score = row.get("rank_score")
            if finite(score) and score >= band["lo"] and score < band["hi"]:
                key = (row["mint"], row["ts"], row["snapshot_kind"])
                if key in outcome_by_key:
                    rows.append(outcome_by_key[key])
        out[band["name"]] = metrics(rows)
    return out


def near_miss(rows):
    return {
        "just_below_threshold": metrics([r for r in rows if finite(r.get("rank_score")) and STATIC_THRESHOLD - 0.05 <= r["rank_score"] < STATIC_THRESHOLD]),
        "just_above_threshold": metrics([r for r in rows if finite(r.get("rank_score")) and STATIC_THRESHOLD <= r["rank_score"] < STATIC_THRESHOLD + 0.05]),
        "high_score_tail_ge_0p65": metrics([r for r in rows if finite(r.get("rank_score")) and r["rank_score"] >= 0.65]),
    }


def concurrency(rows, max_open):
    active = []
    accepted = []
    for row in sorted(rows, key=lambda r: r["ts"]):
        active = [x for x in active if x > row["ts"]]
        if len(active) >= max_open:
            continue
        exit_ms = row.get("exit_ms")
        close_ts = row["ts"] + (int(exit_ms) if finite(exit_ms) else HOLD_MS)
        active.append(close_ts)
        accepted.append(row)
    return metrics(accepted)


def daily(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["ts"] // 86_400_000].append(row)
    return {str(k): metrics(v) for k, v in sorted(groups.items()) if len(v) >= 5}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capsules", required=True)
    ap.add_argument("--candidate-outcomes", required=True)
    ap.add_argument("--atlas-report", required=True)
    ap.add_argument("--ranking-report", required=True)
    ap.add_argument("--adversarial-report", required=True)
    ap.add_argument("--atlas-script", required=True)
    ap.add_argument("--forensics-script", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--limit-rows", type=int, default=0)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    started = time.time()

    atlas = load_module("atlas_v1", args.atlas_script)
    forensics = load_module("static_forensics_v1", args.forensics_script)

    atlas_report = json.load(open(args.atlas_report, "r", encoding="utf-8"))
    ranking_report = json.load(open(args.ranking_report, "r", encoding="utf-8"))
    adversarial_report = json.load(open(args.adversarial_report, "r", encoding="utf-8"))

    candidate_rows, candidate_train = candidate_train_rows(forensics, args.candidate_outcomes)
    stats, weights = forensics.train_ranker(candidate_train)
    for row in candidate_rows:
        row["rank_score"], _, _ = forensics.score_with_contribs(row, stats, weights)
    candidate_threshold_rows = [r for r in candidate_rows if finite(r.get("rank_score")) and r["rank_score"] >= STATIC_THRESHOLD]
    candidate_keys, atlas_rows_raw = candidate_key_set(args.candidate_outcomes)
    rules = atlas.rule_library()

    frozen_score_spec = {
        "classification": "FROZEN_V1_SCORE_SPEC",
        "source_candidate_population": args.candidate_outcomes,
        "do_not_retrain": True,
        "feature_names": forensics.FEATURES,
        "feature_transforms": "z=(value-train_median)/train_iqr_or_p90_p10, clipped to [-5,5]",
        "weights": weights,
        "normalization_constants": stats,
        "intercept": 0.0,
        "missing_value_handling": "feature skipped; score null only if no weighted features available",
        "score_formula": "sum(correlation_weight_i * clipped_train_z_i)",
        "static_threshold": STATIC_THRESHOLD,
        "parity_candidate_static_threshold_metrics": metrics(candidate_threshold_rows),
    }
    write_json(os.path.join(args.out_dir, "frozen_score_spec.json"), frozen_score_spec)

    tmp_scores = os.path.join(args.out_dir, "full_universe_scores.tmp.jsonl.gz")
    final_scores = os.path.join(args.out_dir, "full_universe_scores.jsonl.gz")
    manifest = generate_full_universe(atlas, forensics, args.capsules, tmp_scores, candidate_keys, stats, weights, rules, args.limit_rows)
    split_map, split_meta = first_seen_split(manifest.pop("first_seen"))
    score_count, selected, split_score_rows, selected_counts = assign_and_select(tmp_scores, final_scores, split_map)
    try:
        os.remove(tmp_scores)
    except OSError:
        pass

    marks, mark_meta = collect_marks(atlas, args.capsules, selected, args.limit_rows)
    selected_path = os.path.join(args.out_dir, "selected_trades.jsonl.gz")
    selected_outcomes = score_selected_with_outcomes(atlas, selected, marks, selected_path)
    one_per = first_per_mint(selected_outcomes)

    selected_by_split = by_split(selected_outcomes)
    one_by_split = by_split(one_per)
    full_score_by_split = {k: v for k, v in split_score_rows.items()}

    manifest_out = {
        **manifest,
        "population_classification": "FULL_ELIGIBLE_DECISION_STATE_UNIVERSE" if manifest["scoreable_snapshots"] >= 1_000_000 else "FULL_UNIVERSE_REPLAY_DATA_BLOCKER",
        "split": split_meta,
        "score_rows_written": score_count,
        "selected_threshold_crossings": len(selected),
        "selected_counts_by_split": dict(selected_counts),
        "mark_collection": mark_meta,
        "runtime_sec_partial": time.time() - started,
    }
    write_json(os.path.join(args.out_dir, "full_universe_manifest.json"), manifest_out)

    score_distribution_full = {split: score_distribution(rows) for split, rows in full_score_by_split.items()}
    score_distribution_candidate = score_distribution(candidate_rows)

    atlas_vs_nonatlas = {split: atlas_nonatlas(rows) for split, rows in selected_by_split.items()}
    write_json(os.path.join(args.out_dir, "atlas_vs_nonatlas.json"), atlas_vs_nonatlas)

    train_bands = score_bands_from_train(full_score_by_split.get("train", []))
    monotonicity = {
        "bands_train_defined_from_full_universe_train_scores": train_bands,
        "full_universe_selected_outcome_bands": {
            split: near_miss(rows) for split, rows in selected_by_split.items()
        },
        "atlas_only_selected_outcome_bands": {
            split: near_miss([r for r in rows if r.get("is_original_atlas_candidate")]) for split, rows in selected_by_split.items()
        },
        "note": "Only selected threshold-crossing rows have executable outcomes in this replay; non-selected bands are frequency/score diagnostics unless separately outcome-replayed.",
    }
    write_json(os.path.join(args.out_dir, "score_monotonicity.json"), monotonicity)

    entry_stress = {
        split: {
            "normal": metrics(rows),
            "plus5": metrics([{"mint": r["mint"], "ts": r["ts"], "pnl": (r.get("stress5") or {}).get("pnl"), "outcome": (r.get("stress5") or {}).get("outcome")} for r in rows]),
            "plus10": metrics([{"mint": r["mint"], "ts": r["ts"], "pnl": (r.get("stress10") or {}).get("pnl"), "outcome": (r.get("stress10") or {}).get("outcome")} for r in rows]),
        }
        for split, rows in one_by_split.items()
    }
    write_json(os.path.join(args.out_dir, "entry_stress.json"), entry_stress)

    sellable_sensitivity = {
        split: {
            "p50_964ms": metrics([{"mint": r["mint"], "ts": r["ts"], "pnl": (r.get("p50") or {}).get("pnl"), "outcome": (r.get("p50") or {}).get("outcome")} for r in rows]),
            "p75_1476ms": metrics(rows),
            "p90_1989ms": metrics([{"mint": r["mint"], "ts": r["ts"], "pnl": (r.get("p90") or {}).get("pnl"), "outcome": (r.get("p90") or {}).get("outcome")} for r in rows]),
        }
        for split, rows in one_by_split.items()
    }
    write_json(os.path.join(args.out_dir, "sellable_sensitivity.json"), sellable_sensitivity)

    concurrency_report = {str(k): {split: concurrency(rows, k) for split, rows in one_by_split.items()} for k in (1, 2, 3, 5)}
    concurrency_report["raw_independent"] = {split: metrics(rows) for split, rows in one_by_split.items()}
    write_json(os.path.join(args.out_dir, "concurrency.json"), concurrency_report)

    temporal = {split: daily(rows) for split, rows in one_by_split.items()}
    write_json(os.path.join(args.out_dir, "temporal_stability.json"), temporal)

    diagnostics_rows = one_by_split.get("validation", []) + one_by_split.get("final_test", [])
    fast_diagnostics = {
        "membership": membership_report(diagnostics_rows),
        "buckets": feature_buckets(diagnostics_rows),
    }

    final_report = {
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "read_only_research": True,
        "A_full_universe_coverage": manifest_out,
        "B_frozen_score_spec": frozen_score_spec,
        "C_parity_with_v1": {
            "v1_candidate_static_threshold_all": metrics(candidate_threshold_rows),
            "v1_report_static_validation": (adversarial_report.get("G_top1_threshold_semantics") or {}).get("validation_train_static_threshold"),
            "v1_report_static_final_test": (adversarial_report.get("G_top1_threshold_semantics") or {}).get("holdout_train_static_threshold"),
            "parity_status": "FROZEN_SCORE_RECONSTRUCTED_FROM_V1_CANDIDATE_TRAIN",
        },
        "D_score_distribution": {
            "full_universe_by_split": score_distribution_full,
            "original_71k_candidate_population": score_distribution_candidate,
        },
        "E_static_threshold_frequency": {
            "raw_threshold_crossings": len(selected_outcomes),
            "unique_mint_threshold_crossings": len({r["mint"] for r in selected_outcomes}),
            "one_trade_per_mint_opportunities": len(one_per),
            "raw_crossings_per_hour": len(selected_outcomes) / manifest_out["span_hours"] if manifest_out.get("span_hours") else None,
            "unique_mints_per_hour": len({r["mint"] for r in selected_outcomes}) / manifest_out["span_hours"] if manifest_out.get("span_hours") else None,
            "one_per_mint_per_hour": len(one_per) / manifest_out["span_hours"] if manifest_out.get("span_hours") else None,
            "model_a_historical_signals_per_hour": 2.28,
        },
        "F_train_economics": metrics(selected_by_split.get("train", [])),
        "G_validation_economics": metrics(selected_by_split.get("validation", [])),
        "H_final_test_economics": metrics(selected_by_split.get("final_test", [])),
        "I_one_trade_per_mint_economics": {split: metrics(rows) for split, rows in one_by_split.items()},
        "J_atlas_vs_nonatlas": atlas_vs_nonatlas,
        "K_fast100_first_impulse_diagnostics": fast_diagnostics,
        "L_near_miss_economics": {split: near_miss(rows) for split, rows in selected_by_split.items()},
        "M_monotonicity": monotonicity,
        "N_entry_stress": entry_stress,
        "O_first_sellable_sensitivity": sellable_sensitivity,
        "P_outlier_robustness": {split: metrics(rows) for split, rows in one_by_split.items()},
        "Q_temporal_stability": temporal,
        "R_concurrency": concurrency_report,
        "S_comparison_with_model_a": {
            "model_a_historical": {
                "signals_per_hour": 2.28,
                "wr_pct": 64.83,
                "pf": 2.05,
                "expectancy_pct": 6.59,
                "plus5_pf": 1.44,
            },
            "v2_static_threshold_one_per_mint_validation": metrics(one_by_split.get("validation", [])),
            "v2_static_threshold_one_per_mint_final_test": metrics(one_by_split.get("final_test", [])),
            "warning": "Model A prospective outcomes were not used or tuned in this task.",
        },
        "T_preselection_causal_verdict": {},
        "U_blockers": [],
        "V_final_classification": None,
        "outputs": {
            "frozen_score_spec": os.path.join(args.out_dir, "frozen_score_spec.json"),
            "full_universe_manifest": os.path.join(args.out_dir, "full_universe_manifest.json"),
            "full_universe_scores": final_scores,
            "selected_trades": selected_path,
            "atlas_vs_nonatlas": os.path.join(args.out_dir, "atlas_vs_nonatlas.json"),
            "score_monotonicity": os.path.join(args.out_dir, "score_monotonicity.json"),
            "entry_stress": os.path.join(args.out_dir, "entry_stress.json"),
            "sellable_sensitivity": os.path.join(args.out_dir, "sellable_sensitivity.json"),
            "concurrency": os.path.join(args.out_dir, "concurrency.json"),
            "temporal_stability": os.path.join(args.out_dir, "temporal_stability.json"),
            "final_report": os.path.join(args.out_dir, "final_report.json"),
        },
        "runtime_sec": time.time() - started,
    }

    val = final_report["I_one_trade_per_mint_economics"].get("validation", {})
    final = final_report["I_one_trade_per_mint_economics"].get("final_test", {})
    nonatlas_final = atlas_vs_nonatlas.get("final_test", {}).get("FULL_UNIVERSE_NON_ATLAS", {})
    atlas_final = atlas_vs_nonatlas.get("final_test", {}).get("ORIGINAL_ATLAS_CANDIDATE", {})
    if manifest_out["population_classification"] != "FULL_ELIGIBLE_DECISION_STATE_UNIVERSE":
        classification = "FULL_UNIVERSE_REPLAY_DATA_BLOCKER"
        final_report["U_blockers"].append("snapshot count did not reach expected approximately 1.07M")
    elif (val.get("pf") or 0) > 2 and (final.get("pf") or 0) > 2 and (nonatlas_final.get("pf") or 0) > 1.2:
        classification = "FULL_UNIVERSE_STATIC_RANKING_ALPHA_STRONG"
    elif (val.get("pf") or 0) > 1.2 and (final.get("pf") or 0) > 1.2:
        classification = "FULL_UNIVERSE_STATIC_RANKING_ALPHA_PROMISING"
    elif (atlas_final.get("pf") or 0) > 2 and (nonatlas_final.get("pf") or 0) <= 1.1:
        classification = "RANKING_ALPHA_DEPENDS_ON_ATLAS_PRESELECTION"
    else:
        classification = "FULL_UNIVERSE_STATIC_THRESHOLD_FAILS"
    final_report["V_final_classification"] = classification
    final_report["T_preselection_causal_verdict"] = {
        "A_atlas_preselection": atlas_vs_nonatlas,
        "B_frozen_rank_score": {split: metrics(rows) for split, rows in selected_by_split.items()},
        "C_interaction": "Compare ORIGINAL_ATLAS_CANDIDATE vs FULL_UNIVERSE_NON_ATLAS. If Atlas subset dominates and non-Atlas collapses, edge depends on interaction/preselection.",
        "D_genuine_full_universe_extreme_tail": classification in ("FULL_UNIVERSE_STATIC_RANKING_ALPHA_STRONG", "FULL_UNIVERSE_STATIC_RANKING_ALPHA_PROMISING"),
    }

    write_json(os.path.join(args.out_dir, "final_report.json"), final_report)
    print(json.dumps(compact({
        "classification": classification,
        "full_universe_rows": manifest_out["scoreable_snapshots"],
        "selected_threshold_crossings": len(selected_outcomes),
        "one_per_mint": len(one_per),
        "validation_one_per": val,
        "final_test_one_per": final,
        "atlas_vs_nonatlas_final": atlas_vs_nonatlas.get("final_test"),
        "report": os.path.join(args.out_dir, "final_report.json"),
        "runtime_sec": final_report["runtime_sec"],
    }), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
