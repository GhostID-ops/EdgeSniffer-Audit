#!/usr/bin/env python3
"""Bridge same-source Yellowstone transaction receipts into the native tape.

The target transaction tape is retained unchanged.  This process creates a
separate derived tape only when an exact transaction signature is present in
both the target tape and the Yellowstone receipt stream.  It never submits or
modifies a transaction.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any


RECEIPT_KINDS = {
    "rustNativePumpCurveTransactionReceipt",
    "rustNativeTargetTransactionReceipt",
}
RECEIPT_SCHEMA = "NATIVE_YELLOWSTONE_TARGET_TRANSACTION_RECEIPT_V1"
BRIDGE_SCHEMA = "NATIVE_YELLOWSTONE_SAME_SOURCE_TRANSACTION_V1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def int_value(row: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = row.get(name)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def append_receipts(events_path: Path, receipt_path: Path, state_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except (OSError, json.JSONDecodeError):
            state = {}

    stat = events_path.stat() if events_path.exists() else None
    current_size = stat.st_size if stat else 0
    current_inode = getattr(stat, "st_ino", None) if stat else None
    offset = int(state.get("events_offset", 0) or 0)
    previous_inode = state.get("events_inode")
    # A new state file starts at the current end: old account events were
    # created before the receipt emitter existed and cannot be backfilled.
    initialized = not state_path.exists() and events_path.exists()
    if initialized:
        offset = current_size
    if previous_inode is not None and current_inode is not None and previous_inode != current_inode:
        offset = 0
    if offset > current_size:
        offset = 0

    appended = 0
    malformed = 0
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    if events_path.exists() and current_size > offset:
        with events_path.open("rb") as source, receipt_path.open("a", encoding="utf-8") as destination:
            source.seek(offset)
            for raw in source:
                offset += len(raw)
                try:
                    row = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    malformed += 1
                    continue
                if not isinstance(row, dict) or row.get("kind") not in RECEIPT_KINDS:
                    continue
                signature = str(row.get("signature") or "")
                if not signature:
                    continue
                normalized = {
                    "schema": row.get("schema") or RECEIPT_SCHEMA,
                    "kind": row.get("kind"),
                    "signature": signature,
                    "bonding_curve": row.get("bondingCurve") or row.get("bonding_curve"),
                    "mint": row.get("mint"),
                    "side": row.get("side"),
                    "slot": int_value(row, "slot"),
                    "transaction_index": int_value(row, "transactionIndex", "transaction_index"),
                    "instruction_index": int_value(row, "instructionIndex", "instruction_index"),
                    "native_receive_unix_ns": int_value(row, "nativeReceiveUnixNs", "native_receive_unix_ns"),
                    "native_receive_monotonic_ns": int_value(row, "nativeReceiveMonotonicNs", "native_receive_monotonic_ns"),
                    "native_receive_source": row.get("nativeReceiveSource") or row.get("native_receive_source"),
                    "source": row.get("source"),
                    "causal_stream_id": row.get("causalStreamId") or row.get("causal_stream_id"),
                }
                destination.write(json.dumps(normalized, sort_keys=True, separators=(",", ":")) + "\n")
                appended += 1

    atomic_write_json(state_path, {
        "schema": "NATIVE_YELLOWSTONE_TRANSACTION_BRIDGE_STATE_V1",
        "events_path": str(events_path),
        "events_offset": offset,
        "events_inode": current_inode,
        "initialized_at_ms": state.get("initialized_at_ms") or int(time.time() * 1000),
        "updated_at_ms": int(time.time() * 1000),
    })
    return {
        "events_size": current_size,
        "events_offset": offset,
        "receipt_rows_appended": appended,
        "malformed_event_rows": malformed,
        "initialized_at_end": initialized,
    }


def bridge_tape(
    native_transactions_path: Path,
    receipt_path: Path,
    output_path: Path,
    required_wallet: str | None = None,
) -> dict[str, Any]:
    transactions = read_jsonl(native_transactions_path)
    raw_receipts = read_jsonl(receipt_path)
    unique_receipts_by_signature: dict[str, dict[str, Any]] = {}
    for receipt in raw_receipts:
        signature = str(receipt.get("signature") or "")
        if not signature:
            continue
        previous = unique_receipts_by_signature.get(signature)
        if previous is None:
            unique_receipts_by_signature[signature] = receipt
            continue
        current_clock = int_value(receipt, "native_receive_unix_ns") or 0
        previous_clock = int_value(previous, "native_receive_unix_ns") or 0
        if current_clock and (not previous_clock or current_clock < previous_clock):
            unique_receipts_by_signature[signature] = receipt
    receipts = list(unique_receipts_by_signature.values())
    by_signature: dict[str, list[dict[str, Any]]] = {}
    for receipt in receipts:
        signature = str(receipt.get("signature") or "")
        if signature:
            by_signature.setdefault(signature, []).append(receipt)

    output_rows: list[dict[str, Any]] = []
    missing = 0
    ambiguous = 0
    wrong_wallet = 0
    for tx in transactions:
        signature = str(tx.get("signature") or "")
        if required_wallet is not None and str(tx.get("wallet") or "") != required_wallet:
            wrong_wallet += 1
            continue
        candidates = by_signature.get(signature, [])
        if not candidates:
            missing += 1
            continue
        keys = {str(value) for value in tx.get("account_keys") or []}
        matching = [row for row in candidates if not row.get("bonding_curve") or row.get("bonding_curve") in keys]
        if len(matching) != 1:
            if len(matching) == 0 and len(candidates) == 1:
                matching = candidates
            else:
                ambiguous += 1
                continue
        receipt = matching[0]
        enriched = dict(tx)
        enriched.update({
            "schema": BRIDGE_SCHEMA,
            "same_source_receive_unix_ns": receipt.get("native_receive_unix_ns"),
            "same_source_receive_monotonic_ns": receipt.get("native_receive_monotonic_ns"),
            "same_source_receive_source": receipt.get("native_receive_source"),
            "same_source_slot": receipt.get("slot"),
            "same_source_transaction_index": receipt.get("transaction_index"),
            "same_source_instruction_index": receipt.get("instruction_index"),
            "causal_ordering_source": receipt.get("causal_stream_id") or "yellowstone-grpc-subscription",
            "same_source_receipt_schema": receipt.get("schema") or RECEIPT_SCHEMA,
        })
        output_rows.append(enriched)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in output_rows),
        encoding="utf-8",
    )
    os.replace(temporary, output_path)
    return {
        "native_transaction_rows": len(transactions),
        "receipt_rows": len(raw_receipts),
        "unique_receipt_rows": len(receipts),
        "bridged_rows": len(output_rows),
        "missing_receipt_rows": missing,
        "ambiguous_receipt_rows": ambiguous,
        "wrong_wallet_rows": wrong_wallet,
    }


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    receipt_stats = append_receipts(args.events, args.receipt_output, args.state)
    bridge_stats = bridge_tape(
        args.native_transactions,
        args.receipt_output,
        args.output,
        getattr(args, "required_wallet", None),
    )
    report = {
        "schema": "NATIVE_YELLOWSTONE_TRANSACTION_BRIDGE_AUDIT_V1",
        "generated_at_ms": int(time.time() * 1000),
        "read_only": True,
        "trading_enabled": False,
        "inputs": {
            "events": str(args.events),
            "native_transactions": str(args.native_transactions),
            "receipt_output": str(args.receipt_output),
            "output": str(args.output),
            "required_wallet": getattr(args, "required_wallet", None),
        },
        "receipt_stream": receipt_stats,
        "bridge": bridge_stats,
        "status": (
            "WRONG_WALLET_INPUT"
            if bridge_stats["wrong_wallet_rows"]
            else "BRIDGED_ROWS_AVAILABLE"
            if bridge_stats["bridged_rows"]
            else "WAITING_FOR_YELLOWSTONE_TRANSACTION_RECEIPTS"
        ),
    }
    atomic_write_json(args.report, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--native-transactions", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--required-wallet")
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        report = run_once(args)
        print(json.dumps({"receipt_stream": report["receipt_stream"], "bridge": report["bridge"], "status": report["status"]}, sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
