#!/usr/bin/env python3
import argparse
import gzip
import json
import math
import os
import time
from collections import defaultdict


HOLD_MS = 120_000
ENTRY_DELAY_MS = 25
SELLABLE_P50_MS = 964
SELLABLE_P75_MS = 1476
SELLABLE_P90_MS = 1989
TP = 20.0
SL = -20.0
ROUND_TRIP_COST_PCT = 2.5
MAX_MC_USD = 250_000
TOP_PCTS = [50, 25, 20, 10, 5, 2, 1, 0.5, 0.25]
MODEL_KEYS = [
    "model.A_flow_price_only",
    "model.B_flow_plus_sell_supply",
]


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def pct(n, d):
    return (n * 100.0 / d) if d else 0.0


def median(values):
    xs = sorted(x for x in values if finite(x))
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def quantile(values, q):
    xs = sorted(x for x in values if finite(x))
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


def read_gz_jsonl(path):
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def read_jsonl(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def mint_of(row):
    return (row.get("candidate") or {}).get("mint") or row.get("mint") or (row.get("raw_record") or {}).get("mint")


def ts_of(row):
    return row.get("candidate_ready_ts_ms") or row.get("decode_complete_ts_ms") or row.get("local_receive_ts_ms")


def mc_of(row):
    return (row.get("canonical_state") or {}).get("market_cap_usd") or (row.get("candidate") or {}).get("market_cap_usd")


def score(entry_ts, selected_mc, marks, sellable_ms, entry_stress_pct=0.0):
    if not finite(entry_ts) or not finite(selected_mc) or selected_mc <= 0:
        return {"outcome": "missing", "pnl": None}
    target_entry = entry_ts + ENTRY_DELAY_MS
    entry = None
    for ts, mc in marks:
        if ts >= target_entry:
            entry = (ts, mc * (1.0 + entry_stress_pct / 100.0))
            break
    if entry is None:
        return {"outcome": "no_fill", "pnl": -ROUND_TRIP_COST_PCT, "exit_ms": None}
    entry_ts, entry_mc = entry
    sellable_ts = entry_ts + sellable_ms
    first_tp_before = None
    first_tp_after = None
    first_sl_after = None
    terminal = None
    max_after = -math.inf
    min_after = math.inf
    durations = {20: 0, 15: 0, 10: 0}
    above = {20: False, 15: False, 10: False}
    starts = {20: None, 15: None, 10: None}
    last_ts = None
    ret_at_sellable = None
    for ts, mc in marks:
        if ts < entry_ts or ts > entry_ts + HOLD_MS:
            continue
        roi = ((mc / entry_mc) - 1.0) * 100.0 - ROUND_TRIP_COST_PCT
        if roi >= TP and first_tp_before is None:
            first_tp_before = ts
        if ts < sellable_ts:
            continue
        if ret_at_sellable is None:
            ret_at_sellable = roi
        max_after = max(max_after, roi)
        min_after = min(min_after, roi)
        terminal = (ts, roi)
        if roi >= TP and first_tp_after is None:
            first_tp_after = ts
        if roi <= SL and first_sl_after is None:
            first_sl_after = ts
        for level in (20, 15, 10):
            if roi >= level and not above[level]:
                above[level] = True
                starts[level] = ts
            elif roi < level and above[level]:
                durations[level] += ts - starts[level]
                above[level] = False
        last_ts = ts
    for level in (20, 15, 10):
        if above[level] and last_ts is not None:
            durations[level] += last_ts - starts[level]
    if terminal is None:
        return {
            "outcome": "no_post_sellable_mark",
            "pnl": -ROUND_TRIP_COST_PCT,
            "exit_ms": None,
            "post_sellable_tp_reachable": False,
            "pre_sellable_only_winner": first_tp_before is not None,
            "return_at_first_sellable": None,
            "max_return_after_sellable": None,
            "min_return_after_sellable": None,
            "time_above_20_after_sellable_ms": 0,
        }
    if first_tp_after and (not first_sl_after or first_tp_after <= first_sl_after):
        outcome = "TP"
        pnl = TP - entry_stress_pct
        exit_ts = first_tp_after
    elif first_sl_after:
        outcome = "SL"
        pnl = SL
        exit_ts = first_sl_after
    else:
        outcome = "max_hold"
        pnl = max(-100.0, min(100.0, terminal[1] - entry_stress_pct))
        exit_ts = terminal[0]
    return {
        "outcome": outcome,
        "pnl": pnl,
        "exit_ms": exit_ts - entry_ts,
        "post_sellable_tp_reachable": first_tp_after is not None,
        "pre_sellable_only_winner": first_tp_before is not None and first_tp_after is None,
        "return_at_first_sellable": ret_at_sellable,
        "max_return_after_sellable": max_after if finite(max_after) else None,
        "min_return_after_sellable": min_after if finite(min_after) else None,
        "time_above_20_after_sellable_ms": durations[20],
    }


def summarize(rows, pnl_key="pnl", outcome_key="outcome"):
    pnls = [row[pnl_key] for row in rows if finite(row.get(pnl_key))]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    def pf(values):
        gp = sum(x for x in values if x > 0)
        gl = -sum(x for x in values if x < 0)
        return gp / gl if gl else (999.0 if gp > 0 else 0.0)

    ranked = sorted(pnls, reverse=True)
    return {
        "n": len(rows),
        "resolved": len(pnls),
        "wr_pct": pct(len(wins), len(pnls)),
        "pf": pf(pnls),
        "expectancy_pct": sum(pnls) / len(pnls) if pnls else None,
        "median_return_pct": median(pnls),
        "pnl_at_1sol": sum(pnls) / 100.0,
        "tp": sum(1 for row in rows if row.get(outcome_key) == "TP"),
        "sl": sum(1 for row in rows if row.get(outcome_key) == "SL"),
        "max_hold": sum(1 for row in rows if row.get(outcome_key) == "max_hold"),
        "pf_ex_top1": pf(ranked[1:]) if len(ranked) > 1 else None,
        "pf_ex_top3": pf(ranked[3:]) if len(ranked) > 3 else None,
        "top1_profit_contribution_pct": (ranked[0] / sum(pnls) * 100.0) if ranked and sum(pnls) else None,
        "top3_profit_contribution_pct": (sum(ranked[:3]) / sum(pnls) * 100.0) if len(ranked) >= 3 and sum(pnls) else None,
        "robust_winner_rate_pct": pct(sum(1 for row in rows if row.get("label") == "ROBUST_EXECUTABLE_WINNER"), len(rows)),
        "fragile_winner_rate_pct": pct(sum(1 for row in rows if row.get("label") == "FRAGILE_WINNER"), len(rows)),
        "hard_stop_rate_pct": pct(sum(1 for row in rows if row.get("label") == "HARD_STOP_LOSER"), len(rows)),
        "post_sellable_tp_rate_pct": pct(sum(1 for row in rows if row.get("post_sellable_tp_reachable")), len(rows)),
        "pre_sellable_only_rate_pct": pct(sum(1 for row in rows if row.get("pre_sellable_only_winner")), len(rows)),
        "median_time_above_20_ms": median(row.get("time_above_20_after_sellable_ms") for row in rows),
    }


def max_open1(rows, pnl_key="pnl", outcome_key="outcome"):
    taken = []
    blocked = []
    open_until = -1
    for row in sorted(rows, key=lambda x: x["ts"]):
        if row["ts"] < open_until:
            blocked.append(row)
            continue
        taken.append(row)
        open_until = row["ts"] + (row.get("exit_ms") or HOLD_MS)
    out = summarize(taken, pnl_key, outcome_key)
    out["taken"] = len(taken)
    out["blocked"] = len(blocked)
    return out


def chronological_assign(rows):
    rows.sort(key=lambda x: x["ts"])
    a = len(rows) // 2
    b = len(rows) * 3 // 4
    for idx, row in enumerate(rows):
        row["chrono_split"] = "train" if idx < a else ("validation" if idx < b else "holdout")


def slice_top(rows, key, top_pct):
    xs = [r for r in rows if finite(r.get(key))]
    xs.sort(key=lambda r: r[key], reverse=True)
    n = max(1, int(math.ceil(len(xs) * top_pct / 100.0)))
    return xs[:n]


def slice_bottom(rows, key, bottom_pct):
    xs = [r for r in rows if finite(r.get(key))]
    xs.sort(key=lambda r: r[key])
    n = max(1, int(math.ceil(len(xs) * bottom_pct / 100.0)))
    return xs[:n]


def bucket_report(name, rows, span_hours):
    out = summarize(rows)
    out["signals_per_hour"] = len(rows) / max(0.001, span_hours or 0)
    out["entry_stress_5"] = summarize(rows, "stress5_pnl", "stress5_outcome")
    out["entry_stress_10"] = summarize(rows, "stress10_pnl", "stress10_outcome")
    out["first_sellable_p50"] = summarize(rows, "p50_pnl", "p50_outcome")
    out["first_sellable_p90"] = summarize(rows, "p90_pnl", "p90_outcome")
    out["max_open1"] = max_open1(rows)
    out["chronological"] = {
        split: summarize([r for r in rows if r.get("chrono_split") == split])
        for split in ("train", "validation", "holdout")
    }
    out["label_counts"] = dict(CounterLike(row.get("label") for row in rows))
    out["name"] = name
    return out


def CounterLike(values):
    out = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def decile_reports(rows, model_key, span_hours):
    xs = [r for r in rows if finite(r.get(model_key))]
    xs.sort(key=lambda r: r[model_key])
    out = []
    if not xs:
        return out
    for i in range(10):
        start = len(xs) * i // 10
        end = len(xs) * (i + 1) // 10
        part = xs[start:end]
        out.append({
            "decile_low_to_high": i + 1,
            "score_min": part[0][model_key] if part else None,
            "score_max": part[-1][model_key] if part else None,
            **bucket_report(f"decile_{i+1}", part, span_hours),
        })
    return out


def top_tail_reports(rows, model_key, span_hours):
    out = {"bottom_50": bucket_report("bottom_50", slice_bottom(rows, model_key, 50), span_hours)}
    for p in TOP_PCTS:
        out[f"top_{str(p).replace('.', '_')}pct"] = bucket_report(f"top_{p}pct", slice_top(rows, model_key, p), span_hours)
    return out


def monotonicity(deciles):
    pairs = [(d["decile_low_to_high"], d.get("pf"), d.get("wr_pct"), d.get("expectancy_pct"), d["entry_stress_5"].get("pf")) for d in deciles]
    def corr(idx):
        vals = [(x[0], x[idx]) for x in pairs if finite(x[idx])]
        if len(vals) < 2:
            return None
        xs = [v[0] for v in vals]
        ys = [v[1] for v in vals]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        num = sum((x - mx) * (y - my) for x, y in vals)
        denx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        deny = math.sqrt(sum((y - my) ** 2 for y in ys))
        return num / (denx * deny) if denx and deny else None
    return {
        "decile_vs_pf_corr": corr(1),
        "decile_vs_wr_corr": corr(2),
        "decile_vs_expectancy_corr": corr(3),
        "decile_vs_stress5_pf_corr": corr(4),
        "decile_table": [{"decile": x[0], "pf": x[1], "wr_pct": x[2], "expectancy_pct": x[3], "stress5_pf": x[4]} for x in pairs],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--enriched", required=True)
    parser.add_argument("--capsules", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--limit-rows", type=int, default=0)
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    started = time.time()
    rows = []
    wanted = {}
    for idx, row in enumerate(read_gz_jsonl(args.enriched), 1):
        if args.limit_rows and idx > args.limit_rows:
            break
        if not finite(row.get("ts")) or not finite(row.get("mc")):
            continue
        rows.append(row)
        wanted[row["mint"]] = min(wanted.get(row["mint"], row["ts"]), row["ts"])
    chronological_assign(rows)
    print(json.dumps(compact({"progress": "loaded_enriched", "rows": len(rows), "wanted_mints": len(wanted), "elapsed_sec": time.time() - started})), flush=True)

    marks = defaultdict(list)
    scanned = marks_used = 0
    for row in read_jsonl(args.capsules):
        scanned += 1
        if args.limit_rows and scanned > args.limit_rows:
            break
        mint = mint_of(row)
        if mint not in wanted:
            continue
        ts = ts_of(row)
        mc = mc_of(row)
        if finite(ts) and finite(mc) and wanted[mint] <= int(ts) <= wanted[mint] + HOLD_MS + SELLABLE_P90_MS + 1000 and 0 < float(mc) <= MAX_MC_USD:
            marks[mint].append((int(ts), float(mc)))
            marks_used += 1
        if scanned % 1_000_000 == 0:
            print(json.dumps(compact({"progress": "mark_replay", "rows": scanned, "marks_used": marks_used, "elapsed_sec": time.time() - started})), flush=True)
    for arr in marks.values():
        arr.sort()
    for row in rows:
        m = marks.get(row["mint"], [])
        p50 = score(row["ts"], row["mc"], m, SELLABLE_P50_MS, 0)
        p90 = score(row["ts"], row["mc"], m, SELLABLE_P90_MS, 0)
        row["p50_pnl"] = p50.get("pnl")
        row["p50_outcome"] = p50.get("outcome")
        row["p90_pnl"] = p90.get("pnl")
        row["p90_outcome"] = p90.get("outcome")
    span_hours = ((max(r["ts"] for r in rows) - min(r["ts"] for r in rows)) / 3_600_000) if rows else None
    populations = {
        "combined_1s_2s": rows,
        "snapshot_1s": [r for r in rows if r.get("snapshot_age_ms") == 1000],
        "snapshot_2s": [r for r in rows if r.get("snapshot_age_ms") == 2000],
    }
    report = {
        "model_freeze_verification": {
            "source_enriched": args.enriched,
            "models_audited": MODEL_KEYS,
            "retrained": False,
            "new_features_added": False,
            "labels_changed": False,
            "primary_first_sellable_ms": SELLABLE_P75_MS,
            "entry_delay_ms": ENTRY_DELAY_MS,
            "p50_p90_recomputed_from_raw_marks": True,
        },
        "dataset": {
            "rows": len(rows),
            "unique_mints": len({r["mint"] for r in rows}),
            "span_hours": span_hours,
            "marks_used": marks_used,
            "snapshots_1s": len(populations["snapshot_1s"]),
            "snapshots_2s": len(populations["snapshot_2s"]),
        },
        "models": {},
    }
    for model_key in MODEL_KEYS:
        model_report = {}
        for pop_name, pop_rows in populations.items():
            deciles = decile_reports(pop_rows, model_key, span_hours)
            model_report[pop_name] = {
                "score_distribution": {
                    "min": quantile([r.get(model_key) for r in pop_rows], 0),
                    "p25": quantile([r.get(model_key) for r in pop_rows], 0.25),
                    "median": median(r.get(model_key) for r in pop_rows),
                    "p75": quantile([r.get(model_key) for r in pop_rows], 0.75),
                    "p90": quantile([r.get(model_key) for r in pop_rows], 0.90),
                    "p95": quantile([r.get(model_key) for r in pop_rows], 0.95),
                    "p99": quantile([r.get(model_key) for r in pop_rows], 0.99),
                    "max": quantile([r.get(model_key) for r in pop_rows], 1),
                },
                "deciles": deciles,
                "top_tails": top_tail_reports(pop_rows, model_key, span_hours),
                "monotonicity": monotonicity(deciles),
            }
        report["models"][model_key] = model_report

    def best_bucket():
        candidates = []
        for model_key, mr in report["models"].items():
            for pop_name, pr in mr.items():
                for bucket_name, br in pr["top_tails"].items():
                    if bucket_name == "bottom_50":
                        continue
                    candidates.append({
                        "model": model_key,
                        "population": pop_name,
                        "bucket": bucket_name,
                        "n": br["n"],
                        "signals_per_hour": br["signals_per_hour"],
                        "pf": br["pf"],
                        "expectancy_pct": br["expectancy_pct"],
                        "wr_pct": br["wr_pct"],
                        "stress5_pf": br["entry_stress_5"]["pf"],
                        "stress10_pf": br["entry_stress_10"]["pf"],
                        "max_open1_pf": br["max_open1"]["pf"],
                        "validation_pf": br["chronological"]["validation"]["pf"],
                        "holdout_pf": br["chronological"]["holdout"]["pf"],
                        "pf_ex_top3": br["pf_ex_top3"],
                    })
        candidates.sort(key=lambda x: ((x["max_open1_pf"] or 0), (x["stress5_pf"] or 0), (x["validation_pf"] or 0), (x["holdout_pf"] or 0)), reverse=True)
        return candidates[:20]

    leaderboard = best_bucket()
    report["top_bucket_leaderboard"] = leaderboard
    strong = [
        x for x in leaderboard
        if x["n"] >= 100
        and (x["pf"] or 0) >= 1.4
        and (x["validation_pf"] or 0) >= 1.25
        and (x["holdout_pf"] or 0) >= 1.25
        and (x["stress5_pf"] or 0) > 1.0
        and (x["max_open1_pf"] or 0) > 1.0
    ]
    promising = [
        x for x in leaderboard
        if x["n"] >= 100
        and (x["pf"] or 0) > 1.1
        and (x["max_open1_pf"] or 0) > 1.0
        and (x["stress5_pf"] or 0) > 0.8
    ]
    if strong:
        verdict = "MODEL_SCORE_CONTAINS_STRONG_EXECUTABLE_ALPHA"
    elif promising:
        verdict = "MODEL_SCORE_CONTAINS_PROMISING_TOP_TAIL_ALPHA"
    else:
        # AUC can rank robust labels while still failing payoff/stress economics.
        verdict = "MODEL_DISCRIMINATES_BUT_DOES_NOT_MONETIZE"
    report["final_verdict"] = verdict
    report["outputs"] = {"report": os.path.join(args.out_dir, "report.json")}
    with open(report["outputs"]["report"], "w", encoding="utf-8") as out:
        json.dump(compact(report), out, indent=2)
    print(json.dumps(compact({"done": True, "out_dir": args.out_dir, "dataset": report["dataset"], "top_bucket_leaderboard": leaderboard[:10], "final_verdict": verdict}), separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
