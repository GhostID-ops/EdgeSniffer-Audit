#!/usr/bin/env python3
"""Outcome-blind coverage census for the frozen 2CQ teacher-entry population."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
from typing import Any


SCHEMA = "2CQ_CAUSAL_COVERAGE_RECOVERY_AUDIT_V1"
TRADE_KINDS = {"BUY", "BUY_EXACT_SOL_IN", "BUY_V2", "BUY_EXACT_QUOTE_IN_V2", "SELL", "SELL_V2"}


def utc_ms(value: int | None) -> str | None:
    if value is None:
        return None
    return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def accepted_episodes(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            source = json.loads(line)
            if not source.get("insideFrozenWindow") or not source.get("pumpRelevant"):
                continue
            if source.get("leftCensored") or int(source.get("unknownOpeningInventoryRaw") or 0):
                continue
            if not source.get("entrySignature") or source.get("entryVenue") != "PUMP_BONDING_CURVE":
                continue
            entry = next(
                (row for row in source.get("actions") or [] if row.get("signature") == source["entrySignature"]),
                None,
            )
            if not entry or entry.get("kind") != "ECONOMIC_BUY":
                continue
            rows.append({
                "episodeId": f"{source['mint']}:{source['episodeIndex']}",
                "mint": source["mint"],
                "episodeIndex": int(source["episodeIndex"]),
                "entrySignature": source["entrySignature"],
                "entrySlot": int(entry.get("slot") or 0),
                "entryBlockTimeSec": int(entry.get("timestamp") or 0),
            })
    return rows


def load_indexed_mints(path: pathlib.Path, targets: set[str]) -> set[str]:
    found: set[str] = set()
    with path.open("rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("mint") in targets:
                found.add(row["mint"])
    return found


def load_exact_arrival_signatures(path: pathlib.Path) -> set[str]:
    signatures: set[str] = set()
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        next(stream)  # immutable cache header
        for line in stream:
            signatures.add(json.loads(line)["signature"])
    return signatures


def load_finalized_teacher_transactions(path: pathlib.Path, targets: set[str]) -> set[str]:
    found: set[str] = set()
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("signature") in targets and row.get("transaction") is not None:
                found.add(row["signature"])
    return found


def normalized_coverage(path: pathlib.Path, episodes: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    by_mint: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for episode in episodes:
        by_mint[episode["mint"]].append(episode)
    coverage = {
        row["episodeId"]: {
            "rowsAtOrBeforeEntry": 0,
            "strictPreEntryRows": 0,
            "strictPreEntryUniqueSignatures": set(),
            "entrySignatureRows": 0,
            "hasTransactionIndex": False,
            "earliestReceiveMs": None,
            "latestPreEntryReceiveMs": None,
        }
        for row in episodes
    }
    stats: collections.Counter[str] = collections.Counter()
    min_receive: int | None = None
    max_receive: int | None = None
    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            stats["totalRows"] += 1
            try:
                row = json.loads(line)
            except Exception:
                stats["malformedRows"] += 1
                continue
            mint = row.get("mint")
            if mint not in by_mint:
                continue
            stats["targetMintRows"] += 1
            slot = int(row.get("source_slot") or 0)
            tx_index_raw = row.get("source_transaction_index")
            tx_index = int(tx_index_raw) if tx_index_raw is not None else -1
            signature = str(row.get("transaction_signature") or "")
            receive_ms = int(row.get("local_receive_ts_ms") or 0)
            if receive_ms:
                min_receive = receive_ms if min_receive is None else min(min_receive, receive_ms)
                max_receive = receive_ms if max_receive is None else max(max_receive, receive_ms)
            for episode in by_mint[mint]:
                entry_slot = episode["entrySlot"]
                # The finalized teacher artifact does not retain transaction index.
                # Same-slot non-entry rows are therefore not asserted pre-entry.
                is_entry = signature == episode["entrySignature"]
                strictly_before = slot < entry_slot
                at_or_before = strictly_before or is_entry
                if not at_or_before:
                    continue
                item = coverage[episode["episodeId"]]
                item["rowsAtOrBeforeEntry"] += 1
                item["hasTransactionIndex"] = item["hasTransactionIndex"] or tx_index_raw is not None
                if receive_ms:
                    earliest = item["earliestReceiveMs"]
                    item["earliestReceiveMs"] = receive_ms if earliest is None else min(earliest, receive_ms)
                if is_entry:
                    item["entrySignatureRows"] += 1
                elif strictly_before:
                    item["strictPreEntryRows"] += 1
                    if signature:
                        item["strictPreEntryUniqueSignatures"].add(signature)
                    if receive_ms:
                        latest = item["latestPreEntryReceiveMs"]
                        item["latestPreEntryReceiveMs"] = receive_ms if latest is None else max(latest, receive_ms)
            if line_number % 5_000_000 == 0:
                print(json.dumps({"phase": "normalized_scan", "rows": line_number}), flush=True)
    for item in coverage.values():
        item["strictPreEntryUniqueSignatures"] = len(item["strictPreEntryUniqueSignatures"])
    return coverage, {
        **dict(stats),
        "targetReceiveMinUtc": utc_ms(min_receive),
        "targetReceiveMaxUtc": utc_ms(max_receive),
        "semanticWarning": "raw ShredStream observations have millisecond arrival/order but canonical_state is null; they are not proof of successful canonical execution or complete history",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--teacher-root", type=pathlib.Path, required=True)
    parser.add_argument("--timing-root", type=pathlib.Path, required=True)
    parser.add_argument("--exact-index", type=pathlib.Path, required=True)
    parser.add_argument("--normalized", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()

    episodes = accepted_episodes(args.teacher_root / "episodes.jsonl.gz")
    if len(episodes) != 5296:
        raise RuntimeError(f"frozen population drift: expected 5296, got {len(episodes)}")
    target_mints = {row["mint"] for row in episodes}
    indexed = load_indexed_mints(args.exact_index, target_mints)
    exact_arrivals = load_exact_arrival_signatures(args.timing_root / "causal-events-cache.jsonl.gz")
    teacher_signatures = {row["entrySignature"] for row in episodes}
    finalized_teacher = load_finalized_teacher_transactions(
        args.teacher_root / "primary-entry-finalized-transactions.jsonl.gz", teacher_signatures
    )
    normalized, normalized_stats = normalized_coverage(args.normalized, episodes)

    census: collections.Counter[str] = collections.Counter()
    manifest: list[dict[str, Any]] = []
    for episode in episodes:
        item = normalized[episode["episodeId"]]
        exact_entry_arrival = episode["entrySignature"] in exact_arrivals
        exact_indexed = episode["mint"] in indexed
        finalized_entry = episode["entrySignature"] in finalized_teacher
        if exact_indexed and exact_entry_arrival:
            category = "A_FULL_REALTIME_CAUSAL_COVERAGE"
            basis = "exact-ledger complete partition plus exact finalized event and local arrival join"
        elif exact_indexed and finalized_entry:
            category = "B_EXACT_CHAIN_ORDER_COVERAGE"
            basis = "exact-ledger complete partition plus finalized teacher entry; millisecond entry/event join unavailable"
        elif item["rowsAtOrBeforeEntry"] > 0 or exact_indexed or finalized_entry:
            category = "C_PARTIAL_CHAIN_HISTORY"
            basis = "raw ShredStream and/or finalized teacher-entry evidence exists, but external canonical completeness is unproven"
        else:
            category = "D_NO_USABLE_PRE_ENTRY_HISTORY"
            basis = "no existing pre-entry observation and no complete exact-source index"
        census[category] += 1
        manifest.append({
            **episode,
            "coverageCategory": category,
            "classificationBasis": basis,
            "exactLedgerIndexed": exact_indexed,
            "exactEntryArrivalJoined": exact_entry_arrival,
            "finalizedTeacherEntryAvailable": finalized_entry,
            "normalized": item,
        })

    category_order = [
        "A_FULL_REALTIME_CAUSAL_COVERAGE",
        "B_EXACT_CHAIN_ORDER_COVERAGE",
        "C_PARTIAL_CHAIN_HISTORY",
        "D_NO_USABLE_PRE_ENTRY_HISTORY",
    ]
    coverage_report = {
        key: {"N": census[key], "pct": 100.0 * census[key] / len(episodes)} for key in category_order
    }
    tier_a = census[category_order[0]]
    tier_b = census[category_order[1]]
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "outcomesRead": 0,
        "servicesModified": 0,
        "population": {"episodeN": len(episodes), "uniqueMintN": len(target_mints)},
        "coverageCensus": coverage_report,
        "tiers": {
            "tierAFullOriginalTimingSemanticsN": tier_a,
            "tierBStrictChainOrderedAdditionalN": tier_b,
            "tierBStrictChainOrderedTotalN": tier_a + tier_b,
            "tierCInsufficientN": len(episodes) - tier_a - tier_b,
        },
        "sourceCounts": {
            "exactLedgerIndexedEpisodes": sum(row["exactLedgerIndexed"] for row in manifest),
            "exactLedgerIndexedUniqueMints": len(indexed),
            "exactEntryArrivalJoinedEpisodes": sum(row["exactEntryArrivalJoined"] for row in manifest),
            "finalizedTeacherEntryEpisodes": sum(row["finalizedTeacherEntryAvailable"] for row in manifest),
            "normalizedAnyEvidenceEpisodes": sum(row["normalized"]["rowsAtOrBeforeEntry"] > 0 for row in manifest),
            "normalizedStrictPreEntryEvidenceEpisodes": sum(row["normalized"]["strictPreEntryRows"] > 0 for row in manifest),
            "normalizedEntrySignatureObservedEpisodes": sum(row["normalized"]["entrySignatureRows"] > 0 for row in manifest),
        },
        "normalizedArchive": normalized_stats,
        "classificationRules": {
            "A": "complete exact-ledger partition, finalized exact event, and trustworthy local millisecond arrival join",
            "B": "complete exact-ledger partition and finalized teacher entry, but no trustworthy millisecond join",
            "C": "some raw ShredStream/finalized entry evidence, but success or full external history cannot be proven",
            "D": "no usable existing pre-entry evidence",
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output / "report.json", report)
    manifest_path = args.output / "coverage-manifest.jsonl.gz"
    temp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    with gzip.open(temp_path, "wt", encoding="utf-8") as stream:
        for row in manifest:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temp_path, manifest_path)
    provenance = {
        "schema": SCHEMA,
        "reportSha256": hashlib.sha256((args.output / "report.json").read_bytes()).hexdigest(),
        "manifestSha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "inputs": {name: str(value) for name, value in vars(args).items() if name != "output"},
    }
    atomic_json(args.output / "provenance.json", provenance)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
