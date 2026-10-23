#!/usr/bin/env python3
"""Small, non-invasive catalog for immutable research evidence.

This tool deliberately does not move, compress, delete, or modify evidence.
It records immutable object metadata, active experiment pins, and a guarded
reclaim *plan*.  A separate, future verified archiver must consume that plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS experiments (
  experiment_id TEXT PRIMARY KEY,
  cohort TEXT NOT NULL,
  status TEXT NOT NULL,
  created_utc TEXT NOT NULL,
  notes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS objects (
  path TEXT PRIMARY KEY,
  object_kind TEXT NOT NULL,
  lifecycle TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  modified_ns INTEGER NOT NULL,
  inode INTEGER,
  target TEXT,
  recorded_utc TEXT NOT NULL,
  content_sha256 TEXT,
  hash_scope TEXT,
  verification TEXT NOT NULL,
  FOREIGN KEY(path) REFERENCES objects(path)
);
CREATE TABLE IF NOT EXISTS pins (
  experiment_id TEXT NOT NULL,
  path TEXT NOT NULL,
  pin_type TEXT NOT NULL,
  created_utc TEXT NOT NULL,
  reason TEXT NOT NULL,
  PRIMARY KEY (experiment_id, path, pin_type),
  FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id),
  FOREIGN KEY(path) REFERENCES objects(path)
);
CREATE TABLE IF NOT EXISTS receipts (
  receipt_path TEXT PRIMARY KEY,
  receipt_sha256 TEXT NOT NULL,
  receipt_kind TEXT NOT NULL,
  recorded_utc TEXT NOT NULL
);
"""

ACTIVE_LIFECYCLES = {"OPEN", "ACTIVE", "PROMOTED", "PINNED", "UNKNOWN"}


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def db_connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def classify(path: Path, active_roots: list[Path]) -> tuple[str, str]:
    resolved = path.resolve(strict=False)
    if any(resolved == root or root in resolved.parents for root in active_roots):
        return "ACTIVE", "active-run-root"
    if path.is_symlink():
        return "ARCHIVED_ONLY", "symlinked-local-release"
    name = path.name.lower()
    if name.endswith((".partial", ".open")) or "partial" in name:
        return "OPEN", "open-or-partial-name"
    if "/cache" in str(resolved) or "/tmp" in str(resolved):
        return "CANDIDATE_REBUILDABLE", "cache-or-temp-path"
    return "UNKNOWN", "requires-explicit-verification"


def upsert_object(conn: sqlite3.Connection, path: Path, kind: str, lifecycle: str,
                  verification: str, hash_value: str | None = None,
                  hash_scope: str | None = None) -> None:
    stat = path.lstat()
    target = os.readlink(path) if path.is_symlink() else None
    conn.execute(
        """INSERT INTO objects(path, object_kind, lifecycle, bytes, modified_ns, inode, target,
           recorded_utc, content_sha256, hash_scope, verification)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET object_kind=excluded.object_kind,
             lifecycle=excluded.lifecycle, bytes=excluded.bytes, modified_ns=excluded.modified_ns,
             inode=excluded.inode, target=excluded.target, recorded_utc=excluded.recorded_utc,
             content_sha256=COALESCE(excluded.content_sha256, objects.content_sha256),
             hash_scope=COALESCE(excluded.hash_scope, objects.hash_scope), verification=excluded.verification""",
        (str(path), kind, lifecycle, stat.st_size, stat.st_mtime_ns, stat.st_ino, target,
         utcnow(), hash_value, hash_scope, verification),
    )


def command_init(args: argparse.Namespace) -> dict[str, Any]:
    db = Path(args.db)
    with db_connect(db) as conn:
        conn.commit()
    return {"status": "INITIALIZED", "catalog": str(db), "utc": utcnow(), "schema": "evidence-lake-v1"}


def command_pin(args: argparse.Namespace) -> dict[str, Any]:
    db = Path(args.db)
    paths = [Path(item).absolute() for item in args.path]
    missing = [str(path) for path in paths if not path.exists() and not path.is_symlink()]
    if missing:
        raise ValueError(f"refusing to pin missing path(s): {missing}")
    with db_connect(db) as conn:
        conn.execute("INSERT OR IGNORE INTO experiments VALUES(?,?,?,?,?)",
                     (args.experiment, args.cohort, args.status, utcnow(), args.notes))
        for path in paths:
            upsert_object(conn, path, "PATH", "PINNED", "metadata-only-pin")
            conn.execute("INSERT OR REPLACE INTO pins VALUES(?,?,?,?,?)",
                         (args.experiment, str(path), args.pin_type, utcnow(), args.reason))
        conn.commit()
    return {"status": "PINNED", "experiment": args.experiment, "count": len(paths),
            "pin_type": args.pin_type, "paths": [str(item) for item in paths]}


def command_inventory(args: argparse.Namespace) -> dict[str, Any]:
    db = Path(args.db)
    active_roots = [Path(item).resolve(strict=False) for item in args.active_root]
    candidates: list[dict[str, Any]] = []
    with db_connect(db) as conn:
        for raw in args.path:
            path = Path(raw).absolute()
            if not path.exists() and not path.is_symlink():
                candidates.append({"path": str(path), "status": "MISSING"})
                continue
            lifecycle, reason = classify(path, active_roots)
            upsert_object(conn, path, "PATH", lifecycle, "metadata-only-inventory")
            stat = path.lstat()
            candidates.append({"path": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                               "lifecycle": lifecycle, "reason": reason,
                               "action": "PROTECT" if lifecycle in ACTIVE_LIFECYCLES else "REVIEW_ONLY"})
        conn.commit()
    return {"status": "DRY_RUN", "utc": utcnow(), "objects": candidates,
            "guarantee": "no files moved, compressed, deleted, or modified"}


def command_receipt(args: argparse.Namespace) -> dict[str, Any]:
    db = Path(args.db)
    receipt = Path(args.receipt).absolute()
    if not receipt.is_file():
        raise ValueError(f"receipt is not a regular file: {receipt}")
    digest = sha256_file(receipt)
    with db_connect(db) as conn:
        conn.execute("INSERT OR REPLACE INTO receipts VALUES(?,?,?,?)",
                     (str(receipt), digest, args.receipt_kind, utcnow()))
        conn.commit()
    return {"status": "RECORDED", "receipt": str(receipt), "sha256": digest}


def command_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    db = Path(args.db)
    output = Path(args.output).absolute()
    with db_connect(db) as conn:
        rows = conn.execute("SELECT path, object_kind, lifecycle, bytes, modified_ns, target, content_sha256, hash_scope, verification FROM objects ORDER BY path").fetchall()
        pins = conn.execute("SELECT experiment_id, path, pin_type, created_utc, reason FROM pins ORDER BY experiment_id, path").fetchall()
        experiments = conn.execute("SELECT experiment_id, cohort, status, created_utc, notes FROM experiments ORDER BY experiment_id").fetchall()
        receipts = conn.execute("SELECT receipt_path, receipt_sha256, receipt_kind, recorded_utc FROM receipts ORDER BY receipt_path").fetchall()
    previous = None
    if args.previous_snapshot:
        previous_path = Path(args.previous_snapshot).absolute()
        if not previous_path.is_file():
            raise ValueError(f"previous snapshot is not a regular file: {previous_path}")
        previous = sha256_file(previous_path)
    payload = {"schema": "evidence-lake-v1", "generated_utc": utcnow(), "catalog": str(db),
               "experiments": experiments, "objects": rows, "pins": pins, "receipts": receipts}
    if previous:
        payload["previous_catalog_sha256"] = previous
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json(payload))
    return {"status": "SNAPSHOT_WRITTEN", "snapshot": str(output), "sha256": sha256_file(output),
            "object_count": len(rows), "pin_count": len(pins)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="local SQLite metadata catalog")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init").set_defaults(func=command_init)
    pin = sub.add_parser("pin")
    pin.add_argument("--experiment", required=True); pin.add_argument("--cohort", required=True)
    pin.add_argument("--status", default="RUNNING"); pin.add_argument("--notes", default="")
    pin.add_argument("--pin-type", default="PIN_ACTIVE"); pin.add_argument("--reason", required=True)
    pin.add_argument("--path", action="append", required=True); pin.set_defaults(func=command_pin)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("--path", action="append", required=True)
    inventory.add_argument("--active-root", action="append", default=[])
    inventory.set_defaults(func=command_inventory)
    receipt = sub.add_parser("receipt")
    receipt.add_argument("--receipt", required=True); receipt.add_argument("--receipt-kind", default="ARCHIVE")
    receipt.set_defaults(func=command_receipt)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--output", required=True)
    snapshot.add_argument("--previous-snapshot")
    snapshot.set_defaults(func=command_snapshot)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        print(json.dumps(args.func(args), sort_keys=True))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
