#!/usr/bin/env python3
"""Audit native pre-finality Pump replay against finalized exact state."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

MIN_OVERLAP = 20
MIN_MATCH_RATE = 0.99
MAX_RESERVE_ERROR_LAMPORTS = 2


def rows_by_signature(path: Path) -> dict[str, dict[str, Any]]:
    result = {}
    if not path.exists():
        return result
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            signature = str(row.get("transaction_signature") or row.get("signature") or "")
            if signature:
                result[signature] = row
    return result


def state_pair(row: dict[str, Any], native: bool) -> tuple[dict[str, int], dict[str, int]] | None:
    if native:
        state = row.get("canonical_state") or {}
    else:
        state = row.get("canonical_state") or {}
    pre = state.get("pre_state") if isinstance(state, dict) else None
    post = state.get("post_state") if isinstance(state, dict) else None
    if not isinstance(pre, dict) or not isinstance(post, dict):
        return None
    keys = ("virtual_sol", "virtual_token")
    try:
        return ({k: int(pre[k]) for k in keys}, {k: int(post[k]) for k in keys})
    except (KeyError, TypeError, ValueError):
        return None


def audit(native_path: Path, finalized_path: Path) -> dict[str, Any]:
    native = rows_by_signature(native_path)
    finalized = rows_by_signature(finalized_path)
    overlap = sorted(set(native) & set(finalized))
    mismatches = []
    compared = 0
    max_error = 0
    for signature in overlap:
        n = state_pair(native[signature], True)
        f = state_pair(finalized[signature], False)
        if not n or not f:
            mismatches.append({"signature": signature, "reason": "missing_virtual_state"})
            continue
        compared += 1
        errors = {key: abs(n[0][key] - f[0][key]) for key in n[0]}
        errors.update({f"post_{key}": abs(n[1][key] - f[1][key]) for key in n[1]})
        local_max = max(errors.values()) if errors else 0
        max_error = max(max_error, local_max)
        if local_max > MAX_RESERVE_ERROR_LAMPORTS:
            mismatches.append({"signature": signature, "errors": errors})
    match_rate = (compared - len(mismatches)) / compared if compared else 0.0
    passed = len(overlap) >= MIN_OVERLAP and compared >= MIN_OVERLAP and not mismatches and match_rate >= MIN_MATCH_RATE
    return {
        "schema": "NATIVE_PUMP_REPLAY_PARITY_GATE_V1",
        "generatedAtMs": int(time.time() * 1000),
        "nativeRows": len(native),
        "finalizedRows": len(finalized),
        "overlapRows": len(overlap),
        "comparedRows": compared,
        "mismatchRows": len(mismatches),
        "matchRate": match_rate,
        "maxReserveErrorLamports": max_error,
        "thresholds": {"minOverlap": MIN_OVERLAP, "minMatchRate": MIN_MATCH_RATE,
                        "maxReserveErrorLamports": MAX_RESERVE_ERROR_LAMPORTS},
        "gateStatus": "PASS_NATIVE_REPLAY_PARITY" if passed else "BLOCKED_INSUFFICIENT_NATIVE_REPLAY_PARITY",
        "promotionAllowed": False,
        "economicsInputAllowed": passed,
        "mismatches": mismatches[:100],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--finalized", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = audit(args.native, args.finalized)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
