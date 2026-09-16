#!/usr/bin/env python3
import argparse
import json
import math
import os
import statistics
import time
from collections import Counter, defaultdict, deque


TP = 20.0
SL = -20.0
HOLD_MS = 120_000
ENTRY_DELAY_MS = 25
SELLABLE_P50_MS = 964
SELLABLE_P75_MS = 1476
SELLABLE_P90_MS = 1989
ROUND_TRIP_COST_PCT = 2.5
PARENT_RULE = "reacceleration_v1_benchmark"
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


def sell_quote_sol(row):
    raw = row.get("raw_record") or {}
    if finite(raw.get("sellQuoteLamports")):
        return float(raw["sellQuoteLamports"]) / 1_000_000_000
    return 0.0


def creation_age_ms(row):
    value = (row.get("candidate") or {}).get("creation_age_ms")
    return float(value) if finite(value) else 0.0


def first_buyer_bps(row):
    value = (row.get("candidate") or {}).get("first_buyer_quote_share_bps")
    return float(value) if finite(value) else None


def read_jsonl(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def window(events, ts, width_ms):
    start = ts - width_ms
    buys = []
    sells = []
    by_wallet = Counter()
    sell_by_wallet = Counter()
    sigs = set()
    slots = set()
    mcs = []
    for event in reversed(events):
        if event["ts"] < start:
            break
        if event["ts"] > ts:
            continue
        mcs.append(event["mc"])
        if event.get("slot") is not None:
            slots.add(event["slot"])
        if event["side"] == "buy":
            buys.append(event)
            by_wallet[event["wallet"] or "unknown"] += event["quote_sol"]
            if event.get("sig"):
                sigs.add(event["sig"])
        elif event["side"] == "sell":
            sells.append(event)
            sell_by_wallet[event["wallet"] or "unknown"] += max(event.get("sell_sol") or 0.0, 0.0)
            if event.get("sig"):
                sigs.add(event["sig"])
    gross = sum(e["quote_sol"] for e in buys)
    sell_sol = sum(max(e.get("sell_sol") or 0.0, 0.0) for e in sells)
    buy_sizes = sorted((e["quote_sol"] for e in buys), reverse=True)
    intervals = [buys[i - 1]["ts"] - buys[i]["ts"] for i in range(1, len(buys))]
    local_high = max(mcs) if mcs else None
    local_low = min(mcs) if mcs else None
    current_mc = mcs[0] if mcs else None
    return {
        "buy_count": len(buys),
        "gross_buy_sol": gross,
        "sell_count": len(sells),
        "sell_sol": sell_sol,
        "net_buy_sol": gross - sell_sol,
        "unique_buyers": len(by_wallet),
        "unique_sellers": len(sell_by_wallet),
        "tx_count": len(sigs),
        "slot_count": len(slots),
        "top1_share": (max(by_wallet.values()) / gross) if by_wallet and gross > 0 else None,
        "top3_share": (sum(sorted(by_wallet.values(), reverse=True)[:3]) / gross) if by_wallet and gross > 0 else None,
        "median_buy_sol": median(buy_sizes),
        "largest_buy_sol": buy_sizes[0] if buy_sizes else 0.0,
        "median_interbuy_ms": median(intervals),
        "local_high_mc": local_high,
        "local_low_mc": local_low,
        "drawdown_from_high_pct": ((current_mc / local_high - 1.0) * 100.0) if current_mc and local_high else None,
        "price_lift_pct": ((current_mc / local_low - 1.0) * 100.0) if current_mc and local_low and local_low > 0 else None,
        "price_response_per_sol": (((current_mc / local_low - 1.0) * 100.0) / gross) if current_mc and local_low and local_low > 0 and gross > 0 else None,
        "buy_sell_ratio": gross / max(0.001, sell_sol),
    }


def build_features(events, ts, row, mc):
    w100 = window(events, ts, 100)
    w250 = window(events, ts, 250)
    w500 = window(events, ts, 500)
    w1000 = window(events, ts, 1000)
    w2000 = window(events, ts, 2000)
    w5000 = window(events, ts, 5000)
    first_half = [e for e in events if ts - 5000 <= e["ts"] < ts - 2500 and e["side"] == "buy"]
    second_half = [e for e in events if ts - 2500 <= e["ts"] <= ts and e["side"] == "buy"]
    prior_500_1000 = sum(e["quote_sol"] for e in events if ts - 1000 <= e["ts"] < ts - 500 and e["side"] == "buy")
    prior_1000_5000 = sum(e["quote_sol"] for e in events if ts - 5000 <= e["ts"] < ts - 1000 and e["side"] == "buy")
    current_wallets = {e["wallet"] for e in events if ts - 500 <= e["ts"] <= ts and e["side"] == "buy" and e.get("wallet")}
    prior_wallets = {e["wallet"] for e in events if ts - 5000 <= e["ts"] < ts - 500 and e["side"] == "buy" and e.get("wallet")}
    return {
        "mint": mint_of(row),
        "ts": ts,
        "age_ms": creation_age_ms(row),
        "mc_usd": float(mc),
        "first_buyer_bps": first_buyer_bps(row),
        "prior_gross_500_1000": prior_500_1000,
        "prior_gross_1000_5000": prior_1000_5000,
        "second_to_first_sol_ratio": (sum(e["quote_sol"] for e in second_half) / max(0.001, sum(e["quote_sol"] for e in first_half))),
        "new_wallets_500_vs_prior5s": len(current_wallets - prior_wallets),
        "repeat_wallets_500": len(current_wallets & prior_wallets),
        "w100": w100,
        "w250": w250,
        "w500": w500,
        "w1000": w1000,
        "w2000": w2000,
        "w5000": w5000,
    }


def getf(features, key):
    if "." not in key:
        return features.get(key)
    cur = features
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def parent_rule(features):
    gross500 = getf(features, "w500.gross_buy_sol") or 0.0
    gross5s = getf(features, "w5000.gross_buy_sol") or 0.0
    return (
        (getf(features, "w500.buy_count") or 0) >= 3
        and gross500 >= 2
        and gross5s >= 5
        and 0.25 <= gross500 / max(0.001, gross5s) <= 0.80
    )


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
        return {"outcome": "no_fill", "pnl": -ROUND_TRIP_COST_PCT}
    entry_ts, entry_mc = entry
    sellable_ts = entry_ts + sellable_ms
    first_tp_before = None
    first_tp_after = None
    first_sl_after = None
    terminal = None
    max_after = -math.inf
    min_after = math.inf
    time_above = {20: 0, 15: 0, 10: 0}
    starts = {20: None, 15: None, 10: None}
    active = {20: False, 15: False, 10: False}
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
            if roi >= level and not active[level]:
                active[level] = True
                starts[level] = ts
            elif roi < level and active[level]:
                time_above[level] += ts - starts[level]
                active[level] = False
        last_ts = ts
    for level in (20, 15, 10):
        if active[level] and last_ts is not None:
            time_above[level] += last_ts - starts[level]
    if terminal is None:
        return {
            "outcome": "no_post_sellable_mark",
            "pnl": -ROUND_TRIP_COST_PCT,
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
        "return_at_first_sellable": terminal[1],
        "max_return_after_sellable": max_after if finite(max_after) else None,
        "min_return_after_sellable": min_after if finite(min_after) else None,
        "time_above_20_after_sellable_ms": time_above[20],
        "time_above_15_after_sellable_ms": time_above[15],
        "time_above_10_after_sellable_ms": time_above[10],
        "post_sellable_tp_reachable": first_tp_after is not None,
        "pre_sellable_only_winner": first_tp_before is not None and first_tp_after is None,
        "time_to_tp_ms": first_tp_after - entry_ts if first_tp_after else None,
        "time_to_sl_ms": first_sl_after - entry_ts if first_sl_after else None,
    }


def label(normal, stress5, stress10):
    if finite(normal.get("pnl")) and normal["pnl"] > 0 and finite(stress5.get("pnl")) and stress5["pnl"] > 0:
        return "ROBUST_WINNER"
    if finite(normal.get("pnl")) and normal["pnl"] > 0:
        return "FRAGILE_WINNER"
    if normal.get("pre_sellable_only_winner"):
        return "PRE_SELLABLE_ONLY"
    if normal.get("outcome") == "SL":
        return "HARD_STOP_LOSER"
    return "MAX_HOLD_LOSER"


def summarize(rows):
    pnls = [r["pnl"] for r in rows if finite(r.get("pnl"))]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    gp = sum(wins)
    gl = -sum(losses)
    equity = high = dd = 0.0
    for row in sorted(rows, key=lambda r: r["ts"]):
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
        "wr_pct": pct(len(wins), len(pnls)),
        "pf": pf(pnls),
        "expectancy_pct": sum(pnls) / len(pnls) if pnls else None,
        "pnl_at_1sol": sum(pnls) / 100.0,
        "median_return_pct": median(pnls),
        "tp": sum(1 for r in rows if r.get("outcome") == "TP"),
        "sl": sum(1 for r in rows if r.get("outcome") == "SL"),
        "max_hold": sum(1 for r in rows if r.get("outcome") == "max_hold"),
        "drawdown_at_1sol": dd / 100.0,
        "pf_ex_top1": pf(ranked[1:]) if len(ranked) > 1 else None,
        "pf_ex_top3": pf(ranked[3:]) if len(ranked) > 3 else None,
        "top1_contribution_pct": (ranked[0] / sum(pnls) * 100.0) if ranked and sum(pnls) else None,
        "top3_contribution_pct": (sum(ranked[:3]) / sum(pnls) * 100.0) if len(ranked) >= 3 and sum(pnls) else None,
        "median_time_above_20_ms": median(r.get("time_above_20_after_sellable_ms") for r in rows),
        "median_time_above_15_ms": median(r.get("time_above_15_after_sellable_ms") for r in rows),
        "median_time_above_10_ms": median(r.get("time_above_10_after_sellable_ms") for r in rows),
        "post_sellable_tp_rate_pct": pct(sum(1 for r in rows if r.get("post_sellable_tp_reachable")), len(rows)),
        "pre_sellable_only_rate_pct": pct(sum(1 for r in rows if r.get("pre_sellable_only_winner")), len(rows)),
    }


def max_open_one(rows):
    taken = []
    blocked = []
    open_until = -1
    for row in sorted(rows, key=lambda r: r["ts"]):
        if row["ts"] < open_until:
            blocked.append(row)
            continue
        taken.append(row)
        open_until = row["ts"] + (row.get("exit_ms") or HOLD_MS)
    out = summarize(taken)
    out["taken"] = len(taken)
    out["blocked"] = len(blocked)
    out["blocked_winners"] = sum(1 for r in blocked if finite(r.get("pnl")) and r["pnl"] > 0)
    out["blocked_losers"] = sum(1 for r in blocked if finite(r.get("pnl")) and r["pnl"] < 0)
    return out


def split_rows(rows):
    xs = sorted(rows, key=lambda r: r["ts"])
    a = len(xs) // 2
    b = len(xs) * 3 // 4
    return xs[:a], xs[a:b], xs[b:]


def describe_feature(rows, key):
    vals = [getf(r["features"], key) for r in rows]
    return {
        "median": median(vals),
        "p25": quantile(vals, 0.25),
        "p75": quantile(vals, 0.75),
    }


def feature_forensics(rows, keys):
    groups = defaultdict(list)
    for row in rows:
        groups[row["label"]].append(row)
    out = {}
    for key in keys:
        out[key] = {name: describe_feature(group, key) for name, group in groups.items()}
    return out


def row_child1(row):
    f = row["features"]
    return (
        (getf(f, "w5000.top1_share") or 1) <= 0.60
        and (getf(f, "w5000.unique_buyers") or 0) >= 4
        and (getf(f, "w5000.drawdown_from_high_pct") or 0) >= -10
        and (getf(f, "w5000.buy_sell_ratio") or 0) >= 3
    )


def row_child2(row):
    f = row["features"]
    return (
        (getf(f, "w1000.price_response_per_sol") or 999) <= 18
        and 8 <= (getf(f, "w5000.price_lift_pct") or 999) <= 70
        and (getf(f, "w500.median_interbuy_ms") or 999999) <= 175
        and (getf(f, "w500.unique_buyers") or 0) >= 2
    )


def evaluate_set(rows):
    train, validation, holdout = split_rows(rows)
    return {
        "all": summarize(rows),
        "train": summarize(train),
        "validation": summarize(validation),
        "holdout": summarize(holdout),
        "p50": summarize([{**r, **score(r["ts"], r["mc"], r["marks"], ENTRY_DELAY_MS, SELLABLE_P50_MS, 0)} for r in rows]),
        "p75": summarize(rows),
        "p90": summarize([{**r, **score(r["ts"], r["mc"], r["marks"], ENTRY_DELAY_MS, SELLABLE_P90_MS, 0)} for r in rows]),
        "stress5": summarize([{**r, **score(r["ts"], r["mc"], r["marks"], ENTRY_DELAY_MS, SELLABLE_P75_MS, 5)} for r in rows]),
        "stress10": summarize([{**r, **score(r["ts"], r["mc"], r["marks"], ENTRY_DELAY_MS, SELLABLE_P75_MS, 10)} for r in rows]),
        "max_open1": max_open_one(rows),
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
    parser.add_argument("--v2-outcomes", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    parent_keys = set()
    trigger_rows = {}
    for row in read_jsonl(args.v2_outcomes):
        if row.get("rule") != PARENT_RULE:
            continue
        mint = row.get("mint")
        ts = int(row.get("ts") or 0)
        if mint and ts:
            parent_keys.add((mint, ts))
            trigger_rows[(mint, ts)] = row
    by_mint_ts = defaultdict(set)
    for mint, ts in parent_keys:
        by_mint_ts[mint].add(ts)
    flows = defaultdict(deque)
    features = {}
    rows_scanned = 0
    start = time.time()
    for row in read_jsonl(args.capsules):
        rows_scanned += 1
        mint = mint_of(row)
        ts = ts_of(row)
        if not mint or not finite(ts):
            continue
        ts = int(ts)
        side = side_of(row)
        mc = mc_of(row)
        if not finite(mc) or not (0 < float(mc) <= MAX_MC_USD) or side not in ("buy", "sell"):
            continue
        event = {
            "ts": ts,
            "side": side,
            "quote_sol": quote_sol(row) if side == "buy" else 0.0,
            "sell_sol": sell_quote_sol(row),
            "wallet": (row.get("raw_record") or {}).get("wallet"),
            "sig": (row.get("raw_record") or {}).get("signature") or row.get("transaction_signature"),
            "slot": (row.get("raw_record") or {}).get("slot") or row.get("source_slot"),
            "mc": float(mc),
        }
        dq = flows[mint]
        dq.append(event)
        while dq and dq[0]["ts"] < ts - HOLD_MS:
            dq.popleft()
        if mint in by_mint_ts and ts in by_mint_ts[mint]:
            feat = build_features(dq, ts, row, mc)
            if parent_rule(feat):
                features[(mint, ts)] = feat
        if rows_scanned % 1_000_000 == 0:
            stale_cutoff = ts - HOLD_MS
            for stale in [key for key, val in flows.items() if not val or val[-1]["ts"] < stale_cutoff]:
                del flows[stale]
            print(json.dumps({"progress": "features", "rows": rows_scanned, "features": len(features), "elapsed_sec": round(time.time() - start, 1)}), flush=True)
    wanted = {mint: min(tss) for mint, tss in by_mint_ts.items()}
    marks = defaultdict(list)
    rows_pass2 = 0
    for row in read_jsonl(args.capsules):
        rows_pass2 += 1
        mint = mint_of(row)
        if mint not in wanted:
            continue
        ts = ts_of(row)
        mc = mc_of(row)
        if finite(ts) and finite(mc) and wanted[mint] <= int(ts) <= wanted[mint] + HOLD_MS + SELLABLE_P90_MS + 1000 and 0 < float(mc) <= MAX_MC_USD:
            marks[mint].append((int(ts), float(mc)))
        if rows_pass2 % 2_000_000 == 0:
            print(json.dumps({"progress": "marks", "rows": rows_pass2, "mark_mints": len(marks), "elapsed_sec": round(time.time() - start, 1)}), flush=True)
    for arr in marks.values():
        arr.sort()
    feature_path = os.path.join(args.out_dir, "parent_feature_table.jsonl")
    label_path = os.path.join(args.out_dir, "durability_label_table.jsonl")
    rows = []
    with open(feature_path, "w", encoding="utf-8") as feature_out, open(label_path, "w", encoding="utf-8") as label_out:
        for key, feat in features.items():
            mint, ts = key
            qrow = trigger_rows[key]
            mark_rows = marks.get(mint, [])
            normal = score(ts, qrow["mc"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P75_MS, 0)
            stress5 = score(ts, qrow["mc"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P75_MS, 5)
            stress10 = score(ts, qrow["mc"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P75_MS, 10)
            row = {"mint": mint, "ts": ts, "mc": qrow["mc"], "features": feat, "marks": mark_rows, **normal}
            row["stress5_pnl"] = stress5.get("pnl")
            row["stress10_pnl"] = stress10.get("pnl")
            row["label"] = label(normal, stress5, stress10)
            rows.append(row)
            feature_out.write(json.dumps(compact({"mint": mint, "ts": ts, "mc": qrow["mc"], "features": feat}), separators=(",", ":")) + "\n")
            label_out.write(json.dumps(compact({k: v for k, v in row.items() if k not in ("marks", "features")}), separators=(",", ":")) + "\n")
    train, validation, holdout = split_rows(rows)
    child_defs = {
        "child_1_sell_absorption_breadth": {
            "semantics": "REACCELERATION + SELL_ABSORPTION/BREADTH",
            "definition": "parent + top1_5s<=60% + uniqueBuyers5s>=4 + drawdownFrom5sHigh>=-10% + buySellRatio5s>=3",
            "selected_on": "TRAIN_ONLY feature forensics: robust winners had better breadth/concentration and less pre-decision structural damage than hard-stop losers.",
            "rows": [row for row in rows if row_child1(row)],
        },
        "child_2_low_overextension_fast_cadence": {
            "semantics": "REACCELERATION + LOW_OVEREXTENSION/FAST_CADENCE",
            "definition": "parent + priceResponsePerSol1s<=18 + priceLift5s 8%-70% + medianInterbuy500ms<=175ms + uniqueBuyers500ms>=2",
            "selected_on": "TRAIN_ONLY feature forensics: robust winners were less overextended per SOL and had tighter second-wave cadence.",
            "rows": [row for row in rows if row_child2(row)],
        },
    }
    keys = [
        "age_ms",
        "mc_usd",
        "w500.gross_buy_sol",
        "w5000.gross_buy_sol",
        "w5000.buy_sell_ratio",
        "w5000.sell_count",
        "w5000.unique_buyers",
        "w5000.top1_share",
        "w5000.top3_share",
        "w5000.drawdown_from_high_pct",
        "w5000.price_lift_pct",
        "w1000.price_response_per_sol",
        "w500.median_interbuy_ms",
        "new_wallets_500_vs_prior5s",
        "second_to_first_sol_ratio",
    ]
    report = {
        "dataset": {
            "rows_scanned": rows_scanned,
            "parent_triggers_from_v2": len(parent_keys),
            "features_reconstructed": len(features),
            "time_start_ms": min(r["ts"] for r in rows) if rows else None,
            "time_end_ms": max(r["ts"] for r in rows) if rows else None,
            "span_hours": ((max(r["ts"] for r in rows) - min(r["ts"] for r in rows)) / 3_600_000) if rows else None,
            "entry_delay_ms": ENTRY_DELAY_MS,
            "primary_first_sellable_ms": SELLABLE_P75_MS,
            "rules_created": 2,
        },
        "label_counts": dict(Counter(row["label"] for row in rows)),
        "feature_forensics_train_only": feature_forensics(train, keys),
        "parent": evaluate_set(rows),
        "children": {name: {k: v for k, v in info.items() if k != "rows"} | evaluate_set(info["rows"]) for name, info in child_defs.items()},
        "comparison": [],
    }
    for name, label_name, candidate_rows in [
        ("reacceleration_v1_parent", "Parent", rows),
        ("child_1_sell_absorption_breadth", "Child 1", child_defs["child_1_sell_absorption_breadth"]["rows"]),
        ("child_2_low_overextension_fast_cadence", "Child 2", child_defs["child_2_low_overextension_fast_cadence"]["rows"]),
    ]:
        ev = evaluate_set(candidate_rows)
        report["comparison"].append(
            {
                "name": name,
                "label": label_name,
                "n": len(candidate_rows),
                "signals_per_hour": len(candidate_rows) / max(0.001, report["dataset"]["span_hours"] or 0),
                "p75_pf": ev["p75"]["pf"],
                "p75_expectancy_pct": ev["p75"]["expectancy_pct"],
                "p75_wr_pct": ev["p75"]["wr_pct"],
                "validation_pf": ev["validation"]["pf"],
                "holdout_pf": ev["holdout"]["pf"],
                "stress5_pf": ev["stress5"]["pf"],
                "stress10_pf": ev["stress10"]["pf"],
                "max_open1_pf": ev["max_open1"]["pf"],
                "survival_pct": ev["p75"]["post_sellable_tp_rate_pct"],
                "pf_ex_top1": ev["p75"]["pf_ex_top1"],
                "pf_ex_top3": ev["p75"]["pf_ex_top3"],
                "p50_pf": ev["p50"]["pf"],
                "p90_pf": ev["p90"]["pf"],
            }
        )
    final = "REACCELERATION_SIGNAL_REAL_BUT_NOT_ROBUST_ENOUGH"
    for row in report["comparison"][1:]:
        if (
            row["validation_pf"] >= 1.4
            and row["holdout_pf"] >= 1.3
            and row["stress5_pf"] > 1.0
            and row["max_open1_pf"] > 1.0
            and row["n"] >= 500
        ):
            final = "REACCELERATION_CHILD_READY_FOR_INDEPENDENT_AUDIT"
    report["final_verdict"] = final
    with open(os.path.join(args.out_dir, "report.json"), "w", encoding="utf-8") as out:
        json.dump(compact(report), out, indent=2)
    print(json.dumps(compact({"done": True, "out_dir": args.out_dir, "dataset": report["dataset"], "comparison": report["comparison"], "final_verdict": final}), separators=(",", ":")))


if __name__ == "__main__":
    main()
