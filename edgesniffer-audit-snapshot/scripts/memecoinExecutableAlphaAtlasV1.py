#!/usr/bin/env python3
import argparse
import gzip
import json
import math
import os
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
MAX_MC_USD = 250_000
FIXED_AGES_MS = [500, 1000, 2000, 3000, 5000, 10000, 20000, 30000]
WINDOWS_MS = [100, 250, 500, 1000, 2000, 3000, 5000, 10000, 20000, 30000]


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


def compact(value):
    if isinstance(value, float):
        if math.isinf(value) or math.isnan(value):
            return None
        return round(value, 6)
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


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
    return (
        row.get("candidate_ready_ts_ms")
        or row.get("decode_complete_ts_ms")
        or row.get("local_receive_ts_ms")
        or (row.get("canonical_state") or {}).get("observed_at_ms")
        or row.get("tsMs")
    )


def mc_of(row):
    return (
        (row.get("canonical_state") or {}).get("market_cap_usd")
        or (row.get("candidate") or {}).get("market_cap_usd")
        or row.get("marketCapUsd")
    )


def side_of(row):
    return (row.get("raw_record") or {}).get("side") or row.get("side")


def quote_sol(row):
    raw = row.get("raw_record") or {}
    if finite(raw.get("quoteLamports")):
        return float(raw["quoteLamports"]) / 1_000_000_000
    if finite(row.get("quote_lamports")):
        return float(row["quote_lamports"]) / 1_000_000_000
    return float(row.get("quote_sol") or row.get("quoteSol") or 0.0)


def sell_quote_sol(row):
    raw = row.get("raw_record") or {}
    if finite(raw.get("sellQuoteLamports")):
        return float(raw["sellQuoteLamports"]) / 1_000_000_000
    if finite(row.get("sell_quote_lamports")):
        return float(row["sell_quote_lamports"]) / 1_000_000_000
    return 0.0


def wallet_of(row):
    return (row.get("raw_record") or {}).get("wallet") or row.get("wallet")


def slot_of(row):
    return (row.get("raw_record") or {}).get("slot") or row.get("source_slot") or row.get("sourceSlot")


def signature_of(row):
    return (row.get("raw_record") or {}).get("signature") or row.get("transaction_signature")


def first_buyer_bps(row):
    value = (row.get("candidate") or {}).get("first_buyer_quote_share_bps")
    if finite(value):
        return float(value)
    features = row.get("features") or {}
    for key in ("first_buyer_quote_share_bps_100ms", "first_buyer_quote_share_bps_500ms"):
        if finite(features.get(key)):
            return float(features[key])
    return None


def creation_age_ms(row, first_seen_ts, ts):
    value = (row.get("candidate") or {}).get("creation_age_ms")
    if finite(value):
        return float(value)
    raw_value = (row.get("raw_record") or {}).get("creationAgeMs")
    if finite(raw_value):
        return float(raw_value)
    return float(max(0, ts - first_seen_ts))


def build_event(row, ts, mc, side):
    return {
        "ts": int(ts),
        "side": side,
        "quote_sol": quote_sol(row) if side == "buy" else 0.0,
        "sell_sol": sell_quote_sol(row) if side == "sell" else 0.0,
        "wallet": wallet_of(row),
        "sig": signature_of(row),
        "slot": slot_of(row),
        "mc": float(mc),
    }


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
    sell_sizes = sorted((max(e.get("sell_sol") or 0.0, 0.0) for e in sells), reverse=True)
    intervals = [buys[i - 1]["ts"] - buys[i]["ts"] for i in range(1, len(buys))]
    local_high = max(mcs) if mcs else None
    local_low = min(mcs) if mcs else None
    current_mc = mcs[0] if mcs else None
    price_lift = ((current_mc / local_low - 1.0) * 100.0) if current_mc and local_low and local_low > 0 else None
    drawdown = ((current_mc / local_high - 1.0) * 100.0) if current_mc and local_high else None
    range_width = ((local_high / local_low - 1.0) * 100.0) if local_high and local_low and local_low > 0 else None
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
        "seller_top1_share": (max(sell_by_wallet.values()) / sell_sol) if sell_by_wallet and sell_sol > 0 else None,
        "median_buy_sol": median(buy_sizes),
        "median_sell_sol": median(sell_sizes),
        "largest_buy_sol": buy_sizes[0] if buy_sizes else 0.0,
        "largest_sell_sol": sell_sizes[0] if sell_sizes else 0.0,
        "median_interbuy_ms": median(intervals),
        "local_high_mc": local_high,
        "local_low_mc": local_low,
        "drawdown_from_high_pct": drawdown,
        "price_lift_pct": price_lift,
        "range_width_pct": range_width,
        "price_response_per_sol": (price_lift / gross) if finite(price_lift) and gross > 0 else None,
        "sell_damage_per_sell_count": ((drawdown or 0.0) / len(sells)) if sells else None,
        "buy_sell_ratio": gross / max(0.001, sell_sol if sell_sol > 0 else len(sells) * 0.001),
    }


def flatten_window(features, prefix, values):
    for key, value in values.items():
        features[f"{prefix}.{key}"] = value


def feature_row(events, row, ts, mc, first_seen_ts, snapshot_kind, snapshot_age_ms, global_state):
    age = creation_age_ms(row, first_seen_ts, ts)
    features = {
        "mint": mint_of(row),
        "ts": int(ts),
        "snapshot_kind": snapshot_kind,
        "snapshot_age_ms": snapshot_age_ms,
        "age_ms": age,
        "mc_usd": float(mc),
        "first_buyer_quote_share_bps": first_buyer_bps(row),
        "launches_per_minute": global_state.get("launches_per_minute"),
        "active_flow_mints": global_state.get("active_flow_mints"),
    }
    for width in WINDOWS_MS:
        flatten_window(features, f"w{width}", window(events, ts, width))
    current_wallets = {e["wallet"] for e in events if ts - 500 <= e["ts"] <= ts and e["side"] == "buy" and e.get("wallet")}
    prior_wallets = {e["wallet"] for e in events if ts - 5000 <= e["ts"] < ts - 500 and e["side"] == "buy" and e.get("wallet")}
    first_half = sum(e["quote_sol"] for e in events if ts - 5000 <= e["ts"] < ts - 2500 and e["side"] == "buy")
    second_half = sum(e["quote_sol"] for e in events if ts - 2500 <= e["ts"] <= ts and e["side"] == "buy")
    features["new_wallets_500_vs_prior5s"] = len(current_wallets - prior_wallets)
    features["repeat_wallets_500"] = len(current_wallets & prior_wallets)
    features["second_to_first_sol_ratio"] = second_half / max(0.001, first_half)
    features["prior_gross_500_1000"] = sum(e["quote_sol"] for e in events if ts - 1000 <= e["ts"] < ts - 500 and e["side"] == "buy")
    features["prior_gross_1000_5000"] = sum(e["quote_sol"] for e in events if ts - 5000 <= e["ts"] < ts - 1000 and e["side"] == "buy")
    features["near_high_5s"] = finite(features.get("w5000.drawdown_from_high_pct")) and features["w5000.drawdown_from_high_pct"] >= -3
    features["pullback_10s"] = finite(features.get("w10000.drawdown_from_high_pct")) and -25 <= features["w10000.drawdown_from_high_pct"] <= -4
    features["consolidating_10s"] = finite(features.get("w10000.range_width_pct")) and features["w10000.range_width_pct"] <= 25
    features["sell_absorbed_5s"] = features.get("w5000.sell_count", 0) >= 1 and (features.get("w5000.drawdown_from_high_pct") or 0) >= -10
    return features


def fv(features, key, default=0.0):
    value = features.get(key)
    return value if finite(value) else default


def fmaybe(features, key):
    value = features.get(key)
    return value if finite(value) else None


def leq_if_present(value, cutoff):
    return finite(value) and value <= cutoff


def rule_library():
    return [
        {
            "name": "opening_fast_distributed_500ms_v1",
            "family": "OPENING / CREATE QUALITY",
            "definition": "fixed_500ms, age<=1000, mc<=10000, buy_count500>=3, gross500>=2, unique500>=3, top1_500<=60%, fb<=5000bps",
            "earliest_decision": "500ms",
            "complexity": "low",
            "test": lambda x: x["snapshot_kind"] == "FIXED_500MS"
            and x["age_ms"] <= 1000
            and x["mc_usd"] <= 10000
            and fv(x, "w500.buy_count") >= 3
            and fv(x, "w500.gross_buy_sol") >= 2
            and fv(x, "w500.unique_buyers") >= 3
            and leq_if_present(x.get("w500.top1_share"), 0.60)
            and leq_if_present(x.get("first_buyer_quote_share_bps"), 5000),
        },
        {
            "name": "fast100_fb_le_5000bps_benchmark",
            "family": "OPENING / CREATE QUALITY",
            "definition": "buy_count100>=2 AND first_buyer_quote_share_bps<=5000",
            "earliest_decision": "event-driven",
            "complexity": "low",
            "test": lambda x: fv(x, "w100.buy_count") >= 2 and leq_if_present(x.get("first_buyer_quote_share_bps"), 5000),
        },
        {
            "name": "durable_3s_distributed_flow_v1",
            "family": "DURABLE EARLY CONTINUATION",
            "definition": "fixed_3s/5s, age 2-6s, gross5s>=5, unique5s>=5, top1_5s<=50%, sell_count5s<=1, drawdown5s>=-10%",
            "earliest_decision": "3s",
            "complexity": "low",
            "test": lambda x: x["snapshot_kind"] in ("FIXED_3000MS", "FIXED_5000MS")
            and 2000 <= x["age_ms"] <= 6000
            and fv(x, "w5000.gross_buy_sol") >= 5
            and fv(x, "w5000.unique_buyers") >= 5
            and leq_if_present(x.get("w5000.top1_share"), 0.50)
            and fv(x, "w5000.sell_count") <= 1
            and (x.get("w5000.drawdown_from_high_pct") or 0) >= -10,
        },
        {
            "name": "sell_absorption_reclaim_v1",
            "family": "SELL ABSORPTION",
            "definition": "age 3-20s, sell_count10s>=1, gross5s>=4, drawdown5s>=-8%, gross1s>=1.5, unique1s>=2",
            "earliest_decision": "event-driven",
            "complexity": "medium",
            "test": lambda x: 3000 <= x["age_ms"] <= 20000
            and fv(x, "w10000.sell_count") >= 1
            and fv(x, "w5000.gross_buy_sol") >= 4
            and (x.get("w5000.drawdown_from_high_pct") or 0) >= -8
            and fv(x, "w1000.gross_buy_sol") >= 1.5
            and fv(x, "w1000.unique_buyers") >= 2,
        },
        {
            "name": "pullback_reclaim_v1",
            "family": "PULLBACK / RECLAIM",
            "definition": "age 5-30s, 10s pullback -25%..-4%, price_lift10s>=15%, gross1s>=2, unique1s>=2",
            "earliest_decision": "event-driven",
            "complexity": "medium",
            "test": lambda x: 5000 <= x["age_ms"] <= 30000
            and finite(x.get("w10000.drawdown_from_high_pct"))
            and -25 <= x["w10000.drawdown_from_high_pct"] <= -4
            and fv(x, "w10000.price_lift_pct") >= 15
            and fv(x, "w1000.gross_buy_sol") >= 2
            and fv(x, "w1000.unique_buyers") >= 2,
        },
        {
            "name": "consolidation_breakout_v1",
            "family": "CONSOLIDATION / BREAKOUT",
            "definition": "age 10-30s, range10s<=25%, gross1s>=2, unique1s>=2, drawdown10s>=-3%",
            "earliest_decision": "10s",
            "complexity": "medium",
            "test": lambda x: 10000 <= x["age_ms"] <= 30000
            and leq_if_present(x.get("w10000.range_width_pct"), 25)
            and fv(x, "w1000.gross_buy_sol") >= 2
            and fv(x, "w1000.unique_buyers") >= 2
            and (x.get("w10000.drawdown_from_high_pct") or 0) >= -3,
        },
        {
            "name": "flow_price_efficiency_v1",
            "family": "FLOW / PRICE EFFICIENCY",
            "definition": "gross5s>=5, price_lift5s 8-60%, response<=10 pct/SOL, top1_5s<=55%, unique5s>=4",
            "earliest_decision": "event-driven",
            "complexity": "low",
            "test": lambda x: fv(x, "w5000.gross_buy_sol") >= 5
            and finite(x.get("w5000.price_lift_pct"))
            and 8 <= x["w5000.price_lift_pct"] <= 60
            and leq_if_present(x.get("w5000.price_response_per_sol"), 10)
            and leq_if_present(x.get("w5000.top1_share"), 0.55)
            and fv(x, "w5000.unique_buyers") >= 4,
        },
        {
            "name": "reacceleration_v1_benchmark",
            "family": "SECOND-WAVE / REACCELERATION",
            "definition": "bc500>=3 AND gross500>=2 AND gross5s>=5 AND gross500/gross5s 25%-80%",
            "earliest_decision": "event-driven",
            "complexity": "low",
            "test": lambda x: fv(x, "w500.buy_count") >= 3
            and fv(x, "w500.gross_buy_sol") >= 2
            and fv(x, "w5000.gross_buy_sol") >= 5
            and 0.25 <= fv(x, "w500.gross_buy_sol") / max(0.001, fv(x, "w5000.gross_buy_sol")) <= 0.80,
        },
        {
            "name": "curve_stage_mid_early_v1",
            "family": "CURVE-STAGE / GRADUATION",
            "definition": "age 10-30s, mc 5000-15000, gross10s>=8, unique10s>=6, top1_10s<=50%, drawdown10s>=-8%",
            "earliest_decision": "10s",
            "complexity": "medium",
            "test": lambda x: 10000 <= x["age_ms"] <= 30000
            and 5000 <= x["mc_usd"] <= 15000
            and fv(x, "w10000.gross_buy_sol") >= 8
            and fv(x, "w10000.unique_buyers") >= 6
            and leq_if_present(x.get("w10000.top1_share"), 0.50)
            and (x.get("w10000.drawdown_from_high_pct") or 0) >= -8,
        },
        {
            "name": "quiet_regime_distributed_v1",
            "family": "REGIME-CONDITIONED SETUP",
            "definition": "launches/min<=25, gross5s>=4, unique5s>=4, top1_5s<=50%, sell_count5s<=1, mc<=12000",
            "earliest_decision": "event-driven",
            "complexity": "medium",
            "test": lambda x: finite(x.get("launches_per_minute"))
            and x["launches_per_minute"] <= 25
            and fv(x, "w5000.gross_buy_sol") >= 4
            and fv(x, "w5000.unique_buyers") >= 4
            and leq_if_present(x.get("w5000.top1_share"), 0.50)
            and fv(x, "w5000.sell_count") <= 1
            and x["mc_usd"] <= 12000,
        },
    ]


def score(entry_ts, selected_mc, marks, entry_delay_ms, sellable_ms, entry_stress_pct):
    if not finite(entry_ts) or not finite(selected_mc) or selected_mc <= 0:
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
    durations = {20: 0, 15: 0, 10: 0}
    above = {20: False, 15: False, 10: False}
    starts = {20: None, 15: None, 10: None}
    last_ts = None
    return_at_first_sellable = None
    for ts, mc in marks:
        if ts < entry_ts or ts > entry_ts + HOLD_MS:
            continue
        roi = ((mc / entry_mc) - 1.0) * 100.0 - ROUND_TRIP_COST_PCT
        if roi >= TP and first_tp_before is None:
            first_tp_before = ts
        if ts < sellable_ts:
            continue
        if return_at_first_sellable is None:
            return_at_first_sellable = roi
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
            "entry_ts": entry_ts,
            "entry_mc": entry_mc,
            "first_sellable_ts": sellable_ts,
            "return_at_first_sellable": None,
            "max_return_after_sellable": None,
            "min_return_after_sellable": None,
            "pre_sellable_only_winner": first_tp_before is not None,
            "post_sellable_tp_reachable": False,
            "time_above_20_after_sellable_ms": 0,
            "time_above_15_after_sellable_ms": 0,
            "time_above_10_after_sellable_ms": 0,
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
        "entry_mc": entry_mc,
        "first_sellable_ts": sellable_ts,
        "return_at_first_sellable": return_at_first_sellable,
        "max_return_after_sellable": max_after if finite(max_after) else None,
        "min_return_after_sellable": min_after if finite(min_after) else None,
        "time_to_tp_ms": first_tp_after - entry_ts if first_tp_after else None,
        "time_to_sl_ms": first_sl_after - entry_ts if first_sl_after else None,
        "post_sellable_tp_reachable": first_tp_after is not None,
        "pre_sellable_only_winner": first_tp_before is not None and first_tp_after is None,
        "time_above_20_after_sellable_ms": durations[20],
        "time_above_15_after_sellable_ms": durations[15],
        "time_above_10_after_sellable_ms": durations[10],
    }


def label_outcome(primary, stress5, stress10):
    pnl = primary.get("pnl")
    if primary.get("pre_sellable_only_winner"):
        return "PRE_SELLABLE_ONLY_WINNER"
    if not finite(pnl):
        return "UNRESOLVED"
    if pnl > 0 and finite(stress5.get("pnl")) and stress5["pnl"] > 0:
        return "ROBUST_EXECUTABLE_WINNER"
    if pnl > 0:
        return "FRAGILE_EXECUTABLE_WINNER"
    if primary.get("outcome") == "SL":
        return "HARD_STOP_LOSER"
    if primary.get("outcome") == "max_hold":
        return "MAX_HOLD_LOSER"
    return "OTHER_LOSER"


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
        "median_return_pct": median(pnls),
        "pnl_at_1sol": sum(pnls) / 100.0,
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
    }


def split_summary(rows):
    xs = sorted(rows, key=lambda x: x["ts"])
    a = len(xs) // 2
    b = len(xs) * 3 // 4
    return {
        "train": summarize(xs[:a]),
        "validation": summarize(xs[a:b]),
        "holdout": summarize(xs[b:]),
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


def feature_forensics(rows, fields):
    labels = ["ROBUST_EXECUTABLE_WINNER", "FRAGILE_EXECUTABLE_WINNER", "HARD_STOP_LOSER", "MAX_HOLD_LOSER", "PRE_SELLABLE_ONLY_WINNER"]
    out = {}
    for field in fields:
        out[field] = {}
        for label in labels:
            vals = [r.get("features", {}).get(field) for r in rows if r.get("label") == label]
            vals = [v for v in vals if finite(v)]
            out[field][label] = {
                "coverage": len(vals),
                "median": median(vals),
                "p25": quantile(vals, 0.25),
                "p75": quantile(vals, 0.75),
            }
    return out


def univariate_importance(rows, fields):
    robust = [r for r in rows if r.get("label") == "ROBUST_EXECUTABLE_WINNER"]
    bad = [r for r in rows if r.get("label") in ("HARD_STOP_LOSER", "FRAGILE_EXECUTABLE_WINNER")]
    scores = []
    for field in fields:
        rv = [r.get("features", {}).get(field) for r in robust]
        bv = [r.get("features", {}).get(field) for r in bad]
        rv = [v for v in rv if finite(v)]
        bv = [v for v in bv if finite(v)]
        if len(rv) < 50 or len(bv) < 50:
            continue
        spread = (quantile(rv + bv, 0.75) or 0) - (quantile(rv + bv, 0.25) or 0)
        score = abs((median(rv) or 0) - (median(bv) or 0)) / max(1e-9, abs(spread))
        scores.append({"feature": field, "robust_median": median(rv), "bad_median": median(bv), "robust_n": len(rv), "bad_n": len(bv), "stability_score": score})
    scores.sort(key=lambda x: x["stability_score"], reverse=True)
    return scores[:30]


def family_baseline(rows):
    out = {}
    for key in [500, 1000, 2000, 3000, 5000, 10000, 20000, 30000]:
        subset = [r for r in rows if r.get("snapshot_kind") == f"FIXED_{key}MS"]
        out[f"{key}ms"] = summarize(subset)
    return out


def rule_candidate_output(rule, rows, span_hours):
    primary = summarize(rows)
    splits = split_summary(rows)
    max_open = capacity_one(rows)
    p50 = summarize([r["p50"] for r in rows])
    p90 = summarize([r["p90"] for r in rows])
    stress5 = summarize([r["stress5"] for r in rows])
    stress10 = summarize([r["stress10"] for r in rows])
    positives = sum(1 for split in splits.values() if split["pf"] > 1 and (split.get("expectancy_pct") or -999) > 0)
    return {
        "name": rule["name"],
        "family": rule["family"],
        "definition": rule["definition"],
        "earliest_decision": rule["earliest_decision"],
        "implementation_complexity": rule["complexity"],
        "n": len(rows),
        "signals_per_hour": len(rows) / max(0.001, span_hours or 0),
        "p75_primary": primary,
        "p50_first_sellable": p50,
        "p90_first_sellable": p90,
        "train": splits["train"],
        "validation": splits["validation"],
        "holdout": splits["holdout"],
        "entry_stress_5": stress5,
        "entry_stress_10": stress10,
        "max_open1": max_open,
        "positive_chronological_periods": positives,
        "confidence": confidence(primary, splits, stress5, max_open, len(rows)),
    }


def confidence(primary, splits, stress5, max_open, n):
    if n < 200:
        return "low_sample"
    if (
        splits["validation"]["pf"] >= 1.4
        and splits["holdout"]["pf"] >= 1.3
        and (splits["validation"].get("expectancy_pct") or -999) > 0
        and (splits["holdout"].get("expectancy_pct") or -999) > 0
        and stress5["pf"] > 1.0
        and max_open["pf"] > 1.0
        and primary["pf_ex_top3"] and primary["pf_ex_top3"] > 1.0
    ):
        return "promotion_quality_candidate"
    if splits["validation"]["pf"] > 1.15 and splits["holdout"]["pf"] > 1.10 and max_open["pf"] > 1.0:
        return "promising_needs_audit"
    return "research_only"


def maybe_emit_snapshot(row, feats, rules, qualified, snapshot_counts, sample_out):
    snapshot_counts[feats["snapshot_kind"]] += 1
    if sample_out and snapshot_counts[feats["snapshot_kind"]] <= 25:
        sample_out.write(json.dumps(compact(feats), separators=(",", ":")) + "\n")
    for rule in rules:
        if feats["mint"] not in qualified[rule["name"]] and rule["test"](feats):
            qualified[rule["name"]][feats["mint"]] = {
                "mint": feats["mint"],
                "ts": feats["ts"],
                "mc": feats["mc_usd"],
                "snapshot_kind": feats["snapshot_kind"],
                "snapshot_age_ms": feats["snapshot_age_ms"],
                "features": {k: v for k, v in feats.items() if k not in ("mint", "ts")},
            }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capsules", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--limit-rows", type=int, default=0)
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    rules = rule_library()
    qualified = {rule["name"]: {} for rule in rules}
    flows = defaultdict(deque)
    first_seen = {}
    emitted_fixed = defaultdict(set)
    emitted_events = defaultdict(set)
    unique_mints = set()
    global_first_seen = deque()
    rows_scanned = flow_rows = buy_rows = sell_rows = snapshots_built = 0
    time_min = time_max = None
    started = time.time()
    snapshot_counts = Counter()
    sample_path = os.path.join(args.out_dir, "prospective_snapshot_samples.jsonl.gz")

    with gzip.open(sample_path, "wt", encoding="utf-8") as sample_out:
        for row in read_jsonl(args.capsules):
            rows_scanned += 1
            if args.limit_rows and rows_scanned > args.limit_rows:
                break
            mint = mint_of(row)
            ts = ts_of(row)
            mc = mc_of(row)
            side = side_of(row)
            if not mint or not finite(ts):
                continue
            ts = int(ts)
            unique_mints.add(mint)
            time_min = ts if time_min is None else min(time_min, ts)
            time_max = ts if time_max is None else max(time_max, ts)
            if mint not in first_seen:
                first_seen[mint] = ts
                global_first_seen.append((ts, mint))
            while global_first_seen and global_first_seen[0][0] < ts - 60_000:
                global_first_seen.popleft()
            if not finite(mc) or not (0 < float(mc) <= MAX_MC_USD) or side not in ("buy", "sell"):
                continue
            event = build_event(row, ts, mc, side)
            dq = flows[mint]
            dq.append(event)
            while dq and dq[0]["ts"] < ts - HOLD_MS:
                dq.popleft()
            flow_rows += 1
            buy_rows += 1 if side == "buy" else 0
            sell_rows += 1 if side == "sell" else 0
            age = creation_age_ms(row, first_seen[mint], ts)
            global_state = {
                "launches_per_minute": len({m for _, m in global_first_seen}),
                "active_flow_mints": len(flows),
            }
            for age_ms in FIXED_AGES_MS:
                if age >= age_ms and age_ms not in emitted_fixed[mint]:
                    emitted_fixed[mint].add(age_ms)
                    feats = feature_row(dq, row, ts, mc, first_seen[mint], f"FIXED_{age_ms}MS", age_ms, global_state)
                    snapshots_built += 1
                    maybe_emit_snapshot(row, feats, rules, qualified, snapshot_counts, sample_out)
            if side == "buy":
                # Event-driven states are coarse prospective semantics, not tuned thresholds.
                event_kinds = []
                quick = window(dq, ts, 1000)
                w500 = window(dq, ts, 500)
                w5000 = window(dq, ts, 5000)
                w10000 = window(dq, ts, 10000)
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
                    feats = feature_row(dq, row, ts, mc, first_seen[mint], kind, age, global_state)
                    snapshots_built += 1
                    maybe_emit_snapshot(row, feats, rules, qualified, snapshot_counts, sample_out)
            elif side == "sell" and "FIRST_LARGE_SELLER" not in emitted_events[mint]:
                emitted_events[mint].add("FIRST_LARGE_SELLER")
                feats = feature_row(dq, row, ts, mc, first_seen[mint], "FIRST_LARGE_SELLER", age, global_state)
                snapshots_built += 1
                maybe_emit_snapshot(row, feats, rules, qualified, snapshot_counts, sample_out)
            if rows_scanned % 250_000 == 0:
                stale = ts - HOLD_MS
                for stale_mint in [key for key, value in flows.items() if not value or value[-1]["ts"] < stale]:
                    del flows[stale_mint]
                print(json.dumps(compact({
                    "progress": "pass1",
                    "rows": rows_scanned,
                    "flow_rows": flow_rows,
                    "buy_rows": buy_rows,
                    "sell_rows": sell_rows,
                    "snapshots_built": snapshots_built,
                    "active_flow_mints": len(flows),
                    "qualified": {k: len(v) for k, v in qualified.items()},
                    "elapsed_sec": time.time() - started,
                })), flush=True)

    wanted = {}
    for rows in qualified.values():
        for mint, qrow in rows.items():
            wanted[mint] = min(wanted.get(mint, qrow["ts"]), qrow["ts"])
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
            print(json.dumps(compact({
                "progress": "pass2",
                "rows": rows_pass2,
                "marks_used": marks_used,
                "elapsed_sec": time.time() - started,
            })), flush=True)
    for arr in marks.values():
        arr.sort()

    outcome_path = os.path.join(args.out_dir, "candidate_outcomes.jsonl.gz")
    feature_fields = [
        "mc_usd", "age_ms", "first_buyer_quote_share_bps", "launches_per_minute",
        "w100.buy_count", "w100.gross_buy_sol", "w100.unique_buyers", "w100.top1_share",
        "w500.buy_count", "w500.gross_buy_sol", "w500.unique_buyers", "w500.top1_share", "w500.median_interbuy_ms",
        "w1000.gross_buy_sol", "w1000.unique_buyers", "w1000.price_response_per_sol",
        "w5000.gross_buy_sol", "w5000.net_buy_sol", "w5000.sell_count", "w5000.unique_buyers", "w5000.unique_sellers",
        "w5000.top1_share", "w5000.top3_share", "w5000.price_lift_pct", "w5000.drawdown_from_high_pct", "w5000.range_width_pct",
        "w10000.gross_buy_sol", "w10000.sell_count", "w10000.unique_buyers", "w10000.drawdown_from_high_pct", "w10000.range_width_pct",
        "new_wallets_500_vs_prior5s", "repeat_wallets_500", "second_to_first_sol_ratio",
    ]
    report = {
        "dataset": {
            "capsules": args.capsules,
            "rows_scanned": rows_scanned,
            "flow_rows": flow_rows,
            "buy_rows": buy_rows,
            "sell_rows": sell_rows,
            "unique_mints_observed": len(unique_mints),
            "time_start_ms": time_min,
            "time_end_ms": time_max,
            "span_hours": ((time_max - time_min) / 3_600_000) if time_min and time_max else None,
            "snapshots_built": snapshots_built,
            "snapshot_counts": dict(snapshot_counts),
            "marks_used": marks_used,
            "entry_delay_ms": ENTRY_DELAY_MS,
            "primary_first_sellable_ms": SELLABLE_P75_MS,
            "sensitivity_first_sellable_ms": [SELLABLE_P50_MS, SELLABLE_P75_MS, SELLABLE_P90_MS],
        },
        "feature_inventory": {
            "available": feature_fields,
            "blocked_or_partial": {
                "sell_sol": "often unavailable because sellQuoteLamports is usually null; sell_count and unique_sellers are reliable, sell SOL is partial",
                "participant_historical_quality": "not computed in this atlas because prospectively copyable wallet-quality labels need a separate historical wallet table",
                "creator_launch_context": "not computed here; raw creator/router/funder identity disambiguation is not safe enough in this corpus pass",
                "parquet": "pyarrow is not installed on VPS, so large derived tables are gzipped JSONL instead",
            },
        },
        "rules": {},
        "leaderboard": [],
        "fixed_age_baseline": {},
        "feature_forensics_train_only": {},
        "univariate_feature_importance_train_only": [],
    }
    all_outcomes_for_features = []
    with gzip.open(outcome_path, "wt", encoding="utf-8") as out:
        for rule in rules:
            rows = []
            for mint, qrow in qualified[rule["name"]].items():
                mark_rows = marks.get(mint, [])
                primary = score(qrow["ts"], qrow["mc"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P75_MS, 0)
                stress5 = score(qrow["ts"], qrow["mc"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P75_MS, 5)
                stress10 = score(qrow["ts"], qrow["mc"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P75_MS, 10)
                row = {
                    "rule": rule["name"],
                    "family": rule["family"],
                    "mint": mint,
                    "ts": qrow["ts"],
                    "mc": qrow["mc"],
                    "snapshot_kind": qrow["snapshot_kind"],
                    "snapshot_age_ms": qrow["snapshot_age_ms"],
                    "features": qrow["features"],
                    **primary,
                    "stress5_pnl": stress5.get("pnl"),
                    "stress10_pnl": stress10.get("pnl"),
                    "label": label_outcome(primary, stress5, stress10),
                    "p50": {"mint": mint, "ts": qrow["ts"], **score(qrow["ts"], qrow["mc"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P50_MS, 0)},
                    "p90": {"mint": mint, "ts": qrow["ts"], **score(qrow["ts"], qrow["mc"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P90_MS, 0)},
                    "stress5": {"mint": mint, "ts": qrow["ts"], **stress5},
                    "stress10": {"mint": mint, "ts": qrow["ts"], **stress10},
                }
                rows.append(row)
                out.write(json.dumps(compact(row), separators=(",", ":")) + "\n")
            report["rules"][rule["name"]] = rule_candidate_output(rule, rows, report["dataset"]["span_hours"])
            report["rules"][rule["name"]]["label_counts"] = dict(Counter(r["label"] for r in rows))
            all_outcomes_for_features.extend(rows)
            primary = report["rules"][rule["name"]]["p75_primary"]
            validation = report["rules"][rule["name"]]["validation"]
            holdout = report["rules"][rule["name"]]["holdout"]
            stress5 = report["rules"][rule["name"]]["entry_stress_5"]
            max_open = report["rules"][rule["name"]]["max_open1"]
            report["leaderboard"].append({
                "name": rule["name"],
                "family": rule["family"],
                "definition": rule["definition"],
                "decision_age": rule["earliest_decision"],
                "n": len(rows),
                "signals_per_hour": len(rows) / max(0.001, report["dataset"]["span_hours"] or 0),
                "validation_pf": validation["pf"],
                "holdout_pf": holdout["pf"],
                "expectancy_pct": primary["expectancy_pct"],
                "entry_stress_5_pf": stress5["pf"],
                "p75_pf": primary["pf"],
                "max_open1_pf": max_open["pf"],
                "winner_durability_ms_above20": primary["median_time_above_20_after_sellable_ms"],
                "pf_ex_top1": primary["pf_ex_top1"],
                "pf_ex_top3": primary["pf_ex_top3"],
                "temporal_consistency": report["rules"][rule["name"]]["positive_chronological_periods"],
                "implementation_complexity": rule["complexity"],
                "confidence": report["rules"][rule["name"]]["confidence"],
            })
    report["leaderboard"].sort(
        key=lambda r: (
            {"promotion_quality_candidate": 3, "promising_needs_audit": 2, "research_only": 1, "low_sample": 0}.get(r["confidence"], 0),
            r["max_open1_pf"] or 0,
            r["entry_stress_5_pf"] or 0,
            r["validation_pf"] or 0,
            r["holdout_pf"] or 0,
        ),
        reverse=True,
    )
    report["fixed_age_baseline"] = family_baseline(all_outcomes_for_features)
    train_cut = sorted(all_outcomes_for_features, key=lambda x: x["ts"])
    train_cut = train_cut[: len(train_cut) // 2]
    report["feature_forensics_train_only"] = feature_forensics(train_cut, feature_fields)
    report["univariate_feature_importance_train_only"] = univariate_importance(train_cut, feature_fields)
    strong = [r for r in report["leaderboard"] if r["confidence"] == "promotion_quality_candidate"]
    promising = [r for r in report["leaderboard"] if r["confidence"] == "promising_needs_audit"]
    if len(strong) >= 2:
        classification = "MULTIPLE_PROMISING_EXECUTABLE_FAMILIES"
    elif len(strong) == 1:
        classification = "STRONG_NEW_EXECUTABLE_FAMILY_FOUND"
    elif promising:
        classification = "PROMISING_FEATURES_NEED_RULE_DERIVATION"
    elif rows_scanned == 0 or not all_outcomes_for_features:
        classification = "DATA_FEATURE_BLOCKER"
    else:
        classification = "NO_ROBUST_EXECUTABLE_ALPHA_FOUND"
    report["final_classification"] = classification
    report["outputs"] = {
        "prospective_snapshot_samples": sample_path,
        "candidate_outcomes": outcome_path,
        "report": os.path.join(args.out_dir, "report.json"),
    }
    with open(os.path.join(args.out_dir, "frozen_candidate_definitions.json"), "w", encoding="utf-8") as out:
        json.dump(compact([{k: v for k, v in rule.items() if k != "test"} for rule in rules]), out, indent=2)
    with open(os.path.join(args.out_dir, "report.json"), "w", encoding="utf-8") as out:
        json.dump(compact(report), out, indent=2)
    print(json.dumps(compact({
        "done": True,
        "out_dir": args.out_dir,
        "dataset": report["dataset"],
        "final_classification": classification,
        "leaderboard": report["leaderboard"][:10],
    }), separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
