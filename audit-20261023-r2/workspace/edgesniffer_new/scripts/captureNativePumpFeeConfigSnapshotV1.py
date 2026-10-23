#!/usr/bin/env python3
"""Capture the canonical Pump fee-config account as separate read-only evidence.

Fee configuration is protocol state, not native transaction timing. This tool
therefore writes an explicitly non-causal configuration snapshot and never
labels it as a native receive-time observation.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def env_value(path: Path, name: str) -> str | None:
    if not path.exists():
        return os.environ.get(name)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    return os.environ.get(name)


def discover_accounts(source: Path) -> set[str]:
    found: set[str] = set()
    for line in source.open(encoding="utf-8"):
        if not line.strip():
            continue
        row = json.loads(line)
        tx = row.get("transaction") or {}
        message = (tx.get("transaction") or {}).get("message") or {}
        for instruction in message.get("instructions") or []:
            if instruction.get("programId") != PUMP_PROGRAM_ID:
                continue
            accounts = instruction.get("accounts") or []
            for index in (12, 14, 21, 22):
                if len(accounts) > index and isinstance(accounts[index], str):
                    found.add(accounts[index])
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rpc = env_value(args.env_file, "SECURE_RPC_URL")
    if not rpc:
        raise SystemExit("SECURE_RPC_URL is missing")
    addresses = sorted(discover_accounts(args.source))
    request = {"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts",
               "params": [addresses, {"encoding": "base64", "commitment": "processed"}]}
    body = json.dumps(request, separators=(",", ":")).encode()
    req = urllib.request.Request(rpc, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.load(response)
    context_slot = int(((result.get("result") or {}).get("context") or {}).get("slot") or 0)
    values = ((result.get("result") or {}).get("value") or [])
    captured_ms = int(time.time() * 1000)
    rows = []
    for address, account in zip(addresses, values):
        if not account or not account.get("data"):
            continue
        raw = base64.b64decode(account["data"][0])
        if raw[:8] != bytes([143, 52, 146, 187, 219, 123, 76, 155]):
            continue
        rows.append({
            "schema": "NATIVE_PUMP_FEE_CONFIG_SNAPSHOT_V1",
            "feeConfig": address,
            "owner": account.get("owner"),
            "accountDataBase64": base64.b64encode(raw).decode(),
            "accountDataSha256": hashlib.sha256(raw).hexdigest(),
            "capturedAtMs": captured_ms,
            "rpcContextSlot": context_slot,
            "source": "RPC_ACCOUNT_SNAPSHOT_NONCAUSAL_STATIC_CONFIG",
            "readOnly": True,
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"discovered_accounts": len(addresses), "fee_config_rows": len(rows),
                      "rpc_context_slot": context_slot, "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
