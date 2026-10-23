#!/usr/bin/env python3
"""Bounded read-only wallet activity audit using the existing Helius access."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
from pathlib import Path
import requests


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat().replace("+00:00", "Z") if ts else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    key = os.environ.get("HELIUS_API_KEY", "")
    if not key:
        raise SystemExit("HELIUS_API_KEY unavailable")
    session = requests.Session()
    rows, before = [], None
    for _ in range(max(1, min(args.pages, 20))):
        params = {"api-key": key, "limit": 100}
        if before:
            params["before"] = before
        response = session.get(f"https://api.helius.xyz/v0/addresses/{args.wallet}/transactions", params=params, timeout=60)
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list) or not page:
            break
        rows.extend(page)
        before = page[-1].get("signature")
        if len(page) < 100:
            break

    type_counts = collections.Counter()
    source_counts = collections.Counter()
    venue_counts = collections.Counter()
    program_counts = collections.Counter()
    successful = 0
    failed = 0
    timestamps = []
    signatures = set()
    swap_like = 0
    for row in rows:
        sig = row.get("signature")
        if sig:
            signatures.add(sig)
        ts = row.get("timestamp")
        if ts:
            timestamps.append(int(ts))
        if row.get("transactionError") is None:
            successful += 1
        else:
            failed += 1
        typ = str(row.get("type") or "UNKNOWN")
        source = str(row.get("source") or "UNKNOWN")
        type_counts[typ] += 1
        source_counts[source] += 1
        text = " ".join(str(row.get(k) or "") for k in ("type", "source", "description", "feePayer")).upper()
        if any(x in text for x in ("SWAP", "BUY", "SELL", "TRADE")):
            swap_like += 1
        for venue, needles in {
            "PUMPSWAP": ("PUMPSWAP", "PUMP_SWAP"),
            "RAYDIUM": ("RAYDIUM",),
            "ORCA": ("ORCA",),
            "METEORA": ("METEORA",),
            "PUMP_FUN": ("PUMP_FUN", "PUMP.FUN"),
        }.items():
            if any(n in text for n in needles):
                venue_counts[venue] += 1
        for ix in row.get("instructions") or []:
            pid = ix.get("programId")
            if pid:
                program_counts[str(pid)] += 1

    result = {
        "schema": "WALLET_READONLY_ACTIVITY_AUDIT_V1",
        "wallet": args.wallet,
        "sample_limit": args.pages * 100,
        "transactions_fetched": len(rows),
        "unique_signatures": len(signatures),
        "successful_transactions": successful,
        "failed_transactions": failed,
        "swap_like_transactions": swap_like,
        "oldest_timestamp_utc": iso(min(timestamps)) if timestamps else None,
        "newest_timestamp_utc": iso(max(timestamps)) if timestamps else None,
        "transaction_types": type_counts,
        "sources": source_counts,
        "venue_text_matches": venue_counts,
        "top_program_ids": program_counts.most_common(20),
        "pnl_accessed": False,
        "execution_enabled": False,
        "full_meta_capture_started": False,
        "interpretation": "bounded activity/venue audit only; not a profitability or identity proof",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
