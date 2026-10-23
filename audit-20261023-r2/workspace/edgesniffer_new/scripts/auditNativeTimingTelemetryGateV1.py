#!/usr/bin/env python3
"""Materialize a receive-only native timing/inclusion telemetry gate.

This consumer never submits a transaction and never modifies the source or
window tapes.  It records the timing fields that are already emitted by the
receive-only RPC collectors, keeps them separate from derived block-time
fields, and fails closed until genuine native receive and submitted-exit
telemetry exists.

The current collector's ``captured_at_utc`` is an RPC-finalized observation
timestamp.  It is deliberately not mislabeled as a ShredStream/gRPC receive
clock.  Likewise, no submitted exit exists in research-only mode, so the
gate reports post-deadline market observations as context only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

RAW_SCHEMA = "WALLET_FINALIZED_ROUTE_RAW_TX_V1"
WINDOW_SCHEMA = "WALLET_POST_ENTRY_PUMP_CURVE_WINDOW_V1"
DELAY_SECONDS = 1
EXIT_HORIZON_SECONDS = 5


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tx_index(row: dict[str, Any]) -> int | None:
    transaction = row.get("transaction") or row.get("raw_transaction") or {}
    value = transaction.get("transactionIndex", row.get("transactionIndex"))
    return int(value) if value is not None else None


def order_key(row: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(row.get("slot") or 0),
        tx_index(row) if tx_index(row) is not None else 2**63 - 1,
        str(row.get("signature") or ""),
    )


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def audit(
    source: Path,
    windows: Path,
    native_receipts: Path | None,
    exit_telemetry: Path | None,
    shadow_exit_telemetry: Path | None = None,
) -> dict[str, Any]:
    source_rows: dict[str, dict[str, Any]] = {}
    source_count = 0
    source_malformed = 0
    if source.exists():
        with source.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    source_malformed += 1
                    continue
                source_count += 1
                signature = str(row.get("signature") or "")
                if signature:
                    source_rows[signature] = row

    native_rows: dict[str, dict[str, Any]] = {}
    native_receipt_count = 0
    native_receipt_malformed = 0
    if native_receipts and native_receipts.exists():
        with native_receipts.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    native_receipt_malformed += 1
                    continue
                native_receipt_count += 1
                signature = str(row.get("signature") or "")
                if signature:
                    native_rows[signature] = row

    exit_rows: list[dict[str, Any]] = []
    exit_telemetry_count = 0
    exit_telemetry_malformed = 0
    if exit_telemetry and exit_telemetry.exists():
        with exit_telemetry.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    exit_telemetry_malformed += 1
                    continue
                exit_telemetry_count += 1
                if row.get("telemetrySchema") == "SUBMITTED_EXIT_TELEMETRY_V1":
                    exit_rows.append(row)

    shadow_exit_count = 0
    shadow_exit_malformed = 0
    shadow_exit_rows: list[dict[str, Any]] = []
    if shadow_exit_telemetry and shadow_exit_telemetry.exists():
        with shadow_exit_telemetry.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    shadow_exit_malformed += 1
                    continue
                shadow_exit_count += 1
                if row.get("telemetrySchema") == "SHADOW_EXIT_INTENT_TELEMETRY_V1":
                    shadow_exit_rows.append(row)

    exits_by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in exit_rows:
        mint = str(row.get("mint") or "")
        if mint:
            exits_by_mint[mint].append(row)

    anchors: dict[str, dict[str, Any]] = {}
    market_by_anchor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    window_count = 0
    market_count = 0
    window_malformed = 0
    if windows.exists():
        with windows.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    window_malformed += 1
                    continue
                if row.get("schema") != WINDOW_SCHEMA:
                    continue
                if row.get("record_type") == "ANCHOR_WINDOW":
                    anchors[str(row.get("anchor_id"))] = row
                    window_count += 1
                elif row.get("record_type") == "MARKET_TRANSACTION":
                    market_by_anchor[str(row.get("anchor_id_first_seen") or "")].append(row)
                    market_count += 1

    telemetry_rows: list[dict[str, Any]] = []
    source_observed = source_tx_index = source_finalized = 0
    market_observed = market_tx_index = 0
    post_exit_market_observed = 0
    submitted_exit_count = 0
    included_exit_count = 0
    included_exit_transaction_index_count = 0
    for anchor_id, anchor in sorted(anchors.items()):
        signature = str(anchor.get("anchor_signature") or "")
        source_row = source_rows.get(signature) or {}
        source_time = parse_time(source_row.get("captured_at_utc"))
        anchor_time = int(anchor.get("anchor_block_time") or 0)
        exit_block_time = anchor_time + DELAY_SECONDS + EXIT_HORIZON_SECONDS
        markets = sorted(market_by_anchor.get(anchor_id, []), key=order_key)
        for market in markets:
            market_time = parse_time(market.get("captured_at_utc"))
            market_observed += bool(market_time)
            market_tx_index += tx_index(market) is not None
        post_exit = [market for market in markets if int(market.get("block_time") or 0) >= exit_block_time]
        first_post_exit = post_exit[0] if post_exit else None
        if first_post_exit:
            post_exit_market_observed += bool(parse_time(first_post_exit.get("captured_at_utc")))
        source_observed += bool(source_time)
        source_tx_index += tx_index(source_row) is not None
        source_finalized += bool(source_row.get("finalized") is True)
        decision_deadline = source_time + dt.timedelta(seconds=DELAY_SECONDS) if source_time else None
        native = native_rows.get(signature) or {}
        native_receive_unix_ns = native.get("native_receive_unix_ns", native.get("nativeReceiveUnixNs"))
        native_receive_monotonic_ns = native.get("native_receive_monotonic_ns", native.get("nativeReceiveMonotonicNs"))
        native_receive_source = native.get("native_receive_source", native.get("nativeReceiveSource"))
        native_receive_slot = native.get("native_receive_slot", native.get("slot"))
        mint = str(
            anchor.get("mint")
            or anchor.get("token_mint")
            or anchor.get("base_mint")
            or source_row.get("mint")
            or source_row.get("token_mint")
            or ""
        )
        exits = sorted(
            exits_by_mint.get(mint, []),
            key=lambda row: int(row.get("atMs") or row.get("exitEventAtMs") or 0),
        )
        submitted_exit = next(
            (row for row in exits if row.get("exitPhase") == "SUBMITTED" and row.get("exitSubmitSignature")),
            None,
        )
        submitted_signature = submitted_exit.get("exitSubmitSignature") if submitted_exit else None
        finality = next(
            (
                row for row in exits
                if row.get("exitPhase") == "FINALITY"
                and (not submitted_signature or row.get("signature") == submitted_signature)
            ),
            None,
        )
        exit_included = bool(finality and finality.get("exitInclusionObserved") is True)
        submitted_exit_count += bool(submitted_exit)
        included_exit_count += exit_included
        included_exit_transaction_index_count += bool(
            finality and finality.get("exitInclusionTransactionIndex") is not None
        )
        telemetry_rows.append({
            "schema": "NATIVE_TIMING_TELEMETRY_ANCHOR_V1",
            "anchor_id": anchor_id,
            "anchor_signature": signature,
            "anchor_slot": int(anchor.get("anchor_slot") or source_row.get("slot") or 0),
            "anchor_transaction_index": tx_index(source_row),
            "anchor_block_time": anchor_time,
            "source_observed_at_utc": source_row.get("captured_at_utc"),
            "decision_deadline_observed_at_utc": decision_deadline.isoformat().replace("+00:00", "Z") if decision_deadline else None,
            "exit_target_block_time": exit_block_time,
            "first_post_exit_market_signature": first_post_exit.get("signature") if first_post_exit else None,
            "first_post_exit_market_observed_at_utc": first_post_exit.get("captured_at_utc") if first_post_exit else None,
            "first_post_exit_market_slot": int(first_post_exit.get("slot") or 0) if first_post_exit else None,
            "first_post_exit_market_transaction_index": tx_index(first_post_exit) if first_post_exit else None,
            "native_receive_unix_ns": native_receive_unix_ns,
            "native_receive_monotonic_ns": str(native_receive_monotonic_ns) if native_receive_monotonic_ns is not None else None,
            "native_receive_source": native_receive_source,
            "native_receive_slot": int(native_receive_slot) if native_receive_slot is not None else None,
            "native_receive_signature": signature if native else None,
            "exit_submit_monotonic_ns": (
                submitted_exit.get("exitSubmitMonotonicNs") if submitted_exit else None
            ),
            "exit_submit_signature": submitted_signature,
            "exit_submit_at_ms": submitted_exit.get("atMs") if submitted_exit else None,
            "exit_inclusion_slot": (
                finality.get("exitInclusionSlot") if finality and exit_included else None
            ),
            "exit_inclusion_transaction_index": (
                finality.get("exitInclusionTransactionIndex")
                if finality and exit_included else None
            ),
            "exit_result": (
                finality.get("exitResult") if finality else "NOT_SUBMITTED"
            ),
            "actual_exit_submission_observed": bool(submitted_exit),
            "actual_exit_inclusion_observed": exit_included,
            "receive_observation_basis": "RPC_FINALIZED_CAPTURED_AT_UTC_ONLY" if source_time else "MISSING_SOURCE_OBSERVATION_TIME",
            "inclusion_observation_basis": (
                "RUST_SUBMITTED_EXIT_FINALITY_TELEMETRY"
                if finality else "POST_DEADLINE_MARKET_CONTEXT_ONLY_NO_SUBMITTED_EXIT"
            ),
        })

    source_rows_with_required_fields = sum(
        bool(row.get("finalized") is True and row.get("captured_at_utc") and tx_index(row) is not None)
        for row in source_rows.values()
    )
    market_rows_with_required_fields = sum(
        bool(row.get("captured_at_utc") and tx_index(row) is not None)
        for rows in market_by_anchor.values() for row in rows
    )
    native_receive_matches = sum(
        bool(row.get("native_receive_signature")) for row in telemetry_rows
    )
    native_receive_valid_matches = sum(
        bool(
            row.get("native_receive_signature")
            and row.get("native_receive_unix_ns") is not None
            and row.get("native_receive_monotonic_ns") is not None
            and row.get("native_receive_source")
            and row.get("native_receive_slot") is not None
        )
        for row in telemetry_rows
    )
    native_receive_fields_present = native_receive_valid_matches > 0
    native_receive_status = (
        "PASS_NATIVE_RECEIVE_CLOCK_PARTIAL_COVERAGE"
        if native_receive_fields_present and native_receive_valid_matches < window_count
        else "PASS_NATIVE_RECEIVE_CLOCK_COVERAGE"
        if native_receive_fields_present
        else "BLOCKED_NATIVE_RECEIVE_CLOCK_NOT_PRESENT"
    )
    submitted_exit_fields_present = submitted_exit_count > 0
    submitted_exit_inclusion_fields_present = included_exit_count > 0
    exit_gate_status = (
        "PASS_SUBMITTED_EXIT_AND_INCLUSION_TELEMETRY"
        if submitted_exit_fields_present
        and submitted_exit_inclusion_fields_present
        and included_exit_transaction_index_count > 0
        else "PARTIAL_SUBMITTED_EXIT_TELEMETRY_INCLUSION_INDEX_MISSING"
        if submitted_exit_inclusion_fields_present
        else "PARTIAL_SUBMITTED_EXIT_TELEMETRY"
        if submitted_exit_fields_present
        else "BLOCKED_NO_SUBMITTED_EXIT_TELEMETRY"
    )
    overall_gate_status = (
        "PASS_NATIVE_RECEIVE_AND_EXIT_TELEMETRY"
        if native_receive_fields_present
        and submitted_exit_inclusion_fields_present
        and included_exit_transaction_index_count > 0
        else "BLOCKED_NO_SUBMITTED_EXIT_TELEMETRY"
        if native_receive_fields_present
        else "BLOCKED_UNTIL_NATIVE_RECEIVE_AND_SUBMITTED_EXIT_TELEMETRY"
    )
    return {
        "schema": "NATIVE_RECEIVE_INCLUSION_TELEMETRY_GATE_V1",
        "generated_at_utc": now(),
        "read_only": True,
        "trading_enabled": False,
        "source": {
            "path": str(source),
            "sha256": sha256_file(source),
            "rows": source_count,
            "malformed_json": source_malformed,
            "rows_with_finalized_capture_and_transaction_index": source_rows_with_required_fields,
            "source_observed_at_field": "captured_at_utc",
            "source_observed_at_semantics": "RPC finalized observation time; not a native ShredStream/gRPC receive clock",
            "finalized_field_coverage": source_finalized,
            "transaction_index_coverage": source_tx_index,
            "observed_time_coverage": source_observed,
        },
        "windows": {
            "path": str(windows),
            "sha256": sha256_file(windows),
            "anchors": window_count,
            "market_rows": market_count,
            "malformed_json": window_malformed,
            "market_rows_with_capture_and_transaction_index": market_rows_with_required_fields,
            "market_observed_time_coverage": market_observed,
            "market_transaction_index_coverage": market_tx_index,
            "post_exit_market_observation_time_coverage": post_exit_market_observed,
        },
        "native_receipts": {
            "path": str(native_receipts) if native_receipts else None,
            "sha256": sha256_file(native_receipts) if native_receipts else None,
            "rows": native_receipt_count,
            "malformed_json": native_receipt_malformed,
            "unique_signatures": len(native_rows),
            "anchor_signature_matches": native_receive_matches,
            "valid_native_receive_matches": native_receive_valid_matches,
        },
        "submitted_exit_telemetry": {
            "path": str(exit_telemetry) if exit_telemetry else None,
            "sha256": sha256_file(exit_telemetry) if exit_telemetry else None,
            "rows": exit_telemetry_count,
            "malformed_json": exit_telemetry_malformed,
            "normalized_rows": len(exit_rows),
            "submitted_exit_rows_joined_to_anchors": submitted_exit_count,
            "included_exit_rows_joined_to_anchors": included_exit_count,
            "inclusion_transaction_index_coverage": included_exit_transaction_index_count,
        },
        "shadow_exit_intent_telemetry": {
            "path": str(shadow_exit_telemetry) if shadow_exit_telemetry else None,
            "sha256": sha256_file(shadow_exit_telemetry) if shadow_exit_telemetry else None,
            "rows": shadow_exit_count,
            "malformed_json": shadow_exit_malformed,
            "normalized_rows": len(shadow_exit_rows),
            "execution_mode": "RECEIVE_ONLY_SHADOW",
            "does_not_satisfy_submitted_exit_gate": True,
        },
        "native_receive_gate": {
            "native_receive_fields_present": native_receive_fields_present,
            "status": native_receive_status,
            "coverage_ratio": native_receive_valid_matches / window_count if window_count else 0.0,
            "required_fields": ["native_receive_monotonic_ns", "native_receive_source", "native_receive_slot"],
        },
        "exit_inclusion_gate": {
            "submitted_exit_fields_present": submitted_exit_fields_present,
            "actual_exit_submissions_observed": submitted_exit_count,
            "actual_exit_inclusions_observed": included_exit_count,
            "status": exit_gate_status,
            "required_fields": ["exit_submit_monotonic_ns", "exit_submit_signature", "exit_inclusion_slot", "exit_inclusion_transaction_index", "exit_result"],
        },
        "gate_status": overall_gate_status,
        "promotion_allowed": False,
        "rows": telemetry_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-receipts", type=Path)
    parser.add_argument("--exit-telemetry", type=Path)
    parser.add_argument("--shadow-exit-telemetry", type=Path)
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        result = audit(
            args.source,
            args.windows,
            args.native_receipts,
            args.exit_telemetry,
            args.shadow_exit_telemetry,
        )
        atomic_write(args.output, result)
        print(json.dumps({key: result[key] for key in ("generated_at_utc", "native_receive_gate", "exit_inclusion_gate", "gate_status", "promotion_allowed")}, sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
