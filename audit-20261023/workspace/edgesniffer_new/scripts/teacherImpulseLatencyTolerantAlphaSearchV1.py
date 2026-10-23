#!/usr/bin/env python3
import argparse
import json
import math
import os
from collections import defaultdict, deque


BASE = "/var/lib/edgesniffer-teacher-impulse-parity-v1"
CAPSULES = os.path.join(BASE, "candidate-capsules.jsonl")
OUT_DIR = os.path.join(BASE, "research", "latency-tolerant-alpha-search-v1")
HOLD_MS = 120_000
TP = 20.0
SL = -20.0
ROUND_TRIP_COST_PCT = 2.5
LATENCIES = [5, 20, 50, 100, 250, 500, 1000, 2000]


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def pct(n, d):
    return n * 100.0 / d if d else 0.0


def q(values, quant):
    xs = sorted(value for value in values if finite(value))
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, int((len(xs) - 1) * quant)))]


def read_jsonl(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
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


def side_of(row):
    return (row.get("raw_record") or {}).get("side") or row.get("side")


def quote_sol(row):
    rr = row.get("raw_record") or {}
    if finite(rr.get("quoteLamports")):
        return float(rr["quoteLamports"]) / 1_000_000_000
    return 0.0


def first_buyer_bps(row):
    value = (row.get("candidate") or {}).get("first_buyer_quote_share_bps")
    return float(value) if finite(value) else None


def summarize(rows):
    pnls = [row["pnl"] for row in rows if finite(row.get("pnl"))]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gp = sum(wins)
    gl = -sum(losses)
    return {
        "n": len(rows),
        "resolved": len(pnls),
        "wrPct": pct(len(wins), len(pnls)),
        "pf": gp / gl if gl else ("inf" if gp else 0.0),
        "expectancyPct": sum(pnls) / len(pnls) if pnls else None,
        "normalizedPnlAt1Sol": sum(pnls) / 100.0,
        "tp": sum(1 for row in rows if row.get("outcome") == "tp"),
        "sl": sum(1 for row in rows if row.get("outcome") == "sl"),
        "maxHold": sum(1 for row in rows if row.get("outcome") == "max_hold"),
        "noFill": sum(1 for row in rows if row.get("outcome") == "no_fill"),
    }


def score(entry_ts, entry_mc, marks, delay_ms):
    target = entry_ts + delay_ms
    delayed_entry = None
    for ts, mc in marks:
        if ts >= target:
            delayed_entry = (ts, mc)
            break
    if delayed_entry is None:
        return {"outcome": "no_fill", "pnl": -ROUND_TRIP_COST_PCT, "exit_ms": None}
    e_ts, e_mc = delayed_entry
    first_tp = None
    first_sl = None
    terminal = None
    max_roi = -math.inf
    min_roi = math.inf
    tp_duration = 0
    tp_open = None
    last_ts = None
    for ts, mc in marks:
        if ts < e_ts or ts > e_ts + HOLD_MS:
            continue
        roi = ((mc / e_mc) - 1.0) * 100.0 - ROUND_TRIP_COST_PCT
        max_roi = max(max_roi, roi)
        min_roi = min(min_roi, roi)
        terminal = (ts, roi)
        if roi >= TP and first_tp is None:
            first_tp = (ts, roi)
            tp_open = ts
        if roi < TP and tp_open is not None:
            tp_duration += ts - tp_open
            tp_open = None
        if roi <= SL and first_sl is None:
            first_sl = (ts, roi)
        last_ts = ts
    if tp_open is not None and last_ts is not None:
        tp_duration += last_ts - tp_open
    if first_tp and (not first_sl or first_tp[0] <= first_sl[0]):
        return {
            "outcome": "tp",
            "pnl": TP,
            "exit_ms": first_tp[0] - e_ts,
            "max_roi": max_roi,
            "min_roi": min_roi,
            "tp_duration_ms": tp_duration,
        }
    if first_sl:
        return {
            "outcome": "sl",
            "pnl": SL,
            "exit_ms": first_sl[0] - e_ts,
            "max_roi": max_roi,
            "min_roi": min_roi,
            "tp_duration_ms": tp_duration,
        }
    pnl = terminal[1] if terminal else -ROUND_TRIP_COST_PCT
    return {
        "outcome": "max_hold",
        "pnl": max(-100.0, min(100.0, pnl)),
        "exit_ms": terminal[0] - e_ts if terminal else HOLD_MS,
        "max_roi": max_roi if finite(max_roi) else None,
        "min_roi": min_roi if finite(min_roi) else None,
        "tp_duration_ms": tp_duration,
    }


def window(events, ts, ms):
    start = ts - ms
    vals = [event for event in events if event[0] >= start and event[0] <= ts]
    buys = [event for event in vals if event[2] == "buy"]
    gross = sum(event[1] for event in buys)
    by_wallet = defaultdict(float)
    for _ts, amount, _side, wallet in buys:
        if wallet:
            by_wallet[wallet] += amount
    return {
        "buy_count": len(buys),
        "gross_buy": gross,
        "unique_buyers": len(by_wallet),
        "dominance": max(by_wallet.values()) / gross if gross > 0 and by_wallet else None,
    }


def rule_defs(features):
    fb = features.get("first_buyer_quote_share_bps")
    mc = features.get("mc")
    return {
        "gross1s_ge5": features["gross1s"] >= 5,
        "gross2s_ge5": features["gross2s"] >= 5,
        "bc50_ge2_gross5s_ge5": features["bc50"] >= 2 and features["gross5s"] >= 5,
        "sustained_gross5s_ge8": features["gross5s"] >= 8,
        "persistent_bc500_ge3_gross5s_ge5": features["bc500"] >= 3 and features["gross5s"] >= 5,
        "distributed_gross5s_ge5_unique5s_ge3_dom_le60": (
            features["gross5s"] >= 5
            and features["unique5s"] >= 3
            and finite(features.get("dom5s"))
            and features["dom5s"] <= 0.60
        ),
        "low_firstbuyer_gross1s_ge5_fb_le5000": features["gross1s"] >= 5 and finite(fb) and fb <= 5000,
        "mc5000_gross1s_ge5": features["gross1s"] >= 5 and finite(mc) and mc <= 5000,
        "mc5000_bc100_ge2": features["bc100"] >= 2 and finite(mc) and mc <= 5000,
        "bc100_ge2_gross1s_ge3": features["bc100"] >= 2 and features["gross1s"] >= 3,
        "bc250_ge3_gross2s_ge5": features["bc250"] >= 3 and features["gross2s"] >= 5,
        "second_wave_bc1s_ge4_gross5s_ge5": features["bc1000"] >= 4 and features["gross5s"] >= 5,
    }


def first_pass(limit_rows=None):
    flows = defaultdict(deque)
    qualified = defaultdict(dict)
    stats = {"rows": 0, "flow_rows": 0}
    for row in read_jsonl(CAPSULES):
        stats["rows"] += 1
        if limit_rows and stats["rows"] > limit_rows:
            break
        mint = mint_of(row)
        ts = ts_of(row)
        mc = mc_of(row)
        side = side_of(row)
        if not mint or not finite(ts) or not finite(mc) or side not in ("buy", "sell"):
            continue
        stats["flow_rows"] += 1
        amount = quote_sol(row) if side == "buy" else 0.0
        wallet = (row.get("raw_record") or {}).get("wallet")
        dq = flows[mint]
        dq.append((int(ts), amount, side, wallet))
        cutoff = int(ts) - HOLD_MS
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        w50 = window(dq, int(ts), 50)
        w100 = window(dq, int(ts), 100)
        w250 = window(dq, int(ts), 250)
        w500 = window(dq, int(ts), 500)
        w1000 = window(dq, int(ts), 1000)
        w2000 = window(dq, int(ts), 2000)
        w5000 = window(dq, int(ts), 5000)
        features = {
            "mint": mint,
            "candidate_ready_ts_ms": int(ts),
            "mc": float(mc),
            "first_buyer_quote_share_bps": first_buyer_bps(row),
            "bc50": w50["buy_count"],
            "bc100": w100["buy_count"],
            "bc250": w250["buy_count"],
            "bc500": w500["buy_count"],
            "bc1000": w1000["buy_count"],
            "gross1s": w1000["gross_buy"],
            "gross2s": w2000["gross_buy"],
            "gross5s": w5000["gross_buy"],
            "unique5s": w5000["unique_buyers"],
            "dom5s": w5000["dominance"],
            "sourceSlot": row.get("source_slot"),
            "tokenProgram": (row.get("candidate") or {}).get("token_program"),
        }
        for rule, ok in rule_defs(features).items():
            if ok and mint not in qualified[rule]:
                qualified[rule][mint] = dict(features)
    return qualified, stats


def second_pass_marks(qualified, limit_rows=None):
    wanted = {}
    for by_mint in qualified.values():
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
        if finite(ts) and finite(mc) and wanted[mint] <= ts <= wanted[mint] + HOLD_MS + 1000:
            marks[mint].append((int(ts), float(mc)))
    for vals in marks.values():
        vals.sort()
    return marks


def walk_forward(rows):
    xs = sorted(rows, key=lambda row: row["ts"])
    folds = []
    for idx in range(4):
        a = len(xs) * idx // 4
        b = len(xs) * (idx + 1) // 4
        item = summarize(xs[a:b])
        item["fold"] = idx + 1
        folds.append(item)
    return folds


def durability(rows):
    winners = [row for row in rows if row.get("outcome") == "tp"]
    return {
        "winners": len(winners),
        "tpDurationMedianMs": q((row.get("tp_duration_ms") for row in winners), 0.50),
        "tpDurationP75Ms": q((row.get("tp_duration_ms") for row in winners), 0.75),
        "tpPersistGe250ms": sum(1 for row in winners if finite(row.get("tp_duration_ms")) and row["tp_duration_ms"] >= 250),
        "tpPersistGe500ms": sum(1 for row in winners if finite(row.get("tp_duration_ms")) and row["tp_duration_ms"] >= 500),
        "tpPersistGe1000ms": sum(1 for row in winners if finite(row.get("tp_duration_ms")) and row["tp_duration_ms"] >= 1000),
    }


def opportunity_rate(by_mint):
    ts = sorted(row["candidate_ready_ts_ms"] for row in by_mint.values())
    if not ts:
        return {"perHour": 0, "spanHours": 0}
    span = max((ts[-1] - ts[0]) / 3_600_000, 1 / 3600)
    return {"perHour": len(ts) / span, "spanHours": span}


def production_score(summary, wf, durable, rate):
    pf = summary.get("pf")
    pf_val = pf if finite(pf) else 5.0 if pf == "inf" else 0.0
    exp = summary.get("expectancyPct") or 0.0
    n = summary.get("resolved") or 0
    stable = sum(1 for fold in wf if finite(fold.get("expectancyPct")) and fold["expectancyPct"] > 0)
    persistence = durable["tpPersistGe500ms"] / durable["winners"] if durable["winners"] else 0.0
    return (
        min(n, 5000) / 5000.0
        + min(max(pf_val - 1.0, 0.0), 2.0)
        + min(max(exp, 0.0), 10.0) / 5.0
        + stable / 2.0
        + persistence
        + min(rate.get("perHour") or 0.0, 200.0) / 200.0
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-rows", type=int, default=0)
    parser.add_argument("--out-dir", default=OUT_DIR)
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    limit = args.limit_rows or None
    qualified, scan_stats = first_pass(limit)
    marks = second_pass_marks(qualified, limit)

    report = {
        "methodology": {
            "type": "analytics-only latency-tolerant alpha search",
            "latenciesMs": LATENCIES,
            "qualification": "Earliest point-in-time observation where the fixed candidate rule becomes true.",
            "outcome": "Executable-delay MC-mark proxy with TP20/SL20/120s and 2.5pp round-trip cost.",
            "warning": "This is discovery output. It does not promote a live fingerprint without prospective validation.",
        },
        "scanStats": scan_stats,
        "rules": {},
        "leaderboards": {},
        "topStructurallyDistinct": [],
    }
    for rule, by_mint in qualified.items():
        rule_result = {
            "qualified": len(by_mint),
            "opportunityRate": opportunity_rate(by_mint),
            "latency": {},
        }
        for delay in LATENCIES:
            rows = []
            for mint, qrow in by_mint.items():
                result = score(qrow["candidate_ready_ts_ms"], qrow["mc"], marks.get(mint, []), delay)
                rows.append({"mint": mint, "ts": qrow["candidate_ready_ts_ms"], **result})
            summary = summarize(rows)
            wf = walk_forward(rows)
            dur = durability(rows)
            summary["walkForward"] = wf
            summary["durability"] = dur
            summary["productionScore"] = production_score(summary, wf, dur, rule_result["opportunityRate"])
            rule_result["latency"][str(delay)] = summary
        report["rules"][rule] = rule_result

    for delay in LATENCIES:
        rows = []
        for rule, data in report["rules"].items():
            item = dict(data["latency"][str(delay)])
            item["rule"] = rule
            item["qualified"] = data["qualified"]
            item["perHour"] = data["opportunityRate"]["perHour"]
            rows.append(item)
        rows.sort(key=lambda row: (row.get("productionScore") or 0.0, row.get("expectancyPct") or 0.0), reverse=True)
        report["leaderboards"][f"bestAt{delay}ms"] = rows[:10]

    families = ["gross", "persistent", "distributed", "low_firstbuyer", "mc5000", "second_wave"]
    for family in families:
        candidates = []
        for rule, data in report["rules"].items():
            if family in rule:
                row = dict(data["latency"]["250"])
                row["rule"] = rule
                row["perHour"] = data["opportunityRate"]["perHour"]
                candidates.append(row)
        if candidates:
            candidates.sort(key=lambda row: row.get("productionScore") or 0.0, reverse=True)
            report["topStructurallyDistinct"].append(candidates[0])

    json_path = os.path.join(args.out_dir, "report.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    print(json.dumps({"report": json_path, "rules": len(report["rules"])}, indent=2))


if __name__ == "__main__":
    main()
