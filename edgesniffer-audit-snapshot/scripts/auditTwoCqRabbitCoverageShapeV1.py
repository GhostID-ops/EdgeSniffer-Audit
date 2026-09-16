#!/usr/bin/env python3
"""Compare transaction shape for exact-ledger hits versus finalized-success misses."""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import pathlib
import urllib.request


PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"


def env_value(path: pathlib.Path, name: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError(f"missing {name}")


def transaction(endpoint: str, signature: str) -> dict | None:
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
        "params": [signature, {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 0}],
    }
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        row = json.load(response)
    return row.get("result")


def key_value(row):
    return row.get("pubkey") if isinstance(row, dict) else row


def shape(transaction: dict | None) -> str:
    if transaction is None:
        return "RPC_TRANSACTION_UNAVAILABLE"
    message = transaction["transaction"]["message"]
    static = [key_value(row) for row in message.get("accountKeys") or []]
    loaded = transaction.get("meta", {}).get("loadedAddresses") or {}
    writable = list(loaded.get("writable") or [])
    readonly = list(loaded.get("readonly") or [])
    keys = static + writable + readonly
    if PUMP_PROGRAM in static:
        pump_location = "PUMP_STATIC_KEY"
    elif PUMP_PROGRAM in writable or PUMP_PROGRAM in readonly:
        pump_location = "PUMP_LOADED_ADDRESS"
    else:
        pump_location = "PUMP_KEY_ABSENT"
    if WALLET in static:
        wallet_location = "WALLET_STATIC_KEY"
    elif WALLET in writable or WALLET in readonly:
        wallet_location = "WALLET_LOADED_ADDRESS"
    else:
        wallet_location = "WALLET_KEY_ABSENT"
    outer = any(
        int(row.get("programIdIndex", -1)) < len(keys)
        and keys[int(row["programIdIndex"])] == PUMP_PROGRAM
        for row in message.get("instructions") or []
    )
    inner = any(
        int(row.get("programIdIndex", -1)) < len(keys)
        and keys[int(row["programIdIndex"])] == PUMP_PROGRAM
        for group in transaction.get("meta", {}).get("innerInstructions") or []
        for row in group.get("instructions") or []
    )
    invocation = "PUMP_OUTER" if outer else "PUMP_INNER_ONLY" if inner else "PUMP_NO_COMPILED_INVOCATION"
    version = transaction.get("version")
    return f"{pump_location}|{wallet_location}|{invocation}|VERSION_{version}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=pathlib.Path, required=True)
    parser.add_argument("--missing", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    missing = json.loads(args.missing.read_text(encoding="utf-8"))
    groups = {
        "EXACT_LEDGER_SUCCESS_HIT": [
            row["signature"] for row in audit["rows"]
            if row["classification"] == "FINALIZED_SUCCESSFUL_EXACT_BUY"
        ],
        "EXACT_LEDGER_SUCCESS_MISS": [
            row["signature"] for row in missing["rows"]
            if row["statusClassification"] == "CHAIN_STATUS_FINALIZED_SUCCESS_EXACT_LEDGER_MISS"
        ],
    }
    endpoint = env_value(args.env, "SECURE_RPC_URL")
    signatures = groups["EXACT_LEDGER_SUCCESS_HIT"] + groups["EXACT_LEDGER_SUCCESS_MISS"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        fetched = dict(zip(signatures, pool.map(lambda signature: transaction(endpoint, signature), signatures)))
    report_groups = {}
    for name, values in groups.items():
        counts = collections.Counter(shape(fetched.get(signature)) for signature in values)
        report_groups[name] = {"N": len(values), "shapeCounts": dict(sorted(counts.items()))}
    report = {
        "schema": "2CQ_PROSPECTIVE_COLLECTION_RELIABILITY_REPAIR_V1_RABBIT_COVERAGE_SHAPE",
        "groups": report_groups,
        "outcomesRead": 0,
        "pnlRead": 0,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
