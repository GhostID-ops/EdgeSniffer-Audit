#!/usr/bin/env python3
"""Outcome-blind StonkFun overlay audit over already-cached transactions.

StonkFun is identified as an overlay on existing Raydium venues. This report
does not alter route families, cohort counts, or economic labels.
"""

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


SCHEMA = "2CQ_STONKFUN_OVERLAY_AUDIT_V1"
LAUNCHLAB = "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"
CLMM = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
CPMM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
REWARD_CONFIG = "6BwHHDg3u1854jC8PDLXvR4spTcLNaoBxLJNGC4nTESt"
STANDARD_CONFIG = "4E876qZTE9FJMrBzgVtBrSrzz2TLivB5Y5QXPjB4gZL7"
LEGACY_LAUNCHER = "5CEbueQnq1Ym2uSSx2xXds3jQAqT1BDnkA59RZobSPAG"
LEGACY_CLMM_DISCRIMINATOR = "2B44D4A7592FA401"


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def resolved_keys(tx: dict[str, Any]) -> list[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = [
        str(row.get("pubkey")) if isinstance(row, dict) else str(row)
        for row in message.get("accountKeys") or []
    ]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    keys.extend(str(row) for row in loaded.get("writable") or [])
    keys.extend(str(row) for row in loaded.get("readonly") or [])
    return keys


def invoked_programs(tx: dict[str, Any]) -> tuple[set[str], set[str]]:
    keys = resolved_keys(tx)
    message = (tx.get("transaction") or {}).get("message") or {}

    def program_for(instruction: dict[str, Any]) -> str | None:
        program = instruction.get("programId")
        index = instruction.get("programIdIndex")
        if program is None and index is not None and int(index) < len(keys):
            program = keys[int(index)]
        return None if program is None else str(program)

    top = {p for row in message.get("instructions") or [] if (p := program_for(row))}
    inner: set[str] = set()
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        inner.update(p for row in group.get("instructions") or [] if (p := program_for(row)))
    return top, inner


def signers(tx: dict[str, Any]) -> set[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    account_keys = message.get("accountKeys") or []
    header = message.get("header") or {}
    required = int(header.get("numRequiredSignatures") or 0)
    result: set[str] = set()
    for row in account_keys[:required]:
        if isinstance(row, dict):
            result.add(str(row.get("pubkey")))
        else:
            result.add(str(row))
    return result


def instruction_data(tx: dict[str, Any]) -> list[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    values = [
        str(row.get("data") or "")
        for row in message.get("instructions") or []
        if isinstance(row, dict)
    ]
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        values.extend(
            str(row.get("data") or "")
            for row in group.get("instructions") or []
            if isinstance(row, dict)
        )
    return values


def classify(tx: dict[str, Any]) -> tuple[str | None, list[str]]:
    top, inner = invoked_programs(tx)
    keys = set(resolved_keys(tx))
    evidence: list[str] = []

    if LAUNCHLAB in top | inner:
        if REWARD_CONFIG in keys:
            evidence.append("LAUNCHLAB_REWARD_PLATFORM_CONFIG")
        if STANDARD_CONFIG in keys:
            evidence.append("LAUNCHLAB_STANDARD_PLATFORM_CONFIG")
        if evidence:
            return "STONKFUN_LAUNCHLAB", evidence

    if CLMM in top | inner and LEGACY_LAUNCHER in signers(tx):
        data = [value.upper() for value in instruction_data(tx)]
        if any(value.startswith(LEGACY_CLMM_DISCRIMINATOR) for value in data):
            return "STONKFUN_LEGACY_CLMM", ["LEGACY_LAUNCHER", "LEGACY_CLMM_INSTRUCTION"]

    if CPMM in top | inner:
        # A CPMM transaction alone is not enough to prove StonkFun lineage.
        # It is counted only when the same transaction carries a platform
        # marker; historical lineage requires a separate mint-level join.
        if REWARD_CONFIG in keys or STANDARD_CONFIG in keys:
            return "STONKFUN_CPMM_MARKED", ["PLATFORM_CONFIG", "CPMM"]

    return None, []


def run(args: argparse.Namespace) -> dict[str, Any]:
    source = sqlite3.connect(f"file:{args.source_state}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    rows = list(source.execute(
        "SELECT signature,mint,route,economic_family FROM current_decisions "
        "ORDER BY block_time_ms,slot,transaction_index,signature"
    ))
    source.close()

    output_rows = args.output / "stonkfun-overlay-rows.jsonl"
    output_rows.parent.mkdir(parents=True, exist_ok=True)
    counts: collections.Counter[str] = collections.Counter()
    family_counts: collections.Counter[str] = collections.Counter()
    marker_counts: collections.Counter[str] = collections.Counter()
    mints: set[str] = set()
    missing_cache = 0
    examined = 0
    emitted: list[dict[str, Any]] = []

    for row in rows:
        path = args.wallet_rpc_cache / f"tx-{row['signature']}.json"
        if not path.exists():
            missing_cache += 1
            continue
        examined += 1
        try:
            tx = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        overlay, evidence = classify(tx)
        if overlay is None:
            continue
        counts[overlay] += 1
        family_counts[str(row["economic_family"])] += 1
        mints.add(str(row["mint"]))
        marker_counts.update(evidence)
        emitted.append({
            "schema": SCHEMA + "_ROW",
            "signature": str(row["signature"]),
            "mint": str(row["mint"]),
            "sourceRoute": str(row["route"]),
            "sourceEconomicFamily": str(row["economic_family"]),
            "overlay": overlay,
            "evidence": evidence,
            "outcomesRead": 0,
            "validationRowsRead": 0,
            "submissions": 0,
        })

    temporary = output_rows.with_suffix(output_rows.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for item in emitted:
            stream.write(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, output_rows)

    symmetric = {
        "available": args.route_state is not None,
        "launchLabSelectedN": 0,
        "launchLabEligibleN": 0,
        "launchLabEligibleUniqueMintN": 0,
    }
    if args.route_state is not None:
        route = sqlite3.connect(f"file:{args.route_state}?mode=ro", uri=True)
        route.row_factory = sqlite3.Row
        eligible = list(route.execute(
            "SELECT signature,mint FROM decisions "
            "WHERE lane='LAUNCHLAB_ACTIVE_5000MS_SAME_SPEND_ASSET' AND status='ELIGIBLE'"
        ))
        selected = list(route.execute(
            "SELECT signature FROM decisions "
            "WHERE lane='LAUNCHLAB_ACTIVE_5000MS_SAME_SPEND_ASSET'"
        ))
        route.close()
        overlay_signatures = {str(item["signature"]) for item in emitted}
        eligible_overlay = [row for row in eligible if str(row["signature"]) in overlay_signatures]
        symmetric.update({
            "launchLabSelectedN": sum(
                1 for row in selected if str(row["signature"]) in overlay_signatures
            ),
            "launchLabEligibleN": len(eligible_overlay),
            "launchLabEligibleUniqueMintN": len({str(row["mint"]) for row in eligible_overlay}),
        })

    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "sourceDecisionN": len(rows),
        "transactionCacheExaminedN": examined,
        "transactionCacheMissingN": missing_cache,
        "stonkfunOverlayN": len(emitted),
        "stonkfunUniqueMintN": len(mints),
        "overlayCounts": dict(sorted(counts.items())),
        "markerCounts": dict(sorted(marker_counts.items())),
        "existingLaunchLabSymmetricJoin": symmetric,
        "sourceEconomicFamilyCounts": dict(sorted(family_counts.items())),
        "markers": {
            "launchLabProgram": LAUNCHLAB,
            "rewardPlatformConfig": REWARD_CONFIG,
            "standardPlatformConfig": STANDARD_CONFIG,
            "legacyClmmProgram": CLMM,
            "legacyLauncher": LEGACY_LAUNCHER,
            "graduatedCpmmProgram": CPMM,
        },
        "methodology": {
            "outcomeBlind": True,
            "websiteOrExternalApiUsed": False,
            "officialSolPumpCohortModified": False,
            "platformConfigIsRequiredForNewLaunchLabClassification": True,
            "unmarkedCpmmIsNotClaimedAsStonkfun": True,
        },
    }
    atomic_json(args.output / "stonkfun-overlay-report.json", report)
    atomic_json(args.output / "stonkfun-overlay-provenance.json", {
        "schema": SCHEMA + "_PROVENANCE",
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "sourceStateSha256": sha256_file(args.source_state),
        "rowsSha256": sha256_file(output_rows),
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "submissions": 0,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-state", type=pathlib.Path, required=True)
    parser.add_argument("--wallet-rpc-cache", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--route-state", type=pathlib.Path)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
