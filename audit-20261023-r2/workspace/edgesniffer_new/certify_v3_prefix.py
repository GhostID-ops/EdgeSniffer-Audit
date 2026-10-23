#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path


root = Path(sys.argv[1])
boundary = dt.datetime.fromisoformat("2026-09-30T07:42:31.455772+00:00")
wall = dt.datetime.fromisoformat("2026-09-30T08:00:02.002132+00:00")
start = boundary + dt.timedelta(seconds=120)
end = wall - dt.timedelta(seconds=20)
ns = lambda value: int(value.timestamp() * 1_000_000_000)
database = sqlite3.connect(root / "lane" / "autonomous-when-v3.sqlite3")
labels = database.execute(
    "select label_key,sig,mint,receipt_ns,slot,raw_sha from teacher_labels where finality='FINAL' and receipt_ns>=? and receipt_ns<=? order by receipt_ns,label_key",
    (ns(start), ns(end)),
).fetchall()
ticks = database.execute(
    "select count(*),min(tick_ns),max(tick_ns) from cadence_ticks where tick_ns>=? and tick_ns<=?",
    (ns(start), ns(end)),
).fetchone()
payload = {
    "schema": "AUTONOMOUS_WHEN_V3_FAILED_SEGMENT_PREFIX_CERTIFICATION_V1",
    "root": str(root),
    "boundaryUtc": boundary.isoformat().replace("+00:00", "Z"),
    "warmupSeconds": 120,
    "hardWallUtc": wall.isoformat().replace("+00:00", "Z"),
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
out = root / "receipts" / "failed-segment-prefix-certification-v1.json"
tmp = out.with_suffix(".tmp")
tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
os.replace(tmp, out)
print(out)
print(hashlib.sha256(out.read_bytes()).hexdigest())
print(json.dumps({"duration": payload["certifiedDurationSeconds"], "ticks": ticks[0], "labels": len(labels)}))
