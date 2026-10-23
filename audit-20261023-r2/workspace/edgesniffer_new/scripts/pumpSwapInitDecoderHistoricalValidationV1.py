#!/usr/bin/env python3
"""Outcome-blind historical validation for PumpSwap init decoder V1.

The sample is used only to test finalized instruction/event identity decoding.
It never writes historical rows into the future-only registry.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
from typing import Any

import launchLabEvidenceReconcilerV1 as common
import launchLabExactEconomicsV1 as transaction_layout
import pumpSwapInitDecoderV1 as decoder


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
    os.replace(temporary, path)


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def tx_cache_path(cache: pathlib.Path, signature: str) -> pathlib.Path:
    return cache / "transactions" / f"{signature}.json"


def fetch(rpc: common.Rpc, cache: pathlib.Path, signature: str) -> dict[str, Any] | None:
    path = tx_cache_path(cache, signature)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    value = rpc.call("getTransaction", [signature, {
        "commitment": "finalized", "encoding": "json", "maxSupportedTransactionVersion": 1,
    }])
    if value is not None:
        atomic_json(path, value)
    return value


def idl_witness(tx: dict[str, Any]) -> bool:
    """Independent minimal witness: raw create_pool + current event discriminator."""
    if (tx.get("meta") or {}).get("err") is not None:
        return False
    raw_create = False
    keys = transaction_layout.resolved_keys(tx)
    message = (tx.get("transaction") or {}).get("message") or {}
    groups = [message.get("instructions") or []]
    groups.extend(group.get("instructions") or [] for group in (tx.get("meta") or {}).get("innerInstructions") or [])
    for group in groups:
        for instruction in group:
            if transaction_layout.instruction_program(instruction, keys) != decoder.PUMPSWAP_PROGRAM_ID:
                continue
            try:
                data = decoder.base58_decode(str(instruction.get("data") or ""))
            except Exception:
                continue
            raw_create = raw_create or data.startswith(decoder.CREATE_POOL)
    event = False
    for line in (tx.get("meta") or {}).get("logMessages") or []:
        if "Program data: " not in str(line):
            continue
        try:
            data = base64.b64decode(str(line).split("Program data: ", 1)[1].strip(), validate=True)
        except Exception:
            continue
        event = event or (len(data) == 344 and data.startswith(decoder.CREATE_POOL_EVENT))
    return raw_create and event


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    environment = {**common.load_env(args.env), **os.environ}
    url = environment.get("PUMP_EXACT_LEDGER_RPC_URL") or environment.get("SECURE_RPC_URL")
    if not url:
        raise RuntimeError("PUMP_EXACT_LEDGER_RPC_URL/SECURE_RPC_URL missing")
    rpc = common.Rpc(url)
    signatures: list[dict[str, Any]] = []
    before: str | None = None
    for _ in range(args.pages):
        options: dict[str, Any] = {"limit": args.signature_limit, "commitment": "finalized"}
        if before:
            options["before"] = before
        page = rpc.call("getSignaturesForAddress", [decoder.PUMPSWAP_PROGRAM_ID, options]) or []
        if not page:
            break
        signatures.extend(page)
        before = str(page[-1].get("signature") or "") or None
        if len(page) < args.signature_limit or before is None:
            break
    result = {
        "schema": "PUMPSWAP_INIT_DECODER_HISTORICAL_VALIDATION_V1",
        "generatedAtUtc": now_utc(), "outcomesRead": 0, "submissions": 0,
        "source": "FINALIZED_RPC_PUMPSWAP_PROGRAM_SAMPLE",
        "candidateSignatureN": len(signatures), "fetchedN": 0, "fetchUnavailableN": 0, "fetchErrorN": 0,
        "successfulN": 0, "idlWitnessN": 0, "decoderAcceptedN": 0,
        "decoderRejectedWitnessN": 0, "decoderFalsePositiveN": 0,
        "canonicalPumpMigrationN": 0, "unsupportedOrAmbiguousN": 0,
        "accepted": [], "falsePositives": [], "falseNegatives": [], "ambiguous": [],
    }
    for row in signatures:
        signature = str(row.get("signature") or "")
        if not signature:
            continue
        try:
            tx = fetch(rpc, args.output / "rpc-cache", signature)
        except Exception as exc:
            result["fetchErrorN"] += 1
            result.setdefault("fetchErrors", []).append({"signature": signature, "error": str(exc)[:180]})
            continue
        if tx is None:
            result["fetchUnavailableN"] += 1
            continue
        result["fetchedN"] += 1
        if (tx.get("meta") or {}).get("err") is None:
            result["successfulN"] += 1
        witness = idl_witness(tx)
        result["idlWitnessN"] += int(witness)
        accepted = None
        error = None
        try:
            accepted = decoder.validate_finalized_init(tx)
        except decoder.PumpSwapInitDecodeError as exc:
            error = str(exc)
        if accepted is not None:
            record = {"signature": signature, "pool": accepted.pool, "mint": accepted.mint,
                      "quoteMint": accepted.quote_mint, "origin": accepted.origin,
                      "canonicalPumpMigration": accepted.canonical_pump_migration}
            result["accepted"].append(record)
            result["decoderAcceptedN"] += 1
            result["canonicalPumpMigrationN"] += int(accepted.canonical_pump_migration)
            if not witness:
                result["decoderFalsePositiveN"] += 1
                result["falsePositives"].append(record)
        elif witness:
            result["decoderRejectedWitnessN"] += 1
            result["falseNegatives"].append({"signature": signature, "error": error})
        elif error:
            result["unsupportedOrAmbiguousN"] += 1
            result["ambiguous"].append({"signature": signature, "error": error})
    result["evidenceHash"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    atomic_json(args.output / "report.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--signature-limit", type=int, default=250)
    parser.add_argument("--pages", type=int, default=1)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
