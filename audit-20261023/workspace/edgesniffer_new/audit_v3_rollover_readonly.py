#!/usr/bin/env python3
"""Read-only V3 rollover metadata query. Never writes scientific state."""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


db = Path(sys.argv[1]).resolve()
closure_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
start = "2026-09-30T14:59:50Z"
end = "2026-09-30T15:00:15Z"


def utc_ns(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1_000_000_000)


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


start_ns = utc_ns(start)
end_ns = utc_ns(end)
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
try:
    ticks = conn.execute(
        "SELECT t.tick_ns, t.emitted_ns, c.wake_lateness_ns, c.writer_wait_ns, "
        "c.snapshot_ns, c.commit_ns, c.candidate_count, c.source_watermark_ns "
        "FROM cadence_ticks t LEFT JOIN cadence_telemetry c USING(tick_ns) "
        "WHERE t.tick_ns >= ? AND t.tick_ns <= ? ORDER BY t.tick_ns",
        (start_ns, end_ns),
    ).fetchall()
    expected = list(range(start_ns, end_ns + 1, 1_000_000_000))
    actual = {row[0] for row in ticks}
    telemetry = [row for row in ticks if row[2] is not None]
    payload = {
        "window": [start, end],
        "expected": len(expected),
        "actual": len(ticks),
        "missing_tick_ns": [tick for tick in expected if tick not in actual],
        "duplicates": len(ticks) - len(actual),
        "ticks": [
            {
                "tick_ns": row[0],
                "emitted_ns": row[1],
                "wake_lateness_ns": row[2],
                "writer_wait_ns": row[3],
                "snapshot_ns": row[4],
                "commit_ns": row[5],
                "candidate_count": row[6],
                "source_watermark_ns": row[7],
            }
            for row in ticks
            if row[0] in {utc_ns(f"2026-09-30T14:59:{s:02d}Z") for s in [59]}
            or row[0] in {utc_ns(f"2026-09-30T15:00:{s:02d}Z") for s in range(4)}
        ],
        "wake_lateness_ns": {
            "p50": percentile([row[2] for row in telemetry], .50),
            "p95": percentile([row[2] for row in telemetry], .95),
            "p99": percentile([row[2] for row in telemetry], .99),
            "max": max((row[2] for row in telemetry), default=None),
        },
        "teacher_labels_through_failure": conn.execute(
            "SELECT COUNT(*) FROM teacher_labels WHERE receipt_ns <= ? AND finality='FINAL'",
            (utc_ns("2026-09-30T15:00:02.024192Z"),),
        ).fetchone()[0],
    }
    if closure_path:
        closure_rows = []
        with closure_path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                closed_ns = utc_ns(row["closedUtc"])
                if closed_ns < start_ns:
                    continue
                if closed_ns > end_ns:
                    break
                closure_rows.append((
                    row["writerSequence"],
                    row["selectedReceivedAtUnixNs"],
                    closed_ns,
                ))
        lags = [closed - received for _, received, closed in closure_rows]
        sequence_monotonic = all(
            prior[0] < current[0] for prior, current in zip(closure_rows, closure_rows[1:])
        )
        payload["canonical_closure"] = {
            "count": len(closure_rows),
            "first": closure_rows[0] if closure_rows else None,
            "last": closure_rows[-1] if closure_rows else None,
            "sequence_monotonic": sequence_monotonic,
            "lag_ns": {
                "p50": percentile(lags, .50),
                "p95": percentile(lags, .95),
                "p99": percentile(lags, .99),
                "max": max(lags, default=None),
            },
        }
    print(json.dumps(payload, sort_keys=True))
finally:
    conn.close()
