#!/usr/bin/env python3
"""Resolve executor/CPI action identity without fabricating native economics.

The AllenHark tape proves that the outer executor invokes Pump and preserves
the raw executor instruction bytes.  This audit joins those candidates to the
preserved finalized transaction logs and inner instruction topology.  It
admits BUY_V3/SELL_V3 only when exactly one Pump action is present and the
candidate curve appears in the Pump inner instruction accounts.

This is an action/route gate only.  It does not create pre-receipt reserves,
fees, quotes, or PnL and therefore cannot pass the native-state economics gate
by itself.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

EXECUTOR = "2eFuLSK4McKonBcB6XLax8gSgxwR56zNfrjHsvkNkftj"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_candidates(path: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            signature = str(row.get("signature") or "")
            if signature:
                result.setdefault(signature, []).append(row)
    return result


def account_list(instruction: dict[str, Any], keys: list[str]) -> list[str]:
    values = instruction.get("accounts") or []
    result: list[str] = []
    for value in values:
        if isinstance(value, str):
            result.append(value)
        else:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(keys):
                result.append(keys[index])
    return result


def all_instructions(transaction: dict[str, Any]) -> list[tuple[str, dict[str, Any], list[str]]]:
    message = transaction.get("message") if isinstance(transaction.get("message"), dict) else {}
    keys = [str(value) for value in message.get("accountKeys") or message.get("account_keys") or []]
    result: list[tuple[str, dict[str, Any], list[str]]] = []
    for index, instruction in enumerate(message.get("instructions") or []):
        if isinstance(instruction, dict):
            result.append((f"outer:{index}", instruction, account_list(instruction, keys)))
    meta = transaction.get("meta") if isinstance(transaction.get("meta"), dict) else {}
    for group in meta.get("innerInstructions") or meta.get("inner_instructions") or []:
        if not isinstance(group, dict):
            continue
        outer = group.get("index")
        for index, instruction in enumerate(group.get("instructions") or []):
            if isinstance(instruction, dict):
                result.append((f"inner:{outer}:{index}", instruction, account_list(instruction, keys)))
    return result


def instruction_program(instruction: dict[str, Any], keys: list[str] | None = None) -> str | None:
    value = instruction.get("programId") or instruction.get("program_id")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("value") or value.get("Some") or "") or None
    if keys is not None and value is not None:
        try:
            index = int(value)
            return keys[index] if 0 <= index < len(keys) else None
        except (TypeError, ValueError):
            return None
    return None


def decode_logs(transaction: dict[str, Any]) -> list[str]:
    meta = transaction.get("meta") if isinstance(transaction.get("meta"), dict) else {}
    logs = meta.get("logMessages") or meta.get("log_messages") or []
    actions: list[str] = []
    for message in logs:
        text = str(message)
        if "Instruction: BuyV3" in text:
            actions.append("BUY_V3")
        if "Instruction: SellV3" in text:
            actions.append("SELL_V3")
    return actions


def audit(candidates_path: Path, finalized_path: Path, output_path: Path) -> dict[str, Any]:
    candidates = load_candidates(candidates_path)
    candidate_signatures = set(candidates)
    joined: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    seen: set[str] = set()
    with finalized_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                source = json.loads(line)
            except json.JSONDecodeError:
                continue
            signature = str(source.get("signature") or "")
            if signature not in candidate_signatures or signature in seen:
                continue
            seen.add(signature)
            transaction = source.get("transaction") if isinstance(source.get("transaction"), dict) else {}
            instructions = all_instructions(transaction)
            pump_inner = [
                (coordinate, accounts)
                for coordinate, instruction, accounts in instructions
                if instruction_program(instruction) == PUMP or PUMP in accounts
            ]
            actions = decode_logs(transaction)
            unique_actions = sorted(set(actions))
            route_rows = candidates[signature]
            curve_ids = sorted({str(value) for row in route_rows for value in row.get("curve_candidates") or []})
            curve_in_pump = any(curve in accounts for _, accounts in pump_inner for curve in curve_ids)
            if len(unique_actions) != 1:
                status = "UNRESOLVED_ACTION_LOG"
                action = None
            elif not curve_in_pump:
                status = "CURVE_NOT_CONFIRMED_IN_PUMP_INNER_ACCOUNTS"
                action = unique_actions[0]
            else:
                status = "PASS_EXECUTOR_CPI_ACTION"
                action = unique_actions[0]
            for row in route_rows:
                raw_hex = str(row.get("instruction_data_hex") or "")
                try:
                    raw = bytes.fromhex(raw_hex)
                except ValueError:
                    raw = b""
                joined.append({
                    "schema": "EXECUTOR_CPI_ACTION_PARITY_V1",
                    "signature": signature,
                    "slot": row.get("slot"),
                    "curve": row.get("curve_candidates", [None])[0],
                    "action": action,
                    "status": status,
                    "executor_program_id": row.get("executor_program_id"),
                    "pump_program_id": row.get("pump_program_id"),
                    "executor_instruction_data_hex": raw_hex,
                    "executor_instruction_length": len(raw),
                    "executor_layout_shape": "OPCODE_1_U64_8_ZERO_TAIL_8" if len(raw) == 17 and raw[9:] == b"\x00" * 8 else "UNRECOGNIZED",
                    "pump_inner_instruction_count": len(pump_inner),
                    "curve_confirmed_in_pump_inner_accounts": curve_in_pump,
                    "finalized_source_sha256": sha256(finalized_path),
                })
            status_counts[status] += len(route_rows)
    result = {
        "schema": "EXECUTOR_CPI_ACTION_PARITY_AUDIT_V1",
        "generated_at_ms": int(time.time() * 1000),
        "read_only": True,
        "trading_enabled": False,
        "candidate_rows": sum(len(rows) for rows in candidates.values()),
        "candidate_signatures": len(candidates),
        "finalized_joined_signatures": len(seen),
        "output_rows": len(joined),
        "status_counts": dict(status_counts),
        "action_gate": "PASS_EXECUTOR_CPI_ACTION" if joined and all(row["status"] == "PASS_EXECUTOR_CPI_ACTION" for row in joined) else "BLOCKED_EXECUTOR_CPI_ACTION_PARITY",
        "native_state_economics_allowed": False,
        "inputs": {"candidates": {"path": str(candidates_path), "sha256": sha256(candidates_path)}, "finalized": {"path": str(finalized_path), "sha256": sha256(finalized_path)}},
        "rows": joined,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--finalized", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.candidates, args.finalized, args.output)
    print(json.dumps({key: result[key] for key in ("candidate_rows", "candidate_signatures", "finalized_joined_signatures", "output_rows", "status_counts", "action_gate", "native_state_economics_allowed")}, sort_keys=True))


if __name__ == "__main__":
    main()
