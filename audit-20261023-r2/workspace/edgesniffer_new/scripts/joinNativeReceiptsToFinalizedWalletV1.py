#!/usr/bin/env python3
"""Join route-candidate native receipts to finalized tracked-wallet records.

The native observer cannot resolve address-table wallet membership without RPC.
It therefore records route-candidate receipt clocks; this read-only join waits
for the finalized wallet source to identify the tracked wallet and joins by
exact signature. No timing field is synthesized.
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def load_receipt_root(path: Path) -> list[dict[str, Any]]:
    """Load either a single append-only JSONL file or a JSONL directory."""
    if path.is_file():
        return load_jsonl(path)
    rows: list[dict[str, Any]] = []
    for child in glob.glob(str(path / "*.jsonl")):
        rows.extend(load_jsonl(Path(child)))
    return rows


def receipt_value(row: dict[str, Any], camel: str, snake: str) -> Any:
    """Accept both the Jito receipt schema and the AllenHark codec schema."""
    value = row.get(camel)
    return value if value is not None else row.get(snake)


def run_once(args: argparse.Namespace, seen: set[str]) -> int:
    finalized = {str(row.get("signature")): row for row in load_jsonl(args.finalized)
                 if row.get("signature")}
    receipts: dict[str, dict[str, Any]] = {}
    for receipt_root in args.receipts:
        for row in load_receipt_root(receipt_root):
            if row.get("signature"):
                receipts[str(row["signature"])] = row
    added = 0
    if finalized:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("a", encoding="utf-8") as handle:
            for signature in sorted(set(finalized) & set(receipts)):
                if signature in seen:
                    continue
                receipt = receipts[signature]
                row = finalized[signature]
                handle.write(json.dumps({
                    "schema": "NATIVE_FINALIZED_2CQ_WALLET_JOIN_V1",
                    "signature": signature,
                    "wallet": row.get("tracked_wallet"),
                    "slot": row.get("slot"),
                    "finalizedCapturedAtUtc": row.get("captured_at_utc"),
                    "nativeReceiveUnixNs": receipt_value(receipt, "nativeReceiveUnixNs", "native_receive_unix_ns"),
                    "nativeReceiveMonotonicNs": receipt_value(receipt, "nativeReceiveMonotonicNs", "native_receive_monotonic_ns"),
                    "nativeReceiveSource": receipt_value(receipt, "nativeReceiveSource", "native_receive_source"),
                    "nativeReceiveSlot": receipt_value(receipt, "nativeReceiveSlot", "native_receive_slot"),
                    "nativeReceiveGranularity": receipt_value(receipt, "nativeReceiveGranularity", "native_receive_granularity"),
                    "nativeReceiptMatchMode": receipt.get("matchMode") or receipt.get("schema"),
                    "nativeReceiptSchema": receipt.get("schema"),
                    "routeCandidate": True,
                }, sort_keys=True) + "\n")
                handle.flush()
                seen.add(signature)
                added += 1
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps({
        "schema": "NATIVE_FINALIZED_2CQ_WALLET_JOIN_STATE_V1",
        "seen_signatures": sorted(seen),
        "finalized_rows": len(finalized),
        "native_receipts": len(receipts),
        "joined_rows": len(seen),
    }, sort_keys=True) + "\n", encoding="utf-8")
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--finalized", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--poll-ms", type=int, default=1000)
    args = parser.parse_args()
    seen: set[str] = set()
    if args.state.exists():
        seen = set(json.loads(args.state.read_text(encoding="utf-8")).get("seen_signatures") or [])
    while True:
        print(json.dumps({"added": run_once(args, seen), "joined": len(seen)}), flush=True)
        time.sleep(args.poll_ms / 1000)


if __name__ == "__main__":
    main()
