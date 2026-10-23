#!/usr/bin/env python3
"""Outcome-blind protocol inventory for non-SOL 2CQ acquisition families."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
from typing import Any

import twoCqTrueActivityDenominatorAuditV1 as denominator


SCHEMA = "2CQ_ROUTE_PROTOCOL_INVENTORY_V1"
KNOWN_PROGRAMS = {
    denominator.PUMP: "PUMP_BONDING_CURVE",
    denominator.PUMPSWAP: "PUMPSWAP",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "JUPITER_V6",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "JUPITER_V4",
    "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj": "RAYDIUM_LAUNCHLAB",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "RAYDIUM_CPMM",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "RAYDIUM_CLMM",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "RAYDIUM_AMM_V4",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "METEORA_DLMM",
    "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN": "METEORA_DBC",
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG": "METEORA_DAMM_V2",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "ORCA_WHIRLPOOL",
}
PUMP_FEE_PROGRAM = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
    os.replace(temporary, path)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def protocol_candidates(top: set[str], inner: set[str], family: str) -> list[str]:
    invoked = top | inner
    ignored = set(denominator.BENIGN_PROGRAMS) | {
        denominator.PUMP,
        denominator.PUMPSWAP,
        PUMP_FEE_PROGRAM,
        *denominator.JUPITER,
    }
    candidates = invoked - ignored
    if family == "PUMPSWAP":
        return [denominator.PUMPSWAP]
    if family == "NON_SOL_PUMP_V2":
        return [denominator.PUMP]
    return sorted(candidates)


def run(args: argparse.Namespace) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{args.v2_state}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    source_rows = list(connection.execute(
        "SELECT signature,mint,route,economic_family,detail_json "
        "FROM current_decisions ORDER BY block_time_ms,slot,transaction_index,signature"
    ))
    connection.close()

    rows: list[dict[str, Any]] = []
    family_counts: collections.Counter[str] = collections.Counter()
    family_missing: collections.Counter[str] = collections.Counter()
    family_programs: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    quote_mints: collections.Counter[str] = collections.Counter()

    for source in source_rows:
        signature = str(source["signature"])
        family = str(source["economic_family"])
        family_counts[family] += 1
        detail = json.loads(str(source["detail_json"]))
        if family == "NON_SOL_PUMP_V2":
            for quote_mint in (detail.get("economicFamily") or {}).get("quoteMints") or []:
                quote_mints[str(quote_mint)] += 1
        path = args.wallet_rpc_cache / f"tx-{signature}.json"
        if not path.exists():
            family_missing[family] += 1
            continue
        tx = json.loads(path.read_text(encoding="utf-8"))
        top, inner = denominator.invoked_programs(tx)
        candidates = protocol_candidates(top, inner, family)
        for program in candidates:
            family_programs[family][program] += 1
        rows.append({
            "schema": SCHEMA + "_ROW",
            "signature": signature,
            "mint": str(source["mint"]),
            "route": str(source["route"]),
            "economicFamily": family,
            "topLevelPrograms": sorted(top),
            "innerPrograms": sorted(inner),
            "underlyingProgramCandidates": candidates,
            "knownPrograms": [
                {"programId": program, "name": KNOWN_PROGRAMS[program]}
                for program in sorted(top | inner) if program in KNOWN_PROGRAMS
            ],
            "outcomesRead": 0,
            "validationRowsRead": 0,
            "submissions": 0,
        })

    output_rows = args.output / "route-protocol-inventory.jsonl"
    temporary = output_rows.with_suffix(output_rows.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("wb") as stream:
        for row in rows:
            stream.write(canonical(row) + b"\n")
    os.replace(temporary, output_rows)

    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "sourceDecisionN": len(source_rows),
        "transactionCacheCoverageN": len(rows),
        "transactionCacheMissingN": sum(family_missing.values()),
        "familyCounts": dict(sorted(family_counts.items())),
        "cacheMissingByFamily": dict(sorted(family_missing.items())),
        "underlyingProgramsByFamily": {
            family: [
                {
                    "programId": program,
                    "knownName": KNOWN_PROGRAMS.get(program),
                    "decisionN": count,
                }
                for program, count in counts.most_common()
            ]
            for family, counts in sorted(family_programs.items())
        },
        "nonSolQuoteMintCounts": dict(quote_mints.most_common()),
        "integrity": {
            "outcomesRead": 0,
            "validationRowsRead": 0,
            "submissions": 0,
            "liveTradingModified": False,
        },
    }
    atomic_json(args.output / "route-protocol-report.json", report)
    atomic_json(args.output / "route-protocol-provenance.json", {
        "schema": SCHEMA + "_PROVENANCE",
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "sourceStateSha256": sha256_file(args.v2_state),
        "inventorySha256": sha256_file(output_rows),
        "reportSha256": sha256_file(args.output / "route-protocol-report.json"),
        "outcomesRead": 0,
        "validationRowsRead": 0,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v2-state", type=pathlib.Path, required=True)
    parser.add_argument("--wallet-rpc-cache", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
