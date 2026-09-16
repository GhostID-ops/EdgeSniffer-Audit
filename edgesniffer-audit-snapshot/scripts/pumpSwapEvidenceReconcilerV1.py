#!/usr/bin/env python3
"""Prospective finalized PumpSwap evidence ledger with AllenHark arrival joins."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sqlite3
import time
import urllib.error
from typing import Any

import launchLabEvidenceReconcilerV1 as common
import pumpSwapExactEconomicsV1 as pumpswap


SCHEMA = "PUMPSWAP_EVIDENCE_LEDGER_V1"
IMPLEMENTATION_VERSION = "1.5.0"


class RpcRateLimited(RuntimeError):
    pass


class PacedRpc(common.Rpc):
    def __init__(self, url: str, min_interval_s: float = 0.12) -> None:
        super().__init__(url)
        self.min_interval_s = min_interval_s
        self.last_call = 0.0

    def call(self, method: str, params: list[Any]) -> Any:
        for attempt, delay in enumerate((0.0, 5.0, 15.0, 30.0)):
            if delay:
                time.sleep(delay)
            wait = self.min_interval_s - (time.monotonic() - self.last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                value = super().call(method, params)
                self.last_call = time.monotonic()
                return value
            except urllib.error.HTTPError as error:
                self.last_call = time.monotonic()
                if error.code != 429:
                    raise
                if attempt == 3:
                    raise RpcRateLimited(f"RPC_429:{method}") from error
        raise RpcRateLimited(f"RPC_429:{method}")


def schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS arrivals(
            signature TEXT PRIMARY KEY,
            received_at_utc TEXT NOT NULL,
            received_at_unix_ns INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            observed_ordinal INTEGER NOT NULL DEFAULT -1,
            finalized INTEGER NOT NULL DEFAULT 0,
            successful INTEGER,
            canonical_tx_index INTEGER,
            classification TEXT,
            error TEXT,
            teacher_selected INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS events(
            signature TEXT NOT NULL,
            event_index INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            canonical_tx_index INTEGER NOT NULL,
            received_at_unix_ns INTEGER NOT NULL,
            pool TEXT NOT NULL,
            user TEXT NOT NULL,
            base_mint TEXT NOT NULL,
            quote_mint TEXT NOT NULL,
            side TEXT NOT NULL,
            amount_mode TEXT NOT NULL,
            base_amount_raw TEXT NOT NULL,
            curve_quote_amount_raw TEXT NOT NULL,
            pool_quote_delta_raw TEXT NOT NULL,
            user_quote_amount_raw TEXT NOT NULL,
            lp_fee_raw TEXT NOT NULL,
            protocol_fee_raw TEXT NOT NULL,
            creator_fee_raw TEXT NOT NULL,
            cashback_raw TEXT NOT NULL,
            buyback_fee_raw TEXT NOT NULL,
            pre_state_json TEXT NOT NULL,
            post_state_json TEXT NOT NULL,
            PRIMARY KEY(signature,event_index)
        );
        CREATE INDEX IF NOT EXISTS events_pool_order
            ON events(pool,slot,canonical_tx_index,event_index);
        CREATE TABLE IF NOT EXISTS compact_slots(
            slot INTEGER PRIMARY KEY,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            first_received_ns INTEGER NOT NULL,
            last_received_ns INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS selected_pools(
            pool TEXT PRIMARY KEY,
            selected_signature TEXT NOT NULL,
            selected_slot INTEGER NOT NULL,
            head_signature TEXT,
            history_cursor TEXT,
            history_complete INTEGER NOT NULL DEFAULT 0,
            history_truncated INTEGER NOT NULL DEFAULT 0,
            history_signature_n INTEGER NOT NULL DEFAULT 0
        );
    """)
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(arrivals)")}
    if "teacher_selected" not in columns:
        connection.execute(
            "ALTER TABLE arrivals ADD COLUMN teacher_selected INTEGER NOT NULL DEFAULT 0"
        )
    pool_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(selected_pools)")
    }
    if "head_signature" not in pool_columns:
        connection.execute("ALTER TABLE selected_pools ADD COLUMN head_signature TEXT")
    if "history_cursor" not in pool_columns:
        connection.execute("ALTER TABLE selected_pools ADD COLUMN history_cursor TEXT")


def insert_signature(
    connection: sqlite3.Connection,
    compact_path: pathlib.Path,
    signature: str,
    slot: int,
) -> int:
    received_ns = common.compact_receipt_ns(connection, compact_path, signature, slot) or 0
    received_at = (
        dt.datetime.fromtimestamp(received_ns / 1_000_000_000, tz=dt.timezone.utc)
        .isoformat().replace("+00:00", "Z") if received_ns else ""
    )
    connection.execute(
        "INSERT OR IGNORE INTO arrivals(signature,received_at_utc,received_at_unix_ns,slot) "
        "VALUES(?,?,?,?)", (signature, received_at, received_ns, slot)
    )
    return int(connection.execute("SELECT changes()").fetchone()[0])


def seed_teacher_signatures(
    connection: sqlite3.Connection, source_state: pathlib.Path | None,
    compact_path: pathlib.Path,
) -> int:
    if source_state is None or not source_state.exists():
        return 0
    source = sqlite3.connect(f"file:{source_state}?mode=ro", uri=True)
    rows = list(source.execute(
        "SELECT signature,slot FROM current_decisions WHERE economic_family IN ('PUMPSWAP','PUMPSWAP_CPI')"
    ))
    source.close()
    seeded = 0
    for signature, slot in rows:
        seeded += insert_signature(connection, compact_path, str(signature), int(slot))
        connection.execute(
            "UPDATE arrivals SET teacher_selected=1 WHERE signature=?", (str(signature),)
        )
    return seeded


def bootstrap_compact_index(
    connection: sqlite3.Connection, source_state: pathlib.Path | None
) -> int:
    if source_state is None or not source_state.exists():
        return 0
    if common.metadata_get(connection, "compact_offset") is not None:
        return 0
    source = sqlite3.connect(f"file:{source_state}?mode=ro", uri=True)
    rows = source.execute(
        "SELECT slot,start_offset,end_offset,first_received_ns,last_received_ns FROM compact_slots"
    ).fetchall()
    source_offset = source.execute(
        "SELECT value FROM metadata WHERE key='compact_offset'"
    ).fetchone()
    source.close()
    connection.executemany(
        "INSERT OR REPLACE INTO compact_slots VALUES(?,?,?,?,?)", rows
    )
    if source_offset:
        common.metadata_set(connection, "compact_offset", str(source_offset[0]))
    connection.commit()
    return len(rows)


def register_selected_pools(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        "SELECT e.pool,a.signature,a.slot FROM events e JOIN arrivals a USING(signature) "
        "WHERE a.teacher_selected=1 ORDER BY a.slot,a.canonical_tx_index,e.event_index"
    ).fetchall()
    changed = 0
    for pool, signature, slot in rows:
        existing = connection.execute(
            "SELECT selected_slot FROM selected_pools WHERE pool=?", (str(pool),)
        ).fetchone()
        if existing is None or int(slot) < int(existing[0]):
            connection.execute(
                "INSERT INTO selected_pools(pool,selected_signature,selected_slot) VALUES(?,?,?) "
                "ON CONFLICT(pool) DO UPDATE SET selected_signature=excluded.selected_signature,"
                "selected_slot=excluded.selected_slot,head_signature=NULL,history_cursor=NULL,"
                "history_complete=0,history_truncated=0,"
                "history_signature_n=0",
                (str(pool), str(signature), int(slot)),
            )
            changed += 1
    connection.commit()
    return changed


def backfill_recent_compact_receipts(
    connection: sqlite3.Connection, compact_path: pathlib.Path, limit: int = 5000
) -> tuple[int, int]:
    rows = connection.execute(
        "SELECT signature,slot FROM arrivals WHERE received_at_unix_ns=0 "
        "ORDER BY slot DESC LIMIT ?", (limit,)
    ).fetchall()
    recovered = 0
    for signature, slot in rows:
        received_ns = common.compact_receipt_ns(
            connection, compact_path, str(signature), int(slot)
        )
        if received_ns is None:
            continue
        received_at = (
            dt.datetime.fromtimestamp(received_ns / 1_000_000_000, tz=dt.timezone.utc)
            .isoformat().replace("+00:00", "Z")
        )
        connection.execute(
            "UPDATE arrivals SET received_at_utc=?,received_at_unix_ns=? WHERE signature=?",
            (received_at, received_ns, str(signature)),
        )
        connection.execute(
            "UPDATE events SET received_at_unix_ns=? WHERE signature=?",
            (received_ns, str(signature)),
        )
        recovered += 1
    return recovered, len(rows) - recovered


def discover_selected_pool_history(
    connection: sqlite3.Connection, rpc: common.Rpc, compact_path: pathlib.Path,
    max_signatures_per_pool: int,
) -> dict[str, int]:
    counters = {
        "poolN": 0, "completedPoolN": 0, "truncatedPoolN": 0,
        "discovered": 0, "withRealtimeArrival": 0, "withoutRealtimeArrival": 0,
        "rateLimited": 0,
    }
    rows = connection.execute(
        "SELECT pool,head_signature,history_cursor,history_complete FROM selected_pools"
    ).fetchall()
    for pool, prior_head, history_cursor, prior_complete in rows:
        counters["poolN"] += 1
        newest = None
        discovered_for_pool = 0
        complete = bool(prior_complete)

        # First collect transactions newer than the prior head. This keeps a
        # selected pool current without replaying unrelated global traffic.
        before = None
        while prior_head and discovered_for_pool < max_signatures_per_pool:
            options: dict[str, Any] = {
                "commitment": "finalized",
                "limit": min(1000, max_signatures_per_pool - discovered_for_pool),
                "until": str(prior_head),
            }
            if before:
                options["before"] = before
            try:
                page = rpc.call("getSignaturesForAddress", [str(pool), options]) or []
            except RpcRateLimited:
                counters["rateLimited"] += 1
                connection.commit()
                return counters
            if not page:
                break
            if newest is None:
                newest = str(page[0]["signature"])
            for row in reversed(page):
                signature, slot = str(row["signature"]), int(row["slot"])
                received_ns = common.compact_receipt_ns(
                    connection, compact_path, signature, slot
                )
                counters[
                    "withRealtimeArrival" if received_ns is not None else "withoutRealtimeArrival"
                ] += 1
                counters["discovered"] += insert_signature(
                    connection, compact_path, signature, slot
                )
            discovered_for_pool += len(page)
            before = str(page[-1]["signature"])
            if len(page) < 1000:
                break

        # Then spend the remaining bounded budget walking backward to pool
        # creation. history_cursor is durable, so large pools make progress
        # across runs instead of restarting the scan.
        cursor = str(history_cursor) if history_cursor else None
        if not complete:
            before = cursor
            while discovered_for_pool < max_signatures_per_pool:
                options = {
                    "commitment": "finalized",
                    "limit": min(1000, max_signatures_per_pool - discovered_for_pool),
                }
                if before:
                    options["before"] = before
                try:
                    page = rpc.call("getSignaturesForAddress", [str(pool), options]) or []
                except RpcRateLimited:
                    counters["rateLimited"] += 1
                    connection.commit()
                    return counters
                if not page:
                    complete = True
                    break
                if prior_head is None and newest is None:
                    newest = str(page[0]["signature"])
                for row in reversed(page):
                    signature, slot = str(row["signature"]), int(row["slot"])
                    received_ns = common.compact_receipt_ns(
                        connection, compact_path, signature, slot
                    )
                    counters[
                        "withRealtimeArrival" if received_ns is not None else "withoutRealtimeArrival"
                    ] += 1
                    counters["discovered"] += insert_signature(
                        connection, compact_path, signature, slot
                    )
                discovered_for_pool += len(page)
                cursor = str(page[-1]["signature"])
                before = cursor
                if len(page) < 1000:
                    complete = True
                    break
        truncated = not complete
        counters["completedPoolN"] += int(complete)
        counters["truncatedPoolN"] += int(truncated)
        connection.execute(
            "UPDATE selected_pools SET head_signature=COALESCE(?,head_signature),history_cursor=?,"
            "history_complete=?,history_truncated=?,history_signature_n=history_signature_n+? "
            "WHERE pool=?",
            (newest, cursor, int(complete), int(truncated), discovered_for_pool, str(pool)),
        )
        connection.commit()
    return counters


def reconcile(
    connection: sqlite3.Connection, rpc: common.Rpc, cache: pathlib.Path, limit: int
) -> dict[str, int]:
    counters = {
        "attempted": 0, "finalized": 0, "successful": 0, "exact": 0,
        "errors": 0, "rateLimited": 0,
    }
    rows = connection.execute(
        "SELECT * FROM arrivals WHERE finalized=0 ORDER BY slot LIMIT ?", (limit,)
    ).fetchall()
    for row in rows:
        counters["attempted"] += 1
        signature = str(row["signature"])
        try:
            tx = common.fetch_transaction(rpc, signature, cache)
            if tx is None:
                continue
            slot = int(tx.get("slot") or row["slot"])
            tx_index = common.canonical_index(rpc, slot, signature, cache)
            successful = int((tx.get("meta") or {}).get("err") is None)
            events = pumpswap.validate_transaction(tx) if successful else []
            classification = "EXACT_TRADE" if events else (
                "FINALIZED_NON_TRADE" if successful else "FINALIZED_FAILED"
            )
            for index, event in enumerate(events):
                connection.execute(
                    "INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        signature, index, slot, tx_index, int(row["received_at_unix_ns"]),
                        event["pool"], event["user"], event["baseMint"], event["quoteMint"],
                        event["side"], event["amountMode"], str(event["amountRaw"]),
                        str(event["curveQuoteAmountRaw"]), str(event["poolQuoteDeltaRaw"]),
                        str(event["userQuoteAmountRaw"]), str(event["lpFeeRaw"]),
                        str(event["protocolFeeRaw"]), str(event["creatorFeeRaw"]),
                        str(event["cashbackRaw"]), str(event["buybackFeeRaw"]),
                        json.dumps(event["preState"], sort_keys=True),
                        json.dumps(event["postState"], sort_keys=True),
                    ),
                )
            connection.execute(
                "UPDATE arrivals SET finalized=1,successful=?,canonical_tx_index=?,classification=?,error=NULL "
                "WHERE signature=?", (successful, tx_index, classification, signature)
            )
            counters["finalized"] += 1
            counters["successful"] += successful
            counters["exact"] += len(events)
        except RpcRateLimited:
            counters["rateLimited"] += 1
            break
        except pumpswap.PumpSwapExactError as error:
            connection.execute(
                "UPDATE arrivals SET finalized=1,successful=1,classification='INEXACT_EXCLUDED',error=? "
                "WHERE signature=?", (str(error)[:1000], signature)
            )
            counters["finalized"] += 1
            counters["errors"] += 1
        except Exception as error:
            connection.execute(
                "UPDATE arrivals SET error=? WHERE signature=?", (str(error)[:1000], signature)
            )
            counters["errors"] += 1
        if counters["attempted"] % 100 == 0:
            connection.commit()
    connection.commit()
    return counters


def continuity(connection: sqlite3.Connection) -> tuple[int, int, int]:
    current_pool = None
    prior = None
    pools = comparisons = gaps = 0
    for pool, pre, post in connection.execute(
        "SELECT pool,pre_state_json,post_state_json FROM events "
        "ORDER BY pool,slot,canonical_tx_index,event_index"
    ):
        if pool != current_pool:
            current_pool, prior = pool, None
            pools += 1
        if prior is not None:
            comparisons += 1
            gaps += int(prior != pre)
        prior = post
    return pools, comparisons, gaps


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(args.output / "pumpswap-state-v1.sqlite3")
    connection.row_factory = sqlite3.Row
    schema(connection)
    if common.metadata_get(connection, "buy_mode_rounding_v5_migrated") != "1":
        connection.execute(
            "UPDATE arrivals SET finalized=0,successful=NULL,classification=NULL,error=NULL "
            "WHERE classification='INEXACT_EXCLUDED'"
        )
        common.metadata_set(connection, "buy_mode_rounding_v5_migrated", "1")
        connection.commit()
    compact_slots_bootstrapped = bootstrap_compact_index(
        connection, args.compact_index_state
    )
    compact_records, compact_offset = common.index_compact_arrivals(
        connection, args.compact_arrivals
    )
    seeded = seed_teacher_signatures(
        connection, args.teacher_state, args.compact_arrivals
    )
    env = {**common.load_env(args.env), **os.environ}
    rpc_url = env.get("PUMP_EXACT_LEDGER_RPC_URL") or env.get("SECURE_RPC_URL")
    if not rpc_url:
        raise RuntimeError("PUMP_EXACT_LEDGER_RPC_URL/SECURE_RPC_URL missing")
    rpc = PacedRpc(rpc_url)
    selected_reconcile = reconcile(
        connection, rpc, args.output / "rpc-cache", args.limit
    )
    selected_pools_added = register_selected_pools(connection)
    discovered = discover_selected_pool_history(
        connection, rpc, args.compact_arrivals, args.max_signatures_per_pool
    )
    recovered, still_missing = backfill_recent_compact_receipts(
        connection, args.compact_arrivals
    )
    connection.commit()
    last_run = reconcile(connection, rpc, args.output / "rpc-cache", args.limit)
    pools, comparisons, gaps = continuity(connection)
    totals = dict(connection.execute(
        "SELECT COUNT(*) total,SUM(finalized) finalized,SUM(CASE WHEN successful=1 THEN 1 ELSE 0 END) successful,"
        "SUM(CASE WHEN classification='EXACT_TRADE' THEN 1 ELSE 0 END) exact_tx FROM arrivals"
    ).fetchone())
    exact_events = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    causal_events = int(connection.execute(
        "SELECT COUNT(*) FROM events e JOIN arrivals a USING(signature) WHERE a.received_at_unix_ns>0"
    ).fetchone()[0])
    unresolved = int(connection.execute(
        "SELECT COUNT(*) FROM arrivals WHERE finalized=0"
    ).fetchone()[0])
    inexact = int(connection.execute(
        "SELECT COUNT(*) FROM arrivals WHERE classification='INEXACT_EXCLUDED'"
    ).fetchone()[0])
    pool_history = dict(connection.execute(
        "SELECT COUNT(*) total,SUM(history_complete) complete,"
        "SUM(history_truncated) pending FROM selected_pools"
    ).fetchone())
    connection.close()
    report = {
        "schema": SCHEMA + "_HEALTH",
        "implementationVersion": IMPLEMENTATION_VERSION,
        "generatedAtUtc": common.now_utc(),
        "compactRecordsIndexedThisRun": compact_records,
        "compactSlotsBootstrappedThisRun": compact_slots_bootstrapped,
        "compactJournalOffset": compact_offset,
        "teacherSelectedSignaturesSeededThisRun": seeded,
        "teacherSelectedReconcile": selected_reconcile,
        "selectedPoolsAddedThisRun": selected_pools_added,
        "selectedPoolHistoryDiscovery": discovered,
        "realtimeArrivalBackfilledThisRun": recovered,
        "realtimeArrivalStillMissingN": still_missing,
        "arrivalsN": int(totals["total"] or 0),
        "finalizedN": int(totals["finalized"] or 0),
        "successfulN": int(totals["successful"] or 0),
        "exactTradeTransactionN": int(totals["exact_tx"] or 0),
        "exactTransitionN": exact_events,
        "causalRealtimeExactTransitionN": causal_events,
        "exactTransitionMissingRealtimeN": exact_events - causal_events,
        "unresolvedN": unresolved,
        "inexactExcludedN": inexact,
        "poolN": pools,
        "tradeToTradeContinuityComparisonsN": comparisons,
        "tradeToTradeDiscontinuityN": gaps,
        "tradeToTradeDiscontinuitySemantics": (
            "DESCRIPTIVE_ONLY_UNTIL_POOL_HISTORY_RECONCILED; each trade event carries "
            "its own authoritative pre/post state"
        ),
        "selectedPoolHistoryTotalN": int(pool_history.get("total") or 0),
        "selectedPoolHistoryCompleteN": int(pool_history.get("complete") or 0),
        "selectedPoolHistoryPendingN": int(pool_history.get("pending") or 0),
        "lastRun": last_run,
        "causalAdmission": (
            "FAIL_CLOSED_ON_MISSING_REALTIME_FOR_TIMED_FEATURES_OR_INCOMPLETE_POOL_HISTORY"
        ),
        "collectionScope": "TEACHER_SELECTED_POOL_HISTORY_ONLY",
        "globalProgramPolling": False,
        "officialSemantics": {
            "constantProduct": True,
            "effectiveQuoteReserves": "raw_quote_vault_plus_virtual_quote_reserves",
            "eventSource": "current official PumpSwap BuyEvent/SellEvent",
        },
        "integrity": {"outcomesRead": 0, "validationRowsRead": 0, "submissions": 0},
    }
    common.atomic_json(args.output / "health.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--teacher-state", type=pathlib.Path)
    parser.add_argument("--compact-index-state", type=pathlib.Path)
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--max-signatures-per-pool", type=int, default=10000)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
