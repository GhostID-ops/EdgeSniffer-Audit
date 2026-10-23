#!/usr/bin/env python3
import argparse
import collections
import json
import math
import os
from bisect import bisect_left


BASE = "/var/lib/edgesniffer-teacher-impulse-parity-v1"
RESEARCH = os.path.join(BASE, "research")
TELEMETRY = os.path.join(RESEARCH, "early-burst-telemetry-v1.jsonl")
OUT_DIR = os.path.join(RESEARCH, "corrected-multiscale-fingerprint-v1")

WINDOWS_S = [1, 2, 5, 10, 15, 30, 60, 90, 120]
FAST_WINDOWS_MS = [25, 50, 75, 100, 150]
NET_THRESHOLDS = [0.25, 0.5, 1, 2, 3, 5]
MC_BANDS = [(0, 3000), (3000, 5000), (5000, 8000), (8000, 12000), (12000, 20000), (20000, math.inf)]


def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def pct(n, d):
    return (n / d * 100.0) if d else 0.0


def quantile(xs, q):
    xs = sorted(x for x in xs if finite(x))
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, int((len(xs) - 1) * q)))]


def median(xs):
    xs = sorted(x for x in xs if finite(x))
    if not xs:
        return None
    mid = len(xs) // 2
    if len(xs) % 2:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2


def summ(rows):
    pnls = [r["pnl"] for r in rows if finite(r.get("pnl"))]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    gp = sum(wins)
    gl = -sum(losses)
    return {
        "n": len(rows),
        "wr": pct(len(wins), len(pnls)),
        "pf": (gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)),
        "expectancy_pct": (sum(pnls) / len(pnls) if pnls else None),
        "fee_aware_sol_at_1sol": sum(pnls) / 100.0,
        "tp": sum(1 for r in rows if str(r.get("outcome")).lower() == "tp"),
        "sl": sum(1 for r in rows if str(r.get("outcome")).lower() == "sl"),
        "max_hold": sum(1 for r in rows if str(r.get("outcome")).lower() in ("max_hold", "none")),
        "median_exit_ms": median([r.get("exit_ms") for r in rows]),
        "p75_exit_ms": quantile([r.get("exit_ms") for r in rows], 0.75),
        "best_pct": max(pnls) if pnls else None,
        "worst_pct": min(pnls) if pnls else None,
    }


def clean_metric(v):
    if v == float("inf"):
        return "inf"
    if isinstance(v, float):
        return round(v, 4)
    return v


def jsonl(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def load_outcomes():
    files = {
        "bc100_ge2_mc5000_prospective_v1": os.path.join(RESEARCH, "bc100_ge2_mc5000_prospective_v1", "outcomes.jsonl"),
        "bc100_ge2_net60_ge_0p5_v1": os.path.join(RESEARCH, "bc100_ge2_net60_ge_0p5_v1", "outcomes.jsonl"),
        "bc100_ge2_net60_ge_1p0_v1": os.path.join(RESEARCH, "bc100_ge2_net60_ge_1p0_v1", "outcomes.jsonl"),
        "bc100_ge2_net60_ge_2p0_v1": os.path.join(RESEARCH, "bc100_ge2_net60_ge_2p0_v1", "outcomes.jsonl"),
    }
    rows = []
    seen = set()
    by_source = collections.Counter()
    for source, path in files.items():
        for o in jsonl(path):
            mint = o.get("mint")
            ts = o.get("qualificationTsMs")
            if not mint or not finite(ts):
                continue
            key = (mint, int(ts))
            if key in seen:
                continue
            seen.add(key)
            raw_pnl = o.get("feeAwarePnlPct")
            outcome = o.get("outcome")
            row = {
                "mint": mint,
                "ts": int(ts),
                "mc": o.get("mcUsd"),
                "entry_mc": o.get("entryMcUsd"),
                "entry_ts": o.get("entryTsMs"),
                "exit_ts": o.get("exitTsMs"),
                "exit_ms": (o.get("exitTsMs") - o.get("entryTsMs")) if finite(o.get("exitTsMs")) and finite(o.get("entryTsMs")) else None,
                "raw_pnl": raw_pnl,
                "pnl": executable_policy_pnl(outcome, raw_pnl),
                "clean_pnl": o.get("cleanExecutablePnlPct"),
                "sellfix_expected_pnl": o.get("currentRealisticSellPnlPct"),
                "outcome": outcome,
                "bc100": o.get("buyCount100ms"),
                "bc250": o.get("buyCount250ms"),
                "bc500": o.get("buyCount500ms"),
                "bc1000": o.get("buyCount1000ms"),
                "fb_bps": o.get("firstBuyerQuoteShareBps"),
                "dom": o.get("dominantBuyShare60"),
                "unique60": o.get("uniqueWallets60"),
                "sell_pressure60": o.get("sellPressure60"),
                "net60_original": o.get("netBuySol60"),
                "source_lane": source,
            }
            rows.append(row)
            by_source[source] += 1
    rows.sort(key=lambda r: r["ts"])
    return rows, by_source


def executable_policy_pnl(outcome, raw_pnl):
    # Ranking uses the configured executable TP/SL contract, not impossible
    # mark outliers. Raw PnL is preserved separately for outlier diagnostics.
    label = str(outcome or "").lower()
    if label == "tp":
        return 20.0
    if label == "sl":
        return -20.0
    if finite(raw_pnl):
        return max(-100.0, min(100.0, float(raw_pnl)))
    return None


def load_telemetry_for(rows):
    wanted = collections.defaultdict(list)
    for idx, r in enumerate(rows):
        wanted[r["mint"]].append((r["ts"], idx))
    telemetry = collections.defaultdict(list)
    max_ts_by_mint = {m: max(ts for ts, _ in vals) for m, vals in wanted.items()}
    min_ts_by_mint = {m: min(ts for ts, _ in vals) for m, vals in wanted.items()}
    lower_pad = max(WINDOWS_S) * 1000 + 1000
    upper_pad = 1000
    scanned = 0
    used = 0
    for o in jsonl(TELEMETRY):
        scanned += 1
        mint = o.get("mint")
        ts = o.get("tsMs")
        if mint not in wanted or not finite(ts):
            continue
        if ts < min_ts_by_mint[mint] - lower_pad or ts > max_ts_by_mint[mint] + upper_pad:
            continue
        side = o.get("side")
        quote = o.get("quoteSol") or 0.0
        try:
            quote = float(quote)
        except Exception:
            quote = 0.0
        features = o.get("features") or {}
        telemetry[mint].append(
            {
                "ts": int(ts),
                "side": side,
                "quote": quote,
                "wallet": o.get("wallet"),
                "features": features,
            }
        )
        used += 1
    for arr in telemetry.values():
        arr.sort(key=lambda x: x["ts"])
    return telemetry, {"scanned": scanned, "used": used, "mints": len(telemetry)}


def attach_multiscale(rows, telemetry):
    for r in rows:
        arr = telemetry.get(r["mint"], [])
        ts = r["ts"]
        times = [x["ts"] for x in arr]
        end = bisect_left(times, ts + 1)
        trow = arr[end - 1] if end > 0 and abs(arr[end - 1]["ts"] - ts) <= 50 else None
        feats = trow.get("features", {}) if trow else {}
        for w in FAST_WINDOWS_MS:
            r[f"buy_count_{w}ms"] = feats.get(f"buy_count_{w}ms")
            r[f"unique_buyers_{w}ms"] = feats.get(f"unique_buyers_{w}ms")
            r[f"net_buy_sol_{w}ms"] = feats.get(f"net_buy_sol_{w}ms")
            r[f"first_buyer_quote_share_bps_{w}ms"] = feats.get(f"first_buyer_quote_share_bps_{w}ms")
        for sec in WINDOWS_S:
            start = ts - sec * 1000
            i = bisect_left(times, start)
            window = arr[i:end]
            buy_sol = sum(x["quote"] for x in window if x["side"] == "buy")
            sell_sol = sum(x["quote"] for x in window if x["side"] == "sell")
            buyers = {x["wallet"] for x in window if x["side"] == "buy" and x.get("wallet")}
            counts = collections.Counter(x["wallet"] for x in window if x["side"] == "buy" and x.get("wallet"))
            max_wallet_count = max(counts.values()) if counts else 0
            r[f"gross_buy_sol_{sec}s"] = buy_sol
            r[f"gross_sell_sol_{sec}s"] = sell_sol
            r[f"net_buy_sol_{sec}s"] = buy_sol - sell_sol
            r[f"unique_buyers_{sec}s"] = len(buyers)
            r[f"buy_count_{sec}s"] = sum(1 for x in window if x["side"] == "buy")
            r[f"sell_count_{sec}s"] = sum(1 for x in window if x["side"] == "sell")
            gross = buy_sol + sell_sol
            r[f"buy_fraction_{sec}s"] = buy_sol / gross if gross > 0 else None
            r[f"dominance_count_{sec}s"] = max_wallet_count / r[f"buy_count_{sec}s"] if r[f"buy_count_{sec}s"] else None
            r[f"net_per_mc_{sec}s"] = (buy_sol - sell_sol) / r["mc"] if finite(r.get("mc")) and r["mc"] else None
            r[f"net_per_gross_{sec}s"] = (buy_sol - sell_sol) / gross if gross > 0 else None
        for sec in [5, 10, 30]:
            base = r.get("net_buy_sol_60s")
            r[f"net{sec}s_over_net60s"] = r.get(f"net_buy_sol_{sec}s") / base if finite(base) and abs(base) > 1e-9 else None
        r["accelerating_10s_positive"] = finite(r.get("net_buy_sol_60s")) and r["net_buy_sol_60s"] >= 1 and finite(r.get("net_buy_sol_10s")) and r["net_buy_sol_10s"] > 0


def rule_eval(rows, name, test):
    subset = [r for r in rows if test(r)]
    out = summ(subset)
    out["rule"] = name
    return out


def walk_forward(rows, test, folds=4):
    xs = sorted(rows, key=lambda r: r["ts"])
    if not xs:
        return []
    out = []
    for i in range(folds):
        a = len(xs) * i // folds
        b = len(xs) * (i + 1) // folds
        fold = [r for r in xs[a:b] if test(r)]
        s = summ(fold)
        s["fold"] = i + 1
        out.append(s)
    return out


def capacity(rows, test, max_open):
    selected = [r for r in sorted(rows, key=lambda x: x["entry_ts"] or x["ts"]) if test(r)]
    open_until = []
    taken = []
    blocked = 0
    for r in selected:
        now = r["entry_ts"] or r["ts"]
        open_until = [x for x in open_until if x > now]
        if len(open_until) >= max_open:
            blocked += 1
            continue
        exit_at = r["exit_ts"] or (now + 120000)
        open_until.append(exit_at)
        taken.append(r)
    s = summ(taken)
    s["max_open"] = max_open
    s["blocked"] = blocked
    return s


def durability(rows, test):
    subset = [r for r in rows if test(r)]
    winners = [r for r in subset if str(r.get("outcome")).lower() == "tp"]
    return {
        "n": len(subset),
        "tp": len(winners),
        "tp_with_exit_ge_250ms": sum(1 for r in winners if finite(r.get("exit_ms")) and r["exit_ms"] >= 250),
        "tp_with_exit_ge_500ms": sum(1 for r in winners if finite(r.get("exit_ms")) and r["exit_ms"] >= 500),
        "tp_with_exit_ge_1000ms": sum(1 for r in winners if finite(r.get("exit_ms")) and r["exit_ms"] >= 1000),
        "median_tp_time_ms": median([r.get("exit_ms") for r in winners]),
    }


def outlier(rows, test):
    subset = sorted([r for r in rows if test(r) and finite(r.get("pnl"))], key=lambda r: r["pnl"], reverse=True)
    normal = summ(subset)
    minus1 = summ(subset[1:]) if len(subset) > 1 else summ([])
    minus3 = summ(subset[3:]) if len(subset) > 3 else summ([])
    return {"normal": normal, "exclude_top_1": minus1, "exclude_top_3": minus3}


def make_rules():
    rules = []
    for w in FAST_WINDOWS_MS:
        rules.append((f"bc{w}ms>=2", lambda r, w=w: finite(r.get(f"buy_count_{w}ms")) and r[f"buy_count_{w}ms"] >= 2))
    for sec in WINDOWS_S:
        for th in NET_THRESHOLDS:
            rules.append((f"net_buy_sol_{sec}s>={th}", lambda r, sec=sec, th=th: finite(r.get(f"net_buy_sol_{sec}s")) and r[f"net_buy_sol_{sec}s"] >= th))
    for w in [50, 75, 100]:
        for sec in [5, 10, 15, 30, 60, 90, 120]:
            for th in NET_THRESHOLDS:
                rules.append((f"bc{w}ms>=2 + net_buy_sol_{sec}s>={th}", lambda r, w=w, sec=sec, th=th: finite(r.get(f"buy_count_{w}ms")) and r[f"buy_count_{w}ms"] >= 2 and finite(r.get(f"net_buy_sol_{sec}s")) and r[f"net_buy_sol_{sec}s"] >= th))
    for sec in [10, 30, 60]:
        for th in [1, 2, 3]:
            rules.append((f"bc100ms>=2 + net_buy_sol_{sec}s>={th} + unique_buyers_{sec}s>=3", lambda r, sec=sec, th=th: r.get("buy_count_100ms", 0) >= 2 and finite(r.get(f"net_buy_sol_{sec}s")) and r[f"net_buy_sol_{sec}s"] >= th and r.get(f"unique_buyers_{sec}s", 0) >= 3))
            rules.append((f"bc100ms>=2 + net_buy_sol_{sec}s>={th} + dom60<=0.55", lambda r, sec=sec, th=th: r.get("buy_count_100ms", 0) >= 2 and finite(r.get(f"net_buy_sol_{sec}s")) and r[f"net_buy_sol_{sec}s"] >= th and finite(r.get("dom")) and r["dom"] <= 0.55))
            rules.append((f"bc100ms>=2 + net_buy_sol_{sec}s>={th} + accel10>0", lambda r, sec=sec, th=th: r.get("buy_count_100ms", 0) >= 2 and finite(r.get(f"net_buy_sol_{sec}s")) and r[f"net_buy_sol_{sec}s"] >= th and r.get("accelerating_10s_positive") is True))
    special = [
        ("old_incumbent_bc100>=2_fb<=5000", lambda r: finite(r.get("bc100")) and r["bc100"] >= 2 and finite(r.get("fb_bps")) and r["fb_bps"] <= 5000),
        ("old_mc_challenger_bc100>=2_mc<=5000", lambda r: finite(r.get("bc100")) and r["bc100"] >= 2 and finite(r.get("mc")) and r["mc"] <= 5000),
        ("current_bc100>=2_net60s>=0.5", lambda r: finite(r.get("bc100")) and r["bc100"] >= 2 and finite(r.get("net_buy_sol_60s")) and r["net_buy_sol_60s"] >= 0.5),
        ("current_bc100>=2_net60s>=1", lambda r: finite(r.get("bc100")) and r["bc100"] >= 2 and finite(r.get("net_buy_sol_60s")) and r["net_buy_sol_60s"] >= 1),
        ("current_bc100>=2_net60s>=2", lambda r: finite(r.get("bc100")) and r["bc100"] >= 2 and finite(r.get("net_buy_sol_60s")) and r["net_buy_sol_60s"] >= 2),
    ]
    return special + rules


def ranked_candidates(rows, min_n):
    scores = []
    for name, test in make_rules():
        s = rule_eval(rows, name, test)
        if s["n"] < min_n:
            continue
        wf = walk_forward(rows, test)
        stable = sum(1 for f in wf if f["n"] >= max(5, min_n // 8) and finite(f["pf"]) and f["pf"] > 1)
        d = durability(rows, test)
        o = outlier(rows, test)
        pfs = [f["pf"] for f in wf if finite(f["pf"])]
        s["walk_forward_positive_folds"] = stable
        s["walk_forward_min_pf"] = min(pfs) if pfs else None
        s["tp_durable_ge_500ms_rate"] = pct(d["tp_with_exit_ge_500ms"], d["tp"])
        s["pf_ex_top1"] = o["exclude_top_1"]["pf"]
        s["pf_ex_top3"] = o["exclude_top_3"]["pf"]
        s["score"] = (
            min(s["n"], 1000) / 1000
            + min(s["pf"], 5) * 1.5
            + (s["expectancy_pct"] or 0) / 10
            + stable * 0.5
            + min(s["tp_durable_ge_500ms_rate"], 100) / 100
        )
        scores.append((name, test, s))
    scores.sort(key=lambda x: (x[2]["score"], x[2]["fee_aware_sol_at_1sol"], x[2]["n"]), reverse=True)
    return scores


def plateau(rows):
    out = []
    for sec in [10, 15, 30, 60, 90]:
        for th in [0.5, 1, 2, 3]:
            name = f"bc100ms>=2 + net_buy_sol_{sec}s>={th}"
            out.append(rule_eval(rows, name, lambda r, sec=sec, th=th: r.get("buy_count_100ms", 0) >= 2 and finite(r.get(f"net_buy_sol_{sec}s")) and r[f"net_buy_sol_{sec}s"] >= th))
    return out


def mc_analysis(rows):
    out = []
    for lo, hi in MC_BANDS:
        subset = [r for r in rows if finite(r.get("mc")) and r["mc"] >= lo and r["mc"] < hi]
        s = summ(subset)
        s["band"] = f"{lo:g}-{hi if finite(hi) else 'inf'}"
        out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=25)
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    rows, by_source = load_outcomes()
    telemetry, coverage = load_telemetry_for(rows)
    attach_multiscale(rows, telemetry)
    scores = ranked_candidates(rows, args.min_n)
    top20 = [s for _, _, s in scores[:20]]
    top10 = [s for _, _, s in scores[:10]]
    top3 = scores[:3]
    report = {
        "data": {
            "outcome_rows": len(rows),
            "outcome_sources": dict(by_source),
            "telemetry_coverage": coverage,
            "raw_outlier_rows_abs_gt_1000pct": sum(1 for r in rows if finite(r.get("raw_pnl")) and abs(r["raw_pnl"]) > 1000),
            "note": "net_buy_sol_60 is interpreted as trailing 60 seconds; millisecond flow comes from explicit early-burst telemetry fields.",
            "ranking_pnl_policy": "TP rows score +20%, SL rows score -20%, non-terminal rows use fee-aware raw PnL clipped to [-100%, +100%]. Raw PnL is preserved as raw_pnl and outliers are counted.",
        },
        "benchmarks": {
            "fast_burst": [rule_eval(rows, f"bc{w}ms>=2", lambda r, w=w: finite(r.get(f"buy_count_{w}ms")) and r[f"buy_count_{w}ms"] >= 2) for w in FAST_WINDOWS_MS],
            "net_flow_only": [rule_eval(rows, f"net_buy_sol_{sec}s>={th}", lambda r, sec=sec, th=th: finite(r.get(f"net_buy_sol_{sec}s")) and r[f"net_buy_sol_{sec}s"] >= th) for sec in WINDOWS_S for th in NET_THRESHOLDS],
            "special": [rule_eval(rows, n, t) for n, t in make_rules()[:5]],
            "mc_bands": mc_analysis(rows),
            "plateaus": plateau(rows),
        },
        "top20": top20,
        "top10": top10,
        "top3": [],
    }
    for name, test, s in top3:
        report["top3"].append({
            "summary": s,
            "walk_forward": walk_forward(rows, test),
            "durability": durability(rows, test),
            "outlier": outlier(rows, test),
            "capacity": [capacity(rows, test, n) for n in [1, 2, 3]],
            "size_note": "This pass does not resimulate nonlinear curve impact by size; fee-aware PnL is expressed at 1 SOL notional for ranking only.",
        })
    json_path = os.path.join(OUT_DIR, "report.json")
    md_path = os.path.join(OUT_DIR, "report.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=clean_metric)
    def line_rule(s):
        return f"| {s['rule']} | {s['n']} | {s['wr']:.2f}% | {clean_metric(s['pf'])} | {s['expectancy_pct'] if s['expectancy_pct'] is not None else 'n/a'} | {s['fee_aware_sol_at_1sol']:.4f} | {s.get('walk_forward_positive_folds','')} |"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Corrected Multi-Timescale Fingerprint Research V1\n\n")
        f.write(f"Rows: {len(rows)}. Telemetry used: {coverage['used']} rows across {coverage['mints']} mints.\n\n")
        f.write("`net_buy_sol_60` is trailing 60 seconds, not 60ms. Millisecond burst fields remain separate.\n\n")
        f.write("## Top 20\n\n| Rule | N | WR | PF | Exp % | SOL @1 | WF+ |\n|---|---:|---:|---:|---:|---:|---:|\n")
        for s in top20:
            f.write(line_rule(s) + "\n")
        f.write("\n## Top 3 Detail\n\n")
        for item in report["top3"]:
            s = item["summary"]
            f.write(f"### {s['rule']}\n\n")
            f.write(json.dumps(item, indent=2, sort_keys=True, default=clean_metric)[:6000])
            f.write("\n\n")
        f.write("## Special Benchmarks\n\n")
        for s in report["benchmarks"]["special"]:
            f.write(f"- {s['rule']}: N={s['n']} WR={s['wr']:.2f}% PF={clean_metric(s['pf'])} SOL={s['fee_aware_sol_at_1sol']:.4f}\n")
    print(json.dumps({"json": json_path, "md": md_path, "top3": [x["summary"]["rule"] for x in report["top3"]]}, indent=2))


if __name__ == "__main__":
    main()
