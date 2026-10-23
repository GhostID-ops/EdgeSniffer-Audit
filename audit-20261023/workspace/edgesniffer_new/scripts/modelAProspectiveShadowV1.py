#!/usr/bin/env python3
import argparse
import gzip
import hashlib
import json
import math
import os
import time
from collections import Counter, defaultdict, deque


MODEL_NAME = "A_flow_price_only"
SNAPSHOT_AGE_MS = 1000
ENTRY_DELAY_MS = 25
SELLABLE_P50_MS = 964
SELLABLE_P75_MS = 1476
SELLABLE_P90_MS = 1989
TP_PCT = 20.0
SL_PCT = -20.0
HOLD_MS = 120_000
ROUND_TRIP_COST_PCT = 2.5
MAX_MC_USD = 250_000
WINDOWS = [100, 250, 500, 1000, 2000, 5000, 10000]
LANES = {
    "MODEL_A_1S_TOP_0P25_PROSPECTIVE_V1": "top_0p25",
    "MODEL_A_1S_TOP_0P50_PROSPECTIVE_V1": "top_0p50",
}


MODEL_A_WEIGHTS = [
    {"feature": "w2000.top1_share", "weight": -1.382648, "center": 1.0, "scale": 0.034516},
    {"feature": "w2000.gross_buy_sol", "weight": 1.210854, "center": 0.095, "scale": 0.7},
    {"feature": "mc_usd", "weight": 1.023885, "center": 4551.284343, "scale": 449.287814},
    {"feature": "first_buyer_quote_share_bps", "weight": -0.919253, "center": 9196.0, "scale": 7282.0},
    {"feature": "w1000.gross_buy_sol", "weight": 0.696517, "center": 0.024295, "scale": 0.402},
    {"feature": "w500.gross_buy_sol", "weight": 0.406818, "center": 0.02, "scale": 0.33},
    {"feature": "w100.gross_buy_sol", "weight": 0.310222, "center": 0.01575, "scale": 0.286892},
    {"feature": "token_age_ms", "weight": -0.280938, "center": 7153.0, "scale": 27515.0},
    {"feature": "w500.median_interbuy_ms", "weight": -0.183168, "center": 51.0, "scale": 202.0},
    {"feature": "w100.buy_count", "weight": 0.0, "center": 1.0, "scale": 1.0},
    {"feature": "w100.unique_buyers", "weight": 0.0, "center": 1.0, "scale": 1.0},
    {"feature": "w500.buy_count", "weight": 0.0, "center": 1.0, "scale": 1.0},
]


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def now_ms():
    return int(time.time() * 1000)


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


def model_spec():
    spec = {
        "model": MODEL_NAME,
        "snapshot_age_ms": SNAPSHOT_AGE_MS,
        "model_type": "dependency_free_train_median_iqr_linear_discriminator",
        "feature_list": [w["feature"] for w in MODEL_A_WEIGHTS],
        "windows_ms": WINDOWS,
        "preprocessing": {
            "missing_value_behavior": "If a feature is missing/non-finite, that feature contributes 0 to score.",
            "transform": "z=(value-center)/scale, clipped to [-5,+5], contribution=weight*z.",
            "normalization": "Per-feature historical train median center and historical IQR scale frozen from Enrichment V1.",
            "score": "sum(weight * clipped_z) over frozen weights.",
        },
        "weights": MODEL_A_WEIGHTS,
        "entry_model": {"entry_delay_ms": ENTRY_DELAY_MS},
        "first_sellable_model_ms": {"p50": SELLABLE_P50_MS, "p75_primary": SELLABLE_P75_MS, "p90": SELLABLE_P90_MS},
        "exit_policy": {"tp_pct": TP_PCT, "sl_pct": SL_PCT, "max_hold_ms": HOLD_MS, "round_trip_cost_pct": ROUND_TRIP_COST_PCT},
    }
    encoded = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
    spec["model_version_hash"] = hashlib.sha256(encoded).hexdigest()
    return spec


def model_score(features):
    score = 0.0
    for w in MODEL_A_WEIGHTS:
        value = features.get(w["feature"])
        if finite(value):
            z = (float(value) - float(w["center"])) / float(w["scale"])
            z = max(-5.0, min(5.0, z))
            score += float(w["weight"]) * z
    return score


def read_jsonl_any(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
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
    return row.get("candidate_ready_ts_ms") or row.get("decode_complete_ts_ms") or row.get("local_receive_ts_ms") or (
        row.get("canonical_state") or {}
    ).get("observed_at_ms")


def mc_of(row):
    state = row.get("canonical_state") or {}
    candidate = row.get("candidate") or {}
    value = state.get("market_cap_usd")
    if finite(value):
        return float(value)
    value = candidate.get("market_cap_usd")
    return float(value) if finite(value) else None


def side_of(row):
    return (row.get("raw_record") or {}).get("side") or row.get("side")


def wallet_of(row):
    return (row.get("raw_record") or {}).get("wallet") or row.get("wallet")


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
    if finite(raw.get("sellTokenAmountRaw")) or finite(row.get("sell_token_amount_raw")):
        return "APPROXIMATE_NOT_FOR_PRIMARY_USE", None
    return "UNRECOVERABLE", None


def first_buyer_bps(row):
    value = (row.get("candidate") or {}).get("first_buyer_quote_share_bps")
    return float(value) if finite(value) else None


def is_eligible(row, mint, ts, mc, side):
    if not mint or not finite(ts) or not finite(mc) or side not in ("buy", "sell"):
        return False
    if not (0 < float(mc) <= MAX_MC_USD):
        return False
    state = row.get("canonical_state") or {}
    if state and state.get("venue") not in (None, "pump_fun"):
        return False
    return True


def build_event(row, ts, mc, side):
    status, sell_sol = sell_quote_status(row)
    return {
        "ts": int(ts),
        "side": side,
        "quote_sol": quote_sol(row) if side == "buy" else 0.0,
        "sell_sol": sell_sol,
        "sell_quote_status": status if side == "sell" else None,
        "wallet": wallet_of(row),
        "slot": (row.get("raw_record") or {}).get("slot") or row.get("source_slot"),
        "sig": (row.get("raw_record") or {}).get("signature") or row.get("transaction_signature"),
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
    buy_ts_asc = sorted(e["ts"] for e in buys)
    intervals = [buy_ts_asc[i] - buy_ts_asc[i - 1] for i in range(1, len(buy_ts_asc))]
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


def flatten(prefix, values, out):
    for key, value in values.items():
        out[f"{prefix}.{key}"] = value


def make_features(mint, row, events, snapshot_ts, first_seen_ts, mc):
    features = {
        "mint": mint,
        "ts": int(snapshot_ts),
        "snapshot_age_ms": SNAPSHOT_AGE_MS,
        "mc_usd": float(mc),
        "token_age_ms": int(snapshot_ts - first_seen_ts),
        "first_buyer_quote_share_bps": first_buyer_bps(row),
    }
    for width in WINDOWS:
        flatten(f"w{width}", window(events, snapshot_ts, width), features)
    return features


def score_outcome(entry_ts, selected_mc, marks, sellable_ms, entry_stress_pct=0.0):
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
        if roi >= TP_PCT and first_tp_before is None:
            first_tp_before = ts
        if ts < sellable_ts:
            continue
        if ret_at_sellable is None:
            ret_at_sellable = roi
        max_after = max(max_after, roi)
        min_after = min(min_after, roi)
        terminal = (ts, roi)
        if roi >= TP_PCT and first_tp_after is None:
            first_tp_after = ts
        if roi <= SL_PCT and first_sl_after is None:
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
        pnl = TP_PCT - entry_stress_pct
        exit_ts = first_tp_after
    elif first_sl_after:
        outcome = "SL"
        pnl = SL_PCT
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


class Writer:
    def __init__(self, path, gzip_enabled=False):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        if gzip_enabled:
            self.handle = gzip.open(path, "at", encoding="utf-8")
        else:
            self.handle = open(path, "a", encoding="utf-8")

    def write(self, row):
        self.handle.write(json.dumps(compact(row), sort_keys=True, separators=(",", ":")) + "\n")
        self.handle.flush()


class Prospective:
    def __init__(self, args, thresholds):
        self.args = args
        self.thresholds = thresholds
        self.flows = defaultdict(deque)
        self.first_seen = {}
        self.last_row = {}
        self.snapshot_written = set()
        self.pending_snapshots = {}
        self.pending_outcomes = {}
        self.seen = 0
        self.scored = 0
        self.triggers = Counter()
        self.closed = Counter()
        self.all_scores = Writer(os.path.join(args.out_dir, "all_1s_scores.jsonl.gz"), gzip_enabled=True)
        self.primary = Writer(os.path.join(args.out_dir, "primary_triggers.jsonl"))
        self.secondary = Writer(os.path.join(args.out_dir, "secondary_triggers.jsonl"))
        self.outcomes = Writer(os.path.join(args.out_dir, "executable_outcomes.jsonl"))
        self.checkpoints = Writer(os.path.join(args.out_dir, "checkpoints.jsonl"))
        self.status = Writer(os.path.join(args.out_dir, "status.jsonl"))

    def prune(self, mint, ts):
        dq = self.flows[mint]
        cutoff = ts - HOLD_MS - SELLABLE_P90_MS - 5000
        while dq and dq[0]["ts"] < cutoff:
            dq.popleft()

    def ingest(self, row):
        self.seen += 1
        mint = mint_of(row)
        ts = ts_of(row)
        mc = mc_of(row)
        side = side_of(row)
        if not is_eligible(row, mint, ts, mc, side):
            return
        ts = int(ts)
        event = build_event(row, ts, mc, side)
        self.flows[mint].append(event)
        self.prune(mint, ts)
        if mint not in self.first_seen:
            self.first_seen[mint] = ts
            self.pending_snapshots[mint] = ts + SNAPSHOT_AGE_MS
        self.last_row[mint] = row
        self.update_outcome_marks(mint, ts, mc)
        self.process_due(now_event_ts=ts)
        if self.seen % self.args.status_every == 0:
            self.emit_status()

    def process_due(self, now_event_ts=None):
        current_wall = now_ms()
        due = []
        for mint, due_ts in list(self.pending_snapshots.items()):
            if now_event_ts is not None and due_ts <= now_event_ts:
                due.append(mint)
            elif now_event_ts is None and self.first_seen.get(mint, 0) + SNAPSHOT_AGE_MS <= current_wall:
                due.append(mint)
        for mint in due:
            self.write_snapshot(mint)
            self.pending_snapshots.pop(mint, None)
        self.close_stale_outcomes(now_event_ts)

    def mc_at_or_before(self, events, ts):
        chosen = None
        for event in events:
            if event["ts"] <= ts:
                chosen = event
            else:
                break
        return chosen

    def write_snapshot(self, mint):
        if mint in self.snapshot_written or mint not in self.first_seen:
            return
        self.snapshot_written.add(mint)
        snapshot_ts = self.first_seen[mint] + SNAPSHOT_AGE_MS
        events = self.flows.get(mint) or deque()
        chosen = self.mc_at_or_before(events, snapshot_ts)
        if not chosen:
            return
        features = make_features(mint, self.last_row.get(mint, {}), events, snapshot_ts, self.first_seen[mint], chosen["mc"])
        score = model_score(features)
        primary_hit = score >= self.thresholds["MODEL_A_1S_TOP_0P25_FROZEN_SCORE"]
        secondary_hit = score >= self.thresholds["MODEL_A_1S_TOP_0P50_FROZEN_SCORE"]
        row = {
            "event": "model_a_1s_score",
            "model": MODEL_NAME,
            "mint": mint,
            "createTimestampMs": self.first_seen[mint],
            "snapshotTimestampMs": snapshot_ts,
            "tokenAgeMs": snapshot_ts - self.first_seen[mint],
            "mcUsd": chosen["mc"],
            "modelScore": score,
            "thresholdTop0p25": self.thresholds["MODEL_A_1S_TOP_0P25_FROZEN_SCORE"],
            "thresholdTop0p50": self.thresholds["MODEL_A_1S_TOP_0P50_FROZEN_SCORE"],
            "passesTop0p25": primary_hit,
            "passesTop0p50": secondary_hit,
            "features": features,
            "noSubmit": True,
            "prospectiveVersion": "MODEL_A_PROSPECTIVE_SHADOW_V1",
        }
        self.all_scores.write(row)
        self.scored += 1
        if primary_hit or secondary_hit:
            self.start_trigger(row, primary_hit, secondary_hit)

    def start_trigger(self, score_row, primary_hit, secondary_hit):
        lanes = []
        if primary_hit:
            lanes.append("MODEL_A_1S_TOP_0P25_PROSPECTIVE_V1")
            self.primary.write({**score_row, "event": "model_a_1s_primary_trigger", "lane": lanes[-1]})
        if secondary_hit:
            lanes.append("MODEL_A_1S_TOP_0P50_PROSPECTIVE_V1")
            self.secondary.write({**score_row, "event": "model_a_1s_secondary_trigger", "lane": lanes[-1]})
        mint = score_row["mint"]
        for lane in lanes:
            key = (lane, mint)
            if key in self.pending_outcomes:
                continue
            self.triggers[lane] += 1
            self.pending_outcomes[key] = {
                "score_row": score_row,
                "marks": [(e["ts"], e["mc"]) for e in self.flows[mint] if e["ts"] >= score_row["snapshotTimestampMs"] - 1000],
                "lane": lane,
            }

    def update_outcome_marks(self, mint, ts, mc):
        for (lane, pmint), state in list(self.pending_outcomes.items()):
            if pmint == mint:
                state["marks"].append((ts, mc))
                if ts >= state["score_row"]["snapshotTimestampMs"] + HOLD_MS + SELLABLE_P90_MS:
                    self.close_outcome((lane, pmint))

    def close_stale_outcomes(self, now_event_ts):
        if now_event_ts is None:
            return
        cutoff = now_event_ts - HOLD_MS - SELLABLE_P90_MS - 5000
        for key, state in list(self.pending_outcomes.items()):
            if state["score_row"]["snapshotTimestampMs"] < cutoff:
                self.close_outcome(key)

    def close_outcome(self, key):
        state = self.pending_outcomes.pop(key, None)
        if not state:
            return
        score_row = state["score_row"]
        marks = sorted(state["marks"])
        primary = score_outcome(score_row["snapshotTimestampMs"], score_row["mcUsd"], marks, SELLABLE_P75_MS, 0)
        p50 = score_outcome(score_row["snapshotTimestampMs"], score_row["mcUsd"], marks, SELLABLE_P50_MS, 0)
        p90 = score_outcome(score_row["snapshotTimestampMs"], score_row["mcUsd"], marks, SELLABLE_P90_MS, 0)
        stress5 = score_outcome(score_row["snapshotTimestampMs"], score_row["mcUsd"], marks, SELLABLE_P75_MS, 5)
        stress10 = score_outcome(score_row["snapshotTimestampMs"], score_row["mcUsd"], marks, SELLABLE_P75_MS, 10)
        out = {
            "event": "model_a_1s_executable_outcome",
            "lane": state["lane"],
            "mint": score_row["mint"],
            "modelScore": score_row["modelScore"],
            "snapshotTimestampMs": score_row["snapshotTimestampMs"],
            "decisionTimestampMs": score_row["snapshotTimestampMs"],
            "expectedFirstSendTimestampMs": score_row["snapshotTimestampMs"] + ENTRY_DELAY_MS,
            "selectedMcUsd": score_row["mcUsd"],
            "entryDelayMs": ENTRY_DELAY_MS,
            "primaryFirstSellableMs": SELLABLE_P75_MS,
            "p50FirstSellableMs": SELLABLE_P50_MS,
            "p90FirstSellableMs": SELLABLE_P90_MS,
            "normal": primary,
            "p50FirstSellable": p50,
            "p90FirstSellable": p90,
            "entryStress5": stress5,
            "entryStress10": stress10,
            "observedMarks": len(marks),
            "noSubmit": True,
        }
        self.outcomes.write(out)
        self.closed[state["lane"]] += 1
        if self.closed[state["lane"]] in (25, 50, 100, 200):
            self.checkpoints.write({"event": "model_a_checkpoint_reached", "lane": state["lane"], "closed": self.closed[state["lane"]], "tsMs": now_ms()})

    def emit_status(self):
        self.status.write({
            "event": "model_a_prospective_status",
            "seenRows": self.seen,
            "scored1s": self.scored,
            "pendingSnapshots": len(self.pending_snapshots),
            "triggers": dict(self.triggers),
            "closed": dict(self.closed),
            "openOutcomes": len(self.pending_outcomes),
            "tsMs": now_ms(),
            "noSubmit": True,
        })


def derive_thresholds(enriched_path):
    one_s = []
    diffs = []
    compared = 0
    for row in read_jsonl_any(enriched_path):
        if row.get("snapshot_age_ms") != SNAPSHOT_AGE_MS:
            continue
        stored = row.get("model.A_flow_price_only")
        calc = model_score(row.get("features") or {})
        if finite(stored):
            compared += 1
            diffs.append(abs(float(stored) - calc))
            one_s.append((float(stored), calc, row.get("mint"), row.get("ts")))
    stored_ranked = sorted(one_s, key=lambda x: x[0], reverse=True)
    calc_ranked = sorted(one_s, key=lambda x: x[1], reverse=True)
    n = len(one_s)
    top025_n = max(1, int(math.ceil(n * 0.0025)))
    top050_n = max(1, int(math.ceil(n * 0.005)))
    thresholds = {
        "MODEL_A_1S_TOP_0P25_FROZEN_SCORE": calc_ranked[top025_n - 1][1],
        "MODEL_A_1S_TOP_0P50_FROZEN_SCORE": calc_ranked[top050_n - 1][1],
        "storedHistoricalTop0p25BoundaryScore": stored_ranked[top025_n - 1][0],
        "storedHistoricalTop0p50BoundaryScore": stored_ranked[top050_n - 1][0],
        "historicalPopulation": "snapshot_1s",
        "historicalRows": n,
        "top0p25Count": top025_n,
        "top0p50Count": top050_n,
        "derivation": "ceil(N * percentile) by descending recreated prospective scorer score, after proving ranked set parity with stored historical model.A_flow_price_only.",
        "source": enriched_path,
    }
    stored_top025 = {(x[2], x[3]) for x in stored_ranked[:top025_n]}
    calc_top025 = {(x[2], x[3]) for x in calc_ranked[:top025_n]}
    stored_top050 = {(x[2], x[3]) for x in stored_ranked[:top050_n]}
    calc_top050 = {(x[2], x[3]) for x in calc_ranked[:top050_n]}
    parity = {
        "event": "historical_scorer_parity",
        "nCompared": compared,
        "maxScoreDifference": max(diffs) if diffs else None,
        "scoreDifferenceTolerance": 1e-4,
        "top0p25TriggerDisagreementCount": len(stored_top025.symmetric_difference(calc_top025)),
        "top0p50TriggerDisagreementCount": len(stored_top050.symmetric_difference(calc_top050)),
        "top0p25SetParity": stored_top025 == calc_top025,
        "top0p50SetParity": stored_top050 == calc_top050,
        "status": "PASS",
    }
    if parity["maxScoreDifference"] is None or parity["maxScoreDifference"] > parity["scoreDifferenceTolerance"] or parity["top0p25TriggerDisagreementCount"] or parity["top0p50TriggerDisagreementCount"]:
        parity["status"] = "FAIL"
    return thresholds, parity


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as out:
        json.dump(compact(data), out, indent=2, sort_keys=True)


def tail_loop(args, thresholds):
    collector = Prospective(args, thresholds)
    with open(args.input, "r", encoding="utf-8", errors="replace") as handle:
        if args.start_at_end:
            handle.seek(0, os.SEEK_END)
        last_idle = time.time()
        while True:
            line = handle.readline()
            if not line:
                if time.time() - last_idle >= args.idle_process_sec:
                    collector.process_due(now_event_ts=None)
                    last_idle = time.time()
                time.sleep(args.poll_ms / 1000.0)
                continue
            last_idle = time.time()
            try:
                collector.ingest(json.loads(line))
            except Exception as exc:
                collector.status.write({"event": "model_a_prospective_parse_error", "error": str(exc), "tsMs": now_ms()})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--historical-enriched", required=True)
    parser.add_argument("--start-at-end", action="store_true")
    parser.add_argument("--poll-ms", type=int, default=10)
    parser.add_argument("--idle-process-sec", type=float, default=0.1)
    parser.add_argument("--status-every", type=int, default=10000)
    parser.add_argument("--derive-only", action="store_true")
    parser.add_argument("--max-parity-diff", type=float, default=1e-5)
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    thresholds, parity = derive_thresholds(args.historical_enriched)
    spec = model_spec()
    score_range = {"min": None, "max": None}
    vals = []
    for row in read_jsonl_any(args.historical_enriched):
        if row.get("snapshot_age_ms") == SNAPSHOT_AGE_MS and finite(row.get("model.A_flow_price_only")):
            vals.append(float(row["model.A_flow_price_only"]))
    if vals:
        score_range = {"min": min(vals), "max": max(vals), "p50": quantile(vals, 0.5), "p995": quantile(vals, 0.995), "p9975": quantile(vals, 0.9975)}
    spec["score_range_historical_1s"] = score_range
    write_json(os.path.join(args.out_dir, "frozen_model_spec.json"), spec)
    write_json(os.path.join(args.out_dir, "frozen_thresholds.json"), thresholds)
    write_json(os.path.join(args.out_dir, "scorer_parity_report.json"), parity)
    if parity["status"] != "PASS":
        write_json(os.path.join(args.out_dir, "final_report.json"), {"classification": "MODEL_SCORE_PARITY_BLOCKER", "parity": parity})
        print(json.dumps({"ok": False, "classification": "MODEL_SCORE_PARITY_BLOCKER", "out_dir": args.out_dir, "parity": parity}, separators=(",", ":")))
        return
    if args.derive_only:
        print(json.dumps({"ok": True, "deriveOnly": True, "out_dir": args.out_dir, "thresholds": compact(thresholds), "parity": compact(parity), "modelHash": spec["model_version_hash"]}, separators=(",", ":")))
        return
    tail_loop(args, thresholds)


if __name__ == "__main__":
    main()
