#!/usr/bin/env python3
"""Receive-only finalized transaction source for the canonical 13-arm program.

This is a research capture lane, not an execution dependency.  It polls the
canonical venue program addresses at finalized commitment, writes append-only
raw transaction evidence, and never assigns PnL, selects a policy, or submits
transactions.  Arm assignment remains a separate exact materialization step.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable


SCHEMA = "KEFI_13_ARM_FINALIZED_RPC_RAW_TX_V1"
STATE_SCHEMA = "KEFI_13_ARM_FINALIZED_RPC_SOURCE_STATE_V1"
WSOL = "So11111111111111111111111111111111111111112"

WATCHES = [
    {"program_id": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "family": "PUMP_PROGRAM", "arm_ids": ["ARM01_SOL_PUMP_CURVE_2CQ", "ARM02_SOL_PUMP_CPI_2CQ", "ARM03_ACTIVE_SOL_PUMP_5000MS"]},
    {"program_id": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "family": "PUMPSWAP_PROGRAM", "arm_ids": ["ARM07_PUMPSWAP_DIRECT", "ARM08_PUMPSWAP_CPI"]},
    {"program_id": "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj", "family": "RAYDIUM_LAUNCHLAB", "arm_ids": ["ARM05_RAYDIUM_LAUNCHLAB", "ARM06_STONKFUN_LAUNCHLAB"]},
    {"program_id": "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C", "family": "RAYDIUM_CPMM", "arm_ids": ["ARM12_OTHER_DEX_PROTOCOL_NATIVE"]},
    {"program_id": "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK", "family": "RAYDIUM_CLMM", "arm_ids": ["ARM09_ROUTER_RAYDIUM_CLMM", "ARM12_OTHER_DEX_PROTOCOL_NATIVE"]},
    {"program_id": "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", "family": "RAYDIUM_AMM_V4", "arm_ids": ["ARM12_OTHER_DEX_PROTOCOL_NATIVE"]},
    {"program_id": "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", "family": "METEORA_DLMM", "arm_ids": ["ARM11_ROUTER_METEORA_DLMM"]},
    {"program_id": "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN", "family": "METEORA_DBC", "arm_ids": ["ARM12_OTHER_DEX_PROTOCOL_NATIVE"]},
    {"program_id": "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc", "family": "ORCA_WHIRLPOOL", "arm_ids": ["ARM10_ROUTER_ORCA_WHIRLPOOL"]},
    {"program_id": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "family": "JUPITER_V6_ROUTER", "arm_ids": ["ARM09_ROUTER_RAYDIUM_CLMM", "ARM10_ROUTER_ORCA_WHIRLPOOL", "ARM11_ROUTER_METEORA_DLMM", "ARM12_OTHER_DEX_PROTOCOL_NATIVE"]},
    {"program_id": "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB", "family": "JUPITER_V4_ROUTER", "arm_ids": ["ARM09_ROUTER_RAYDIUM_CLMM", "ARM10_ROUTER_ORCA_WHIRLPOOL", "ARM11_ROUTER_METEORA_DLMM", "ARM12_OTHER_DEX_PROTOCOL_NATIVE"]},
]
WATCH_BY_PROGRAM = {row["program_id"]: row for row in WATCHES}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class RpcClient:
    def __init__(self, url: str, timeout: float = 20.0, opener: Callable[..., Any] | None = None):
        self.url = url
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen
        self.request_id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        self.request_id += 1
        payload = json.dumps({"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}).encode()
        request = urllib.request.Request(self.url, data=payload, headers={"content-type": "application/json"})
        with self.opener(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode())
        if result.get("error"):
            raise RuntimeError(f"RPC_{method}:{result['error']}")
        return result.get("result")


def load_state(path: Path, cohort_id: str, boundary: str) -> dict[str, Any]:
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema") != STATE_SCHEMA:
            raise RuntimeError("FAIL_CLOSED: invalid source state schema")
        if state.get("cohort_id") != cohort_id or state.get("boundary_utc") != boundary:
            raise RuntimeError("FAIL_CLOSED: source state cohort boundary mismatch")
        state.setdefault("seen_signatures", [])
        state.setdefault("last_seen_by_program", {})
        return state
    return {
        "schema": STATE_SCHEMA,
        "cohort_id": cohort_id,
        "boundary_utc": boundary,
        "created_at_utc": iso(utc_now()),
        "seen_signatures": [],
        "last_seen_by_program": {},
        "records_written": 0,
        "errors": 0,
    }


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def transaction_program_ids(transaction: dict[str, Any]) -> set[str]:
    """Return program IDs explicitly present in outer/inner instructions/logs."""
    found: set[str] = set()
    message = ((transaction.get("transaction") or {}).get("message") or {})
    for instruction in message.get("instructions") or []:
        if isinstance(instruction, dict):
            program_id = instruction.get("programId")
            if program_id:
                found.add(str(program_id))
            program = instruction.get("program")
            if isinstance(program, str) and program in WATCH_BY_PROGRAM:
                found.add(program)
    for group in (transaction.get("meta") or {}).get("innerInstructions") or []:
        for instruction in group.get("instructions") or []:
            if isinstance(instruction, dict):
                program_id = instruction.get("programId")
                if program_id:
                    found.add(str(program_id))
    for message_line in (transaction.get("meta") or {}).get("logMessages") or []:
        text = str(message_line)
        if text.startswith("Program ") and " invoke" in text:
            found.add(text.split("Program ", 1)[1].split(" invoke", 1)[0])
    return found


def transaction_account_keys(transaction: dict[str, Any]) -> set[str]:
    message = ((transaction.get("transaction") or {}).get("message") or {})
    keys: set[str] = set()
    for key in message.get("accountKeys") or []:
        if isinstance(key, str):
            keys.add(key)
        elif isinstance(key, dict) and key.get("pubkey"):
            keys.add(str(key["pubkey"]))
    loaded = (transaction.get("meta") or {}).get("loadedAddresses") or {}
    for key in (loaded.get("writable") or []) + (loaded.get("readonly") or []):
        keys.add(str(key))
    return keys


def run_once(client: RpcClient, state: dict[str, Any], output: Path, limit: int = 100, tracked_wallet: str | None = None) -> dict[str, int]:
    seen = set(state["seen_signatures"])
    written = 0
    fetched = 0
    errors = 0
    boundary = dt.datetime.fromisoformat(str(state["boundary_utc"]).replace("Z", "+00:00"))
    candidates: dict[str, dict[str, Any]] = {}
    query_targets: list[tuple[str, dict[str, Any] | None]] = (
        [(tracked_wallet, None)] if tracked_wallet else
        [(watch["program_id"], watch) for watch in WATCHES]
    )
    for query_address, query_watch in query_targets:
        signatures: list[dict[str, Any]] = []
        try:
            signatures = client.call("getSignaturesForAddress", [query_address, {"commitment": "finalized", "limit": limit}]) or []
            for summary in reversed(signatures):
                signature = str(summary.get("signature", ""))
                if not signature or signature in seen or summary.get("err") is not None:
                    continue
                block_time = summary.get("blockTime")
                if block_time is not None and dt.datetime.fromtimestamp(int(block_time), tz=dt.timezone.utc) < boundary:
                    seen.add(signature)
                    continue
                candidate = candidates.setdefault(signature, {"summary": summary, "watches": []})
                if query_watch:
                    candidate["watches"].append(query_watch)
        except Exception:
            errors += 1
        if signatures:
            state["last_seen_by_program"][query_address] = str(signatures[0].get("signature", ""))
    for signature, candidate in candidates.items():
        try:
            transaction = client.call("getTransaction", [signature, {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 1}])
            fetched += 1
            seen.add(signature)
            if not transaction or transaction.get("meta", {}).get("err") is not None:
                continue
            if tracked_wallet and tracked_wallet not in transaction_account_keys(transaction):
                continue
            watched_ids = {watch["program_id"] for watch in candidate["watches"]}
            watched_ids.update(transaction_program_ids(transaction) & set(WATCH_BY_PROGRAM))
            observed_watches = [WATCH_BY_PROGRAM[program_id] for program_id in sorted(watched_ids)]
            arm_ids = sorted({arm_id for watch in observed_watches for arm_id in watch["arm_ids"]})
            if not observed_watches:
                continue
            captured = iso(utc_now())
            record = {
                "schema": SCHEMA,
                "cohort_id": state["cohort_id"],
                "captured_at_utc": captured,
                "boundary_utc": state["boundary_utc"],
                "finalized": True,
                "tracked_wallet": tracked_wallet,
                "program_id": observed_watches[0]["program_id"],
                "program_family": observed_watches[0]["family"],
                "observed_program_ids": sorted(watched_ids),
                "observed_program_families": [watch["family"] for watch in observed_watches],
                "candidate_arm_ids": arm_ids,
                "signature": signature,
                "slot": int(transaction.get("slot", candidate["summary"].get("slot", -1))),
                "block_time": candidate["summary"].get("blockTime"),
                "transaction_sha256": sha256_bytes(json.dumps(transaction, sort_keys=True, separators=(",", ":")).encode()),
                "transaction": transaction,
            }
            append_record(output, record)
            state["records_written"] += 1
            written += 1
        except Exception:
            errors += 1
    state["seen_signatures"] = list(seen)[-20_000:]
    state["errors"] += errors
    return {"fetched": fetched, "written": written, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-url", default=os.environ.get("SECURE_RPC_URL") or os.environ.get("SOLANA_RPC_URL"))
    parser.add_argument("--tracked-wallet", default=os.environ.get("TRACKED_WALLET"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--boundary-utc", required=True)
    parser.add_argument("--interval-seconds", type=float, default=10.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.rpc_url:
        raise SystemExit("FAIL_CLOSED: --rpc-url or SECURE_RPC_URL required")
    if not args.tracked_wallet:
        raise SystemExit("FAIL_CLOSED: --tracked-wallet or TRACKED_WALLET required")
    manifest = {
        "schema": "KEFI_13_ARM_FINALIZED_RPC_SOURCE_MANIFEST_V1",
        "cohort_id": args.cohort_id,
        "boundary_utc": args.boundary_utc,
        "collection_mode": "RECEIVE_ONLY_FINALIZED_RPC",
        "live_trading_enabled": False,
        "jupiter_fallback_enabled": False,
        "tracked_wallet": args.tracked_wallet,
        "watches": WATCHES,
    }
    atomic_json(args.manifest, manifest)
    state = load_state(args.state, args.cohort_id, args.boundary_utc)
    client = RpcClient(args.rpc_url)
    while True:
        result = run_once(client, state, args.output, tracked_wallet=args.tracked_wallet)
        atomic_json(args.state, state)
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
