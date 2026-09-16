#!/usr/bin/env python3
"""Resolve cached Solana signatures against the public archival status RPC."""

import argparse
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def save(path: Path, value: dict[str, str]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", required=True)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    path = Path(args.cache)
    cache = json.loads(path.read_text())
    pending = sorted(signature for signature, status in cache.items() if status == "UNKNOWN")
    if args.limit:
        pending = pending[:args.limit]
    endpoint = "https://api.mainnet-beta.solana.com"
    chunks = [pending[offset:offset + args.batch_size] for offset in range(0, len(pending), args.batch_size)]

    def resolve(item: tuple[int, list[str]]) -> dict[str, str]:
        index, chunk = item
        payload = {"jsonrpc": "2.0", "id": index + 1, "method": "getSignatureStatuses",
                   "params": [chunk, {"searchTransactionHistory": True}]}
        attempt = 0
        while True:
            attempt += 1
            try:
                request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
                                                 headers={"Content-Type": "application/json", "User-Agent": "8ycb-read-only-status-audit"})
                with urllib.request.urlopen(request, timeout=90) as response:
                    result = json.load(response)["result"]["value"]
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt >= 20:
                    raise
                retry_after = float(exc.headers.get("Retry-After") or 2)
                time.sleep(max(2, retry_after))
            except Exception:
                if attempt >= 10:
                    raise
                time.sleep(min(10, attempt))
        return {signature: ("SUCCESS" if status.get("err") is None else "FAILED")
                for signature, status in zip(chunk, result) if status is not None}

    processed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(resolve, item) for item in enumerate(chunks)]
        for future in as_completed(futures):
            resolved = future.result()
            cache.update(resolved)
            processed += args.batch_size
            if processed % (args.batch_size * 10) == 0 or processed >= len(pending):
                save(path, cache)
                print(json.dumps({"processed": min(processed, len(pending)), "pending_initial": len(pending),
                                  "counts": dict(Counter(cache.values()))}, sort_keys=True), flush=True)
    save(path, cache)


if __name__ == "__main__":
    main()
