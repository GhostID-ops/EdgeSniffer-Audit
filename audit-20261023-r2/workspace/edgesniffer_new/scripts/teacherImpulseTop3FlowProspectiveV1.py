#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import os
import time
from collections import defaultdict, deque

HOLD_MS_DEFAULT = 120_000
TP_DEFAULT = 20.0
SL_DEFAULT = -20.0
ROUND_TRIP_COST_PCT_DEFAULT = 2.5
FAST_LATENCIES_MS = [0, 1, 1, 1, 2, 2, 4, 19, 20, 25]
RULES = [
    "net1s_ge_5_prospective_v1",
    "net2s_ge_5_prospective_v1",
    "bc50_ge2_net5s_ge5_prospective_v1",
    "grossbuy_1s_ge5_prospective_v1",
    "grossbuy_2s_ge5_prospective_v1",
    "bc50_ge2_grossbuy5s_ge5_prospective_v1",
    "truenet_1s_ge5_prospective_v1",
    "truenet_2s_ge5_prospective_v1",
    "bc50_ge2_truenet5s_ge5_prospective_v1",
]


def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def stable_latency_ms(mint):
    digest = hashlib.sha256(mint.encode("utf-8")).hexdigest()
    return FAST_LATENCIES_MS[int(digest[:8], 16) % len(FAST_LATENCIES_MS)]


def read_tail(path, start_at_end, poll_ms):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        if start_at_end:
            handle.seek(0, os.SEEK_END)
        while True:
            pos = handle.tell()
            line = handle.readline()
            if not line:
                handle.seek(pos)
                time.sleep(poll_ms / 1000.0)
                continue
            yield line


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
    return 0.0


def sell_quote_sol(row):
    rr = row.get("raw_record") or {}
    if finite(rr.get("sellQuoteLamports")):
        return float(rr["sellQuoteLamports"]) / 1_000_000_000
    return 0.0


def sell_quote_known(row):
    rr = row.get("raw_record") or {}
    return finite(rr.get("sellQuoteLamports"))


def first_buyer_share_bps(row):
    candidate = row.get("candidate") or {}
    value = candidate.get("first_buyer_quote_share_bps")
    return value if finite(value) else None


def score(entry_ts, entry_mc, marks, tp, sl, hold_ms, cost_pct, sellfix=False):
    if not finite(entry_ts) or not finite(entry_mc) or entry_mc <= 0:
        return None
    first_tp = None
    first_sl = None
    terminal = None
    max_roi = -math.inf
    min_roi = math.inf
    tp_duration = 0
    tp_open = None
    last_t = None
    for t, mc in marks:
        if t < entry_ts or t > entry_ts + hold_ms:
            continue
        roi = ((mc / entry_mc) - 1.0) * 100.0 - cost_pct
        max_roi = max(max_roi, roi)
        min_roi = min(min_roi, roi)
        terminal = (t, roi)
        if roi >= tp and first_tp is None:
            first_tp = (t, roi)
            tp_open = t
        if roi < tp and tp_open is not None:
            tp_duration += t - tp_open
            tp_open = None
        if roi <= sl and first_sl is None:
            first_sl = (t, roi)
        last_t = t
    if tp_open is not None and last_t is not None:
        tp_duration += last_t - tp_open
    if first_tp and (not first_sl or first_tp[0] <= first_sl[0]):
        pnl = tp - (3.0 if sellfix else 0.0)
        return {"outcome": "tp", "pnlPct": pnl, "exitMs": first_tp[0] - entry_ts, "maxRoiPct": max_roi, "minRoiPct": min_roi, "tpDurationMs": tp_duration}
    if first_sl:
        return {"outcome": "sl", "pnlPct": sl, "exitMs": first_sl[0] - entry_ts, "maxRoiPct": max_roi, "minRoiPct": min_roi, "tpDurationMs": tp_duration}
    if terminal:
        return {"outcome": "max_hold", "pnlPct": max(-100.0, min(100.0, terminal[1])), "exitMs": terminal[0] - entry_ts, "maxRoiPct": max_roi if finite(max_roi) else None, "minRoiPct": min_roi if finite(min_roi) else None, "tpDurationMs": tp_duration}
    return None


class Writer:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.handle = open(path, "a", encoding="utf-8")

    def write(self, row):
        self.handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        self.handle.flush()


class Top3Prospective:
    def __init__(self, args):
        self.args = args
        self.flow = defaultdict(deque)
        self.seen_mints = defaultdict(set)
        self.pending = {}
        self.seen = 0
        self.qualified = defaultdict(int)
        self.closed = defaultdict(int)
        self.qual_out = {
            rule: Writer(os.path.join(args.base_out_dir, rule, "qualifications.jsonl"))
            for rule in RULES
        }
        self.outcome_out = {
            rule: Writer(os.path.join(args.base_out_dir, rule, "outcomes.jsonl"))
            for rule in self.qual_out
        }
        self.status_out = {
            rule: Writer(os.path.join(args.base_out_dir, rule, "status.jsonl"))
            for rule in self.qual_out
        }

    def update_flow(self, row):
        mint = mint_of(row)
        ts = ts_of(row)
        mc = mc_of(row)
        if not mint or not finite(ts) or not finite(mc):
            return None
        rr = row.get("raw_record") or {}
        side = rr.get("side")
        sell_known = True
        if side == "buy":
            amount = quote_sol(row)
        elif side == "sell":
            sell_known = sell_quote_known(row)
            amount = -sell_quote_sol(row)
        else:
            amount = 0.0
        dq = self.flow[mint]
        dq.append((int(ts), amount, side, rr.get("wallet"), sell_known))
        cutoff = int(ts) - 120_000
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        return mint, int(ts), float(mc), row

    def window(self, mint, ts, ms):
        start = ts - ms
        vals = [x for x in self.flow[mint] if x[0] >= start and x[0] <= ts]
        buy_count = sum(1 for x in vals if x[2] == "buy")
        sell_count = sum(1 for x in vals if x[2] == "sell")
        missing_sell_quote_count = sum(1 for x in vals if x[2] == "sell" and not x[4])
        partial_net = sum(x[1] for x in vals)
        buy_total = sum(x[1] for x in vals if x[2] == "buy" and x[1] > 0)
        sell_total = -sum(x[1] for x in vals if x[2] == "sell" and x[1] < 0)
        true_net = buy_total - sell_total if missing_sell_quote_count == 0 else None
        buyers = {x[3] for x in vals if x[2] == "buy" and x[3]}
        by_wallet = defaultdict(float)
        for _, amount, side, wallet, _sell_known in vals:
            if side == "buy" and wallet and amount > 0:
                by_wallet[wallet] += amount
        dominance = (max(by_wallet.values()) / buy_total) if buy_total > 0 and by_wallet else None
        return {
            "net": partial_net,
            "gross_buy": buy_total,
            "true_net": true_net,
            "true_net_safe": missing_sell_quote_count == 0,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "missing_sell_quote_count": missing_sell_quote_count,
            "buy_sol": buy_total,
            "sell_sol": sell_total,
            "unique_buyers": len(buyers),
            "dominance": dominance,
        }

    def burst_windows(self, mint, ts):
        windows = {}
        for ms in [25, 50, 75, 100, 150, 250, 500, 1000, 2000, 5000]:
            w = self.window(mint, ts, ms)
            suffix = f"{ms}ms" if ms < 1000 else f"{ms // 1000}s"
            windows[f"netBuySol{suffix}"] = w["net"]
            windows[f"grossBuySol{suffix}"] = w["gross_buy"]
            windows[f"trueNetBuySol{suffix}"] = w["true_net"]
            windows[f"trueNetPointInTimeSafe{suffix}"] = w["true_net_safe"]
            windows[f"buyCount{suffix}"] = w["buy_count"]
            windows[f"sellCount{suffix}"] = w["sell_count"]
            windows[f"missingSellQuoteCount{suffix}"] = w["missing_sell_quote_count"]
            windows[f"buySol{suffix}"] = w["buy_sol"]
            windows[f"sellSol{suffix}"] = w["sell_sol"]
            windows[f"uniqueBuyers{suffix}"] = w["unique_buyers"]
            windows[f"dominance{suffix}"] = w["dominance"]
        return windows

    def maybe_qualify(self, mint, ts, mc, row):
        w1 = self.window(mint, ts, 1000)
        w2 = self.window(mint, ts, 2000)
        w5 = self.window(mint, ts, 5000)
        w50 = self.window(mint, ts, 50)
        windows = self.burst_windows(mint, ts)
        windows["firstBuyerQuoteShareBps"] = first_buyer_share_bps(row)
        candidates = [
            ("net1s_ge_5_prospective_v1", w1["net"] >= 5.0),
            ("net2s_ge_5_prospective_v1", w2["net"] >= 5.0),
            ("bc50_ge2_net5s_ge5_prospective_v1", w50["buy_count"] >= 2 and w5["net"] >= 5.0),
            ("grossbuy_1s_ge5_prospective_v1", w1["gross_buy"] >= 5.0),
            ("grossbuy_2s_ge5_prospective_v1", w2["gross_buy"] >= 5.0),
            ("bc50_ge2_grossbuy5s_ge5_prospective_v1", w50["buy_count"] >= 2 and w5["gross_buy"] >= 5.0),
            ("truenet_1s_ge5_prospective_v1", w1["true_net_safe"] and finite(w1["true_net"]) and w1["true_net"] >= 5.0),
            ("truenet_2s_ge5_prospective_v1", w2["true_net_safe"] and finite(w2["true_net"]) and w2["true_net"] >= 5.0),
            ("bc50_ge2_truenet5s_ge5_prospective_v1", w50["buy_count"] >= 2 and w5["true_net_safe"] and finite(w5["true_net"]) and w5["true_net"] >= 5.0),
        ]
        for rule, ok in candidates:
            if not ok or mint in self.seen_mints[rule]:
                continue
            self.seen_mints[rule].add(mint)
            latency = stable_latency_ms(mint)
            qualification_wall_ms = int(time.time() * 1000)
            rule_truth_at = ts
            flow_observed_at = row.get("local_receive_ts_ms") or row.get("decode_complete_ts_ms") or ts
            feature_ready_at = row.get("candidate_ready_ts_ms") or row.get("decode_complete_ts_ms") or flow_observed_at
            authority_ready_at = (row.get("canonical_state") or {}).get("observed_at_ms") or feature_ready_at
            would_send_at = max(qualification_wall_ms, authority_ready_at) + latency
            qrow = {
                "event": "top3_flow_prospective_qualification",
                "rule": rule,
                "mint": mint,
                "qualificationAtMs": ts,
                "ruleTruthAtMs": rule_truth_at,
                "flowEventObservedAtMs": flow_observed_at,
                "flowFeatureReadyAtMs": feature_ready_at,
                "prospectiveQualificationAtMs": qualification_wall_ms,
                "candidateSelectedAtMs": qualification_wall_ms,
                "authoritativeStateReadyAtMs": authority_ready_at,
                "shadowWouldFirstSendAtMs": would_send_at,
                "latencyComponentsMs": {
                    "ruleTruthToFeatureReady": feature_ready_at - rule_truth_at if finite(feature_ready_at) and finite(rule_truth_at) else None,
                    "featureReadyToQualification": qualification_wall_ms - feature_ready_at if finite(feature_ready_at) else None,
                    "qualificationToSelected": 0,
                    "selectedToAuthoritativeReady": max(0, authority_ready_at - qualification_wall_ms) if finite(authority_ready_at) else None,
                    "authoritativeReadyToWouldSend": would_send_at - max(qualification_wall_ms, authority_ready_at) if finite(authority_ready_at) else latency,
                    "ruleTruthToWouldSendTotal": would_send_at - rule_truth_at if finite(rule_truth_at) else None,
                },
                "mcUsd": mc,
                "sourceSlot": row.get("source_slot"),
                "sourceTransactionIndex": row.get("source_transaction_index"),
                "source": row.get("source"),
                "tokenProgram": (row.get("candidate") or {}).get("token_program"),
                "executableState": (row.get("canonical_state") or {}).get("unavailable_reason") is None and (row.get("canonical_state") or {}).get("curve_complete") is not True,
                "windows": windows,
                "idealExecutableEntry": {"entryTsMs": ts, "entryMcUsd": mc},
                "fastInfraLatencyAdjustedEntry": {"latencyMs": latency, "targetEntryTsMs": ts + latency, "entryTsMs": None, "entryMcUsd": None},
                "tpPct": self.args.tp_pct,
                "slPct": self.args.sl_pct,
                "maxHoldMs": self.args.max_hold_ms,
                "noSubmit": True,
            }
            self.qual_out[rule].write(qrow)
            self.pending[(rule, mint)] = {
                "q": qrow,
                "marks": [(ts, mc)],
                "entry": None,
                "mainTerminal": None,
                "wouldSendEntry": None,
                "wouldSendTerminal": None,
            }
            if latency == 0:
                self.pending[(rule, mint)]["entry"] = (ts, mc)
                qrow["fastInfraLatencyAdjustedEntry"].update({"entryTsMs": ts, "entryMcUsd": mc, "entryDeteriorationPct": 0.0})
            if finite(would_send_at) and ts >= would_send_at:
                self.pending[(rule, mint)]["wouldSendEntry"] = (ts, mc)
            self.qualified[rule] += 1

    def update_pending(self, mint, ts, mc):
        for key, state in list(self.pending.items()):
            rule, pmint = key
            qrow = state["q"]
            qts = qrow["qualificationAtMs"]
            if ts > qts + self.args.max_hold_ms + self.args.stale_grace_ms:
                self.close(key)
                continue
            if pmint != mint:
                continue
            state["marks"].append((ts, mc))
            target = qrow["fastInfraLatencyAdjustedEntry"]["targetEntryTsMs"]
            if state["entry"] is None and ts >= target:
                state["entry"] = (ts, mc)
                qrow["fastInfraLatencyAdjustedEntry"].update({
                    "entryTsMs": ts,
                    "entryMcUsd": mc,
                    "entryDeteriorationPct": ((mc / qrow["mcUsd"]) - 1.0) * 100.0 if qrow["mcUsd"] else None,
                })
            would_target = qrow.get("shadowWouldFirstSendAtMs")
            if state["wouldSendEntry"] is None and finite(would_target) and ts >= would_target:
                state["wouldSendEntry"] = (ts, mc)
            if state["entry"] is not None and state["mainTerminal"] is None:
                entry_ts, entry_mc = state["entry"]
                s = score(entry_ts, entry_mc, state["marks"], self.args.tp_pct, self.args.sl_pct, self.args.max_hold_ms, self.args.cost_bps / 100.0)
                if s and s["outcome"] in ("tp", "sl") and s["exitMs"] is not None:
                    state["mainTerminal"] = s
            if state["wouldSendEntry"] is not None and state["wouldSendTerminal"] is None:
                wentry_ts, wentry_mc = state["wouldSendEntry"]
                ws = score(wentry_ts, wentry_mc, state["marks"], self.args.tp_pct, self.args.sl_pct, self.args.max_hold_ms, self.args.cost_bps / 100.0)
                if ws and ws["outcome"] in ("tp", "sl") and ws["exitMs"] is not None:
                    state["wouldSendTerminal"] = ws
            if state["mainTerminal"] is not None and state["wouldSendTerminal"] is not None:
                self.close(key)

    def close(self, key):
        state = self.pending.pop(key, None)
        if not state:
            return
        rule, mint = key
        qrow = state["q"]
        entry = state["entry"]
        if entry is None:
            outcome = {"outcome": "no_fill", "pnlPct": -self.args.cost_bps / 100.0, "exitMs": None}
        else:
            outcome = state.get("mainTerminal") or score(entry[0], entry[1], state["marks"], self.args.tp_pct, self.args.sl_pct, self.args.max_hold_ms, self.args.cost_bps / 100.0) or {"outcome": "max_hold", "pnlPct": -self.args.cost_bps / 100.0, "exitMs": self.args.max_hold_ms}
        sellfix = dict(outcome)
        if sellfix.get("outcome") == "tp":
            sellfix["pnlPct"] = sellfix.get("pnlPct", self.args.tp_pct) - self.args.sellfix_tp_penalty_pp
        wentry = state.get("wouldSendEntry")
        if wentry is None:
            would_outcome = {"outcome": "no_fill", "pnlPct": -self.args.cost_bps / 100.0, "exitMs": None}
        else:
            would_outcome = state.get("wouldSendTerminal") or score(wentry[0], wentry[1], state["marks"], self.args.tp_pct, self.args.sl_pct, self.args.max_hold_ms, self.args.cost_bps / 100.0) or {"outcome": "max_hold", "pnlPct": -self.args.cost_bps / 100.0, "exitMs": self.args.max_hold_ms}
        would_sellfix = dict(would_outcome)
        if would_sellfix.get("outcome") == "tp":
            would_sellfix["pnlPct"] = would_sellfix.get("pnlPct", self.args.tp_pct) - self.args.sellfix_tp_penalty_pp
        out = dict(qrow)
        out.update({
            "event": "top3_flow_prospective_outcome",
            "entryTsMs": entry[0] if entry else None,
            "entryMcUsd": entry[1] if entry else None,
            "outcome": outcome.get("outcome"),
            "cleanExecutablePnlPct": outcome.get("pnlPct"),
            "feeAwarePnlPct": outcome.get("pnlPct"),
            "sellFixV11ExpectedPnlPct": sellfix.get("pnlPct"),
            "exitMs": outcome.get("exitMs"),
            "maxRoiPct": outcome.get("maxRoiPct"),
            "minRoiPct": outcome.get("minRoiPct"),
            "tpDurationMs": outcome.get("tpDurationMs"),
            "wouldSendParityEntry": {
                "entryTsMs": wentry[0] if wentry else None,
                "entryMcUsd": wentry[1] if wentry else None,
                "entryDeteriorationPct": ((wentry[1] / qrow["mcUsd"]) - 1.0) * 100.0 if wentry and qrow["mcUsd"] else None,
            },
            "wouldSendParityOutcome": would_outcome.get("outcome"),
            "wouldSendParityPnlPct": would_outcome.get("pnlPct"),
            "wouldSendParitySellFixV11ExpectedPnlPct": would_sellfix.get("pnlPct"),
            "wouldSendParityExitMs": would_outcome.get("exitMs"),
            "wouldSendParityMaxRoiPct": would_outcome.get("maxRoiPct"),
            "wouldSendParityMinRoiPct": would_outcome.get("minRoiPct"),
            "wouldSendParityTpDurationMs": would_outcome.get("tpDurationMs"),
            "observedMarks": len(state["marks"]),
        })
        self.outcome_out[rule].write(out)
        self.closed[rule] += 1

    def emit_status(self):
        now = int(time.time() * 1000)
        for rule in self.qual_out:
            self.status_out[rule].write({
                "event": "top3_flow_prospective_status",
                "rule": rule,
                "seen": self.seen,
                "qualified": self.qualified[rule],
                "closed": self.closed[rule],
                "open": sum(1 for r, _ in self.pending if r == rule),
                "tsMs": now,
                "noSubmit": True,
            })

    def ingest(self, row):
        self.seen += 1
        parsed = self.update_flow(row)
        if parsed:
            mint, ts, mc, source = parsed
            self.update_pending(mint, ts, mc)
            self.maybe_qualify(mint, ts, mc, source)
        if self.seen % self.args.status_every == 0:
            self.emit_status()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--base-out-dir", required=True)
    p.add_argument("--start-at-end", action="store_true")
    p.add_argument("--poll-ms", type=int, default=25)
    p.add_argument("--status-every", type=int, default=5000)
    p.add_argument("--tp-pct", type=float, default=TP_DEFAULT)
    p.add_argument("--sl-pct", type=float, default=SL_DEFAULT)
    p.add_argument("--max-hold-ms", type=int, default=HOLD_MS_DEFAULT)
    p.add_argument("--cost-bps", type=float, default=250.0)
    p.add_argument("--sellfix-tp-penalty-pp", type=float, default=3.0)
    p.add_argument("--stale-grace-ms", type=int, default=5000)
    args = p.parse_args()
    runner = Top3Prospective(args)
    for line in read_tail(args.input, args.start_at_end, args.poll_ms):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        runner.ingest(row)


if __name__ == "__main__":
    main()
