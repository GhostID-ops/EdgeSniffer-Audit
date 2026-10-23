#!/usr/bin/env python3
"""Outcome-blind first-stage materializer for the isolated full-meta lane."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"


def rpc(url: str, method: str, params):
    request = urllib.request.Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload.get("result")


def read_env(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"{key} missing")


def account_keys(transaction: dict) -> list[str]:
    keys = (transaction.get("transaction") or {}).get("message", {}).get("accountKeys") or []
    result = [entry.get("pubkey") if isinstance(entry, dict) else entry for entry in keys]
    meta = transaction.get("meta") or {}
    loaded = meta.get("loadedAddresses") or {}
    result.extend(loaded.get("writable") or [])
    result.extend(loaded.get("readonly") or [])
    return result


def invokes_pump(transaction: dict) -> bool:
    keys = account_keys(transaction)
    message = (transaction.get("transaction") or {}).get("message") or {}
    meta = transaction.get("meta") or {}
    def is_pump(ix: dict) -> bool:
        index = ix.get("programIdIndex")
        return isinstance(index, int) and index < len(keys) and keys[index] == PUMP
    return any(is_pump(ix) for ix in message.get("instructions") or []) or any(
        is_pump(ix)
        for group in meta.get("innerInstructions") or []
        for ix in group.get("instructions") or []
    )


def acquired_mints(transaction: dict) -> list[str]:
    meta = transaction.get("meta") or {}
    before: dict[tuple[int, str], int] = {}
    after: dict[tuple[int, str], int] = {}
    for row in meta.get("preTokenBalances") or []:
        if row.get("owner") == WALLET:
            before[(int(row.get("accountIndex", -1)), str(row.get("mint")))] = int((row.get("uiTokenAmount") or {}).get("amount") or 0)
    for row in meta.get("postTokenBalances") or []:
        if row.get("owner") == WALLET:
            after[(int(row.get("accountIndex", -1)), str(row.get("mint")))] = int((row.get("uiTokenAmount") or {}).get("amount") or 0)
    deltas: dict[str, int] = defaultdict(int)
    for key in set(before) | set(after):
        deltas[key[1]] += after.get(key, 0) - before.get(key, 0)
    return sorted(mint for mint, delta in deltas.items() if delta > 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--env", type=Path, default=Path("/root/bot/EdgeSniffer/.env"))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--incremental", action="store_true", help="Only fetch new or retryable canonical candidates.")
    args = parser.parse_args()
    boundary = json.loads((args.root / "cohort-boundary.json").read_text(encoding="utf-8"))
    boundary_time = str(boundary["boundaryTimeUtc"])
    candidates = [
        json.loads(line)
        for line in (args.root / "2cq-wallet-pump-candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    candidates = [row for row in candidates if str(row["receivedAtUtc"]) > boundary_time]
    by_signature = {str(row["signature"]): row for row in candidates}
    previous_rows: dict[str, dict] = {}
    if args.incremental and args.report.exists():
        previous = json.loads(args.report.read_text(encoding="utf-8"))
        previous_rows = {str(row["signature"]): row for row in previous.get("rows") or []}
    rpc_url = read_env(args.env, "SECURE_RPC_URL")
    rows = []
    retryable_stages = {"CANONICAL_FINALITY_PENDING"}
    retryable_reasons = {"FINALITY_NOT_AVAILABLE", "CANONICAL_FETCH_RETRY:TimeoutError", "CANONICAL_FETCH_RETRY:URLError"}
    for signature, candidate in sorted(by_signature.items(), key=lambda item: item[1]["receivedAtUnixNs"]):
        prior = previous_rows.get(signature)
        if prior and prior.get("stage") not in retryable_stages and prior.get("reason") not in retryable_reasons:
            rows.append(prior)
            continue
        row = {
            "signature": signature,
            "receivedAtUtc": candidate["receivedAtUtc"],
            "receivedAtUnixNs": candidate["receivedAtUnixNs"],
            "observedSlot": candidate["observedSlot"],
            "continuityEpoch": candidate["continuityEpoch"],
            "realtimeAcquisitionProvenance": True,
            "stage": "CANONICAL_FINALITY_PENDING",
            "reason": None,
            "outcomesRead": 0,
        }
        try:
            transaction = rpc(rpc_url, "getTransaction", [signature, {
                "commitment": "finalized", "encoding": "json", "maxSupportedTransactionVersion": 1,
            }])
        except Exception as error:
            row["reason"] = f"CANONICAL_FETCH_RETRY:{type(error).__name__}"
            rows.append(row)
            continue
        if not transaction:
            row["reason"] = "FINALITY_NOT_AVAILABLE"
            rows.append(row)
            continue
        if int(transaction.get("slot") or 0) != int(candidate["observedSlot"]):
            row["reason"] = "CANONICAL_SLOT_MISMATCH"
            rows.append(row)
            continue
        if (transaction.get("meta") or {}).get("err") is not None:
            row.update(stage="CANONICAL_FINALIZED_NON_SUCCESS", reason="CANONICAL_TRANSACTION_FAILED")
            rows.append(row)
            continue
        if not invokes_pump(transaction):
            row.update(stage="CANONICAL_FINALIZED_NON_PUMP", reason="NO_SUCCESSFUL_PUMP_INVOCATION")
            rows.append(row)
            continue
        mints = acquired_mints(transaction)
        if not mints:
            row.update(stage="CANONICAL_FINALIZED_NON_ACQUISITION", reason="NO_POSITIVE_2CQ_TOKEN_DELTA")
            rows.append(row)
            continue
        row.update(
            stage="CANONICAL_SUCCESSFUL_PUMP_ACQUISITION_PENDING_FULL_WINDOW",
            reason="AWAITING_120S_FULL_META_WINDOW_AND_EXACT_STATE",
            acquiredMints=mints,
            canonicalSlot=int(transaction["slot"]),
        )
        rows.append(row)
        time.sleep(0.02)
    rows.sort(key=lambda row: int(row.get("receivedAtUnixNs") or 0))
    summary = Counter(row["stage"] for row in rows)
    report = {
        "schema": "2CQ_FULL_META_PROSPECTIVE_FUNNEL_V1",
        "boundaryTimeUtc": boundary_time,
        "candidateCount": len(rows),
        "stages": dict(sorted(summary.items())),
        "rows": rows,
        "outcomeBlind": True,
        "submissions": 0,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
