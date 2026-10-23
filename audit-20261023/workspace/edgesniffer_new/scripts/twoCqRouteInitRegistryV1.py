#!/usr/bin/env python3
"""Future-only finalized route-init registries for LaunchLab and PumpSwap.

This is infrastructure for a *future* route-specific symmetric-choice study.
It captures local arrival evidence first, finalizes it second, and does not
read teacher outcomes, PnL, exits, or official SOL-Pump cohort state.

PumpSwap is intentionally fail-closed: a pool is never called initialized
until a dedicated finalized init/migration decoder proves it. Its raw arrival
and exact-trade state are still retained prospectively for that decoder.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
from typing import Any, Iterable

import launchLabEvidenceReconcilerV1 as common
import launchLabExactEconomicsV1 as launchlab
import pumpSwapExactEconomicsV1 as pumpswap
import pumpSwapInitDecoderV1 as pumpswap_init


SCHEMA = "2CQ_ROUTE_INIT_REGISTRY_V1"
LAUNCHLAB = "LAUNCHLAB"
PUMPSWAP = "PUMPSWAP"
POOL_CREATE_EVENT = bytes.fromhex("97d7e20976a173ae")
STONK_PLATFORM_CONFIGS = {
    "6BwHHDg3u1854jC8PDLXvR4spTcLNaoBxLJNGC4nTESt",
    "4E876qZTE9FJMrBzgVtBrSrzz2TLivB5Y5QXPjB4gZL7",
}


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
    os.replace(temporary, path)


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def fetch_transaction_v1(rpc: common.Rpc, signature: str, cache: pathlib.Path) -> dict[str, Any] | None:
    """Route venues legitimately use v0/v1 messages; cache finalized evidence."""
    path = cache / "transactions" / f"{signature}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    result = rpc.call("getTransaction", [signature, {
        "commitment": "finalized", "encoding": "json", "maxSupportedTransactionVersion": 1,
    }])
    if result is not None:
        atomic_json(path, result)
    return result


def canonical_index_v1(rpc: common.Rpc, slot: int, signature: str, cache: pathlib.Path) -> int:
    path = cache / "blocks" / f"{slot}.json"
    if path.exists():
        signatures = json.loads(path.read_text(encoding="utf-8"))
    else:
        result = rpc.call("getBlock", [slot, {
            "commitment": "finalized", "transactionDetails": "signatures", "rewards": False,
            "maxSupportedTransactionVersion": 1,
        }])
        if result is None:
            raise RuntimeError("finalized block unavailable")
        signatures = result.get("signatures") or []
        atomic_json(path, signatures)
    try:
        return list(signatures).index(signature)
    except ValueError as error:
        raise RuntimeError("signature absent from finalized block") from error


def route_kind(row: dict[str, Any]) -> str | None:
    refs = {str(value) for value in row.get("routeProgramReferences") or []}
    # A transaction can reference both; preserve the exact candidate in both
    # independent registries rather than assigning a speculative mixed family.
    if LAUNCHLAB in refs:
        return LAUNCHLAB
    if PUMPSWAP in refs:
        return PUMPSWAP
    return None


def schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS arrival_files(
            path TEXT PRIMARY KEY, byte_offset INTEGER NOT NULL, content_sha256 TEXT
        );
        CREATE TABLE IF NOT EXISTS candidates(
            signature TEXT PRIMARY KEY,
            received_at_unix_ns INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            observed_ordinal INTEGER NOT NULL,
            connection_epoch INTEGER NOT NULL,
            route_refs_json TEXT NOT NULL,
            direct_instruction_json TEXT NOT NULL,
            source_provenance TEXT NOT NULL,
            arrival_evidence_hash TEXT NOT NULL,
            source_file TEXT NOT NULL,
            finalized INTEGER NOT NULL DEFAULT 0,
            successful INTEGER,
            canonical_tx_index INTEGER,
            finalized_classification TEXT,
            canonical_tx_sha256 TEXT,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS candidates_pending
          ON candidates(finalized,slot,observed_ordinal);
        CREATE TABLE IF NOT EXISTS init_registry(
            route TEXT NOT NULL,
            mint TEXT NOT NULL,
            pool TEXT NOT NULL,
            init_signature TEXT NOT NULL,
            slot INTEGER NOT NULL,
            canonical_tx_index INTEGER NOT NULL,
            received_at_unix_ns INTEGER NOT NULL,
            source_provenance TEXT NOT NULL,
            arrival_evidence_hash TEXT NOT NULL,
            canonical_tx_sha256 TEXT NOT NULL,
            finalized_status TEXT NOT NULL,
            platform_classification TEXT,
            platform_config TEXT,
            creator TEXT,
            quote_mint TEXT,
            first_seen_unix_ns INTEGER NOT NULL,
            immutable_evidence_hash TEXT NOT NULL,
            PRIMARY KEY(route,mint,pool)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS init_registry_signature
          ON init_registry(route,init_signature,mint,pool);
        CREATE INDEX IF NOT EXISTS init_registry_route_arrival
          ON init_registry(route,received_at_unix_ns);
        CREATE TABLE IF NOT EXISTS exact_route_events(
            route TEXT NOT NULL,
            signature TEXT NOT NULL,
            event_index INTEGER NOT NULL,
            pool TEXT NOT NULL,
            mint TEXT NOT NULL,
            quote_mint TEXT NOT NULL,
            side TEXT NOT NULL,
            slot INTEGER NOT NULL,
            canonical_tx_index INTEGER NOT NULL,
            received_at_unix_ns INTEGER NOT NULL,
            pre_state_json TEXT NOT NULL,
            post_state_json TEXT NOT NULL,
            source_provenance TEXT NOT NULL,
            arrival_evidence_hash TEXT NOT NULL,
            canonical_tx_sha256 TEXT NOT NULL,
            PRIMARY KEY(route,signature,event_index)
        );
        CREATE INDEX IF NOT EXISTS exact_route_events_universe
          ON exact_route_events(route,quote_mint,received_at_unix_ns,pool);
        CREATE TABLE IF NOT EXISTS pump_swap_init_candidates(
            signature TEXT PRIMARY KEY,
            slot INTEGER NOT NULL,
            canonical_tx_index INTEGER NOT NULL,
            received_at_unix_ns INTEGER NOT NULL,
            classification TEXT NOT NULL,
            source_provenance TEXT NOT NULL,
            arrival_evidence_hash TEXT NOT NULL,
            canonical_tx_sha256 TEXT NOT NULL,
            decoder_note TEXT NOT NULL
        );
    """)


def allenhark_clock_healthy(output: pathlib.Path) -> bool:
    """AllenHark can win a same-signature clock only while it is advancing."""
    path = output / "observer-health.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(value.get("active")) and value.get("state") == "ADVANCING" and int(value.get("candidateRecords") or 0) > 0


def ingest_arrivals(connection: sqlite3.Connection, arrival_root: pathlib.Path,
                    allow_allenhark_precedence: bool) -> dict[str, int]:
    counters = {"files": 0, "rows": 0, "inserted": 0}
    for path in sorted(arrival_root.glob("hour-*.jsonl")):
        row = connection.execute(
            "SELECT byte_offset FROM arrival_files WHERE path=?", (str(path),)
        ).fetchone()
        offset = int(row[0]) if row else 0
        size = path.stat().st_size
        if size < offset:
            raise RuntimeError(f"arrival partition shrank: {path}")
        counters["files"] += 1
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
                payload = json.loads(raw)
                signature = str(payload.get("signature") or "")
                if not signature:
                    offset = stream.tell()
                    continue
                source = str(payload.get("sourceProvenance") or "UNKNOWN")
                connection.execute(
                    "INSERT INTO candidates(signature,received_at_unix_ns,slot,observed_ordinal,"
                    "connection_epoch,route_refs_json,direct_instruction_json,source_provenance,"
                    "arrival_evidence_hash,source_file) VALUES(?,?,?,?,?,?,?,?,?,?) "
                    # Keep each source's clock distinct. The canonical row uses
                    # AllenHark when it exists; Yellowstone is the explicit
                    # same-signature fallback while AllenHark is unavailable.
                    "ON CONFLICT(signature) DO UPDATE SET "
                    "received_at_unix_ns=excluded.received_at_unix_ns,slot=excluded.slot,"
                    "observed_ordinal=excluded.observed_ordinal,connection_epoch=excluded.connection_epoch,"
                    "route_refs_json=excluded.route_refs_json,direct_instruction_json=excluded.direct_instruction_json,"
                    "source_provenance=excluded.source_provenance,arrival_evidence_hash=excluded.arrival_evidence_hash,"
                    "source_file=excluded.source_file, "
                    "finalized=0,successful=NULL,canonical_tx_index=NULL,"
                    "finalized_classification=NULL,canonical_tx_sha256=NULL,error=NULL "
                    "WHERE (excluded.source_provenance LIKE 'ALLENHARK%' "
                    "AND candidates.source_provenance NOT LIKE 'ALLENHARK%' AND ?) "
                    "OR (excluded.source_provenance NOT LIKE 'ALLENHARK%' "
                    "AND candidates.source_provenance LIKE 'ALLENHARK%' AND ?)",
                    (
                        signature, int(payload["receivedAtUnixNs"]), int(payload["slot"]),
                        int(payload["observedSlotTransactionOrdinal"]),
                        int(payload.get("connectionEpoch") or 0),
                        json.dumps(payload.get("routeProgramReferences") or [], sort_keys=True),
                        json.dumps(payload.get("directInstructionEvidence") or [], sort_keys=True),
                        source,
                        str(payload.get("arrivalEvidenceHash") or stable_hash(payload)), str(path),
                        int(allow_allenhark_precedence), int(not allow_allenhark_precedence),
                    ),
                )
                counters["rows"] += 1
                counters["inserted"] += int(connection.execute("SELECT changes()").fetchone()[0])
                offset = stream.tell()
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        connection.execute(
            "INSERT INTO arrival_files(path,byte_offset,content_sha256) VALUES(?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET byte_offset=excluded.byte_offset,content_sha256=excluded.content_sha256",
            (str(path), offset, file_hash),
        )
    return counters


def program_instructions(tx: dict[str, Any], program: str) -> Iterable[tuple[str, tuple[str, ...]]]:
    keys = launchlab.resolved_keys(tx)
    message = (tx.get("transaction") or {}).get("message") or {}
    groups: list[tuple[str, list[dict[str, Any]]]] = [("outer", message.get("instructions") or [])]
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        groups.append((f"inner:{int(inner.get('index') or 0)}", inner.get("instructions") or []))
    for group_name, group in groups:
        for index, instruction in enumerate(group):
            if launchlab.instruction_program(instruction, keys) != program:
                continue
            yield f"{group_name}:{index}", launchlab.instruction_accounts(instruction, keys)


def launchlab_init(tx: dict[str, Any]) -> dict[str, Any] | None:
    """Fail-closed parser for the current LaunchLab initialize layouts."""
    logs = (tx.get("meta") or {}).get("logMessages") or []
    init_logs = [str(line) for line in logs if "Instruction: Initialize" in str(line)]
    if not init_logs:
        return None
    pools: list[str] = []
    for line in logs:
        marker = "Program data: "
        if marker not in str(line):
            continue
        try:
            raw = base64.b64decode(str(line).split(marker, 1)[1].strip(), validate=True)
        except Exception:
            continue
        if raw.startswith(POOL_CREATE_EVENT) and len(raw) >= 40:
            pools.append(launchlab.b58encode(raw[8:40]))
    pools = sorted(set(pools))
    candidates = [accounts for _, accounts in program_instructions(tx, launchlab.PROGRAM_ID)
                  if len(accounts) >= 8 and accounts[5] in pools]
    if len(candidates) != 1 or len(pools) != 1:
        return None
    accounts = candidates[0]
    # The frozen IDL layouts put creator/platform/pool/base/quote at these
    # positions for Initialize, InitializeV2, and InitializeWithToken2022.
    return {
        "route": LAUNCHLAB,
        "creator": accounts[1], "platformConfig": accounts[3], "pool": accounts[5],
        "mint": accounts[6], "quoteMint": accounts[7],
        "platformClassification": "STONKFUN" if accounts[3] in STONK_PLATFORM_CONFIGS else "LAUNCHLAB_OTHER_PLATFORM",
    }


def pump_swap_init(tx: dict[str, Any]) -> dict[str, Any] | None:
    """Return only a finalized, instruction/event-proven PumpSwap init."""
    verified = pumpswap_init.validate_finalized_init(tx)
    if verified is None:
        return None
    return {
        "route": PUMPSWAP,
        "creator": verified.creator,
        "platformConfig": None,
        "pool": verified.pool,
        "mint": verified.mint,
        "quoteMint": verified.quote_mint,
        "platformClassification": verified.origin,
    }


def store_events(connection: sqlite3.Connection, route: str, candidate: sqlite3.Row,
                 tx: dict[str, Any], tx_hash: str) -> int:
    if route == LAUNCHLAB:
        events = launchlab.validate_transaction(tx)
        normalized = [
            (event["poolState"], event["baseMint"], event["quoteMint"], event["side"],
             event["preState"], event["postState"]) for event in events
        ]
    else:
        events = pumpswap.validate_transaction(tx)
        normalized = [
            (event["pool"], event["baseMint"], event["quoteMint"], event["side"],
             event["preState"], event["postState"]) for event in events
        ]
    for index, (pool, mint, quote, side, pre, post) in enumerate(normalized):
        connection.execute(
            "INSERT OR REPLACE INTO exact_route_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (route, candidate["signature"], index, pool, mint, quote, side,
             int(candidate["slot"]), int(candidate["canonical_tx_index"]),
             int(candidate["received_at_unix_ns"]), json.dumps(pre, sort_keys=True),
             json.dumps(post, sort_keys=True), candidate["source_provenance"],
             candidate["arrival_evidence_hash"], tx_hash),
        )
    return len(normalized)


def has_canonical_route_instruction(tx: dict[str, Any], program: str) -> bool:
    """A static account reference alone is never enough to invoke a parser."""
    return any(True for _ in program_instructions(tx, program))


def finalize(connection: sqlite3.Connection, rpc: common.Rpc, cache: pathlib.Path, limit: int,
             strict_capture_start_ns: int, allow_allenhark_precedence: bool) -> dict[str, int]:
    counters = {"attempted": 0, "finalized": 0, "launchlabInit": 0, "pumpSwapInit": 0,
                "exactEvents": 0, "pumpSwapInitPendingDecoder": 0, "errors": 0}
    rows = connection.execute(
        "SELECT * FROM candidates WHERE finalized=0 AND received_at_unix_ns >= ? "
        "ORDER BY CASE "
        "WHEN direct_instruction_json LIKE '%\\\"pumpSwapMigrationCandidateHint\\\": true%' THEN 0 "
        "WHEN direct_instruction_json LIKE '%\\\"launchLabInitializeHint\\\": true%' THEN 1 "
        "ELSE 2 END, "
        "slot,observed_ordinal LIMIT ?", (strict_capture_start_ns, limit)
    ).fetchall()
    for candidate in rows:
        counters["attempted"] += 1
        signature = str(candidate["signature"])
        if str(candidate["source_provenance"]).startswith("ALLENHARK") and not allow_allenhark_precedence:
            connection.execute(
                "UPDATE candidates SET finalized=1,finalized_classification='ALLENHARK_CLOCK_UNHEALTHY_NOT_ADMITTED' "
                "WHERE signature=?", (signature,)
            )
            continue
        try:
            tx = fetch_transaction_v1(rpc, signature, cache)
            if tx is None:
                continue
            slot = int(tx.get("slot") or candidate["slot"])
            canonical_index = canonical_index_v1(rpc, slot, signature, cache)
            successful = (tx.get("meta") or {}).get("err") is None
            tx_hash = stable_hash(tx)
            refs = set(json.loads(candidate["route_refs_json"]))
            classification = "FINALIZED_FAILED" if not successful else "FINALIZED_NON_ROUTE_EVENT"
            connection.execute(
                "UPDATE candidates SET finalized=1,successful=?,canonical_tx_index=?,canonical_tx_sha256=?,error=NULL "
                "WHERE signature=?", (int(successful), canonical_index, tx_hash, signature),
            )
            candidate = connection.execute("SELECT * FROM candidates WHERE signature=?", (signature,)).fetchone()
            route_errors: list[str] = []
            if successful and LAUNCHLAB in refs and has_canonical_route_instruction(tx, launchlab.PROGRAM_ID):
                init = launchlab_init(tx)
                try:
                    counters["exactEvents"] += store_events(connection, LAUNCHLAB, candidate, tx, tx_hash)
                except launchlab.LaunchLabExactError as error:
                    route_errors.append(f"LAUNCHLAB:{error}")
                if init is not None:
                    immutable = stable_hash({**init, "signature": signature, "slot": slot,
                                             "canonicalTxIndex": canonical_index,
                                             "arrivalEvidenceHash": candidate["arrival_evidence_hash"],
                                             "canonicalTxSha256": tx_hash})
                    connection.execute(
                        "INSERT OR IGNORE INTO init_registry VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (LAUNCHLAB, init["mint"], init["pool"], signature, slot, canonical_index,
                         candidate["received_at_unix_ns"], candidate["source_provenance"],
                         candidate["arrival_evidence_hash"], tx_hash, "FINALIZED_SUCCESS",
                         init["platformClassification"], init["platformConfig"], init["creator"],
                         init["quoteMint"], candidate["received_at_unix_ns"], immutable),
                    )
                    counters["launchlabInit"] += int(connection.execute("SELECT changes()").fetchone()[0])
                    classification = "LAUNCHLAB_FINALIZED_POOL_INIT_EXACT"
                elif classification == "FINALIZED_NON_ROUTE_EVENT":
                    classification = "LAUNCHLAB_FINALIZED_NON_INIT"
            if successful and PUMPSWAP in refs and has_canonical_route_instruction(tx, pumpswap.PROGRAM_ID):
                init = None
                try:
                    event_n = store_events(connection, PUMPSWAP, candidate, tx, tx_hash)
                except pumpswap.PumpSwapExactError as error:
                    event_n = 0
                    route_errors.append(f"PUMPSWAP:{error}")
                try:
                    init = pump_swap_init(tx)
                except pumpswap_init.PumpSwapInitDecodeError as error:
                    route_errors.append(f"PUMPSWAP_INIT:{error}")
                counters["exactEvents"] += event_n
                if init is not None:
                    immutable = stable_hash({**init, "signature": signature, "slot": slot,
                                             "canonicalTxIndex": canonical_index,
                                             "arrivalEvidenceHash": candidate["arrival_evidence_hash"],
                                             "canonicalTxSha256": tx_hash,
                                             "decoderSchema": pumpswap_init.SCHEMA})
                    connection.execute(
                        "INSERT OR IGNORE INTO init_registry VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (PUMPSWAP, init["mint"], init["pool"], signature, slot, canonical_index,
                         candidate["received_at_unix_ns"], candidate["source_provenance"],
                         candidate["arrival_evidence_hash"], tx_hash, "FINALIZED_SUCCESS",
                         init["platformClassification"], None, init["creator"], init["quoteMint"],
                         candidate["received_at_unix_ns"], immutable),
                    )
                    counters["pumpSwapInit"] += int(connection.execute("SELECT changes()").fetchone()[0])
                    pump_class = "PUMPSWAP_FINALIZED_POOL_INIT_EXACT"
                elif event_n:
                    pump_class = "PUMPSWAP_FINALIZED_TRADE_NOT_INIT"
                else:
                    pump_class = "PUMPSWAP_FINALIZED_INEXACT_NON_INIT_OR_UNSUPPORTED" if route_errors else "PUMPSWAP_FINALIZED_NON_INIT"
                connection.execute(
                    "INSERT OR REPLACE INTO pump_swap_init_candidates VALUES(?,?,?,?,?,?,?,?,?)",
                    (signature, slot, canonical_index, candidate["received_at_unix_ns"], pump_class,
                     candidate["source_provenance"], candidate["arrival_evidence_hash"], tx_hash,
                     "PUMPSWAP_FINALIZED_INIT_DECODER_V1: finalized create_pool plus matching CreatePoolEvent required."),
                )
                counters["pumpSwapInitPendingDecoder"] += int(pump_class == "PUMPSWAP_FINALIZED_INIT_DECODER_PENDING")
                if classification == "FINALIZED_NON_ROUTE_EVENT":
                    classification = pump_class
            if route_errors and classification == "FINALIZED_NON_ROUTE_EVENT":
                classification = "FINALIZED_ROUTE_REFERENCE_INEXACT_EXCLUDED"
            connection.execute("UPDATE candidates SET finalized_classification=? WHERE signature=?", (classification, signature))
            counters["finalized"] += 1
        except Exception as error:
            connection.execute("UPDATE candidates SET error=? WHERE signature=?", (str(error)[:500], signature))
            counters["errors"] += 1
        if counters["attempted"] % 50 == 0:
            connection.commit()
    connection.commit()
    return counters


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(args.output / "route-init-registry-v1.sqlite3")
    connection.row_factory = sqlite3.Row
    schema(connection)
    configured_start = os.environ.get("ROUTE_INIT_STRICT_CAPTURE_START_UNIX_NS")
    if not configured_start or not configured_start.isdigit():
        raise RuntimeError("ROUTE_INIT_STRICT_CAPTURE_START_UNIX_NS must be an explicit integer")
    strict_capture_start_ns = int(configured_start)
    stored_start = connection.execute(
        "SELECT value FROM metadata WHERE key='strict_init_capture_start_unix_ns'"
    ).fetchone()
    if stored_start is None:
        connection.execute(
            "INSERT INTO metadata VALUES('strict_init_capture_start_unix_ns',?)",
            (str(strict_capture_start_ns),),
        )
    elif int(stored_start[0]) != strict_capture_start_ns:
        raise RuntimeError("strict init capture boundary changed after initialization")
    allow_allenhark_precedence = allenhark_clock_healthy(args.output)
    arrivals = ingest_arrivals(connection, args.arrival_root, allow_allenhark_precedence)
    # Retain broad bootstrap arrivals as immutable raw evidence, but do not let
    # ordinary old swaps consume the future-only init decoder queue. The new
    # registry starts only after the observer's published init discriminators.
    pre_strict = connection.execute(
        "UPDATE candidates SET finalized=1,successful=NULL,canonical_tx_index=NULL,"
        "finalized_classification='PRE_STRICT_BROAD_CAPTURE_NOT_ADMITTED',error=NULL "
        "WHERE finalized=0 AND received_at_unix_ns < ?", (strict_capture_start_ns,)
    ).rowcount
    connection.commit()
    environment = {**common.load_env(args.env), **os.environ}
    rpc_url = environment.get("PUMP_EXACT_LEDGER_RPC_URL") or environment.get("SECURE_RPC_URL")
    if not rpc_url:
        raise RuntimeError("PUMP_EXACT_LEDGER_RPC_URL/SECURE_RPC_URL missing")
    # Re-evaluate only the fresh sibling rows that the first parser version
    # marked inexact due to static references. Their immutable arrival and
    # canonical transaction evidence remain unchanged.
    connection.execute(
        "UPDATE candidates SET finalized=0,error=NULL WHERE finalized_classification='INEXACT_EXCLUDED' "
        "AND source_provenance LIKE 'YELLOWSTONE_DIRECT_PROGRAM_FILTER_V1%'"
    )
    # Two rows were finalized while the registry deliberately had no approved
    # PumpSwap init decoder. Re-run only that explicitly-labelled state under
    # the new frozen decoder; arrival and canonical evidence are unchanged.
    connection.execute(
        "UPDATE candidates SET finalized=0,error=NULL WHERE finalized_classification="
        "'PUMPSWAP_FINALIZED_INIT_DECODER_PENDING'"
    )
    connection.execute(
        "UPDATE candidates SET finalized=0,error=NULL WHERE finalized=0 AND source_provenance "
        "LIKE 'YELLOWSTONE_DIRECT_PROGRAM_FILTER_V1%' AND (error LIKE '%maxSupportedTransactionVersion%' "
        "OR error LIKE '%pump_swap_init_candidates has 9 columns%')"
    )
    connection.commit()
    final = finalize(connection, common.Rpc(rpc_url), args.output / "rpc-cache", args.limit,
                     strict_capture_start_ns, allow_allenhark_precedence)
    totals = dict(connection.execute("SELECT COUNT(*) candidatesN,SUM(finalized) finalizedN FROM candidates").fetchone())
    route_events = dict(connection.execute("SELECT route,COUNT(*) n FROM exact_route_events GROUP BY route").fetchall())
    inits = dict(connection.execute("SELECT route,COUNT(*) n FROM init_registry GROUP BY route").fetchall())
    pending = int(connection.execute("SELECT COUNT(*) FROM candidates WHERE finalized=0").fetchone()[0])
    pump_pending = int(connection.execute("SELECT COUNT(*) FROM pump_swap_init_candidates WHERE classification='PUMPSWAP_FINALIZED_INIT_DECODER_PENDING'").fetchone()[0])
    registry_start = connection.execute("SELECT value FROM metadata WHERE key='registry_start_utc'").fetchone()
    if registry_start is None:
        registry_start_value = now_utc()
        connection.execute("INSERT INTO metadata VALUES('registry_start_utc',?)", (registry_start_value,))
    else:
        registry_start_value = str(registry_start[0])
    connection.commit()
    report = {
        "schema": SCHEMA + "_HEALTH", "generatedAtUtc": now_utc(), "registryStartUtc": registry_start_value,
        "strictInitCaptureStartUnixNs": strict_capture_start_ns,
        "allenharkClockPrecedenceActive": allow_allenhark_precedence,
        "preStrictBroadCandidatesQuarantinedN": int(pre_strict or 0),
        "futureOnly": True, "outcomesRead": 0, "submissions": 0,
        "arrivalIngest": arrivals, "finalization": final,
        "candidateN": int(totals["candidatesN"] or 0), "finalizedCandidateN": int(totals["finalizedN"] or 0),
        "pendingFinalizationN": pending, "exactRouteEventsByRoute": route_events,
        "verifiedInitByRoute": inits,
        "launchLabLifecycleAgeAvailable": True,
        "pumpSwapLifecycleAgeAvailable": bool(inits.get(PUMPSWAP, 0)),
        "pumpSwapInitDecoderPendingN": pump_pending,
        "alternativeUniverse": {
            "launchlab": "FUTURE_ONLY_VERIFIED_POOL_INIT + exact finalized route events with same-signature local arrival. At a future teacher entry, only same-quote pools initialized and continuously observed before the decision are eligible.",
            "pumpswap": "FUTURE_ONLY canonical Pump migration candidates are captured through the Pump+PumpSwap transaction intersection. Finalized create_pool plus a matching canonical CreatePoolEvent proves pool/mint/quote/creator/index; a first trade never qualifies a pool. Standalone non-Pump PumpSwap creates remain explicitly unsupported for this alternative universe.",
            "noFutureInformation": True,
        },
        "integrity": "OUTCOME_BLIND_FAIL_CLOSED",
    }
    atomic_json(args.output / "health.json", report)
    connection.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arrival-root", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--limit", type=int, default=250)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
