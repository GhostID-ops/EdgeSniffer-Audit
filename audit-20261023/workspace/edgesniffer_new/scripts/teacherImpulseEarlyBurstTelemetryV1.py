#!/usr/bin/env python3
import argparse
import json
import math
import os
import time
from collections import defaultdict, deque

WINDOWS_MS = [25, 50, 75, 100, 150, 250, 500]


def finite_number(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def obs_ts(row):
    return row.get("decode_complete_ts_ms") or row.get("local_receive_ts_ms")


def side(row):
    raw = row.get("side")
    if isinstance(raw, str):
        return raw.lower()
    raw_record = row.get("raw_record") or {}
    raw = raw_record.get("side")
    return raw.lower() if isinstance(raw, str) else "unknown"


def quote_sol(row):
    value = row.get("quote_sol")
    if finite_number(value):
        return float(value)
    lamports = row.get("quote_lamports")
    if isinstance(lamports, int):
        return lamports / 1_000_000_000
    raw_record = row.get("raw_record") or {}
    lamports = raw_record.get("quoteLamports")
    if isinstance(lamports, int):
        return lamports / 1_000_000_000
    return 0.0


def wallet(row):
    value = row.get("wallet")
    if isinstance(value, str):
        return value
    raw_record = row.get("raw_record") or {}
    value = raw_record.get("wallet")
    return value if isinstance(value, str) else None


def market_cap(row):
    canonical = row.get("canonical_state") or {}
    candidate = row.get("candidate") or {}
    return canonical.get("market_cap_usd") or candidate.get("market_cap_usd")


class EarlyBurstTelemetry:
    def __init__(self, args):
        self.args = args
        self.flows = defaultdict(deque)
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        self.out = open(args.output, "a", encoding="utf-8")
        self.seen = 0

    def write(self, row):
        self.out.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        self.out.flush()

    def ingest(self, row):
        ts = obs_ts(row)
        mint = row.get("mint") or (row.get("candidate") or {}).get("mint")
        if not mint or not isinstance(ts, int):
            return
        s = side(row)
        q = quote_sol(row)
        w = wallet(row)
        flows = self.flows[mint]
        flows.append({"ts": ts, "side": s, "quote": q, "wallet": w})
        cutoff = ts - self.args.keep_ms
        while flows and flows[0]["ts"] < cutoff:
            flows.popleft()

        features = {}
        for window in WINDOWS_MS:
            start = ts - window
            recent = [point for point in flows if point["ts"] >= start]
            buys = [point for point in recent if point["side"] == "buy"]
            sells = [point for point in recent if point["side"] == "sell"]
            buy_sol = sum(point["quote"] for point in buys)
            sell_sol = sum(point["quote"] for point in sells)
            wallets = {point["wallet"] for point in buys if point["wallet"]}
            largest = max([point["quote"] for point in buys], default=0.0)
            first = min(buys, key=lambda point: point["ts"])["quote"] if buys else 0.0
            prefix = f"{window}ms"
            features[f"buy_count_{prefix}"] = len(buys)
            features[f"sell_count_{prefix}"] = len(sells)
            features[f"net_buy_sol_{prefix}"] = buy_sol - sell_sol
            features[f"unique_buyers_{prefix}"] = len(wallets)
            features[f"dominant_buy_share_{prefix}"] = (
                largest / buy_sol if buy_sol > 0 else None
            )
            features[f"first_buyer_quote_share_bps_{prefix}"] = (
                round((first / buy_sol) * 10_000) if buy_sol > 0 else None
            )

        self.write(
            {
                "event": "teacher_impulse_early_burst_telemetry_v1",
                "mint": mint,
                "tsMs": ts,
                "side": s,
                "wallet": w,
                "quoteSol": q,
                "marketCapUsd": market_cap(row),
                "source": row.get("source"),
                "sourceSlot": row.get("source_slot"),
                "sourceTransactionIndex": row.get("source_transaction_index"),
                "features": features,
            }
        )
        self.seen += 1
        if self.seen % self.args.status_every == 0:
            self.write(
                {
                    "event": "teacher_impulse_early_burst_status",
                    "seen": self.seen,
                    "trackedMints": len(self.flows),
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
    parser.add_argument("--output", required=True)
    parser.add_argument("--keep-ms", type=int, default=60000)
    parser.add_argument("--poll-ms", type=int, default=25)
    parser.add_argument("--status-every", type=int, default=5000)
    parser.add_argument("--start-at-end", action="store_true")
    args = parser.parse_args()

    telemetry = EarlyBurstTelemetry(args)
    for line in follow_jsonl(args.input, args.start_at_end, args.poll_ms):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        telemetry.ingest(row)


if __name__ == "__main__":
    main()
