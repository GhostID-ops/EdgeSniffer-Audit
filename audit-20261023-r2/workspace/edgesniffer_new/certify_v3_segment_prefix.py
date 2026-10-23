#!/usr/bin/env python3
"""Immutable certification of one V3 pre-hard-wall segment prefix."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for data in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--wall", required=True)
    parser.add_argument("--receipt-name", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    boundary = parse(args.boundary)
    hard_wall = parse(args.wall)
    start = boundary + timedelta(seconds=120)
    end = hard_wall - timedelta(seconds=20)
    if end <= start:
        raise SystemExit("hard wall leaves no certifiable prefix")
    db_path = root / "lane" / "autonomous-when-v3.sqlite3"
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        labels = db.execute(
            "SELECT label_key,sig,mint,receipt_ns,slot,raw_sha FROM teacher_labels "
            "WHERE finality='FINAL' AND receipt_ns>=? AND receipt_ns<=? ORDER BY receipt_ns,label_key",
            (ns(start), ns(end)),
        ).fetchall()
        ticks = db.execute(
            "SELECT COUNT(*),MIN(tick_ns),MAX(tick_ns),COUNT(DISTINCT tick_ns) FROM cadence_ticks "
            "WHERE tick_ns>=? AND tick_ns<=?", (ns(start), ns(end)),
        ).fetchone()
    finally:
        db.close()
    identities = {(row[1], row[2]) for row in labels}
    if len(identities) != len(labels):
        raise SystemExit("duplicate finalized teacher identity")
    if ticks[0] != ticks[3]:
        raise SystemExit("duplicate cadence tick")
    payload = {
        "schema": "AUTONOMOUS_WHEN_V3_FAILED_SEGMENT_PREFIX_CERTIFICATION_V1",
        "root": str(root),
        "boundaryUtc": boundary.isoformat().replace("+00:00", "Z"),
        "warmupSeconds": 120,
        "hardWallUtc": hard_wall.isoformat().replace("+00:00", "Z"),
        "suffixIsolationSeconds": 20,
        "certifiedStartUtc": start.isoformat().replace("+00:00", "Z"),
        "certifiedEndUtc": end.isoformat().replace("+00:00", "Z"),
        "certifiedDurationSeconds": (end - start).total_seconds(),
        "cadenceTicks": {"count": ticks[0], "first": ticks[1], "last": ticks[2]},
        "finalizedTeacherLabels": [
            {"labelKey": key, "signature": sig, "mint": mint, "receiptNs": receipt, "slot": slot, "rawSha256": raw_sha}
            for key, sig, mint, receipt, slot, raw_sha in labels
        ],
        "certifiableFinalizedIndependentTeacherN": len(labels),
        "crossWallState": "PROHIBITED",
        "postWallEvidence": "EXCLUDED",
        "result": "CERTIFIED_PREFIX_ONLY",
    }
    destination = root / "receipts" / args.receipt_name
    if destination.exists():
        raise SystemExit(f"immutable receipt exists: {destination}")
    temporary = destination.with_suffix(".partial")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    print(json.dumps({"receipt": str(destination), "sha256": sha256(destination), "seconds": payload["certifiedDurationSeconds"], "labels": len(labels), "ticks": ticks[0]}, sort_keys=True))


if __name__ == "__main__":
    main()
