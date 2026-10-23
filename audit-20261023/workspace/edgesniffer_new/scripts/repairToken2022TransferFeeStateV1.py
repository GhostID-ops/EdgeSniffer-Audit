#!/usr/bin/env python3
"""Re-decode the append-only Token-2022 state stream after parser repairs.

This is receive-only evidence maintenance. It fetches finalized mint account
bytes again and writes a new output; the caller can preserve the old output
before replacing it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enrichNativePumpCurveStateV1 as bridge


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.input.open("r", encoding="utf-8") as source, args.output.open("w", encoding="utf-8") as target:
        for line in source:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            mint = str(row.get("mint") or "")
            if not mint:
                continue
            account = bridge.rpc_account_info(args.rpc_url, mint, args.timeout)
            state = bridge.decode_token2022_mint_state(account["data"])
            state.update({
                "mint": mint,
                "mintAccountOwner": account.get("owner"),
                "mintAccountLamports": account.get("lamports"),
                "mintAccountFetchedAtMs": int(time.time() * 1000),
            })
            row["mintState"] = state
            row["repair"] = {"schema": "TOKEN2022_TRANSFER_FEE_STATE_REPAIR_V1", "repairedAtMs": int(time.time() * 1000)}
            target.write(json.dumps(row, sort_keys=True) + "\n")
            count += 1
    print(json.dumps({"status": "REPAIRED", "rows": count, "output": str(args.output)}))


if __name__ == "__main__":
    main()
