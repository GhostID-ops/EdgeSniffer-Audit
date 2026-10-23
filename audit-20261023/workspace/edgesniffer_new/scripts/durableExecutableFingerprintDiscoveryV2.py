#!/usr/bin/env python3
import argparse
import json
import math
import os
import time
from collections import Counter, defaultdict, deque


HOLD_MS = 120_000
TP = 20.0
SL = -20.0
ROUND_TRIP_COST_PCT = 2.5
PRIMARY_ENTRY_DELAY_MS = 25
SELLABLE_P50_MS = 964
SELLABLE_P75_MS = 1476
SELLABLE_P90_MS = 1989
MAX_MC_USD = 250_000


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def num(value, default=None):
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def pct(n, d):
    return (n * 100.0 / d) if d else 0.0


def quantile(values, q):
    xs = sorted(x for x in values if finite(x))
    if not xs:
        return None
    idx = min(len(xs) - 1, max(0, int((len(xs) - 1) * q)))
    return xs[idx]


def median(values):
    xs = sorted(x for x in values if finite(x))
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def mint_of(row):
    return (row.get("candidate") or {}).get("mint") or row.get("mint") or (row.get("raw_record") or {}).get("mint")


def ts_of(row):
    return (
        row.get("candidate_ready_ts_ms")
        or row.get("decode_complete_ts_ms")
        or row.get("local_receive_ts_ms")
        or (row.get("canonical_state") or {}).get("observed_at_ms")
    )


def mc_of(row):
    return (row.get("canonical_state") or {}).get("market_cap_usd") or (row.get("candidate") or {}).get("market_cap_usd")


def side_of(row):
    return (row.get("raw_record") or {}).get("side") or row.get("side")


def quote_sol(row):
    raw = row.get("raw_record") or {}
    if finite(raw.get("quoteLamports")):
        return float(raw["quoteLamports"]) / 1_000_000_000
    return float(row.get("quote_sol") or 0.0)


def first_buyer_bps(row):
    value = (row.get("candidate") or {}).get("first_buyer_quote_share_bps")
    return float(value) if finite(value) else None


def creation_age_ms(row):
    value = (row.get("candidate") or {}).get("creation_age_ms")
    return float(value) if finite(value) else 0.0


def read_jsonl(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def window_features(events, ts, width_ms):
    start = ts - width_ms
    buys = []
    sells = 0
    quote_by_wallet = Counter()
    sigs = set()
    mcs = []
    for event in reversed(events):
        if event["ts"] < start:
            break
        if event["ts"] > ts:
            continue
        mcs.append(event["mc"])
        if event["side"] == "buy":
            buys.append(event)
            quote_by_wallet[event["wallet"] or "unknown"] += event["quote_sol"]
            if event.get("sig"):
                sigs.add(event["sig"])
        elif event["side"] == "sell":
            sells += 1
    gross = sum(event["quote_sol"] for event in buys)
    quotes = sorted((event["quote_sol"] for event in buys), reverse=True)
    intervals = [buys[i - 1]["ts"] - buys[i]["ts"] for i in range(1, len(buys))]
    local_high = max(mcs) if mcs else None
    local_low = min(mcs) if mcs else None
    current_mc = mcs[0] if mcs else None
    return {
        "bc": len(buys),
        "gross": gross,
        "sells": sells,
        "unique": len(quote_by_wallet),
        "dom": (max(quote_by_wallet.values()) / gross) if quote_by_wallet and gross > 0 else None,
        "top3": (sum(sorted(quote_by_wallet.values(), reverse=True)[:3]) / gross) if quote_by_wallet and gross > 0 else None,
        "median_buy": median(quotes),
        "largest_buy": quotes[0] if quotes else 0.0,
        "median_interbuy_ms": median(intervals),
        "tx_count": len(sigs),
        "local_high": local_high,
        "local_low": local_low,
        "drawdown_from_high_pct": ((current_mc / local_high - 1.0) * 100.0) if current_mc and local_high else None,
        "price_lift_pct": ((current_mc / local_low - 1.0) * 100.0) if current_mc and local_low and local_low > 0 else None,
        "price_response_per_sol": (((current_mc / local_low - 1.0) * 100.0) / gross) if current_mc and local_low and local_low > 0 and gross > 0 else None,
    }


def current_new_wallets(events, ts, current_ms, prior_ms):
    cur_start = ts - current_ms
    prior_start = ts - prior_ms
    cur = {event["wallet"] for event in events if cur_start <= event["ts"] <= ts and event["side"] == "buy" and event.get("wallet")}
    prior = {event["wallet"] for event in events if prior_start <= event["ts"] < cur_start and event["side"] == "buy" and event.get("wallet")}
    return len(cur - prior)


def feature_row(events, ts, row, mc):
    w100 = window_features(events, ts, 100)
    w250 = window_features(events, ts, 250)
    w500 = window_features(events, ts, 500)
    w1000 = window_features(events, ts, 1000)
    w2000 = window_features(events, ts, 2000)
    w5000 = window_features(events, ts, 5000)
    prior_500_1000 = sum(event["quote_sol"] for event in events if ts - 1000 <= event["ts"] < ts - 500 and event["side"] == "buy")
    prior_1000_5000 = sum(event["quote_sol"] for event in events if ts - 5000 <= event["ts"] < ts - 1000 and event["side"] == "buy")
    return {
        "ts": ts,
        "mc": float(mc),
        "age": creation_age_ms(row),
        "fb_bps": first_buyer_bps(row),
        "new_wallets_500_vs_5000": current_new_wallets(events, ts, 500, 5000),
        "prior_gross_500_1000": prior_500_1000,
        "prior_gross_1000_5000": prior_1000_5000,
        "w100": w100,
        "w250": w250,
        "w500": w500,
        "w1000": w1000,
        "w2000": w2000,
        "w5000": w5000,
    }


def f(features, window, key, default=0):
    value = (features.get(window) or {}).get(key)
    return value if finite(value) else default


def maybe(features, window, key):
    value = (features.get(window) or {}).get(key)
    return value if finite(value) else None


def rule_library():
    return [
        (
            "reacceleration_v1_benchmark",
            "Prior weak benchmark",
            "bc500>=3 AND gross500>=2 AND gross5s>=5 AND gross500/gross5s 25%-80%",
            lambda x: f(x, "w500", "bc") >= 3
            and f(x, "w500", "gross") >= 2
            and f(x, "w5000", "gross") >= 5
            and 0.25 <= f(x, "w500", "gross") / max(0.001, f(x, "w5000", "gross")) <= 0.80,
        ),
        (
            "reacceleration_v2_new_wallet_lull",
            "Reacceleration V2",
            "age 1-15s, gross5s>=5, prior 1-5s>=1, lull 500-1000ms<=0.75, current500>=2, unique500>=2, new wallet present",
            lambda x: 1000 <= x["age"] <= 15000
            and f(x, "w5000", "gross") >= 5
            and x["prior_gross_1000_5000"] >= 1
            and x["prior_gross_500_1000"] <= 0.75
            and f(x, "w500", "gross") >= 2
            and f(x, "w500", "unique") >= 2
            and x["new_wallets_500_vs_5000"] >= 1,
        ),
        (
            "buyer_persistence_v2",
            "Buyer persistence",
            "gross5s>=5, unique5s>=5, unique1s>=3, tx_count5s>=5, top3<=75%, dom5s<=45%",
            lambda x: f(x, "w5000", "gross") >= 5
            and f(x, "w5000", "unique") >= 5
            and f(x, "w1000", "unique") >= 3
            and f(x, "w5000", "tx_count") >= 5
            and maybe(x, "w5000", "top3") is not None
            and maybe(x, "w5000", "top3") <= 0.75
            and maybe(x, "w5000", "dom") is not None
            and maybe(x, "w5000", "dom") <= 0.45,
        ),
        (
            "flow_quality_medium_buys_v2",
            "Flow quality",
            "gross5s>=4, buy_count5s>=5, median buy 0.15-1.5 SOL, largest share<=45%, median interbuy<=350ms",
            lambda x: f(x, "w5000", "gross") >= 4
            and f(x, "w5000", "bc") >= 5
            and maybe(x, "w5000", "median_buy") is not None
            and 0.15 <= maybe(x, "w5000", "median_buy") <= 1.5
            and maybe(x, "w5000", "dom") is not None
            and maybe(x, "w5000", "dom") <= 0.45
            and (maybe(x, "w5000", "median_interbuy_ms") or 999999) <= 350,
        ),
        (
            "price_response_efficiency_v2",
            "Price response efficiency",
            "gross1s>=3, price lift 10%-80% in 5s, price response <=25 pct/SOL, drawdown from high >= -12%",
            lambda x: f(x, "w1000", "gross") >= 3
            and maybe(x, "w5000", "price_lift_pct") is not None
            and 10 <= maybe(x, "w5000", "price_lift_pct") <= 80
            and maybe(x, "w5000", "price_response_per_sol") is not None
            and maybe(x, "w5000", "price_response_per_sol") <= 25
            and (maybe(x, "w5000", "drawdown_from_high_pct") or 0) >= -12,
        ),
        (
            "impulse_maturity_v2",
            "Impulse age / maturity",
            "age 1.5-10s, gross5s>=5, gross1s>=2, unique5s>=4, dom5s<=60%, drawdown>=-10%",
            lambda x: 1500 <= x["age"] <= 10000
            and f(x, "w5000", "gross") >= 5
            and f(x, "w1000", "gross") >= 2
            and f(x, "w5000", "unique") >= 4
            and maybe(x, "w5000", "dom") is not None
            and maybe(x, "w5000", "dom") <= 0.60
            and (maybe(x, "w5000", "drawdown_from_high_pct") or 0) >= -10,
        ),
        (
            "post_burst_stability_v2",
            "Post-burst stability",
            "gross5s>=5, drawdown from 5s high -15%..-2%, current500 gross>=1, current500 unique>=2",
            lambda x: f(x, "w5000", "gross") >= 5
            and maybe(x, "w5000", "drawdown_from_high_pct") is not None
            and -15 <= maybe(x, "w5000", "drawdown_from_high_pct") <= -2
            and f(x, "w500", "gross") >= 1
            and f(x, "w500", "unique") >= 2,
        ),
        (
            "low_overextension_breadth_v2",
            "Limited combo",
            "gross5s>=5, unique5s>=4, price lift<=60%, dom5s<=55%, drawdown>=-8%",
            lambda x: f(x, "w5000", "gross") >= 5
            and f(x, "w5000", "unique") >= 4
            and maybe(x, "w5000", "price_lift_pct") is not None
            and maybe(x, "w5000", "price_lift_pct") <= 60
            and maybe(x, "w5000", "dom") is not None
            and maybe(x, "w5000", "dom") <= 0.55
            and (maybe(x, "w5000", "drawdown_from_high_pct") or 0) >= -8,
        ),
    ]


def score(entry_ts, entry_mc, marks, entry_delay_ms, sellable_ms, entry_stress_pct):
    if not finite(entry_ts) or not finite(entry_mc) or entry_mc <= 0:
        return {"outcome": "missing", "pnl": None}
    target_entry = entry_ts + entry_delay_ms
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
    above = {20: False, 15: False, 10: False}
    starts = {20: None, 15: None, 10: None}
    durations = {20: 0, 15: 0, 10: 0}
    last_ts = None
    for ts, mc in marks:
        if ts < entry_ts or ts > entry_ts + HOLD_MS:
            continue
        roi = ((mc / entry_mc) - 1.0) * 100.0 - ROUND_TRIP_COST_PCT
        if roi >= TP and first_tp_before is None:
            first_tp_before = ts
        if ts < sellable_ts:
            continue
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
            "return_at_first_sellable": None,
            "max_return_after_sellable": None,
            "min_return_after_sellable": None,
            "pre_sellable_only_winner": first_tp_before is not None,
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
        "entry_ts": entry_ts,
        "first_sellable_ts": sellable_ts,
        "return_at_first_sellable": terminal[1],
        "max_return_after_sellable": max_after if finite(max_after) else None,
        "min_return_after_sellable": min_after if finite(min_after) else None,
        "post_sellable_tp_reachable": first_tp_after is not None,
        "pre_sellable_only_winner": first_tp_before is not None and first_tp_after is None,
        "time_above_20_after_sellable_ms": durations[20],
        "time_above_15_after_sellable_ms": durations[15],
        "time_above_10_after_sellable_ms": durations[10],
    }


def summarize(rows):
    pnls = [row["pnl"] for row in rows if finite(row.get("pnl"))]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gp = sum(wins)
    gl = -sum(losses)
    equity = high = dd = 0.0
    for row in sorted(rows, key=lambda x: x["ts"]):
        if finite(row.get("pnl")):
            equity += row["pnl"]
            high = max(high, equity)
            dd = min(dd, equity - high)
    ranked = sorted(pnls, reverse=True)

    def pf(values):
        gp2 = sum(x for x in values if x > 0)
        gl2 = -sum(x for x in values if x < 0)
        return gp2 / gl2 if gl2 else (999.0 if gp2 > 0 else 0.0)

    return {
        "n": len(rows),
        "resolved": len(pnls),
        "wr_pct": pct(len(wins), len(pnls)),
        "pf": pf(pnls),
        "expectancy_pct": sum(pnls) / len(pnls) if pnls else None,
        "normalized_pnl_at_1sol": sum(pnls) / 100.0,
        "median_return_pct": median(pnls),
        "tp": sum(1 for row in rows if row.get("outcome") == "TP"),
        "sl": sum(1 for row in rows if row.get("outcome") == "SL"),
        "max_hold": sum(1 for row in rows if row.get("outcome") == "max_hold"),
        "drawdown_at_1sol": dd / 100.0,
        "pf_ex_top1": pf(ranked[1:]) if len(ranked) > 1 else None,
        "pf_ex_top3": pf(ranked[3:]) if len(ranked) > 3 else None,
        "top1_contribution_pct": (ranked[0] / sum(pnls) * 100.0) if ranked and sum(pnls) else None,
        "top3_contribution_pct": (sum(ranked[:3]) / sum(pnls) * 100.0) if len(ranked) >= 3 and sum(pnls) else None,
        "median_time_above_20_after_sellable_ms": median(row.get("time_above_20_after_sellable_ms") for row in rows),
        "median_time_above_15_after_sellable_ms": median(row.get("time_above_15_after_sellable_ms") for row in rows),
        "median_time_above_10_after_sellable_ms": median(row.get("time_above_10_after_sellable_ms") for row in rows),
        "post_sellable_tp_rate_pct": pct(sum(1 for row in rows if row.get("post_sellable_tp_reachable")), len(rows)),
        "pre_sellable_only_win_rate_pct": pct(sum(1 for row in rows if row.get("pre_sellable_only_winner")), len(rows)),
        "post_sellable_hard_stop_rate_pct": pct(sum(1 for row in rows if row.get("outcome") == "SL"), len(rows)),
    }


def capacity_one(rows):
    taken = []
    blocked = []
    open_until = -1
    for row in sorted(rows, key=lambda x: x["ts"]):
        if row["ts"] < open_until:
            blocked.append(row)
            continue
        taken.append(row)
        open_until = row["ts"] + (row.get("exit_ms") or HOLD_MS)
    out = summarize(taken)
    out["taken"] = len(taken)
    out["blocked"] = len(blocked)
    out["blocked_winners"] = sum(1 for row in blocked if finite(row.get("pnl")) and row["pnl"] > 0)
    out["blocked_losers"] = sum(1 for row in blocked if finite(row.get("pnl")) and row["pnl"] < 0)
    return out


def split_summary(rows):
    xs = sorted(rows, key=lambda x: x["ts"])
    a = len(xs) // 2
    b = len(xs) * 3 // 4
    return {
        "train": summarize(xs[:a]),
        "validation": summarize(xs[a:b]),
        "holdout": summarize(xs[b:]),
    }


def compact(value):
    if isinstance(value, float):
        if math.isinf(value):
            return "inf"
        return round(value, 6)
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capsules", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--limit-rows", type=int, default=0)
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    rules = rule_library()
    qualified = {name: {} for name, *_ in rules}
    first_buy_controls = {}
    flows = defaultdict(deque)
    unique_mints = set()
    rows_scanned = flow_rows = buy_rows = 0
    time_min = None
    time_max = None
    started = time.time()
    for row in read_jsonl(args.capsules):
        rows_scanned += 1
        if args.limit_rows and rows_scanned > args.limit_rows:
            break
        mint = mint_of(row)
        ts = ts_of(row)
        if not mint or not finite(ts):
            continue
        ts = int(ts)
        unique_mints.add(mint)
        time_min = ts if time_min is None else min(time_min, ts)
        time_max = ts if time_max is None else max(time_max, ts)
        side = side_of(row)
        mc = mc_of(row)
        if not finite(mc) or not (0 < float(mc) <= MAX_MC_USD) or side not in ("buy", "sell"):
            continue
        event = {
            "ts": ts,
            "side": side,
            "quote_sol": quote_sol(row) if side == "buy" else 0.0,
            "wallet": (row.get("raw_record") or {}).get("wallet"),
            "sig": (row.get("raw_record") or {}).get("signature") or row.get("transaction_signature"),
            "mc": float(mc),
        }
        dq = flows[mint]
        dq.append(event)
        while dq and dq[0]["ts"] < ts - HOLD_MS:
            dq.popleft()
        flow_rows += 1
        if side != "buy":
            continue
        buy_rows += 1
        feats = feature_row(dq, ts, row, mc)
        if mint not in first_buy_controls:
            first_buy_controls[mint] = {"mint": mint, "ts": ts, "mc": float(mc), "features": feats}
        for name, _family, _definition, test in rules:
            if mint not in qualified[name] and test(feats):
                qualified[name][mint] = {"mint": mint, "ts": ts, "mc": float(mc), "features": feats}
        if rows_scanned % 250_000 == 0:
            stale_cutoff = ts - HOLD_MS
            for stale_mint in [key for key, value in flows.items() if not value or value[-1]["ts"] < stale_cutoff]:
                del flows[stale_mint]
            print(
                json.dumps(
                    {
                        "progress": "pass1",
                        "rows": rows_scanned,
                        "flow_rows": flow_rows,
                        "buy_rows": buy_rows,
                        "active_flow_mints": len(flows),
                        "qualified": {k: len(v) for k, v in qualified.items()},
                        "elapsed_sec": round(time.time() - started, 1),
                    }
                ),
                flush=True,
            )
    wanted = {}
    for by_mint in qualified.values():
        for mint, row in by_mint.items():
            wanted[mint] = min(wanted.get(mint, row["ts"]), row["ts"])
    marks = defaultdict(list)
    rows_pass2 = marks_used = 0
    for row in read_jsonl(args.capsules):
        rows_pass2 += 1
        if args.limit_rows and rows_pass2 > args.limit_rows:
            break
        mint = mint_of(row)
        if mint not in wanted:
            continue
        ts = ts_of(row)
        mc = mc_of(row)
        if finite(ts) and finite(mc) and wanted[mint] <= int(ts) <= wanted[mint] + HOLD_MS + SELLABLE_P90_MS + 1000 and 0 < float(mc) <= MAX_MC_USD:
            marks[mint].append((int(ts), float(mc)))
            marks_used += 1
        if rows_pass2 % 1_000_000 == 0:
            print(
                json.dumps(
                    {
                        "progress": "pass2",
                        "rows": rows_pass2,
                        "marks_used": marks_used,
                        "elapsed_sec": round(time.time() - started, 1),
                    }
                ),
                flush=True,
            )
    for arr in marks.values():
        arr.sort()
    report = {
        "dataset": {
            "capsules": args.capsules,
            "rows_scanned": rows_scanned,
            "flow_rows": flow_rows,
            "buy_rows": buy_rows,
            "unique_mints_observed": len(unique_mints),
            "time_start_ms": time_min,
            "time_end_ms": time_max,
            "span_hours": ((time_max - time_min) / 3_600_000) if time_min and time_max else None,
            "marks_used": marks_used,
            "rules_evaluated": len(rules),
            "entry_delay_ms": PRIMARY_ENTRY_DELAY_MS,
            "primary_first_sellable_ms": SELLABLE_P75_MS,
            "sensitivity_first_sellable_ms": [SELLABLE_P50_MS, SELLABLE_P75_MS, SELLABLE_P90_MS],
        },
        "rules": {},
        "leaderboard": [],
    }
    outcomes_path = os.path.join(args.out_dir, "raw_replay_outcomes.jsonl")
    definitions = []
    with open(outcomes_path, "w", encoding="utf-8") as out:
        for name, family, definition, _test in rules:
            definitions.append({"name": name, "family": family, "definition": definition})
            rows = []
            theoretical_rows = []
            for mint, qrow in qualified[name].items():
                mark_rows = marks.get(mint, [])
                s = score(qrow["ts"], qrow["mc"], mark_rows, PRIMARY_ENTRY_DELAY_MS, SELLABLE_P75_MS, 0)
                row = {"rule": name, "mint": mint, "ts": qrow["ts"], "mc": qrow["mc"], **s}
                rows.append(row)
                theoretical_rows.append(
                    {"rule": name, "mint": mint, "ts": qrow["ts"], "mc": qrow["mc"], **score(qrow["ts"], qrow["mc"], mark_rows, 0, 0, 0)}
                )
                out.write(json.dumps(compact(row), separators=(",", ":")) + "\n")
            p50_rows = [
                {"mint": mint, "ts": qrow["ts"], **score(qrow["ts"], qrow["mc"], marks.get(mint, []), PRIMARY_ENTRY_DELAY_MS, SELLABLE_P50_MS, 0)}
                for mint, qrow in qualified[name].items()
            ]
            p90_rows = [
                {"mint": mint, "ts": qrow["ts"], **score(qrow["ts"], qrow["mc"], marks.get(mint, []), PRIMARY_ENTRY_DELAY_MS, SELLABLE_P90_MS, 0)}
                for mint, qrow in qualified[name].items()
            ]
            stress = {
                str(stress_pct): summarize(
                    [
                        {"mint": mint, "ts": qrow["ts"], **score(qrow["ts"], qrow["mc"], marks.get(mint, []), PRIMARY_ENTRY_DELAY_MS, SELLABLE_P75_MS, stress_pct)}
                        for mint, qrow in qualified[name].items()
                    ]
                )
                for stress_pct in (0, 5, 10, 20)
            }
            theoretical_tp = {row["mint"] for row in theoretical_rows if row.get("outcome") == "TP"}
            executable_tp = {row["mint"] for row in rows if row.get("outcome") == "TP"}
            splits = split_summary(rows)
            max_open = capacity_one(rows)
            primary = summarize(rows)
            holdout = splits["holdout"]
            survival = pct(len(theoretical_tp & executable_tp), len(theoretical_tp))
            positive_folds = sum(1 for part in splits.values() if part["pf"] > 1 and (part["expectancy_pct"] or -999) > 0)
            report["rules"][name] = {
                "family": family,
                "definition": definition,
                "triggers": len(rows),
                "trigger_rate_pct": pct(len(rows), len(unique_mints)),
                "signals_per_hour": len(rows) / max(0.001, report["dataset"]["span_hours"] or 0),
                "theoretical_shadow": summarize(theoretical_rows),
                "executable_p50": summarize(p50_rows),
                "executable_p75_primary": primary,
                "executable_p90": summarize(p90_rows),
                "splits": splits,
                "max_open_1": max_open,
                "entry_stress": stress,
                "first_sellable_winner_survival_rate_pct": survival,
                "pre_sellable_only_win_rate_pct": 100.0 - survival if theoretical_tp else None,
            }
            report["leaderboard"].append(
                {
                    "rule": name,
                    "family": family,
                    "definition": definition,
                    "triggers": len(rows),
                    "signals_per_hour": len(rows) / max(0.001, report["dataset"]["span_hours"] or 0),
                    "p75_pf": primary["pf"],
                    "p75_expectancy_pct": primary["expectancy_pct"],
                    "p75_wr_pct": primary["wr_pct"],
                    "p75_pnl_at_1sol": primary["normalized_pnl_at_1sol"],
                    "validation_pf": splits["validation"]["pf"],
                    "holdout_pf": holdout["pf"],
                    "holdout_expectancy_pct": holdout["expectancy_pct"],
                    "max_open1_pf": max_open["pf"],
                    "entry_stress_5_pf": stress["5"]["pf"],
                    "entry_stress_5_expectancy_pct": stress["5"]["expectancy_pct"],
                    "survival_pct": survival,
                    "pf_ex_top1": primary["pf_ex_top1"],
                    "pf_ex_top3": primary["pf_ex_top3"],
                    "positive_periods": positive_folds,
                    "score": primary["pf"] * 0.8 + holdout["pf"] * 0.7 + stress["5"]["pf"] * 0.5 + survival / 100.0 + positive_folds * 0.25,
                }
            )
    report["leaderboard"].sort(key=lambda row: row["score"], reverse=True)
    with open(os.path.join(args.out_dir, "frozen_candidate_definitions.json"), "w", encoding="utf-8") as out:
        json.dump(compact(definitions), out, indent=2)
    with open(os.path.join(args.out_dir, "report.json"), "w", encoding="utf-8") as out:
        json.dump(compact(report), out, indent=2)
    print(json.dumps(compact({"done": True, "out_dir": args.out_dir, "report": report["dataset"], "leaderboard": report["leaderboard"]}), separators=(",", ":")))


if __name__ == "__main__":
    main()
