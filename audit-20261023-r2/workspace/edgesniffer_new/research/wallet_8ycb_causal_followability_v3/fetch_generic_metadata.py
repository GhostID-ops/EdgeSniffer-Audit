#!/usr/bin/env python3
"""Fetch/decode matched generic CREATE transactions into the isolated V3 lane."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import time
from pathlib import Path
from typing import Any

import requests


PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
INITIAL_VSOL = 30_000_000_000
INITIAL_VTOK = 1_073_000_000_000_000
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def b58decode(value: str) -> bytes:
    number = 0
    for char in value:
        number = number * 58 + ALPHABET.index(char)
    data = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + data


def b58encode(data: bytes) -> str:
    zeros = len(data) - len(data.lstrip(b"\0"))
    number = int.from_bytes(data, "big")
    chars = []
    while number:
        number, remainder = divmod(number, 58)
        chars.append(ALPHABET[remainder])
    return "1" * zeros + "".join(reversed(chars))


def borsh_string(data: bytes, offset: int) -> tuple[str, int]:
    size = int.from_bytes(data[offset:offset + 4], "little")
    offset += 4
    if size > 20_000 or offset + size > len(data):
        raise ValueError("invalid borsh string")
    return data[offset:offset + size].decode("utf-8"), offset + size


def decode_create(tx: dict[str, Any]) -> dict[str, Any] | None:
    for instruction in tx.get("instructions") or []:
        if instruction.get("programId") != PUMP_PROGRAM or len(instruction.get("data") or "") < 50:
            continue
        try:
            data = b58decode(instruction["data"])
            offset = 8
            name, offset = borsh_string(data, offset)
            symbol, offset = borsh_string(data, offset)
            uri, offset = borsh_string(data, offset)
            creator = b58encode(data[offset:offset + 32])
            mint = instruction.get("accounts", [None])[0]
            return {"mint": mint, "name": name, "symbol": symbol, "metadata_uri": uri, "creator": creator}
        except Exception:
            continue
    return None


def creator_token_delta(tx: dict[str, Any], creator: str, mint: str) -> int:
    total = 0
    for account in tx.get("accountData") or []:
        for change in account.get("tokenBalanceChanges") or []:
            if change.get("userAccount") != creator or change.get("mint") != mint:
                continue
            raw = (change.get("rawTokenAmount") or {}).get("tokenAmount")
            if raw is not None:
                total += int(raw)
    return total


def inferred_principal(tokens: int) -> float:
    if tokens <= 0 or tokens >= INITIAL_VTOK:
        return 0.0
    post_vtok = INITIAL_VTOK - tokens
    gross = (INITIAL_VSOL * INITIAL_VTOK) // post_vtok - INITIAL_VSOL
    return gross / 1_000_000_000


def fetch_batch(session: requests.Session, keys: list[str], signatures: list[str], offset: int) -> list[dict[str, Any]]:
    last_error = "unknown"
    for attempt in range(18):
        key = keys[(offset + attempt) % len(keys)]
        try:
            response = session.post(
                f"https://api.helius.xyz/v0/transactions/?api-key={key}",
                json={"transactions": signatures}, timeout=120,
            )
            if response.status_code == 200:
                value = response.json()
                if isinstance(value, list):
                    return value
                last_error = "non-list response"
            else:
                last_error = f"HTTP {response.status_code}: {response.text[:160]}"
        except Exception as exc:
            last_error = repr(exc)
        time.sleep(min(15.0, 0.75 + attempt))
    raise RuntimeError(f"enhanced transaction fetch failed: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v3", required=True)
    args = parser.parse_args()
    base = Path(args.v3)
    load_env(Path("/root/bot/EdgeSniffer/.env"))
    keys = [value.strip() for value in [os.environ.get("HELIUS_API_KEY", ""), *os.environ.get("HELIUS_API_KEYS", "").split(",")] if value.strip()]
    keys = list(dict.fromkeys(keys))
    if not keys:
        raise RuntimeError("no Helius key available")
    signatures = [line.strip() for line in (base / "generic_control_signatures.txt").read_text().splitlines() if line.strip()]
    with gzip.open(base / "generic_control_selection.jsonl.gz", "rt", encoding="utf-8") as handle:
        selections = {row["create_signature"]: row for row in map(json.loads, handle) if row}

    transactions = []
    session = requests.Session()
    for index in range(0, len(signatures), 100):
        transactions.extend(fetch_batch(session, keys, signatures[index:index + 100], index // 100))
        print(f"fetched={min(index + 100, len(signatures))}/{len(signatures)} returned={len(transactions)}", flush=True)
    transactions = list({tx.get("signature"): tx for tx in transactions if tx.get("signature")}.values())

    rows = []
    decode_errors = []
    for tx in transactions:
        decoded = decode_create(tx)
        if not decoded:
            decode_errors.append({"signature": tx.get("signature"), "reason": "create_decode_failed"})
            continue
        tokens = creator_token_delta(tx, decoded["creator"], decoded["mint"])
        selection = selections.get(tx["signature"], {})
        rows.append({
            **selection,
            **decoded,
            "fee_payer": tx.get("feePayer"),
            "transaction_error": tx.get("transactionError"),
            "creator_initial_tokens_raw": max(0, tokens),
            "creator_atomic_initial_buy": tokens > 0,
            "creator_initial_principal_sol_inferred": inferred_principal(tokens),
            "name_length": len(decoded["name"]),
            "name_word_count": len(decoded["name"].split()),
            "name_upper_ratio": sum(char.isupper() for char in decoded["name"]) / max(1, sum(char.isalpha() for char in decoded["name"])),
            "symbol_length": len(decoded["symbol"]),
            "metadata_uri_domain": decoded["metadata_uri"].split("/")[2].lower() if "://" in decoded["metadata_uri"] else None,
            "metadata_source": "onchain_create_instruction",
        })
    rows.sort(key=lambda row: (row.get("create_ts_ms", 0), row.get("create_signature", "")))
    with gzip.open(base / "generic_control_metadata.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    with gzip.open(base / "generic_control_enhanced_transactions.jsonl.gz", "wt", encoding="utf-8") as handle:
        for tx in transactions:
            handle.write(json.dumps(tx, separators=(",", ":"), sort_keys=True) + "\n")
    summary = {
        "requested_signatures": len(signatures),
        "returned_unique_transactions": len(transactions),
        "decoded_create_rows": len(rows),
        "decode_errors": len(decode_errors),
        "atomic_creator_initial_buy_rows": sum(row["creator_atomic_initial_buy"] for row in rows),
        "no_creator_initial_buy_rows": sum(not row["creator_atomic_initial_buy"] for row in rows),
    }
    (base / "generic_control_metadata_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (base / "generic_control_metadata_errors.json").write_text(json.dumps(decode_errors, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
