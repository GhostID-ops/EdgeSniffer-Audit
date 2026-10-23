#!/usr/bin/env python3
"""Rebuildable indexed causal state for prospective 2CQ choice materialization."""

from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import json
import pathlib
import sqlite3
from typing import Any

import twoCqDecisionSystemDecompositionV1 as decision
import twoCqProspectiveSymmetricChoiceMaterializerV1 as legacy
from exactCurveEconomicsV1 import CurveState, decode_trade_events
from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "2CQ_INDEXED_CAUSAL_STATE_PLANE_V1"
YELLOWSTONE_SOURCE = "yellowstone-direct-exact-signature-v1"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def open_state(path: pathlib.Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS evidence_files (
          path TEXT PRIMARY KEY,
          size INTEGER NOT NULL,
          mtime_ns INTEGER NOT NULL,
          sha256 TEXT,
          imported_utc TEXT NOT NULL,
          successful_tx_n INTEGER NOT NULL,
          pump_tx_n INTEGER NOT NULL,
          event_n INTEGER NOT NULL,
          create_n INTEGER NOT NULL,
          incomplete_n INTEGER NOT NULL,
          missing_block_time_n INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions (
          signature TEXT PRIMARY KEY,
          block_time_s INTEGER NOT NULL,
          slot INTEGER NOT NULL,
          tx_index INTEGER NOT NULL,
          kinds_json TEXT NOT NULL,
          hinted_mints_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS transactions_block_time
          ON transactions(block_time_s,slot,tx_index,signature);
        CREATE TABLE IF NOT EXISTS transaction_evidence (
          signature TEXT NOT NULL,
          path TEXT NOT NULL,
          PRIMARY KEY(signature,path),
          FOREIGN KEY(signature) REFERENCES transactions(signature),
          FOREIGN KEY(path) REFERENCES evidence_files(path)
        );
        CREATE TABLE IF NOT EXISTS causal_events (
          signature TEXT NOT NULL,
          instruction_index INTEGER NOT NULL,
          mint TEXT NOT NULL,
          is_buy INTEGER NOT NULL,
          user TEXT NOT NULL,
          creator TEXT NOT NULL,
          quote_lamports INTEGER NOT NULL,
          token_raw INTEGER NOT NULL,
          pre_virtual_sol INTEGER NOT NULL,
          pre_virtual_token INTEGER NOT NULL,
          pre_real_sol INTEGER NOT NULL,
          pre_real_token INTEGER NOT NULL,
          post_virtual_sol INTEGER NOT NULL,
          post_virtual_token INTEGER NOT NULL,
          post_real_sol INTEGER NOT NULL,
          post_real_token INTEGER NOT NULL,
          PRIMARY KEY(signature,instruction_index,mint),
          FOREIGN KEY(signature) REFERENCES transactions(signature)
        );
        CREATE INDEX IF NOT EXISTS causal_events_mint ON causal_events(mint);
        CREATE TABLE IF NOT EXISTS create_events (
          signature TEXT NOT NULL,
          mint TEXT NOT NULL,
          PRIMARY KEY(signature,mint),
          FOREIGN KEY(signature) REFERENCES transactions(signature)
        );
        CREATE INDEX IF NOT EXISTS create_events_mint ON create_events(mint);
        CREATE TABLE IF NOT EXISTS incomplete_events (
          signature TEXT NOT NULL,
          mint TEXT NOT NULL,
          reason TEXT NOT NULL,
          PRIMARY KEY(signature,mint,reason),
          FOREIGN KEY(signature) REFERENCES transactions(signature)
        );
        CREATE INDEX IF NOT EXISTS incomplete_events_mint ON incomplete_events(mint);
        CREATE TABLE IF NOT EXISTS realtime_arrival_files (
          path TEXT PRIMARY KEY,
          size INTEGER NOT NULL,
          mtime_ns INTEGER NOT NULL,
          sha256 TEXT,
          imported_utc TEXT NOT NULL,
          arrival_n INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS realtime_arrivals (
          signature TEXT NOT NULL,
          source TEXT NOT NULL,
          received_ns INTEGER NOT NULL,
          slot INTEGER NOT NULL,
          evidence_path TEXT NOT NULL,
          PRIMARY KEY(signature,source)
        );
        CREATE INDEX IF NOT EXISTS realtime_arrivals_slot
          ON realtime_arrivals(slot,signature);
        """
    )
    connection.commit()
    return connection


def import_evidence_file(
    connection: sqlite3.Connection,
    path: pathlib.Path,
    sha256: str | None,
) -> tuple[bool, dict[str, int]]:
    resolved = str(path.resolve())
    stat = path.stat()
    prior = connection.execute(
        "SELECT size,mtime_ns,sha256 FROM evidence_files WHERE path=?", (resolved,)
    ).fetchone()
    if prior:
        if (int(prior[0]), int(prior[1])) != (stat.st_size, stat.st_mtime_ns):
            raise RuntimeError(f"INDEXED_EVIDENCE_IDENTITY_CHANGED:{resolved}")
        if sha256 and prior[2] and str(prior[2]) != sha256:
            raise RuntimeError(f"INDEXED_EVIDENCE_HASH_CHANGED:{resolved}")
        return False, {}

    counts: collections.Counter[str] = collections.Counter()
    connection.execute("BEGIN IMMEDIATE")
    try:
        # Insert the parent first so transaction_evidence retains file provenance.
        connection.execute(
            "INSERT INTO evidence_files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                resolved, stat.st_size, stat.st_mtime_ns, sha256,
                dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                0, 0, 0, 0, 0, 0,
            ),
        )
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            if metadata.get("kind") != "rpc_finalized_transaction" or metadata.get("successful") is not True:
                continue
            counts["successfulTx"] += 1
            signature = str(metadata.get("signature") or "")
            if not signature:
                continue
            try:
                tx = json.loads(raw).get("result")
            except Exception:
                tx = None
            if not tx or (tx.get("meta") or {}).get("err") is not None:
                continue
            block_time = int(tx.get("blockTime") or 0)
            if block_time <= 0:
                counts["missingBlockTime"] += 1
                continue
            slot = int(metadata.get("slot") or tx.get("slot") or 0)
            tx_index = int(
                metadata.get("transactionIndex")
                if metadata.get("transactionIndex") is not None else -1
            )
            kinds = set(metadata.get("pumpInstructionKinds") or [])
            base_hints = {
                str(mint) for mint in (metadata.get("discoveredMints") or []) if mint
            }
            try:
                create_mints = set(legacy.coverage.canonical_create_mints(tx))
            except Exception:
                create_mints = set()
                counts["createDecodeFailures"] += 1
            try:
                decoded_hint = decode_trade_events((tx.get("meta") or {}).get("logMessages") or [])
                hinted_mints = {str(row.mint) for row in decoded_hint}
                hint_failed = False
            except Exception:
                decoded_hint = []
                hinted_mints = base_hints
                hint_failed = True
            if hint_failed:
                decoded = []
                failure = (
                    "TRADE_LOG_DECODE_FAILED"
                    if kinds.intersection(decision.TRADE_KINDS) else None
                )
            else:
                try:
                    decoded, failure = legacy.coverage.decode_causal_events(
                        tx, signature, 0, slot, tx_index
                    )
                except Exception:
                    decoded, failure = [], "TRANSACTION_DECODE_EXCEPTION"
            pump_relevant = bool(
                decoded or create_mints
                or kinds.intersection(decision.TRADE_KINDS | {"CREATE", "CREATE_V2"})
            )
            if not pump_relevant:
                continue
            all_hints = hinted_mints | create_mints | base_hints
            connection.execute(
                "INSERT OR IGNORE INTO transactions VALUES (?,?,?,?,?,?)",
                (signature, block_time, slot, tx_index, canonical(sorted(kinds)), canonical(sorted(all_hints))),
            )
            connection.execute(
                "INSERT OR IGNORE INTO transaction_evidence VALUES (?,?)",
                (signature, resolved),
            )
            counts["pumpTx"] += 1
            for mint in create_mints:
                before = connection.total_changes
                connection.execute(
                    "INSERT OR IGNORE INTO create_events VALUES (?,?)", (signature, mint)
                )
                counts["creates"] += int(connection.total_changes > before)
            incomplete_reason = failure
            if not decoded and not failure and kinds.intersection(decision.TRADE_KINDS):
                incomplete_reason = "TRADE_EVENT_NOT_DECODED"
            if incomplete_reason:
                for mint in all_hints:
                    before = connection.total_changes
                    connection.execute(
                        "INSERT OR IGNORE INTO incomplete_events VALUES (?,?,?)",
                        (signature, mint, incomplete_reason),
                    )
                    counts["incomplete"] += int(connection.total_changes > before)
            for event in decoded:
                values = (
                    event.signature, int(event.instruction_index), event.mint,
                    int(event.is_buy), event.user, event.creator,
                    int(event.quote_lamports), int(event.token_raw),
                    int(event.pre_state.virtual_sol), int(event.pre_state.virtual_token),
                    int(event.pre_state.real_sol), int(event.pre_state.real_token),
                    int(event.post_state.virtual_sol), int(event.post_state.virtual_token),
                    int(event.post_state.real_sol), int(event.post_state.real_token),
                )
                before = connection.total_changes
                connection.execute(
                    "INSERT OR IGNORE INTO causal_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                counts["events"] += int(connection.total_changes > before)
        connection.execute(
            """UPDATE evidence_files SET successful_tx_n=?,pump_tx_n=?,event_n=?,
               create_n=?,incomplete_n=?,missing_block_time_n=? WHERE path=?""",
            (
                counts["successfulTx"], counts["pumpTx"], counts["events"],
                counts["creates"], counts["incomplete"], counts["missingBlockTime"], resolved,
            ),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return True, dict(counts)


def import_realtime_arrivals(
    connection: sqlite3.Connection,
    path: pathlib.Path,
    sha256: str | None,
) -> tuple[bool, int]:
    """Index exact local Yellowstone receipts without treating them as finality.

    Canonical success, state and ordering continue to come exclusively from the
    finalized transaction rows. This table only preserves the independently
    recorded local arrival clock for an exact-signature join.
    """
    resolved = str(path.resolve())
    stat = path.stat()
    prior = connection.execute(
        "SELECT size,mtime_ns,sha256 FROM realtime_arrival_files WHERE path=?",
        (resolved,),
    ).fetchone()
    if prior:
        if (int(prior[0]), int(prior[1])) != (stat.st_size, stat.st_mtime_ns):
            raise RuntimeError(f"INDEXED_REALTIME_EVIDENCE_IDENTITY_CHANGED:{resolved}")
        if sha256 and prior[2] and str(prior[2]) != sha256:
            raise RuntimeError(f"INDEXED_REALTIME_EVIDENCE_HASH_CHANGED:{resolved}")
        return False, 0

    arrivals = 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        for metadata, _ in iter_frames(path, path.suffix == ".partial"):
            if metadata.get("kind") != "transaction":
                continue
            if metadata.get("rawFormat") != "yellowstone_subscribe_update_protobuf":
                continue
            signature = str(metadata.get("signature") or "")
            received_ns = int(metadata.get("receivedAtUnixNs") or 0)
            slot = int(metadata.get("slot") or 0)
            if not signature or received_ns <= 0 or slot <= 0:
                continue
            connection.execute(
                """INSERT INTO realtime_arrivals
                   (signature,source,received_ns,slot,evidence_path)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(signature,source) DO UPDATE SET
                     received_ns=excluded.received_ns,
                     slot=excluded.slot,
                     evidence_path=excluded.evidence_path
                   WHERE excluded.received_ns < realtime_arrivals.received_ns""",
                (signature, YELLOWSTONE_SOURCE, received_ns, slot, resolved),
            )
            arrivals += 1
        connection.execute(
            "INSERT INTO realtime_arrival_files VALUES (?,?,?,?,?,?)",
            (
                resolved, stat.st_size, stat.st_mtime_ns, sha256,
                dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                arrivals,
            ),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return True, arrivals


def _state(prefix: str, row: sqlite3.Row) -> CurveState:
    return CurveState(
        virtual_sol=int(row[f"{prefix}_virtual_sol"]),
        virtual_token=int(row[f"{prefix}_virtual_token"]),
        real_sol=int(row[f"{prefix}_real_sol"]),
        real_token=int(row[f"{prefix}_real_token"]),
    )


def materialize_universe(
    connection: sqlite3.Connection,
    compact_path: pathlib.Path,
    boundaries: list[int],
    yellowstone_fallback_ranges_ns: list[tuple[int, int | None]] | None = None,
) -> tuple[
    dict[str, list[decision.CausalEvent]],
    dict[str, dict[str, Any]],
    dict[str, list[tuple[tuple[int, int, int, str], str]]],
    dict[str, Any],
]:
    if not boundaries:
        return {}, {}, {}, {"indexedCanonicalSuccessfulPumpTransactions": 0}
    connection.row_factory = sqlite3.Row
    lower_s = (min(boundaries) - legacy.MAX_AGE_NS - legacy.BLOCK_TIME_SAFETY_NS) // 1_000_000_000
    upper_s = (max(boundaries) + legacy.BLOCK_TIME_SAFETY_NS) // 1_000_000_000 + 1
    tx_rows = [
        row for row in connection.execute(
            "SELECT * FROM transactions WHERE block_time_s BETWEEN ? AND ?",
            (lower_s, upper_s),
        )
        if legacy.inside_expanded_decision_window(
            int(row["block_time_s"]) * 1_000_000_000, boundaries
        )
    ]
    by_signature = {str(row["signature"]): row for row in tx_rows}
    signatures = set(by_signature)
    if signatures:
        arrivals, compact_evidence = legacy.targeted_compact_arrivals_by_slot(
            compact_path,
            {legacy.tier_a.signature_digest(signature) for signature in signatures},
            min(int(row["slot"]) for row in tx_rows),
            max(int(row["slot"]) for row in tx_rows),
        )
    else:
        arrivals, compact_evidence = {}, {
            "compactRecordsScanned": 0,
            "compactSlotInversionsObserved": 0,
            "compactTargetDigests": 0,
            "compactTargetMatches": 0,
        }

    if signatures:
        connection.execute("DROP TABLE IF EXISTS temp.target_signatures")
        connection.execute("CREATE TEMP TABLE target_signatures(signature TEXT PRIMARY KEY)")
        connection.executemany(
            "INSERT INTO target_signatures VALUES (?)", ((signature,) for signature in signatures)
        )
    yellowstone_arrivals = {
        str(row[0]): (int(row[1]), int(row[2]))
        for row in connection.execute(
            """SELECT r.signature,r.received_ns,r.slot FROM realtime_arrivals r
                 JOIN target_signatures s USING(signature)
                 WHERE r.source=?""",
            (YELLOWSTONE_SOURCE,),
        )
    } if signatures else {}

    arrival_sources: collections.Counter[str] = collections.Counter()

    def resolved_arrival(signature: str) -> tuple[int, int, str] | None:
        digest = legacy.tier_a.signature_digest(signature)
        allenhark = arrivals.get(digest)
        if allenhark is not None:
            arrival_sources["allenhark"] += 1
            return int(allenhark[0]), int(allenhark[1]), "allenhark-capture-all-finalized-signature-join-v1"
        yellowstone = yellowstone_arrivals.get(signature)
        if yellowstone is None:
            return None
        received_ns, observed_slot = yellowstone
        calibrated = any(
            received_ns >= start_ns and (end_ns is None or received_ns < end_ns)
            for start_ns, end_ns in (yellowstone_fallback_ranges_ns or [])
        )
        if not calibrated:
            return None
        canonical_slot = int(by_signature[signature]["slot"])
        if observed_slot != canonical_slot:
            return None
        arrival_sources["yellowstone"] += 1
        return received_ns, observed_slot, YELLOWSTONE_SOURCE

    resolved_arrivals = {
        signature: arrival for signature in signatures
        if (arrival := resolved_arrival(signature)) is not None
    }

    events: dict[str, dict[tuple[str, int], decision.CausalEvent]] = collections.defaultdict(dict)
    creates: dict[str, dict[str, Any]] = {}
    incomplete: dict[str, list[tuple[tuple[int, int, int, str], str]]] = collections.defaultdict(list)
    for signature, tx_row in by_signature.items():
        if signature in resolved_arrivals:
            continue
        base_order = (int(tx_row["slot"]), int(tx_row["tx_index"]), -1, signature)
        for mint in json.loads(str(tx_row["hinted_mints_json"])):
            incomplete[str(mint)].append((base_order, "ALLENHARK_TRADE_ARRIVAL_MISSING"))
    if signatures:
        for row in connection.execute(
            f"""SELECT e.*,t.slot,t.tx_index FROM causal_events e
                 JOIN transactions t USING(signature)
                 JOIN target_signatures s USING(signature)""",
        ):
            signature = str(row["signature"])
            arrival = resolved_arrivals.get(signature)
            if arrival is None:
                continue
            event = decision.CausalEvent(
                mint=str(row["mint"]), signature=signature, slot=int(row["slot"]),
                transaction_index=int(row["tx_index"]), instruction_index=int(row["instruction_index"]),
                received_ns=int(arrival[0]), is_buy=bool(row["is_buy"]), user=str(row["user"]),
                creator=str(row["creator"]), quote_lamports=int(row["quote_lamports"]),
                token_raw=int(row["token_raw"]), pre_state=_state("pre", row),
                post_state=_state("post", row), realtime_source=str(arrival[2]),
            )
            events[event.mint][(signature, event.instruction_index)] = event
        for row in connection.execute(
            f"""SELECT c.*,t.slot,t.tx_index FROM create_events c
                 JOIN transactions t USING(signature)
                 JOIN target_signatures s USING(signature)""",
        ):
            signature = str(row["signature"])
            arrival = resolved_arrivals.get(signature)
            if arrival is None:
                continue
            order = (int(row["slot"]), int(row["tx_index"]), -1, signature)
            mint = str(row["mint"])
            candidate = {"mint": mint, "signature": signature, "order": order,
                         "blockTime": int(by_signature[signature]["block_time_s"]),
                         "receivedNs": int(arrival[0]), "slot": int(arrival[1]),
                         "realtimeSource": str(arrival[2])}
            if mint not in creates or order < tuple(creates[mint]["order"]):
                creates[mint] = candidate
        for row in connection.execute(
            f"""SELECT i.*,t.slot,t.tx_index FROM incomplete_events i
                 JOIN transactions t USING(signature)
                 JOIN target_signatures s USING(signature)""",
        ):
            signature = str(row["signature"])
            order = (int(row["slot"]), int(row["tx_index"]), -1, signature)
            incomplete[str(row["mint"])].append((order, str(row["reason"])))
        connection.execute("DROP TABLE target_signatures")

    connection.row_factory = None
    ordered = {mint: sorted(rows.values(), key=lambda event: event.order) for mint, rows in events.items()}
    return ordered, creates, dict(incomplete), {
        "indexedCanonicalSuccessfulPumpTransactions": len(tx_rows),
        "indexedRealtimeCreateMints": len(creates),
        "indexedDecodedTradeEvents": sum(len(rows) for rows in ordered.values()),
        "indexedIncompleteMintHistories": len(incomplete),
        "indexedAllenharkArrivalTransactions": int(arrival_sources["allenhark"]),
        "indexedYellowstoneFallbackTransactions": int(arrival_sources["yellowstone"]),
        "yellowstoneFallbackRangesUnixNs": yellowstone_fallback_ranges_ns or [],
        **compact_evidence,
    }


def ensure_and_materialize(
    state_path: pathlib.Path,
    evidence_paths: list[pathlib.Path],
    evidence_hashes: dict[str, str],
    compact_path: pathlib.Path,
    boundaries: list[int],
    integrity_pragma: str = "integrity_check",
    yellowstone_fallback_ranges_ns: list[tuple[int, int | None]] | None = None,
) -> tuple[Any, Any, Any, dict[str, Any]]:
    connection = open_state(state_path)
    imported = 0
    import_counts: collections.Counter[str] = collections.Counter()
    try:
        for path in evidence_paths:
            realtime_changed, realtime_n = import_realtime_arrivals(
                connection, path, evidence_hashes.get(str(path.resolve()))
            )
            if realtime_changed:
                import_counts["realtimeArrivalFiles"] += 1
                import_counts["realtimeArrivals"] += realtime_n
            changed, counts = import_evidence_file(
                connection, path, evidence_hashes.get(str(path.resolve()))
            )
            imported += int(changed)
            import_counts.update(counts)
            if changed:
                import_counts["evidenceBytes"] += path.stat().st_size
        events, creates, incomplete, evidence = materialize_universe(
            connection, compact_path, boundaries, yellowstone_fallback_ranges_ns
        )
        indexed_files = int(connection.execute("SELECT COUNT(*) FROM evidence_files").fetchone()[0])
        if integrity_pragma not in {"integrity_check", "quick_check(1)", "none"}:
            raise ValueError("UNSUPPORTED_SQLITE_INTEGRITY_PRAGMA")
        integrity = (
            "ok" if integrity_pragma == "none"
            else str(connection.execute(f"PRAGMA {integrity_pragma}").fetchone()[0])
        )
    finally:
        connection.close()
    if integrity != "ok":
        raise RuntimeError(f"INDEXED_CAUSAL_STATE_INTEGRITY:{integrity}")
    return events, creates, incomplete, {
        **evidence,
        "causalStatePlaneSchema": SCHEMA,
        "evidenceFilesImportedThisRun": imported,
        "evidenceFilesIndexedTotal": indexed_files,
        "importCounts": dict(import_counts),
        "sqliteIntegrity": integrity,
    }
