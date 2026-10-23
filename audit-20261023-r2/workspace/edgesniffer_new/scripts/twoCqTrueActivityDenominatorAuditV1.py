#!/usr/bin/env python3
"""Independent finalized-wallet denominator for a frozen 2CQ activity window."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import struct
import time
import urllib.request
from collections import Counter
from typing import Any


SCHEMA = "2CQ_TRUE_ACTIVITY_DENOMINATOR_AUDIT_V1"
WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"
MAX_SUPPORTED_TRANSACTION_VERSION = 1
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
WSOL = "So11111111111111111111111111111111111111112"
JUPITER = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB",
}
BENIGN_PROGRAMS = {
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
}
COMPACT_MAGIC = b"AHCDV1\n\0"
COMPACT_RECORD = struct.Struct("<32sqQ")
SIGNATURE_LEDGER_SCHEMA = "2CQ_WALLET_SIGNATURE_POPULATION_V1"


def iso_from_ms(value: int) -> str:
    return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ms(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_env(path: pathlib.Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(errors="ignore").splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            key, value = raw.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def rpc(url: str, method: str, params: list[Any], request_id: int = 1) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}).encode()
    last_error: Exception | None = None
    for attempt in range(8):
        try:
            request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=45) as response:
                parsed = json.load(response)
            if parsed.get("error"):
                raise RuntimeError(str(parsed["error"]))
            return parsed.get("result")
        except Exception as error:
            last_error = error
            time.sleep(min(8.0, 0.5 * (2**attempt)))
    raise RuntimeError(f"RPC_{method.upper()}:{last_error}")


def signature_sort_key(row: dict[str, Any]) -> tuple[int, bool, int, str]:
    transaction_index = row.get("transactionIndex")
    return (
        int(row.get("slot") or 0),
        transaction_index is None,
        int(transaction_index) if transaction_index is not None else 0,
        str(row.get("signature") or ""),
    )


def dedupe_signatures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = {str(row["signature"]): row for row in rows if row.get("signature")}
    return sorted(deduped.values(), key=signature_sort_key)


def fetch_signatures(
    url: str, lower_block_time: int, max_pages: int = 100,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    before: str | None = None
    boundary_reached = False
    for page in range(max_pages):
        config: dict[str, Any] = {"limit": 1000, "commitment": "finalized"}
        if before:
            config["before"] = before
        batch = rpc(url, "getSignaturesForAddress", [WALLET, config], page + 1) or []
        if not batch:
            break
        rows.extend(batch)
        next_before = str(batch[-1]["signature"])
        if next_before == before:
            raise RuntimeError("SIGNATURE_PAGINATION_CURSOR_REPEATED")
        before = next_before
        oldest = int(batch[-1].get("blockTime") or 0)
        if oldest and oldest <= lower_block_time:
            boundary_reached = True
            break
    if not boundary_reached:
        raise RuntimeError("SIGNATURE_PAGINATION_BOUNDARY_NOT_REACHED")
    return dedupe_signatures(rows)


def signature_ledger_manifest_path(path: pathlib.Path) -> pathlib.Path:
    return path.with_name(path.name + ".manifest.json")


def write_signature_ledger(
    path: pathlib.Path,
    rows: list[dict[str, Any]],
    population_start_block_time: int,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    ordered = dedupe_signatures(rows)
    if any(int(row.get("blockTime") or 0) < population_start_block_time for row in ordered):
        raise RuntimeError("SIGNATURE_LEDGER_ROW_BEFORE_POPULATION_START")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            for row in ordered:
                stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    os.replace(temporary, path)
    manifest = {
        "schema": SIGNATURE_LEDGER_SCHEMA,
        "wallet": WALLET,
        "populationStartBlockTime": population_start_block_time,
        "rowN": len(ordered),
        "oldestBlockTime": min((int(row.get("blockTime") or 0) for row in ordered), default=None),
        "newestBlockTime": max((int(row.get("blockTime") or 0) for row in ordered), default=None),
        "oldestSlot": min((int(row.get("slot") or 0) for row in ordered), default=None),
        "newestSlot": max((int(row.get("slot") or 0) for row in ordered), default=None),
        "ledgerSha256": file_sha256(path),
        "provenance": provenance,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(signature_ledger_manifest_path(path), manifest)
    return manifest


def load_signature_ledger(
    path: pathlib.Path, population_start_block_time: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_path = signature_ledger_manifest_path(path)
    if not path.exists() or not manifest_path.exists():
        raise RuntimeError("SIGNATURE_LEDGER_OR_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SIGNATURE_LEDGER_SCHEMA:
        raise RuntimeError("SIGNATURE_LEDGER_SCHEMA_MISMATCH")
    if manifest.get("wallet") != WALLET:
        raise RuntimeError("SIGNATURE_LEDGER_WALLET_MISMATCH")
    if int(manifest.get("populationStartBlockTime") or 0) != population_start_block_time:
        raise RuntimeError("SIGNATURE_LEDGER_BOUNDARY_MISMATCH")
    if manifest.get("ledgerSha256") != file_sha256(path):
        raise RuntimeError("SIGNATURE_LEDGER_HASH_MISMATCH")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    ordered = dedupe_signatures(rows)
    if len(ordered) != len(rows) or len(ordered) != int(manifest.get("rowN") or -1):
        raise RuntimeError("SIGNATURE_LEDGER_DUPLICATE_OR_COUNT_MISMATCH")
    if ordered != rows:
        raise RuntimeError("SIGNATURE_LEDGER_ORDER_MISMATCH")
    if any(int(row.get("blockTime") or 0) < population_start_block_time for row in rows):
        raise RuntimeError("SIGNATURE_LEDGER_ROW_BEFORE_POPULATION_START")
    return rows, manifest


def fetch_signatures_incremental(
    url: str,
    population_start_block_time: int,
    ledger_path: pathlib.Path,
    max_pages: int = 100,
) -> list[dict[str, Any]]:
    if not ledger_path.exists():
        rows = fetch_signatures(url, population_start_block_time - 180, max_pages=max_pages)
        bounded = [
            row for row in rows
            if int(row.get("blockTime") or 0) >= population_start_block_time
        ]
        write_signature_ledger(
            ledger_path, bounded, population_start_block_time,
            {"source": "FULL_REMOTE_BOUNDARY_REACHED"},
        )
        return bounded

    baseline, prior_manifest = load_signature_ledger(ledger_path, population_start_block_time)
    if not baseline:
        raise RuntimeError("SIGNATURE_LEDGER_EMPTY")
    baseline_by_signature = {str(row["signature"]): row for row in baseline}
    baseline_newest_slot = max(int(row.get("slot") or 0) for row in baseline)
    fetched: list[dict[str, Any]] = []
    before: str | None = None
    overlap_reached = False
    for page in range(max_pages):
        config: dict[str, Any] = {"limit": 1000, "commitment": "finalized"}
        if before:
            config["before"] = before
        batch = rpc(url, "getSignaturesForAddress", [WALLET, config], page + 1) or []
        if not batch:
            break
        next_before = str(batch[-1]["signature"])
        if next_before == before:
            raise RuntimeError("SIGNATURE_PAGINATION_CURSOR_REPEATED")
        fetched.extend(batch)
        before = next_before
        if any(str(row.get("signature") or "") in baseline_by_signature for row in batch):
            overlap_reached = True
            break
    if not overlap_reached:
        raise RuntimeError("SIGNATURE_INCREMENTAL_BASELINE_OVERLAP_NOT_REACHED")

    for row in fetched:
        signature = str(row.get("signature") or "")
        prior = baseline_by_signature.get(signature)
        if prior is not None:
            if int(prior.get("slot") or 0) != int(row.get("slot") or 0):
                raise RuntimeError("SIGNATURE_INCREMENTAL_OVERLAP_SLOT_MISMATCH")
            if int(prior.get("blockTime") or 0) != int(row.get("blockTime") or 0):
                raise RuntimeError("SIGNATURE_INCREMENTAL_OVERLAP_TIME_MISMATCH")
            if prior.get("transactionIndex") is not None:
                row["transactionIndex"] = int(prior["transactionIndex"])
                row["transactionIndexSource"] = prior.get(
                    "transactionIndexSource", "PERSISTED_EXACT_CANONICAL_ORDER"
                )
        # getSignaturesForAddress pagination is eventually complete rather than
        # strictly append-only when an address has very dense same-slot traffic.
        # A later query can therefore reveal a new finalized signature below the
        # prior newest slot. Merge that immutable backfill; contradictions for a
        # signature already in the ledger still fail closed above.

    bounded_fetched = [
        row for row in fetched
        if int(row.get("blockTime") or 0) >= population_start_block_time
    ]
    historical_backfills = [
        row for row in bounded_fetched
        if str(row.get("signature") or "") not in baseline_by_signature
        and int(row.get("slot") or 0) <= baseline_newest_slot
    ]
    merged = dedupe_signatures(baseline + bounded_fetched)
    write_signature_ledger(
        ledger_path, merged, population_start_block_time,
        {
            "source": "PERSISTED_BASELINE_PLUS_EXACT_REMOTE_APPEND",
            "priorLedgerSha256": prior_manifest["ledgerSha256"],
            "fetchedN": len(fetched),
            "overlapN": sum(
                str(row.get("signature") or "") in baseline_by_signature for row in fetched
            ),
            "historicalBackfillN": len(historical_backfills),
        },
    )
    return merged


def bootstrap_signature_ledger_from_transaction_cache(
    cache: pathlib.Path,
    ledger_path: pathlib.Path,
    population_start_block_time: int,
    successful_status_path: pathlib.Path,
) -> dict[str, Any]:
    if ledger_path.exists() or signature_ledger_manifest_path(ledger_path).exists():
        raise RuntimeError("SIGNATURE_LEDGER_ALREADY_EXISTS")
    status = json.loads(successful_status_path.read_text(encoding="utf-8"))
    if status.get("schema") != "2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_MATERIALIZER_V3_STATUS":
        raise RuntimeError("SIGNATURE_LEDGER_PROOF_STATUS_SCHEMA_MISMATCH")
    if status.get("outcomesRead") != 0 or status.get("submissions") != 0:
        raise RuntimeError("SIGNATURE_LEDGER_PROOF_NOT_OUTCOME_BLIND")
    status_mtime_ns = successful_status_path.stat().st_mtime_ns
    rows: list[dict[str, Any]] = []
    latest_cache_mtime_ns = 0
    for path in cache.glob("tx-*.json"):
        latest_cache_mtime_ns = max(latest_cache_mtime_ns, path.stat().st_mtime_ns)
        try:
            tx = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            raise RuntimeError(f"SIGNATURE_LEDGER_CACHE_MALFORMED:{path.name}") from error
        if not isinstance(tx, dict):
            raise RuntimeError(f"SIGNATURE_LEDGER_CACHE_NULL:{path.name}")
        signature = path.name[3:-5]
        transaction_signatures = (tx.get("transaction") or {}).get("signatures") or []
        if not transaction_signatures or str(transaction_signatures[0]) != signature:
            raise RuntimeError(f"SIGNATURE_LEDGER_CACHE_SIGNATURE_MISMATCH:{path.name}")
        block_time = int(tx.get("blockTime") or 0)
        slot = int(tx.get("slot") or 0)
        if not block_time or not slot:
            raise RuntimeError(f"SIGNATURE_LEDGER_CACHE_POSITION_MISSING:{path.name}")
        if block_time < population_start_block_time:
            continue
        rows.append({
            "signature": signature,
            "slot": slot,
            "blockTime": block_time,
            "err": (tx.get("meta") or {}).get("err"),
            "memo": None,
            "confirmationStatus": "finalized",
        })
    if latest_cache_mtime_ns > status_mtime_ns:
        raise RuntimeError("SIGNATURE_LEDGER_CACHE_NEWER_THAN_SUCCESS_PROOF")
    if not rows:
        raise RuntimeError("SIGNATURE_LEDGER_CACHE_EMPTY")
    # RPC blockTime is second-resolution. Existing materialization excludes the
    # fractional cohort-start second, so the first admissible second is start+1.
    if min(int(row["blockTime"]) for row in rows) > population_start_block_time + 1:
        raise RuntimeError("SIGNATURE_LEDGER_CACHE_START_BOUNDARY_NOT_COVERED")
    return write_signature_ledger(
        ledger_path, rows, population_start_block_time,
        {
            "source": "PRIOR_SUCCESSFUL_MATERIALIZER_TRANSACTION_CACHE",
            "successfulStatusSha256": file_sha256(successful_status_path),
            "successfulStatusAtUtc": status.get("atUtc"),
            "successfulRunStartedAtUtc": status.get("runStartedAtUtc"),
            "cacheFileN": len(rows),
        },
    )


def fetch_transaction(url: str, signature: str) -> dict[str, Any] | None:
    return rpc(url, "getTransaction", [signature, {
        "commitment": "finalized",
        "encoding": "json",
        "maxSupportedTransactionVersion": MAX_SUPPORTED_TRANSACTION_VERSION,
    }])


def b58decode(value: str) -> bytes:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    for character in value:
        number = number * 58 + alphabet.index(character)
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + raw


def signature_digest(signature: str) -> bytes:
    return hashlib.sha256(b58decode(signature)).digest()


def compact_arrivals(path: pathlib.Path, targets: set[bytes]) -> dict[bytes, tuple[int, int]]:
    arrivals: dict[bytes, tuple[int, int]] = {}
    with path.open("rb") as stream:
        if stream.read(len(COMPACT_MAGIC)) != COMPACT_MAGIC:
            raise RuntimeError("COMPACT_ARRIVAL_MAGIC_MISMATCH")
        while True:
            raw = stream.read(COMPACT_RECORD.size)
            if not raw:
                break
            if len(raw) != COMPACT_RECORD.size:
                break
            digest, received_ns, slot = COMPACT_RECORD.unpack(raw)
            if digest in targets:
                candidate = (int(received_ns), int(slot))
                if digest not in arrivals or candidate < arrivals[digest]:
                    arrivals[digest] = candidate
    return arrivals


def resolved_keys(tx: dict[str, Any]) -> list[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = [str(row.get("pubkey")) if isinstance(row, dict) else str(row) for row in message.get("accountKeys") or []]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    keys.extend(str(value) for value in loaded.get("writable") or [])
    keys.extend(str(value) for value in loaded.get("readonly") or [])
    return keys


def invoked_programs(tx: dict[str, Any]) -> tuple[set[str], set[str]]:
    keys = resolved_keys(tx)
    message = (tx.get("transaction") or {}).get("message") or {}

    def program_id(instruction: dict[str, Any]) -> str | None:
        if instruction.get("programId"):
            return str(instruction["programId"])
        index = instruction.get("programIdIndex")
        return keys[int(index)] if index is not None and int(index) < len(keys) else None

    top = {program for row in message.get("instructions") or [] if (program := program_id(row))}
    inner: set[str] = set()
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        inner.update(program for row in group.get("instructions") or [] if (program := program_id(row)))
    return top, inner


def wallet_token_states(tx: dict[str, Any]) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    meta = tx.get("meta") or {}
    owner_by_index: dict[int, str] = {}
    decimals: dict[str, int] = {}
    for side in ("preTokenBalances", "postTokenBalances"):
        for row in meta.get(side) or []:
            if row.get("owner"):
                owner_by_index[int(row["accountIndex"])] = str(row["owner"])
    output: list[dict[str, int]] = []
    for side in ("preTokenBalances", "postTokenBalances"):
        amounts: dict[str, int] = {}
        for row in meta.get(side) or []:
            if str(row.get("owner") or owner_by_index.get(int(row["accountIndex"]), "")) != WALLET:
                continue
            mint = str(row.get("mint") or "")
            raw = row.get("uiTokenAmount") or {}
            if not mint or raw.get("amount") is None:
                continue
            amounts[mint] = amounts.get(mint, 0) + int(raw["amount"])
            decimals[mint] = int(raw.get("decimals") or 0)
        output.append(amounts)
    return output[0], output[1], decimals


def wallet_native_delta(tx: dict[str, Any]) -> tuple[int | None, int]:
    keys = resolved_keys(tx)
    try:
        index = keys.index(WALLET)
    except ValueError:
        return None, int((tx.get("meta") or {}).get("fee") or 0)
    meta = tx.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if index >= len(pre) or index >= len(post):
        return None, int(meta.get("fee") or 0)
    return int(post[index]) - int(pre[index]), int(meta.get("fee") or 0)


def route_for(tx: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    top, inner = invoked_programs(tx)
    if PUMP in top:
        route = "PUMP_BONDING_CURVE"
    elif PUMPSWAP in top:
        route = "PUMPSWAP"
    elif top & JUPITER:
        route = "JUPITER_OR_ROUTER"
    elif PUMP in inner:
        route = "PUMP_CPI"
    elif PUMPSWAP in inner:
        route = "PUMPSWAP_CPI"
    elif inner & JUPITER:
        route = "JUPITER_OR_ROUTER"
    else:
        route = "OTHER_DEX"
    return route, {
        "topLevelPrograms": sorted(top),
        "innerPrograms": sorted(inner),
        "nonBenignPrograms": sorted((top | inner) - BENIGN_PROGRAMS),
    }


def write_jsonl_gz(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def read_detector(path: pathlib.Path, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("rb") as stream:
        for raw in stream:
            try:
                row = json.loads(raw)
            except Exception:
                continue
            timestamp = int(row.get("detectedAtMs") or 0)
            if start_ms <= timestamp <= end_ms:
                rows.append(row)
    return rows


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=False)
    start_ms = parse_ms(args.start)
    end_ms = parse_ms(args.end)
    if end_ms <= start_ms:
        raise RuntimeError("INVALID_WINDOW")
    contract = {
        "schema": SCHEMA + "_CONTRACT",
        "wallet": WALLET,
        "startUtcInclusive": args.start,
        "endUtcInclusive": args.end,
        "independentDenominatorAuthority": "finalized wallet signatures plus canonical token balance transitions",
        "windowClock": "canonical finalized blockTime; AllenHark local receipt retained as boundary sensitivity evidence",
        "detectorReadAfterDenominatorFreeze": True,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(args.output / "contract.json", contract)

    env = load_env(args.env)
    url = env["SECURE_RPC_URL"]
    signatures = fetch_signatures(url, start_ms // 1000 - 180)
    signature_window = [
        row for row in signatures
        if start_ms // 1000 - 180 <= int(row.get("blockTime") or 0) <= end_ms // 1000 + 180
    ]
    write_jsonl_gz(args.output / "wallet-signatures-bounded-context.jsonl.gz", signature_window)
    successful = [row for row in signature_window if row.get("err") is None]

    transactions: dict[str, dict[str, Any] | None] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        future_map = {
            executor.submit(fetch_transaction, url, str(row["signature"])): str(row["signature"])
            for row in successful
        }
        for future in concurrent.futures.as_completed(future_map):
            signature = future_map[future]
            transactions[signature] = future.result()
    write_jsonl_gz(args.output / "wallet-finalized-success-transactions.jsonl.gz", [
        {"signature": str(row["signature"]), "transaction": transactions.get(str(row["signature"]))}
        for row in successful
    ])

    digests = {signature_digest(str(row["signature"])) for row in signature_window}
    arrivals = compact_arrivals(args.compact_arrivals, digests)
    rows: list[dict[str, Any]] = []
    seen_trade_mints: set[str] = set()
    missing_transactions = 0
    missing_arrivals = 0
    window_success_missing_arrivals = 0
    canonical_start_ms = ((start_ms + 999) // 1000) * 1000
    canonical_end_ms = (end_ms // 1000) * 1000
    for signature_row in signature_window:
        if signature_row.get("err") is not None:
            continue
        signature = str(signature_row["signature"])
        tx = transactions.get(signature)
        if tx is None:
            missing_transactions += 1
            continue
        arrival = arrivals.get(signature_digest(signature))
        block_time_ms = int(tx.get("blockTime") or signature_row.get("blockTime") or 0) * 1000
        if not arrival:
            missing_arrivals += 1
        if not canonical_start_ms <= block_time_ms <= canonical_end_ms:
            continue
        if not arrival:
            window_success_missing_arrivals += 1
        observed_ms = block_time_ms
        pre, post, decimals = wallet_token_states(tx)
        native_delta, fee = wallet_native_delta(tx)
        route, route_evidence = route_for(tx)
        for mint in sorted(set(pre) | set(post)):
            before = int(pre.get(mint, 0))
            after = int(post.get(mint, 0))
            delta = after - before
            if delta <= 0:
                continue
            if mint == WSOL:
                classification = "NON_TRADE"
                is_trade = False
            elif route in {"PUMP_BONDING_CURVE", "PUMPSWAP", "PUMP_CPI", "PUMPSWAP_CPI", "JUPITER_OR_ROUTER"}:
                is_trade = True
                if before > 0:
                    classification = "ADD_WHILE_ALREADY_EXPOSED"
                elif mint in seen_trade_mints:
                    classification = "FLAT_TO_REENTRY"
                else:
                    classification = "NEW_FROM_FLAT"
            elif route_evidence["nonBenignPrograms"] and native_delta is not None and native_delta < -(fee + 10_000):
                is_trade = True
                if before > 0:
                    classification = "ADD_WHILE_ALREADY_EXPOSED"
                elif mint in seen_trade_mints:
                    classification = "FLAT_TO_REENTRY"
                else:
                    classification = "NEW_FROM_FLAT"
            elif not route_evidence["nonBenignPrograms"]:
                classification = "TRANSFER_RECEIPT"
                is_trade = False
            else:
                classification = "UNKNOWN"
                is_trade = False
            if is_trade and before == 0:
                seen_trade_mints.add(mint)
            rows.append({
                "schema": SCHEMA + "_ACQUISITION",
                "signature": signature,
                "mint": mint,
                "slot": int(tx.get("slot") or signature_row.get("slot") or 0),
                "transactionIndex": signature_row.get("transactionIndex"),
                "blockTimeUtc": iso_from_ms(block_time_ms),
                "observedAtUtc": iso_from_ms(observed_ms),
                "windowClock": "FINALIZED_BLOCKTIME",
                "allenharkArrivalAtUtc": iso_from_ms(arrival[0] // 1_000_000) if arrival else None,
                "allenharkArrivalInExactWindow": bool(
                    arrival and start_ms <= arrival[0] // 1_000_000 <= end_ms
                ),
                "preRaw": before,
                "postRaw": after,
                "deltaRaw": delta,
                "decimals": decimals.get(mint),
                "nativeDeltaLamports": native_delta,
                "transactionFeeLamports": fee,
                "classification": classification,
                "route": route if is_trade else None,
                "isTradeAcquisition": is_trade,
                "isIndependentEntryEpisode": is_trade and classification in {"NEW_FROM_FLAT", "FLAT_TO_REENTRY"},
                "routeEvidence": route_evidence,
            })
    rows.sort(key=lambda row: (row["observedAtUtc"], row["slot"], row.get("transactionIndex") or -1, row["signature"], row["mint"]))
    denominator_path = args.output / "independent-denominator.jsonl.gz"
    write_jsonl_gz(denominator_path, rows)
    freeze = {
        "schema": SCHEMA + "_DENOMINATOR_FREEZE",
        "createdAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "contractSha256": file_sha256(args.output / "contract.json"),
        "denominatorSha256": file_sha256(denominator_path),
        "acquisitionRows": len(rows),
        "detectorRead": False,
    }
    atomic_json(args.output / "denominator-freeze.json", freeze)

    # The current detector is intentionally opened only after the independent denominator is frozen.
    detector = read_detector(args.detector, start_ms, end_ms)
    detector_signatures = {str(row.get("signature") or "") for row in detector}
    trades = [row for row in rows if row["isTradeAcquisition"]]
    episodes = [row for row in trades if row["isIndependentEntryEpisode"]]
    matched = [row for row in episodes if row["signature"] in detector_signatures]
    missed = [row for row in episodes if row["signature"] not in detector_signatures]
    classes = Counter(row["classification"] for row in rows)
    routes = Counter(row["route"] for row in trades)
    missed_routes = Counter(row["route"] for row in missed)
    elapsed_hours = (end_ms - start_ms) / 3_600_000
    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "contractSha256": freeze["contractSha256"],
        "denominatorSha256": freeze["denominatorSha256"],
        "window": {"startUtc": args.start, "endUtc": args.end, "elapsedHours": elapsed_hours},
        "walletSignatureContextN": len(signature_window),
        "finalizedSuccessfulTransactionsN": len(successful),
        "missingFinalizedTransactionsN": missing_transactions,
        "signatureRowsMissingAllenharkArrivalN": missing_arrivals,
        "windowSuccessfulTransactionsMissingAllenharkArrivalN": window_success_missing_arrivals,
        "positiveTokenAcquisitionRowsN": len(rows),
        "classifications": dict(sorted(classes.items())),
        "trueTradeAcquisitions": len(trades),
        "trueNewFromFlat": classes["NEW_FROM_FLAT"],
        "trueFlatToReentry": classes["FLAT_TO_REENTRY"],
        "trueAddWhileExposed": classes["ADD_WHILE_ALREADY_EXPOSED"],
        "trueIndependentEntryEpisodes": len(episodes),
        "trueIndependentEntryEpisodesPerHour": len(episodes) / elapsed_hours,
        "arrivalClockSensitivityTradeAcquisitionsN": sum(
            row["allenharkArrivalInExactWindow"] for row in trades
        ),
        "boundarySecondTradeAcquisitionsN": sum(
            int(dt.datetime.fromisoformat(row["blockTimeUtc"].replace("Z", "+00:00")).timestamp() * 1000)
            in {canonical_start_ms, canonical_end_ms}
            for row in trades
        ),
        "routes": dict(sorted(routes.items())),
        "currentDetectorRowsInWindow": len(detector),
        "currentDetectorUniqueSignaturesInWindow": len(detector_signatures),
        "currentDetectorMatched": len(matched),
        "currentDetectorMissed": len(missed),
        "currentDetectorRecallPct": 100.0 * len(matched) / len(episodes) if episodes else None,
        "missesByRoute": dict(sorted(missed_routes.items())),
        "missedEpisodes": missed,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(args.output / "report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--detector", type=pathlib.Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
