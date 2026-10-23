#!/usr/bin/env python3
"""Capture finalized causal market windows for exact Pump bonding-curve buys.

This lane is deliberately separate from PumpSwap.  A wallet BUY is admitted
only from the exact Pump TradeEvent decoder.  Post-entry market transactions
are collected by mint, then decoded later by the venue-native evaluator.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from materializeWalletRouteCensusV1 import curve_events_with_extension

SCHEMA = "WALLET_POST_ENTRY_PUMP_CURVE_WINDOW_V1"
STATE_SCHEMA = "WALLET_POST_ENTRY_PUMP_CURVE_WINDOW_STATE_V1"
RAW_SCHEMA = "WALLET_FINALIZED_ROUTE_RAW_TX_V1"


def now() -> str:
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
        os.fsync(handle.fileno())


def load_state(path: Path, cohort: str, boundary: str) -> dict[str, Any]:
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") != STATE_SCHEMA or value.get("cohort_id") != cohort or value.get("boundary_utc") != boundary:
            raise RuntimeError("FAIL_CLOSED: Pump curve window state mismatch")
        return value
    return {
        "schema": STATE_SCHEMA,
        "cohort_id": cohort,
        "boundary_utc": boundary,
        "source_byte_offset": 0,
        "processed_anchors": [],
        "seen_signatures": [],
        "written_records": 0,
        "anchors": 0,
        "market_transactions": 0,
        "errors": 0,
        "error_samples": [],
        "created_at_utc": now(),
    }


def read_rows(path: Path, offset: int):
    if not path.exists():
        return
    with path.open("rb") as handle:
        if path.stat().st_size < offset:
            offset = 0
        handle.seek(offset)
        while True:
            start = handle.tell()
            line = handle.readline()
            if not line:
                break
            if not line.strip():
                continue
            try:
                yield json.loads(line.decode("utf-8")), handle.tell(), start
            except json.JSONDecodeError:
                # The source is append-only; leave a partial final line for the next cycle.
                break


def exact_curve_buys(row: dict[str, Any]) -> list[tuple[int, Any]]:
    if row.get("schema") != RAW_SCHEMA or row.get("program_family") != "PUMP_PROGRAM":
        return []
    wallet = str(row.get("wallet") or "")
    try:
        events, _variant = curve_events_with_extension(row.get("transaction") or {})
    except Exception:
        return []
    return [(index, event) for index, event in enumerate(events) if event.user == wallet and event.is_buy]


def curve_route(tx: dict[str, Any]) -> str:
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = [str(value.get("pubkey")) if isinstance(value, dict) else str(value) for value in message.get("accountKeys") or []]
    for instruction in message.get("instructions") or []:
        program = instruction.get("programId")
        if program is None and instruction.get("programIdIndex") is not None:
            index = int(instruction["programIdIndex"])
            program = keys[index] if 0 <= index < len(keys) else None
        if program == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":
            return "PUMP_BONDING_CURVE_TOP_LEVEL"
    return "PUMP_BONDING_CURVE_CPI"


def run_once(
    rpc: Rpc,
    state: dict[str, Any],
    source: Path,
    output: Path,
    limit: int,
    horizon: int,
    slot_span: int,
    max_anchors: int,
    workers: int,
    route_filter: str | None = None,
) -> dict[str, int]:
    processed = set(state.get("processed_anchors") or [])
    seen = set(state.get("seen_signatures") or [])
    anchors = transactions = errors = rows_seen = 0
    offset = int(state.get("source_byte_offset", 0))
    committed_offset = offset

    for row, row_offset, row_start in read_rows(source, offset) or ():
        rows_seen += 1
        candidates = exact_curve_buys(row)
        if route_filter:
            candidates = [
                item for item in candidates
                if curve_route(row.get("transaction") or {}) == route_filter
            ]
        if not candidates:
            committed_offset = row_offset
            continue
        signature = str(row.get("signature") or "")
        anchor_slot = int(row.get("slot") or 0)
        anchor_time = int(row.get("block_time") or 0)
        for event_index, event in candidates:
            if anchors >= max_anchors:
                break
            anchor_id = f"{signature}:{event_index}"
            if not signature or anchor_id in processed:
                continue
            mint = str(event.mint)
            try:
                summaries = rpc.call("getSignaturesForAddress", [mint, {"commitment": "finalized", "limit": limit}]) or []
                window_signatures: list[str] = []
                fetch_candidates: list[tuple[str, int, Any]] = []
                for summary in reversed(summaries):
                    candidate = str(summary.get("signature") or "")
                    slot = int(summary.get("slot") or 0)
                    summary_time = summary.get("blockTime")
                    if not candidate or slot < anchor_slot or slot > anchor_slot + slot_span:
                        continue
                    if anchor_time and summary_time is not None and int(summary_time) > anchor_time + horizon:
                        continue
                    window_signatures.append(candidate)
                    if candidate not in seen and summary.get("err") is None:
                        fetch_candidates.append((candidate, slot, summary_time))

                fetched: dict[str, tuple[dict[str, Any] | None, str | None]] = {}
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [executor.submit(fetch_transaction, rpc.url, candidate) for candidate, _, _ in fetch_candidates]
                    for future in as_completed(futures):
                        candidate, tx, fetch_error = future.result()
                        fetched[candidate] = (tx, fetch_error)

                for candidate, slot, summary_time in fetch_candidates:
                    tx, fetch_error = fetched.get(candidate, (None, "MISSING_FETCH"))
                    if fetch_error:
                        errors += 1
                        continue
                    if not tx or (tx.get("meta") or {}).get("err") is not None:
                        continue
                    append(output, {
                        "schema": SCHEMA,
                        "record_type": "MARKET_TRANSACTION",
                        "cohort_id": state["cohort_id"],
                        "boundary_utc": state["boundary_utc"],
                        "captured_at_utc": now(),
                        "anchor_id_first_seen": anchor_id,
                        "anchor_mint": mint,
                        "signature": candidate,
                        "slot": slot,
                        "block_time": tx.get("blockTime", summary_time),
                        "transaction_sha256": digest(tx),
                        "raw_transaction": tx,
                    })
                    state["written_records"] += 1
                    state["market_transactions"] += 1
                    transactions += 1
                    seen.add(candidate)

                append(output, {
                    "schema": SCHEMA,
                    "record_type": "ANCHOR_WINDOW",
                    "cohort_id": state["cohort_id"],
                    "boundary_utc": state["boundary_utc"],
                    "captured_at_utc": now(),
                    "anchor_id": anchor_id,
                    "anchor_signature": signature,
                    "anchor_event_index": event_index,
                    "anchor_slot": anchor_slot,
                    "anchor_block_time": anchor_time,
                    "wallet": row.get("wallet"),
                    "mint": mint,
                    "quote_mint": str(event.quote_mint),
                    "route": curve_route(row.get("transaction") or {}),
                    "entry_delay_seconds": 1,
                    "exit_horizon_seconds": horizon,
                    "slot_span_requested": slot_span,
                    "anchor_event": dataclasses.asdict(event),
                    "window_signatures": sorted(set(window_signatures)),
                })
                state["written_records"] += 1
                state["anchors"] += 1
                processed.add(anchor_id)
                anchors += 1
            except Exception as error:
                errors += 1
                if len(state.setdefault("error_samples", [])) < 20:
                    state["error_samples"].append({"anchor_id": anchor_id, "mint": mint, "error": type(error).__name__ + ":" + str(error)})
                # Retry this anchor on the next cycle rather than advancing past it.
                committed_offset = row_start
                break
        if anchors >= max_anchors:
            break
        committed_offset = row_offset

    state["source_byte_offset"] = committed_offset
    state["processed_anchors"] = list(processed)[-50_000:]
    state["seen_signatures"] = list(seen)[-200_000:]
    state["errors"] = int(state.get("errors", 0)) + errors
    state["updated_at_utc"] = now()
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
    parser.add_argument("--slot-span", type=int, default=900)
    parser.add_argument("--max-anchors-per-cycle", type=int, default=5)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--route-filter", choices=("PUMP_BONDING_CURVE_TOP_LEVEL", "PUMP_BONDING_CURVE_CPI"))
    parser.add_argument("--start-at-end", action="store_true", help="initialize a new cohort at the current append-only source end")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.rpc_url:
        raise SystemExit("FAIL_CLOSED: RPC URL is required")
    state = load_state(args.state, args.cohort_id, args.boundary_utc)
    if args.start_at_end and not args.state.exists():
        state["source_byte_offset"] = args.source.stat().st_size
    client = Rpc(args.rpc_url)
    while True:
        result = run_once(client, state, args.source, args.output, args.limit, args.horizon_seconds, args.slot_span, args.max_anchors_per_cycle, args.workers, args.route_filter)
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
