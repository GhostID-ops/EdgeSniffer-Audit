#!/usr/bin/env python3
"""Resolve status-cache nulls via batched archival getTransaction calls."""

import argparse
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path


def rpc_url(env: Path) -> str:
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("SECURE_RPC_URL="):
            return line.split("=", 1)[1].strip().strip("\"").strip("'")
    raise RuntimeError("SECURE_RPC_URL unavailable")


def save(path: Path, value: dict[str, str]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", required=True)
    parser.add_argument("--env", default="/root/bot/EdgeSniffer/.env")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    path = Path(args.cache)
    cache = json.loads(path.read_text())
    pending = sorted(signature for signature, status in cache.items() if status == "UNKNOWN")
    if args.limit:
        pending = pending[:args.limit]
    endpoint = rpc_url(Path(args.env))
    for offset in range(0, len(pending), args.batch_size):
        chunk = pending[offset:offset + args.batch_size]
        payload_rows = [{"jsonrpc": "2.0", "id": index, "method": "getTransaction",
                         "params": [signature, {"encoding": "base64", "commitment": "finalized", "maxSupportedTransactionVersion": 0}]}
                        for index, signature in enumerate(chunk)]
        payload = payload_rows[0] if len(payload_rows) == 1 else payload_rows
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": "8ycb-read-only-tx-status-audit"})
        attempt = 0
        while True:
            attempt += 1
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    answer = json.load(response)
                if isinstance(answer, dict) and len(payload_rows) == 1:
                    answer = [answer]
                if not isinstance(answer, list):
                    raise RuntimeError(json.dumps(answer)[:1000])
                break
            except Exception:
                if attempt >= 8:
                    raise
                time.sleep(min(10, attempt * 2))
        by_id = {int(row["id"]): row for row in answer}
        for index, signature in enumerate(chunk):
            result = (by_id.get(index) or {}).get("result")
            if result is not None:
                cache[signature] = "SUCCESS" if (result.get("meta") or {}).get("err") is None else "FAILED"
        save(path, cache)
        print(json.dumps({"processed": min(offset + len(chunk), len(pending)), "pending_run": len(pending),
                          "counts": dict(Counter(cache.values()))}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
