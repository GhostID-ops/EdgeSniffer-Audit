#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import os
import time
from collections import OrderedDict

EMPIRICAL_LATENCIES_MS = [0, 1, 1, 1, 2, 2, 4, 19, 20, 25]
NET_BUY_RULES = {
    "bc100_ge2_net60_ge_0p5_v1": 0.5,
    "bc100_ge2_net60_ge_1p0_v1": 1.0,
    "bc100_ge2_net60_ge_2p0_v1": 2.0,
}


def stable_latency_ms(mint: str) -> int:
    digest = hashlib.sha256(mint.encode("utf-8")).hexdigest()
    return EMPIRICAL_LATENCIES_MS[int(digest[:8], 16) % len(EMPIRICAL_LATENCIES_MS)]


def finite_number(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def candidate_ts_ms(row):
    provider = row.get("provider") or {}
    return (
        provider.get("candidate_ready_ts_ms")
        or provider.get("decode_complete_ts_ms")
        or provider.get("local_receive_ts_ms")
    )


def candidate_mc(row):
    canonical = row.get("canonical_state") or {}
    candidate = row.get("candidate") or {}
    return canonical.get("market_cap_usd") or candidate.get("market_cap_usd")


def event_id(mint, ts_ms, rule):
    raw = f"{rule}:{mint}:{ts_ms}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class JsonlWriter:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.file = open(path, "a", encoding="utf-8")

    def write(self, row):
        self.file.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        self.file.flush()


class ProspectiveShadow:
    def __init__(self, args):
        self.args = args
        self.rule = args.rule
        self.qualifications = JsonlWriter(os.path.join(args.out_dir, "qualifications.jsonl"))
        self.outcomes = JsonlWriter(os.path.join(args.out_dir, "outcomes.jsonl"))
        self.status = JsonlWriter(os.path.join(args.out_dir, "status.jsonl"))
        self.pending = OrderedDict()
        self.seen = 0
        self.qualified = 0
        self.closed = 0

    def qualifies(self, row):
        candidate = row.get("candidate") or {}
        bc100 = candidate.get("buy_count_100ms")
        if not isinstance(bc100, int) or bc100 < 2:
            return False
        if self.rule == "bc100_ge2_mc5000_prospective_v1":
            mc = candidate_mc(row)
            return finite_number(mc) and mc <= self.args.max_mc_usd
        if self.rule in NET_BUY_RULES:
            net_buy_sol_60 = candidate.get("net_buy_sol_60")
            return finite_number(net_buy_sol_60) and net_buy_sol_60 >= NET_BUY_RULES[self.rule]
        raise ValueError(f"unsupported prospective rule: {self.rule}")

    def features(self, row):
        candidate = row.get("candidate") or {}
        canonical = row.get("canonical_state") or {}
        provider = row.get("provider") or {}
        return {
            "mint": candidate.get("mint"),
            "qualificationTsMs": candidate_ts_ms(row),
            "selectedSlot": provider.get("source_slot"),
            "source": provider.get("source"),
            "mcUsd": candidate_mc(row),
            "buyCount100ms": candidate.get("buy_count_100ms"),
            "buyCount250ms": candidate.get("buy_count_250ms"),
            "buyCount500ms": candidate.get("buy_count_500ms"),
            "buyCount1000ms": candidate.get("buy_count_1000ms"),
            "firstBuyerQuoteShareBps": candidate.get("first_buyer_quote_share_bps"),
            "dominantBuyShare60": candidate.get("dominant_buy_share_60"),
            "netBuySol60": candidate.get("net_buy_sol_60"),
            "uniqueWallets60": candidate.get("unique_wallets_60"),
            "sellPressure60": candidate.get("sell_pressure_60"),
            "venue": canonical.get("venue"),
            "curveComplete": canonical.get("curve_complete"),
            "canonicalSource": canonical.get("source"),
            "canonicalStateObservedAtMs": canonical.get("observed_at_ms"),
            "executableState": canonical.get("unavailable_reason") is None
            and canonical.get("market_cap_usd") is not None
            and canonical.get("curve_complete") is not True,
        }

    def ingest(self, row):
        self.seen += 1
        ts_ms = candidate_ts_ms(row)
        candidate = row.get("candidate") or {}
        mint = candidate.get("mint")
        mc = candidate_mc(row)
        if not mint or not ts_ms or not finite_number(mc):
            return

        self.update_pending(mint, int(ts_ms), float(mc))

        if self.qualifies(row) and mint not in self.pending:
            latency = stable_latency_ms(mint)
            qualification = self.features(row)
            qualification.update(
                {
                    "event": "prospective_qualification",
                    "rule": self.rule,
                    "eventId": event_id(mint, ts_ms, self.rule),
                    "idealExecutableEntry": {
                        "entryTsMs": ts_ms,
                        "entryMcUsd": float(mc),
                    },
                    "fastInfraLatencyAdjustedEntry": {
                        "latencyMs": latency,
                        "targetEntryTsMs": int(ts_ms) + latency,
                        "entryTsMs": None,
                        "entryMcUsd": None,
                        "entryDeteriorationPct": None,
                    },
                    "outcome": None,
                    "feeAwarePnlSol": None,
                    "tpPct": self.args.tp_pct,
                    "slPct": self.args.sl_pct,
                    "maxHoldMs": self.args.max_hold_ms,
                }
            )
            self.qualifications.write(qualification)
            self.pending[mint] = {
                "qualification": qualification,
                "marks": [(int(ts_ms), float(mc))],
                "entry": None,
                "latency": latency,
                "target_ts": int(ts_ms) + latency,
            }
            if latency == 0:
                qualification["fastInfraLatencyAdjustedEntry"].update(
                    {
                        "entryTsMs": int(ts_ms),
                        "entryMcUsd": float(mc),
                        "entryDeteriorationPct": 0.0,
                    }
                )
                self.pending[mint]["entry"] = (int(ts_ms), float(mc))
            self.qualified += 1

        if self.seen % self.args.status_every == 0:
            self.emit_status()

    def update_pending(self, mint, ts_ms, mc):
        stale = []
        for key, state in list(self.pending.items()):
            q = state["qualification"]
            max_ts = q["qualificationTsMs"] + state["latency"] + self.args.max_hold_ms
            if ts_ms > max_ts + self.args.stale_grace_ms:
                self.close_position(key, max_ts, state["marks"][-1][1], "max_hold")
                continue
            if key != mint:
                continue
            state["marks"].append((ts_ms, mc))
            if state["entry"] is None and ts_ms >= state["target_ts"]:
                qmc = q["mcUsd"]
                state["entry"] = (ts_ms, mc)
                q["fastInfraLatencyAdjustedEntry"].update(
                    {
                        "entryTsMs": ts_ms,
                        "entryMcUsd": mc,
                        "entryDeteriorationPct": ((mc / qmc) - 1.0) * 100.0
                        if qmc
                        else None,
                    }
                )
            if state["entry"] is None:
                continue
            entry_ts, entry_mc = state["entry"]
            if ts_ms > entry_ts + self.args.max_hold_ms:
                self.close_position(key, ts_ms, mc, "max_hold")
                continue
            roi_pct = ((mc / entry_mc) - 1.0) * 100.0
            if roi_pct >= self.args.tp_pct:
                self.close_position(key, ts_ms, mc, "tp")
            elif roi_pct <= self.args.sl_pct:
                self.close_position(key, ts_ms, mc, "sl")
        for key in stale:
            self.pending.pop(key, None)

    def close_position(self, mint, exit_ts_ms, exit_mc_usd, reason):
        state = self.pending.pop(mint, None)
        if not state:
            return
        q = state["qualification"]
        if state["entry"] is None:
            outcome = "no_fill"
            pnl_pct = -self.args.cost_bps / 100.0
            entry_ts_ms = None
            entry_mc_usd = None
        else:
            entry_ts_ms, entry_mc_usd = state["entry"]
            outcome = reason
            pnl_pct = ((exit_mc_usd / entry_mc_usd) - 1.0) * 100.0 - (
                self.args.cost_bps / 100.0
            )
        current_realistic_pnl_pct = pnl_pct
        if outcome == "tp":
            current_realistic_pnl_pct -= self.args.realistic_tp_penalty_pp
        pnl_sol = pnl_pct / 100.0
        current_realistic_pnl_sol = current_realistic_pnl_pct / 100.0
        row = dict(q)
        row.update(
            {
                "event": "prospective_outcome",
                "outcome": outcome,
                "exitTsMs": exit_ts_ms,
                "exitMcUsd": exit_mc_usd,
                "entryTsMs": entry_ts_ms,
                "entryMcUsd": entry_mc_usd,
                "cleanExecutablePnlPct": pnl_pct,
                "cleanExecutablePnlSol": pnl_sol,
                "currentRealisticSellPnlPct": current_realistic_pnl_pct,
                "currentRealisticSellPnlSol": current_realistic_pnl_sol,
                "currentRealisticSellModel": f"tp_penalty_{self.args.realistic_tp_penalty_pp:g}pp_when_tp_else_clean",
                "feeAwarePnlPct": pnl_pct,
                "feeAwarePnlSol": pnl_sol,
                "observedMarks": len(state["marks"]),
            }
        )
        self.outcomes.write(row)
        self.closed += 1

    def emit_status(self):
        self.status.write(
            {
                "event": "prospective_status",
                "rule": self.rule,
                "seen": self.seen,
                "qualified": self.qualified,
                "closed": self.closed,
                "open": len(self.pending),
                "tsMs": int(time.time() * 1000),
            }
        )


def follow_jsonl(path, start_at_end, poll_ms):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        if start_at_end:
            handle.seek(0, os.SEEK_END)
        while True:
            where = handle.tell()
            line = handle.readline()
            if not line:
                handle.seek(where)
                time.sleep(poll_ms / 1000.0)
                continue
            yield line


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--rule", default="bc100_ge2_mc5000_prospective_v1")
    parser.add_argument("--max-mc-usd", type=float, default=5000.0)
    parser.add_argument("--tp-pct", type=float, default=20.0)
    parser.add_argument("--sl-pct", type=float, default=-20.0)
    parser.add_argument("--max-hold-ms", type=int, default=120000)
    parser.add_argument("--cost-bps", type=float, default=250.0)
    parser.add_argument("--realistic-tp-penalty-pp", type=float, default=17.0)
    parser.add_argument("--poll-ms", type=int, default=25)
    parser.add_argument("--status-every", type=int, default=5000)
    parser.add_argument("--stale-grace-ms", type=int, default=5000)
    parser.add_argument("--start-at-end", action="store_true")
    args = parser.parse_args()

    shadow = ProspectiveShadow(args)
    for line in follow_jsonl(args.input, args.start_at_end, args.poll_ms):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        shadow.ingest(row)


if __name__ == "__main__":
    main()
