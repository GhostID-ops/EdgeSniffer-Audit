#!/usr/bin/env python3
"""Prospective full-program PumpSwap evidence universe (research only)."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import pathlib
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import launchLabEvidenceReconcilerV1 as common
import pumpSwapEvidenceReconcilerV1 as selected
import pumpSwapExactEconomicsV1 as pumpswap


SCHEMA = "PUMPSWAP_GLOBAL_PROSPECTIVE_EVIDENCE_V1"
IMPLEMENTATION_VERSION = "1.5.0"
RPC_WORKERS = 32


class BatchRpc(selected.PacedRpc):
    def __init__(self, url: str) -> None:
        super().__init__(url)
        self._request_lock = threading.Lock()

    def batch(self, method: str, params: list[list[Any]]) -> list[Any]:
        if not params:
            return []
        requests = []
        ids: list[int] = []
        with self._request_lock:
            for item in params:
                self.request_id += 1
                ids.append(self.request_id)
                requests.append({
                    "jsonrpc": "2.0", "id": self.request_id,
                    "method": method, "params": item,
                })
        payload = json.dumps(requests).encode()
        for attempt, delay in enumerate((0.0, 5.0, 15.0, 30.0)):
            if delay:
                time.sleep(delay)
            request = urllib.request.Request(
                self.url, data=payload, headers={"content-type": "application/json"}
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    rows = json.loads(response.read())
                by_id = {int(row["id"]): row for row in rows}
                results: list[Any] = []
                for request_id in ids:
                    row = by_id.get(request_id)
                    if row is None or row.get("error"):
                        results.append(None)
                    else:
                        results.append(row.get("result"))
                return results
            except urllib.error.HTTPError as error:
                if error.code == 400:
                    if len(params) == 1:
                        return [self.call(method, params[0])]
                    midpoint = len(params) // 2
                    return self.batch(method, params[:midpoint]) + self.batch(
                        method, params[midpoint:]
                    )
                if error.code != 429 or attempt == 3:
                    raise
        raise selected.RpcRateLimited(f"RPC_429_BATCH:{method}")


def index_compact_arrivals_by_slot(
    connection: sqlite3.Connection, path: pathlib.Path,
) -> tuple[int, int]:
    """Index new compact receipts with one SQLite upsert per slot."""
    if not path.exists():
        raise RuntimeError("compact AllenHark arrival journal missing")
    stored_offset = common.metadata_get(connection, "compact_offset")
    with path.open("rb") as stream:
        if stream.read(len(common.COMPACT_MAGIC)) != common.COMPACT_MAGIC:
            raise RuntimeError("invalid compact AllenHark journal magic")
        size = path.stat().st_size
        if stored_offset is None:
            offset = len(common.COMPACT_MAGIC) + (
                (size - len(common.COMPACT_MAGIC)) // common.COMPACT_RECORD.size
            ) * common.COMPACT_RECORD.size
            common.metadata_set(connection, "compact_offset", str(offset))
            return 0, offset
        offset = int(stored_offset)
        if size < offset:
            raise RuntimeError("compact AllenHark journal truncated")
        stream.seek(offset)
        records = 0
        slots: dict[int, list[int]] = {}
        while True:
            start = stream.tell()
            raw = stream.read(common.COMPACT_RECORD.size)
            if not raw:
                break
            if len(raw) != common.COMPACT_RECORD.size:
                stream.seek(start)
                break
            _, received_ns, slot = common.COMPACT_RECORD.unpack(raw)
            end = stream.tell()
            values = slots.setdefault(
                int(slot), [start, end, int(received_ns), int(received_ns)]
            )
            values[0] = min(values[0], start)
            values[1] = max(values[1], end)
            values[2] = min(values[2], int(received_ns))
            values[3] = max(values[3], int(received_ns))
            offset = end
            records += 1
    connection.executemany(
        "INSERT INTO compact_slots(slot,start_offset,end_offset,first_received_ns,last_received_ns) "
        "VALUES(?,?,?,?,?) ON CONFLICT(slot) DO UPDATE SET "
        "start_offset=MIN(start_offset,excluded.start_offset),"
        "end_offset=MAX(end_offset,excluded.end_offset),"
        "first_received_ns=MIN(first_received_ns,excluded.first_received_ns),"
        "last_received_ns=MAX(last_received_ns,excluded.last_received_ns)",
        [(slot, *values) for slot, values in slots.items()],
    )
    common.metadata_set(connection, "compact_offset", str(offset))
    return records, offset


def global_schema(connection: sqlite3.Connection) -> None:
    selected.schema(connection)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS exact_gaps(
            signature TEXT NOT NULL,
            pool TEXT,
            slot INTEGER NOT NULL,
            canonical_tx_index INTEGER NOT NULL,
            received_at_unix_ns INTEGER NOT NULL,
            error TEXT NOT NULL,
            PRIMARY KEY(signature,pool)
        );
        CREATE INDEX IF NOT EXISTS exact_gaps_pool_order
            ON exact_gaps(pool,slot,canonical_tx_index);
    """)


def discover_program_signatures(
    connection: sqlite3.Connection, rpc: selected.PacedRpc,
    compact_path: pathlib.Path, max_signatures: int,
) -> dict[str, Any]:
    prior_head = common.metadata_get(connection, "program_head_signature")
    scan_new_head = common.metadata_get(connection, "scan_new_head")
    scan_old_head = common.metadata_get(connection, "scan_old_head")
    scan_cursor = common.metadata_get(connection, "scan_cursor")
    first_page_limit = min(1000, max_signatures)
    page = rpc.call("getSignaturesForAddress", [pumpswap.PROGRAM_ID, {
        "commitment": "finalized", "limit": first_page_limit,
        **({"before": scan_cursor} if scan_cursor else {}),
        **({"until": scan_old_head} if scan_new_head and scan_old_head else
           {"until": prior_head} if prior_head else {}),
    }]) or []
    if prior_head is None:
        if page:
            common.metadata_set(connection, "program_head_signature", str(page[0]["signature"]))
        start = common.now_utc()
        common.metadata_set(connection, "cohort_start_utc", start)
        connection.commit()
        return {
            "initialized": True, "cohortStartUtc": start, "discovered": 0,
            "withRealtimeArrival": 0, "withoutRealtimeArrival": 0,
            "truncated": False,
        }
    if not scan_new_head and page:
        scan_new_head = str(page[0]["signature"])
        scan_old_head = prior_head
    pages = list(page)
    before = str(page[-1]["signature"]) if len(page) == first_page_limit else None
    while before and len(pages) < max_signatures:
        next_page_limit = min(1000, max_signatures - len(pages))
        next_page = rpc.call("getSignaturesForAddress", [pumpswap.PROGRAM_ID, {
            "commitment": "finalized", "limit": next_page_limit,
            "before": before, **({"until": scan_old_head} if scan_old_head else {}),
        }]) or []
        pages.extend(next_page)
        before = (
            str(next_page[-1]["signature"])
            if len(next_page) == next_page_limit else None
        )
    scan_complete = before is None
    if pages and not scan_complete:
        common.metadata_set(connection, "scan_new_head", str(scan_new_head))
        common.metadata_set(connection, "scan_old_head", str(scan_old_head or ""))
        common.metadata_set(connection, "scan_cursor", str(pages[-1]["signature"]))
    elif scan_complete and scan_new_head:
        common.metadata_set(connection, "program_head_signature", str(scan_new_head))
        connection.execute(
            "DELETE FROM metadata WHERE key IN ('scan_new_head','scan_old_head','scan_cursor')"
        )
    with_realtime = without_realtime = inserted = 0
    for row in reversed(pages):
        signature, slot = str(row["signature"]), int(row["slot"])
        receipt = common.compact_receipt_ns(connection, compact_path, signature, slot)
        if receipt is None:
            without_realtime += 1
        else:
            with_realtime += 1
        inserted += selected.insert_signature(connection, compact_path, signature, slot)
    connection.commit()
    return {
        "initialized": False,
        "cohortStartUtc": common.metadata_get(connection, "cohort_start_utc"),
        "discovered": inserted,
        "withRealtimeArrival": with_realtime,
        "withoutRealtimeArrival": without_realtime,
        "truncated": not scan_complete,
        "durableCursor": str(pages[-1]["signature"]) if pages and not scan_complete else None,
        "scanCommitted": scan_complete,
    }


def fetch_transactions(
    rpc: BatchRpc, signatures: list[str], cache: pathlib.Path | None,
) -> dict[str, dict[str, Any] | None]:
    results: dict[str, dict[str, Any] | None] = {}
    missing: list[str] = []
    for signature in signatures:
        path = cache / "transactions" / f"{signature}.json" if cache else None
        if path is not None and path.exists():
            results[signature] = json.loads(path.read_text(encoding="utf-8"))
        else:
            missing.append(signature)
    batches = [missing[offset:offset + 10] for offset in range(0, len(missing), 10)]

    def fetch(batch: list[str]) -> tuple[list[str], list[Any]]:
        values = rpc.batch("getTransaction", [[signature, {
            "commitment": "finalized", "encoding": "json",
            "maxSupportedTransactionVersion": 0,
        }] for signature in batch])
        return batch, values

    with concurrent.futures.ThreadPoolExecutor(max_workers=RPC_WORKERS) as executor:
        fetched = executor.map(fetch, batches)
        for batch, values in fetched:
            for signature, value in zip(batch, values):
                results[signature] = value
                if value is not None and cache is not None:
                    path = cache / "transactions" / f"{signature}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    common.atomic_json(path, value)
    return results


def fetch_block_signatures(
    rpc: BatchRpc, slots: list[int], cache: pathlib.Path | None,
) -> dict[int, list[str]]:
    results: dict[int, list[str]] = {}
    missing: list[int] = []
    for slot in sorted(set(slots)):
        path = cache / "blocks" / f"{slot}.json" if cache else None
        if path is not None and path.exists():
            results[slot] = json.loads(path.read_text(encoding="utf-8"))
        else:
            missing.append(slot)

    def fetch(slot: int) -> tuple[int, Any]:
        worker_rpc = selected.PacedRpc(rpc.url, min_interval_s=0.0)
        value = worker_rpc.call("getBlock", [slot, {
            "commitment": "finalized", "transactionDetails": "signatures",
            "rewards": False, "maxSupportedTransactionVersion": 0,
        }])
        return slot, value

    with concurrent.futures.ThreadPoolExecutor(max_workers=RPC_WORKERS) as executor:
        fetched = executor.map(fetch, missing)
        for slot, value in fetched:
            signatures = list((value or {}).get("signatures") or [])
            results[slot] = signatures
            if value is not None and cache is not None:
                path = cache / "blocks" / f"{slot}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                common.atomic_json(path, signatures)
    return results


def reconcile_batched(
    connection: sqlite3.Connection, rpc: BatchRpc, cache: pathlib.Path | None, limit: int,
) -> dict[str, int]:
    rows = connection.execute(
        "SELECT * FROM arrivals WHERE finalized=0 ORDER BY slot LIMIT ?", (limit,)
    ).fetchall()
    counters = {"attempted": len(rows), "finalized": 0, "successful": 0,
                "exact": 0, "errors": 0, "batchRpc": 1}
    signatures = [str(row["signature"]) for row in rows]
    transactions = fetch_transactions(rpc, signatures, cache)
    slots = [int(tx.get("slot") or row["slot"]) for row in rows
             if (tx := transactions.get(str(row["signature"]))) is not None]
    blocks = fetch_block_signatures(rpc, slots, cache)
    for row in rows:
        signature = str(row["signature"])
        tx = transactions.get(signature)
        if tx is None:
            continue
        try:
            slot = int(tx.get("slot") or row["slot"])
            tx_index = blocks.get(slot, []).index(signature)
            successful = int((tx.get("meta") or {}).get("err") is None)
            events = pumpswap.validate_transaction(tx) if successful else []
            classification = "EXACT_TRADE" if events else (
                "FINALIZED_NON_TRADE" if successful else "FINALIZED_FAILED"
            )
            connection.execute("DELETE FROM exact_gaps WHERE signature=?", (signature,))
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
                "UPDATE arrivals SET finalized=1,successful=?,canonical_tx_index=?,"
                "classification=?,error=NULL WHERE signature=?",
                (successful, tx_index, classification, signature),
            )
            counters["finalized"] += 1
            counters["successful"] += successful
            counters["exact"] += len(events)
        except pumpswap.PumpSwapExactError as error:
            counters["errors"] += 1
            try:
                pools = sorted({row.pool for row in pumpswap.decode_instructions(tx)}) or [None]
            except pumpswap.PumpSwapExactError:
                pools = [None]
            message = f"{type(error).__name__}:{error}"[:1000]
            for pool in pools:
                connection.execute(
                    "INSERT OR REPLACE INTO exact_gaps VALUES(?,?,?,?,?,?)",
                    (
                        signature, pool, slot, tx_index,
                        int(row["received_at_unix_ns"]), message,
                    ),
                )
            connection.execute(
                "UPDATE arrivals SET finalized=1,successful=1,canonical_tx_index=?,"
                "classification='INEXACT_EXCLUDED',error=? WHERE signature=?",
                (tx_index, message, signature),
            )
            counters["finalized"] += 1
            counters["successful"] += 1
        except Exception as error:
            counters["errors"] += 1
            connection.execute(
                "UPDATE arrivals SET error=? WHERE signature=?",
                (f"{type(error).__name__}:{error}"[:1000], signature),
            )
    connection.commit()
    return counters


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(args.output / "pumpswap-global-state-v1.sqlite3")
    connection.row_factory = sqlite3.Row
    global_schema(connection)
    compact_bootstrap = selected.bootstrap_compact_index(connection, args.compact_index_state)
    compact_records, compact_offset = index_compact_arrivals_by_slot(
        connection, args.compact_arrivals
    )
    env = {**common.load_env(args.env), **os.environ}
    rpc_url = env.get("PUMP_EXACT_LEDGER_RPC_URL") or env.get("SECURE_RPC_URL")
    if not rpc_url:
        raise RuntimeError("PUMP_EXACT_LEDGER_RPC_URL/SECURE_RPC_URL missing")
    rpc = BatchRpc(rpc_url)
    discovered = discover_program_signatures(
        connection, rpc, args.compact_arrivals, args.max_signatures
    )
    recovered, missing_receipts = selected.backfill_recent_compact_receipts(
        connection, args.compact_arrivals, args.receipt_backfill_limit
    )
    reconciled = reconcile_batched(connection, rpc, None, args.limit)
    totals = dict(connection.execute(
        "SELECT COUNT(*) total,SUM(finalized) finalized,"
        "SUM(CASE WHEN classification='EXACT_TRADE' THEN 1 ELSE 0 END) exact_tx "
        "FROM arrivals"
    ).fetchone())
    exact_events = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    causal_events = int(connection.execute(
        "SELECT COUNT(*) FROM events WHERE received_at_unix_ns>0"
    ).fetchone()[0])
    unresolved = int(connection.execute(
        "SELECT COUNT(*) FROM arrivals WHERE finalized=0"
    ).fetchone()[0])
    inexact = int(connection.execute(
        "SELECT COUNT(*) FROM arrivals WHERE classification='INEXACT_EXCLUDED'"
    ).fetchone()[0])
    exact_gaps = int(connection.execute("SELECT COUNT(*) FROM exact_gaps").fetchone()[0])
    poolless_gaps = int(connection.execute(
        "SELECT COUNT(*) FROM exact_gaps WHERE pool IS NULL"
    ).fetchone()[0])
    max_finalized_slot = int(connection.execute(
        "SELECT COALESCE(MAX(slot),0) FROM arrivals WHERE finalized=1"
    ).fetchone()[0])
    connection.close()
    report = {
        "schema": SCHEMA + "_HEALTH",
        "implementationVersion": IMPLEMENTATION_VERSION,
        "generatedAtUtc": common.now_utc(),
        "collectionScope": "FULL_PUMPSWAP_PROGRAM_PROSPECTIVE_ONLY",
        "cohortStartUtc": discovered["cohortStartUtc"],
        "compactSlotsBootstrappedThisRun": compact_bootstrap,
        "compactRecordsIndexedThisRun": compact_records,
        "compactJournalOffset": compact_offset,
        "programDiscovery": discovered,
        "realtimeArrivalBackfilledThisRun": recovered,
        "recentRealtimeArrivalStillMissingN": missing_receipts,
        "arrivalsN": int(totals.get("total") or 0),
        "finalizedN": int(totals.get("finalized") or 0),
        "exactTradeTransactionN": int(totals.get("exact_tx") or 0),
        "exactTransitionN": exact_events,
        "causalRealtimeExactTransitionN": causal_events,
        "exactTransitionMissingRealtimeN": exact_events - causal_events,
        "unresolvedN": unresolved,
        "inexactExcludedN": inexact,
        "exactGapN": exact_gaps,
        "poollessExactGapN": poolless_gaps,
        "maxFinalizedSlot": max_finalized_slot,
        "lastRun": reconciled,
        "rpcWorkers": RPC_WORKERS,
        "rawRpcCacheRetained": False,
        "symmetricUniverseAuthority": "UNBIASED_FUTURE_PROGRAM_POPULATION",
        "integrity": {"outcomesRead": 0, "validationRowsRead": 0, "submissions": 0},
    }
    common.atomic_json(args.output / "health.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--compact-index-state", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--max-signatures", type=int, default=10000)
    parser.add_argument("--receipt-backfill-limit", type=int, default=10000)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
