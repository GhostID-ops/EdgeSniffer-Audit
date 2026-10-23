#!/usr/bin/env python3
"""Deduplicated finalized block windows after wallet anchors.

This is receive-only research capture.  It records complete finalized blocks
once per slot and maps each wallet anchor to the blocks covering the explicit
one-second entry delay through the 300-second maximum exit horizon.
"""
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

import exactCurveEconomicsV1 as curve
import pumpSwapExactEconomicsV1 as pumpswap

SCHEMA = "WALLET_POST_ENTRY_MARKET_WINDOW_V1"
STATE_SCHEMA = "WALLET_POST_ENTRY_MARKET_WINDOW_STATE_V1"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Rpc:
    def __init__(self, url: str, timeout: float = 60.0):
        self.url, self.timeout, self.request_id = url, timeout, 0

    def call(self, method: str, params: list[Any]) -> Any:
        self.request_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}).encode()
        request = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode())
        if result.get("error"):
            raise RuntimeError(f"RPC_{method}:{result['error']}")
        return result.get("result")


def fetch_block(rpc_url: str, slot: int) -> tuple[int, dict[str, Any] | None, str | None]:
    try:
        client = Rpc(rpc_url, timeout=30.0)
        block = client.call("getBlock", [slot, {"commitment": "finalized", "transactionDetails": "full", "rewards": False, "encoding": "json", "maxSupportedTransactionVersion": 1}])
        return slot, block, None
    except Exception as error:
        return slot, None, type(error).__name__ + ":" + str(error)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_state(path: Path, cohort: str, boundary: str) -> dict[str, Any]:
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema") != STATE_SCHEMA or state.get("cohort_id") != cohort or state.get("boundary_utc") != boundary:
            raise RuntimeError("FAIL_CLOSED: market window state contract mismatch")
        return state
    return {
        "schema": STATE_SCHEMA, "cohort_id": cohort, "boundary_utc": boundary,
        "source_byte_offset": 0, "processed_anchors": [], "seen_slots": [],
        "written_records": 0, "anchor_records": 0, "block_records": 0,
        "errors": 0, "error_samples": [], "created_at_utc": utc_now(),
    }


def read_rows(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], offset
    if path.stat().st_size < offset:
        offset = 0
    rows: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            line = handle.readline()
            if not line:
                break
            if line.strip():
                rows.append(json.loads(line.decode("utf-8")))
        return rows, handle.tell()


def normalize_anchor(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("role") == "ANCHOR":
        return row
    if row.get("schema") == "WALLET_FINALIZED_ROUTE_RAW_TX_V1":
        return {
            "role": "ANCHOR",
            "anchor_signature": row.get("signature"),
            "anchor_slot": row.get("slot"),
            "block_time": row.get("block_time"),
            "wallet": row.get("wallet"),
            "raw_transaction": row.get("transaction") or {},
            "route": {},
        }
    return None


def resolved_keys(tx: dict[str, Any]) -> list[str]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    keys = [str(value.get("pubkey")) if isinstance(value, dict) else str(value) for value in message.get("accountKeys") or []]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    return keys + [str(value) for value in loaded.get("writable") or []] + [str(value) for value in loaded.get("readonly") or []]


def anchor_accounts(anchor: dict[str, Any]) -> set[str]:
    tx = anchor.get("raw_transaction") or {}
    accounts: set[str] = set()
    try:
        rows = pumpswap.validate_transaction(tx)
        for row in rows:
            if row.get("side") == "BUY":
                accounts.update(str(row.get(key)) for key in ("pool", "baseMint", "quoteMint") if row.get(key))
        if accounts:
            return accounts
    except Exception:
        pass
    keys = resolved_keys(tx)
    for row in (tx.get("meta") or {}).get("postTokenBalances") or []:
        if row.get("owner") == anchor.get("wallet"):
            accounts.add(str(row.get("mint") or ""))
    accounts.update(key for key in keys if key in {curve.PUMP_PROGRAM_ID})
    return {value for value in accounts if value}


def transaction_accounts(tx: dict[str, Any]) -> set[str]:
    return set(resolved_keys(tx))


def filter_block(block: dict[str, Any], accounts: set[str]) -> dict[str, Any] | None:
    if not accounts:
        return None
    selected = []
    for entry in block.get("transactions") or []:
        if transaction_accounts(entry) & accounts:
            selected.append(entry)
    if not selected:
        return None
    return block | {"transactions": selected}


def run_once(rpc: Rpc, state: dict[str, Any], source: Path, output: Path, max_anchors: int, slot_span: int, horizon_seconds: int, workers: int) -> dict[str, int]:
    processed = set(state.get("processed_anchors") or [])
    seen_slots = set(int(slot) for slot in state.get("seen_slots") or [])
    anchors = blocks = errors = 0
    source_rows = 0
    offset = int(state.get("source_byte_offset", 0))
    if source.exists() and source.stat().st_size < offset:
        offset = 0
    with source.open("rb") as handle:
        handle.seek(offset)
        while anchors < max_anchors:
            line = handle.readline()
            if not line:
                break
            source_rows += 1
            row = normalize_anchor(json.loads(line.decode("utf-8"))) if line.strip() else None
            offset = handle.tell()
            if row is None:
                continue
            signature = str(row.get("anchor_signature") or "")
            if not signature or signature in processed:
                continue
            anchor_slot = int(row.get("anchor_slot") or row.get("slot") or 0)
            anchor_time = int(row.get("block_time") or 0)
            eligible_after = anchor_time + 1 if anchor_time else None
            accounts = anchor_accounts(row)
            try:
                slot_end = anchor_slot + slot_span
                slots = rpc.call("getBlocks", [anchor_slot, slot_end, {"commitment": "finalized"}]) or []
                window_slots: list[int] = []
                pending_slots = [int(slot_value) for slot_value in slots if int(slot_value) not in seen_slots]
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [executor.submit(fetch_block, rpc.url, slot) for slot in pending_slots]
                    for future in as_completed(futures):
                        slot, block, fetch_error = future.result()
                        if fetch_error:
                            errors += 1
                        continue
                    if not block:
                        continue
                    block_time = block.get("blockTime")
                    if anchor_time and block_time is not None and int(block_time) > anchor_time + horizon_seconds:
                        continue
                    filtered_block = filter_block(block, accounts)
                    if filtered_block is None:
                        continue
                    append_jsonl(output, {
                        "schema": SCHEMA, "record_type": "FINALIZED_BLOCK",
                        "cohort_id": state["cohort_id"], "boundary_utc": state["boundary_utc"],
                        "captured_at_utc": utc_now(), "slot": slot, "block_time": block_time,
                        "anchor_accounts": sorted(accounts),
                        "anchor_signature_first_seen": signature, "block_sha256": digest(block),
                        "raw_block": filtered_block,
                    })
                    state["written_records"] += 1
                    state["block_records"] += 1
                    blocks += 1
                    seen_slots.add(slot)
                    window_slots.append(slot)
                window_slots.extend(int(slot_value) for slot_value in slots if int(slot_value) in seen_slots)
                append_jsonl(output, {
                    "schema": SCHEMA, "record_type": "ANCHOR_WINDOW",
                    "cohort_id": state["cohort_id"], "boundary_utc": state["boundary_utc"],
                    "captured_at_utc": utc_now(), "anchor_signature": signature,
                    "anchor_slot": anchor_slot, "anchor_block_time": anchor_time,
                    "anchor_accounts": sorted(accounts),
                    "entry_delay_seconds": 1, "eligible_after_block_time": eligible_after,
                    "exit_horizon_seconds": horizon_seconds, "slot_span_requested": slot_span,
                    "window_slots": sorted(set(window_slots)),
                })
                state["written_records"] += 1
                state["anchor_records"] += 1
                anchors += 1
                processed.add(signature)
            except Exception as error:
                errors += 1
                if len(state.setdefault("error_samples", [])) < 20:
                    state["error_samples"].append({"anchor_signature": signature, "error": type(error).__name__ + ":" + str(error)})
        state["source_byte_offset"] = offset
    state["processed_anchors"] = list(processed)[-20_000:]
    state["seen_slots"] = sorted(seen_slots)[-200_000:]
    state["errors"] = int(state.get("errors", 0)) + errors
    state["updated_at_utc"] = utc_now()
    return {"source_rows": source_rows, "anchors": anchors, "blocks": blocks, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-url", default=os.environ.get("SECURE_RPC_URL") or os.environ.get("SOLANA_RPC_URL"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--boundary-utc", required=True)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-anchors-per-cycle", type=int, default=2)
    parser.add_argument("--slot-span", type=int, default=900)
    parser.add_argument("--horizon-seconds", type=int, default=300)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.rpc_url:
        raise SystemExit("FAIL_CLOSED: RPC URL is required")
    state = load_state(args.state, args.cohort_id, args.boundary_utc)
    client = Rpc(args.rpc_url)
    while True:
        result = run_once(client, state, args.source, args.output, args.max_anchors_per_cycle, args.slot_span, args.horizon_seconds, args.workers)
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
