#!/usr/bin/env python3
"""Fetch canonical signature order for retained-overlap target CREATE slots."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import time
from pathlib import Path

import requests


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--batch", type=int, default=20)
    args = parser.parse_args()
    base = Path(args.dir)
    for path in [Path("/root/bot/EdgeSniffer/.env"), Path("/root/bot/EdgeSniffer/.env.live-v4-high-flow-runner")]:
        load_env(path)
    rpc_url = (
        os.environ.get("EDGE_ONCHAIN_RPC_URL")
        or os.environ.get("SOLANA_RPC_URL")
        or os.environ.get("RPC_URL")
        or os.environ.get("EDGE_LIVE_CONFIRM_RPC_URLS", "").split(",")[0].strip()
        or os.environ.get("SECURE_RPC_URL", "")
    )
    snapshot = json.loads((base / "stream_snapshot.json").read_text())
    launches = []
    with gzip.open(base / "target_launches.jsonl.gz", "rt") as handle:
        for line in handle:
            row = json.loads(line)
            if int(row["create_block_time"]) * 1000 >= int(snapshot["first_observation_ts_ms"]):
                launches.append(row)
    slots = sorted({int(row["create_slot"]) for row in launches})
    output = {}
    session = requests.Session()
    for start in range(0, len(slots), args.batch):
        batch_slots = slots[start:start + args.batch]
        payload = [
            {
                "jsonrpc": "2.0", "id": slot, "method": "getBlock",
                "params": [slot, {"commitment": "confirmed", "transactionDetails": "signatures", "rewards": False, "maxSupportedTransactionVersion": 0}],
            }
            for slot in batch_slots
        ]
        last_error = None
        for attempt in range(10):
            try:
                response = session.post(rpc_url, json=payload, timeout=180)
                if response.status_code == 200:
                    values = response.json()
                    if isinstance(values, list):
                        break
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            except Exception as exc:
                last_error = repr(exc)
            time.sleep(1 + attempt)
        else:
            raise RuntimeError(last_error)
        by_id = {int(value["id"]): value for value in values}
        for slot in batch_slots:
            value = by_id.get(slot, {})
            result = value.get("result")
            output[str(slot)] = {
                "slot": slot,
                "block_time": result.get("blockTime") if result else None,
                "signatures": result.get("signatures") if result else None,
                "error": value.get("error") if not result else None,
            }
        print(f"blocks {start + 1}-{min(len(slots), start + len(batch_slots))}/{len(slots)}", flush=True)
    with gzip.open(base / "canonical_block_signature_order.json.gz", "wt", compresslevel=6) as handle:
        json.dump(output, handle, separators=(",", ":"), sort_keys=True)
    print(json.dumps({"slots": len(slots), "complete": sum(bool(row.get("signatures")) for row in output.values()), "missing": sum(not row.get("signatures") for row in output.values())}))


if __name__ == "__main__":
    main()
