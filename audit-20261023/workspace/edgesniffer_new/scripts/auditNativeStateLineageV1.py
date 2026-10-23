#!/usr/bin/env python3
"""Audit whether native Pump state was causally available before each target tx.

This is deliberately separate from exact finalized-value parity. A native
state may be valid for a live decision even when a later finalized account
update contains a different post-trade value. The audit never changes the
finalized parity gate and never admits execution.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def number(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def audit(rows: list[dict[str, Any]], *, min_rows: int = 20) -> dict[str, Any]:
    valid = 0
    reasons: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        signature = str(row.get("signature") or "")
        if not signature or signature in seen:
            continue
        seen.add(signature)
        native = row.get("native_state") if isinstance(row.get("native_state"), dict) else {}
        tx_ns = number(row.get("same_source_receive_unix_ns") or row.get("native_receive_unix_ns"))
        seed_ns = number(native.get("seed_native_receive_unix_ns"))
        tx_slot = number(row.get("same_source_slot") or row.get("slot"))
        seed_slot = number(native.get("seed_slot"))
        seed_sig = str(native.get("seed_account_txn_signature") or "")
        lineage_ok = all((
            tx_ns is not None,
            seed_ns is not None,
            seed_ns < tx_ns if tx_ns is not None and seed_ns is not None else False,
            tx_slot is not None,
            seed_slot is not None,
            seed_slot == tx_slot if tx_slot is not None and seed_slot is not None else False,
            bool(native.get("seed_native_receive_source")),
            bool(seed_sig),
            seed_sig != signature,
            number(native.get("seed_write_version")) is not None,
        ))
        if lineage_ok:
            valid += 1
            continue
        reason = "invalid_or_missing_causal_lineage"
        reasons[reason] = reasons.get(reason, 0) + 1
        if len(samples) < 100:
            samples.append({
                "signature": signature,
                "tx_receive_unix_ns": tx_ns,
                "seed_receive_unix_ns": seed_ns,
                "tx_slot": tx_slot,
                "seed_slot": seed_slot,
                "seed_account_txn_signature": seed_sig or None,
            })
    unique_rows = len(seen)
    passed = unique_rows >= min_rows and valid == unique_rows
    return {
        "schema": "NATIVE_PUMP_CAUSAL_STATE_LINEAGE_GATE_V1",
        "generated_at_ms": int(time.time() * 1000),
        "read_only": True,
        "trading_enabled": False,
        "promotion_allowed": False,
        "economics_input_allowed": False,
        "unique_native_state_rows": unique_rows,
        "causally_lineaged_rows": valid,
        "invalid_rows": unique_rows - valid,
        "coverage_ratio": valid / unique_rows if unique_rows else 0.0,
        "thresholds": {"min_rows": min_rows, "required_coverage": 1.0},
        "status": "PASS_CAUSAL_STATE_LINEAGE" if passed else "BLOCKED_CAUSAL_STATE_LINEAGE",
        "reasons": reasons,
        "samples": samples,
        "interpretation": "Causal lineage and exact finalized reserve-value parity are separate evidence gates.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-rows", type=int, default=20)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        result = audit(read_jsonl(args.input), min_rows=args.min_rows)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(args.output)
        print(json.dumps({k: result[k] for k in ("status", "unique_native_state_rows", "causally_lineaged_rows", "invalid_rows")}, sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
