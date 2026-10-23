#!/usr/bin/env python3
"""Materialize exact same-pool Raydium CLMM contemporaneous controls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import raydiumClmmExactEconomicsV1 as clmm


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--control-seconds", type=int, default=5)
    args = parser.parse_args()
    anchors = {}
    market_rows = {}
    for line in args.windows.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("record_type") == "ANCHOR_WINDOW":
            anchors[str(row.get("anchor_id"))] = row
        elif row.get("record_type") == "MARKET_TRANSACTION":
            market_rows[str(row.get("signature"))] = row
    output = []
    seen = set()
    for anchor_id, anchor in anchors.items():
        pool = str(anchor.get("pool") or "")
        anchor_time = int(anchor.get("anchor_block_time") or 0)
        wallet = str(anchor.get("wallet") or "")
        for signature in anchor.get("window_signatures") or []:
            key = (anchor_id, str(signature))
            if key in seen:
                continue
            row = market_rows.get(str(signature))
            if not row:
                continue
            block_time = int(row.get("block_time") or 0)
            if anchor_time and not anchor_time + 1 <= block_time <= anchor_time + args.control_seconds:
                continue
            tx = row.get("raw_transaction") or {}
            try:
                transitions = clmm.validate_transaction(tx)
            except Exception:
                continue
            for index, transition in enumerate(transitions):
                if str(transition.get("pool") or "") != pool:
                    continue
                if str(transition.get("payer") or "") == wallet:
                    continue
                output.append({
                    "schema": "WALLET_RAYDIUM_CLMM_CONTROLS_V1",
                    "record_type": "CONTROL_SAME_POOL_WINDOW",
                    "anchor_id": anchor_id,
                    "anchor_signature": anchor.get("anchor_signature"),
                    "control_signature": signature,
                    "control_event_index": index,
                    "pool": pool,
                    "block_time": block_time,
                    "route": anchor.get("route"),
                    "exact_transition_validated": True,
                    "tracked_wallet": wallet,
                    "control_scope": "same native CLMM pool, +1..+%ss after anchor block time" % args.control_seconds,
                    "transition": transition,
                })
            seen.add(key)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in output), encoding="utf-8")
    print(json.dumps({"status": "RAYDIUM_CLMM_CONTROLS_COMPLETE", "anchors": len(anchors), "controls": len(output)}, sort_keys=True))


if __name__ == "__main__":
    main()
