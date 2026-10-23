#!/usr/bin/env python3
"""Focused Linux regression coverage for the shared full-meta proof cache."""

from __future__ import annotations

import multiprocessing as mp
import os
import pathlib
import sqlite3
import tempfile

from twoCqFullMetaCanonicalStatePlaneV1 import cache_canonical_proofs, open_proof_cache


def write_batches(path: str, worker: int) -> None:
    database = pathlib.Path(path)
    connection = open_proof_cache(database)
    try:
        for batch in range(20):
            proofs = {
                f"{worker:02d}-{batch:02d}-{item:02d}": (1_000_000 + batch, item, 1_700_000_000)
                for item in range(5)
            }
            result = cache_canonical_proofs(connection, proofs, shared_cache_path=database)
            assert result["rows"] == 5
    finally:
        connection.close()


def abandon_uncommitted_write(path: str) -> None:
    connection = open_proof_cache(pathlib.Path(path))
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT OR IGNORE INTO canonical_transaction_proofs VALUES (?,?,?,?)",
        ("uncommitted", 1, 1, 1),
    )
    os._exit(0)


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        database = pathlib.Path(temporary) / "proof-cache.sqlite3"
        workers = [mp.Process(target=write_batches, args=(str(database), worker)) for worker in range(6)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(90)
            assert worker.exitcode == 0, worker.exitcode

        connection = open_proof_cache(database)
        try:
            expected = 6 * 20 * 5
            assert connection.execute("SELECT COUNT(*) FROM canonical_transaction_proofs").fetchone()[0] == expected
            cache_canonical_proofs(connection, {"00-00-00": (1_000_000, 0, 1_700_000_000)}, shared_cache_path=database)
            assert connection.execute("SELECT COUNT(*) FROM canonical_transaction_proofs").fetchone()[0] == expected
        finally:
            connection.close()

        interrupted = mp.Process(target=abandon_uncommitted_write, args=(str(database),))
        interrupted.start()
        interrupted.join(30)
        assert interrupted.exitcode == 0

        connection = open_proof_cache(database)
        try:
            cache_canonical_proofs(connection, {"after-interrupt": (2, 2, 2)}, shared_cache_path=database)
            assert connection.execute("SELECT COUNT(*) FROM canonical_transaction_proofs WHERE signature='after-interrupt'").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM canonical_transaction_proofs WHERE signature='uncommitted'").fetchone()[0] == 0
            assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        finally:
            connection.close()
    print("PASS: shared proof-cache concurrency, duplicate writes, and interrupted transaction recovery")


if __name__ == "__main__":
    main()
