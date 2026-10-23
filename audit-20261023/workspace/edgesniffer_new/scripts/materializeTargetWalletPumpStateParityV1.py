#!/usr/bin/env python3
"""Materialize exact finalized Pump state for the target wallet.

This is the finalized-state side of the native parity gate.  It decodes the
raw target-wallet source with the canonical Pump TradeEvent decoder and joins
only exact native-receipt signatures.  It never invents native reserve state:
the output explicitly records that the AllenHark receipt carries timing and
slot, while the finalized source carries the decoded state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import exactCurveEconomicsV1 as curve


RAW_SCHEMA = "KEFI_13_ARM_FINALIZED_RPC_RAW_TX_V1"
OUTPUT_SCHEMA = "TARGET_WALLET_PUMP_FINALIZED_STATE_V1"
WALLET = "9qz7cWBqYBP2TnGfzMmxX64G4PQip7HtogjPBBnMhWpw"


def sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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


def load_join_map(path: Path | None) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("signature")): row
        for row in read_jsonl(path) if row.get("signature")
    } if path else {}


def event_row(raw: dict[str, Any], event: curve.TradeEvent, index: int, join: dict[str, Any] | None) -> dict[str, Any]:
    event_data = curve.event_to_dict(event)
    return {
        "schema": OUTPUT_SCHEMA,
        "signature": str(raw.get("signature") or ""),
        "slot": int(raw.get("slot") or 0),
        "block_time": int(raw.get("block_time") or 0),
        "wallet": str(raw.get("tracked_wallet") or WALLET),
        "event_index": index,
        "side": "BUY" if event.is_buy else "SELL",
        "mint": event.mint,
        "quote_mint": event.quote_mint,
        "pre_state": event_data.get("pre_state"),
        "post_state": event_data.get("post_state"),
        "fee_basis_points": int(event.fee_basis_points),
        "creator_fee_basis_points": int(event.creator_fee_basis_points),
        "fee": int(event.fee),
        "creator_fee": int(event.creator_fee),
        "quote_amount": int(event.quote_amount),
        "token_amount": int(event.token_amount),
        "source_transaction_sha256": raw.get("transaction_sha256"),
        "finalized": raw.get("finalized") is True,
        "native_receipt_joined": join is not None,
        "native_receive_unix_ns": (join or {}).get("nativeReceiveUnixNs") or (join or {}).get("native_receive_unix_ns"),
        "native_receive_monotonic_ns": (join or {}).get("nativeReceiveMonotonicNs") or (join or {}).get("native_receive_monotonic_ns"),
        "native_receive_slot": (join or {}).get("nativeReceiveSlot") if join else (join or {}).get("native_receive_slot"),
        "native_receive_source": (join or {}).get("nativeReceiveSource") if join else (join or {}).get("native_receive_source"),
        "native_state_present": False,
        "state_lineage": "FINALIZED_PUMP_TRADE_EVENT;_NATIVE_RECEIPT_CLOCK_ONLY",
    }


def materialize(source: Path, joins: Path | None, output: Path, report_path: Path) -> dict[str, Any]:
    rows = read_jsonl(source)
    join_map = load_join_map(joins)
    output_rows: list[dict[str, Any]] = []
    malformed = 0
    source_rows = 0
    exact_events = 0
    joined_events = 0
    decoder_failures: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for raw in rows:
        if raw.get("schema") != RAW_SCHEMA or raw.get("finalized") is not True:
            continue
        source_rows += 1
        signature = str(raw.get("signature") or "")
        if not signature:
            malformed += 1
            continue
        try:
            events = curve.decode_trade_events(((raw.get("transaction") or {}).get("meta") or {}).get("logMessages") or [])
        except Exception as error:
            if len(decoder_failures) < 100:
                decoder_failures.append({"signature": signature, "error": type(error).__name__ + ":" + str(error)})
            continue
        for index, event in enumerate(events):
            if event.user != str(raw.get("tracked_wallet") or WALLET):
                continue
            identity = (signature, index)
            if identity in seen:
                continue
            seen.add(identity)
            joined = join_map.get(signature)
            output_rows.append(event_row(raw, event, index, joined))
            exact_events += 1
            joined_events += joined is not None
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in output_rows), encoding="utf-8")
    os.replace(temporary, output)
    report = {
        "schema": "TARGET_WALLET_PUMP_STATE_BRIDGE_AUDIT_V1",
        "generated_at_ms": int(time.time() * 1000),
        "read_only": True,
        "trading_enabled": False,
        "source": {"path": str(source), "sha256": sha256(source.read_bytes().decode("utf-8", errors="replace")) if source.exists() else None, "rows": len(rows), "finalized_rows": source_rows},
        "native_joins": {"path": str(joins) if joins else None, "sha256": sha256(joins.read_bytes().decode("utf-8", errors="replace")) if joins and joins.exists() else None, "unique_signatures": len(join_map)},
        "materialized_exact_wallet_events": exact_events,
        "native_receipt_joined_events": joined_events,
        "native_state_rows": 0,
        "native_state_status": "NOT_PRESENT_RECEIPT_SOURCE_HAS_CLOCK_ONLY",
        "decoder_failures": len(decoder_failures),
        "decoder_failure_samples": decoder_failures,
        "output_rows": len(output_rows),
        "parity_status": "BLOCKED_NATIVE_STATE_NOT_CARRIED_BY_RECEIPT",
        "economics_input_allowed": False,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--native-joins", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        report = materialize(args.source, args.native_joins, args.output, args.report)
        print(json.dumps({key: report[key] for key in ("materialized_exact_wallet_events", "native_receipt_joined_events", "parity_status")}, sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
