#!/usr/bin/env python3
"""Exact PumpSwap state planes for bounded 2CQ symmetric-choice windows."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3
from typing import Any

import launchLabEvidenceReconcilerV1 as common
import pumpSwapEvidenceReconcilerV1 as selected
import pumpSwapExactEconomicsV1 as pumpswap
import pumpSwapGlobalEvidenceReconcilerV1 as global_source


SCHEMA = "2CQ_PUMPSWAP_CHOICE_WINDOW_EVIDENCE_V1"
IMPLEMENTATION_VERSION = "1.0.1"
WINDOW_SLOTS = 20


def invokes_pumpswap(tx: dict[str, Any]) -> bool:
    keys = pumpswap._message_keys(tx)
    for _, instruction in pumpswap._compiled_instructions(tx):
        program = instruction.get("programId")
        if program is None and instruction.get("programIdIndex") is not None:
            index = int(instruction["programIdIndex"])
            program = keys[index] if index < len(keys) else None
        if program == pumpswap.PROGRAM_ID:
            return True
    return False


def schema(connection: sqlite3.Connection) -> None:
    global_source.global_schema(connection)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS choice_windows(
            selected_signature TEXT PRIMARY KEY,
            selected_slot INTEGER NOT NULL,
            start_slot INTEGER NOT NULL,
            end_slot INTEGER NOT NULL,
            status TEXT NOT NULL,
            pump_transaction_n INTEGER NOT NULL,
            exact_event_n INTEGER NOT NULL,
            exact_gap_n INTEGER NOT NULL,
            error TEXT,
            updated_utc TEXT NOT NULL
        );
    """)


def selected_decisions(source_state: pathlib.Path, route_state: pathlib.Path) -> list[tuple[str, int]]:
    source = sqlite3.connect(f"file:{source_state}?mode=ro", uri=True)
    routes = sqlite3.connect(f"file:{route_state}?mode=ro", uri=True)
    exact = {
        str(row[0]) for row in routes.execute(
            "SELECT signature FROM route_evidence WHERE "
            "classification='EXACT_SELECTED_TRANSITION' AND resolved_venue='PUMPSWAP'"
        )
    }
    rows = [
        (str(signature), int(slot))
        for signature, slot in source.execute(
            "SELECT signature,slot FROM current_decisions ORDER BY slot,transaction_index,signature"
        )
        if str(signature) in exact
    ]
    source.close()
    routes.close()
    return rows


def fetch_block(rpc: selected.PacedRpc, cache: pathlib.Path, slot: int) -> dict[str, Any] | None:
    path = cache / f"{slot}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    value = rpc.call("getBlock", [slot, {
        "commitment": "finalized", "encoding": "json",
        "transactionDetails": "full", "rewards": False,
        "maxSupportedTransactionVersion": 0,
    }])
    if value is not None:
        common.atomic_json(path, value)
    return value


def persist_transaction(
    connection: sqlite3.Connection, compact_path: pathlib.Path,
    tx: dict[str, Any], signature: str, slot: int, tx_index: int,
) -> tuple[int, int]:
    selected.insert_signature(connection, compact_path, signature, slot)
    row = connection.execute(
        "SELECT received_at_unix_ns FROM arrivals WHERE signature=?", (signature,)
    ).fetchone()
    received_ns = int(row[0]) if row else 0
    meta = tx.get("meta") or {}
    successful = meta.get("err") is None
    events: list[dict[str, Any]] = []
    try:
        events = pumpswap.validate_transaction(tx) if successful else []
        classification = "EXACT_TRADE" if events else (
            "FINALIZED_NON_TRADE" if successful else "FINALIZED_FAILED"
        )
        connection.execute("DELETE FROM exact_gaps WHERE signature=?", (signature,))
        for index, event in enumerate(events):
            connection.execute(
                "INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    signature, index, slot, tx_index, received_ns,
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
            (int(successful), tx_index, classification, signature),
        )
        return len(events), 0
    except pumpswap.PumpSwapExactError as error:
        try:
            pools = sorted({row.pool for row in pumpswap.decode_instructions(tx)}) or [None]
        except pumpswap.PumpSwapExactError:
            pools = [None]
        message = f"{type(error).__name__}:{error}"[:1000]
        for pool in pools:
            connection.execute(
                "INSERT OR REPLACE INTO exact_gaps VALUES(?,?,?,?,?,?)",
                (signature, pool, slot, tx_index, received_ns, message),
            )
        connection.execute(
            "UPDATE arrivals SET finalized=1,successful=1,canonical_tx_index=?,"
            "classification='INEXACT_EXCLUDED',error=? WHERE signature=?",
            (tx_index, message, signature),
        )
        return 0, len(pools)


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(args.output / "pumpswap-choice-window-state-v1.sqlite3")
    connection.row_factory = sqlite3.Row
    schema(connection)
    selected.bootstrap_compact_index(connection, args.compact_index_state)
    compact_records, compact_offset = global_source.index_compact_arrivals_by_slot(
        connection, args.compact_arrivals
    )
    env = {**common.load_env(args.env), **os.environ}
    rpc_url = env.get("PUMP_EXACT_LEDGER_RPC_URL") or env.get("SECURE_RPC_URL")
    if not rpc_url:
        raise RuntimeError("PUMP_EXACT_LEDGER_RPC_URL/SECURE_RPC_URL missing")
    rpc = selected.PacedRpc(rpc_url)
    decisions = selected_decisions(args.source_state, args.route_state)
    existing = {
        str(row[0]) for row in connection.execute(
            "SELECT selected_signature FROM choice_windows WHERE status='COMPLETE'"
        )
    }
    processed = 0
    for selected_signature, selected_slot in decisions:
        if selected_signature in existing:
            continue
        start_slot = max(0, selected_slot - WINDOW_SLOTS)
        pump_n = exact_n = gap_n = 0
        errors: list[str] = []
        for slot in range(start_slot, selected_slot + 1):
            try:
                block = fetch_block(rpc, args.output / "block-cache", slot)
            except Exception as error:
                errors.append(f"slot={slot}:{type(error).__name__}:{error}"[:1000])
                continue
            for tx_index, tx in enumerate((block or {}).get("transactions") or []):
                tx = dict(tx) | {"slot": slot}
                if not invokes_pumpswap(tx):
                    continue
                signatures = ((tx.get("transaction") or {}).get("signatures") or [])
                if not signatures:
                    errors.append(f"slot={slot}:tx={tx_index}:MISSING_SIGNATURE")
                    continue
                signature = str(signatures[0])
                pump_n += 1
                try:
                    exact, gaps = persist_transaction(
                        connection, args.compact_arrivals, tx, signature, slot, tx_index
                    )
                    exact_n += exact
                    gap_n += gaps
                except Exception as error:
                    errors.append(
                        f"slot={slot}:tx={tx_index}:{type(error).__name__}:{error}"[:1000]
                    )
        selected_event = connection.execute(
            "SELECT 1 FROM events WHERE signature=? LIMIT 1", (selected_signature,)
        ).fetchone()
        status = "COMPLETE" if not errors and selected_event else "DEFERRED"
        if selected_event is None:
            errors.append("SELECTED_EXACT_EVENT_MISSING")
        connection.execute(
            "INSERT OR REPLACE INTO choice_windows VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                selected_signature, selected_slot, start_slot, selected_slot, status,
                pump_n, exact_n, gap_n, ";".join(errors)[:4000] or None,
                dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
        )
        connection.commit()
        processed += 1
    recovered, still_missing = selected.backfill_recent_compact_receipts(
        connection, args.compact_arrivals, 50000
    )
    counts = dict(connection.execute(
        "SELECT status,COUNT(*) FROM choice_windows GROUP BY status"
    ).fetchall())
    totals = connection.execute(
        "SELECT COUNT(*),SUM(finalized),SUM(classification='INEXACT_EXCLUDED') FROM arrivals"
    ).fetchone()
    event_n = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    causal_n = int(connection.execute(
        "SELECT COUNT(*) FROM events WHERE received_at_unix_ns>0"
    ).fetchone()[0])
    connection.close()
    shutil.rmtree(args.output / "block-cache", ignore_errors=True)
    report = {
        "schema": SCHEMA + "_HEALTH", "implementationVersion": IMPLEMENTATION_VERSION,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "windowSlots": WINDOW_SLOTS, "selectedDecisionN": len(decisions),
        "processedThisRun": processed, "windowStatusCounts": counts,
        "pumpTransactionsN": int(totals[0] or 0), "finalizedN": int(totals[1] or 0),
        "inexactExcludedN": int(totals[2] or 0), "exactTransitionN": event_n,
        "causalRealtimeExactTransitionN": causal_n,
        "exactTransitionMissingRealtimeN": event_n - causal_n,
        "compactRecordsIndexedThisRun": compact_records,
        "compactJournalOffset": compact_offset,
        "realtimeArrivalBackfilledThisRun": recovered,
        "recentRealtimeArrivalStillMissingN": still_missing,
        "universeAuthority": "TEACHER_BOUNDARY_CONDITIONED_CANONICAL_FINALIZED_WINDOWS",
        "rawBlockCacheRetained": False,
        "integrity": {"outcomesRead": 0, "validationRowsRead": 0, "submissions": 0},
    }
    common.atomic_json(args.output / "health.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-state", type=pathlib.Path, required=True)
    parser.add_argument("--route-state", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--compact-index-state", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
