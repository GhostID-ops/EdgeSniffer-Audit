#!/usr/bin/env python3
"""Finalize and normalize the prospective AllenHark LaunchLab evidence tape."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
import struct
import urllib.request
from typing import Any

import launchLabExactEconomicsV1 as launchlab


SCHEMA = "LAUNCHLAB_EVIDENCE_LEDGER_V1"
COMPACT_MAGIC = b"AHCDV1\n\0"
COMPACT_RECORD = struct.Struct("<32sqQ")
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
    os.replace(temporary, path)


def load_env(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"").strip("'")
    return values


class Rpc:
    def __init__(self, url: str) -> None:
        self.url = url
        self.request_id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        self.request_id += 1
        payload = json.dumps({
            "jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params
        }).encode()
        request = urllib.request.Request(
            self.url, data=payload, headers={"content-type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.loads(response.read())
        if value.get("error"):
            raise RuntimeError(f"RPC {method}: {value['error']}")
        return value.get("result")


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
            observed_ordinal INTEGER NOT NULL,
            direct_instruction_json TEXT NOT NULL,
            finalized INTEGER NOT NULL DEFAULT 0,
            successful INTEGER,
            canonical_tx_index INTEGER,
            classification TEXT,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS events(
            signature TEXT NOT NULL,
            event_index INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            canonical_tx_index INTEGER NOT NULL,
            received_at_unix_ns INTEGER NOT NULL,
            pool_state TEXT NOT NULL,
            payer TEXT NOT NULL,
            base_mint TEXT NOT NULL,
            quote_mint TEXT NOT NULL,
            side TEXT NOT NULL,
            amount_mode TEXT NOT NULL,
            curve_amount_in_raw TEXT NOT NULL,
            curve_amount_out_raw TEXT NOT NULL,
            curve_fee_raw TEXT NOT NULL,
            wallet_base_delta_raw TEXT,
            wallet_quote_delta_raw TEXT,
            base_transfer_fee_raw TEXT,
            quote_transfer_fee_raw TEXT,
            pre_state_json TEXT NOT NULL,
            post_state_json TEXT NOT NULL,
            PRIMARY KEY(signature,event_index)
        );
        CREATE INDEX IF NOT EXISTS events_pool_order
            ON events(pool_state,slot,canonical_tx_index,event_index);
        CREATE TABLE IF NOT EXISTS compact_slots(
            slot INTEGER PRIMARY KEY,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            first_received_ns INTEGER NOT NULL,
            last_received_ns INTEGER NOT NULL
        );
    """)


def ingest_arrivals(connection: sqlite3.Connection, path: pathlib.Path) -> tuple[int, int]:
    offset_row = connection.execute(
        "SELECT value FROM metadata WHERE key='arrival_offset'"
    ).fetchone()
    offset = int(offset_row[0]) if offset_row else 0
    if not path.exists() or path.stat().st_size < offset:
        raise RuntimeError("arrival journal missing or truncated")
    inserted = 0
    with path.open("rb") as stream:
        stream.seek(offset)
        while True:
            start = stream.tell()
            raw = stream.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                stream.seek(start)
                break
            row = json.loads(raw)
            connection.execute(
                "INSERT INTO arrivals(signature,received_at_utc,received_at_unix_ns,slot,"
                "observed_ordinal,direct_instruction_json) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(signature) DO UPDATE SET "
                "received_at_utc=excluded.received_at_utc,"
                "received_at_unix_ns=excluded.received_at_unix_ns,"
                "observed_ordinal=excluded.observed_ordinal,"
                "direct_instruction_json=excluded.direct_instruction_json",
                (
                    str(row["signature"]), str(row["receivedAtUtc"]), int(row["receivedAtUnixNs"]),
                    int(row["slot"]), int(row["observedSlotTransactionOrdinal"]),
                    json.dumps(row.get("directInstructions") or [], sort_keys=True),
                ),
            )
            inserted += int(connection.execute("SELECT changes()").fetchone()[0])
            offset = stream.tell()
    connection.execute(
        "INSERT INTO metadata(key,value) VALUES('arrival_offset',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(offset),)
    )
    return inserted, offset


def metadata_get(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row[0])


def metadata_set(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value)
    )


def index_compact_arrivals(
    connection: sqlite3.Connection, path: pathlib.Path
) -> tuple[int, int]:
    if not path.exists():
        raise RuntimeError("compact AllenHark arrival journal missing")
    stored_offset = metadata_get(connection, "compact_offset")
    with path.open("rb") as stream:
        if stream.read(len(COMPACT_MAGIC)) != COMPACT_MAGIC:
            raise RuntimeError("invalid compact AllenHark journal magic")
        size = path.stat().st_size
        if stored_offset is None:
            offset = len(COMPACT_MAGIC) + (
                (size - len(COMPACT_MAGIC)) // COMPACT_RECORD.size
            ) * COMPACT_RECORD.size
            metadata_set(connection, "compact_offset", str(offset))
            return 0, offset
        offset = int(stored_offset)
        if size < offset:
            raise RuntimeError("compact AllenHark journal truncated")
        stream.seek(offset)
        records = 0
        while True:
            start = stream.tell()
            raw = stream.read(COMPACT_RECORD.size)
            if not raw:
                break
            if len(raw) != COMPACT_RECORD.size:
                stream.seek(start)
                break
            _, received_ns, slot = COMPACT_RECORD.unpack(raw)
            end = stream.tell()
            connection.execute(
                "INSERT INTO compact_slots(slot,start_offset,end_offset,first_received_ns,last_received_ns) "
                "VALUES(?,?,?,?,?) ON CONFLICT(slot) DO UPDATE SET "
                "start_offset=MIN(start_offset,excluded.start_offset),"
                "end_offset=MAX(end_offset,excluded.end_offset),"
                "first_received_ns=MIN(first_received_ns,excluded.first_received_ns),"
                "last_received_ns=MAX(last_received_ns,excluded.last_received_ns)",
                (int(slot), start, end, int(received_ns), int(received_ns)),
            )
            offset = end
            records += 1
    metadata_set(connection, "compact_offset", str(offset))
    return records, offset


def base58_decode(value: str) -> bytes:
    number = 0
    for character in value:
        number = number * 58 + BASE58_ALPHABET.index(character)
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + decoded


def compact_receipt_ns(
    connection: sqlite3.Connection, path: pathlib.Path, signature: str, slot: int
) -> int | None:
    bounds = connection.execute(
        "SELECT start_offset,end_offset FROM compact_slots WHERE slot=?", (slot,)
    ).fetchone()
    if bounds is None:
        return None
    signature_bytes = base58_decode(signature)
    if len(signature_bytes) != 64:
        raise RuntimeError("invalid Solana signature")
    target = hashlib.sha256(signature_bytes).digest()
    with path.open("rb") as stream:
        stream.seek(int(bounds[0]))
        while stream.tell() < int(bounds[1]):
            raw = stream.read(COMPACT_RECORD.size)
            if len(raw) != COMPACT_RECORD.size:
                break
            digest, received_ns, record_slot = COMPACT_RECORD.unpack(raw)
            if int(record_slot) == slot and digest == target:
                return int(received_ns)
    return None


def discover_program_signatures(
    connection: sqlite3.Connection, rpc: Rpc, compact_path: pathlib.Path
) -> dict[str, int]:
    previous_head = metadata_get(connection, "launchlab_program_head")
    first_page = rpc.call("getSignaturesForAddress", [launchlab.PROGRAM_ID, {
        "commitment": "finalized", "limit": 1000,
        **({"until": previous_head} if previous_head else {}),
    }]) or []
    if not previous_head:
        if first_page:
            metadata_set(connection, "launchlab_program_head", str(first_page[0]["signature"]))
        return {"discovered": 0, "withRealtimeArrival": 0, "withoutRealtimeArrival": 0}
    pages = list(first_page)
    before = str(first_page[-1]["signature"]) if len(first_page) == 1000 else None
    while before:
        page = rpc.call("getSignaturesForAddress", [launchlab.PROGRAM_ID, {
            "commitment": "finalized", "limit": 1000, "before": before, "until": previous_head,
        }]) or []
        pages.extend(page)
        before = str(page[-1]["signature"]) if len(page) == 1000 else None
    if pages:
        metadata_set(connection, "launchlab_program_head", str(pages[0]["signature"]))
    counters = {"discovered": 0, "withRealtimeArrival": 0, "withoutRealtimeArrival": 0}
    for row in reversed(pages):
        signature = str(row["signature"])
        slot = int(row["slot"])
        received_ns = compact_receipt_ns(connection, compact_path, signature, slot)
        counters["withRealtimeArrival" if received_ns is not None else "withoutRealtimeArrival"] += 1
        received_ns = received_ns or 0
        received_at = (
            dt.datetime.fromtimestamp(received_ns / 1_000_000_000, tz=dt.timezone.utc)
            .isoformat().replace("+00:00", "Z") if received_ns else ""
        )
        connection.execute(
            "INSERT OR IGNORE INTO arrivals(signature,received_at_utc,received_at_unix_ns,slot,"
            "observed_ordinal,direct_instruction_json) VALUES(?,?,?,?,?,?)",
            (signature, received_at, received_ns, slot, -1, "[]"),
        )
        counters["discovered"] += int(connection.execute("SELECT changes()").fetchone()[0])
    return counters


def backfill_compact_receipts(
    connection: sqlite3.Connection, compact_path: pathlib.Path
) -> tuple[int, int]:
    rows = connection.execute(
        "SELECT signature,slot FROM arrivals WHERE received_at_unix_ns=0"
    ).fetchall()
    recovered = 0
    for signature, slot in rows:
        received_ns = compact_receipt_ns(connection, compact_path, str(signature), int(slot))
        if received_ns is None:
            continue
        received_at = (
            dt.datetime.fromtimestamp(received_ns / 1_000_000_000, tz=dt.timezone.utc)
            .isoformat().replace("+00:00", "Z")
        )
        connection.execute(
            "UPDATE arrivals SET received_at_utc=?,received_at_unix_ns=? WHERE signature=?",
            (received_at, received_ns, signature),
        )
        recovered += 1
    return recovered, len(rows) - recovered


def canonical_index(rpc: Rpc, slot: int, signature: str, cache: pathlib.Path) -> int:
    path = cache / "blocks" / f"{slot}.json"
    if path.exists():
        signatures = json.loads(path.read_text(encoding="utf-8"))
    else:
        result = rpc.call("getBlock", [slot, {
            "commitment": "finalized", "transactionDetails": "signatures", "rewards": False,
            "maxSupportedTransactionVersion": 0,
        }])
        if result is None:
            raise RuntimeError("finalized block unavailable")
        signatures = result.get("signatures") or []
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, signatures)
    try:
        return signatures.index(signature)
    except ValueError as error:
        raise RuntimeError("signature absent from finalized block") from error


def fetch_transaction(rpc: Rpc, signature: str, cache: pathlib.Path) -> dict[str, Any] | None:
    path = cache / "transactions" / f"{signature}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    result = rpc.call("getTransaction", [signature, {
        "commitment": "finalized", "encoding": "json", "maxSupportedTransactionVersion": 0,
    }])
    if result is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, result)
    return result


def reconcile(
    connection: sqlite3.Connection, rpc: Rpc, cache: pathlib.Path, limit: int
) -> dict[str, int]:
    counters = {"attempted": 0, "finalized": 0, "successful": 0, "exact": 0, "errors": 0}
    rows = connection.execute(
        "SELECT * FROM arrivals WHERE finalized=0 ORDER BY slot,observed_ordinal LIMIT ?", (limit,)
    ).fetchall()
    for row in rows:
        counters["attempted"] += 1
        signature = str(row["signature"])
        try:
            tx = fetch_transaction(rpc, signature, cache)
            if tx is None:
                continue
            slot = int(tx.get("slot") or row["slot"])
            tx_index = canonical_index(rpc, slot, signature, cache)
            successful = int((tx.get("meta") or {}).get("err") is None)
            classification = "FINALIZED_FAILED"
            validated: list[dict[str, Any]] = []
            if successful:
                counters["successful"] += 1
                validated = launchlab.validate_transaction(tx)
                classification = "EXACT_TRADE" if validated else "FINALIZED_NON_TRADE"
            for index, event in enumerate(validated):
                connection.execute(
                    "INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        signature, index, slot, tx_index, int(row["received_at_unix_ns"]),
                        event["poolState"], event["payer"], event["baseMint"], event["quoteMint"], event["side"],
                        event["amountMode"], str(event["curveAmountInRaw"]),
                        str(event["curveAmountOutRaw"]), str(event["curveFeeRaw"]),
                        None if event["walletBaseDeltaRaw"] is None else str(event["walletBaseDeltaRaw"]),
                        None if event["walletQuoteDeltaRaw"] is None else str(event["walletQuoteDeltaRaw"]),
                        None if event["baseTransferFeeRaw"] is None else str(event["baseTransferFeeRaw"]),
                        None if event["quoteTransferFeeRaw"] is None else str(event["quoteTransferFeeRaw"]),
                        json.dumps(event["preState"], sort_keys=True),
                        json.dumps(event["postState"], sort_keys=True),
                    ),
                )
            counters["exact"] += len(validated)
            connection.execute(
                "UPDATE arrivals SET finalized=1,successful=?,canonical_tx_index=?,classification=?,error=NULL "
                "WHERE signature=?", (successful, tx_index, classification, signature)
            )
            counters["finalized"] += 1
        except launchlab.LaunchLabExactError as error:
            counters["errors"] += 1
            connection.execute(
                "UPDATE arrivals SET finalized=1,successful=1,canonical_tx_index=?,"
                "classification='INEXACT_EXCLUDED',error=? WHERE signature=?",
                (tx_index, str(error)[:500], signature),
            )
            counters["finalized"] += 1
        except Exception as error:
            counters["errors"] += 1
            connection.execute(
                "UPDATE arrivals SET error=? WHERE signature=?", (str(error)[:500], signature)
            )
        if counters["attempted"] % 100 == 0:
            connection.commit()
    connection.commit()
    return counters


def continuity(connection: sqlite3.Connection) -> tuple[int, int, int]:
    rows = connection.execute(
        "SELECT pool_state,pre_state_json,post_state_json FROM events "
        "ORDER BY pool_state,slot,canonical_tx_index,event_index"
    )
    pools = 0
    compared = 0
    gaps = 0
    current_pool = None
    previous = None
    for pool, pre, post in rows:
        if pool != current_pool:
            pools += 1
            current_pool = pool
            previous = None
        if previous is not None:
            compared += 1
            gaps += int(previous != pre)
        previous = post
    return pools, compared, gaps


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(args.output / "launchlab-state-v1.sqlite3")
    connection.row_factory = sqlite3.Row
    schema(connection)
    inserted, offset = ingest_arrivals(connection, args.arrivals)
    compact_records, compact_offset = index_compact_arrivals(connection, args.compact_arrivals)
    connection.commit()
    env = {**load_env(args.env), **os.environ}
    rpc_url = env.get("PUMP_EXACT_LEDGER_RPC_URL") or env.get("SECURE_RPC_URL")
    if not rpc_url:
        raise RuntimeError("PUMP_EXACT_LEDGER_RPC_URL/SECURE_RPC_URL missing")
    rpc = Rpc(rpc_url)
    program_discovery = discover_program_signatures(connection, rpc, args.compact_arrivals)
    realtime_backfilled, realtime_still_missing = backfill_compact_receipts(
        connection, args.compact_arrivals
    )
    connection.commit()
    counters = reconcile(connection, rpc, args.output / "rpc-cache", args.limit)
    pools, compared, gaps = continuity(connection)
    totals = dict(connection.execute(
        "SELECT COUNT(*) total,SUM(finalized) finalized,"
        "SUM(CASE WHEN successful=1 THEN 1 ELSE 0 END) successful,"
        "SUM(CASE WHEN classification='EXACT_TRADE' THEN 1 ELSE 0 END) exact_tx FROM arrivals"
    ).fetchone())
    exact_events = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    causal_realtime_exact_events = int(connection.execute(
        "SELECT COUNT(*) FROM events e JOIN arrivals a USING(signature) "
        "WHERE a.received_at_unix_ns>0"
    ).fetchone()[0])
    unresolved = int(connection.execute("SELECT COUNT(*) FROM arrivals WHERE finalized=0").fetchone()[0])
    errors = int(connection.execute("SELECT COUNT(*) FROM arrivals WHERE error IS NOT NULL").fetchone()[0])
    inexact_excluded = int(connection.execute(
        "SELECT COUNT(*) FROM arrivals WHERE classification='INEXACT_EXCLUDED'"
    ).fetchone()[0])
    pools_path = args.output / "known-pools.txt"
    pools_values = set()
    if args.seed_pools and args.seed_pools.exists():
        pools_values.update(
            value.strip()
            for value in args.seed_pools.read_text(encoding="ascii").splitlines()
            if value.strip()
        )
    if pools_path.exists():
        pools_values.update(value.strip() for value in pools_path.read_text(encoding="ascii").splitlines() if value.strip())
    pools_values.update(row[0] for row in connection.execute("SELECT DISTINCT pool_state FROM events"))
    pools_temporary = pools_path.with_suffix(".txt.tmp")
    pools_temporary.write_text("".join(f"{value}\n" for value in sorted(pools_values)), encoding="ascii")
    os.replace(pools_temporary, pools_path)
    connection.close()
    report = {
        "schema": SCHEMA + "_HEALTH",
        "generatedAtUtc": now_utc(),
        "arrivalJournalOffset": offset,
        "newArrivalsIngested": inserted,
        "compactRecordsIndexedThisRun": compact_records,
        "compactJournalOffset": compact_offset,
        "programSignatureDiscovery": program_discovery,
        "realtimeArrivalBackfilledThisRun": realtime_backfilled,
        "realtimeArrivalStillMissingN": realtime_still_missing,
        "arrivalsN": int(totals["total"] or 0),
        "finalizedN": int(totals["finalized"] or 0),
        "successfulN": int(totals["successful"] or 0),
        "exactTradeTransactionN": int(totals["exact_tx"] or 0),
        "exactTransitionN": exact_events,
        "causalRealtimeExactTransitionN": causal_realtime_exact_events,
        "exactTransitionMissingRealtimeN": exact_events - causal_realtime_exact_events,
        "unresolvedN": unresolved,
        "errorN": errors,
        "inexactExcludedN": inexact_excluded,
        "poolN": pools,
        "observerRegistryPoolN": len(pools_values),
        "continuityComparisonsN": compared,
        "continuityGapN": gaps,
        "lastRun": counters,
        "causalAdmission": "FAIL_CLOSED_ON_UNRESOLVED_OR_CONTINUITY_GAP",
        "integrity": {"outcomesRead": 0, "validationRowsRead": 0, "submissions": 0},
    }
    atomic_json(args.output / "health.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--seed-pools", type=pathlib.Path)
    parser.add_argument("--limit", type=int, default=500)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
