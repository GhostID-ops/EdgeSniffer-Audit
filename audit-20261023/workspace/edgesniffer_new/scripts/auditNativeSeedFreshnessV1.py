#!/usr/bin/env python3
"""Diagnose why native reserve rows fail finalized parity.

This report never repairs a row by importing finalized state.  It only measures
the causal seed's age and slot distance at the native receive clock, then
compares the native reserves with the preserved finalized event state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rows(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("signature"):
                result[str(row["signature"])] = row
    return result


def reserve_pair(row: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]] | None:
    state = row.get("native_state") if isinstance(row.get("native_state"), dict) else row
    pre = state.get("pre_state") if isinstance(state, dict) else None
    post = state.get("post_state") if isinstance(state, dict) else None
    if not isinstance(pre, dict) or not isinstance(post, dict):
        return None
    keys = ("virtual_sol", "virtual_token")
    try:
        return (
            {key: int(pre[key]) for key in keys},
            {key: int(post[key]) for key in keys},
        )
    except (KeyError, TypeError, ValueError):
        return None


def finalized_pair(row: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]] | None:
    try:
        return (
            {key: int(row["pre_state"][key]) for key in ("virtual_sol", "virtual_token")},
            {key: int(row["post_state"][key]) for key in ("virtual_sol", "virtual_token")},
        )
    except (KeyError, TypeError, ValueError):
        return None


def audit(native_path: Path, finalized_path: Path, output_path: Path) -> dict[str, Any]:
    native = rows(native_path)
    finalized = rows(finalized_path)
    records: list[dict[str, Any]] = []
    categories: Counter[str] = Counter()
    for signature in sorted(set(native) & set(finalized)):
        n = native[signature]
        f = finalized[signature]
        state = n.get("native_state") if isinstance(n.get("native_state"), dict) else {}
        receive_ns = n.get("native_receive_unix_ns")
        try:
            receive_ms = int(receive_ns) // 1_000_000
        except (TypeError, ValueError):
            receive_ms = None
        seed_ms = state.get("seed_observed_at_ms")
        seed_slot = state.get("seed_slot")
        tx_slot = n.get("slot")
        age_ms = receive_ms - int(seed_ms) if receive_ms is not None and seed_ms is not None else None
        slot_gap = int(tx_slot) - int(seed_slot) if tx_slot is not None and seed_slot is not None else None
        actual = reserve_pair(n)
        expected = finalized_pair(f)
        max_error = None
        exact = False
        if actual is not None and expected is not None:
            max_error = max(
                abs(actual[0]["virtual_sol"] - expected[0]["virtual_sol"]),
                abs(actual[0]["virtual_token"] - expected[0]["virtual_token"]),
                abs(actual[1]["virtual_sol"] - expected[1]["virtual_sol"]),
                abs(actual[1]["virtual_token"] - expected[1]["virtual_token"]),
            )
            exact = max_error <= 2
        if exact:
            category = "EXACT"
        elif age_ms is None or slot_gap is None:
            category = "MISSING_CAUSAL_CLOCK_OR_SEED_METADATA"
        elif slot_gap > 0:
            category = "OLDER_SLOT_SEED"
        elif age_ms > 1000:
            category = "STALE_SAME_SLOT_SEED"
        else:
            category = "SAME_SLOT_NEAR_RECEIVE_MISMATCH"
        categories[category] += 1
        records.append({
            "signature": signature,
            "category": category,
            "native_receive_ms": receive_ms,
            "seed_observed_at_ms": seed_ms,
            "seed_age_ms": age_ms,
            "transaction_slot": tx_slot,
            "seed_slot": seed_slot,
            "slot_gap": slot_gap,
            "max_reserve_error_lamports": max_error,
            "exact": exact,
        })
    records.sort(key=lambda row: (row["category"], -(row["seed_age_ms"] or 0), row["signature"]))
    result = {
        "schema": "NATIVE_SEED_FRESHNESS_AUDIT_V1",
        "generated_at_ms": int(time.time() * 1000),
        "read_only": True,
        "trading_enabled": False,
        "native_rows": len(native),
        "finalized_rows": len(finalized),
        "overlap_rows": len(records),
        "category_counts": dict(categories),
        "thresholds": {"exact_reserve_error_lamports": 2, "native_seed_must_precede_receive": True},
        "inputs": {"native": {"path": str(native_path), "sha256": sha256(native_path)}, "finalized": {"path": str(finalized_path), "sha256": sha256(finalized_path)}},
        "records": records,
        "next_engineering_action": "Capture the curve account update before the target transaction within the same slot; do not substitute finalized state.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--finalized", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.native, args.finalized, args.output)
    print(json.dumps({key: result[key] for key in ("native_rows", "finalized_rows", "overlap_rows", "category_counts")}, sort_keys=True))


if __name__ == "__main__":
    main()
