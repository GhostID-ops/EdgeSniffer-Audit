#!/usr/bin/env python3
"""Audit a non-look-ahead native Pump state replay bridge.

This bridge does not replace the strict finalized-value parity gate. It answers
the live question directly:

1. Was the selected Pump account state causally available at receipt?
2. Can the target instruction be decoded and replayed from that state using
   the native fee fields?
3. Was the finalized event's pre-state actually visible by receipt, or only
   later in the account-update stream?

The bridge is read-only. It never uses a future account update as live input,
never admits economics, and never submits a transaction.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
MAX_RESERVE_ERROR_LAMPORTS = 2


def load_curve_module() -> Any:
    path = Path(__file__).with_name("exactCurveEconomicsV1.py")
    spec = importlib.util.spec_from_file_location("causal_replay_curve", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


curve = load_curve_module()


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


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def signature(row: dict[str, Any]) -> str:
    return str(row.get("signature") or row.get("transaction_signature") or "")


def unique(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = signature(row)
        if key:
            result[key] = row
    return result


def nested_state(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("native_state")
    return value if isinstance(value, dict) else row


def reserve_state(row: dict[str, Any], name: str) -> tuple[int, int, int, int] | None:
    state = nested_state(row).get(name)
    if not isinstance(state, dict):
        return None

    def read(*names: str) -> int | None:
        for candidate in names:
            parsed = as_int(state.get(candidate))
            if parsed is not None:
                return parsed
        return None

    values = (
        read("virtual_sol", "virtualSolReserves", "virtualSol"),
        read("virtual_token", "virtualTokenReserves", "virtualToken"),
        read("real_sol", "realSolReserves", "realSol"),
        read("real_token", "realTokenReserves", "realToken"),
    )
    return values if all(value is not None for value in values) else None


def virtual_pair(row: dict[str, Any], name: str) -> tuple[int, int] | None:
    state = reserve_state(row, name)
    return (state[0], state[1]) if state is not None else None


def exact_reserves(left: tuple[int, ...] | None, right: tuple[int, ...] | None) -> bool:
    return bool(left is not None and right is not None and max(abs(a - b) for a, b in zip(left, right)) <= MAX_RESERVE_ERROR_LAMPORTS)


def curve_id(row: dict[str, Any]) -> str:
    state = row.get("native_state") if isinstance(row.get("native_state"), dict) else {}
    return str(row.get("bonding_curve") or row.get("bondingCurve") or state.get("bonding_curve") or "")


def tx_receive_ns(row: dict[str, Any]) -> int | None:
    return (
        as_int(row.get("same_source_receive_unix_ns"))
        or as_int(row.get("native_receive_unix_ns"))
        or as_int(row.get("nativeReceiveUnixNs"))
    )


def tx_slot(row: dict[str, Any]) -> int | None:
    return as_int(row.get("same_source_slot")) or as_int(row.get("slot"))


def tx_index(row: dict[str, Any]) -> int | None:
    return as_int(row.get("same_source_transaction_index")) or as_int(row.get("transaction_index"))


def seed_receive_ns(row: dict[str, Any]) -> int | None:
    return as_int(row.get("native_receive_unix_ns")) or as_int(row.get("nativeReceiveUnixNs"))


def seed_pair(row: dict[str, Any]) -> tuple[int, int] | None:
    sol = as_int(row.get("virtualSolReserves")) or as_int(row.get("virtual_sol_reserves")) or as_int(row.get("virtual_sol"))
    token = as_int(row.get("virtualTokenReserves")) or as_int(row.get("virtual_token_reserves")) or as_int(row.get("virtual_token"))
    return (sol, token) if sol is not None and token is not None else None


def causal_lineage(row: dict[str, Any]) -> bool:
    state = row.get("native_state") if isinstance(row.get("native_state"), dict) else {}
    seed_ns = as_int(state.get("seed_native_receive_unix_ns"))
    receive_ns = tx_receive_ns(row)
    seed_slot = as_int(state.get("seed_slot"))
    slot = tx_slot(row)
    seed_sig = str(state.get("seed_account_txn_signature") or "")
    return bool(
        seed_ns is not None
        and receive_ns is not None
        and seed_ns < receive_ns
        and seed_slot is not None
        and slot is not None
        and seed_slot == slot
        and seed_sig
        and seed_sig != signature(row)
        and as_int(state.get("seed_write_version")) is not None
        and state.get("seed_native_receive_source")
    )


def pump_instruction_for_target(tx: dict[str, Any], target_curve: str) -> tuple[Any, int] | None:
    keys = [str(value) for value in tx.get("account_keys") or []]
    for index, instruction in enumerate(tx.get("instructions") or []):
        program_id = instruction.get("program_id")
        if isinstance(program_id, dict):
            program_id = program_id.get("Some") or program_id.get("value")
        if program_id != PUMP_PROGRAM_ID:
            continue
        account_indexes = [as_int(value) for value in instruction.get("accounts") or []]
        accounts = [keys[i] for i in account_indexes if i is not None and 0 <= i < len(keys)]
        if target_curve not in accounts:
            continue
        try:
            decoded = curve.decode_pump_instruction(bytes.fromhex(str(instruction.get("data_hex") or "")))
        except Exception:
            continue
        return decoded, index
    return None


def fee_fields(row: dict[str, Any]) -> Any | None:
    state = row.get("native_state") if isinstance(row.get("native_state"), dict) else {}
    protocol = as_int(state.get("protocol_fee_basis_points"))
    creator = as_int(state.get("creator_fee_basis_points"))
    lp = as_int(state.get("lp_fee_basis_points"))
    if protocol is None or creator is None:
        return None
    return curve.Fees(protocol_bps=protocol, creator_bps=creator, lp_bps=lp or 0)


def first_seed_matches(
    seeds: list[dict[str, Any]],
    expected: tuple[int, int] | None,
    receive_ns: int | None,
    slot: int | None,
    target_signature: str,
) -> dict[str, Any] | None:
    if expected is None:
        return None
    candidates: list[dict[str, Any]] = []
    for seed in seeds:
        if seed_pair(seed) != expected:
            continue
        seed_ns = seed_receive_ns(seed)
        seed_slot = as_int(seed.get("slot"))
        seed_signature = str(seed.get("account_txn_signature") or seed.get("accountTxnSignature") or "")
        if seed_ns is None or receive_ns is None or seed_signature == target_signature:
            continue
        candidates.append({
            "receive_unix_ns": seed_ns,
            "delta_ns_from_target": seed_ns - receive_ns,
            "slot": seed_slot,
            "same_slot": bool(slot is not None and seed_slot == slot),
            "write_version": as_int(seed.get("write_version") or seed.get("writeVersion")),
            "account_txn_signature": seed_signature,
        })
    candidates.sort(key=lambda item: item["receive_unix_ns"])
    return candidates[0] if candidates else None


def audit(
    native_state_path: Path,
    finalized_state_path: Path,
    seed_path: Path,
    transactions_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    native = unique(read_rows(native_state_path))
    finalized = unique(read_rows(finalized_state_path))
    transactions = unique(read_rows(transactions_path))
    seeds_by_curve: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for seed in read_rows(seed_path):
        curve_address = str(seed.get("bondingCurve") or seed.get("bonding_curve") or "")
        if curve_address:
            seeds_by_curve[curve_address].append(seed)
    for values in seeds_by_curve.values():
        values.sort(key=lambda row: seed_receive_ns(row) or as_int(row.get("observedAtMs")) or 0)

    categories: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    overlap = sorted(set(native) & set(finalized))
    replay_exact = 0
    replay_failures = 0
    lineage_valid = 0

    for sig in overlap:
        native_row = native[sig]
        finalized_row = finalized[sig]
        curve_address = curve_id(native_row)
        receive_ns = tx_receive_ns(native_row)
        slot = tx_slot(native_row)
        final_pre = virtual_pair(finalized_row, "pre_state")
        final_post = virtual_pair(finalized_row, "post_state")
        native_pre = reserve_state(native_row, "pre_state")
        native_post = reserve_state(native_row, "post_state")
        tx = transactions.get(sig)
        decoded = pump_instruction_for_target(tx, curve_address) if tx else None
        replay_status = "NOT_REPLAYED"
        replay_post: tuple[int, int, int, int] | None = None
        replay_error: str | None = None
        if decoded and native_pre is not None:
            fees = fee_fields(native_row)
            if fees is None:
                replay_error = "missing_native_fee_fields"
            else:
                try:
                    state = curve.CurveState(*native_pre)
                    quote = curve.apply_instruction(state, decoded[0], fees)
                    replay_post = (
                        quote.post_state.virtual_sol,
                        quote.post_state.virtual_token,
                        quote.post_state.real_sol,
                        quote.post_state.real_token,
                    )
                    replay_status = "EXACT" if exact_reserves(replay_post, native_post) else "MISMATCH"
                    if replay_status == "EXACT":
                        replay_exact += 1
                    else:
                        replay_failures += 1
                except Exception as exc:
                    replay_error = f"{type(exc).__name__}:{exc}"
        else:
            replay_error = "target_pump_instruction_not_found" if tx else "target_transaction_not_found"

        if causal_lineage(native_row):
            lineage_valid += 1
        pre_match = first_seed_matches(seeds_by_curve.get(curve_address, []), final_pre, receive_ns, slot, sig)
        post_match = first_seed_matches(seeds_by_curve.get(curve_address, []), final_post, receive_ns, slot, sig)
        pre_before = bool(pre_match and pre_match["delta_ns_from_target"] <= 0)
        pre_after = bool(pre_match and pre_match["delta_ns_from_target"] > 0)
        native_final_exact = exact_reserves(native_pre, reserve_state(finalized_row, "pre_state")) and exact_reserves(native_post, reserve_state(finalized_row, "post_state"))
        if replay_status != "EXACT":
            category = "CAUSAL_REPLAY_NOT_PROVEN"
        elif native_final_exact:
            category = "RECEIPT_STATE_EQUALS_FINALIZED_STATE"
        elif pre_after and not pre_before:
            category = "FINALIZED_PRE_LOOKAHEAD_ONLY"
        elif pre_before:
            category = "FINALIZED_PRE_RECEIPT_VISIBLE_BUT_NATIVE_SELECTION_DIFFERS"
        else:
            category = "FINALIZED_PRE_NOT_IN_NATIVE_SEED_TAPE"
        categories[category] += 1
        rows.append({
            "signature": sig,
            "bonding_curve": curve_address,
            "slot": slot,
            "transaction_index": tx_index(native_row),
            "receive_unix_ns": receive_ns,
            "selected_native_lineage_valid": causal_lineage(native_row),
            "instruction_replay_status": replay_status,
            "instruction_replay_error": replay_error,
            "replayed_post": replay_post,
            "native_post": native_post,
            "finalized_pre": final_pre,
            "finalized_post": final_post,
            "finalized_pre_seed_match": pre_match,
            "finalized_post_seed_match": post_match,
            "native_equals_finalized": native_final_exact,
            "category": category,
        })

    result = {
        "schema": "NATIVE_CAUSAL_REPLAY_BRIDGE_V1",
        "generated_at_ms": int(time.time() * 1000),
        "read_only": True,
        "trading_enabled": False,
        "economics_input_allowed": False,
        "promotion_allowed": False,
        "method": {
            "receipt_boundary": "same_source_native_transaction_receive",
            "state_source": "latest_causal_native_account_seed_selected_before_receipt",
            "replay": "decode_target_pump_instruction_and_apply_exact_curve_formula_from_native_state_and_native_fee_fields",
            "lookahead_forbidden": True,
            "finalized_value_comparison_is_diagnostic_only": True,
        },
        "inputs": {
            "native_state": {"path": str(native_state_path), "sha256": sha256_file(native_state_path), "rows": len(native)},
            "finalized_state": {"path": str(finalized_state_path), "sha256": sha256_file(finalized_state_path), "rows": len(finalized)},
            "native_seeds": {"path": str(seed_path), "sha256": sha256_file(seed_path), "rows": sum(len(v) for v in seeds_by_curve.values())},
            "native_transactions": {"path": str(transactions_path), "sha256": sha256_file(transactions_path), "rows": len(transactions)},
        },
        "summary": {
            "native_rows": len(native),
            "finalized_rows": len(finalized),
            "overlap_rows": len(overlap),
            "instruction_replay_exact_rows": replay_exact,
            "instruction_replay_failure_rows": replay_failures,
            "causal_lineage_valid_rows": lineage_valid,
            "category_counts": dict(sorted(categories.items())),
            "causal_replay_gate_status": "PASS_CAUSAL_NATIVE_REPLAY" if overlap and replay_exact == len(overlap) and lineage_valid == len(overlap) else "BLOCKED_CAUSAL_NATIVE_REPLAY",
            "strict_finalized_parity_status": "UNCHANGED_SEPARATE_GATE",
        },
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
    parser.add_argument("--native-transactions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.native_state, args.finalized_state, args.native_seeds, args.native_transactions, args.output)
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
