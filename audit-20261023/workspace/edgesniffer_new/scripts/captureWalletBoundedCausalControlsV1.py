#!/usr/bin/env python3
"""Bounded same-slot PumpSwap controls for the confirmed 2CQ wallet.

This is receive-only research capture. It fetches a finalized block only for
an exact tracked-wallet PumpSwap buy, retains exact PumpSwap controls from the
same slot up to a fixed cap, and never emits a venue guess for other rows.
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

import pumpSwapExactEconomicsV1 as pumpswap
from materializeWalletRouteCensusV1 import pumpswap_tx_with_extension
import twoCqRouteEvidenceAdaptersV1 as adapters

SCHEMA = "2CQ_BOUNDED_CAUSAL_CONTROLS_V1"
STATE_SCHEMA = "2CQ_BOUNDED_CAUSAL_CONTROLS_STATE_V1"


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


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_state(path: Path, cohort: str, boundary: str) -> dict[str, Any]:
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema") != STATE_SCHEMA or value.get("cohort_id") != cohort or value.get("boundary_utc") != boundary:
            raise RuntimeError("FAIL_CLOSED: bounded control state mismatch")
        return value
    return {"schema": STATE_SCHEMA, "cohort_id": cohort, "boundary_utc": boundary,
            "source_byte_offset": 0, "processed_anchor_signatures": [],
            "anchors": 0, "controls": 0, "errors": 0, "error_samples": [],
            "created_at_utc": now()}


def read_new_rows(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows, offset
    with path.open("rb") as handle:
        if path.stat().st_size < offset:
            offset = 0
        handle.seek(offset)
        while True:
            line = handle.readline()
            if not line:
                break
            if line.strip():
                rows.append(json.loads(line.decode("utf-8")))
        return rows, handle.tell()


def exact_wallet_pumpswap_buy(row: dict[str, Any], wallet: str) -> dict[str, Any] | None:
    try:
        tx, variant = pumpswap_tx_with_extension(row.get("transaction") or {})
        transitions = pumpswap.validate_transaction(tx)
        resolved = adapters.resolve_route(tx)
    except Exception:
        return None
    venues = resolved.get("underlyingVenues") or []
    if len(venues) != 1 or venues[0].get("venue") != "PUMPSWAP":
        return None
    matches = [item for item in transitions if item.get("side") == "BUY" and item.get("user") == wallet]
    if len(matches) != 1:
        return None
    transition = matches[0]
    transition["decoder_variant"] = variant
    transition["research_route"] = "PUMPSWAP_CPI" if venues[0].get("invocation") == "CPI" else "PUMPSWAP_DIRECT"
    return transition


def exact_control_transitions(tx: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        normalized, variant = pumpswap_tx_with_extension(tx)
        resolved = adapters.resolve_route(normalized)
        venues = resolved.get("underlyingVenues") or []
        if len(venues) != 1 or venues[0].get("venue") != "PUMPSWAP":
            return []
        transitions = pumpswap.validate_transaction(normalized)
    except Exception:
        return []
    for transition in transitions:
        transition["decoder_variant"] = variant
        transition["research_route"] = "PUMPSWAP_CPI" if venues[0].get("invocation") == "CPI" else "PUMPSWAP_DIRECT"
    return transitions


def run_once(rpc: Rpc, state: dict[str, Any], source: Path, output: Path, wallet: str, max_controls: int) -> dict[str, int]:
    rows, new_offset = read_new_rows(source, int(state.get("source_byte_offset", 0)))
    processed = set(state.get("processed_anchor_signatures") or [])
    anchors = controls = errors = 0
    for row in rows:
        if row.get("schema") != "WALLET_FINALIZED_ROUTE_RAW_TX_V1":
            continue
        signature = str(row.get("signature") or "")
        if not signature or signature in processed or not row.get("finalized"):
            continue
        transition = exact_wallet_pumpswap_buy(row, wallet)
        if not transition:
            processed.add(signature)
            continue
        try:
            slot = int(row.get("slot") or 0)
            block = rpc.call("getBlock", [slot, {"commitment": "finalized", "transactionDetails": "full", "rewards": False, "encoding": "json", "maxSupportedTransactionVersion": 1}])
            if not block:
                raise RuntimeError("FINALIZED_BLOCK_UNAVAILABLE")
            chosen = 0
            for index, tx in enumerate(block.get("transactions") or []):
                if not isinstance(tx, dict):
                    continue
                tx.setdefault("slot", slot)
                tx.setdefault("blockTime", block.get("blockTime"))
                tx_signature = str(((tx.get("transaction") or {}).get("signatures") or [""])[0])
                if tx_signature == signature or (tx.get("meta") or {}).get("err") is not None:
                    continue
                decoded = exact_control_transitions(tx)
                if not decoded:
                    continue
                append_jsonl(output, {
                    "schema": SCHEMA, "record_type": "CONTROL_SAME_SLOT",
                    "cohort_id": state["cohort_id"], "boundary_utc": state["boundary_utc"], "captured_at_utc": now(),
                    "anchor_signature": signature, "anchor_slot": slot, "anchor_block_time": row.get("block_time"),
                    "anchor_pool": transition.get("pool"), "anchor_mint": transition.get("baseMint"),
                    "control_signature": tx_signature, "control_slot": slot, "slot_transaction_index": index,
                    "control_transaction_sha256": digest(tx), "control_transitions": decoded, "raw_transaction": tx,
                })
                state["controls"] += 1
                controls += 1
                chosen += 1
                if chosen >= max_controls:
                    break
            append_jsonl(output, {
                "schema": SCHEMA, "record_type": "ANCHOR_CONTROL_WINDOW",
                "cohort_id": state["cohort_id"], "boundary_utc": state["boundary_utc"], "captured_at_utc": now(),
                "anchor_signature": signature, "anchor_slot": slot, "anchor_block_time": row.get("block_time"),
                "anchor_pool": transition.get("pool"), "anchor_mint": transition.get("baseMint"),
                "anchor_route": transition.get("research_route"), "max_controls_per_anchor": max_controls,
            })
            state["anchors"] += 1
            anchors += 1
            processed.add(signature)
        except Exception as error:
            errors += 1
            if len(state.setdefault("error_samples", [])) < 20:
                state["error_samples"].append({"signature": signature, "error": type(error).__name__ + ":" + str(error)})
    state["source_byte_offset"] = new_offset
    state["processed_anchor_signatures"] = list(processed)[-20_000:]
    state["errors"] = int(state.get("errors", 0)) + errors
    state["updated_at_utc"] = now()
    return {"source_rows": len(rows), "anchors": anchors, "controls": controls, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-url", default=os.environ.get("SECURE_RPC_URL") or os.environ.get("SOLANA_RPC_URL"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--boundary-utc", required=True)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-controls-per-anchor", type=int, default=25)
    args = parser.parse_args()
    if not args.rpc_url:
        raise SystemExit("FAIL_CLOSED: RPC URL is required")
    state = load_state(args.state, args.cohort_id, args.boundary_utc)
    client = Rpc(args.rpc_url)
    while True:
        print(json.dumps(run_once(client, state, args.source, args.output, args.wallet, args.max_controls_per_anchor), sort_keys=True), flush=True)
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
