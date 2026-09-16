#!/usr/bin/env python3
import argparse
import gzip
import importlib.util
import json
import math
import os
import time
from collections import defaultdict


STATIC_THRESHOLD = 0.590596
ENTRY_DELAY_MS = 25
SELLABLE_P75_MS = 1476
SELLABLE_P90_MS = 1989
HOLD_MS = 120_000


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


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


def metrics(rows):
    vals = [r["pnl"] for r in rows if finite(r.get("pnl"))]
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    return {
        "n": len(vals),
        "wins": len(wins),
        "losses": len(losses),
        "wr_pct": len(wins) * 100.0 / len(vals) if vals else None,
        "pf": gp / gl if gl else (float("inf") if gp else None),
        "expectancy_pct": sum(vals) / len(vals) if vals else None,
        "median_return_pct": q(vals, 0.5),
        "pnl_at_1sol": sum(vals) / 100.0,
    }


def read_jsonl_gz(path):
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def load_scored_rows(scores_path):
    rows = []
    ranges = {}
    split_train = []
    for row in read_jsonl_gz(scores_path):
        item = {
            "mint": row["mint"],
            "ts": int(row["ts"]),
            "snapshot_kind": row.get("snapshot_kind"),
            "split": row.get("split"),
            "rank_score": row.get("rank_score"),
            "is_original_atlas_candidate": bool(row.get("is_original_atlas_candidate")),
            "mc_usd": row.get("mc_usd"),
        }
        rows.append(item)
        if item["split"] == "train":
            split_train.append(item)
        start, end = ranges.get(item["mint"], (item["ts"], item["ts"]))
        ranges[item["mint"]] = (min(start, item["ts"]), max(end, item["ts"]))
    return rows, split_train, {m: (a, b + HOLD_MS + SELLABLE_P90_MS + 1000) for m, (a, b) in ranges.items()}


def collect_marks(atlas, capsules, ranges):
    marks = defaultdict(list)
    rows_scanned = marks_used = 0
    started = time.time()
    for raw in atlas.read_jsonl(capsules):
        rows_scanned += 1
        mint = atlas.mint_of(raw)
        if mint not in ranges:
            continue
        ts = atlas.ts_of(raw)
        mc = atlas.mc_of(raw)
        if finite(ts) and finite(mc):
            ts = int(ts)
            start, end = ranges[mint]
            if start <= ts <= end and 0 < float(mc) <= atlas.MAX_MC_USD:
                marks[mint].append((ts, float(mc)))
                marks_used += 1
        if rows_scanned % 2_000_000 == 0:
            print(json.dumps(compact({
                "progress": "supplement_marks",
                "rows_scanned": rows_scanned,
                "marks_used": marks_used,
                "elapsed_sec": time.time() - started,
            })), flush=True)
    for arr in marks.values():
        arr.sort()
    return marks, {"rows_scanned": rows_scanned, "marks_used": marks_used, "mints": len(ranges)}


def train_bands(train_rows):
    scores = [r["rank_score"] for r in train_rows if finite(r.get("rank_score"))]
    points = [
        ("min", q(scores, 0.00)),
        ("p50", q(scores, 0.50)),
        ("p75", q(scores, 0.75)),
        ("p90", q(scores, 0.90)),
        ("p95", q(scores, 0.95)),
        ("p98", q(scores, 0.98)),
        ("threshold", STATIC_THRESHOLD),
        ("p99_5", q(scores, 0.995)),
        ("max", q(scores, 1.00)),
    ]
    ordered = []
    seen = set()
    for label, value in sorted(points, key=lambda x: -1e99 if x[1] is None else x[1]):
        if value is None:
            continue
        key = round(value, 12)
        if key in seen:
            continue
        seen.add(key)
        ordered.append((label, value))
    return [{"name": f"{ordered[i][0]}_to_{ordered[i+1][0]}", "lo": ordered[i][1], "hi": ordered[i+1][1]} for i in range(len(ordered) - 1)]


def band_name(score, bands):
    for band in bands:
        if finite(score) and score >= band["lo"] and score < band["hi"]:
            return band["name"]
    if bands and finite(score) and score == bands[-1]["hi"]:
        return bands[-1]["name"]
    return "outside"


def grouped_metrics(rows, key_fn):
    groups = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(row)
    return {k: metrics(v) for k, v in sorted(groups.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capsules", required=True)
    ap.add_argument("--scores", required=True)
    ap.add_argument("--atlas-script", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    started = time.time()
    atlas = load_module("atlas_v1", args.atlas_script)
    rows, train_rows, ranges = load_scored_rows(args.scores)
    marks, mark_meta = collect_marks(atlas, args.capsules, ranges)
    bands = train_bands(train_rows)

    out_path = os.path.join(args.out_dir, "full_universe_outcomes.jsonl.gz")
    outcomes = []
    with gzip.open(out_path, "wt", encoding="utf-8") as out:
        for row in rows:
            outcome = atlas.score(row["ts"], row["mc_usd"], marks.get(row["mint"], []), ENTRY_DELAY_MS, SELLABLE_P75_MS, 0)
            item = {
                **row,
                **outcome,
                "score_band": band_name(row.get("rank_score"), bands),
                "near_threshold_band": (
                    "just_below_threshold" if finite(row.get("rank_score")) and STATIC_THRESHOLD - 0.05 <= row["rank_score"] < STATIC_THRESHOLD
                    else "just_above_threshold" if finite(row.get("rank_score")) and STATIC_THRESHOLD <= row["rank_score"] < STATIC_THRESHOLD + 0.05
                    else "high_score_tail_ge_0p65" if finite(row.get("rank_score")) and row["rank_score"] >= 0.65
                    else "other"
                ),
            }
            outcomes.append(item)
            out.write(json.dumps(compact(item), separators=(",", ":")) + "\n")

    by_split = defaultdict(list)
    for row in outcomes:
        by_split[row.get("split")].append(row)
    selected = [r for r in outcomes if finite(r.get("rank_score")) and r["rank_score"] >= STATIC_THRESHOLD]
    selected_by_split = defaultdict(list)
    for row in selected:
        selected_by_split[row.get("split")].append(row)

    monotonicity = {
        "bands_train_defined_from_full_universe_train_scores": bands,
        "full_universe_all_observation_outcome_bands": {
            split: grouped_metrics(vals, lambda r: r["score_band"]) for split, vals in by_split.items()
        },
        "atlas_only_all_observation_outcome_bands": {
            split: grouped_metrics([r for r in vals if r.get("is_original_atlas_candidate")], lambda r: r["score_band"]) for split, vals in by_split.items()
        },
        "full_universe_near_threshold": {
            split: grouped_metrics(vals, lambda r: r["near_threshold_band"]) for split, vals in by_split.items()
        },
        "atlas_only_near_threshold": {
            split: grouped_metrics([r for r in vals if r.get("is_original_atlas_candidate")], lambda r: r["near_threshold_band"]) for split, vals in by_split.items()
        },
        "selected_threshold_by_split_check": {split: metrics(vals) for split, vals in selected_by_split.items()},
        "mark_collection": mark_meta,
        "full_universe_outcomes": out_path,
    }

    mono_path = os.path.join(args.out_dir, "score_monotonicity.json")
    with open(mono_path, "w", encoding="utf-8") as handle:
        json.dump(compact(monotonicity), handle, indent=2, sort_keys=True)

    report = json.load(open(args.report, "r", encoding="utf-8"))
    report["M_monotonicity"] = monotonicity
    report["L_near_miss_economics"] = monotonicity["full_universe_near_threshold"]
    report["outputs"]["full_universe_outcomes"] = out_path
    report["outputs"]["score_monotonicity"] = mono_path
    report["supplement_runtime_sec"] = time.time() - started
    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump(compact(report), handle, indent=2, sort_keys=True)

    print(json.dumps(compact({
        "done": True,
        "rows_outcomed": len(outcomes),
        "selected_check": {split: metrics(vals) for split, vals in selected_by_split.items()},
        "monotonicity": mono_path,
        "outcomes": out_path,
        "runtime_sec": time.time() - started,
    }), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
