#!/usr/bin/env python3
"""Summarize unknown invoked programs from an immutable finalized RPC tape.

This is deliberately diagnostic only. It does not assign a venue or alter any
route/economics artifact. The output preserves enough instruction/log/account
shape to decide whether a decoder can be admitted later.
"""
from __future__ import annotations

import argparse
import base64
import collections
import hashlib
import json
from pathlib import Path
from typing import Any

import twoCqRouteEvidenceAdaptersV1 as adapters


TARGETS = {
    "93JqDj2DjxDYuKPY3Z1rUG3YzPJykyMsrisPnvWYeuq5",
    "ABWKmNctDYqfC3nTBJW63oFkU5KYAjfMFigWzhuia8NV",
}

BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def decode_base58(value: str) -> bytes:
    number = 0
    for char in value:
        number = number * 58 + BASE58.index(char)
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading + raw


def resolved_keys(tx: dict[str, Any]) -> list[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = [
        str(row.get("pubkey")) if isinstance(row, dict) else str(row)
        for row in message.get("accountKeys") or []
    ]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    keys.extend(str(row) for row in loaded.get("writable") or [])
    keys.extend(str(row) for row in loaded.get("readonly") or [])
    return keys


def instruction_record(instruction: dict[str, Any], keys: list[str], location: str) -> dict[str, Any]:
    index = instruction.get("programIdIndex")
    program = instruction.get("programId")
    if program is None and index is not None and int(index) < len(keys):
        program = keys[int(index)]
    data = instruction.get("data")
    decoded_data: dict[str, Any] = {"encoding": "unknown", "length": None, "prefix_hex": None}
    if isinstance(data, str):
        try:
            raw = decode_base58(data)
            decoded_data = {
                "encoding": "base58",
                "length": len(raw),
                "prefix_hex": raw[:32].hex(),
            }
        except Exception:
            decoded_data = {"encoding": "string", "length": len(data), "prefix_hex": data[:64]}
    accounts = instruction.get("accounts") or []
    account_pubkeys = []
    for account in accounts[:32]:
        try:
            idx = int(account)
            account_pubkeys.append(keys[idx] if 0 <= idx < len(keys) else f"INDEX:{idx}")
        except (TypeError, ValueError):
            account_pubkeys.append(str(account))
    return {
        "location": location,
        "program_id": str(program) if program is not None else None,
        "program_index": index,
        "data": decoded_data,
        "account_count": len(accounts),
        "account_pubkeys_prefix": account_pubkeys,
    }


def tx_summary(raw: dict[str, Any], target: str) -> dict[str, Any]:
    tx = raw.get("transaction") or {}
    meta = tx.get("meta") or {}
    # RPC rows in this project store the Solana message under transaction.
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = resolved_keys(tx)
    top = []
    target_instructions = []
    for index, instruction in enumerate(message.get("instructions") or []):
        row = instruction_record(instruction, keys, f"top:{index}")
        top.append(row.get("program_id"))
        if row.get("program_id") == target:
            target_instructions.append(row)
    inner = []
    for group in meta.get("innerInstructions") or []:
        for index, instruction in enumerate(group.get("instructions") or []):
            row = instruction_record(instruction, keys, f"inner:{group.get('index')}:{index}")
            inner.append(row)
            if row.get("program_id") == target:
                target_instructions.append(row)
    logs = [str(row) for row in meta.get("logMessages") or []]
    target_logs = [row for row in logs if target in row]
    balances = {
        "pre_lamports": meta.get("preBalances") or [],
        "post_lamports": meta.get("postBalances") or [],
        "pre_token_count": len(meta.get("preTokenBalances") or []),
        "post_token_count": len(meta.get("postTokenBalances") or []),
    }
    wallet = str(raw.get("wallet") or raw.get("tracked_wallet") or "")
    token_deltas: dict[str, int] = {}
    for name, direction in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for row in meta.get(name) or []:
            if str(row.get("owner") or "") != wallet:
                continue
            mint = str(row.get("mint") or "")
            token_deltas[mint] = token_deltas.get(mint, 0) + direction * int(((row.get("uiTokenAmount") or {}).get("amount")) or 0)
    return {
        "signature": raw.get("signature"),
        "slot": raw.get("slot"),
        "block_time": raw.get("block_time"),
        "err": meta.get("err"),
        "program_family": raw.get("program_family"),
        "source_wallet": raw.get("wallet") or raw.get("tracked_wallet"),
        "source_keys": sorted(raw.keys()),
        "message_account_keys": keys,
        "route_resolution": adapters.resolve_route(tx),
        "target": target,
        "top_level_programs": list(dict.fromkeys(row for row in top if row)),
        "inner_programs": list(dict.fromkeys(row.get("program_id") for row in inner if row.get("program_id"))),
        "target_instructions": target_instructions,
        "target_log_lines": target_logs[:32],
        "log_lines": logs[:128],
        "balances_shape": balances,
        "wallet_token_deltas": {mint: delta for mint, delta in token_deltas.items() if delta},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    counts: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[dict[str, Any]]] = {target: [] for target in TARGETS}
    signatures: dict[str, list[str]] = {target: [] for target in TARGETS}
    aggregates: dict[str, dict[str, collections.Counter[str]]] = {
        target: {
            "program_family": collections.Counter(),
            "top_cooccurrence": collections.Counter(),
            "inner_cooccurrence": collections.Counter(),
            "instruction_shape": collections.Counter(),
            "data_shape": collections.Counter(),
            "log_shape": collections.Counter(),
            "target_position": collections.Counter(),
            "wallet_lamport_delta": collections.Counter(),
            "wallet_token_delta": collections.Counter(),
            "wallet_key_presence": collections.Counter(),
            "wallet_token_owner_presence": collections.Counter(),
        }
        for target in TARGETS
    }
    raw_rows = 0
    with args.raw.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            raw = json.loads(line)
            raw_rows += 1
            tx = raw.get("transaction") or {}
            top, inner = adapters.invoked_programs(tx)
            invoked = set(top) | set(inner)
            for target in sorted(TARGETS & invoked):
                counts[target] += 1
                signatures[target].append(str(raw.get("signature")))
                aggregate = aggregates[target]
                aggregate["program_family"][str(raw.get("program_family") or "")] += 1
                for program in top:
                    if program != target:
                        aggregate["top_cooccurrence"][program] += 1
                if target in top:
                    aggregate["target_position"]["TOP_LEVEL"] += 1
                if target in inner:
                    aggregate["target_position"]["CPI"] += 1
                for program in inner:
                    if program != target:
                        aggregate["inner_cooccurrence"][program] += 1
                for instruction in (tx.get("transaction") or {}).get("message", {}).get("instructions") or []:
                    index = instruction.get("programIdIndex")
                    program = instruction.get("programId")
                    keys = adapters.resolved_keys(tx)
                    if program is None and index is not None and int(index) < len(keys):
                        program = keys[int(index)]
                    if str(program) == target:
                        data = str(instruction.get("data") or "")
                        try:
                            raw_data = decode_base58(data)
                            data_shape = f"len={len(raw_data)} prefix={raw_data[:16].hex()}"
                        except Exception:
                            data_shape = f"unparsed:{data[:32]}"
                        aggregate["instruction_shape"][f"accounts={len(instruction.get('accounts') or [])}"] += 1
                        aggregate["data_shape"][data_shape] += 1
                for log in (tx.get("meta") or {}).get("logMessages") or []:
                    if target in str(log):
                        if " invoke " in str(log):
                            aggregate["log_shape"]["invoke"] += 1
                        elif " success" in str(log):
                            aggregate["log_shape"]["success"] += 1
                        elif "failed" in str(log).lower() or "error" in str(log).lower():
                            aggregate["log_shape"]["failure_or_error"] += 1
                        else:
                            aggregate["log_shape"]["other"] += 1
                wallet = str(raw.get("wallet") or "")
                if not wallet:
                    wallet = str(raw.get("tracked_wallet") or "")
                keys = adapters.resolved_keys(tx)
                aggregate["wallet_key_presence"]["PRESENT" if wallet in keys else "ABSENT"] += 1
                token_owner_present = any(
                    str(row.get("owner") or "") == wallet
                    for name in ("preTokenBalances", "postTokenBalances")
                    for row in (tx.get("meta") or {}).get(name) or []
                )
                aggregate["wallet_token_owner_presence"]["PRESENT" if token_owner_present else "ABSENT"] += 1
                wallet_index = keys.index(wallet) if wallet in keys else -1
                pre_balances = (tx.get("meta") or {}).get("preBalances") or []
                post_balances = (tx.get("meta") or {}).get("postBalances") or []
                if 0 <= wallet_index < len(pre_balances) and wallet_index < len(post_balances):
                    delta = int(post_balances[wallet_index]) - int(pre_balances[wallet_index])
                    sign = "POSITIVE" if delta > 0 else "NEGATIVE" if delta < 0 else "ZERO"
                    aggregate["wallet_lamport_delta"][sign] += 1
                token_deltas: dict[str, int] = {}
                for name, direction in (("preTokenBalances", -1), ("postTokenBalances", 1)):
                    for row in (tx.get("meta") or {}).get(name) or []:
                        if str(row.get("owner") or "") != wallet:
                            continue
                        mint = str(row.get("mint") or "")
                        amount = int(((row.get("uiTokenAmount") or {}).get("amount")) or 0)
                        token_deltas[mint] = token_deltas.get(mint, 0) + direction * amount
                token_sign = "POSITIVE" if any(value > 0 for value in token_deltas.values()) else "NEGATIVE" if any(value < 0 for value in token_deltas.values()) else "ZERO"
                aggregate["wallet_token_delta"][token_sign] += 1
                if len(examples[target]) < 12:
                    examples[target].append(tx_summary(raw, target))
    result = {
        "schema": "2CQ_UNKNOWN_PROGRAM_DIAGNOSTIC_V1",
        "read_only": True,
        "raw_rows": raw_rows,
        "input": str(args.raw),
        "input_sha256": hashlib.sha256(args.raw.read_bytes()).hexdigest(),
        "targets": {
            target: {
                "rows": counts[target],
                "unique_signatures": len(set(signatures[target])),
                "aggregates": {
                    name: dict(counter.most_common(30))
                    for name, counter in aggregates[target].items()
                },
                "example_rows": examples[target],
            }
            for target in sorted(TARGETS)
        },
        "venue_assignment": "NONE",
        "promotion_ready": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "DIAGNOSTIC_COMPLETE", "raw_rows": raw_rows, "counts": dict(counts)}, sort_keys=True))


if __name__ == "__main__":
    main()
