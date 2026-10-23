#!/usr/bin/env python3
import argparse
import collections
import hashlib
import json
import math
import os
from collections import defaultdict, deque


BASE = "/var/lib/edgesniffer-teacher-impulse-parity-v1"
CAPSULES = os.path.join(BASE, "candidate-capsules.jsonl")
PRIOR_REPORT = os.path.join(BASE, "research", "corrected-multiscale-fingerprint-v1", "report.json")
OUT_DIR = os.path.join(BASE, "research", "full-universe-top3-flow-validation-v1")

HOLD_MS = 120_000
TP = 20.0
SL = -20.0
ROUND_TRIP_COST_PCT = 2.5
FAST_LATENCIES_MS = [0, 1, 1, 1, 2, 2, 4, 19, 20, 25]
LATENCY_GRID = [0, 2, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60, 75, 100, 250, 500, 1000, 2000]


def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def pct(n, d):
    return n * 100.0 / d if d else 0.0


def q(xs, quant):
    xs = sorted(x for x in xs if finite(x))
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, int((len(xs) - 1) * quant)))]


def median(xs):
    xs = sorted(x for x in xs if finite(x))
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def clean(v):
    if isinstance(v, float):
        if math.isinf(v):
            return "inf"
        return round(v, 6)
    return v


def read_jsonl(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def ts_of(row):
    return (
        row.get("candidate_ready_ts_ms")
        or row.get("decode_complete_ts_ms")
        or row.get("local_receive_ts_ms")
        or (row.get("canonical_state") or {}).get("observed_at_ms")
    )


def mint_of(row):
    return (row.get("candidate") or {}).get("mint") or row.get("mint") or (row.get("raw_record") or {}).get("mint")


def mc_of(row):
    return (row.get("canonical_state") or {}).get("market_cap_usd") or (row.get("candidate") or {}).get("market_cap_usd")


def quote_sol(row):
    rr = row.get("raw_record") or {}
    if finite(rr.get("quoteLamports")):
        return float(rr["quoteLamports"]) / 1_000_000_000
    c = row.get("candidate") or {}
    return float(c.get("net_buy_sol_60") or 0.0)


def sell_quote_sol(row):
    rr = row.get("raw_record") or {}
    if finite(rr.get("sellQuoteLamports")):
        return float(rr["sellQuoteLamports"]) / 1_000_000_000
    return 0.0


def stable_latency_ms(mint):
    h = hashlib.sha256(mint.encode()).hexdigest()
    return FAST_LATENCIES_MS[int(h[:8], 16) % len(FAST_LATENCIES_MS)]


def load_empirical_latencies(path):
    if not path or not os.path.exists(path):
        return []
    latencies = []
    for row in read_jsonl(path):
        latency = (row.get("latencyComponentsMs") or {}).get("ruleTruthToWouldSendTotal")
        if finite(latency) and latency >= 0:
            latencies.append(int(latency))
    return latencies


def empirical_latency_ms(mint, latencies):
    if not latencies:
        return stable_latency_ms(mint)
    h = hashlib.sha256(("empirical:" + mint).encode()).hexdigest()
    return latencies[int(h[:8], 16) % len(latencies)]


def eval_score(entry_ts, entry_mc, marks, delay_ms=0, model="clean"):
    if not finite(entry_ts) or not finite(entry_mc) or entry_mc <= 0:
        return {"outcome": "missing", "pnl": None, "exit_ms": None}
    target = entry_ts + delay_ms
    delayed_entry = None
    for t, mc in marks:
        if t >= target:
            delayed_entry = (t, mc)
            break
    if delayed_entry is None:
        return {"outcome": "no_fill", "pnl": -ROUND_TRIP_COST_PCT, "exit_ms": None}
    e_ts, e_mc = delayed_entry
    first_tp = None
    first_sl = None
    terminal = None
    max_roi = -math.inf
    min_roi = math.inf
    tp_durations = []
    in_tp = None
    last_t = None
    for t, mc in marks:
        if t < e_ts or t > e_ts + HOLD_MS:
            continue
        roi = ((mc / e_mc) - 1.0) * 100.0 - ROUND_TRIP_COST_PCT
        max_roi = max(max_roi, roi)
        min_roi = min(min_roi, roi)
        terminal = (t, roi)
        if roi >= TP and first_tp is None:
            first_tp = (t, roi)
            in_tp = t
        if roi < TP and in_tp is not None:
            tp_durations.append(t - in_tp)
            in_tp = None
        if roi <= SL and first_sl is None:
            first_sl = (t, roi)
        last_t = t
    if in_tp is not None and last_t is not None:
        tp_durations.append(last_t - in_tp)
    if first_tp and (not first_sl or first_tp[0] <= first_sl[0]):
        pnl = TP
        if model == "sellfix_expected":
            pnl -= 3.0
        return {
            "outcome": "TP",
            "pnl": pnl,
            "exit_ms": first_tp[0] - e_ts,
            "max_roi": max_roi,
            "min_roi": min_roi,
            "first_tp_ms": first_tp[0] - e_ts,
            "tp_duration_ms": sum(tp_durations),
        }
    if first_sl:
        return {
            "outcome": "SL",
            "pnl": SL,
            "exit_ms": first_sl[0] - e_ts,
            "max_roi": max_roi,
            "min_roi": min_roi,
            "first_tp_ms": None,
            "tp_duration_ms": sum(tp_durations),
        }
    pnl = terminal[1] if terminal else -ROUND_TRIP_COST_PCT
    pnl = max(-100.0, min(100.0, pnl))
    return {
        "outcome": "max_hold",
        "pnl": pnl,
        "exit_ms": terminal[0] - e_ts if terminal else HOLD_MS,
        "max_roi": max_roi if finite(max_roi) else None,
        "min_roi": min_roi if finite(min_roi) else None,
        "first_tp_ms": None,
        "tp_duration_ms": sum(tp_durations),
    }


def summarize(rows):
    pnls = [r["pnl"] for r in rows if finite(r.get("pnl"))]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    gp = sum(wins)
    gl = -sum(losses)
    return {
        "n": len(rows),
        "resolved": len(pnls),
        "wr": pct(len(wins), len(pnls)),
        "pf": gp / gl if gl else ("inf" if gp > 0 else 0.0),
        "expectancy_pct": sum(pnls) / len(pnls) if pnls else None,
        "normalized_pnl_sol_at_1sol": sum(pnls) / 100.0,
        "tp": sum(1 for r in rows if r.get("outcome") == "TP"),
        "hard_stop": sum(1 for r in rows if r.get("outcome") == "SL"),
        "max_hold": sum(1 for r in rows if r.get("outcome") == "max_hold"),
        "median_exit_ms": median(r.get("exit_ms") for r in rows),
        "p75_exit_ms": q((r.get("exit_ms") for r in rows), 0.75),
    }


def first_pass_find_qualifiers(limit_rows=None, start_ts_ms=None, end_ts_ms=None):
    flows = defaultdict(deque)
    qualified = {
        "grossbuy_1s_ge5": {},
        "grossbuy_2s_ge5": {},
        "bc50_ge2_grossbuy5s_ge5": {},
    }
    stats = {"rows": 0, "buy_rows": 0, "sell_rows": 0, "sell_quote_missing": 0}
    for row in read_jsonl(CAPSULES):
        stats["rows"] += 1
        if limit_rows and stats["rows"] > limit_rows:
            break
        mint = mint_of(row)
        ts = ts_of(row)
        mc = mc_of(row)
        if not mint or not finite(ts) or not finite(mc):
            continue
        rr = row.get("raw_record") or {}
        side = rr.get("side")
        amount = 0.0
        if side == "buy":
            amount = quote_sol(row)
            stats["buy_rows"] += 1
        elif side == "sell":
            amount = -sell_quote_sol(row)
            stats["sell_rows"] += 1
            if amount == 0:
                stats["sell_quote_missing"] += 1
        else:
            continue
        dq = flows[mint]
        dq.append((int(ts), amount, side, rr.get("wallet")))
        cutoff = int(ts) - 120_000
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        net_sums = {}
        gross_buy_sums = {}
        counts = {}
        for win in [1000, 2000, 5000, 50]:
            start = int(ts) - win
            vals = [x for x in dq if x[0] >= start]
            net_sums[win] = sum(x[1] for x in vals)
            gross_buy_sums[win] = sum(x[1] for x in vals if x[2] == "buy" and x[1] > 0)
            counts[win] = sum(1 for x in vals if x[2] == "buy")
        features = {
            "net_buy_sol_1s": net_sums[1000],
            "net_buy_sol_2s": net_sums[2000],
            "net_buy_sol_5s": net_sums[5000],
            "gross_buy_sol_1s": gross_buy_sums[1000],
            "gross_buy_sol_2s": gross_buy_sums[2000],
            "gross_buy_sol_5s": gross_buy_sums[5000],
            "buy_count_50ms": counts[50],
            "mc": mc,
            "side": side,
            "source_slot": row.get("source_slot"),
            "candidate_ready_ts_ms": ts,
            "source": row.get("source"),
            "token_program": (row.get("candidate") or {}).get("token_program"),
            "has_source_ix_accounts": bool((row.get("extra_decision_fields") or {}).get("sourceIxAccounts")),
            "has_source_ix_data": bool((row.get("candidate") or {}).get("source_ix_data_base64")),
        }
        in_range = (
            (start_ts_ms is None or int(ts) >= start_ts_ms)
            and (end_ts_ms is None or int(ts) <= end_ts_ms)
        )
        if not in_range:
            continue
        if gross_buy_sums[1000] >= 5 and mint not in qualified["grossbuy_1s_ge5"]:
            qualified["grossbuy_1s_ge5"][mint] = features.copy()
        if gross_buy_sums[2000] >= 5 and mint not in qualified["grossbuy_2s_ge5"]:
            qualified["grossbuy_2s_ge5"][mint] = features.copy()
        if counts[50] >= 2 and gross_buy_sums[5000] >= 5 and mint not in qualified["bc50_ge2_grossbuy5s_ge5"]:
            qualified["bc50_ge2_grossbuy5s_ge5"][mint] = features.copy()
    return qualified, stats


def second_pass_marks(qualified, limit_rows=None):
    wanted = {}
    for rule, by_mint in qualified.items():
        for mint, qrow in by_mint.items():
            wanted[mint] = min(wanted.get(mint, qrow["candidate_ready_ts_ms"]), qrow["candidate_ready_ts_ms"])
    marks = defaultdict(list)
    rows = 0
    for row in read_jsonl(CAPSULES):
        rows += 1
        if limit_rows and rows > limit_rows:
            break
        mint = mint_of(row)
        if mint not in wanted:
            continue
        ts = ts_of(row)
        mc = mc_of(row)
        if finite(ts) and finite(mc) and ts >= wanted[mint] and ts <= wanted[mint] + HOLD_MS + 1000:
            marks[mint].append((int(ts), float(mc)))
    for arr in marks.values():
        arr.sort()
    return marks


def prior_counts():
    if not os.path.exists(PRIOR_REPORT):
        return {}
    r = json.load(open(PRIOR_REPORT, "r", encoding="utf-8"))
    out = {}
    for row in r.get("top20", []) + r.get("benchmarks", {}).get("special", []):
        out[row.get("rule")] = row.get("n")
    return out


def evaluate_rule(rule, by_mint, marks):
    out = {}
    for model_name, delay_fn, sell_model in [
        ("executable_zero_delay", lambda m: 0, "clean"),
        ("fast_infra_realistic", stable_latency_ms, "clean"),
        ("sell_fix_v1_1_expected", stable_latency_ms, "sellfix_expected"),
    ]:
        rows = []
        for mint, qrow in by_mint.items():
            score = eval_score(qrow["candidate_ready_ts_ms"], qrow["mc"], marks.get(mint, []), delay_fn(mint), sell_model)
            rows.append({"mint": mint, **score, "ts": qrow["candidate_ready_ts_ms"]})
        out[model_name] = summarize(rows)
        out[model_name]["rows"] = rows
    return out


def evaluate_empirical(by_mint, marks, latency_path):
    latencies = load_empirical_latencies(latency_path)
    rows = []
    for mint, qrow in by_mint.items():
        score = eval_score(
            qrow["candidate_ready_ts_ms"],
            qrow["mc"],
            marks.get(mint, []),
            empirical_latency_ms(mint, latencies),
            "clean",
        )
        rows.append({"mint": mint, **score, "ts": qrow["candidate_ready_ts_ms"]})
    s = summarize(rows)
    s["latency_sample_count"] = len(latencies)
    s["latency_source"] = latency_path
    s["rows"] = rows
    return s


def walk_forward(rows):
    xs = sorted(rows, key=lambda r: r["ts"])
    folds = []
    for i in range(4):
        a = len(xs) * i // 4
        b = len(xs) * (i + 1) // 4
        s = summarize(xs[a:b])
        s["fold"] = i + 1
        folds.append(s)
    return folds


def recent(rows, n):
    xs = sorted(rows, key=lambda r: r["ts"])[-n:]
    return summarize(xs)


def outlier(rows):
    xs = sorted([r for r in rows if finite(r.get("pnl"))], key=lambda r: r["pnl"], reverse=True)
    normal = summarize(xs)
    ex1 = summarize(xs[1:])
    ex3 = summarize(xs[3:])
    total = normal["normalized_pnl_sol_at_1sol"]
    top1 = (xs[0]["pnl"] / 100.0) if xs else 0
    top3 = sum(r["pnl"] for r in xs[:3]) / 100.0
    return {
        "all": normal,
        "exclude_top_1": ex1,
        "exclude_top_3": ex3,
        "top1_contribution_pct": pct(top1, total) if total else None,
        "top3_contribution_pct": pct(top3, total) if total else None,
    }


def durability(rows):
    winners = [r for r in rows if r.get("outcome") == "TP"]
    return {
        "winners": len(winners),
        "tp_persist_ge_250ms": sum(1 for r in winners if finite(r.get("tp_duration_ms")) and r["tp_duration_ms"] >= 250),
        "tp_persist_ge_500ms": sum(1 for r in winners if finite(r.get("tp_duration_ms")) and r["tp_duration_ms"] >= 500),
        "tp_persist_ge_1000ms": sum(1 for r in winners if finite(r.get("tp_duration_ms")) and r["tp_duration_ms"] >= 1000),
        "tp_persist_ge_2000ms": sum(1 for r in winners if finite(r.get("tp_duration_ms")) and r["tp_duration_ms"] >= 2000),
        "median_first_tp_ms": median(r.get("first_tp_ms") for r in winners),
        "median_tp_duration_ms": median(r.get("tp_duration_ms") for r in winners),
        "p75_tp_duration_ms": q((r.get("tp_duration_ms") for r in winners), 0.75),
    }


def latency_sensitivity(by_mint, marks):
    out = []
    for delay in LATENCY_GRID:
        rows = []
        for mint, qrow in by_mint.items():
            score = eval_score(qrow["candidate_ready_ts_ms"], qrow["mc"], marks.get(mint, []), delay, "clean")
            rows.append({"mint": mint, **score, "ts": qrow["candidate_ready_ts_ms"]})
        s = summarize(rows)
        s["delay_ms"] = delay
        out.append(s)
    return out


def capacity(rows, max_open):
    xs = sorted(rows, key=lambda r: r["ts"])
    open_until = []
    taken = []
    blocked = []
    for r in xs:
        open_until = [x for x in open_until if x > r["ts"]]
        if len(open_until) >= max_open:
            blocked.append(r)
            continue
        open_until.append(r["ts"] + (r.get("exit_ms") or HOLD_MS))
        taken.append(r)
    s = summarize(taken)
    s["max_open"] = max_open
    s["taken"] = len(taken)
    s["blocked"] = len(blocked)
    s["blocked_winners"] = sum(1 for r in blocked if r.get("pnl", 0) > 0)
    s["blocked_losers"] = sum(1 for r in blocked if r.get("pnl", 0) < 0)
    return s


def opportunity_rate(by_mint):
    ts = sorted(q["candidate_ready_ts_ms"] for q in by_mint.values())
    if not ts:
        return {"per_hour": 0, "per_day": 0, "span_hours": 0}
    span_h = max((ts[-1] - ts[0]) / 3_600_000, 1 / 3600)
    return {"per_hour": len(ts) / span_h, "per_day": len(ts) / span_h * 24, "span_hours": span_h}


def overlap(qualified):
    sets = {k: set(v.keys()) for k, v in qualified.items()}
    names = list(sets)
    counts = {}
    all_mints = set().union(*sets.values()) if sets else set()
    for mint in all_mints:
        key = "+".join(name for name in names if mint in sets[name])
        counts[key] = counts.get(key, 0) + 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-rows", type=int, default=0)
    ap.add_argument("--empirical-latency-jsonl", default="")
    ap.add_argument("--start-ts-ms", type=int, default=0)
    ap.add_argument("--end-ts-ms", type=int, default=0)
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    limit = args.limit_rows or None
    qualified, scan_stats = first_pass_find_qualifiers(
        limit,
        args.start_ts_ms or None,
        args.end_ts_ms or None,
    )
    marks = second_pass_marks(qualified, limit)
    prior = prior_counts()
    results = {
        "methodology": {
            "universe": "Full candidate-capsules stream; no old lane qualification required.",
            "qualification": "Earliest point-in-time candidate-capsule observation where each frozen rule first becomes true per mint.",
            "net_flow": "Trailing window ending at candidate_ready_ts_ms; buys use raw_record.quoteLamports; sells subtract raw_record.sellQuoteLamports when present. Missing sellQuoteLamports are counted and currently contribute 0.",
            "duplicate_behavior": "Repeated same-wallet trades are included as separate flow events; each mint qualifies once at earliest rule truth.",
            "future_exclusion": "Only flow events at or before qualification timestamp enter rule features.",
            "outcome_model": "MC mark proxy over future candidate-capsule observations; TP +20, SL -20, non-terminal clipped. This is executable-replay approximation, not on-chain fill replay.",
            "zero_delay_model": "EXECUTABLE_ZERO_DELAY uses the same candidate-capsule executable entry and outcome model as delayed rows with executionDelayMs=0. It is not theoretical-shadow entry semantics.",
            "theoretical_shadow": "Kept separate. This report does not label theoretical shadow as 0ms.",
            "qualification_time_range": {
                "startTsMs": args.start_ts_ms or None,
                "endTsMs": args.end_ts_ms or None,
            },
        },
        "scan_stats": scan_stats,
        "prior_scored_counts": prior,
        "rules": {},
        "overlap": overlap(qualified),
    }
    for rule, by_mint in qualified.items():
        evals = evaluate_rule(rule, by_mint, marks)
        empirical = evaluate_empirical(by_mint, marks, args.empirical_latency_jsonl)
        fast_rows = evals["fast_infra_realistic"].pop("rows")
        zero_rows = evals["executable_zero_delay"].pop("rows")
        sellfix_rows = evals["sell_fix_v1_1_expected"].pop("rows")
        empirical_rows = empirical.pop("rows")
        scored_prior = {
            "grossbuy_1s_ge5": prior.get("gross_buy_sol_1s>=5"),
            "grossbuy_2s_ge5": prior.get("gross_buy_sol_2s>=5"),
            "bc50_ge2_grossbuy5s_ge5": prior.get("bc50ms>=2 + gross_buy_sol_5s>=5"),
        }.get(rule)
        full_n = len(by_mint)
        results["rules"][rule] = {
            "qualifiers_full_universe": full_n,
            "prior_conditional_scored_qualifiers": scored_prior,
            "conditional_coverage_pct": pct(scored_prior or 0, full_n) if full_n else None,
            "models": evals,
            "empirical_pipeline_latency_v1": empirical,
            "walk_forward_fast_realistic": walk_forward(fast_rows),
            "recent_fast_realistic": {
                "last_250": recent(fast_rows, 250),
                "last_500": recent(fast_rows, 500),
                "last_1000": recent(fast_rows, 1000),
            },
            "outlier_fast_realistic": outlier(fast_rows),
            "durability_fast_realistic": durability(fast_rows),
            "latency_sensitivity": latency_sensitivity(by_mint, marks),
            "capacity_fast_realistic": [capacity(fast_rows, n) for n in [1, 2, 3]],
            "opportunity_rate": opportunity_rate(by_mint),
            "size_sensitivity_note": "Not performed in this pass; candidate-capsule mark replay lacks full per-size curve impact simulation. Requires executor quote replay from stored reserves.",
        }
        # Keep compact row samples for audit reproducibility without huge files.
        with open(os.path.join(out_dir, f"{rule}-fast-realistic-sample.jsonl"), "w", encoding="utf-8") as f:
            for row in fast_rows[:5000]:
                f.write(json.dumps(row, sort_keys=True) + "\n")
    json_path = os.path.join(out_dir, "report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True, default=clean)
    md_path = os.path.join(out_dir, "report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Full-Universe Top 3 Flow Validation V1\n\n")
        f.write(json.dumps(results["methodology"], indent=2) + "\n\n")
        f.write("| Rule | Full N | Prior N | Coverage | Fast PF | Fast WR | Fast Exp % | Recent500 PF | Per Hour |\n")
        f.write("|---|---:|---:|---:|---:|---:|---:|---:|---:|\n")
        for rule, r in results["rules"].items():
            fast = r["models"]["fast_infra_realistic"]
            rec = r["recent_fast_realistic"]["last_500"]
            f.write(f"| {rule} | {r['qualifiers_full_universe']} | {r['prior_conditional_scored_qualifiers']} | {clean(r['conditional_coverage_pct'])}% | {clean(fast['pf'])} | {clean(fast['wr'])}% | {clean(fast['expectancy_pct'])} | {clean(rec['pf'])} | {clean(r['opportunity_rate']['per_hour'])} |\n")
    print(json.dumps({"json": json_path, "md": md_path, "rules": list(results["rules"].keys())}, indent=2))


if __name__ == "__main__":
    main()
