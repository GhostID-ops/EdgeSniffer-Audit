#!/usr/bin/env python3
"""Bounded, read-only five-entry probe for a successor wallet."""
from __future__ import annotations
import argparse, datetime as dt, json, os
from pathlib import Path
import requests


def iso(ts):
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).isoformat().replace("+00:00", "Z")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--entries", type=int, default=5)
    args = ap.parse_args()
    key = os.environ.get("HELIUS_API_KEY")
    if not key:
        raise SystemExit("HELIUS_API_KEY unavailable")
    rows = []
    before = None
    target = max(1, min(args.entries, 150))
    while len(rows) < target:
        params = {"api-key": key, "limit": 100}
        if before:
            params["before"] = before
        page = requests.get(f"https://api.helius.xyz/v0/addresses/{args.wallet}/transactions", params=params, timeout=60).json()
        if not page:
            break
        for row in page:
            if row.get("type") != "SWAP":
                continue
            incoming = [t for t in row.get("tokenTransfers", []) if t.get("toUserAccount") == args.wallet]
            outgoing_lamports = sum(int(t.get("amount", 0)) for t in row.get("nativeTransfers", []) if t.get("fromUserAccount") == args.wallet)
            if not incoming:
                continue
            rows.append({
                "signature": row.get("signature"),
                "timestamp_utc": iso(row["timestamp"]),
                "source": row.get("source"),
                "type": row.get("type"),
                "direction": "BUY_LIKE_TOKEN_IN",
                "selected_mints": sorted({t.get("mint") for t in incoming if t.get("mint")}),
                "token_amounts": [{"mint": t.get("mint"), "amount": t.get("tokenAmount")} for t in incoming],
                "wallet_outgoing_native_lamports": outgoing_lamports,
                "wallet_outgoing_native_sol": outgoing_lamports / 1_000_000_000,
                "description": row.get("description", ""),
            })
            if len(rows) >= target:
                break
        before = page[-1].get("signature")
        if len(page) < 100:
            break
    result = {
        "schema": "WALLET_FIVE_ENTRY_PROBE_V1",
        "wallet": args.wallet,
        "entries_requested": target,
        "entries_captured": len(rows),
        "entries": rows,
        "read_only": True,
        "pnl_accessed": False,
        "full_meta_capture_started": False,
        "interpretation": "bounded entry-timing/venue/size probe; not a profitability result",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
