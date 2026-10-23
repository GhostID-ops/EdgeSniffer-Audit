#!/usr/bin/env python3
"""Receive-only same-slot causal tape for the confirmed 2CQ wallet.

The wallet source remains the authoritative anchor stream.  This collector
adds contemporaneous successful transactions from each anchor's finalized
slot, preserving raw transaction lineage and route-resolution status.  It is
not an evaluator and never selects a policy or submits a transaction.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

import twoCqRouteEvidenceAdaptersV1 as adapters

SCHEMA = "WALLET_PROSPECTIVE_CAUSAL_TAPE_V1"
STATE_SCHEMA = "WALLET_PROSPECTIVE_CAUSAL_TAPE_STATE_V1"


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
            payload = json.loads(response.read().decode())
        if payload.get("error"):
            raise RuntimeError(f"RPC_{method}:{payload['error']}")
        return payload.get("result")


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
            raise RuntimeError("FAIL_CLOSED: causal tape state contract mismatch")
        return state
    return {
        "schema": STATE_SCHEMA, "cohort_id": cohort, "boundary_utc": boundary,
        "source_byte_offset": 0, "processed_anchor_signatures": [],
        "written_records": 0, "anchors": 0, "controls": 0, "errors": 0,
        "error_samples": [], "created_at_utc": utc_now(),
    }


def read_new_rows(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
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


def route_summary(tx: dict[str, Any]) -> dict[str, Any]:
    try:
        resolved = adapters.resolve_route(tx)
        return {
            "route_resolution": resolved.get("resolution"),
            "resolved": bool(resolved.get("resolved")),
            "underlying_venues": resolved.get("underlyingVenues") or [],
            "routers": resolved.get("routers") or [],
            "unknown_programs": resolved.get("unknownPrograms") or [],
            "top_level_programs": resolved.get("topLevelPrograms") or [],
            "inner_programs": resolved.get("innerPrograms") or [],
        }
    except Exception as error:
        return {"resolved": False, "decoder_error": type(error).__name__ + ":" + str(error)}


def transaction_record(tx: dict[str, Any], *, anchor: dict[str, Any], role: str, index: int) -> dict[str, Any]:
    meta = tx.get("meta") or {}
    return {
        "schema": SCHEMA,
        "cohort_id": anchor["cohort_id"],
        "boundary_utc": anchor["boundary_utc"],
        "captured_at_utc": utc_now(),
        "role": role,
        "anchor_signature": anchor["signature"],
        "anchor_slot": int(anchor["slot"]),
        "slot_transaction_index": index,
        "signature": anchor["signature"] if role == "ANCHOR" else str(tx.get("signature") or ""),
        "slot": int(tx.get("slot") or anchor["slot"]),
        "block_time": tx.get("blockTime", anchor.get("block_time")),
        "successful": meta.get("err") is None,
        "transaction_sha256": digest(tx),
        "route": route_summary(tx),
        "raw_transaction": tx,
    }


def run_once(rpc: Rpc, state: dict[str, Any], source: Path, output: Path, max_controls: int) -> dict[str, int]:
    rows, new_offset = read_new_rows(source, int(state.get("source_byte_offset", 0)))
    processed = set(state.get("processed_anchor_signatures") or [])
    anchors = controls = errors = 0
    for anchor in rows:
        if anchor.get("schema") != "WALLET_FINALIZED_ROUTE_RAW_TX_V1":
            continue
        signature = str(anchor.get("signature") or "")
        if not signature or signature in processed or not anchor.get("finalized"):
            continue
        block_time = anchor.get("block_time")
        if block_time is not None and dt.datetime.fromtimestamp(int(block_time), tz=dt.timezone.utc) < dt.datetime.fromisoformat(state["boundary_utc"].replace("Z", "+00:00")):
            processed.add(signature)
            continue
        try:
            slot = int(anchor.get("slot") or 0)
            block = rpc.call("getBlock", [slot, {"commitment": "finalized", "transactionDetails": "full", "rewards": False, "encoding": "json", "maxSupportedTransactionVersion": 1}])
            if not block:
                raise RuntimeError("FINALIZED_BLOCK_UNAVAILABLE")
            transactions = block.get("transactions") or []
            found = False
            anchor_controls = 0
            for index, entry in enumerate(transactions):
                # getBlock(full) returns the complete transaction envelope:
                # {transaction: {message, signatures}, meta: {...}}. Keep
                # the envelope so signature, success, and route evidence are
                # available to the anchor/control classifier.
                tx = entry if isinstance(entry, dict) else entry
                if not isinstance(tx, dict):
                    continue
                tx.setdefault("slot", slot)
                tx.setdefault("blockTime", block.get("blockTime"))
                tx_signature = str(((tx.get("transaction") or {}).get("signatures") or [""])[0])
                if tx_signature == signature:
                    found = True
                    append_jsonl(output, transaction_record(anchor.get("transaction") or {}, anchor=anchor, role="ANCHOR", index=index) | {"raw_transaction": anchor.get("transaction") or {}, "transaction_sha256": anchor.get("transaction_sha256") or digest(anchor.get("transaction") or {})})
                    state["written_records"] += 1
                    state["anchors"] += 1
                elif anchor_controls < max_controls and (tx.get("meta") or {}).get("err") is None:
                    append_jsonl(output, transaction_record(tx, anchor=anchor, role="CONTROL_SAME_SLOT", index=index))
                    state["written_records"] += 1
                    state["controls"] += 1
                    controls += 1
                    anchor_controls += 1
            if not found:
                raise RuntimeError("ANCHOR_NOT_FOUND_IN_FINALIZED_BLOCK")
            anchors += 1
            processed.add(signature)
        except Exception as error:
            errors += 1
            if len(state.setdefault("error_samples", [])) < 20:
                state["error_samples"].append({"signature": signature, "error": type(error).__name__ + ":" + str(error)})
    state["source_byte_offset"] = new_offset
    state["processed_anchor_signatures"] = list(processed)[-20_000:]
    state["errors"] = int(state.get("errors", 0)) + errors
    state["updated_at_utc"] = utc_now()
    return {"source_rows": len(rows), "anchors": anchors, "controls": controls, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-url", default=os.environ.get("SECURE_RPC_URL") or os.environ.get("SOLANA_RPC_URL"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--boundary-utc", required=True)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-controls-per-anchor", type=int, default=500)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.rpc_url:
        raise SystemExit("FAIL_CLOSED: RPC URL is required")
    state = load_state(args.state, args.cohort_id, args.boundary_utc)
    client = Rpc(args.rpc_url)
    while True:
        result = run_once(client, state, args.source, args.output, args.max_controls_per_anchor)
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
