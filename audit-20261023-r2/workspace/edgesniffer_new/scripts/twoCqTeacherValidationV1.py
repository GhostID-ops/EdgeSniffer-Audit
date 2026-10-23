#!/usr/bin/env python3
"""Clean-sheet, wallet-authoritative validation of the tracked 2CQ teacher.

This tool deliberately does not read any prior alpha model, ranker, fingerprint,
or shadow outcome.  It reconstructs wallet position episodes from finalized
on-chain balance changes and uses existing Pump evidence only for descriptive
entry-state enrichment.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pathlib
import statistics
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Iterable

from exactCurveEconomicsV1 import decode_trade_events


SCHEMA = "2CQ_TEACHER_VALIDATION_V1"
WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"
PUMP_CURVE = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
WSOL = "So11111111111111111111111111111111111111112"
SYSTEM = "11111111111111111111111111111111"
TOKEN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}
LAMPORTS = 1_000_000_000
PRIMARY_DAYS = 7
EXTENDED_DAYS = 30
MIN_PRIMARY_EPISODES = 500
CONTEXT_DAYS = 2
EFFECTIVE_ZERO_FRACTION = 1_000_000_000  # <= one part per billion acquired


def utc_iso(seconds: int | float | None) -> str | None:
    if seconds is None:
        return None
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile(values: Iterable[float], q: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def median(values: Iterable[float]) -> float | None:
    values = list(values)
    return statistics.median(values) if values else None


def mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return statistics.fmean(values) if values else None


def load_env(path: pathlib.Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def request_json(url: str, payload: dict[str, Any] | None = None, retries: int = 8) -> Any:
    body = canonical(payload) if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except Exception as error:
            last_error = error
            time.sleep(min(12.0, 0.5 * (2**attempt)))
    raise RuntimeError(f"request failed after {retries} attempts: {last_error}")


def helius_keys(env: dict[str, str]) -> list[str]:
    keys = [value.strip() for value in env.get("HELIUS_API_KEYS", "").split(",") if value.strip()]
    single = env.get("HELIUS_API_KEY")
    if single and single not in keys:
        keys.append(single)
    if not keys:
        raise RuntimeError("no Helius API keys available")
    return keys


def helius_rpc(keys: list[str], method: str, params: list[Any], cursor: int = 0) -> Any:
    last_error: Exception | None = None
    for attempt in range(16):
        key = keys[(cursor + attempt) % len(keys)]
        url = f"https://mainnet.helius-rpc.com/?api-key={key}"
        try:
            response = request_json(
                url,
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                retries=1,
            )
            if "error" not in response:
                return response.get("result")
            last_error = RuntimeError(str(response["error"]))
        except Exception as error:
            last_error = error
        time.sleep(min(8.0, 0.35 + attempt * 0.25))
    raise RuntimeError(f"Helius RPC {method} failed: {last_error}")


def fetch_signature_ledger(
    keys: list[str],
    oldest_seconds: int,
    output: pathlib.Path,
    max_pages: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    before: str | None = None
    for page in range(max_pages):
        config: dict[str, Any] = {"limit": 1000, "commitment": "finalized"}
        if before:
            config["before"] = before
        batch = helius_rpc(keys, "getSignaturesForAddress", [WALLET, config], page) or []
        if not batch:
            break
        rows.extend(batch)
        before = str(batch[-1]["signature"])
        oldest = int(batch[-1].get("blockTime") or 0)
        if (page + 1) % 25 == 0:
            print(json.dumps({"signaturePages": page + 1, "rows": len(rows), "oldestUtc": utc_iso(oldest)}), flush=True)
        if len(batch) < 1000 or (oldest and oldest <= oldest_seconds):
            break
        time.sleep(0.20)
    else:
        raise RuntimeError(f"signature pagination cap {max_pages} reached before frozen boundary")
    deduped = {str(row["signature"]): row for row in rows if row.get("signature")}
    ordered = sorted(
        deduped.values(),
        key=lambda row: (int(row.get("blockTime") or 0), str(row["signature"])),
    )
    if not ordered or int(ordered[0].get("blockTime") or 0) > oldest_seconds:
        raise RuntimeError("signature ledger did not reach frozen context boundary")
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8") as handle:
        for row in ordered:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    return ordered


def read_jsonl_gz(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def fetch_enhanced_batch(keys: list[str], signatures: list[str], cursor: int) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(16):
        key = keys[(cursor + attempt) % len(keys)]
        url = f"https://api-mainnet.helius-rpc.com/v0/transactions?api-key={key}"
        try:
            result = request_json(url, {"transactions": signatures}, retries=1)
            if isinstance(result, list):
                return result
            last_error = RuntimeError(f"unexpected enhanced batch payload {type(result)}")
        except Exception as error:
            last_error = error
        time.sleep(min(10.0, 0.5 + attempt * 0.3))
    raise RuntimeError(f"enhanced transaction batch failed: {last_error}")


def fetch_enhanced_transactions(
    keys: list[str],
    signature_rows: list[dict[str, Any]],
    output: pathlib.Path,
) -> list[dict[str, Any]]:
    partial = output.with_suffix(output.suffix + ".partial.jsonl")
    found: dict[str, dict[str, Any]] = {}
    if output.exists():
        return read_jsonl_gz(output)
    if partial.exists():
        with partial.open(errors="ignore") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("signature"):
                    found[str(row["signature"])] = row
    wanted = [str(row["signature"]) for row in signature_rows]
    missing = [signature for signature in wanted if signature not in found]
    print(json.dumps({"enhancedAlreadyCached": len(found), "enhancedMissing": len(missing)}), flush=True)
    with partial.open("a", encoding="utf-8") as handle:
        for offset in range(0, len(missing), 100):
            signatures = missing[offset : offset + 100]
            batch = fetch_enhanced_batch(keys, signatures, offset // 100)
            returned = {str(row["signature"]): row for row in batch if row.get("signature")}
            for signature in signatures:
                row = returned.get(signature)
                if row is None:
                    row = {"signature": signature, "_enhancedUnavailable": True}
                found[signature] = row
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            handle.flush()
            if (offset // 100 + 1) % 25 == 0:
                print(json.dumps({"enhancedBatches": offset // 100 + 1, "cached": len(found), "remaining": len(missing) - offset - len(signatures)}), flush=True)
            time.sleep(0.10)
    ordered = [found[signature] for signature in wanted if signature in found]
    with gzip.open(output, "wt", encoding="utf-8") as handle:
        for row in ordered:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    partial.unlink()
    return ordered


def helius_rpc_batch(keys: list[str], calls: list[dict[str, Any]], cursor: int) -> list[Any]:
    last_error: Exception | None = None
    for attempt in range(16):
        key = keys[(cursor + attempt) % len(keys)]
        url = f"https://mainnet.helius-rpc.com/?api-key={key}"
        try:
            response = request_json(url, calls, retries=1)
            if isinstance(response, list):
                by_id = {int(row.get("id")): row for row in response if isinstance(row, dict)}
                return [by_id.get(int(call["id"]), {}).get("result") for call in calls]
            last_error = RuntimeError(f"unexpected RPC batch payload {type(response)}")
        except Exception as error:
            last_error = error
        time.sleep(min(8.0, 0.35 + attempt * 0.25))
    raise RuntimeError(f"Helius RPC batch failed: {last_error}")


def rpc_batch_url(url: str, calls: list[dict[str, Any]]) -> list[Any]:
    last_error: Exception | None = None
    for attempt in range(10):
        try:
            response = request_json(url, calls, retries=1)
            if isinstance(response, list):
                by_id = {int(row.get("id")): row for row in response if isinstance(row, dict)}
                return [by_id.get(int(call["id"]), {}).get("result") for call in calls]
            last_error = RuntimeError(f"unexpected RPC batch payload {type(response)}")
        except Exception as error:
            last_error = error
        time.sleep(min(8.0, 0.5 * (attempt + 1)))
    raise RuntimeError(f"configured RPC batch failed: {last_error}")


def fetch_raw_transactions(
    keys: list[str], signatures: list[str], output: pathlib.Path, rpc_url: str | None = None
) -> dict[str, dict[str, Any] | None]:
    if output.exists():
        rows = read_jsonl_gz(output)
        return {str(row["signature"]): row.get("transaction") for row in rows}
    result: dict[str, dict[str, Any] | None] = {}
    unique = list(dict.fromkeys(signature for signature in signatures if signature))
    if rpc_url:
        def fetch_one(item: tuple[int, str]) -> tuple[str, dict[str, Any] | None]:
            index, signature = item
            call = {
                "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                "params": [signature, {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 0}],
            }
            return signature, rpc_batch_url(rpc_url, [call])[0]

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            for completed, pair in enumerate(executor.map(fetch_one, enumerate(unique)), 1):
                signature, tx = pair
                result[signature] = tx
                if completed % 500 == 0:
                    print(json.dumps({"rawEntryTransactions": completed, "total": len(unique)}), flush=True)
    batch_size = 100
    if not rpc_url:
        for offset in range(0, len(unique), batch_size):
            batch = unique[offset : offset + batch_size]
            calls = [
                {
                    "jsonrpc": "2.0",
                    "id": index + 1,
                    "method": "getTransaction",
                    "params": [signature, {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 0}],
                }
                for index, signature in enumerate(batch)
            ]
            values = helius_rpc_batch(keys, calls, offset // batch_size)
            result.update(zip(batch, values))
    with gzip.open(output, "wt", encoding="utf-8") as handle:
        for signature in unique:
            handle.write(json.dumps({"signature": signature, "transaction": result.get(signature)}, separators=(",", ":")) + "\n")
    return result


def rpc(url: str, method: str, params: list[Any]) -> Any:
    response = request_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in response:
        raise RuntimeError(f"RPC {method}: {response['error']}")
    return response.get("result")


def fetch_enhanced_history(
    api_key: str,
    oldest_seconds: int,
    output: pathlib.Path,
    max_pages: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    before: str | None = None
    for page in range(max_pages):
        query = {"api-key": api_key, "limit": "100"}
        if before:
            query["before"] = before
        url = (
            f"https://api-mainnet.helius-rpc.com/v0/addresses/{WALLET}/transactions?"
            + urllib.parse.urlencode(query)
        )
        batch = request_json(url)
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        before = str(batch[-1].get("signature") or "")
        oldest = min(int(row.get("timestamp") or 0) for row in batch)
        if (page + 1) % 25 == 0:
            print(json.dumps({"historyPages": page + 1, "rows": len(rows), "oldestUtc": utc_iso(oldest)}), flush=True)
        if oldest <= oldest_seconds or len(batch) < 100:
            break
        time.sleep(0.08)
    else:
        raise RuntimeError(f"history pagination cap {max_pages} reached before frozen boundary")

    deduped = {str(row["signature"]): row for row in rows if row.get("signature")}
    ordered = sorted(
        deduped.values(),
        key=lambda row: (int(row.get("timestamp") or 0), int(row.get("slot") or 0), str(row["signature"])),
    )
    if not ordered or int(ordered[0].get("timestamp") or 0) > oldest_seconds:
        raise RuntimeError("enhanced history did not reach the frozen context boundary")
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8") as handle:
        for row in ordered:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    return ordered


def read_history(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def recursive_program_ids(instructions: Iterable[Any]) -> set[str]:
    output: set[str] = set()
    stack = list(instructions or [])
    while stack:
        instruction = stack.pop()
        if not isinstance(instruction, dict):
            continue
        if instruction.get("programId"):
            output.add(str(instruction["programId"]))
        stack.extend(instruction.get("innerInstructions") or [])
    return output


def wallet_token_deltas(tx: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    amounts: dict[str, int] = collections.defaultdict(int)
    decimals: dict[str, int] = {}
    for account in tx.get("accountData") or []:
        for change in account.get("tokenBalanceChanges") or []:
            if change.get("userAccount") != WALLET:
                continue
            mint = str(change.get("mint") or "")
            raw = change.get("rawTokenAmount") or {}
            if not mint or raw.get("tokenAmount") is None:
                continue
            amounts[mint] += int(raw["tokenAmount"])
            decimals[mint] = int(raw.get("decimals") or 0)
    return dict(amounts), decimals


def wallet_native_delta(tx: dict[str, Any]) -> int:
    return sum(
        int(account.get("nativeBalanceChange") or 0)
        for account in tx.get("accountData") or []
        if account.get("account") == WALLET
    )


def explicit_native_flows(tx: dict[str, Any]) -> tuple[int, int, list[str]]:
    sent = 0
    received = 0
    counterparties: list[str] = []
    for transfer in tx.get("nativeTransfers") or []:
        amount = int(transfer.get("amount") or 0)
        if transfer.get("fromUserAccount") == WALLET:
            sent += amount
            if transfer.get("toUserAccount"):
                counterparties.append(str(transfer["toUserAccount"]))
        if transfer.get("toUserAccount") == WALLET:
            received += amount
            if transfer.get("fromUserAccount"):
                counterparties.append(str(transfer["fromUserAccount"]))
    return sent, received, counterparties


def venue_category(tx: dict[str, Any], programs: set[str], has_token_delta: bool) -> str:
    tx_type = str(tx.get("type") or "UNKNOWN")
    source = str(tx.get("source") or "UNKNOWN")
    if PUMPSWAP in programs or source in {"PUMP_AMM", "PUMPSWAP"}:
        return "PUMPSWAP"
    if PUMP_CURVE in programs:
        return "PUMP_BONDING_CURVE"
    if source == "PUMP_FUN" and tx_type == "SWAP":
        return "PUMP_UNRESOLVED_ROUTE"
    if tx_type == "SWAP":
        return f"OTHER_DEX_OR_AGGREGATOR:{source}"
    if has_token_delta or tx.get("tokenTransfers"):
        return "PLAIN_SPL_TRANSFER"
    if tx.get("nativeTransfers"):
        return "SOL_TRANSFER"
    return "UNRELATED"


def transaction_facts(tx: dict[str, Any]) -> dict[str, Any]:
    evidence_available = not bool(tx.get("_enhancedUnavailable"))
    chain_failed = tx.get("_signatureError") is not None or tx.get("transactionError") is not None
    token_deltas, decimals = wallet_token_deltas(tx)
    wsol_delta = int(token_deltas.pop(WSOL, 0))
    programs = recursive_program_ids(tx.get("instructions") or [])
    native_delta = wallet_native_delta(tx)
    sent, received, counterparties = explicit_native_flows(tx)
    category = venue_category(tx, programs, bool(token_deltas))
    attempted = {
        "tokenDeltas": token_deltas,
        "wsolDeltaLamports": wsol_delta,
        "walletNativeDeltaLamports": native_delta,
        "explicitNativeSentLamports": sent,
        "explicitNativeReceivedLamports": received,
    }
    if chain_failed:
        token_deltas = {}
        wsol_delta = 0
        native_delta = -int(tx.get("fee") or 0) if tx.get("feePayer") == WALLET else 0
        sent = 0
        received = 0
        counterparties = []
    quote_delta = native_delta + wsol_delta
    return {
        "signature": str(tx.get("signature") or ""),
        "timestamp": int(tx.get("timestamp") or 0),
        "utc": utc_iso(int(tx.get("timestamp") or 0)),
        "slot": int(tx.get("slot") or 0),
        "evidenceAvailable": evidence_available,
        "successful": evidence_available and not chain_failed,
        "chainFailed": chain_failed,
        "transactionError": tx.get("transactionError"),
        "attemptedRolledBackDeltas": attempted if chain_failed else None,
        "type": str(tx.get("type") or "UNKNOWN"),
        "source": str(tx.get("source") or "UNKNOWN"),
        "category": category,
        "programIds": sorted(programs),
        "tokenDeltas": token_deltas,
        "tokenDecimals": decimals,
        "wsolDeltaLamports": wsol_delta,
        "walletNativeDeltaLamports": native_delta,
        "walletQuoteDeltaLamports": quote_delta,
        "feeLamports": int(tx.get("fee") or 0),
        "explicitNativeSentLamports": sent,
        "explicitNativeReceivedLamports": received,
        "nativeCounterparties": counterparties,
        "feePayer": tx.get("feePayer"),
    }


def action_kind(category: str, token_delta: int, quote_delta: int) -> str:
    is_swap = category.startswith("PUMP_") or category.startswith("OTHER_DEX")
    if is_swap and token_delta > 0 and quote_delta < 0:
        return "ECONOMIC_BUY"
    if is_swap and token_delta < 0 and quote_delta > 0:
        return "ECONOMIC_SELL"
    if token_delta > 0:
        return "TOKEN_TRANSFER_IN"
    return "TOKEN_TRANSFER_OUT"


def build_actions(facts: list[dict[str, Any]], start: int, end: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    actions: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for tx in facts:
        if not tx["successful"] or not tx["tokenDeltas"]:
            continue
        targets = [(mint, delta) for mint, delta in tx["tokenDeltas"].items() if delta]
        if len(targets) != 1:
            unsupported.append({
                "signature": tx["signature"], "utc": tx["utc"], "reason": "MULTI_MINT_WALLET_DELTA",
                "tokenDeltas": tx["tokenDeltas"], "walletQuoteDeltaLamports": tx["walletQuoteDeltaLamports"],
            })
            continue
        mint, delta = targets[0]
        actions.append({
            "mint": mint,
            "signature": tx["signature"],
            "timestamp": tx["timestamp"],
            "utc": tx["utc"],
            "slot": tx["slot"],
            "kind": action_kind(tx["category"], delta, tx["walletQuoteDeltaLamports"]),
            "category": tx["category"],
            "source": tx["source"],
            "type": tx["type"],
            "tokenDeltaRaw": delta,
            "tokenDecimals": int(tx["tokenDecimals"].get(mint) or 0),
            "walletQuoteDeltaLamports": tx["walletQuoteDeltaLamports"],
            "walletNativeDeltaLamports": tx["walletNativeDeltaLamports"],
            "wsolDeltaLamports": tx["wsolDeltaLamports"],
            "feeLamports": tx["feeLamports"],
            "explicitNativeSentLamports": tx["explicitNativeSentLamports"],
            "nativeCounterparties": tx["nativeCounterparties"],
            "insideWindow": start <= tx["timestamp"] < end,
        })
    actions.sort(key=lambda row: (row["timestamp"], row["slot"], row["signature"]))
    return actions, unsupported


def effectively_flat(inventory: int, acquired: int) -> bool:
    tolerance = max(1, acquired // EFFECTIVE_ZERO_FRACTION)
    return 0 <= inventory <= tolerance


def reconstruct_episodes(actions: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    by_mint: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for action in actions:
        by_mint[action["mint"]].append(action)
    episodes: list[dict[str, Any]] = []
    for mint, mint_actions in by_mint.items():
        current: dict[str, Any] | None = None
        episode_index = 0
        for action in mint_actions:
            delta = int(action["tokenDeltaRaw"])
            if current is None:
                episode_index += 1
                current = {
                    "mint": mint,
                    "episodeIndex": episode_index,
                    "actions": [],
                    "inventoryRaw": 0,
                    "economicAcquiredRaw": 0,
                    "tokenReceivedRaw": 0,
                    "tokenSoldRaw": 0,
                    "tokenTransferredOutRaw": 0,
                    "unknownOpeningInventoryRaw": 0,
                    "leftCensored": action["timestamp"] < start or delta < 0,
                }
                if delta < 0:
                    current["unknownOpeningInventoryRaw"] = -delta
                    current["inventoryRaw"] = -delta
            current["actions"].append(action)
            if action["kind"] == "ECONOMIC_BUY":
                current["economicAcquiredRaw"] += delta
            elif action["kind"] == "TOKEN_TRANSFER_IN":
                current["tokenReceivedRaw"] += delta
            elif action["kind"] == "ECONOMIC_SELL":
                current["tokenSoldRaw"] += -delta
            elif action["kind"] == "TOKEN_TRANSFER_OUT":
                current["tokenTransferredOutRaw"] += -delta
            current["inventoryRaw"] += delta
            total_known_in = current["economicAcquiredRaw"] + current["tokenReceivedRaw"] + current["unknownOpeningInventoryRaw"]
            if current["inventoryRaw"] < 0:
                current["soldExceedsKnownAcquired"] = True
            if effectively_flat(max(current["inventoryRaw"], 0), total_known_in):
                current["closedTimestamp"] = action["timestamp"]
                current["closedUtc"] = action["utc"]
                current["residualRaw"] = max(current["inventoryRaw"], 0)
                episodes.append(finalize_episode(current, start, end))
                current = None
        if current is not None:
            current["closedTimestamp"] = None
            current["closedUtc"] = None
            current["residualRaw"] = max(current["inventoryRaw"], 0)
            episodes.append(finalize_episode(current, start, end))
    episodes.sort(key=lambda row: (row["openedTimestamp"], row["mint"], row["episodeIndex"]))
    return episodes


def finalize_episode(episode: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    actions = episode["actions"]
    first = actions[0]
    buys = [row for row in actions if row["kind"] == "ECONOMIC_BUY"]
    sells = [row for row in actions if row["kind"] == "ECONOMIC_SELL"]
    transfers = [row for row in actions if row["kind"].startswith("TOKEN_TRANSFER")]
    opened = first["timestamp"]
    closed = episode.get("closedTimestamp")
    in_scope = start <= opened < end
    right_censored = in_scope and (closed is None or closed >= end)
    left_censored = bool(episode.get("leftCensored")) or opened < start
    pump_relevant = any(row["category"].startswith("PUMP_") for row in actions)
    signed = sum(int(row["walletQuoteDeltaLamports"]) for row in buys + sells)
    deployed = sum(max(-int(row["walletQuoteDeltaLamports"]), 0) for row in buys)
    returned = sum(max(int(row["walletQuoteDeltaLamports"]), 0) for row in sells)
    self_contained = (
        in_scope
        and not left_censored
        and not right_censored
        and closed is not None
        and first["kind"] == "ECONOMIC_BUY"
        and not transfers
        and not episode.get("soldExceedsKnownAcquired", False)
        and episode["economicAcquiredRaw"] > 0
        and episode["tokenSoldRaw"] <= episode["economicAcquiredRaw"]
    )
    reason: list[str] = []
    if not in_scope:
        reason.append("OUTSIDE_PRIMARY_START")
    if left_censored:
        reason.append("LEFT_CENSORED")
    if right_censored:
        reason.append("RIGHT_CENSORED")
    if closed is None:
        reason.append("OPEN_RESIDUAL")
    if transfers:
        reason.append("UNEXPLAINED_TOKEN_TRANSFER")
    if episode.get("soldExceedsKnownAcquired") or episode["tokenSoldRaw"] > episode["economicAcquiredRaw"] + episode["tokenReceivedRaw"]:
        reason.append("SOLD_EXCEEDS_KNOWN_ACQUIRED")
    if first["kind"] != "ECONOMIC_BUY":
        reason.append("NO_RECONSTRUCTED_ECONOMIC_BASIS")
    episode.update({
        "openedTimestamp": opened,
        "openedUtc": first["utc"],
        "rightCensored": right_censored,
        "insideFrozenWindow": in_scope,
        "pumpRelevant": pump_relevant,
        "fullySelfContained": self_contained,
        "reconciliationReasons": sorted(set(reason)),
        "buyCount": len(buys),
        "sellCount": len(sells),
        "addCount": max(0, len(buys) - 1),
        "stagedExit": len(sells) > 1,
        "deployedLamports": deployed,
        "returnedLamports": returned,
        "netPnlLamports": signed,
        "deployedSol": deployed / LAMPORTS,
        "returnedSol": returned / LAMPORTS,
        "netPnlSol": signed / LAMPORTS,
        "returnPct": (signed / deployed * 100) if deployed else None,
        "holdingSeconds": (closed - opened) if closed is not None else None,
        "firstBuySol": (-int(buys[0]["walletQuoteDeltaLamports"]) / LAMPORTS) if buys else None,
        "totalFeesSol": sum(int(row["feeLamports"]) for row in buys + sells) / LAMPORTS,
        "explicitNativeSentSol": sum(int(row["explicitNativeSentLamports"]) for row in buys + sells) / LAMPORTS,
        "entryVenue": buys[0]["category"] if buys else None,
        "exitVenues": sorted(set(row["category"] for row in sells)),
        "entrySignature": buys[0]["signature"] if buys else None,
        "finalExitSignature": sells[-1]["signature"] if sells else None,
    })
    return episode


def stats(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = [float(row["netPnlSol"]) for row in episodes]
    returns = [float(row["returnPct"]) for row in episodes if row.get("returnPct") is not None]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    ordered = sorted(episodes, key=lambda row: (row.get("closedTimestamp") or 0, row["mint"]))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in ordered:
        equity += float(row["netPnlSol"])
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    holds = [float(row["holdingSeconds"]) for row in episodes if row.get("holdingSeconds") is not None]
    sizes = [float(row["deployedSol"]) for row in episodes]
    best = max(episodes, key=lambda row: float(row["netPnlSol"])) if episodes else None
    worst = min(episodes, key=lambda row: float(row["netPnlSol"])) if episodes else None
    return {
        "episodes": len(episodes),
        "uniqueMints": len({row["mint"] for row in episodes}),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(pnl) - len(wins) - len(losses),
        "deployedSol": sum(float(row["deployedSol"]) for row in episodes),
        "returnedSol": sum(float(row["returnedSol"]) for row in episodes),
        "netPnlSol": sum(pnl),
        "winRatePct": len(wins) / len(episodes) * 100 if episodes else None,
        "profitFactor": gross_win / gross_loss if gross_loss else None,
        "profitFactorInfinite": bool(gross_win and not gross_loss),
        "expectancySol": mean(pnl),
        "medianReturnPct": median(returns),
        "averageWinnerSol": mean(wins),
        "averageLoserSol": mean(losses),
        "bestEpisodeSol": max(pnl) if pnl else None,
        "worstEpisodeSol": min(pnl) if pnl else None,
        "bestEpisode": ({"mint": best["mint"], "pnlSol": best["netPnlSol"], "returnPct": best["returnPct"], "openedUtc": best["openedUtc"], "closedUtc": best["closedUtc"]} if best else None),
        "worstEpisode": ({"mint": worst["mint"], "pnlSol": worst["netPnlSol"], "returnPct": worst["returnPct"], "openedUtc": worst["openedUtc"], "closedUtc": worst["closedUtc"]} if worst else None),
        "sequentialMaxDrawdownSol": max_drawdown,
        "holdingSeconds": {"p10": percentile(holds, 0.10), "p50": percentile(holds, 0.50), "p90": percentile(holds, 0.90)},
        "entrySizeSol": {"p10": percentile(sizes, 0.10), "p25": percentile(sizes, 0.25), "p50": percentile(sizes, 0.50), "p75": percentile(sizes, 0.75), "p90": percentile(sizes, 0.90), "max": max(sizes) if sizes else None},
    }


def apply_failed_fee_drag(result: dict[str, Any], failed_fee_sol: float) -> dict[str, Any]:
    output = dict(result)
    episode_net = float(output.get("netPnlSol") or 0)
    output["closedEpisodeNetPnlBeforeFailedFeesSol"] = episode_net
    output["failedAttemptFeeDragSol"] = failed_fee_sol
    output["netPnlAfterFailedFeesSol"] = episode_net - failed_fee_sol
    output["deployedIncludingFailedFeesSol"] = float(output.get("deployedSol") or 0) + failed_fee_sol
    output["expectancyAfterFailedFeesPerEpisodeSol"] = (
        (episode_net - failed_fee_sol) / int(output["episodes"]) if output.get("episodes") else None
    )
    # Period-level PF treats all failed-attempt fees as realized losses while
    # leaving episode win/loss labels untouched.
    output["profitFactorAfterFailedFees"] = None
    return output


def add_adjusted_profit_factor(result: dict[str, Any], episodes: list[dict[str, Any]], failed_fee_sol: float) -> None:
    gross_win = sum(max(float(row["netPnlSol"]), 0) for row in episodes)
    gross_loss = sum(max(-float(row["netPnlSol"]), 0) for row in episodes) + failed_fee_sol
    result["profitFactorAfterFailedFees"] = gross_win / gross_loss if gross_loss else None
    result["profitFactorAfterFailedFeesInfinite"] = bool(gross_win and not gross_loss)


def concentration(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(episodes, key=lambda row: float(row["netPnlSol"]), reverse=True)
    total = sum(float(row["netPnlSol"]) for row in ordered)
    top_one_pct_n = max(1, math.ceil(len(ordered) * 0.01)) if ordered else 0

    def contribution(n: int) -> dict[str, Any]:
        amount = sum(float(row["netPnlSol"]) for row in ordered[:n])
        return {"n": min(n, len(ordered)), "pnlSol": amount, "shareOfNetPct": amount / total * 100 if total else None}

    removals: dict[str, Any] = {}
    for label, count in (("best1", 1), ("best5", 5), ("best10", 10), ("top1Pct", top_one_pct_n)):
        removals[label] = stats(ordered[count:])
    return {
        "best1": contribution(1),
        "top5": contribution(5),
        "top10": contribution(10),
        "top1Pct": contribution(top_one_pct_n),
        "afterRemoval": removals,
    }


def group_stats(episodes: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in episodes:
        grouped[str(key_fn(row))].append(row)
    return [{"bucket": key, **stats(group)} for key, group in sorted(grouped.items())]


def activity_inventory(facts: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for tx in facts:
        if start <= tx["timestamp"] < end:
            grouped[tx["category"]].append(tx)
    output = []
    for category, rows in sorted(grouped.items()):
        output.append({
            "category": category,
            "transactions": len(rows),
            "successful": sum(1 for row in rows if row["successful"]),
            "failed": sum(1 for row in rows if row["chainFailed"]),
            "evidenceUnavailable": sum(1 for row in rows if not row["evidenceAvailable"]),
            "walletOutflowSol": sum(max(-int(row["walletQuoteDeltaLamports"]), 0) for row in rows) / LAMPORTS,
            "walletInflowSol": sum(max(int(row["walletQuoteDeltaLamports"]), 0) for row in rows) / LAMPORTS,
            "absoluteWalletVolumeSol": sum(abs(int(row["walletQuoteDeltaLamports"])) for row in rows) / LAMPORTS,
            "feesSol": sum(
                int(row["feeLamports"])
                for row in rows
                if row.get("feePayer") == WALLET
            ) / LAMPORTS,
        })
    return output


def load_create_index(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return output
    with path.open(errors="ignore") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except Exception:
                continue
            mint = row.get("mint")
            observed_ns = int(row.get("createReceivedAtUnixNs") or 0)
            if mint and observed_ns:
                output[str(mint)] = row
    return output


def enrich_lifecycle(
    episodes: list[dict[str, Any]],
    create_index: dict[str, dict[str, Any]],
    raw_entries: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    ages: list[float] = []
    mc_proxies: list[float] = []
    entry_venues: collections.Counter[str] = collections.Counter()
    for row in episodes:
        entry_venues[str(row.get("entryVenue") or "UNKNOWN")] += 1
        create = create_index.get(row["mint"])
        if create:
            create_ms = int(create["createReceivedAtUnixNs"]) // 1_000_000
            age_ms = row["openedTimestamp"] * 1000 - create_ms
            if age_ms >= 0:
                row["createObservedUtc"] = utc_iso(create_ms / 1000)
                row["entryAgeMs"] = age_ms
                ages.append(age_ms)
        signature = row.get("entrySignature")
        tx = raw_entries.get(str(signature)) if signature else None
        if tx and (tx.get("meta") or {}).get("err") is None:
            try:
                events = [
                    event for event in decode_trade_events((tx.get("meta") or {}).get("logMessages") or [])
                    if event.user == WALLET and event.mint == row["mint"] and event.is_buy
                ]
            except Exception as error:
                row["entryTradeEventDecodeError"] = str(error)
                events = []
            if len(events) == 1:
                event = events[0]
                market_cap_sol_proxy = event.post_state.virtual_sol * 1_000_000 / event.post_state.virtual_token
                row["entryPumpTradeEvent"] = {
                    "timestamp": event.timestamp,
                    "solAmountLamports": event.sol_amount,
                    "tokenAmountRaw": event.token_amount,
                    "immediateWalletQuoteAmountLamports": event.immediate_quote_amount,
                    "feeBasisPoints": event.fee_basis_points,
                    "feeLamports": event.fee,
                    "creatorFeeBasisPoints": event.creator_fee_basis_points,
                    "creatorFeeLamports": event.creator_fee,
                    "ixName": event.ix_name,
                    "mayhemMode": event.mayhem_mode,
                    "postState": {
                        "virtualSolReserves": event.post_state.virtual_sol,
                        "virtualTokenReserves": event.post_state.virtual_token,
                        "realSolReserves": event.post_state.real_sol,
                        "realTokenReserves": event.post_state.real_token,
                    },
                    "standardOneBillionSupplyMarketCapSolProxy": market_cap_sol_proxy,
                }
                mc_proxies.append(market_cap_sol_proxy)
    return {
        "entryVenueCounts": dict(entry_venues),
        "preMigrationEntries": entry_venues.get("PUMP_BONDING_CURVE", 0),
        "postMigrationEntries": entry_venues.get("PUMPSWAP", 0),
        "pumpRouteUnresolvedEntries": entry_venues.get("PUMP_UNRESOLVED_ROUTE", 0),
        "entryAgeCoverageN": len(ages),
        "entryAgeCoveragePct": len(ages) / len(episodes) * 100 if episodes else None,
        "entryAgeMs": {"p10": percentile(ages, 0.10), "p50": percentile(ages, 0.50), "p90": percentile(ages, 0.90)},
        "reserveProxyCoverageN": len(mc_proxies),
        "reserveProxyCoveragePct": len(mc_proxies) / len(episodes) * 100 if episodes else None,
        "standardOneBillionSupplyMarketCapSolProxy": {
            "p10": percentile(mc_proxies, 0.10),
            "p50": percentile(mc_proxies, 0.50),
            "p90": percentile(mc_proxies, 0.90),
        },
        "eventPositionCoverageN": 0,
        "limitations": [
            "Create-time enrichment is reported only where the independent exact-evidence mint index overlaps the wallet episode.",
            "The reserve MC value is explicitly a SOL proxy using Pump's standard one-billion-token supply; it is not a USD market-cap lookup.",
            "No market-cap/reserve or global event-position proxy is emitted when a finalized Pump TradeEvent was not independently decoded for the entry.",
        ],
    }


def transfer_integrity(episodes: list[dict[str, Any]], facts: list[dict[str, Any]], start: int, end: int) -> dict[str, Any]:
    scope = [row for row in episodes if row["insideFrozenWindow"]]
    contaminated = [row for row in scope if any(a["kind"].startswith("TOKEN_TRANSFER") for a in row["actions"])]
    sold_exceeds = [row for row in scope if row.get("soldExceedsKnownAcquired") or "SOLD_EXCEEDS_KNOWN_ACQUIRED" in row["reconciliationReasons"]]
    self_contained = [row for row in scope if row["fullySelfContained"]]
    counterparties: collections.Counter[str] = collections.Counter()
    sol_funding: collections.Counter[str] = collections.Counter()
    for tx in facts:
        if not (start <= tx["timestamp"] < end):
            continue
        if not tx["successful"]:
            continue
        for counterparty in tx["nativeCounterparties"]:
            counterparties[counterparty] += 1
        if tx["category"] == "SOL_TRANSFER" and tx["walletQuoteDeltaLamports"] > 0:
            for counterparty in tx["nativeCounterparties"]:
                sol_funding[counterparty] += tx["walletQuoteDeltaLamports"]
    causes = collections.Counter(reason for row in scope for reason in row["reconciliationReasons"])
    return {
        "episodesInScope": len(scope),
        "fullySelfContainedEpisodes": len(self_contained),
        "fullySelfContainedPct": len(self_contained) / len(scope) * 100 if scope else None,
        "transferContaminatedEpisodes": len(contaminated),
        "transferContaminatedPct": len(contaminated) / len(scope) * 100 if scope else None,
        "soldExceedsKnownAcquiredEpisodes": len(sold_exceeds),
        "soldExceedsKnownAcquiredPct": len(sold_exceeds) / len(scope) * 100 if scope else None,
        "principalNonReconciliationCauses": dict(causes.most_common()),
        "repeatedNativeCounterparties": [{"address": key, "count": value} for key, value in counterparties.most_common(15)],
        "largestSolFundingCounterparties": [{"address": key, "receivedSol": value / LAMPORTS} for key, value in sol_funding.most_common(15)],
        "heroicClusteringPerformed": False,
    }


def behavioral(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    winners = [row for row in episodes if row["netPnlSol"] > 0]
    losers = [row for row in episodes if row["netPnlSol"] < 0]
    buy_counts = [row["buyCount"] for row in episodes]
    sell_counts = [row["sellCount"] for row in episodes]
    first_sizes = [row["firstBuySol"] for row in episodes if row.get("firstBuySol") is not None]
    deployed = [row["deployedSol"] for row in episodes]
    rounded_size_bands = collections.Counter(round(value, 3) for value in first_sizes)
    return {
        "holdingSeconds": stats(episodes)["holdingSeconds"],
        "buyCount": {"p50": percentile(buy_counts, 0.5), "p90": percentile(buy_counts, 0.9), "max": max(buy_counts) if buy_counts else None},
        "sellCount": {"p50": percentile(sell_counts, 0.5), "p90": percentile(sell_counts, 0.9), "max": max(sell_counts) if sell_counts else None},
        "stagedExitFractionPct": sum(1 for row in episodes if row["stagedExit"]) / len(episodes) * 100 if episodes else None,
        "episodesWithAddsPct": sum(1 for row in episodes if row["addCount"] > 0) / len(episodes) * 100 if episodes else None,
        "addsPerEpisode": {"p50": percentile([row["addCount"] for row in episodes], 0.5), "p90": percentile([row["addCount"] for row in episodes], 0.9)},
        "firstBuySizeSol": {"p10": percentile(first_sizes, 0.1), "p50": percentile(first_sizes, 0.5), "p90": percentile(first_sizes, 0.9)},
        "totalDeployedSol": {"p10": percentile(deployed, 0.1), "p50": percentile(deployed, 0.5), "p90": percentile(deployed, 0.9)},
        "winnerHoldingSecondsP50": median(row["holdingSeconds"] for row in winners if row.get("holdingSeconds") is not None),
        "loserHoldingSecondsP50": median(row["holdingSeconds"] for row in losers if row.get("holdingSeconds") is not None),
        "commonRoundedFirstBuyBands": [{"sol": key, "n": value} for key, value in rounded_size_bands.most_common(12)],
    }


def verdicts(primary: list[dict[str, Any]], integrity: dict[str, Any], economics: dict[str, Any], concentration_data: dict[str, Any], chronology: list[dict[str, Any]]) -> tuple[str, str, list[str]]:
    reasons: list[str] = []
    self_pct = float(integrity.get("fullySelfContainedPct") or 0)
    if self_pct >= 90 and len(primary) >= 500:
        data_verdict = "2CQ_ADDRESS_SELF_CONTAINED_ENOUGH"
    elif self_pct >= 50 and len(primary) >= 100:
        data_verdict = "2CQ_ADDRESS_PARTIALLY_CONTAMINATED_BUT_USABLE"
    else:
        data_verdict = "2CQ_ADDRESS_ACCOUNTING_BLOCKER"
    if economics.get("netPnlAfterFailedFeesSol", 0) <= 0 or (economics.get("profitFactorAfterFailedFees") or 0) <= 1:
        profitability = "2CQ_TEACHER_PROFITABILITY_NOT_CONFIRMED"
        reasons.append("closed reconcilable Pump episodes were not profitable in aggregate")
    else:
        after_top10 = concentration_data["afterRemoval"]["best10"]
        positive_days = sum(1 for row in chronology if row.get("netPnlAfterFailedFeesSol", row.get("netPnlSol", 0)) > 0)
        broadly_positive = after_top10.get("netPnlAfterFailedFeesSol", 0) > 0 and (after_top10.get("profitFactorAfterFailedFees") or 0) > 1
        chronologically_consistent = positive_days >= max(1, math.ceil(len(chronology) / 2))
        if data_verdict != "2CQ_ADDRESS_ACCOUNTING_BLOCKER" and broadly_positive and chronologically_consistent:
            profitability = "2CQ_TEACHER_PROFITABILITY_CONFIRMED"
        else:
            profitability = "2CQ_TEACHER_PROFITABILITY_PROMISING_BUT_ACCOUNTING_INCOMPLETE"
        if not broadly_positive:
            reasons.append("aggregate profitability does not survive removal of the ten best episodes")
        if not chronologically_consistent:
            reasons.append("fewer than half of represented UTC days were profitable")
    return profitability, data_verdict, reasons


def render_markdown(report: dict[str, Any]) -> str:
    h = report["headline"]
    e = report["primaryEconomics"]
    lines = [
        "# 2CQ Teacher Validation V1",
        "",
        f"Generated: `{report['generatedAtUtc']}`",
        f"Wallet: `{report['wallet']}`",
        f"Frozen window: `{report['frozenWindow']['startUtc']}` to `{report['frozenWindow']['endUtc']}`",
        f"Window choice: **{report['frozenWindow']['days']} complete UTC days** ({report['frozenWindow']['extensionReason']})",
        "",
        "## Verdict",
        "",
        f"- Profitability: **{report['verdicts']['profitability']}**",
        f"- Data: **{report['verdicts']['data']}**",
        f"- Closed, reconcilable Pump episodes: **{h['primaryEpisodes']:,}**",
        "",
        "## Primary Economics",
        "",
        "| Metric | Result |",
        "|---|---:|",
    ]
    for label, key in (
        ("Episodes", "episodes"), ("Unique mints", "uniqueMints"), ("SOL deployed", "deployedSol"),
        ("SOL returned", "returnedSol"), ("Closed-episode net before failed fees SOL", "closedEpisodeNetPnlBeforeFailedFeesSol"),
        ("Failed-attempt fee drag SOL", "failedAttemptFeeDragSol"), ("Net after failed fees SOL", "netPnlAfterFailedFeesSol"),
        ("Win rate %", "winRatePct"), ("Episode PF before failed fees", "profitFactor"),
        ("Period PF after failed fees", "profitFactorAfterFailedFees"),
        ("Expectancy after failed fees / episode SOL", "expectancyAfterFailedFeesPerEpisodeSol"),
        ("Median return %", "medianReturnPct"), ("Average winner SOL", "averageWinnerSol"),
        ("Average loser SOL", "averageLoserSol"), ("Max drawdown SOL", "sequentialMaxDrawdownSol"),
    ):
        lines.append(f"| {label} | {e.get(key)} |")
    lines += ["", "## Activity By Venue", "", "| Category | Tx | Outflow SOL | Inflow SOL | Absolute volume SOL | Fees SOL |", "|---|---:|---:|---:|---:|---:|"]
    for row in report["activityInventory"]:
        lines.append(f"| {row['category']} | {row['transactions']} | {row['walletOutflowSol']:.6f} | {row['walletInflowSol']:.6f} | {row['absoluteWalletVolumeSol']:.6f} | {row['feesSol']:.6f} |")
    lines += ["", "## Chronology", "", "| UTC day | N | WR % | PF after failed fees | Failed attempts | Failed fees SOL | Net after fees SOL |", "|---|---:|---:|---:|---:|---:|---:|"]
    for row in report["chronologyDaily"]:
        lines.append(f"| {row['bucket']} | {row['episodes']} | {row['winRatePct']} | {row['profitFactorAfterFailedFees']} | {row['failedAttempts']} | {row['failedAttemptFeeDragSol']} | {row['netPnlAfterFailedFeesSol']} |")
    lines += ["", "## Concentration", ""]
    for key in ("best1", "top5", "top10", "top1Pct"):
        row = report["pnlConcentration"][key]
        lines.append(f"- {key}: `{row['pnlSol']:.6f} SOL`, `{row['shareOfNetPct']}%` of net PnL")
    lines += ["", "## Accounting Notes", ""]
    lines.extend(f"- {note}" for note in report["accountingNotes"])
    lines += ["", "## Next Decomposition", "", report["nextBehavioralDecomposition"], ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--env-file", type=pathlib.Path, default=pathlib.Path(".env"))
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--create-index", type=pathlib.Path, default=pathlib.Path("/var/lib/pump-exact-evidence-ledger-v1/exact-alpha-research-v1/discovery-mint-index.jsonl"))
    parser.add_argument("--end-utc", default=None, help="exclusive UTC midnight, YYYY-MM-DD")
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--reuse-history", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    env_path = args.env_file if args.env_file.is_absolute() else root / args.env_file
    env = load_env(env_path)
    merged_env = dict(env)
    if os.environ.get("HELIUS_API_KEY"):
        merged_env["HELIUS_API_KEY"] = os.environ["HELIUS_API_KEY"]
    keys = helius_keys(merged_env)
    rpc_url = env.get("SECURE_RPC_URL") or env.get("EDGE_CURVE_PRICER_RPC") or os.environ.get("SECURE_RPC_URL")

    now = dt.datetime.now(dt.timezone.utc)
    if args.end_utc:
        end_dt = dt.datetime.strptime(args.end_utc, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    else:
        end_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    primary_start_dt = end_dt - dt.timedelta(days=PRIMARY_DAYS)
    context_start_dt = primary_start_dt - dt.timedelta(days=CONTEXT_DAYS)
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    freeze_path = output / "window-freeze.json"
    frozen = {
        "schema": SCHEMA,
        "wallet": WALLET,
        "fixedBeforeEconomics": True,
        "primaryDays": PRIMARY_DAYS,
        "primaryStartUtc": primary_start_dt.isoformat().replace("+00:00", "Z"),
        "endUtcExclusive": end_dt.isoformat().replace("+00:00", "Z"),
        "contextStartUtc": context_start_dt.isoformat().replace("+00:00", "Z"),
        "extensionRule": f"extend once to {EXTENDED_DAYS} complete UTC days only if fewer than {MIN_PRIMARY_EPISODES} closed reconcilable episodes",
        "createdBeforeEconomicsAtUtc": now.isoformat().replace("+00:00", "Z"),
    }
    if freeze_path.exists():
        existing = json.loads(freeze_path.read_text())
        for key in ("wallet", "primaryStartUtc", "endUtcExclusive"):
            if existing.get(key) != frozen.get(key):
                raise RuntimeError(f"existing frozen window disagrees on {key}")
        frozen = existing
    else:
        freeze_path.write_text(json.dumps(frozen, indent=2) + "\n")
        os.chmod(freeze_path, 0o444)

    signature_path = output / "wallet-finalized-signatures.jsonl.gz"
    if args.reuse_history and signature_path.exists():
        signature_rows = read_jsonl_gz(signature_path)
    else:
        signature_rows = fetch_signature_ledger(keys, int(context_start_dt.timestamp()), signature_path, args.max_pages)
    if min(int(row.get("blockTime") or 0) for row in signature_rows) > int(context_start_dt.timestamp()):
        raise RuntimeError("signature ledger does not cover context boundary")
    context_signature_rows = [
        row for row in signature_rows
        if int(context_start_dt.timestamp()) <= int(row.get("blockTime") or 0) < int(end_dt.timestamp())
    ]
    frozen_signature_rows = [
        row for row in context_signature_rows
        if int(primary_start_dt.timestamp()) <= int(row.get("blockTime") or 0)
    ]
    frozen_signature_path = output / "wallet-frozen-7d-signatures.jsonl.gz"
    with gzip.open(frozen_signature_path, "wt", encoding="utf-8") as handle:
        for row in frozen_signature_rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    history_path = output / "wallet-finalized-transactions.jsonl.gz"
    history = fetch_enhanced_transactions(keys, context_signature_rows, history_path)
    signature_by_id = {str(row["signature"]): row for row in signature_rows}
    for tx in history:
        signature = str(tx.get("signature") or "")
        signature_meta = signature_by_id.get(signature) or {}
        if not tx.get("timestamp") and signature_meta.get("blockTime"):
            tx["timestamp"] = signature_meta["blockTime"]
        tx["_signatureError"] = signature_meta.get("err")
        tx["_confirmationStatus"] = signature_meta.get("confirmationStatus")
        if tx.get("_enhancedUnavailable"):
            tx["transactionError"] = signature_meta.get("err")
    facts = [transaction_facts(tx) for tx in history]
    finality = {
        "checked": len(context_signature_rows),
        "finalized": sum(1 for row in context_signature_rows if row.get("confirmationStatus") == "finalized"),
        "notFinalized": sum(1 for row in context_signature_rows if row.get("confirmationStatus") not in (None, "finalized")),
        "statusOmitted": sum(1 for row in context_signature_rows if row.get("confirmationStatus") is None),
        "enhancedUnavailable": sum(1 for row in facts if not row["evidenceAvailable"]),
    }
    if finality["notFinalized"]:
        raise RuntimeError(f"signature ledger contains {finality['notFinalized']} non-finalized rows")

    start = int(primary_start_dt.timestamp())
    end = int(end_dt.timestamp())
    actions, unsupported = build_actions(facts, start, end)
    episodes = reconstruct_episodes(actions, start, end)
    primary = [row for row in episodes if row["insideFrozenWindow"] and row["pumpRelevant"] and row["fullySelfContained"]]
    if len(primary) < MIN_PRIMARY_EPISODES:
        raise RuntimeError(
            f"7-day window produced only {len(primary)} closed reconcilable episodes; "
            "the required one-time 30-day extension must be fetched before reporting"
        )

    create_index = load_create_index(args.create_index)
    raw_entry_path = output / "primary-entry-finalized-transactions.jsonl.gz"
    raw_entries = fetch_raw_transactions(
        keys, [str(row.get("entrySignature") or "") for row in primary], raw_entry_path, rpc_url
    )
    lifecycle = enrich_lifecycle(primary, create_index, raw_entries)
    integrity = transfer_integrity(episodes, facts, start, end)
    failed_attempts = [
        row for row in facts
        if start <= row["timestamp"] < end and row["chainFailed"] and row["feePayer"] == WALLET
    ]
    failed_fee_sol = sum(int(row["feeLamports"]) for row in failed_attempts) / LAMPORTS
    economics = apply_failed_fee_drag(stats(primary), failed_fee_sol)
    add_adjusted_profit_factor(economics, primary, failed_fee_sol)
    concentration_data = concentration(primary)
    ordered_by_pnl = sorted(primary, key=lambda row: float(row["netPnlSol"]), reverse=True)
    removal_counts = {
        "best1": 1,
        "best5": 5,
        "best10": 10,
        "top1Pct": max(1, math.ceil(len(primary) * 0.01)),
    }
    for label, count in removal_counts.items():
        adjusted = apply_failed_fee_drag(concentration_data["afterRemoval"][label], failed_fee_sol)
        concentration_data["afterRemoval"][label].update(adjusted)
        add_adjusted_profit_factor(
            concentration_data["afterRemoval"][label], ordered_by_pnl[count:], failed_fee_sol
        )
    daily = group_stats(primary, lambda row: utc_iso(row["closedTimestamp"])[:10])
    failed_by_day: collections.Counter[str] = collections.Counter()
    failed_n_by_day: collections.Counter[str] = collections.Counter()
    for row in failed_attempts:
        day = str(row["utc"])[:10]
        failed_by_day[day] += int(row["feeLamports"])
        failed_n_by_day[day] += 1
    episodes_by_day: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in primary:
        episodes_by_day[str(row["closedUtc"])[:10]].append(row)
    for row in daily:
        drag = failed_by_day[row["bucket"]] / LAMPORTS
        row.update(apply_failed_fee_drag(row, drag))
        add_adjusted_profit_factor(row, episodes_by_day[row["bucket"]], drag)
        row["failedAttempts"] = failed_n_by_day[row["bucket"]]
    for day in sorted(set(failed_by_day) - {row["bucket"] for row in daily}):
        empty = apply_failed_fee_drag(stats([]), failed_by_day[day] / LAMPORTS)
        add_adjusted_profit_factor(empty, [], failed_by_day[day] / LAMPORTS)
        daily.append({"bucket": day, **empty, "failedAttempts": failed_n_by_day[day]})
    daily.sort(key=lambda row: row["bucket"])
    behavior = behavioral(primary)
    profitability, data_verdict, verdict_reasons = verdicts(primary, integrity, economics, concentration_data, daily)

    in_scope = [row for row in episodes if row["insideFrozenWindow"]]
    economic_right_censored = [
        row for row in in_scope
        if row["rightCensored"] and row["economicAcquiredRaw"] > 0
    ]
    residual_fractions = [
        row["residualRaw"] / row["economicAcquiredRaw"]
        for row in economic_right_censored if row["economicAcquiredRaw"] > 0
    ]
    right_censored_cashflow = sum(float(row["netPnlSol"]) for row in economic_right_censored)
    zero_value_stress = float(economics["netPnlAfterFailedFeesSol"]) + right_censored_cashflow
    material_unvalued_book = len(economic_right_censored) >= max(50, math.ceil(len(in_scope) * 0.05))
    if profitability == "2CQ_TEACHER_PROFITABILITY_CONFIRMED" and material_unvalued_book:
        profitability = "2CQ_TEACHER_PROFITABILITY_PROMISING_BUT_ACCOUNTING_INCOMPLETE"
        verdict_reasons.append(
            "systematic retained inventory remains unvalued across a material share of in-window economic episodes"
        )
    period_facts = [row for row in facts if start <= row["timestamp"] < end and row["evidenceAvailable"]]
    wallet_period = {
        "actualWalletQuoteDeltaSol": sum(int(row["walletQuoteDeltaLamports"]) for row in period_facts) / LAMPORTS,
        "successfulTransactionQuoteDeltaSol": sum(int(row["walletQuoteDeltaLamports"]) for row in period_facts if row["successful"]) / LAMPORTS,
        "failedTransactionQuoteDeltaSol": sum(int(row["walletQuoteDeltaLamports"]) for row in period_facts if row["chainFailed"]) / LAMPORTS,
        "primaryClosedEpisodeNetAfterFailedFeesSol": economics["netPnlAfterFailedFeesSol"],
        "scopeDifferenceExplanation": "whole-wallet delta also contains open/censored inventory, non-Pump activity, plain SOL transfers, rent timing, and unreconciled episodes",
    }
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "wallet": WALLET,
        "frozenWindow": {
            "startUtc": primary_start_dt.isoformat().replace("+00:00", "Z"),
            "endUtc": end_dt.isoformat().replace("+00:00", "Z"),
            "days": PRIMARY_DAYS,
            "extensionReason": f"7-day window retained because {len(primary)} >= {MIN_PRIMARY_EPISODES} closed reconcilable Pump episodes",
            "windowFreezeSha256": file_sha256(freeze_path),
        },
        "provenance": {
            "signatureLedgerPath": str(signature_path),
            "signatureLedgerSha256": file_sha256(signature_path),
            "signatureRows": len(signature_rows),
            "frozenSignatureLedgerPath": str(frozen_signature_path),
            "frozenSignatureLedgerSha256": file_sha256(frozen_signature_path),
            "frozenSignatureRows": len(frozen_signature_rows),
            "contextSignatureRows": len(context_signature_rows),
            "enhancedHistoryPath": str(history_path),
            "enhancedHistorySha256": file_sha256(history_path),
            "historyRows": len(history),
            "historyOldestUtc": utc_iso(min(int(row.get("timestamp") or 0) for row in history)),
            "historyNewestUtc": utc_iso(max(int(row.get("timestamp") or 0) for row in history)),
            "primaryEntryRawTransactionsPath": str(raw_entry_path),
            "primaryEntryRawTransactionsSha256": file_sha256(raw_entry_path),
            "finality": finality,
            "economicsAuthority": "exact wallet native balance delta + wallet-owned WSOL raw delta; transaction fees/tips are therefore included in actual wallet economics",
            "priorAlphaArtifactsRead": False,
        },
        "headline": {
            "transactionsInWindow": sum(1 for row in facts if start <= row["timestamp"] < end),
            "successfulTransactionsInWindow": sum(1 for row in facts if start <= row["timestamp"] < end and row["successful"]),
            "failedTransactionsInWindow": sum(1 for row in facts if start <= row["timestamp"] < end and row["chainFailed"]),
            "transactionEvidenceUnavailableInWindow": sum(1 for row in facts if start <= row["timestamp"] < end and not row["evidenceAvailable"]),
            "walletPaidFailedAttempts": len(failed_attempts),
            "failedAttemptFeeDragSol": failed_fee_sol,
            "episodeStartsInWindow": len(in_scope),
            "primaryEpisodes": len(primary),
            "leftCensored": sum(1 for row in in_scope if row["leftCensored"]),
            "rightCensored": sum(1 for row in in_scope if row["rightCensored"]),
            "unreconciled": sum(1 for row in in_scope if not row["fullySelfContained"]),
            "unsupportedMultiMintTransactions": len(unsupported),
        },
        "activityInventory": activity_inventory(facts, start, end),
        "wholeWalletPeriodReconciliation": wallet_period,
        "rightCensoredInventory": {
            "economicEpisodes": len(economic_right_censored),
            "shareOfInScopeEpisodesPct": len(economic_right_censored) / len(in_scope) * 100 if in_scope else None,
            "deployedSol": sum(float(row["deployedSol"]) for row in economic_right_censored),
            "returnedSol": sum(float(row["returnedSol"]) for row in economic_right_censored),
            "netCashflowBeforeResidualValuationSol": right_censored_cashflow,
            "retainedFractionOfAcquired": {
                "p10": percentile(residual_fractions, 0.10),
                "p50": percentile(residual_fractions, 0.50),
                "p75": percentile(residual_fractions, 0.75),
                "p90": percentile(residual_fractions, 0.90),
            },
            "closedBookPlusFailedFeesIfResidualMarkedZeroSol": zero_value_stress,
            "breakEvenAggregateResidualValueSol": max(0.0, -zero_value_stress),
            "interpretation": "not realized PnL; exact cutoff valuation is required because retained inventory is systematic rather than incidental dust",
        },
        "failedAttemptAccounting": {
            "failedSignaturesInWindow": sum(1 for row in facts if start <= row["timestamp"] < end and row["chainFailed"]),
            "walletWasFeePayerN": len(failed_attempts),
            "actualFeeDragSol": failed_fee_sol,
            "inventoryMutation": "ZERO by construction",
            "attribution": "wallet-period execution overhead; never treated as a successful mint acquisition or exit",
            "errorCounts": dict(collections.Counter(json.dumps(row["transactionError"], sort_keys=True) for row in failed_attempts)),
        },
        "transferClusterIntegrity": integrity,
        "primaryEconomics": economics,
        "pnlConcentration": concentration_data,
        "chronologyDaily": daily,
        "behavioralDescription": behavior,
        "venueLifecycleDescription": lifecycle,
        "publicDashboardSanity": {
            "existingProjectValue": {"transactionCap": 5000, "positions": 904, "realizedTrades": 3416, "realizedPnlSol": 201.84268416148322, "winRatePct": 54.18618266978923, "profitFactor": 2.2894026158385126},
            "comparison": "NOT_COMPARABLE_AS_TRUTH: legacy values use a different, transaction-capped lifetime window and prior approximation; they are shown only as an existing-project sanity reference.",
        },
        "verdicts": {"profitability": profitability, "data": data_verdict, "reasons": verdict_reasons},
        "nextBehavioralDecomposition": (
            "ENTRY SELECTION should be investigated first. The teacher's own closed-episode economics and chronology pass before any attempt to explain why it selected these mints; timing, sizing, exit, and execution remain later decompositions."
            if profitability == "2CQ_TEACHER_PROFITABILITY_CONFIRMED" and data_verdict != "2CQ_ADDRESS_ACCOUNTING_BLOCKER"
            else "NONE. The teacher did not pass both Phase 0A gates strongly enough to justify behavioral decomposition."
        ),
        "accountingNotes": [
            "Primary unit is a zero-to-positive-to-effectively-zero mint position episode, not a transaction.",
            "Effectively zero means at most one raw unit or one part per billion of known acquired inventory, whichever is larger.",
            "Transferred-in inventory is never assigned a purchase basis; any episode containing unexplained token transfers is excluded from primary economics.",
            "Actual wallet quote delta includes base/priority transaction fee, explicit delivery transfers, token-account rent changes, Pump/creator fees, and swap proceeds as they affected this address.",
            "Failed transactions mutate zero inventory. When 2CQ was the fee payer, their exact transaction fee is deducted separately from period economics and period-level adjusted PF.",
            "Transactions with more than one non-WSOL wallet token delta are fail-closed as unsupported instead of allocating one SOL delta to multiple mints.",
            "No prior alpha model, ranker, threshold, lifecycle anchor, or shadow outcome was read.",
        ],
        "episodes": episodes,
        "unsupportedTransactions": unsupported,
    }
    report["reportSha256"] = sha256({key: value for key, value in report.items() if key != "reportSha256"})
    report_path = output / "report.json"
    episodes_path = output / "episodes.jsonl.gz"
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    with gzip.open(episodes_path, "wt", encoding="utf-8") as handle:
        for row in episodes:
            handle.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
    (output / "report.md").write_text(render_markdown(report))
    print(json.dumps({
        "classification": [profitability, data_verdict],
        "window": report["frozenWindow"],
        "headline": report["headline"],
        "economics": economics,
        "output": str(output),
    }, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
