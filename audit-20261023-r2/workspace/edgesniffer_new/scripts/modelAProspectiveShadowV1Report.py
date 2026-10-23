#!/usr/bin/env python3
import argparse
import gzip
import json
import math
import os
from collections import Counter


PRIMARY = "MODEL_A_1S_TOP_0P25_PROSPECTIVE_V1"
SECONDARY = "MODEL_A_1S_TOP_0P50_PROSPECTIVE_V1"
HOLD_MS = 120_000
HISTORICAL = {
    PRIMARY: {"n": 290, "wr_pct": 64.827586, "pf": 2.049009, "expectancy_pct": 6.586641, "max_open1_pf": 2.092026, "stress5_pf": 1.436747, "holdout_pf": 1.724},
    SECONDARY: {"n": 579, "wr_pct": 60.967, "pf": 1.739, "expectancy_pct": 5.125, "max_open1_pf": 1.747, "stress5_pf": 1.157, "holdout_pf": 1.631},
}


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def pct(n, d):
    return (n * 100.0 / d) if d else 0.0


def median(values):
    xs = sorted(v for v in values if finite(v))
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def quantile(values, q):
    xs = sorted(v for v in values if finite(v))
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, int((len(xs) - 1) * q)))]


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


def read_jsonl(path):
    if not os.path.exists(path):
        return
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except EOFError:
        # Live gzip files do not have an end-of-stream marker until the daemon
        # closes them. Keep report generation usable while the cohort is active.
        return


def summarize(rows, outcome_key="normal"):
    vals = []
    outcomes = Counter()
    exit_ms = []
    for row in rows:
        obj = row.get(outcome_key) or {}
        pnl = obj.get("pnl")
        if finite(pnl):
            vals.append(float(pnl))
        outcomes[obj.get("outcome") or "unknown"] += 1
        if finite(obj.get("exit_ms")):
            exit_ms.append(float(obj["exit_ms"]))
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gp = sum(wins)
    gl = -sum(losses)
    pf = gp / gl if gl else (999.0 if gp > 0 else 0.0)
    return {
        "n": len(rows),
        "resolved": len(vals),
        "wr_pct": pct(len(wins), len(vals)),
        "pf": pf,
        "expectancy_pct": sum(vals) / len(vals) if vals else None,
        "median_return_pct": median(vals),
        "normalized_pnl_at_1sol": sum(vals) / 100.0,
        "tp": outcomes.get("TP", 0),
        "sl": outcomes.get("SL", 0),
        "max_hold": outcomes.get("max_hold", 0),
        "no_fill": outcomes.get("no_fill", 0),
        "no_post_sellable_mark": outcomes.get("no_post_sellable_mark", 0),
        "median_exit_ms": median(exit_ms),
    }


def max_open1(rows):
    taken = []
    blocked = []
    open_until = -1
    for row in sorted(rows, key=lambda r: r.get("snapshotTimestampMs") or 0):
        ts = row.get("snapshotTimestampMs") or 0
        if ts < open_until:
            blocked.append(row)
            continue
        taken.append(row)
        exit_ms = ((row.get("normal") or {}).get("exit_ms") or HOLD_MS)
        open_until = ts + exit_ms
    out = summarize(taken)
    out["taken"] = len(taken)
    out["blocked"] = len(blocked)
    return out


def trigger_span_hours(rows):
    ts = [r.get("snapshotTimestampMs") for r in rows if finite(r.get("snapshotTimestampMs"))]
    if len(ts) < 2:
        return None
    return (max(ts) - min(ts)) / 3_600_000


def checkpoint_summaries(rows):
    out = {}
    ordered = sorted(rows, key=lambda r: r.get("snapshotTimestampMs") or 0)
    for n in (25, 50, 100, 200):
        if len(ordered) >= n:
            subset = ordered[:n]
            out[str(n)] = {
                "normal": summarize(subset),
                "max_open1": max_open1(subset),
                "stress5": summarize(subset, "entryStress5"),
                "signals_per_hour": n / max(0.001, trigger_span_hours(subset) or 0.001),
            }
    return out


def feature_drift(scores):
    features = [
        "mc_usd",
        "token_age_ms",
        "first_buyer_quote_share_bps",
        "w100.gross_buy_sol",
        "w500.gross_buy_sol",
        "w500.median_interbuy_ms",
        "w1000.gross_buy_sol",
        "w2000.gross_buy_sol",
        "w2000.top1_share",
    ]
    out = {}
    for feature in features:
        values = [(row.get("features") or {}).get(feature) for row in scores]
        out[feature] = {
            "coverage": sum(1 for v in values if finite(v)),
            "median": median(values),
            "p25": quantile(values, 0.25),
            "p75": quantile(values, 0.75),
            "p90": quantile(values, 0.90),
        }
    return out


def classify(primary_rows, primary_summary):
    n = primary_summary["resolved"]
    pf = primary_summary["pf"] or 0
    exp = primary_summary["expectancy_pct"] or 0
    if n < 25:
        return "MODEL_A_PROSPECTIVE_PROMISING_NEEDS_MORE_N"
    if n >= 100 and pf >= 1.5 and exp > 3:
        return "MODEL_A_PROSPECTIVE_STRONG_PASS"
    if n >= 50 and pf < 1.0:
        return "MODEL_A_PROSPECTIVE_FAIL"
    return "MODEL_A_PROSPECTIVE_PROMISING_NEEDS_MORE_N"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    thresholds_path = os.path.join(args.out_dir, "frozen_thresholds.json")
    parity_path = os.path.join(args.out_dir, "scorer_parity_report.json")
    thresholds = json.load(open(thresholds_path, "r", encoding="utf-8")) if os.path.exists(thresholds_path) else {}
    parity = json.load(open(parity_path, "r", encoding="utf-8")) if os.path.exists(parity_path) else {}
    score_rows = list(read_jsonl(os.path.join(args.out_dir, "all_1s_scores.jsonl.gz")) or [])
    outcome_rows = list(read_jsonl(os.path.join(args.out_dir, "executable_outcomes.jsonl")) or [])
    by_lane = {
        PRIMARY: [r for r in outcome_rows if r.get("lane") == PRIMARY],
        SECONDARY: [r for r in outcome_rows if r.get("lane") == SECONDARY],
    }
    score_values = [r.get("modelScore") for r in score_rows]
    span_ts = [r.get("snapshotTimestampMs") for r in score_rows if finite(r.get("snapshotTimestampMs"))]
    span_hours = ((max(span_ts) - min(span_ts)) / 3_600_000) if len(span_ts) > 1 else None
    full = {
        "scored_1s": len(score_rows),
        "span_hours": span_hours,
        "score_distribution": {
            "min": quantile(score_values, 0),
            "p50": quantile(score_values, 0.5),
            "p90": quantile(score_values, 0.9),
            "p95": quantile(score_values, 0.95),
            "p99": quantile(score_values, 0.99),
            "p995": quantile(score_values, 0.995),
            "p9975": quantile(score_values, 0.9975),
            "max": quantile(score_values, 1),
        },
        "trigger_rate": {
            "top0p25_count": sum(1 for r in score_rows if r.get("passesTop0p25")),
            "top0p25_pct": pct(sum(1 for r in score_rows if r.get("passesTop0p25")), len(score_rows)),
            "top0p50_count": sum(1 for r in score_rows if r.get("passesTop0p50")),
            "top0p50_pct": pct(sum(1 for r in score_rows if r.get("passesTop0p50")), len(score_rows)),
        },
        "feature_distribution_drift_inputs": feature_drift(score_rows),
    }
    lanes = {}
    for lane, rows in by_lane.items():
        hrs = trigger_span_hours(rows)
        lanes[lane] = {
            "normal": summarize(rows),
            "first_sellable_p50": summarize(rows, "p50FirstSellable"),
            "first_sellable_p90": summarize(rows, "p90FirstSellable"),
            "entry_stress_5": summarize(rows, "entryStress5"),
            "entry_stress_10": summarize(rows, "entryStress10"),
            "max_open1": max_open1(rows),
            "signals_per_hour": len(rows) / max(0.001, hrs or 0.001),
            "checkpoints": checkpoint_summaries(rows),
            "historical_benchmark": HISTORICAL.get(lane),
        }
    report = {
        "model_freeze": json.load(open(os.path.join(args.out_dir, "frozen_model_spec.json"), "r", encoding="utf-8")) if os.path.exists(os.path.join(args.out_dir, "frozen_model_spec.json")) else None,
        "score_thresholds": thresholds,
        "historical_scorer_parity": parity,
        "prospective_data_coverage": full,
        "lanes": lanes,
        "final_classification": classify(by_lane[PRIMARY], lanes[PRIMARY]["normal"]) if parity.get("status") == "PASS" else "MODEL_SCORE_PARITY_BLOCKER",
        "notes": [
            "Read-only/no-submit prospective validation.",
            "Thresholds are absolute historical score cutoffs and are not recomputed from prospective rows.",
            "Primary portfolio interpretation is MAX_OPEN1 after enough resolved triggers exist.",
        ],
    }
    if args.write:
        with open(os.path.join(args.out_dir, "final_report.json"), "w", encoding="utf-8") as out:
            json.dump(compact(report), out, indent=2, sort_keys=True)
        with open(os.path.join(args.out_dir, "drift_report.json"), "w", encoding="utf-8") as out:
            json.dump(compact({
                "score_thresholds": thresholds,
                "prospective_data_coverage": full,
                "historical_score_distribution": (report.get("model_freeze") or {}).get("score_range_historical_1s"),
                "feature_distribution_drift_inputs": full["feature_distribution_drift_inputs"],
                "note": "Thresholds remain frozen; this file is descriptive drift monitoring only.",
            }), out, indent=2, sort_keys=True)
    print(json.dumps(compact(report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
