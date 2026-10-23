#!/usr/bin/env python3
"""Receive-only native Pump curve state enrichment.

The native ShredStream observation already contains the causal receive clock
and transaction identity.  This bridge adds exact finalized Pump TradeEvent
state by signature.  It never changes the original observation and never
submits a transaction.  RPC is deliberately kept in this research bridge,
outside the execution hot path.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import dataclasses
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import exactCurveEconomicsV1 as curve
from materializeWalletRouteCensusV1 import curve_events_with_extension

TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TRANSFER_FEE_CONFIG_EXTENSION = 1

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


def exact_pump_observation(row: dict[str, Any]) -> bool:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    accounts = raw.get("sourceIxAccounts") or row.get("source_ix_accounts") or row.get("extra", {}).get("sourceIxAccounts") or []
    return bool(raw.get("bondingCurve") and PUMP_PROGRAM in accounts)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def rpc_call(url: str, signature: str, timeout: float) -> dict[str, Any]:
    body = json.dumps({"jsonrpc": "2.0", "id": signature, "method": "getTransaction",
                       "params": [signature, {"encoding": "json", "commitment": "finalized",
                                                "maxSupportedTransactionVersion": 0}]}).encode()
    request = urllib.request.Request(url, data=body, headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    result = json.loads(payload)
    if result.get("error"):
        raise RuntimeError(str(result["error"]))
    if not result.get("result"):
        raise RuntimeError("transaction not finalized or unavailable")
    return result["result"]


def rpc_account_info(url: str, address: str, timeout: float) -> dict[str, Any]:
    """Fetch finalized mint bytes in this offline/read-only bridge."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": f"mint:{address}", "method": "getAccountInfo",
        "params": [address, {"encoding": "base64", "commitment": "finalized"}],
    }).encode()
    request = urllib.request.Request(url, data=body, headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    result = json.loads(payload)
    if result.get("error"):
        raise RuntimeError(str(result["error"]))
    value = (result.get("result") or {}).get("value")
    data = value.get("data") if isinstance(value, dict) else None
    if not isinstance(data, list) or len(data) < 2 or data[1] != "base64":
        raise RuntimeError("mint account was not returned as base64")
    return {"owner": value.get("owner"), "lamports": value.get("lamports"), "data": data[0]}


def _decode_transfer_fee_config(payload: bytes, offset: int) -> dict[str, Any]:
    if len(payload) < 108:
        return {"complete": False, "payloadLength": len(payload), "rawHex": payload.hex()}

    def schedule(start: int) -> dict[str, int]:
        return {
            "epoch": int.from_bytes(payload[start:start + 8], "little"),
            "maximumFeeRaw": int.from_bytes(payload[start + 8:start + 16], "little"),
            "transferFeeBasisPoints": int.from_bytes(payload[start + 16:start + 18], "little"),
        }

    return {
        "complete": True,
        "payloadLength": len(payload),
        "transferFeeConfigAuthorityHex": payload[0:32].hex(),
        "withdrawWithheldAuthorityHex": payload[32:64].hex(),
        "withheldAmountRaw": int.from_bytes(payload[64:72], "little"),
        "olderTransferFee": schedule(72),
        "newerTransferFee": schedule(90),
        "sourceOffset": offset,
    }


def decode_token2022_mint_state(data_base64: str) -> dict[str, Any]:
    """Decode the Token-2022 mint TLV state without requiring an SDK."""
    raw = base64.b64decode(data_base64, validate=True)
    result: dict[str, Any] = {
        "schema": "TOKEN2022_MINT_EXTENSION_STATE_V1",
        "accountDataLength": len(raw),
        "baseMintLength": min(82, len(raw)),
        "extensionTypes": [],
        "transferFeeConfigPresent": False,
        "transferFeeConfigComplete": False,
        "stateSource": "FINALIZED_MINT_ACCOUNT",
    }
    offset = 82
    while offset + 4 <= len(raw):
        extension_type = int.from_bytes(raw[offset:offset + 2], "little")
        length = int.from_bytes(raw[offset + 2:offset + 4], "little")
        if extension_type == 0 and length == 0:
            result["tlvComplete"] = True
            break
        start, end = offset + 4, offset + 4 + length
        if end > len(raw):
            result["tlvComplete"] = False
            result["truncatedExtension"] = {"type": extension_type, "offset": offset, "declaredLength": length}
            break
        result["extensionTypes"].append({"type": extension_type, "length": length, "offset": offset})
        if extension_type == TRANSFER_FEE_CONFIG_EXTENSION:
            result["transferFeeConfigPresent"] = True
            config = _decode_transfer_fee_config(raw[start:end], offset)
            result["transferFeeConfigComplete"] = bool(config.get("complete"))
            result["transferFeeConfig"] = config
        offset = end
    else:
        result["tlvComplete"] = offset == len(raw) or offset + 4 > len(raw)
    result["complete"] = bool(result.get("tlvComplete", False) and
                                result["transferFeeConfigPresent"] == result["transferFeeConfigComplete"])
    return result


def token2022_balance_deltas(tx: dict[str, Any], mint: str) -> list[dict[str, Any]]:
    meta = tx.get("meta") or {}
    pre = {int(item.get("accountIndex")): item for item in (meta.get("preTokenBalances") or [])
           if item.get("mint") == mint and item.get("programId") == TOKEN_2022}
    post = {int(item.get("accountIndex")): item for item in (meta.get("postTokenBalances") or [])
            if item.get("mint") == mint and item.get("programId") == TOKEN_2022}
    result = []
    for index in sorted(set(pre) | set(post)):
        before = int(((pre.get(index) or {}).get("uiTokenAmount") or {}).get("amount") or 0)
        after = int(((post.get(index) or {}).get("uiTokenAmount") or {}).get("amount") or 0)
        item = post.get(index) or pre.get(index) or {}
        result.append({"accountIndex": index, "owner": item.get("owner"), "beforeRaw": before,
                       "afterRaw": after, "deltaRaw": after - before})
    return result


def token2022_state_record(row: dict[str, Any], tx: dict[str, Any], fetched_at_ms: int,
                           mint_account: dict[str, Any]) -> dict[str, Any]:
    mint = str(row.get("mint") or "")
    accounts = (row.get("extra") or {}).get("sourceIxAccounts") or []
    source_data = row.get("source_ix_data_base64") or ""
    state = decode_token2022_mint_state(mint_account["data"])
    return {
        "schema": "NATIVE_TOKEN2022_TRANSFER_FEE_STATE_V1",
        "capturedAtMs": fetched_at_ms,
        "nativeReceiveTsMs": timestamp_ms(row),
        "signature": row.get("transaction_signature") or row.get("signature"),
        "slot": int(row.get("source_slot") or 0),
        "mint": mint,
        "side": row.get("side") or (row.get("raw_record") or {}).get("side"),
        "tokenProgram": row.get("token_program"),
        "sourceIxAccounts": accounts,
        "sourceIxDataBase64": source_data,
        "sourceAccountCount": len(accounts),
        "canonicalPumpV2AccountShape": bool(len(accounts) >= 27 and len(accounts) > 3 and accounts[1] == mint and
                                             accounts[3] == TOKEN_2022 and accounts[26] == PUMP_PROGRAM),
        "mintAccountOwner": mint_account.get("owner"),
        "mintAccountLamports": mint_account.get("lamports"),
        "mintState": state,
        "observedBalanceDeltas": token2022_balance_deltas(tx, mint),
        "transactionSha256": sha256_bytes(json.dumps(tx, sort_keys=True, separators=(",", ":")).encode()),
    }


def load_seen_state_signatures(path: Path | None) -> set[str]:
    if not path or not path.exists():
        return set()
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value.get("signature"):
                seen.add(str(value["signature"]))
    return seen


def timestamp_ms(row: dict[str, Any]) -> int | None:
    for key in ("local_receive_ts_ms", "receive_ts_ms", "signal_receive_ts_ms"):
        if row.get(key) is not None:
            try:
                return int(row[key])
            except (TypeError, ValueError):
                return None
    return None


def matching_events(tx: dict[str, Any], row: dict[str, Any]) -> list[curve.TradeEvent]:
    logs = ((tx.get("meta") or {}).get("logMessages") or [])
    events, _variant = curve_events_with_extension(tx)
    mint = str(row.get("mint") or row.get("raw_record", {}).get("mint") or "")
    side = str(row.get("side") or row.get("raw_record", {}).get("side") or "").lower()
    return [event for event in events if event.mint == mint and (event.is_buy == (side == "buy"))
            and event.quote_mint in curve.SOL_QUOTE_MINTS]


def enrich_row(row: dict[str, Any], tx: dict[str, Any], fetched_at_ms: int,
               mint_account: dict[str, Any] | None = None) -> dict[str, Any]:
    events = matching_events(tx, row)
    if len(events) != 1:
        raise RuntimeError(f"expected one exact Pump event, found {len(events)}")
    event = events[0]
    received = timestamp_ms(row)
    if received is None:
        raise RuntimeError("native receive timestamp missing")
    output = dict(row)
    output["canonical_state"] = {
        "schema": "PUMP_CURVE_TRADE_EVENT_STATE_V1",
        "route": "PUMP_BONDING_CURVE_TOP_LEVEL",
        "state_source": "FINALIZED_PUMP_TRADE_EVENT",
        "state_observed_at_ms": received,
        "finalized_block_time_ms": int(tx.get("blockTime") or 0) * 1000,
        "slot": int(tx.get("slot") or 0),
        "pre_state": dataclasses.asdict(event.pre_state),
        "post_state": dataclasses.asdict(event.post_state),
        "fee_basis_points": event.fee_basis_points,
        "creator_fee_basis_points": event.creator_fee_basis_points,
        "cashback_fee_basis_points": event.cashback_fee_basis_points,
        "quote_mint": event.quote_mint,
    }
    output["state_bridge"] = {
        "schema": "NATIVE_PUMP_CURVE_STATE_BRIDGE_V1",
        "native_receive_ts_ms": received,
        "finalized_signature": row.get("transaction_signature") or row.get("signature"),
        "finalized_slot": int(tx.get("slot") or 0),
        "fetched_at_ms": fetched_at_ms,
        "rpc_commitment": "finalized",
        "raw_transaction_sha256": sha256_bytes(json.dumps(tx, sort_keys=True, separators=(",", ":")).encode()),
    }
    if row.get("token_program") == TOKEN_2022:
        if not mint_account:
            raise RuntimeError("Token-2022 mint account state unavailable")
        token_state = decode_token2022_mint_state(mint_account["data"])
        if not token_state.get("complete"):
            raise RuntimeError("Token-2022 mint extension state incomplete")
        token_state.update({
            "mint": str(row.get("mint") or ""),
            "mintAccountOwner": mint_account.get("owner"),
            "mintAccountLamports": mint_account.get("lamports"),
            "mintAccountFetchedAtMs": fetched_at_ms,
            "observedBalanceDeltas": token2022_balance_deltas(tx, str(row.get("mint") or "")),
        })
        output["token2022_state"] = token_state
    return output


def load_line(handle: Any) -> dict[str, Any] | None:
    line = handle.readline()
    if not line:
        return None
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def run(args: argparse.Namespace) -> None:
    rpc_url = args.rpc_url or os.environ.get(args.rpc_url_env, "")
    if not rpc_url:
        raise SystemExit(f"missing --rpc-url or ${args.rpc_url_env}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    state_path = args.state
    offset = 0
    if state_path.exists():
        offset = int(json.loads(state_path.read_text(encoding="utf-8")).get("input_offset", 0))
    stats = {"read": 0, "enriched": 0, "rejected": 0, "rpc_errors": 0, "duplicates": 0, "pending": 0}
    seen: set[str] = set()
    pending: dict[str, dict[str, Any]] = {}
    if state_path.exists():
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        for item in saved.get("pending_rows") or []:
            signature = str(item.get("signature") or "")
            row = item.get("row")
            if signature and isinstance(row, dict) and exact_pump_observation(row):
                pending[signature] = {"row": row, "attempt": int(item.get("attempt") or 0), "next_ms": int(item.get("next_ms") or 0)}
    errors_path = args.errors_out or args.output.with_suffix(".errors.jsonl")
    token2022_state_path = args.token2022_state_output
    if token2022_state_path:
        token2022_state_path.parent.mkdir(parents=True, exist_ok=True)
    token2022_seen = load_seen_state_signatures(token2022_state_path)
    with args.input.open("r", encoding="utf-8") as source, args.output.open("a", encoding="utf-8") as output, errors_path.open("a", encoding="utf-8") as errors:
        if args.start_at_end and not state_path.exists():
            source.seek(0, 2)
            offset = source.tell()
        source.seek(offset)
        while True:
            batch: list[tuple[str, dict[str, Any], int]] = []
            while len(batch) < args.batch_size:
                row = load_line(source)
                if row is None:
                    break
                stats["read"] += 1
                signature = str(row.get("transaction_signature") or row.get("signature") or row.get("raw_record", {}).get("signature") or "")
                if not signature or signature in seen or signature in pending:
                    stats["duplicates"] += 1
                    continue
                raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
                if not exact_pump_observation(row):
                    continue
                pending[signature] = {"row": row, "attempt": 0, "next_ms": int(time.time() * 1000) + args.finality_delay_sec * 1000}
            now_ms = int(time.time() * 1000)
            for signature, item in list(pending.items()):
                if len(batch) >= args.batch_size:
                    break
                if int(item.get("next_ms") or 0) <= now_ms:
                    batch.append((signature, item["row"], int(item.get("attempt") or 0)))
            if not batch:
                if args.once:
                    break
                time.sleep(args.poll_ms / 1000)
                continue
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(rpc_call, rpc_url, signature, args.timeout): (signature, row, attempt) for signature, row, attempt in batch}
                for future in concurrent.futures.as_completed(futures):
                    signature, row, attempt = futures[future]
                    try:
                        transaction = future.result()
                        fetched_at_ms = int(time.time() * 1000)
                        mint_account = None
                        if row.get("token_program") == TOKEN_2022:
                            mint_account = rpc_account_info(rpc_url, str(row.get("mint") or ""), args.timeout)
                            if token2022_state_path:
                                signature_key = str(row.get("transaction_signature") or row.get("signature") or "")
                                if signature_key and signature_key not in token2022_seen:
                                    state_record = token2022_state_record(row, transaction, fetched_at_ms, mint_account)
                                    with token2022_state_path.open("a", encoding="utf-8") as token_state:
                                        token_state.write(json.dumps(state_record, sort_keys=True) + "\n")
                                        token_state.flush()
                                    token2022_seen.add(signature_key)
                        enriched = enrich_row(row, transaction, fetched_at_ms, mint_account)
                        output.write(json.dumps(enriched, sort_keys=True) + "\n")
                        stats["enriched"] += 1
                        pending.pop(signature, None)
                        seen.add(signature)
                    except Exception as error:
                        transaction = None
                        try:
                            transaction = future.result()
                        except Exception:
                            stats["rpc_errors"] += 1
                        terminal = bool(isinstance(transaction, dict) and (transaction.get("meta") or {}).get("err")) or "expected one exact Pump event" in str(error)
                        if terminal:
                            errors.write(json.dumps({"schema": "NATIVE_PUMP_CURVE_STATE_BRIDGE_REJECTION_V1", "signature": signature,
                                                     "reason": str(error), "finalized": isinstance(transaction, dict), "row": row}, sort_keys=True) + "\n")
                            pending.pop(signature, None)
                        else:
                            pending[signature] = {"row": row, "attempt": attempt + 1,
                                                  "next_ms": int(time.time() * 1000) + args.retry_ms}
                        stats["rejected"] += 1
                output.flush()
                errors.flush()
            offset = source.tell()
            stats["pending"] = len(pending)
            state_path.write_text(json.dumps({"schema": "NATIVE_PUMP_CURVE_STATE_BRIDGE_STATE_V1", "input_offset": offset,
                                              "stats": stats,
                                              "pending_rows": [{"signature": sig, "row": item["row"], "attempt": item["attempt"], "next_ms": item["next_ms"]}
                                                               for sig, item in pending.items()]}, sort_keys=True) + "\n", encoding="utf-8")
            if args.once and source.tell() == source.stat().st_size:
                break
    print(json.dumps(stats, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--rpc-url")
    parser.add_argument("--rpc-url-env", default="SECURE_RPC_URL")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--poll-ms", type=int, default=250)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--finality-delay-sec", type=int, default=15)
    parser.add_argument("--retry-ms", type=int, default=5000)
    parser.add_argument("--errors-out", type=Path)
    parser.add_argument("--token2022-state-output", type=Path)
    parser.add_argument("--start-at-end", action="store_true")
    parser.add_argument("--once", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
