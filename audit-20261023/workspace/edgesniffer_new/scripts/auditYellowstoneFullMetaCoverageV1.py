#!/usr/bin/env python3
"""Outcome-blind, bounded canonical coverage check for the full-meta lane."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
MAGIC = b"PEL1FRM\0"
CREATE = bytes([24, 30, 200, 40, 5, 28, 7, 119])
CREATE_V2 = bytes([214, 144, 76, 236, 95, 139, 49, 180])
BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def read_env(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"{key} not found in {path}")


def scan_records(root: Path):
    by_slot: dict[int, set[str]] = defaultdict(set)
    meta_by_signature: dict[str, dict] = {}
    for path in root.glob("partitions/*/*/*/*/evidence.frames*"):
        if path.name.endswith(".zst"):
            continue
        data = path.read_bytes()
        if not data.startswith(MAGIC):
            continue
        cursor = len(MAGIC)
        while cursor + 8 <= len(data):
            meta_len, raw_len = struct.unpack_from("<II", data, cursor)
            end = cursor + 8 + meta_len + raw_len + 32
            if end > len(data):
                break
            body_start = cursor + 8
            body = data[body_start : body_start + meta_len + raw_len]
            digest = data[body_start + meta_len + raw_len : end]
            if hashlib.sha256(body).digest() != digest:
                break
            metadata = json.loads(data[body_start : body_start + meta_len])
            slot = metadata.get("slot")
            signature = metadata.get("signature")
            if slot is not None and signature:
                by_slot[int(slot)].add(signature)
                meta_by_signature[signature] = metadata
            cursor = end
    return by_slot, meta_by_signature


def rpc(url: str, method: str, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=50) as response:
        value = json.load(response)
    if value.get("error"):
        raise RuntimeError(value["error"])
    return value.get("result")


def b58decode(value: str) -> bytes:
    number = 0
    for character in value:
        number = number * 58 + BASE58.index(character)
    encoded = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return b"\0" * (len(value) - len(value.lstrip("1"))) + encoded


def pump_shape(transaction: dict) -> str | None:
    meta = transaction.get("meta") or {}
    message = (transaction.get("transaction") or {}).get("message") or {}
    keys = message.get("accountKeys") or []
    normalized = [key.get("pubkey") if isinstance(key, dict) else key for key in keys]
    loaded = meta.get("loadedAddresses") or {}
    normalized.extend(loaded.get("writable") or [])
    normalized.extend(loaded.get("readonly") or [])
    def matches(instruction):
        index = instruction.get("programIdIndex")
        return isinstance(index, int) and index < len(normalized) and normalized[index] == PUMP
    outer = [item for item in message.get("instructions") or [] if matches(item)]
    inner = [item for group in meta.get("innerInstructions") or [] for item in group.get("instructions") or [] if matches(item)]
    instructions = outer + inner
    if not instructions:
        return None
    is_create = any(
        isinstance(item.get("data"), str) and b58decode(item["data"])[:8] in {CREATE, CREATE_V2}
        for item in instructions
    )
    if is_create:
        return "CREATE"
    return "DIRECT_PUMP" if outer else "PUMP_CPI"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--env", default="/root/bot/EdgeSniffer/.env", type=Path)
    parser.add_argument("--slots", type=int, default=32)
    parser.add_argument("--lag-slots", type=int, default=700)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    health = json.loads((args.root / "health.json").read_text())
    latest = int(health["latestSlot"])
    upper = latest - args.lag_slots
    lower = upper - args.slots + 1
    observed, metadata = scan_records(args.root)
    rpc_url = read_env(args.env, "SECURE_RPC_URL")
    canonical: set[str] = set()
    canonical_by_shape: dict[str, set[str]] = defaultdict(set)
    rpc_errors = []
    for slot in range(lower, upper + 1):
        try:
            block = rpc(rpc_url, "getBlock", [slot, {
                "commitment": "finalized", "encoding": "json", "transactionDetails": "full",
                "rewards": False, "maxSupportedTransactionVersion": 1,
            }])
        except Exception as error:  # Keep the audit honest when RPC does not serve a slot.
            rpc_errors.append({"slot": slot, "error": str(error)})
            continue
        if not block:
            rpc_errors.append({"slot": slot, "error": "null_block"})
            continue
        for transaction in block.get("transactions") or []:
            shape = pump_shape(transaction) if (transaction.get("meta") or {}).get("err") is None else None
            if shape:
                signatures = (transaction.get("transaction") or {}).get("signatures") or []
                if signatures:
                    canonical.add(signatures[0])
                    canonical_by_shape[shape].add(signatures[0])
        time.sleep(0.03)
    arrivals = set().union(*(observed.get(slot, set()) for slot in range(lower, upper + 1)))
    matches = canonical & arrivals
    misses = sorted(canonical - arrivals)
    extra = sorted(arrivals - canonical)
    meta_complete = sum(
        1 for signature in matches if not metadata[signature].get("missingReserveTransition", True)
    )
    coverage_by_shape = {
        shape: {
            "canonical": len(signatures),
            "matched": len(signatures & arrivals),
            "coveragePercent": round(100 * len(signatures & arrivals) / len(signatures), 6) if signatures else None,
        }
        for shape, signatures in sorted(canonical_by_shape.items())
    }
    report = {
        "schema": "YELLOWSTONE_FULL_META_CANONICAL_COVERAGE_AUDIT_V1",
        "windowSlots": {"start": lower, "end": upper, "count": args.slots, "lagSlots": args.lag_slots},
        "canonicalSuccessfulPump": len(canonical),
        "fullMetaRealtimeArrivals": len(arrivals),
        "matched": len(matches),
        "coveragePercent": round((100 * len(matches) / len(canonical)), 6) if canonical else None,
        "matchedMetaComplete": meta_complete,
        "coverageByShape": coverage_by_shape,
        "misses": misses,
        "arrivalsNotCanonicalPump": len(extra),
        "rpcErrors": rpc_errors,
        "sourceHealth": health,
        "outcomeBlind": True,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if not rpc_errors else 2


if __name__ == "__main__":
    sys.exit(main())
