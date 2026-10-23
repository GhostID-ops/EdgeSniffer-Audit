#!/usr/bin/env python3
"""Receive-only finalized RPC source for an explicitly named wallet."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
SCHEMA = "WALLET_FINALIZED_ROUTE_RAW_TX_V1"

def rpc(url: str, method: str, params: list[Any]) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload.get("result")

def load_env(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values

def account_keys(tx: dict[str, Any]) -> list[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = [x.get("pubkey") if isinstance(x, dict) else x for x in message.get("accountKeys") or []]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    return keys + list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])

def invoked_program_ids(tx: dict[str, Any]) -> set[str]:
    keys = account_keys(tx)
    result: set[str] = set()
    groups = [((tx.get("transaction") or {}).get("message") or {}).get("instructions") or []]
    groups += [group.get("instructions") or [] for group in (tx.get("meta") or {}).get("innerInstructions") or []]
    for group in groups:
        for ix in group:
            index = ix.get("programIdIndex")
            if isinstance(index, int) and 0 <= index < len(keys):
                result.add(str(keys[index]))
            elif ix.get("programId"):
                result.add(str(ix["programId"]))
    return result

def classify_programs(tx: dict[str, Any]) -> tuple[str, list[str]]:
    programs = sorted(invoked_program_ids(tx))
    if PUMPSWAP in programs:
        return "PUMPSWAP_PROGRAM", programs
    if PUMP in programs:
        return "PUMP_PROGRAM", programs
    return "OTHER_PROGRAM", programs

def sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def load_seen(path: pathlib.Path) -> set[str]:
    if not path.exists():
        return set()
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # A receive-only process can be interrupted during its final
                # append. Preserve the evidence and ignore the malformed
                # record; the repair procedure keeps the raw tail separately
                # before resuming this collector.
                continue
            signature = row.get("signature")
            if signature:
                seen.add(str(signature))
    return seen

def append_jsonl(path: pathlib.Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())

def fetch_signatures(url: str, wallet: str, limit: int, maximum: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    before: str | None = None
    while len(result) < maximum:
        options: dict[str, Any] = {"limit": min(limit, maximum - len(result))}
        if before:
            options["before"] = before
        page = rpc(url, "getSignaturesForAddress", [wallet, options]) or []
        if not page:
            break
        result.extend(page)
        before = str(page[-1].get("signature") or "")
        if len(page) < options["limit"]:
            break
    return result[:maximum]

def run_once(args: argparse.Namespace, url: str) -> dict[str, int]:
    seen = load_seen(args.output)
    summaries = fetch_signatures(url, args.wallet, args.page_size, args.max_signatures)
    pending = [summary for summary in reversed(summaries) if summary.get("signature") and str(summary["signature"]) not in seen and summary.get("err") is None]
    skipped = len(summaries) - len(pending)

    def fetch_one(summary: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
        signature = str(summary.get("signature") or "")
        try:
            tx = rpc(url, "getTransaction", [signature, {"commitment": "finalized", "encoding": "json", "maxSupportedTransactionVersion": 1}])
            return summary, tx, None
        except Exception as error:
            return summary, None, type(error).__name__ + ":" + str(error)

    fetched = written = failed = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_one, summary) for summary in pending]
        for future in as_completed(futures):
            summary, tx, error = future.result()
            signature = str(summary.get("signature") or "")
            if error:
                failed += 1
                continue
            fetched += 1
            seen.add(signature)
            if not tx or (tx.get("meta") or {}).get("err") is not None:
                skipped += 1
                continue
            family, programs = classify_programs(tx)
            block_time = tx.get("blockTime")
            append_jsonl(args.output, {
                "schema": SCHEMA,
                "wallet": args.wallet,
                "cohort_id": args.cohort_id,
                "boundary_utc": args.boundary_utc,
                "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                "finalized": True,
                "signature": signature,
                "slot": int(tx.get("slot") or summary.get("slot") or 0),
                "block_time": int(block_time) if block_time is not None else None,
                "program_family": family,
                "observed_program_ids": programs,
                "transaction_sha256": sha256(tx),
                "transaction": tx,
            })
            written += 1
    return {"signatures": len(summaries), "fetched": fetched, "written": written, "failed": failed, "skipped": skipped}

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--boundary-utc", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, default=pathlib.Path("/root/bot/EdgeSniffer/.env"))
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--max-signatures", type=int, default=10000)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    values = load_env(args.env)
    url = values.get("SECURE_RPC_URL") or values.get("SOLANA_RPC_URL")
    if not url:
        raise SystemExit("FAIL_CLOSED: SECURE_RPC_URL or SOLANA_RPC_URL is required")
    while True:
        print(json.dumps(run_once(args, url), sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)

if __name__ == "__main__":
    main()
