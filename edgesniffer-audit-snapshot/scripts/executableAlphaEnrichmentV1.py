#!/usr/bin/env python3
import argparse
import gzip
import heapq
import json
import math
import os
import time
from collections import Counter, defaultdict, deque


TP = 20.0
SL = -20.0
HOLD_MS = 120_000
ENTRY_DELAY_MS = 25
SELLABLE_P75_MS = 1476
ROUND_TRIP_COST_PCT = 2.5
MAX_MC_USD = 250_000
SNAPSHOT_AGES = [1000, 2000]
WINDOWS = [100, 250, 500, 1000, 2000, 5000, 10000]


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
    return row.get("candidate_ready_ts_ms") or row.get("decode_complete_ts_ms") or row.get("local_receive_ts_ms")


def mc_of(row):
    return (row.get("canonical_state") or {}).get("market_cap_usd") or (row.get("candidate") or {}).get("market_cap_usd")


def side_of(row):
    return (row.get("raw_record") or {}).get("side") or row.get("side")


def wallet_of(row):
    return (row.get("raw_record") or {}).get("wallet") or row.get("wallet")


def slot_of(row):
    return (row.get("raw_record") or {}).get("slot") or row.get("source_slot")


def signature_of(row):
    return (row.get("raw_record") or {}).get("signature") or row.get("transaction_signature")


def quote_sol(row):
    raw = row.get("raw_record") or {}
    if finite(raw.get("quoteLamports")):
        return float(raw["quoteLamports"]) / 1_000_000_000
    if finite(row.get("quote_lamports")):
        return float(row["quote_lamports"]) / 1_000_000_000
    return float(row.get("quote_sol") or 0.0)


def sell_quote_status(row):
    raw = row.get("raw_record") or {}
    if finite(raw.get("sellQuoteLamports")):
        return "EXACT_SELL_QUOTE", float(raw["sellQuoteLamports"]) / 1_000_000_000
    if finite(row.get("sell_quote_lamports")):
        return "EXACT_SELL_QUOTE", float(row["sell_quote_lamports"]) / 1_000_000_000
    # The corpus exposes sell token amount, but not enough reserve delta/balance delta
    # evidence to treat reconstructed SOL as primary.
    if finite(raw.get("sellTokenAmountRaw")) or finite(row.get("sell_token_amount_raw")):
        return "APPROXIMATE_NOT_FOR_PRIMARY_USE", None
    return "UNRECOVERABLE", None


def first_buyer_bps(row):
    value = (row.get("candidate") or {}).get("first_buyer_quote_share_bps")
    return float(value) if finite(value) else None


def build_event(row, ts, mc, side):
    status, sell_sol = sell_quote_status(row)
    return {
        "ts": int(ts),
        "side": side,
        "quote_sol": quote_sol(row) if side == "buy" else 0.0,
        "sell_sol": sell_sol,
        "sell_quote_status": status if side == "sell" else None,
        "wallet": wallet_of(row),
        "sig": signature_of(row),
        "slot": slot_of(row),
        "mc": float(mc),
    }


def window(events, ts, width_ms):
    start = ts - width_ms
    buys = []
    sells = []
    buy_by_wallet = Counter()
    sell_by_wallet = Counter()
    sigs = set()
    slots = set()
    mcs = []
    exact_sell_sol = 0.0
    exact_sell_count = 0
    approx_sell_count = 0
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
            buy_by_wallet[event["wallet"] or "unknown"] += event["quote_sol"]
            if event.get("sig"):
                sigs.add(event["sig"])
        elif event["side"] == "sell":
            sells.append(event)
            if event["sell_sol"] is not None:
                exact_sell_count += 1
                exact_sell_sol += event["sell_sol"]
                sell_by_wallet[event["wallet"] or "unknown"] += event["sell_sol"]
            else:
                approx_sell_count += 1
                sell_by_wallet[event["wallet"] or "unknown"] += 1.0
            if event.get("sig"):
                sigs.add(event["sig"])
    gross = sum(e["quote_sol"] for e in buys)
    buy_sizes = sorted((e["quote_sol"] for e in buys), reverse=True)
    exact_sell_sizes = sorted((e["sell_sol"] for e in sells if e["sell_sol"] is not None), reverse=True)
    intervals = [buys[i - 1]["ts"] - buys[i]["ts"] for i in range(1, len(buys))]
    local_high = max(mcs) if mcs else None
    local_low = min(mcs) if mcs else None
    current_mc = mcs[0] if mcs else None
    price_lift = ((current_mc / local_low - 1.0) * 100.0) if current_mc and local_low and local_low > 0 else None
    drawdown = ((current_mc / local_high - 1.0) * 100.0) if current_mc and local_high else None
    exact_net = gross - exact_sell_sol if exact_sell_count else None
    return {
        "buy_count": len(buys),
        "gross_buy_sol": gross,
        "sell_count": len(sells),
        "exact_sell_count": exact_sell_count,
        "approx_sell_count": approx_sell_count,
        "gross_sell_sol_exact": exact_sell_sol if exact_sell_count else None,
        "net_buy_sol_exact": exact_net,
        "unique_buyers": len(buy_by_wallet),
        "unique_sellers": len(sell_by_wallet),
        "tx_count": len(sigs),
        "slot_count": len(slots),
        "top1_share": (max(buy_by_wallet.values()) / gross) if buy_by_wallet and gross > 0 else None,
        "top3_share": (sum(sorted(buy_by_wallet.values(), reverse=True)[:3]) / gross) if buy_by_wallet and gross > 0 else None,
        "seller_top1_share": (max(sell_by_wallet.values()) / sum(sell_by_wallet.values())) if sell_by_wallet and sum(sell_by_wallet.values()) > 0 else None,
        "median_buy_sol": median(buy_sizes),
        "median_sell_sol_exact": median(exact_sell_sizes),
        "largest_buy_sol": buy_sizes[0] if buy_sizes else 0.0,
        "largest_sell_sol_exact": exact_sell_sizes[0] if exact_sell_sizes else None,
        "median_interbuy_ms": median(intervals),
        "local_high_mc": local_high,
        "local_low_mc": local_low,
        "drawdown_from_high_pct": drawdown,
        "price_lift_pct": price_lift,
        "range_width_pct": ((local_high / local_low - 1.0) * 100.0) if local_high and local_low and local_low > 0 else None,
        "price_response_per_sol": (price_lift / gross) if finite(price_lift) and gross > 0 else None,
        "sell_count_damage_pct": ((drawdown or 0.0) / len(sells)) if sells else None,
        "buy_sell_count_ratio": len(buys) / max(1, len(sells)),
        "buy_sell_sol_ratio_exact": gross / exact_sell_sol if exact_sell_sol > 0 else None,
    }


def participant_map(events, ts, width_ms):
    start = ts - width_ms
    out = Counter()
    arrival = {}
    for event in events:
        if start <= event["ts"] <= ts and event["side"] == "buy" and event.get("wallet"):
            out[event["wallet"]] += event["quote_sol"]
            arrival[event["wallet"]] = max(arrival.get(event["wallet"], 0), event["ts"])
    return [{"wallet": w, "quote_sol": q, "last_buy_age_ms": ts - arrival[w]} for w, q in out.items()]


def flatten(prefix, values, out):
    for key, value in values.items():
        out[f"{prefix}.{key}"] = value


def absorption_features(events, ts):
    sells = [e for e in events if e["side"] == "sell" and e["ts"] <= ts]
    if not sells:
        return {
            "meaningful_sell_before_snapshot": False,
            "last_sell_age_ms": None,
            "price_damage_after_last_sell_pct": None,
            "price_recovery_after_last_sell_pct": None,
            "buy_sol_after_last_sell": 0.0,
            "new_buyers_after_last_sell": 0,
            "price_retained_vs_pre_sell_pct": None,
        }
    last_sell = sells[-1]
    pre = [e["mc"] for e in events if e["ts"] <= last_sell["ts"]]
    post = [e for e in events if last_sell["ts"] < e["ts"] <= ts]
    pre_high = max(pre) if pre else last_sell["mc"]
    post_low = min([e["mc"] for e in post], default=last_sell["mc"])
    post_high = max([e["mc"] for e in post], default=last_sell["mc"])
    buyers_after = {e["wallet"] for e in post if e["side"] == "buy" and e.get("wallet")}
    buy_sol_after = sum(e["quote_sol"] for e in post if e["side"] == "buy")
    current_mc = events[-1]["mc"] if events else None
    return {
        "meaningful_sell_before_snapshot": True,
        "last_sell_age_ms": ts - last_sell["ts"],
        "price_damage_after_last_sell_pct": ((post_low / pre_high - 1.0) * 100.0) if pre_high else None,
        "price_recovery_after_last_sell_pct": ((post_high / post_low - 1.0) * 100.0) if post_low else None,
        "buy_sol_after_last_sell": buy_sol_after,
        "new_buyers_after_last_sell": len(buyers_after),
        "price_retained_vs_pre_sell_pct": ((current_mc / pre_high) * 100.0) if current_mc and pre_high else None,
    }


def make_snapshot(row, events, ts, mc, first_seen_ts, age_ms):
    features = {
        "mint": mint_of(row),
        "ts": int(ts),
        "snapshot_age_ms": age_ms,
        "mc_usd": float(mc),
        "token_age_ms": int(ts - first_seen_ts),
        "first_buyer_quote_share_bps": first_buyer_bps(row),
    }
    for width in WINDOWS:
        flatten(f"w{width}", window(events, ts, width), features)
    current_wallets = {e["wallet"] for e in events if ts - 500 <= e["ts"] <= ts and e["side"] == "buy" and e.get("wallet")}
    prior_wallets = {e["wallet"] for e in events if ts - 5000 <= e["ts"] < ts - 500 and e["side"] == "buy" and e.get("wallet")}
    features["new_wallets_500_vs_prior5s"] = len(current_wallets - prior_wallets)
    features["repeat_wallets_500"] = len(current_wallets & prior_wallets)
    for key, value in absorption_features(events, ts).items():
        features[f"absorption.{key}"] = value
    participants = participant_map(events, ts, 2000)
    return {"mint": features["mint"], "ts": int(ts), "mc": float(mc), "snapshot_age_ms": age_ms, "features": features, "participants": participants}


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
            "entry_ts": entry_ts,
            "entry_mc": entry_mc,
            "first_sellable_ts": sellable_ts,
            "return_at_first_sellable": None,
            "max_return_after_sellable": None,
            "min_return_after_sellable": None,
            "post_sellable_tp_reachable": False,
            "pre_sellable_only_winner": first_tp_before is not None,
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
        "return_at_first_sellable": ret_at_sellable,
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


def label(primary, stress5):
    pnl = primary.get("pnl")
    if primary.get("pre_sellable_only_winner"):
        return "PRE_SELLABLE_ONLY"
    if not finite(pnl):
        return "UNRESOLVED"
    if pnl > 0 and finite(stress5.get("pnl")) and stress5["pnl"] > 0:
        return "ROBUST_EXECUTABLE_WINNER"
    if pnl > 0:
        return "FRAGILE_WINNER"
    if primary.get("outcome") == "SL":
        return "HARD_STOP_LOSER"
    if primary.get("outcome") == "max_hold":
        return "MAX_HOLD_LOSER"
    return "OTHER_LOSER"


def summarize(rows):
    pnls = [row["pnl"] for row in rows if finite(row.get("pnl"))]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gp = sum(wins)
    gl = -sum(losses)

    def pf(values):
        gp2 = sum(x for x in values if x > 0)
        gl2 = -sum(x for x in values if x < 0)
        return gp2 / gl2 if gl2 else (999.0 if gp2 > 0 else 0.0)

    ranked = sorted(pnls, reverse=True)
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
        "pf_ex_top1": pf(ranked[1:]) if len(ranked) > 1 else None,
        "pf_ex_top3": pf(ranked[3:]) if len(ranked) > 3 else None,
        "post_sellable_tp_rate_pct": pct(sum(1 for row in rows if row.get("post_sellable_tp_reachable")), len(rows)),
        "pre_sellable_only_rate_pct": pct(sum(1 for row in rows if row.get("pre_sellable_only_winner")), len(rows)),
        "median_time_above_20_ms": median(row.get("time_above_20_after_sellable_ms") for row in rows),
    }


def split_rows(rows):
    xs = sorted(rows, key=lambda x: x["ts"])
    a = len(xs) // 2
    b = len(xs) * 3 // 4
    return xs[:a], xs[a:b], xs[b:]


def max_open1(rows):
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
    return out


def update_wallet_features(rows):
    stats = defaultdict(lambda: {"n": 0, "wins": 0, "losses": 0, "gross_profit": 0.0, "gross_loss": 0.0, "pnl": 0.0, "robust": 0, "hard_stop": 0, "mints": set()})
    pending = []
    seq = 0
    enriched = []
    for row in sorted(rows, key=lambda x: x["ts"]):
        while pending and pending[0][0] <= row["ts"]:
            _completion, _seq, wallet, mint, pnl, lab = heapq.heappop(pending)
            s = stats[wallet]
            s["n"] += 1
            s["mints"].add(mint)
            s["pnl"] += pnl
            if pnl > 0:
                s["wins"] += 1
                s["gross_profit"] += pnl
            elif pnl < 0:
                s["losses"] += 1
                s["gross_loss"] += -pnl
            if lab == "ROBUST_EXECUTABLE_WINNER":
                s["robust"] += 1
            if lab == "HARD_STOP_LOSER":
                s["hard_stop"] += 1
        wallet_rows = []
        for p in row.get("participants", []):
            s = stats[p["wallet"]]
            n = s["n"]
            pf = s["gross_profit"] / s["gross_loss"] if s["gross_loss"] else (999.0 if s["gross_profit"] > 0 else 0.0)
            expectancy = s["pnl"] / n if n else None
            high_quality = n >= 5 and pf >= 1.2 and finite(expectancy) and expectancy > 0 and s["robust"] / max(1, n) >= 0.25
            poor_quality = n >= 5 and (pf < 0.8 or (finite(expectancy) and expectancy < -2.0))
            infrastructure = len(s["mints"]) >= 100 or n >= 200
            wallet_rows.append({
                "wallet": p["wallet"],
                "quote_sol": p["quote_sol"],
                "last_buy_age_ms": p.get("last_buy_age_ms"),
                "prior_entries": n,
                "prior_unique_mints": len(s["mints"]),
                "prior_pf": pf,
                "prior_expectancy_pct": expectancy,
                "prior_wr_pct": pct(s["wins"], n),
                "prior_robust_rate_pct": pct(s["robust"], n),
                "prior_hard_stop_rate_pct": pct(s["hard_stop"], n),
                "high_quality": high_quality and not infrastructure,
                "poor_quality": poor_quality and not infrastructure,
                "infrastructure": infrastructure,
            })
        usable = [w for w in wallet_rows if not w["infrastructure"] and w["prior_entries"] > 0]
        highq = [w for w in wallet_rows if w["high_quality"]]
        poor = [w for w in wallet_rows if w["poor_quality"]]
        hq_sol = sum(w["quote_sol"] for w in highq)
        total_sol = sum(w["quote_sol"] for w in wallet_rows)
        row["wallet_quality"] = {
            "participant_wallets": len(wallet_rows),
            "wallets_with_history": len(usable),
            "high_quality_wallets": len(highq),
            "poor_quality_wallets": len(poor),
            "infrastructure_wallets": sum(1 for w in wallet_rows if w["infrastructure"]),
            "highest_quality_score": max((w["prior_expectancy_pct"] or 0.0) * math.log1p(w["prior_entries"]) for w in usable) if usable else None,
            "median_quality_expectancy_pct": median(w["prior_expectancy_pct"] for w in usable),
            "quality_weighted_buy_sol": sum(max(0.0, w["prior_expectancy_pct"] or 0.0) * w["quote_sol"] for w in usable),
            "high_quality_buy_sol": hq_sol,
            "high_quality_flow_share": hq_sol / total_sol if total_sol > 0 else None,
            "top_quality_wallet_buy_sol": max((w["quote_sol"] for w in highq), default=0.0),
            "independent_high_quality_wallets": len(highq),
            "poor_quality_buy_sol": sum(w["quote_sol"] for w in poor),
        }
        row["features"].update({f"wallet.{k}": v for k, v in row["wallet_quality"].items()})
        completion = row.get("entry_ts", row["ts"]) + (row.get("exit_ms") or HOLD_MS)
        if finite(row.get("pnl")):
            for p in row.get("participants", []):
                seq += 1
                heapq.heappush(pending, (completion, seq, p["wallet"], row["mint"], row["pnl"], row["label"]))
        enriched.append(row)
    return enriched


def auc(rows, score_key):
    pairs = []
    for row in rows:
        y = 1 if row.get("label") == "ROBUST_EXECUTABLE_WINNER" else 0
        s = row.get(score_key)
        if finite(s):
            pairs.append((s, y))
    pos = sum(y for _, y in pairs)
    neg = len(pairs) - pos
    if not pos or not neg:
        return None
    pairs.sort(key=lambda x: x[0])
    rank_sum = 0.0
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0
        rank_sum += avg_rank * sum(y for _, y in pairs[i:j + 1])
        i = j + 1
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def train_linear_discriminator(train, fields):
    robust = [r for r in train if r["label"] == "ROBUST_EXECUTABLE_WINNER"]
    bad = [r for r in train if r["label"] in ("HARD_STOP_LOSER", "FRAGILE_WINNER", "MAX_HOLD_LOSER", "OTHER_LOSER")]
    weights = []
    for field in fields:
        rv = [r["features"].get(field) for r in robust]
        bv = [r["features"].get(field) for r in bad]
        rv = [v for v in rv if finite(v)]
        bv = [v for v in bv if finite(v)]
        allv = rv + bv
        if len(rv) < 50 or len(bv) < 50:
            continue
        iqr = (quantile(allv, 0.75) or 0) - (quantile(allv, 0.25) or 0)
        if abs(iqr) < 1e-9:
            continue
        weight = ((median(rv) or 0) - (median(bv) or 0)) / iqr
        weights.append({"feature": field, "weight": weight, "center": median(allv) or 0, "scale": iqr, "robust_median": median(rv), "bad_median": median(bv)})
    weights.sort(key=lambda x: abs(x["weight"]), reverse=True)
    return weights[:12]


def apply_model(rows, model, score_key):
    for row in rows:
        score = 0.0
        for w in model:
            value = row["features"].get(w["feature"])
            if finite(value):
                z = max(-5.0, min(5.0, (value - w["center"]) / w["scale"]))
                score += w["weight"] * z
        row[score_key] = score


def model_compare(rows):
    train, validation, holdout = split_rows(rows)
    flow_fields = [
        "mc_usd", "token_age_ms", "first_buyer_quote_share_bps",
        "w100.buy_count", "w100.gross_buy_sol", "w100.unique_buyers", "w100.top1_share",
        "w500.buy_count", "w500.gross_buy_sol", "w500.unique_buyers", "w500.top1_share", "w500.median_interbuy_ms",
        "w1000.gross_buy_sol", "w1000.unique_buyers", "w1000.price_response_per_sol",
        "w2000.gross_buy_sol", "w2000.unique_buyers", "w2000.top1_share", "w2000.price_lift_pct",
    ]
    sell_fields = [
        "w1000.sell_count", "w2000.sell_count", "w5000.sell_count", "w10000.sell_count",
        "w5000.unique_sellers", "w10000.unique_sellers", "w5000.seller_top1_share",
        "w5000.buy_sell_count_ratio", "w5000.sell_count_damage_pct",
        "absorption.meaningful_sell_before_snapshot", "absorption.last_sell_age_ms",
        "absorption.price_damage_after_last_sell_pct", "absorption.price_recovery_after_last_sell_pct",
        "absorption.buy_sol_after_last_sell", "absorption.new_buyers_after_last_sell", "absorption.price_retained_vs_pre_sell_pct",
    ]
    wallet_fields = [
        "wallet.wallets_with_history", "wallet.high_quality_wallets", "wallet.poor_quality_wallets",
        "wallet.highest_quality_score", "wallet.median_quality_expectancy_pct", "wallet.quality_weighted_buy_sol",
        "wallet.high_quality_buy_sol", "wallet.high_quality_flow_share", "wallet.independent_high_quality_wallets",
        "wallet.poor_quality_buy_sol", "wallet.infrastructure_wallets",
    ]
    sets = {
        "A_flow_price_only": flow_fields,
        "B_flow_plus_sell_supply": flow_fields + sell_fields,
        "C_flow_sell_wallet_quality": flow_fields + sell_fields + wallet_fields,
    }
    out = {}
    for name, fields in sets.items():
        model = train_linear_discriminator(train, fields)
        apply_model(rows, model, f"model.{name}")
        out[name] = {
            "model_type": "dependency_free_train_median_linear_discriminator",
            "top_weights_train_only": model,
            "train_auc": auc(train, f"model.{name}"),
            "validation_auc": auc(validation, f"model.{name}"),
            "holdout_auc": auc(holdout, f"model.{name}"),
        }
    return out


def feature_lifts(rows, fields):
    train, _, _ = split_rows(rows)
    labels = ["ROBUST_EXECUTABLE_WINNER", "FRAGILE_WINNER", "HARD_STOP_LOSER", "MAX_HOLD_LOSER"]
    out = {}
    for field in fields:
        out[field] = {}
        for lab in labels:
            vals = [r["features"].get(field) for r in train if r["label"] == lab]
            vals = [v for v in vals if finite(v)]
            out[field][lab] = {"coverage": len(vals), "median": median(vals), "p25": quantile(vals, 0.25), "p75": quantile(vals, 0.75)}
    return out


def candidate_rules_allowed(model_results):
    a = model_results["A_flow_price_only"]["validation_auc"] or 0
    b = model_results["B_flow_plus_sell_supply"]["validation_auc"] or 0
    c = model_results["C_flow_sell_wallet_quality"]["validation_auc"] or 0
    return (b - a) >= 0.02 or (c - a) >= 0.03


def candidate_defs():
    return [
        {
            "name": "quality_demand_1s_2s_v1",
            "family": "QUALITY_DEMAND",
            "definition": "1s/2s, high_quality_wallets>=1, quality_weighted_buy_sol>=1, gross1s>=1.5, mc<=12000",
            "test": lambda x: x["features"].get("wallet.high_quality_wallets", 0) >= 1
            and (x["features"].get("wallet.quality_weighted_buy_sol") or 0) >= 1
            and (x["features"].get("w1000.gross_buy_sol") or 0) >= 1.5
            and x["mc"] <= 12000,
        },
        {
            "name": "multi_quality_low_overextension_v1",
            "family": "MULTIPLE_PROVEN_PARTICIPANTS + LOW_OVEREXTENSION",
            "definition": "high_quality_wallets>=2, w2s price_response_per_sol<=8, top1_2s<=70%, mc<=15000",
            "test": lambda x: x["features"].get("wallet.high_quality_wallets", 0) >= 2
            and finite(x["features"].get("w2000.price_response_per_sol"))
            and x["features"]["w2000.price_response_per_sol"] <= 8
            and finite(x["features"].get("w2000.top1_share"))
            and x["features"]["w2000.top1_share"] <= 0.70
            and x["mc"] <= 15000,
        },
        {
            "name": "post_sell_buyer_renewal_v1",
            "family": "POST_SELL_BUYER_RENEWAL",
            "definition": "sell before snapshot, retained>=90% of pre-sell high, buy_sol_after_sell>=1.5, new_buyers_after_sell>=2",
            "test": lambda x: bool(x["features"].get("absorption.meaningful_sell_before_snapshot"))
            and finite(x["features"].get("absorption.price_retained_vs_pre_sell_pct"))
            and x["features"]["absorption.price_retained_vs_pre_sell_pct"] >= 90
            and (x["features"].get("absorption.buy_sol_after_last_sell") or 0) >= 1.5
            and (x["features"].get("absorption.new_buyers_after_last_sell") or 0) >= 2,
        },
        {
            "name": "clean_2s_quality_flow_v1",
            "family": "2S_QUALITY_FLOW",
            "definition": "2s snapshot, gross2s>=3, unique2s>=3, top1_2s<=60%, poor_quality_wallets=0, mc<=10000",
            "test": lambda x: x["snapshot_age_ms"] == 2000
            and (x["features"].get("w2000.gross_buy_sol") or 0) >= 3
            and (x["features"].get("w2000.unique_buyers") or 0) >= 3
            and finite(x["features"].get("w2000.top1_share"))
            and x["features"]["w2000.top1_share"] <= 0.60
            and x["features"].get("wallet.poor_quality_wallets", 0) == 0
            and x["mc"] <= 10000,
        },
        {
            "name": "exact_sell_absorption_v1",
            "family": "SELL_ABSORPTION",
            "definition": "exact sell quote seen in 2s/5s, buy/sell SOL ratio exact>=2, retained>=90%",
            "test": lambda x: (x["features"].get("w5000.exact_sell_count") or 0) >= 1
            and finite(x["features"].get("w5000.buy_sell_sol_ratio_exact"))
            and x["features"]["w5000.buy_sell_sol_ratio_exact"] >= 2
            and finite(x["features"].get("absorption.price_retained_vs_pre_sell_pct"))
            and x["features"]["absorption.price_retained_vs_pre_sell_pct"] >= 90,
        },
    ]


def evaluate_candidates(rows, span_hours):
    out = {}
    leaderboard = []
    for cand in candidate_defs():
        selected = [r for r in rows if cand["test"](r)]
        train, val, hold = split_rows(selected)
        stress5 = [{"mint": r["mint"], "ts": r["ts"], "pnl": r.get("stress5_pnl"), "outcome": r.get("stress5_outcome")} for r in selected]
        stress10 = [{"mint": r["mint"], "ts": r["ts"], "pnl": r.get("stress10_pnl"), "outcome": r.get("stress10_outcome")} for r in selected]
        item = {
            "family": cand["family"],
            "definition": cand["definition"],
            "n": len(selected),
            "signals_per_hour": len(selected) / max(0.001, span_hours or 0),
            "all": summarize(selected),
            "validation": summarize(val),
            "holdout": summarize(hold),
            "stress5": summarize(stress5),
            "stress10": summarize(stress10),
            "max_open1": max_open1(selected),
        }
        out[cand["name"]] = item
        leaderboard.append({
            "name": cand["name"],
            "family": cand["family"],
            "n": len(selected),
            "signals_per_hour": item["signals_per_hour"],
            "validation_pf": item["validation"]["pf"],
            "holdout_pf": item["holdout"]["pf"],
            "p75_pf": item["all"]["pf"],
            "expectancy_pct": item["all"]["expectancy_pct"],
            "stress5_pf": item["stress5"]["pf"],
            "stress10_pf": item["stress10"]["pf"],
            "max_open1_pf": item["max_open1"]["pf"],
            "pf_ex_top3": item["all"]["pf_ex_top3"],
        })
    leaderboard.sort(key=lambda x: (x["validation_pf"] or 0, x["holdout_pf"] or 0, x["stress5_pf"] or 0), reverse=True)
    return out, leaderboard


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capsules", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--limit-rows", type=int, default=0)
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    started = time.time()
    flows = defaultdict(deque)
    first_seen = {}
    emitted = defaultdict(set)
    snapshots = []
    sell_recovery = Counter()
    rows_scanned = flow_rows = buy_rows = sell_rows = marks_used = 0
    time_min = time_max = None
    unique_mints = set()

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
        if not finite(mc) or not (0 < float(mc) <= MAX_MC_USD) or side not in ("buy", "sell"):
            continue
        event = build_event(row, ts, mc, side)
        dq = flows[mint]
        dq.append(event)
        while dq and dq[0]["ts"] < ts - HOLD_MS:
            dq.popleft()
        flow_rows += 1
        if side == "buy":
            buy_rows += 1
        else:
            sell_rows += 1
            sell_recovery[event["sell_quote_status"]] += 1
        age = ts - first_seen[mint]
        for age_ms in SNAPSHOT_AGES:
            if age >= age_ms and age_ms not in emitted[mint]:
                emitted[mint].add(age_ms)
                snapshots.append(make_snapshot(row, dq, ts, mc, first_seen[mint], age_ms))
        if rows_scanned % 500_000 == 0:
            stale = ts - HOLD_MS
            for key in [k for k, v in flows.items() if not v or v[-1]["ts"] < stale]:
                del flows[key]
            print(json.dumps(compact({
                "progress": "pass1_snapshots",
                "rows": rows_scanned,
                "snapshots": len(snapshots),
                "active_flow_mints": len(flows),
                "sell_recovery": dict(sell_recovery),
                "elapsed_sec": time.time() - started,
            })), flush=True)

    wanted = {}
    for snap in snapshots:
        wanted[snap["mint"]] = min(wanted.get(snap["mint"], snap["ts"]), snap["ts"])
    marks = defaultdict(list)
    rows_pass2 = 0
    for row in read_jsonl(args.capsules):
        rows_pass2 += 1
        if args.limit_rows and rows_pass2 > args.limit_rows:
            break
        mint = mint_of(row)
        if mint not in wanted:
            continue
        ts = ts_of(row)
        mc = mc_of(row)
        if finite(ts) and finite(mc) and wanted[mint] <= int(ts) <= wanted[mint] + HOLD_MS + SELLABLE_P75_MS + 1000 and 0 < float(mc) <= MAX_MC_USD:
            marks[mint].append((int(ts), float(mc)))
            marks_used += 1
        if rows_pass2 % 1_000_000 == 0:
            print(json.dumps(compact({"progress": "pass2_marks", "rows": rows_pass2, "marks_used": marks_used, "elapsed_sec": time.time() - started})), flush=True)
    for arr in marks.values():
        arr.sort()

    outcomes = []
    for snap in snapshots:
        mark_rows = marks.get(snap["mint"], [])
        primary = score(snap["ts"], snap["mc"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P75_MS, 0)
        stress5 = score(snap["ts"], snap["mc"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P75_MS, 5)
        stress10 = score(snap["ts"], snap["mc"], mark_rows, ENTRY_DELAY_MS, SELLABLE_P75_MS, 10)
        snap.update(primary)
        snap["stress5_pnl"] = stress5.get("pnl")
        snap["stress5_outcome"] = stress5.get("outcome")
        snap["stress10_pnl"] = stress10.get("pnl")
        snap["stress10_outcome"] = stress10.get("outcome")
        snap["label"] = label(primary, stress5)
        outcomes.append(snap)
    enriched = update_wallet_features(outcomes)
    train, validation, holdout = split_rows(enriched)
    model_results = model_compare(enriched)
    flow_lift_fields = [
        "mc_usd", "token_age_ms", "w1000.gross_buy_sol", "w1000.buy_count", "w1000.unique_buyers",
        "w2000.gross_buy_sol", "w2000.buy_count", "w2000.unique_buyers", "w2000.price_response_per_sol",
    ]
    sell_lift_fields = [
        "w1000.sell_count", "w2000.sell_count", "w5000.sell_count", "w5000.unique_sellers",
        "absorption.meaningful_sell_before_snapshot", "absorption.price_damage_after_last_sell_pct",
        "absorption.price_recovery_after_last_sell_pct", "absorption.buy_sol_after_last_sell",
        "absorption.new_buyers_after_last_sell", "absorption.price_retained_vs_pre_sell_pct",
    ]
    wallet_lift_fields = [
        "wallet.wallets_with_history", "wallet.high_quality_wallets", "wallet.poor_quality_wallets",
        "wallet.highest_quality_score", "wallet.median_quality_expectancy_pct", "wallet.quality_weighted_buy_sol",
        "wallet.high_quality_buy_sol", "wallet.high_quality_flow_share", "wallet.independent_high_quality_wallets",
    ]
    candidates = {}
    candidate_leaderboard = []
    if candidate_rules_allowed(model_results):
        candidates, candidate_leaderboard = evaluate_candidates(enriched, ((time_max - time_min) / 3_600_000) if time_min and time_max else None)
    one_s = [r for r in enriched if r["snapshot_age_ms"] == 1000]
    two_s = [r for r in enriched if r["snapshot_age_ms"] == 2000]
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
            "snapshots_total": len(enriched),
            "snapshots_1s": len(one_s),
            "snapshots_2s": len(two_s),
            "marks_used": marks_used,
            "entry_delay_ms": ENTRY_DELAY_MS,
            "first_sellable_ms": SELLABLE_P75_MS,
        },
        "sell_quote_recovery": dict(sell_recovery),
        "sell_quote_recovery_notes": "Exact sellQuoteLamports is used only when present. sellTokenAmountRaw alone is classified approximate and excluded from primary SOL sell pressure.",
        "summary_1s": summarize(one_s),
        "summary_2s": summarize(two_s),
        "label_counts": dict(Counter(r["label"] for r in enriched)),
        "label_counts_1s": dict(Counter(r["label"] for r in one_s)),
        "label_counts_2s": dict(Counter(r["label"] for r in two_s)),
        "splits": {
            "train": summarize(train),
            "validation": summarize(validation),
            "holdout": summarize(holdout),
        },
        "sell_side_lifts_train_only": feature_lifts(enriched, sell_lift_fields),
        "wallet_quality_lifts_train_only": feature_lifts(enriched, wallet_lift_fields),
        "flow_lifts_train_only": feature_lifts(enriched, flow_lift_fields),
        "model_results": model_results,
        "candidate_rules_created": len(candidates),
        "candidate_results": candidates,
        "candidate_leaderboard": candidate_leaderboard,
        "comparison_to_prior_atlas": {
            "prior_best_flow_family": "reacceleration_v1_benchmark from Atlas V1",
            "prior_best_p75_pf": 1.190912,
            "prior_best_plus5_pf": 0.664927,
            "prior_best_max_open1_pf": 1.181001,
        },
    }
    a = model_results["A_flow_price_only"]["validation_auc"] or 0
    b = model_results["B_flow_plus_sell_supply"]["validation_auc"] or 0
    c = model_results["C_flow_sell_wallet_quality"]["validation_auc"] or 0
    if candidates and any((x.get("validation_pf") or 0) >= 1.4 and (x.get("holdout_pf") or 0) >= 1.3 and (x.get("stress5_pf") or 0) > 1 for x in candidate_leaderboard):
        classification = "ENRICHED_EXECUTABLE_FINGERPRINT_FOUND"
    elif c - a >= 0.03:
        classification = "WALLET_QUALITY_SIGNAL_FOUND_BUT_RULE_NOT_READY"
    elif b - a >= 0.02:
        classification = "SELL_ABSORPTION_SIGNAL_FOUND_BUT_RULE_NOT_READY"
    elif max(b, c) - a >= 0.01:
        classification = "ENRICHED_FEATURES_MATERIALLY_IMPROVE_SIGNAL"
    elif sell_rows and sell_recovery.get("EXACT_SELL_QUOTE", 0) == 0:
        classification = "NO_MATERIAL_ENRICHMENT_FOUND"
    else:
        classification = "NO_MATERIAL_ENRICHMENT_FOUND"
    report["final_classification"] = classification
    out_path = os.path.join(args.out_dir, "enriched_1s_2s_snapshots.jsonl.gz")
    with gzip.open(out_path, "wt", encoding="utf-8") as out:
        for row in enriched:
            out.write(json.dumps(compact(row), separators=(",", ":")) + "\n")
    report["outputs"] = {
        "enriched_snapshots": out_path,
        "report": os.path.join(args.out_dir, "report.json"),
    }
    with open(report["outputs"]["report"], "w", encoding="utf-8") as out:
        json.dump(compact(report), out, indent=2)
    print(json.dumps(compact({"done": True, "out_dir": args.out_dir, "dataset": report["dataset"], "model_results": model_results, "candidate_leaderboard": candidate_leaderboard, "final_classification": classification}), separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
