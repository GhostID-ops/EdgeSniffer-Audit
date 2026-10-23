#!/usr/bin/env python3
"""Adjudicate native-vs-finalized Pump state mismatches without changing a gate.

The canonical parity gate intentionally asks a strict question: did the state
selected at native receipt equal the later finalized event's pre/post state?
This report answers the next question: when they differ, does the finalized
pre-state appear in the native account tape only *after* the transaction was
received?  If so, the difference is receipt/account-stream ordering lag, not a
missing causal seed and not proof that the native decoder is wrong.

This is read-only research telemetry.  It never changes the canonical parity
gate, admits economics, freezes a policy, or submits a transaction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


MAX_RESERVE_ERROR_LAMPORTS = 2


def sha256_file(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    if path.suffix == ".jsonl":
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


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    for key in ("native_state", "canonical_state", "state"):
        value = row.get(key)
        if isinstance(value, dict):
            return value
    return row


def state_pair(row: dict[str, Any], key: str) -> tuple[int, int] | None:
    state = nested_state(row)
    value = state.get(key)
    if not isinstance(value, dict):
        return None

    def read(*names: str) -> int | None:
        for name in names:
            parsed = as_int(value.get(name))
            if parsed is not None:
                return parsed
        return None

    sol = read("virtual_sol", "virtualSolReserves", "virtualSol")
    token = read("virtual_token", "virtualTokenReserves", "virtualToken")
    return (sol, token) if sol is not None and token is not None else None


def curve_id(row: dict[str, Any]) -> str:
    state = row.get("native_state")
    return str(row.get("bonding_curve") or row.get("bondingCurve") or (state or {}).get("bonding_curve") or "")


def row_slot(row: dict[str, Any]) -> int | None:
    return as_int(row.get("same_source_slot")) or as_int(row.get("slot"))


def tx_receive_ns(row: dict[str, Any]) -> int | None:
    return (
        as_int(row.get("same_source_receive_unix_ns"))
        or as_int(row.get("native_receive_unix_ns"))
        or as_int(row.get("nativeReceiveUnixNs"))
    )


def seed_receive_ns(row: dict[str, Any]) -> int | None:
    return as_int(row.get("native_receive_unix_ns")) or as_int(row.get("nativeReceiveUnixNs"))


def seed_virtual_pair(row: dict[str, Any]) -> tuple[int, int] | None:
    sol = as_int(row.get("virtualSolReserves")) or as_int(row.get("virtual_sol_reserves")) or as_int(row.get("virtual_sol"))
    token = as_int(row.get("virtualTokenReserves")) or as_int(row.get("virtual_token_reserves")) or as_int(row.get("virtual_token"))
    return (sol, token) if sol is not None and token is not None else None


def selected_lineage_is_causal(native: dict[str, Any], finalized: dict[str, Any]) -> bool:
    state = native.get("native_state") if isinstance(native.get("native_state"), dict) else {}
    seed_ns = as_int(state.get("seed_native_receive_unix_ns"))
    tx_ns = tx_receive_ns(native) or tx_receive_ns(finalized)
    seed_slot = as_int(state.get("seed_slot"))
    tx_slot = row_slot(native) or row_slot(finalized)
    seed_sig = str(state.get("seed_account_txn_signature") or "")
    tx_sig = signature(native)
    write_version = as_int(state.get("seed_write_version"))
    return bool(
        seed_ns is not None
        and tx_ns is not None
        and seed_ns < tx_ns
        and seed_slot is not None
        and tx_slot is not None
        and seed_slot == tx_slot
        and seed_sig
        and seed_sig != tx_sig
        and write_version is not None
        and state.get("seed_native_receive_source")
    )


def exact(left: tuple[int, int] | None, right: tuple[int, int] | None) -> bool:
    if left is None or right is None:
        return False
    return max(abs(left[0] - right[0]), abs(left[1] - right[1])) <= MAX_RESERVE_ERROR_LAMPORTS


def adjudicate(
    native_state_path: Path,
    finalized_state_path: Path,
    seed_path: Path,
    output_path: Path,
    sample_limit: int = 100,
) -> dict[str, Any]:
    native = unique_by_signature(read_rows(native_state_path))
    finalized = unique_by_signature(read_rows(finalized_state_path))
    seeds_by_curve: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_rows(seed_path):
        curve = str(row.get("bondingCurve") or row.get("bonding_curve") or "")
        if curve:
            seeds_by_curve[curve].append(row)
    for rows in seeds_by_curve.values():
        rows.sort(key=lambda row: seed_receive_ns(row) or as_int(row.get("observedAtMs")) or 0)

    overlap = sorted(set(native) & set(finalized))
    categories: Counter[str] = Counter()
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows: list[dict[str, Any]] = []

    for sig in overlap:
        nrow = native[sig]
        frow = finalized[sig]
        npre, npost = state_pair(nrow, "pre_state"), state_pair(nrow, "post_state")
        fpre, fpost = state_pair(frow, "pre_state"), state_pair(frow, "post_state")
        pre_exact, post_exact = exact(npre, fpre), exact(npost, fpost)
        tx_ns = tx_receive_ns(nrow) or tx_receive_ns(frow)
        slot = row_slot(nrow) or row_slot(frow)
        curve = curve_id(nrow) or curve_id(frow)
        causal = selected_lineage_is_causal(nrow, frow)
        later_finalized_pre_matches: list[dict[str, Any]] = []
        earlier_finalized_pre_matches: list[dict[str, Any]] = []
        for seed in seeds_by_curve.get(curve, []):
            pair = seed_virtual_pair(seed)
            seed_ns = seed_receive_ns(seed)
            seed_slot = as_int(seed.get("slot"))
            if pair is None or not exact(pair, fpre):
                continue
            if slot is not None and seed_slot != slot:
                continue
            if tx_ns is None or seed_ns is None:
                continue
            seed_sig = str(seed.get("account_txn_signature") or seed.get("accountTxnSignature") or "")
            if seed_sig == sig:
                continue
            item = {
                "receive_unix_ns": seed_ns,
                "delta_ns_from_tx": seed_ns - tx_ns,
                "slot": seed_slot,
                "write_version": as_int(seed.get("write_version") or seed.get("writeVersion")),
                "account_txn_signature": seed_sig,
            }
            (later_finalized_pre_matches if seed_ns > tx_ns else earlier_finalized_pre_matches).append(item)

        if not pre_exact and not post_exact:
            category = "NATIVE_AND_FINALIZED_PRE_POST_DIFFER"
        elif not pre_exact and post_exact:
            category = "FINALIZED_POST_MATCHES_NATIVE_PRE_DIFFERS"
        elif pre_exact and not post_exact:
            category = "FINALIZED_PRE_MATCHES_NATIVE_POST_DIFFERS"
        else:
            category = "EXACT_PRE_POST_MATCH"
        if not causal:
            category = "SELECTED_NATIVE_LINEAGE_NOT_CAUSAL"
        elif not pre_exact and later_finalized_pre_matches:
            category = "FINALIZED_PRE_APPEARS_AFTER_RECEIPT"
        categories[category] += 1
        detail = {
            "signature": sig,
            "bonding_curve": curve,
            "slot": slot,
            "tx_receive_unix_ns": tx_ns,
            "selected_native_lineage_causal": causal,
            "pre_exact": pre_exact,
            "post_exact": post_exact,
            "native_pre": npre,
            "finalized_pre": fpre,
            "native_post": npost,
            "finalized_post": fpost,
            "later_same_slot_finalized_pre_seed_matches": later_finalized_pre_matches,
            "earlier_same_slot_finalized_pre_seed_matches": earlier_finalized_pre_matches,
            "category": category,
        }
        rows.append(detail)
        if len(samples[category]) < sample_limit:
            samples[category].append(detail)

    causal_rows = sum(1 for row in rows if row["selected_native_lineage_causal"])
    later_rows = categories["FINALIZED_PRE_APPEARS_AFTER_RECEIPT"]
    result = {
        "schema": "NATIVE_PARITY_MISMATCH_ADJUDICATOR_V1",
        "generated_at_ms": int(time.time() * 1000),
        "read_only": True,
        "trading_enabled": False,
        "economics_input_allowed": False,
        "promotion_allowed": False,
        "interpretation": {
            "purpose": "Distinguish receipt-ordering lag from decoder or lineage failures.",
            "strict_99_percent_gate_unchanged": True,
            "later_finalized_pre_match_is_not_live_visible_at_receipt": True,
            "causal_state_is_not_identical_to_finalized_event_state": True,
        },
        "inputs": {
            "native_state": {"path": str(native_state_path), "sha256": sha256_file(native_state_path), "rows": len(native)},
            "finalized_state": {"path": str(finalized_state_path), "sha256": sha256_file(finalized_state_path), "rows": len(finalized)},
            "native_seeds": {"path": str(seed_path), "sha256": sha256_file(seed_path), "rows": sum(len(v) for v in seeds_by_curve.values())},
        },
        "summary": {
            "native_rows": len(native),
            "finalized_rows": len(finalized),
            "overlap_rows": len(overlap),
            "causally_valid_selected_native_rows": causal_rows,
            "category_counts": dict(sorted(categories.items())),
            "finalized_pre_found_only_after_receipt_rows": later_rows,
            "strict_99_gate_status": "UNCHANGED_BLOCKED_SEPARATE_ADJUDICATION",
        },
        "samples": {key: value for key, value in sorted(samples.items())},
        "rows": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-state", type=Path, required=True)
    parser.add_argument("--finalized-state", type=Path, required=True)
    parser.add_argument("--native-seeds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-limit", type=int, default=100)
    args = parser.parse_args()
    result = adjudicate(args.native_state, args.finalized_state, args.native_seeds, args.output, args.sample_limit)
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
