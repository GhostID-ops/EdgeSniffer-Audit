#!/usr/bin/env python3
"""Canonical, outcome-blind state plane for the prospective full-meta lane.

This adapter deliberately keeps the two clocks separate.  A retained Shyft
Yellowstone frame supplies only the local receipt time; a finalized block
supplies success and transaction order.  A transaction reaches this isolated
state plane only when those independently recorded facts agree exactly.
"""

from __future__ import annotations

import argparse
import base64
import collections
import concurrent.futures
import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from typing import Any

import twoCqDecisionSystemDecompositionV1 as decision
import twoCqIndexedCausalStatePlaneV1 as plane
import twoCqProspectiveSymmetricChoiceMaterializerV1 as legacy
import twoCqChoiceSetCoverageRepairV1 as coverage
from exactCurveEconomicsV1 import CurveState, decode_trade_events
from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "2CQ_FULL_META_CANONICAL_STATE_PLANE_V1"
SOURCE = "shyft-yellowstone-confirmed-full-meta-prospective-v1"
WALLET = decision.WALLET
PROOF_CACHE_BUSY_TIMEOUT_MS = 60_000
PROOF_CACHE_LOCK_TIMEOUT_S = 90.0
PROOF_CACHE_MAX_WRITE_ATTEMPTS = 5


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def compact_idempotency_digest(connection: sqlite3.Connection) -> str:
    """Hash the legacy canonical state representation without materializing it."""
    digest = hashlib.sha256()

    def write(value: str) -> None:
        digest.update(value.encode("utf-8"))

    write('{"events":[')
    first = True
    for row in connection.execute(
        "SELECT signature,instruction_index,mint FROM causal_events "
        "ORDER BY signature,instruction_index,mint"
    ):
        if not first:
            write(",")
        write(canonical(list(row)))
        first = False
    write('],"transactions":[')
    first = True
    for row in connection.execute(
        "SELECT signature,slot,tx_index FROM transactions ORDER BY signature"
    ):
        if not first:
            write(",")
        write(canonical(list(row)))
        first = False
    write("]}")
    return digest.hexdigest()


def write_report_atomic(path: pathlib.Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_env(path: pathlib.Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip("\"").strip("'")
    raise RuntimeError(f"MISSING_ENV:{key}")


def rpc(url: str, method: str, params: list[Any]) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=75) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(f"RPC:{payload['error']}")
    return payload.get("result")


def fetch_block(url: str, slot: int) -> tuple[int, dict[str, Any] | None, str | None]:
    for attempt in range(4):
        try:
            result = rpc(url, "getBlock", [slot, {
                "commitment": "finalized", "encoding": "json", "transactionDetails": "full",
                "rewards": False, "maxSupportedTransactionVersion": 1,
            }])
            return slot, result, None
        except Exception as error:  # bounded retry for provider transients only
            if attempt == 3:
                return slot, None, type(error).__name__
            time.sleep(0.2 * (attempt + 1))
    raise AssertionError("unreachable")


def source_rows(path: pathlib.Path, normalizer: pathlib.Path, timings: dict[str, float]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], collections.Counter[str]]:
    """Return normalized retained frames and their immutable receipt metadata."""
    raw_rows: list[tuple[dict[str, Any], bytes]] = []
    metadata_by_signature: dict[str, dict[str, Any]] = {}
    duplicates: collections.Counter[str] = collections.Counter()
    started = time.perf_counter()
    for metadata, raw in iter_frames(path, False):
        if metadata.get("kind") != "transaction":
            continue
        if metadata.get("rawFormat") != "yellowstone_subscribe_update_protobuf_full_meta":
            continue
        signature = str(metadata.get("signature") or "")
        received_ns = int(metadata.get("receivedAtUnixNs") or 0)
        slot = int(metadata.get("slot") or 0)
        if not signature or not received_ns or not slot:
            continue
        current = metadata_by_signature.get(signature)
        if current is not None:
            duplicates["DUPLICATE_SIGNATURE"] += 1
            if int(current["receivedAtUnixNs"]) != received_ns or int(current["slot"]) != slot:
                duplicates["CONFLICTING_SIGNATURE_METADATA"] += 1
            continue
        metadata_by_signature[signature] = metadata
        raw_rows.append((metadata, raw))
    timings["rawFrameScanSeconds"] = time.perf_counter() - started

    # The full Pump universe can produce hundreds of MB of normalized JSON.
    # File-backed streaming avoids a bounded stdout pipe stalling the decoder.
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="2cq-full-meta-normalizer-") as temporary:
        temporary_path = pathlib.Path(temporary)
        input_path = temporary_path / "source.jsonl"
        output_path = temporary_path / "normalized.jsonl"
        error_path = temporary_path / "normalizer.stderr"
        with input_path.open("w", encoding="utf-8") as input_file:
            for metadata, raw in raw_rows:
                input_file.write(canonical({
                    "signature": str(metadata["signature"]),
                    "rawBase64": base64.b64encode(raw).decode("ascii"),
                }) + "\n")
        with input_path.open("r", encoding="utf-8") as input_file, output_path.open("w", encoding="utf-8") as output_file, error_path.open("w", encoding="utf-8") as error_file:
            completed = subprocess.run(
                [str(normalizer), "--all"], stdin=input_file, stdout=output_file,
                stderr=error_file, text=True, check=False,
            )
        if completed.returncode:
            raise RuntimeError(f"NORMALIZER_FAILED:{error_path.read_text(encoding='utf-8')[-1000:]}")
        normalized_lines = output_path.read_text(encoding="utf-8").splitlines()
    timings["protobufNormalizeSeconds"] = time.perf_counter() - started

    normalized: list[dict[str, Any]] = []
    started = time.perf_counter()
    for line in normalized_lines:
        item = json.loads(line)
        if not item.get("ok"):
            duplicates[f"NORMALIZER_{item.get('error') or 'FAILURE'}"] += 1
            continue
        row = item["normalized"]
        signature = str(row.get("signature") or "")
        if signature not in metadata_by_signature:
            duplicates["NORMALIZER_SIGNATURE_NOT_RETAINED"] += 1
            continue
        normalized.append(row)
    timings["normalizedJsonParseSeconds"] = time.perf_counter() - started
    return normalized, metadata_by_signature, duplicates


def relevant(normalized: dict[str, Any], signature: str, slot: int, tx_index: int) -> tuple[set[str], list[Any], str | None]:
    body = dict(normalized)
    try:
        creates = set(legacy.coverage.canonical_create_mints(body))
        hint = decode_trade_events((body.get("meta") or {}).get("logMessages") or [])
        events, failure = coverage.decode_causal_events(body, signature, 0, slot, tx_index)
    except Exception:
        return set(), [], "FULL_META_CAUSAL_DECODE_EXCEPTION"
    if not creates and not hint and not events:
        return set(), [], None
    return creates, events, failure


def canonical_proofs(rpc_url: str, slots: set[int], workers: int) -> tuple[dict[str, tuple[int, int, int]], collections.Counter[str]]:
    """signature -> (slot, canonical index, block time), successful only."""
    proofs: dict[str, tuple[int, int, int]] = {}
    errors: collections.Counter[str] = collections.Counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_block, rpc_url, slot) for slot in sorted(slots)]
        for future in concurrent.futures.as_completed(futures):
            slot, block, error = future.result()
            if error or not isinstance(block, dict):
                errors[f"CANONICAL_BLOCK_{error or 'MISSING'}"] += 1
                continue
            block_time = int(block.get("blockTime") or 0)
            if block_time <= 0:
                errors["CANONICAL_BLOCK_TIME_MISSING"] += 1
                continue
            for tx_index, transaction in enumerate(block.get("transactions") or []):
                if (transaction.get("meta") or {}).get("err") is not None:
                    continue
                signatures = ((transaction.get("transaction") or {}).get("signatures") or [])
                if signatures:
                    proofs[str(signatures[0])] = (slot, tx_index, block_time)
    return proofs, errors


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_adapter_state(
    path: pathlib.Path, evidence_path: pathlib.Path, expected_evidence_sha256: str | None = None,
) -> sqlite3.Connection:
    connection = plane.open_state(path)
    connection.execute("CREATE INDEX IF NOT EXISTS realtime_arrivals_source_received ON realtime_arrivals(source,received_ns,signature)")
    connection.execute("""CREATE TABLE IF NOT EXISTS canonical_transaction_proofs (
        signature TEXT PRIMARY KEY,
        slot INTEGER NOT NULL,
        tx_index INTEGER NOT NULL,
        block_time_s INTEGER NOT NULL
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS canonical_transaction_proofs_slot ON canonical_transaction_proofs(slot)")
    connection.execute("CREATE TABLE IF NOT EXISTS canonical_proof_cache_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.executemany(
        "INSERT OR IGNORE INTO canonical_proof_cache_metadata VALUES (?,?)",
        [
            ("schema", "2CQ_FINALIZED_CANONICAL_PROOF_CACHE_V1"),
            ("source", "finalized_getBlock_json_full"),
            ("proof_fields", "signature,slot,tx_index,block_time_s"),
        ],
    )
    connection.commit()
    resolved = str(evidence_path.resolve())
    existing = connection.execute("SELECT size,mtime_ns,sha256 FROM evidence_files WHERE path=?", (resolved,)).fetchone()
    stat = evidence_path.stat()
    if expected_evidence_sha256 is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_evidence_sha256):
            raise RuntimeError("INVALID_PROMOTED_EVIDENCE_SHA256")
        # The worker computes this digest while atomically writing the promoted
        # evidence file. Reusing that immutable identity avoids a second full
        # file scan without changing the evidence identity contract.
        digest = expected_evidence_sha256
    else:
        digest = file_sha256(evidence_path)
    if existing is None:
        connection.execute(
            "INSERT INTO evidence_files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (resolved, stat.st_size, stat.st_mtime_ns, digest, "full-meta-adapter", 0, 0, 0, 0, 0, 0),
        )
        connection.commit()
    elif (int(existing[0]), int(existing[1]), str(existing[2])) != (stat.st_size, stat.st_mtime_ns, digest):
        raise RuntimeError("PROMOTED_EVIDENCE_IDENTITY_CHANGED")
    return connection


def cached_canonical_proofs(connection: sqlite3.Connection, signatures: set[str]) -> dict[str, tuple[int, int, int]]:
    """Transactions in this state plane were already canonically proven once."""
    if not signatures:
        return {}
    connection.execute("DROP TABLE IF EXISTS cached_proof_signatures")
    connection.execute("CREATE TEMP TABLE cached_proof_signatures(signature TEXT PRIMARY KEY)")
    connection.executemany("INSERT INTO cached_proof_signatures VALUES (?)", [(value,) for value in sorted(signatures)])
    rows = connection.execute(
        """SELECT c.signature,
                  COALESCE(t.slot,p.slot),COALESCE(t.tx_index,p.tx_index),COALESCE(t.block_time_s,p.block_time_s)
           FROM cached_proof_signatures c
           LEFT JOIN transactions t USING(signature)
           LEFT JOIN canonical_transaction_proofs p USING(signature)
           WHERE t.signature IS NOT NULL OR p.signature IS NOT NULL"""
    ).fetchall()
    connection.execute("DROP TABLE cached_proof_signatures")
    connection.commit()
    return {str(signature): (int(slot), int(index), int(block_time)) for signature, slot, index, block_time in rows}


@contextlib.contextmanager
def proof_cache_write_lock(path: pathlib.Path):
    """Serialize the only shared proof-cache mutation section across lanes."""
    lock_path = path.with_name(path.name + ".write.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with lock_path.open("a+") as lock:
        while True:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.perf_counter() - started >= PROOF_CACHE_LOCK_TIMEOUT_S:
                    raise RuntimeError("PROOF_CACHE_WRITE_LOCK_TIMEOUT")
                time.sleep(0.05)
        try:
            yield time.perf_counter() - started
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def cache_canonical_proofs(
    connection: sqlite3.Connection,
    proofs: dict[str, tuple[int, int, int]],
    shared_cache_path: pathlib.Path | None = None,
) -> dict[str, float | int]:
    """Persist compact finalized order proof for future overlapping windows."""
    if not proofs:
        return {"rows": 0, "attempts": 0, "lockWaitSeconds": 0.0}

    @contextlib.contextmanager
    def no_lock():
        yield 0.0

    lock = proof_cache_write_lock(shared_cache_path) if shared_cache_path else no_lock()
    with lock as lock_wait:
        for attempt in range(1, PROOF_CACHE_MAX_WRITE_ATTEMPTS + 1):
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.executemany(
                    "INSERT OR IGNORE INTO canonical_transaction_proofs VALUES (?,?,?,?)",
                    [(signature, slot, index, block_time) for signature, (slot, index, block_time) in proofs.items()],
                )
                connection.commit()
                return {"rows": len(proofs), "attempts": attempt, "lockWaitSeconds": round(lock_wait, 6)}
            except sqlite3.OperationalError as error:
                connection.rollback()
                if "locked" not in str(error).lower() or attempt == PROOF_CACHE_MAX_WRITE_ATTEMPTS:
                    raise
                time.sleep(0.05 * attempt)
    raise AssertionError("unreachable")


def proof_cache_schema_ready(connection: sqlite3.Connection) -> bool:
    try:
        row = connection.execute(
            "SELECT value FROM canonical_proof_cache_metadata WHERE key='schema'"
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None and str(row[0]) == "2CQ_FINALIZED_CANONICAL_PROOF_CACHE_V1"


def open_proof_cache(path: pathlib.Path) -> sqlite3.Connection:
    """Open the compact immutable cache shared with the prefetch lane."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=PROOF_CACHE_BUSY_TIMEOUT_MS / 1000)
    connection.execute(f"PRAGMA busy_timeout={PROOF_CACHE_BUSY_TIMEOUT_MS}")
    if proof_cache_schema_ready(connection):
        return connection
    connection.close()
    # Schema creation and WAL configuration are a one-time mutation. They must
    # not run on every adapter/prefetch connection because that makes readers
    # contend with unrelated metadata writes.
    with proof_cache_write_lock(path):
        connection = sqlite3.connect(path, timeout=PROOF_CACHE_BUSY_TIMEOUT_MS / 1000)
        connection.execute(f"PRAGMA busy_timeout={PROOF_CACHE_BUSY_TIMEOUT_MS}")
        if proof_cache_schema_ready(connection):
            return connection
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("""CREATE TABLE IF NOT EXISTS canonical_transaction_proofs (
            signature TEXT PRIMARY KEY,
            slot INTEGER NOT NULL,
            tx_index INTEGER NOT NULL,
            block_time_s INTEGER NOT NULL
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS canonical_transaction_proofs_slot ON canonical_transaction_proofs(slot)")
        connection.execute("CREATE TABLE IF NOT EXISTS canonical_proof_cache_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT OR IGNORE INTO canonical_proof_cache_metadata VALUES (?,?)",
            [
                ("schema", "2CQ_FINALIZED_CANONICAL_PROOF_CACHE_V1"),
                ("source", "finalized_getBlock_json_full"),
                ("proof_fields", "signature,slot,tx_index,block_time_s"),
            ],
        )
        connection.commit()
        return connection


def external_cached_proofs(connection: sqlite3.Connection, signatures: set[str]) -> dict[str, tuple[int, int, int]]:
    if not signatures:
        return {}
    connection.execute("DROP TABLE IF EXISTS requested_signatures")
    connection.execute("CREATE TEMP TABLE requested_signatures(signature TEXT PRIMARY KEY)")
    connection.executemany("INSERT INTO requested_signatures VALUES (?)", [(signature,) for signature in sorted(signatures)])
    rows = connection.execute(
        """SELECT p.signature,p.slot,p.tx_index,p.block_time_s
           FROM canonical_transaction_proofs p JOIN requested_signatures r USING(signature)"""
    ).fetchall()
    connection.execute("DROP TABLE requested_signatures")
    connection.commit()
    return {str(signature): (int(slot), int(index), int(block_time)) for signature, slot, index, block_time in rows}


def ingest(
    connection: sqlite3.Connection, normalized: dict[str, Any], metadata: dict[str, Any],
    evidence_path: pathlib.Path, proof: tuple[int, int, int],
) -> tuple[bool, str, int, int]:
    signature = str(metadata["signature"])
    receipt_ns = int(metadata["receivedAtUnixNs"])
    observed_slot = int(metadata["slot"])
    slot, tx_index, block_time = proof
    if str(normalized.get("signature") or "") != signature:
        return False, "NORMALIZED_SIGNATURE_MISMATCH", 0, 0
    if int(normalized.get("slot") or 0) != observed_slot or observed_slot != slot:
        return False, "CANONICAL_SLOT_MISMATCH", 0, 0
    # Source transaction index is useful diagnostic evidence only.  The block
    # index below is the authoritative ordering used by the state plane.
    creates, events, failure = relevant(normalized, signature, slot, tx_index)
    if not creates and not events:
        return False, failure or "NOT_PUMP_STATE_RELEVANT", 0, 0
    hint = {str(event.mint) for event in events} | creates
    connection.execute("INSERT OR IGNORE INTO transactions VALUES (?,?,?,?,?,?)", (
        signature, block_time, slot, tx_index,
        canonical([SCHEMA]), canonical(sorted(hint)),
    ))
    connection.execute("INSERT OR IGNORE INTO transaction_evidence VALUES (?,?)", (signature, str(evidence_path.resolve())))
    connection.execute(
        "INSERT INTO realtime_arrivals VALUES (?,?,?,?,?) ON CONFLICT(signature,source) DO NOTHING",
        (signature, SOURCE, receipt_ns, observed_slot, str(evidence_path.resolve())),
    )
    for mint in creates:
        connection.execute("INSERT OR IGNORE INTO create_events VALUES (?,?)", (signature, mint))
    if failure:
        for mint in hint:
            connection.execute("INSERT OR IGNORE INTO incomplete_events VALUES (?,?,?)", (signature, mint, failure))
        return True, failure, 0, len(creates)
    inserted = 0
    for event in events:
        before = connection.total_changes
        connection.execute("INSERT OR IGNORE INTO causal_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            event.signature, int(event.instruction_index), event.mint, int(event.is_buy), event.user,
            event.creator, int(event.quote_lamports), int(event.token_raw),
            int(event.pre_state.virtual_sol), int(event.pre_state.virtual_token), int(event.pre_state.real_sol), int(event.pre_state.real_token),
            int(event.post_state.virtual_sol), int(event.post_state.virtual_token), int(event.post_state.real_sol), int(event.post_state.real_token),
        ))
        inserted += int(connection.total_changes > before)
    return True, "OK", inserted, len(creates)


def load_state(
    connection: sqlite3.Connection, boundaries: list[int],
) -> tuple[dict[str, list[decision.CausalEvent]], dict[str, dict[str, Any]], dict[str, list[tuple[tuple[int, int, int, str], str]]]]:
    """Load only the exact causal interval needed by this deterministic batch.

    The state database may contain future windows from earlier completed work.
    Limiting by the original realtime receipt clock avoids both quadratic memory
    growth and accidental use of those later observations.
    """
    if not boundaries:
        return {}, {}, {}
    lower = min(boundaries) - legacy.MAX_AGE_NS
    upper = max(boundaries)
    connection.row_factory = sqlite3.Row
    connection.execute("DROP TABLE IF EXISTS state_scope_signatures")
    connection.execute("CREATE TEMP TABLE state_scope_signatures(signature TEXT PRIMARY KEY)")
    connection.execute(
        """INSERT INTO state_scope_signatures
           SELECT signature FROM realtime_arrivals
           WHERE source=? AND received_ns BETWEEN ? AND ?""",
        (SOURCE, lower, upper),
    )
    arrivals = {
        str(row[0]): (int(row[1]), int(row[2]))
        for row in connection.execute(
            "SELECT r.signature,r.received_ns,r.slot FROM realtime_arrivals r JOIN state_scope_signatures s USING(signature) WHERE r.source=?",
            (SOURCE,),
        )
    }
    events: dict[str, list[decision.CausalEvent]] = collections.defaultdict(list)
    for row in connection.execute("SELECT e.*,t.slot,t.tx_index FROM causal_events e JOIN transactions t USING(signature) JOIN state_scope_signatures s USING(signature)"):
        arrival = arrivals.get(str(row["signature"]))
        if arrival is None:
            continue
        events[str(row["mint"])].append(decision.CausalEvent(
            mint=str(row["mint"]), signature=str(row["signature"]), slot=int(row["slot"]),
            transaction_index=int(row["tx_index"]), instruction_index=int(row["instruction_index"]),
            received_ns=int(arrival[0]), is_buy=bool(row["is_buy"]), user=str(row["user"]), creator=str(row["creator"]),
            quote_lamports=int(row["quote_lamports"]), token_raw=int(row["token_raw"]),
            pre_state=CurveState(int(row["pre_virtual_sol"]), int(row["pre_virtual_token"]), int(row["pre_real_sol"]), int(row["pre_real_token"])),
            post_state=CurveState(int(row["post_virtual_sol"]), int(row["post_virtual_token"]), int(row["post_real_sol"]), int(row["post_real_token"])),
            realtime_source=SOURCE,
        ))
    creates: dict[str, dict[str, Any]] = {}
    for row in connection.execute("SELECT c.mint,c.signature,t.slot,t.tx_index,t.block_time_s,r.received_ns,r.slot AS receipt_slot FROM create_events c JOIN transactions t USING(signature) JOIN realtime_arrivals r USING(signature) JOIN state_scope_signatures s USING(signature) WHERE r.source=?", (SOURCE,)):
        candidate = {"mint": str(row["mint"]), "signature": str(row["signature"]), "order": (int(row["slot"]), int(row["tx_index"]), -1, str(row["signature"])), "blockTime": int(row["block_time_s"]), "receivedNs": int(row["received_ns"]), "slot": int(row["receipt_slot"]), "realtimeSource": SOURCE}
        prior = creates.get(candidate["mint"])
        if prior is None or tuple(candidate["order"]) < tuple(prior["order"]):
            creates[candidate["mint"]] = candidate
    incomplete: dict[str, list[tuple[tuple[int, int, int, str], str]]] = collections.defaultdict(list)
    for row in connection.execute("SELECT i.*,t.slot,t.tx_index FROM incomplete_events i JOIN transactions t USING(signature) JOIN state_scope_signatures s USING(signature)"):
        incomplete[str(row["mint"])].append(((int(row["slot"]), int(row["tx_index"]), -1, str(row["signature"])), str(row["reason"])))
    connection.execute("DROP TABLE state_scope_signatures")
    connection.row_factory = None
    return {mint: sorted(rows, key=lambda event: event.order) for mint, rows in events.items()}, creates, dict(incomplete)


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    timings: dict[str, float] = {}
    phase = time.perf_counter()
    funnel = json.loads(args.funnel.read_text(encoding="utf-8"))
    acquisitions = [row for row in funnel["rows"] if row.get("stage") == "CANONICAL_SUCCESSFUL_PUMP_ACQUISITION_PENDING_FULL_WINDOW"]
    timings["acquisitionLoadSeconds"] = time.perf_counter() - phase
    normalized, metadata, diagnostics = source_rows(args.evidence, args.normalizer, timings)
    phase = time.perf_counter()
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in normalized:
        signature = str(row["signature"])
        meta = metadata[signature]
        creates, events, failure = relevant(row, signature, int(meta["slot"]), int(row.get("transactionIndex") or 0))
        if creates or events or failure:
            candidates.append((row, meta))
    timings["relevanceClassificationSeconds"] = time.perf_counter() - phase
    report: dict[str, Any] = {"schema": SCHEMA, "dryRun": bool(args.dry_run), "outcomeBlind": True, "candidateFrames": len(candidates), "sourceFramesNormalized": len(normalized), "diagnostics": dict(diagnostics), "canonicalErrors": {}, "canonicalProofFailures": collections.Counter(), "decodeFailures": collections.Counter(), "directPumpDecodeFailures": 0, "pumpCpiDecodeFailures": 0, "transactionsInserted": 0, "eventsInserted": 0, "createsInserted": 0}
    state_path = args.state_db if not args.dry_run else args.state_db.with_suffix(".dryrun.sqlite3")
    if args.dry_run:
        state_path.unlink(missing_ok=True)
        state_path.with_suffix(state_path.suffix + "-wal").unlink(missing_ok=True)
        state_path.with_suffix(state_path.suffix + "-shm").unlink(missing_ok=True)
    phase = time.perf_counter()
    connection = open_adapter_state(state_path, args.evidence, args.expected_evidence_sha256)
    proof_cache = open_proof_cache(args.proof_cache) if args.proof_cache else None
    try:
        timings["stateOpenAndEvidenceIdentitySeconds"] = time.perf_counter() - phase
        phase = time.perf_counter()
        candidate_signatures = {str(meta["signature"]) for _, meta in candidates}
        local_cached = cached_canonical_proofs(connection, candidate_signatures)
        cached = dict(local_cached)
        external_cached = {}
        if proof_cache is not None:
            external_cached = external_cached_proofs(proof_cache, candidate_signatures - set(cached))
            cached.update(external_cached)
        proofs = dict(cached)
        timings["cachedCanonicalProofLookupSeconds"] = time.perf_counter() - phase
        phase = time.perf_counter()
        missing_signatures = candidate_signatures - set(proofs)
        missing_slots = {int(meta["slot"]) for _, meta in candidates if str(meta["signature"]) in missing_signatures}
        # getBlock returns every successful transaction in a requested slot.  The
        # state plane only needs canonical proofs for candidate signatures; caching
        # the entire block made dense slots write hundreds of thousands of unrelated
        # rows and obscured the distinction between block expansion and useful work.
        block_proofs, canonical_errors = canonical_proofs(args.rpc_url, missing_slots, args.canonical_fetch_workers)
        fetched_proofs = {signature: block_proofs[signature] for signature in missing_signatures if signature in block_proofs}
        cache_canonical_proofs(connection, fetched_proofs)
        report["inputEvidenceBytes"] = args.evidence.stat().st_size
        report["evidenceIdentityHashSource"] = "PROMOTION_MANIFEST" if args.expected_evidence_sha256 else "ADAPTER_FILE_SCAN"
        if proof_cache is not None:
            try:
                report["proofCacheWrite"] = cache_canonical_proofs(
                    proof_cache, fetched_proofs, shared_cache_path=args.proof_cache,
                )
            except RuntimeError as error:
                if str(error) != "PROOF_CACHE_WRITE_LOCK_TIMEOUT":
                    raise
                # The same finalized proofs have already committed to this
                # canonical state plane. The shared cache is an acceleration
                # layer only, so a contended cache write may be deferred but
                # must remain explicit in the immutable batch report.
                report["proofCacheWrite"] = {
                    "rows": len(fetched_proofs), "status": "DEFERRED_LOCK_TIMEOUT",
                    "reason": str(error),
                }
        proofs.update(fetched_proofs)
        report["canonicalErrors"] = dict(canonical_errors)
        report["canonicalProofsCached"] = len(cached)
        report["canonicalProofsCachedLocal"] = len(local_cached)
        report["canonicalProofsCachedExternal"] = len(external_cached)
        report["canonicalProofsFetched"] = len(fetched_proofs)
        report["canonicalProofsExpandedFromBlocks"] = len(block_proofs)
        report["canonicalProofsExcludedFromCache"] = len(block_proofs) - len(fetched_proofs)
        report["finalizedBlockRpcCalls"] = len(missing_slots)
        report["finalizedBlockRpcCallsAvoided"] = max(0, len({int(meta["slot"]) for _, meta in candidates}) - len(missing_slots))
        timings["canonicalProofRpcSeconds"] = time.perf_counter() - phase
        phase = time.perf_counter()
        commits = 0
        for start_index in range(0, len(candidates), args.ingest_transaction_batch_size):
            connection.execute("BEGIN IMMEDIATE")
            try:
                for row, meta in candidates[start_index:start_index + args.ingest_transaction_batch_size]:
                    signature = str(meta["signature"])
                    proof = proofs.get(signature)
                    if proof is None:
                        report["canonicalProofFailures"]["CANONICAL_SUCCESS_OR_BLOCK_PROOF_MISSING"] += 1
                        continue
                    if proof[0] != int(meta["slot"]):
                        report["canonicalProofFailures"]["CANONICAL_SLOT_MISMATCH"] += 1
                        continue
                    accepted, reason, events, creates = ingest(connection, row, meta, args.evidence, proof)
                    is_router = "PUMP_CPI_ROUTER_FILTER_MATCH" in set(meta.get("pumpInstructionKinds") or [])
                    if not accepted:
                        report["decodeFailures"][reason] += 1
                        report["pumpCpiDecodeFailures" if is_router else "directPumpDecodeFailures"] += 1
                    else:
                        report["transactionsInserted"] += 1
                        report["eventsInserted"] += events
                        report["createsInserted"] += creates
                        if reason != "OK":
                            report["decodeFailures"][reason] += 1
                            report["pumpCpiDecodeFailures" if is_router else "directPumpDecodeFailures"] += 1
                connection.commit()
                commits += 1
            except BaseException:
                connection.rollback()
                raise
        timings["canonicalStatePlaneIngestSeconds"] = time.perf_counter() - phase
        report["statePlaneIngestTransactionBatchSize"] = args.ingest_transaction_batch_size
        report["statePlaneIngestCommitCount"] = commits
        phase = time.perf_counter()
        boundaries = [int(row["receivedAtUnixNs"]) for row in acquisitions]
        events, creates, incomplete = load_state(connection, boundaries)
        timings["scopedStateLoadSeconds"] = time.perf_counter() - phase
        phase = time.perf_counter()
        replay_rows = []
        for acquisition in acquisitions:
            for mint in acquisition.get("acquiredMints") or []:
                stored_order = connection.execute(
                    "SELECT tx_index FROM transactions WHERE signature=?", (acquisition["signature"],)
                ).fetchone()
                selected = {
                    "signature": acquisition["signature"], "mint": mint,
                    "entryReceivedAtUnixNs": int(acquisition["receivedAtUnixNs"]),
                    "entrySlot": int(acquisition["canonicalSlot"]),
                    "entryTransactionIndex": int(stored_order[0]) if stored_order else int(proofs.get(acquisition["signature"], (0, -1, 0))[1]),
                    "route": "SOL_PUMP_FULL_META_PROSPECTIVE",
                    "acquisitionClass": "CANONICAL_SUCCESSFUL_PUMP_ACQUISITION",
                }
                choice, reasons, detail = legacy.build_choice(selected, events, creates, incomplete)
                replay_rows.append({"signature": selected["signature"], "mint": mint, "selectedCreatePass": mint in creates, "full120sWindowPass": True, "selectedExactStateComplete": not any(reason.startswith("SELECTED_") for reason in reasons), "legalAlternativeWithCompleteState": not ("NO_CONTEMPORANEOUS_ALTERNATIVE" in reasons), "symmetricEligible": choice is not None, "failureReasons": reasons, "detail": detail})
        timings["symmetricEligibilityEvaluationSeconds"] = time.perf_counter() - phase
        report["postBoundaryAcquisitionsTested"] = len(acquisitions)
        report["replayRows"] = replay_rows
        report["selectedCreatePass"] = sum(bool(row["selectedCreatePass"]) for row in replay_rows)
        report["selectedExactStateComplete"] = sum(bool(row["selectedExactStateComplete"]) for row in replay_rows)
        report["legalAlternativeWithCompleteState"] = sum(bool(row["legalAlternativeWithCompleteState"]) for row in replay_rows)
        report["symmetricEligible"] = sum(bool(row["symmetricEligible"]) for row in replay_rows)
        report["causalEventsMaterialized"] = int(connection.execute("SELECT COUNT(*) FROM causal_events").fetchone()[0])
        report["statePlaneTransactions"] = int(connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0])
        report["idempotencyDigest"] = compact_idempotency_digest(connection)
        report["idempotencyDigestFormat"] = "sha256-legacy-canonical-state-v1"
    finally:
        if proof_cache is not None:
            proof_cache.close()
        connection.close()
    report["canonicalProofFailures"] = dict(sorted(report["canonicalProofFailures"].items()))
    report["decodeFailures"] = dict(sorted(report["decodeFailures"].items()))
    timings["totalSeconds"] = time.perf_counter() - started
    report["timingsSeconds"] = {key: round(value, 6) for key, value in timings.items()}
    phase = time.perf_counter()
    write_report_atomic(args.report, report)
    report["timingsSeconds"]["artifactWriteSeconds"] = round(time.perf_counter() - phase, 6)
    print(json.dumps({key: value for key, value in report.items() if key != "replayRows"}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=pathlib.Path, required=True)
    parser.add_argument("--funnel", type=pathlib.Path, required=True)
    parser.add_argument("--state-db", type=pathlib.Path, required=True)
    parser.add_argument("--normalizer", type=pathlib.Path, required=True)
    parser.add_argument("--rpc-url")
    parser.add_argument("--env", type=pathlib.Path, default=pathlib.Path("/root/bot/EdgeSniffer/.env"))
    parser.add_argument("--report", type=pathlib.Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--canonical-fetch-workers", type=int, default=4)
    parser.add_argument("--ingest-transaction-batch-size", type=int, default=512)
    parser.add_argument("--proof-cache", type=pathlib.Path)
    parser.add_argument("--expected-evidence-sha256")
    args = parser.parse_args()
    if not args.rpc_url:
        args.rpc_url = read_env(args.env, "SECURE_RPC_URL")
    if args.canonical_fetch_workers < 1 or args.canonical_fetch_workers > 16:
        parser.error("--canonical-fetch-workers must be between 1 and 16")
    if args.ingest_transaction_batch_size < 1 or args.ingest_transaction_batch_size > 4096:
        parser.error("--ingest-transaction-batch-size must be between 1 and 4096")
    run(args)


if __name__ == "__main__":
    main()
