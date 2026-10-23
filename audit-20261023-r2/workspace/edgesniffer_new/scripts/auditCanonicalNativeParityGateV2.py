#!/usr/bin/env python3
"""Audit native timing joins and native-vs-finalized Pump state separately.

This is a read-only gate.  It deliberately does not treat a native receipt
clock as proof that native reserve state was available.  The two evidence
planes are audited independently:

* native receipt/finalized wallet joins: signature, slot, and receive-clock
  completeness;
* native replay/finalized state rows: exact pre/post virtual-reserve parity.

The gate never submits, selects, freezes, or promotes a strategy.  It also
does not overwrite the older parity reports; callers provide a new output
path for this version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any


MIN_STATE_OVERLAP = 20
MIN_STATE_MATCH_RATE = 0.99
MAX_RESERVE_ERROR_LAMPORTS = 2
MIN_RECEIPT_JOINS = 20


def sha256_file(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_rows(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    if path.suffix == ".jsonl":
        rows: list[dict[str, Any]] = []
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
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return [value] if isinstance(value, dict) else []


def signature(row: dict[str, Any]) -> str:
    return str(
        row.get("signature")
        or row.get("transaction_signature")
        or row.get("candidate_signature")
        or ""
    )


def unique_by_signature(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = signature(row)
        if key:
            result[key] = row
    return result


def nested_state(row: dict[str, Any]) -> dict[str, Any]:
    state = row.get("native_state")
    if isinstance(state, dict):
        return state
    state = row.get("canonical_state")
    if isinstance(state, dict):
        return state
    state = row.get("state")
    return state if isinstance(state, dict) else {}


def reserve_pair(row: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]] | None:
    state = nested_state(row)
    pre = state.get("pre_state") if state else row.get("pre_state")
    post = state.get("post_state") if state else row.get("post_state")
    if not isinstance(pre, dict) or not isinstance(post, dict):
        return None

    def read(source: dict[str, Any], *names: str) -> int | None:
        for name in names:
            if source.get(name) is not None:
                try:
                    return int(source[name])
                except (TypeError, ValueError):
                    return None
        return None

    pre_sol = read(pre, "virtual_sol", "virtualSolReserves", "virtualSol")
    pre_token = read(pre, "virtual_token", "virtualTokenReserves", "virtualToken")
    post_sol = read(post, "virtual_sol", "virtualSolReserves", "virtualSol")
    post_token = read(post, "virtual_token", "virtualTokenReserves", "virtualToken")
    if None in (pre_sol, pre_token, post_sol, post_token):
        return None
    return (
        {"virtual_sol": int(pre_sol), "virtual_token": int(pre_token)},
        {"virtual_sol": int(post_sol), "virtual_token": int(post_token)},
    )


def compare_state_rows(
    native_rows: dict[str, dict[str, Any]],
    finalized_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    overlap = sorted(set(native_rows) & set(finalized_rows))
    compared = 0
    exact = 0
    missing_state = 0
    mismatches: list[dict[str, Any]] = []
    max_error = 0
    for key in overlap:
        native = reserve_pair(native_rows[key])
        finalized = reserve_pair(finalized_rows[key])
        if native is None or finalized is None:
            missing_state += 1
            mismatches.append({"signature": key, "reason": "missing_virtual_reserve_state"})
            continue
        compared += 1
        errors = {
            "pre_virtual_sol": abs(native[0]["virtual_sol"] - finalized[0]["virtual_sol"]),
            "pre_virtual_token": abs(native[0]["virtual_token"] - finalized[0]["virtual_token"]),
            "post_virtual_sol": abs(native[1]["virtual_sol"] - finalized[1]["virtual_sol"]),
            "post_virtual_token": abs(native[1]["virtual_token"] - finalized[1]["virtual_token"]),
        }
        local_max = max(errors.values())
        max_error = max(max_error, local_max)
        if local_max <= MAX_RESERVE_ERROR_LAMPORTS:
            exact += 1
        else:
            mismatches.append({"signature": key, "errors": errors})

    match_rate = exact / compared if compared else 0.0
    passed = (
        len(overlap) >= MIN_STATE_OVERLAP
        and compared >= MIN_STATE_OVERLAP
        and exact == compared
        and match_rate >= MIN_STATE_MATCH_RATE
        and max_error <= MAX_RESERVE_ERROR_LAMPORTS
    )
    return {
        "native_rows": len(native_rows),
        "finalized_rows": len(finalized_rows),
        "overlap_rows": len(overlap),
        "compared_rows": compared,
        "exact_rows": exact,
        "missing_state_rows": missing_state,
        "mismatch_rows": len(mismatches),
        "match_rate": match_rate,
        "max_reserve_error_lamports": max_error,
        "thresholds": {
            "min_overlap": MIN_STATE_OVERLAP,
            "min_match_rate": MIN_STATE_MATCH_RATE,
            "max_reserve_error_lamports": MAX_RESERVE_ERROR_LAMPORTS,
        },
        "status": "PASS_NATIVE_STATE_REPLAY_PARITY" if passed else "BLOCKED_NATIVE_STATE_REPLAY_PARITY",
        "mismatches": mismatches[:100],
    }


def audit_receipts(joins: list[dict[str, Any]]) -> dict[str, Any]:
    unique = unique_by_signature(joins)
    valid_clock = 0
    slot_mismatch = 0
    invalid_samples: list[dict[str, Any]] = []
    for key, row in unique.items():
        native_slot = row.get("nativeReceiveSlot")
        if native_slot is None:
            native_slot = row.get("native_receive_slot")
        finalized_slot = row.get("slot")
        unix_ns = row.get("nativeReceiveUnixNs")
        if unix_ns is None:
            unix_ns = row.get("native_receive_unix_ns")
        mono_ns = row.get("nativeReceiveMonotonicNs")
        if mono_ns is None:
            mono_ns = row.get("native_receive_monotonic_ns")
        source = row.get("nativeReceiveSource")
        if source is None:
            source = row.get("native_receive_source")
        complete = all(value not in (None, "") for value in (native_slot, unix_ns, mono_ns, source))
        if complete:
            valid_clock += 1
        if native_slot is not None and finalized_slot is not None and int(native_slot) != int(finalized_slot):
            slot_mismatch += 1
            if len(invalid_samples) < 100:
                invalid_samples.append({"signature": key, "reason": "native_finalized_slot_mismatch", "native_slot": native_slot, "finalized_slot": finalized_slot})
        elif not complete and len(invalid_samples) < 100:
            invalid_samples.append({"signature": key, "reason": "incomplete_native_clock"})
    passed = len(unique) >= MIN_RECEIPT_JOINS and valid_clock == len(unique) and slot_mismatch == 0
    return {
        "rows": len(joins),
        "unique_signatures": len(unique),
        "valid_native_clock_rows": valid_clock,
        "clock_coverage_ratio": valid_clock / len(unique) if unique else 0.0,
        "native_finalized_slot_mismatches": slot_mismatch,
        "thresholds": {"min_receipt_joins": MIN_RECEIPT_JOINS},
        "status": "PASS_NATIVE_RECEIPT_JOIN" if passed else "BLOCKED_NATIVE_RECEIPT_JOIN",
        "invalid_samples": invalid_samples,
    }


def audit(
    native_replay_path: Path,
    finalized_state_path: Path,
    joins_path: Path | None,
    target_finalized_state_path: Path | None = None,
    target_native_state_path: Path | None = None,
) -> dict[str, Any]:
    native_rows = unique_by_signature(load_json_rows(native_replay_path))
    finalized_rows = unique_by_signature(load_json_rows(finalized_state_path))
    target_rows = unique_by_signature(load_json_rows(target_finalized_state_path))
    target_native_rows = unique_by_signature(load_json_rows(target_native_state_path))
    joins = load_json_rows(joins_path)
    state_gate = compare_state_rows(native_rows, finalized_rows)
    target_state_gate = compare_state_rows(native_rows, target_rows) if target_rows else {
        "native_rows": len(native_rows),
        "finalized_rows": 0,
        "overlap_rows": 0,
        "compared_rows": 0,
        "exact_rows": 0,
        "missing_state_rows": 0,
        "mismatch_rows": 0,
        "match_rate": 0.0,
        "max_reserve_error_lamports": 0,
        "thresholds": {
            "min_overlap": MIN_STATE_OVERLAP,
            "min_match_rate": MIN_STATE_MATCH_RATE,
            "max_reserve_error_lamports": MAX_RESERVE_ERROR_LAMPORTS,
        },
        "status": "BLOCKED_TARGET_FINALIZED_STATE_MISSING",
        "mismatches": [],
    }
    target_native_state_gate = compare_state_rows(target_native_rows, target_rows) if target_rows else {
        "native_rows": len(target_native_rows),
        "finalized_rows": len(target_rows),
        "overlap_rows": 0,
        "compared_rows": 0,
        "exact_rows": 0,
        "missing_state_rows": 0,
        "mismatch_rows": 0,
        "match_rate": 0.0,
        "max_reserve_error_lamports": 0,
        "thresholds": {
            "min_overlap": MIN_STATE_OVERLAP,
            "min_match_rate": MIN_STATE_MATCH_RATE,
            "max_reserve_error_lamports": MAX_RESERVE_ERROR_LAMPORTS,
        },
        "status": "BLOCKED_TARGET_FINALIZED_STATE_MISSING",
        "mismatches": [],
    }
    receipt_gate = audit_receipts(joins)
    state_pass = state_gate["status"] == "PASS_NATIVE_STATE_REPLAY_PARITY"
    target_state_pass = target_state_gate["status"] == "PASS_NATIVE_STATE_REPLAY_PARITY"
    target_native_state_pass = (
        target_native_state_path is None
        or target_native_state_gate["status"] == "PASS_NATIVE_STATE_REPLAY_PARITY"
    )
    return {
        "schema": "CANONICAL_NATIVE_PARITY_GATE_V2",
        "generated_at_ms": int(time.time() * 1000),
        "read_only": True,
        "trading_enabled": False,
        "promotion_allowed": False,
        "economics_input_allowed": state_pass and target_state_pass and target_native_state_pass and receipt_gate["status"] == "PASS_NATIVE_RECEIPT_JOIN",
        "inputs": {
            "native_replay": {"path": str(native_replay_path), "sha256": sha256_file(native_replay_path)},
            "finalized_state": {"path": str(finalized_state_path), "sha256": sha256_file(finalized_state_path)},
            "native_finalized_joins": {"path": str(joins_path) if joins_path else None, "sha256": sha256_file(joins_path)},
            "target_finalized_state": {"path": str(target_finalized_state_path) if target_finalized_state_path else None, "sha256": sha256_file(target_finalized_state_path)},
            "target_native_state": {"path": str(target_native_state_path) if target_native_state_path else None, "sha256": sha256_file(target_native_state_path)},
        },
        "native_receipt_gate": receipt_gate,
        "native_state_gate": state_gate,
        "target_wallet_state_gate": target_state_gate,
        "target_native_state_gate": target_native_state_gate if target_native_state_path else {
            "status": "NOT_CONFIGURED",
            "native_rows": 0,
            "finalized_rows": len(target_rows),
        },
        "gate_status": "PASS_NATIVE_RECEIPT_AND_STATE_PARITY" if state_pass and target_state_pass and target_native_state_pass and receipt_gate["status"] == "PASS_NATIVE_RECEIPT_JOIN" else "BLOCKED_NATIVE_STATE_OR_RECEIPT_PARITY",
        "reason": "Native receipt clocks and reserve-state parity are separate gates; timing alone cannot admit economics.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-replay", type=Path, required=True)
    parser.add_argument("--finalized-state", type=Path, required=True)
    parser.add_argument("--native-joins", type=Path)
    parser.add_argument("--target-finalized-state", type=Path)
    parser.add_argument("--target-native-state", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.native_replay, args.finalized_state, args.native_joins, args.target_finalized_state, args.target_native_state)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("gate_status", "economics_input_allowed", "native_receipt_gate", "native_state_gate")}, sort_keys=True))


if __name__ == "__main__":
    main()
