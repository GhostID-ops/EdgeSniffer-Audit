#!/usr/bin/env python3
"""Bounded, outcome-blind finalized-proof prefetch for the full-meta lane."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import pathlib
import sqlite3
import time
from typing import Any

from reconcilePumpExactEvidenceLedgerV1 import iter_frames
from twoCqFullMetaCanonicalStatePlaneV1 import canonical_proofs, cache_canonical_proofs, external_cached_proofs, open_proof_cache, read_env


WINDOW_NS = 120_000_000_000
SCHEMA = "2CQ_FULL_META_CANONICAL_PROOF_PREFETCH_V1"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def pending_window(work_db: pathlib.Path, limit: int, span_ns: int) -> list[sqlite3.Row]:
    connection = sqlite3.connect(work_db, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT signature,mint,received_ns,slot FROM acquisition_work
               WHERE status IN ('PENDING_MATERIALIZATION','RETRYABLE')
                 AND received_ns>0 AND batch_id IS NULL
               ORDER BY received_ns,signature,mint"""
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        return []
    first = int(rows[0]["received_ns"])
    return [row for row in rows if int(row["received_ns"]) <= first + span_ns][:limit]


def observed_signatures_by_slot(root: pathlib.Path, rows: list[sqlite3.Row]) -> dict[int, set[str]]:
    start_ns = min(int(row["received_ns"]) for row in rows) - WINDOW_NS
    end_ns = max(int(row["received_ns"]) for row in rows)
    hour = dt.datetime.fromtimestamp(start_ns / 1_000_000_000, dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    last = dt.datetime.fromtimestamp(end_ns / 1_000_000_000, dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    signatures_by_slot: dict[int, set[str]] = {}
    while hour <= last:
        directory = root / "partitions" / hour.strftime("%Y/%m/%d/%H")
        path = directory / "evidence.frames.zst"
        if not path.exists():
            path = directory / "evidence.frames.partial"
        if path.exists():
            for metadata, _ in iter_frames(path, path.name.endswith(".partial")):
                if metadata.get("kind") != "transaction":
                    continue
                if metadata.get("rawFormat") != "yellowstone_subscribe_update_protobuf_full_meta":
                    continue
                received_ns = int(metadata.get("receivedAtUnixNs") or 0)
                slot = int(metadata.get("slot") or 0)
                signature = str(metadata.get("signature") or "")
                if start_ns <= received_ns <= end_ns and slot and signature:
                    signatures_by_slot.setdefault(slot, set()).add(signature)
        hour += dt.timedelta(hours=1)
    return signatures_by_slot


def atomic_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    with args.lock.open("w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"schema": SCHEMA, "status": "LOCK_BUSY", "outcomeBlind": True}
        rows = pending_window(args.work_db, args.lookahead_acquisitions, args.max_batch_span_s * 1_000_000_000)
        if not rows:
            return {"schema": SCHEMA, "status": "NO_PENDING_WORK", "outcomeBlind": True}
        signatures_by_slot = observed_signatures_by_slot(args.root, rows)
        slots = set(signatures_by_slot)
        if len(slots) > args.max_slots:
            slots = set(sorted(slots)[:args.max_slots])
            signatures_by_slot = {slot: signatures_by_slot[slot] for slot in slots}
        observed_signatures = set().union(*signatures_by_slot.values()) if signatures_by_slot else set()
        cache = open_proof_cache(args.proof_cache)
        try:
            cached = external_cached_proofs(cache, observed_signatures)
            missing_signatures = observed_signatures - set(cached)
            missing_slots = {slot for slot, signatures in signatures_by_slot.items() if signatures & missing_signatures}
            started = time.perf_counter()
            block_proofs, errors = canonical_proofs(read_env(args.env, "SECURE_RPC_URL"), missing_slots, args.fetch_workers)
            proofs = {signature: block_proofs[signature] for signature in missing_signatures if signature in block_proofs}
            cache_write = cache_canonical_proofs(cache, proofs, shared_cache_path=args.proof_cache)
            elapsed = time.perf_counter() - started
        finally:
            cache.close()
        result = {
            "schema": SCHEMA, "status": "OK", "runAtUtc": utc_now(), "outcomeBlind": True,
            "selectedAcquisitions": len(rows), "windowStartNs": min(int(row["received_ns"]) for row in rows) - WINDOW_NS,
            "windowEndNs": max(int(row["received_ns"]) for row in rows), "observedSlots": len(slots),
            "cachedSignatures": len(cached), "missingSignatures": len(missing_signatures),
            "fetchedSlots": len(missing_slots), "proofsInserted": len(proofs),
            "proofsExpandedFromBlocks": len(block_proofs),
            "proofsExcludedFromCache": len(block_proofs) - len(proofs),
            "rpcErrors": dict(errors), "rpcSeconds": round(elapsed, 6), "fetchWorkers": args.fetch_workers,
            "proofCacheWrite": cache_write,
        }
        atomic_json(args.report, result)
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--work-db", type=pathlib.Path, required=True)
    parser.add_argument("--proof-cache", type=pathlib.Path, required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    parser.add_argument("--lock", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, default=pathlib.Path("/root/bot/EdgeSniffer/.env"))
    parser.add_argument("--lookahead-acquisitions", type=int, default=2)
    parser.add_argument("--max-batch-span-s", type=int, default=120)
    parser.add_argument("--max-slots", type=int, default=600)
    parser.add_argument("--fetch-workers", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.fetch_workers <= 16:
        parser.error("--fetch-workers must be between 1 and 16")
    print(json.dumps(run(args), sort_keys=True))


if __name__ == "__main__":
    main()
