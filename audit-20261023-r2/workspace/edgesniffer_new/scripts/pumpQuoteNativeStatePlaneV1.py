#!/usr/bin/env python3
"""Durable exact state plane for non-SOL Pump V2 markets."""

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

import exactCurveEconomicsQuoteV2 as quote_decoder
import exactCurveEconomicsV1 as pump_decoder
import launchLabEvidenceReconcilerV1 as arrivals
from exactCurveEconomicsV1 import (
    PUMP_PROGRAM_ID,
    SOL_QUOTE_MINTS,
    base58_decode,
    extract_pump_source_observations,
    resolved_account_keys,
)
from exactCurveEconomicsQuoteV2 import decode_quote_trade_events, validate_quote_transition
from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "PUMP_QUOTE_NATIVE_STATE_PLANE_V1"
IMPLEMENTATION_VERSION = "1.3.0"
CREATE_V2 = bytes((214, 144, 76, 236, 95, 139, 49, 180))


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decoder_dependency_sha256() -> str:
    dependencies = {
        "pumpTradeEventDecoder": sha256_file(pathlib.Path(pump_decoder.__file__)),
        "quoteTradeEventDecoder": sha256_file(pathlib.Path(quote_decoder.__file__)),
    }
    return hashlib.sha256(
        json.dumps(dependencies, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def schema(connection: sqlite3.Connection) -> bool:
    connection.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence_files(
            path TEXT PRIMARY KEY,size INTEGER NOT NULL,mtime_ns INTEGER NOT NULL,
            sha256 TEXT NOT NULL,imported_utc TEXT NOT NULL,transaction_n INTEGER NOT NULL,
            exact_event_n INTEGER NOT NULL,error_n INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events(
            signature TEXT NOT NULL,instruction_index INTEGER NOT NULL,
            mint TEXT NOT NULL,quote_mint TEXT NOT NULL,slot INTEGER NOT NULL,
            canonical_tx_index INTEGER NOT NULL,block_time_s INTEGER NOT NULL,
            received_at_unix_ns INTEGER NOT NULL,is_buy INTEGER NOT NULL,user TEXT NOT NULL,
            creator TEXT NOT NULL,quote_amount_raw TEXT NOT NULL,token_amount_raw TEXT NOT NULL,
            pre_state_json TEXT NOT NULL,post_state_json TEXT NOT NULL,
            fee_json TEXT NOT NULL,evidence_path TEXT NOT NULL,
            PRIMARY KEY(signature,instruction_index,mint)
        );
        CREATE INDEX IF NOT EXISTS events_quote_order
            ON events(quote_mint,slot,canonical_tx_index,instruction_index);
        CREATE INDEX IF NOT EXISTS events_mint_order
            ON events(mint,slot,canonical_tx_index,instruction_index);
        CREATE TABLE IF NOT EXISTS create_events(
            signature TEXT NOT NULL,mint TEXT NOT NULL,slot INTEGER NOT NULL,
            canonical_tx_index INTEGER NOT NULL,block_time_s INTEGER NOT NULL,
            received_at_unix_ns INTEGER NOT NULL,evidence_path TEXT NOT NULL,
            quote_mint TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(signature,mint)
        );
        CREATE TABLE IF NOT EXISTS exclusions(
            signature TEXT NOT NULL,mint TEXT NOT NULL,reason TEXT NOT NULL,
            detail TEXT,evidence_path TEXT NOT NULL,
            PRIMARY KEY(signature,mint,reason)
        );
        CREATE TABLE IF NOT EXISTS compact_slots(
            slot INTEGER PRIMARY KEY,start_offset INTEGER NOT NULL,end_offset INTEGER NOT NULL,
            first_received_ns INTEGER NOT NULL,last_received_ns INTEGER NOT NULL
        );
    """)
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(create_events)")}
    if "quote_mint" not in columns:
        connection.execute(
            "ALTER TABLE create_events ADD COLUMN quote_mint TEXT NOT NULL DEFAULT ''"
        )
    current = connection.execute(
        "SELECT value FROM metadata WHERE key='implementation_version'"
    ).fetchone()
    current_dependency = connection.execute(
        "SELECT value FROM metadata WHERE key='decoder_dependency_sha256'"
    ).fetchone()
    dependency_sha = decoder_dependency_sha256()
    reset_derived_cache = (
        current is None
        or str(current[0]) != IMPLEMENTATION_VERSION
        or current_dependency is None
        or str(current_dependency[0]) != dependency_sha
    )
    if reset_derived_cache:
        connection.execute("DELETE FROM evidence_files")
        connection.execute("DELETE FROM events")
        connection.execute("DELETE FROM create_events")
        connection.execute("DELETE FROM exclusions")
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('implementation_version',?)",
            (IMPLEMENTATION_VERSION,),
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('decoder_dependency_sha256',?)",
            (dependency_sha,),
        )
        connection.commit()
    return reset_derived_cache


def quote_create_records(tx: dict[str, Any]) -> list[tuple[str, str]]:
    keys = resolved_account_keys(tx)
    message = (tx.get("transaction") or {}).get("message") or {}
    instructions = list(message.get("instructions") or [])
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        instructions.extend(group.get("instructions") or [])
    rows: list[tuple[str, str]] = []
    for instruction in instructions:
        program_index = instruction.get("programIdIndex")
        accounts = instruction.get("accounts") or []
        encoded = instruction.get("data")
        if program_index is None or int(program_index) >= len(keys) or not encoded:
            continue
        if keys[int(program_index)] != PUMP_PROGRAM_ID:
            continue
        try:
            payload = base58_decode(str(encoded))
        except Exception:
            continue
        # CreateV2 has 16 IDL accounts followed by the optional quote-mint,
        # quote-curve ATA and quote-token-program triplet.
        if payload[:8] != CREATE_V2 or len(accounts) < 19:
            continue
        mint_index, quote_index = int(accounts[0]), int(accounts[16])
        if mint_index >= len(keys) or quote_index >= len(keys):
            continue
        mint, quote_mint = str(keys[mint_index]), str(keys[quote_index])
        if quote_mint not in SOL_QUOTE_MINTS:
            rows.append((mint, quote_mint))
    return rows


def evidence_partition_key(path: pathlib.Path, root: pathlib.Path) -> str:
    relative = path.relative_to(root / "partitions")
    if len(relative.parts) < 4:
        raise ValueError(f"invalid exact-evidence partition path: {path}")
    return "/".join(relative.parts[:4])


def complete_evidence(
    root: pathlib.Path, minimum_partition: str | None = None
) -> dict[pathlib.Path, str]:
    result: dict[pathlib.Path, str] = {}
    for manifest in root.glob("partitions/*/*/*/*/reconciliation-manifest.json"):
        row = json.loads(manifest.read_text(encoding="utf-8"))
        if row.get("exactReplayComplete") is not True:
            continue
        for item in row.get("inputEvidence") or []:
            path = root / str(item["path"])
            if (
                path.exists()
                and (
                    minimum_partition is None
                    or evidence_partition_key(path, root) >= minimum_partition
                )
            ):
                result[path.resolve()] = str(item.get("sha256") or sha256_file(path))
    return result


def import_file(
    connection: sqlite3.Connection, path: pathlib.Path, expected_sha: str,
    compact_path: pathlib.Path,
) -> tuple[bool, dict[str, int]]:
    stat = path.stat()
    existing = connection.execute(
        "SELECT size,mtime_ns,sha256 FROM evidence_files WHERE path=?", (str(path),)
    ).fetchone()
    if existing:
        if (int(existing[0]), int(existing[1]), str(existing[2])) != (
            stat.st_size, stat.st_mtime_ns, expected_sha
        ):
            raise RuntimeError(f"QUOTE_STATE_EVIDENCE_IDENTITY_CHANGED:{path}")
        return False, {}
    counters: collections.Counter[str] = collections.Counter()
    for metadata, raw in iter_frames(path, path.suffix == ".partial"):
        if metadata.get("kind") != "rpc_finalized_transaction" or metadata.get("successful") is not True:
            continue
        counters["transactions"] += 1
        signature = str(metadata.get("signature") or "")
        if not signature:
            continue
        try:
            tx = json.loads(raw).get("result")
        except Exception as error:
            tx = None
            counters["errors"] += 1
            connection.execute(
                "INSERT OR IGNORE INTO exclusions VALUES(?,?,?,?,?)",
                (signature, "", "RAW_TRANSACTION_DECODE_FAILED", str(error)[:500], str(path)),
            )
        if not tx or (tx.get("meta") or {}).get("err") is not None:
            continue
        slot = int(metadata.get("slot") or tx.get("slot") or 0)
        tx_index = int(metadata.get("transactionIndex") if metadata.get("transactionIndex") is not None else -1)
        block_time = int(tx.get("blockTime") or 0)
        received_ns = arrivals.compact_receipt_ns(connection, compact_path, signature, slot) or 0
        for mint, quote_mint in quote_create_records(tx):
            connection.execute(
                "INSERT OR IGNORE INTO create_events"
                "(signature,mint,slot,canonical_tx_index,block_time_s,received_at_unix_ns,evidence_path,quote_mint) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    signature, mint, slot, tx_index, block_time, received_ns,
                    str(path), quote_mint,
                ),
            )
        try:
            quote_events = decode_quote_trade_events((tx.get("meta") or {}).get("logMessages") or [])
        except Exception as error:
            counters["errors"] += 1
            connection.execute(
                "INSERT OR IGNORE INTO exclusions VALUES(?,?,?,?,?)",
                (signature, "", "QUOTE_EVENT_DECODE_FAILED", str(error)[:500], str(path)),
            )
            continue
        for event in quote_events:
            try:
                sources = extract_pump_source_observations(
                    tx, signature, event.mint, 0, "finalized_chain_order"
                )
                candidates = [row for row in sources if row.side == ("buy" if event.is_buy else "sell")]
                if len(candidates) != 1:
                    raise ValueError(f"source/event cardinality {len(candidates)}")
                validated = validate_quote_transition(candidates[0].instruction, event)
                connection.execute(
                    "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        signature, int(candidates[0].instruction_index), event.mint,
                        event.quote_mint, slot, tx_index, block_time, received_ns,
                        int(event.is_buy), event.user, event.creator, str(event.quote_amount),
                        str(event.token_amount), json.dumps(dataclasses_asdict(event.pre_state)),
                        json.dumps(dataclasses_asdict(event.post_state)),
                        json.dumps({
                            "protocolBps": event.fee_basis_points,
                            "protocolRaw": event.fee,
                            "creatorBps": event.creator_fee_basis_points,
                            "creatorRaw": event.creator_fee,
                            "cashbackBps": event.cashback_fee_basis_points,
                            "cashbackRaw": event.cashback,
                            "buybackBps": event.buyback_fee_basis_points,
                            "buybackRaw": event.buyback_fee,
                            "classification": validated["classification"],
                        }, sort_keys=True), str(path),
                    ),
                )
                counters["exactEvents"] += int(connection.execute("SELECT changes()").fetchone()[0])
            except Exception as error:
                counters["errors"] += 1
                connection.execute(
                    "INSERT OR IGNORE INTO exclusions VALUES(?,?,?,?,?)",
                    (signature, event.mint, "QUOTE_TRANSITION_NOT_EXACT", str(error)[:500], str(path)),
                )
    connection.execute(
        "INSERT INTO evidence_files VALUES(?,?,?,?,?,?,?,?)",
        (
            str(path), stat.st_size, stat.st_mtime_ns, expected_sha,
            dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            counters["transactions"], counters["exactEvents"], counters["errors"],
        ),
    )
    connection.commit()
    return True, dict(counters)


def dataclasses_asdict(value: Any) -> dict[str, Any]:
    return {
        "virtualQuote": int(value.virtual_sol),
        "virtualToken": int(value.virtual_token),
        "realQuote": int(value.real_sol),
        "realToken": int(value.real_token),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(args.output / "quote-native-state-v1.sqlite3")
    derived_cache_reset = schema(connection)
    compact_records, compact_offset = arrivals.index_compact_arrivals(
        connection, args.compact_arrivals
    )
    evidence = complete_evidence(args.exact_root, args.minimum_partition)
    imported = 0
    already_imported = int(connection.execute("SELECT COUNT(*) FROM evidence_files").fetchone()[0])
    totals: collections.Counter[str] = collections.Counter()
    for path, expected_sha in sorted(evidence.items(), key=lambda row: str(row[0])):
        if imported >= args.max_files_per_run:
            break
        changed, counts = import_file(
            connection, path, expected_sha, args.compact_arrivals
        )
        imported += int(changed)
        totals.update(counts)
    event_n = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    realtime_n = int(connection.execute(
        "SELECT COUNT(*) FROM events WHERE received_at_unix_ns>0"
    ).fetchone()[0])
    mint_n = int(connection.execute("SELECT COUNT(DISTINCT mint) FROM events").fetchone()[0])
    quote_n = int(connection.execute("SELECT COUNT(DISTINCT quote_mint) FROM events").fetchone()[0])
    create_n = int(connection.execute("SELECT COUNT(*) FROM create_events").fetchone()[0])
    exclusion_n = int(connection.execute("SELECT COUNT(*) FROM exclusions").fetchone()[0])
    continuity_comparisons = continuity_gaps = 0
    prior: dict[tuple[str, str], str] = {}
    for mint, quote, pre, post in connection.execute(
        "SELECT mint,quote_mint,pre_state_json,post_state_json FROM events "
        "ORDER BY mint,quote_mint,slot,canonical_tx_index,instruction_index"
    ):
        key = (str(mint), str(quote))
        if key in prior:
            continuity_comparisons += 1
            continuity_gaps += int(prior[key] != pre)
        prior[key] = post
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    connection.close()
    report = {
        "schema": SCHEMA + "_HEALTH",
        "implementationVersion": IMPLEMENTATION_VERSION,
        "decoderDependencySha256": decoder_dependency_sha256(),
        "derivedCacheResetThisRun": derived_cache_reset,
        "minimumPartition": args.minimum_partition,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "completeEvidenceFilesN": len(evidence),
        "evidenceFilesImportedTotalN": already_imported + imported,
        "pendingEvidenceFilesN": max(0, len(evidence) - already_imported - imported),
        "historicalBackfillComplete": len(evidence) == already_imported + imported,
        "evidenceFilesImportedThisRun": imported,
        "importCounts": dict(totals),
        "compactRecordsIndexedThisRun": compact_records,
        "compactJournalOffset": compact_offset,
        "exactTransitionN": event_n,
        "causalRealtimeExactTransitionN": realtime_n,
        "missingRealtimeTransitionN": event_n - realtime_n,
        "mintN": mint_n,
        "quoteMintN": quote_n,
        "createN": create_n,
        "createSemantics": "OFFICIAL_CREATE_V2_WITH_EXPLICIT_NON_SOL_QUOTE_ACCOUNTS_ONLY",
        "exclusionN": exclusion_n,
        "continuityComparisonsN": continuity_comparisons,
        "continuityGapN": continuity_gaps,
        "sqliteIntegrity": integrity,
        "choiceSemantics": "SAME_QUOTE_MINT_ONLY_FAIL_CLOSED",
        "solNormalization": "BLOCKED_UNLESS_CAUSAL_QUOTE_TO_SOL_CONVERSION_EXISTS",
        "integrity": {"outcomesRead": 0, "validationRowsRead": 0, "submissions": 0},
    }
    arrivals.atomic_json(args.output / "health.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--max-files-per-run", type=int, default=5)
    parser.add_argument(
        "--minimum-partition",
        help="inclusive UTC partition key (YYYY/MM/DD/HH) for a bounded cohort state plane",
    )
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
