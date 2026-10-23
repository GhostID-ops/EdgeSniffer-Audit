#!/usr/bin/env python3
"""Deduplicated pool-transaction windows for exact wallet anchors."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pumpSwapExactEconomicsV1 as pumpswap
from materializeWalletRouteCensusV1 import pumpswap_tx_with_extension

SCHEMA = "WALLET_POST_ENTRY_POOL_WINDOW_V1"
STATE_SCHEMA = "WALLET_POST_ENTRY_POOL_WINDOW_STATE_V1"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Rpc:
    def __init__(self, url: str, timeout: float = 60.0):
        self.url, self.timeout, self.request_id = url, timeout, 0

    def call(self, method: str, params: list[Any]) -> Any:
        self.request_id += 1
        payload = json.dumps({"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}).encode()
        request = urllib.request.Request(self.url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode())
        if result.get("error"):
            raise RuntimeError(f"RPC_{method}:{result['error']}")
        return result.get("result")


def fetch_transaction(url: str, signature: str) -> tuple[str, dict[str, Any] | None, str | None]:
    try:
        client = Rpc(url, timeout=30.0)
        tx = client.call("getTransaction", [signature, {"commitment": "finalized", "encoding": "json", "maxSupportedTransactionVersion": 1}])
        return signature, tx, None
    except Exception as error:
        return signature, None, type(error).__name__ + ":" + str(error)


def append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()


def state(path: Path, cohort: str, boundary: str) -> dict[str, Any]:
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("schema") != STATE_SCHEMA or value.get("cohort_id") != cohort or value.get("boundary_utc") != boundary:
                raise RuntimeError("FAIL_CLOSED: pool window state mismatch")
            return value
        except json.JSONDecodeError:
            # A process killed during the final state write leaves an empty
            # file. Preserve that evidence but recover into a clean pass.
            pass
    return {"schema": STATE_SCHEMA, "cohort_id": cohort, "boundary_utc": boundary, "source_byte_offset": 0, "processed_anchors": [], "seen_signatures": [], "written_records": 0, "anchors": 0, "market_transactions": 0, "errors": 0, "error_samples": []}


def exact_buy(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        tx, _variant = pumpswap_tx_with_extension(row.get("transaction") or {})
        transitions = pumpswap.validate_transaction(tx)
    except Exception:
        return None
    wallet = str(row.get("wallet") or "")
    matches = [item for item in transitions if item.get("side") == "BUY" and (not wallet or item.get("user") == wallet)]
    return matches[0] if len(matches) == 1 else None


def read_rows(path: Path, offset: int):
    if not path.exists():
        return
    with path.open("rb") as handle:
        if path.stat().st_size < offset:
            offset = 0
        handle.seek(offset)
        while True:
            line = handle.readline()
            if not line:
                break
            if line.strip():
                try:
                    yield json.loads(line.decode("utf-8")), handle.tell()
                except json.JSONDecodeError:
                    # The source is append-only and may be read while its
                    # current final line is still being written. Hold that
                    # partial line for the next cycle instead of crashing.
                    break


def run_once(client: Rpc, st: dict[str, Any], source: Path, output: Path, limit: int, horizon: int, max_anchors: int, workers: int) -> dict[str, int]:
    processed = set(st.get("processed_anchors") or [])
    seen = set(st.get("seen_signatures") or [])
    anchors = transactions = errors = rows_seen = 0
    offset = int(st.get("source_byte_offset", 0))
    for row, row_offset in read_rows(source, offset) or ():
        rows_seen += 1
        if anchors >= max_anchors:
            break
        if row.get("schema") != "WALLET_FINALIZED_ROUTE_RAW_TX_V1":
            offset = row_offset
            continue
        signature = str(row.get("signature") or "")
        offset = row_offset
        if not signature or signature in processed:
            continue
        transition = exact_buy(row)
        if not transition:
            continue
        anchor_slot = int(row.get("slot") or 0)
        anchor_time = int(row.get("block_time") or 0)
        pool = str(transition.get("pool") or "")
        try:
            summaries = client.call("getSignaturesForAddress", [pool, {"commitment": "finalized", "limit": limit}]) or []
            window_signatures: list[str] = []
            candidates: list[tuple[str, int, Any]] = []
            for summary in reversed(summaries):
                candidate = str(summary.get("signature") or "")
                slot = int(summary.get("slot") or 0)
                summary_time = summary.get("blockTime")
                if not candidate or slot < anchor_slot or slot > anchor_slot + 900:
                    continue
                if anchor_time and summary_time is not None and int(summary_time) > anchor_time + horizon:
                    continue
                window_signatures.append(candidate)
                if candidate not in seen and summary.get("err") is None:
                    candidates.append((candidate, slot, summary_time))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(fetch_transaction, client.url, candidate) for candidate, _, _ in candidates]
                fetched = {future.result()[0]: future.result()[1:] for future in futures}
            for candidate, slot, summary_time in candidates:
                tx, fetch_error = fetched.get(candidate, (None, "MISSING_FETCH"))
                if fetch_error:
                    errors += 1
                    continue
                if not tx or (tx.get("meta") or {}).get("err") is not None:
                    continue
                append(output, {
                    "schema": SCHEMA, "record_type": "MARKET_TRANSACTION",
                    "cohort_id": st["cohort_id"], "boundary_utc": st["boundary_utc"],
                    "captured_at_utc": now(), "anchor_signature_first_seen": signature,
                    "anchor_pool": pool, "signature": candidate, "slot": slot,
                    "block_time": tx.get("blockTime", summary_time), "transaction_sha256": digest(tx),
                    "raw_transaction": tx,
                })
                st["written_records"] += 1
                st["market_transactions"] += 1
                transactions += 1
                seen.add(candidate)
            append(output, {
                "schema": SCHEMA, "record_type": "ANCHOR_WINDOW",
                "cohort_id": st["cohort_id"], "boundary_utc": st["boundary_utc"],
                "captured_at_utc": now(), "anchor_signature": signature,
                "anchor_slot": anchor_slot, "anchor_block_time": anchor_time,
                "entry_delay_seconds": 1, "exit_horizon_seconds": horizon,
                "pool": pool, "window_signatures": sorted(set(window_signatures)),
            })
            st["written_records"] += 1
            st["anchors"] += 1
            anchors += 1
            processed.add(signature)
        except Exception as error:
            errors += 1
            if len(st.setdefault("error_samples", [])) < 20:
                st["error_samples"].append({"anchor_signature": signature, "pool": pool, "error": type(error).__name__ + ":" + str(error)})
    st["source_byte_offset"] = offset
    st["processed_anchors"] = list(processed)[-20_000:]
    st["seen_signatures"] = list(seen)[-100_000:]
    st["errors"] = int(st.get("errors", 0)) + errors
    st["updated_at_utc"] = now()
    return {"source_rows": rows_seen, "anchors": anchors, "market_transactions": transactions, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-url", default=os.environ.get("SECURE_RPC_URL") or os.environ.get("SOLANA_RPC_URL"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--boundary-utc", required=True)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--horizon-seconds", type=int, default=300)
    parser.add_argument("--max-anchors-per-cycle", type=int, default=5)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    if not args.rpc_url:
        raise SystemExit("FAIL_CLOSED: RPC URL is required")
    st = state(args.state, args.cohort_id, args.boundary_utc)
    client = Rpc(args.rpc_url)
    while True:
        result = run_once(client, st, args.source, args.output, args.limit, args.horizon_seconds, args.max_anchors_per_cycle, args.workers)
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(st, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result | {"error_samples": st.get("error_samples") or []}, sort_keys=True), flush=True)
        if args.interval_seconds <= 0:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
