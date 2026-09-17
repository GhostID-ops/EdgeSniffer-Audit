#!/usr/bin/env python3
"""Read-only, outcome-blind EdgeSniffer runtime evidence exporter."""

from __future__ import annotations

import collections
import csv
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
from typing import Any


OUT = pathlib.Path(sys.argv[1]).resolve()
V3 = pathlib.Path("/mnt/HC_Volume_106775198/edgesniffer-research/2cq-prospective-symmetric-choice-census-v3")
EXACT = pathlib.Path("/var/lib/pump-exact-evidence-ledger-v1")
DECISIONS = V3 / "causal-state-v2.sqlite3"
NOW = dt.datetime.now(dt.timezone.utc)
UNITS = [
    "pump-exact-evidence-ledger-v1.service",
    "pump-exact-evidence-ledger-v1-reconcile.service",
    "pump-exact-evidence-ledger-v1-reconcile.timer",
    "edgesniffer-2cq-symmetric-choice-materializer-v3.service",
    "edgesniffer-2cq-symmetric-choice-materializer-v3.timer",
    "edgesniffer-2cq-capture-first-v1.service",
    "edgesniffer-allenhark-compact-delivery-observer-v1.service",
]
SECRET = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|private[_-]?key|authorization)"
    r"\s*([=:])\s*([^\s,;]+)"
)
URL_SECRET = re.compile(r"(?i)([?&](?:api[_-]?key|token|key)=)[^&\s]+")
BASE58_IDENTIFIER = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{40,88}\b")
SAFE_LOG = re.compile(
    r"(?i)(starting|started|finished|stopped|failed|failure|timeout|killed|oom|memory|"
    r"consumed|exit|lock|wait|traceback|error|warning|resource|peak|duration)"
)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def pseudonym(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def redact(text: str) -> str:
    text = SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    return URL_SECRET.sub(lambda m: f"{m.group(1)}[REDACTED]", text)


def run(args: list[str], timeout: int = 30) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    return redact((result.stdout + result.stderr).strip())


def write_text(relative: str, value: str) -> None:
    path = OUT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def write_json(relative: str, value: Any) -> None:
    write_text(relative, json.dumps(value, indent=2, sort_keys=True))


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "min": min(values) if values else None,
        "p50": percentile(values, 0.50),
        "p75": percentile(values, 0.75),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values) if values else None,
        "mean": sum(values) / len(values) if values else None,
    }


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def collect_system() -> None:
    commands = {
        "timestamp.txt": ["date", "-u", "+%Y-%m-%dT%H:%M:%S.%NZ"],
        "uptime.txt": ["uptime"],
        "memory.txt": ["free", "-b"],
        "vmstat.txt": ["vmstat", "1", "5"],
        "disk.txt": ["df", "-B1", "-x", "tmpfs", "-x", "devtmpfs"],
        "mounts.txt": ["findmnt", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS"],
        "processes.txt": ["ps", "-eo", "pid,ppid,etimes,%cpu,%mem,rss,vsz,stat,comm", "--sort=-rss"],
        "pressure-cpu.txt": ["cat", "/proc/pressure/cpu"],
        "pressure-memory.txt": ["cat", "/proc/pressure/memory"],
        "pressure-io.txt": ["cat", "/proc/pressure/io"],
        "swaps.txt": ["cat", "/proc/swaps"],
        "block-devices.txt": ["lsblk", "-b", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS"],
    }
    for name, command in commands.items():
        write_text(f"system/{name}", run(command, 60))
    if pathlib.Path("/usr/bin/iostat").exists():
        write_text("system/iostat.txt", run(["iostat", "-xz", "1", "3"], 15))
    else:
        write_text("system/iostat-unavailable.txt", "iostat is not installed; no historical device latency series exists.")
    write_text("system/kernel-oom-7d.txt", run([
        "journalctl", "-k", "--since", "7 days ago", "--no-pager",
        "--grep", "oom|out of memory|killed process|memory cgroup",
    ], 60))


def collect_units() -> None:
    show_properties = (
        "Id,LoadState,ActiveState,SubState,UnitFileState,MainPID,NRestarts,Result,"
        "ExecMainStatus,ActiveEnterTimestamp,InactiveEnterTimestamp,ExecMainStartTimestamp,"
        "ExecMainExitTimestamp,MemoryCurrent,MemoryPeak,MemoryHigh,MemoryMax,CPUUsageNSec,"
        "TasksCurrent,IOReadBytes,IOWriteBytes,TimeoutStartUSec,TimeoutStopUSec,"
        "NextElapseUSecRealtime,LastTriggerUSec"
    )
    for unit in UNITS:
        safe = unit.replace("/", "_")
        write_text(f"systemd/{safe}.cat.txt", run(["systemctl", "cat", unit]))
        write_text(f"systemd/{safe}.show.txt", run([
            "systemctl", "show", unit, f"--property={show_properties}"
        ]))
    write_text("systemd/timers.txt", run(["systemctl", "list-timers", "--all", "--no-pager"]))
    failed = run(["systemctl", "--failed", "--plain", "--no-legend", "--no-pager"])
    relevant_failed = [
        line for line in failed.splitlines()
        if any(unit.removesuffix(".service").removesuffix(".timer") in line for unit in UNITS)
    ]
    write_text("systemd/failed-relevant-units.txt", "\n".join(relevant_failed) or "none")

    for days in (1, 7):
        rows = []
        for unit in UNITS:
            if not unit.endswith(".service"):
                continue
            raw = run([
                "journalctl", "-u", unit, "--since", f"{days} days ago",
                "--no-pager", "-o", "json", "--grep", SAFE_LOG.pattern,
                "--case-sensitive=no", "--lines", "5000",
            ], 120)
            for line in raw.splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = BASE58_IDENTIFIER.sub(
                    "[IDENTIFIER_REDACTED]",
                    redact(str(item.get("MESSAGE") or "")),
                )
                if re.search(
                    r'(?i)"?(signature|mint|wallet|pnl|roi|outcome|winner|loser|validation)"?\s*:',
                    message,
                ):
                    continue
                # Restrict the bundle to operational lifecycle/resource/error lines.
                if str(item.get("_PID") or "") != "1" and not SAFE_LOG.search(message):
                    continue
                rows.append({
                    "realtimeTimestampUs": int(item.get("__REALTIME_TIMESTAMP") or 0),
                    "utc": iso(dt.datetime.fromtimestamp(
                        int(item.get("__REALTIME_TIMESTAMP") or 0) / 1_000_000,
                        dt.timezone.utc,
                    )),
                    "unit": unit,
                    "priority": item.get("PRIORITY"),
                    "message": message[:2000],
                })
        rows.sort(key=lambda row: row["realtimeTimestampUs"])
        path = OUT / f"logs/operational-{days}d.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True) + "\n")


def readonly_connection(path: pathlib.Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def collect_sqlite_metadata() -> None:
    databases = {
        "decision-state": DECISIONS,
        "causal-state-plane": V3 / "causal-state-plane-v1.sqlite3",
    }
    for name, path in databases.items():
        connection = readonly_connection(path)
        schema = [
            {"type": row[0], "name": row[1], "table": row[2], "sql": row[3]}
            for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE type IN ('table','index') ORDER BY type,name"
            )
        ]
        pragmas = {}
        for pragma in ("page_size", "page_count", "freelist_count", "journal_mode", "synchronous", "cache_size"):
            pragmas[pragma] = connection.execute(f"PRAGMA {pragma}").fetchone()[0]
        tables = {}
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            table = row[0]
            tables[table] = {
                "columns": [dict(zip(
                    ("cid", "name", "type", "notnull", "default", "pk"), item
                )) for item in connection.execute(f"PRAGMA table_info('{table}')")],
                "indexes": [list(item) for item in connection.execute(f"PRAGMA index_list('{table}')")],
            }
        write_json(f"sqlite/{name}-metadata.json", {
            "path": str(path), "sizeBytes": path.stat().st_size,
            "pragmas": pragmas, "schema": schema, "tables": tables,
        })
        connection.close()

    connection = readonly_connection(DECISIONS)
    plans = {}
    queries = {
        "status_order": "SELECT signature FROM current_decisions WHERE status='ONLINE_ELIGIBLE' ORDER BY block_time_ms,slot,transaction_index,signature LIMIT 350",
        "signature_lookup": "SELECT generation_sha FROM current_decisions WHERE signature='pseudonym'",
        "partition_lookup": "SELECT generation_sha,exact_complete FROM partition_generations WHERE partition_hour='2026-09-16T22'",
        "versions_by_signature": "SELECT MIN(created_utc),MAX(created_utc),COUNT(*) FROM decision_versions WHERE signature='pseudonym'",
        "deferred_scan": "SELECT block_time_ms,updated_utc,reasons_json FROM current_decisions WHERE status='DEFERRED' ORDER BY block_time_ms",
    }
    for key, query in queries.items():
        plans[key] = [list(row) for row in connection.execute("EXPLAIN QUERY PLAN " + query)]
    write_json("sqlite/decision-query-plans.json", plans)
    connection.close()


def collect_decisions() -> None:
    connection = readonly_connection(DECISIONS)
    rows = connection.execute(
        "SELECT c.signature,c.block_time_ms,c.status,c.reasons_json,c.updated_utc,"
        "MIN(v.created_utc),MAX(v.created_utc),COUNT(v.version_sha) "
        "FROM current_decisions c LEFT JOIN decision_versions v ON v.signature=c.signature "
        "GROUP BY c.signature ORDER BY c.block_time_ms"
    ).fetchall()
    status_counts = collections.Counter()
    reason_counts = collections.Counter()
    ages_by_status: dict[str, list[float]] = collections.defaultdict(list)
    latency_first: list[float] = []
    latency_current: list[float] = []
    timeline_path = OUT / "pipeline/pseudonymous-decision-timeline.csv"
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    with timeline_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "observation_id", "acquisition_utc", "first_decision_utc",
            "current_decision_utc", "current_status", "reason_codes",
            "decision_versions", "acquisition_to_first_decision_s",
            "acquisition_to_current_decision_s", "age_at_snapshot_s",
        ])
        for signature, block_ms, status, reasons_json, updated, first_created, last_created, versions in rows:
            acquisition = dt.datetime.fromtimestamp(block_ms / 1000, dt.timezone.utc)
            reasons = sorted(json.loads(reasons_json))
            status_counts[status] += 1
            reason_counts.update(reasons)
            age = (NOW - acquisition).total_seconds()
            ages_by_status[status].append(age)
            first_latency = None
            current_latency = None
            if first_created:
                first_latency = (parse_utc(first_created) - acquisition).total_seconds()
                latency_first.append(first_latency)
            if updated:
                current_latency = (parse_utc(updated) - acquisition).total_seconds()
                latency_current.append(current_latency)
            writer.writerow([
                pseudonym(signature), iso(acquisition), first_created, updated, status,
                "|".join(reasons), versions, first_latency, current_latency, age,
            ])
    exact_pending = []
    for signature, block_ms, updated, reasons_json in connection.execute(
        "SELECT signature,block_time_ms,updated_utc,reasons_json FROM current_decisions "
        "WHERE status='DEFERRED' ORDER BY block_time_ms"
    ):
        reasons = json.loads(reasons_json)
        if "EXACT_RECONCILIATION_NOT_READY" not in reasons:
            continue
        acquisition = dt.datetime.fromtimestamp(block_ms / 1000, dt.timezone.utc)
        exact_pending.append({
            "observationId": pseudonym(signature),
            "acquisitionUtc": iso(acquisition),
            "lastDecisionUtc": updated,
            "ageAtSnapshotSeconds": (NOW - acquisition).total_seconds(),
        })
    write_json("pipeline/decision-summary.json", {
        "snapshotUtc": iso(NOW),
        "currentDecisionN": len(rows),
        "statusCounts": dict(sorted(status_counts.items())),
        "reasonCounts": dict(sorted(reason_counts.items())),
        "ageSecondsByStatus": {
            key: distribution(values) for key, values in sorted(ages_by_status.items())
        },
        "acquisitionToFirstDecisionSeconds": distribution(latency_first),
        "acquisitionToCurrentDecisionSeconds": distribution(latency_current),
        "exactReconciliationNotReadyN": len(exact_pending),
        "outcomesRead": 0,
        "validationRowsIncluded": 0,
    })
    write_json("pipeline/exact-not-ready-pseudonymous.json", exact_pending)
    connection.close()


def collect_partitions() -> None:
    records = []
    for path in sorted((EXACT / "partitions").glob("*/*/*/*/reconciliation-status.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        key = str(row.get("partitionKey") or "")
        try:
            closed = dt.datetime.strptime(key, "%Y/%m/%d/%H").replace(tzinfo=dt.timezone.utc) + dt.timedelta(hours=1)
            reconciled = parse_utc(row["reconciledAtUtc"])
            lag = (reconciled - closed).total_seconds()
        except (ValueError, KeyError, TypeError):
            closed, reconciled, lag = None, None, None
        coverage = row.get("coverage") or {}
        evidence = row.get("inputEvidence") or []
        records.append({
            "partitionHour": key,
            "hourClosedUtc": iso(closed) if closed else None,
            "reconciledAtUtc": iso(reconciled) if reconciled else None,
            "closeToReconciledSeconds": lag,
            "exactReplayComplete": bool(row.get("exactReplayComplete")),
            "temporalHourClosed": bool(row.get("temporalHourClosed")),
            "inputStableDuringRun": row.get("inputStableDuringRun"),
            "inputEvidenceBytes": sum(int(item.get("size") or 0) for item in evidence),
            "inputPartitionCount": len(evidence),
            "joinedEvidenceIdCount": row.get("joinedEvidenceIdCount"),
            "relevantTransactionsObserved": coverage.get("relevantTransactionsObserved"),
            "finalized": coverage.get("finalized"),
            "successful": coverage.get("successful"),
            "failed": coverage.get("failed"),
            "unresolved": coverage.get("unresolved"),
            "decodeFailures": coverage.get("decodeFailures"),
            "missingFinality": coverage.get("missingFinality"),
            "missingRawTx": coverage.get("missingRawTx"),
            "missingCanonicalOrdering": coverage.get("missingCanonicalOrdering"),
        })
    recent = [row for row in records if row["hourClosedUtc"] and parse_utc(row["hourClosedUtc"]) >= NOW - dt.timedelta(days=7)]
    path = OUT / "pipeline/reconciliation-partitions-7d.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(records[0].keys()) if records else []
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(recent)
    lags = [float(row["closeToReconciledSeconds"]) for row in recent if row["closeToReconciledSeconds"] is not None]
    write_json("pipeline/reconciliation-summary.json", {
        "snapshotUtc": iso(NOW),
        "partitionsInSevenDays": len(recent),
        "complete": sum(row["exactReplayComplete"] for row in recent),
        "incomplete": sum(not row["exactReplayComplete"] for row in recent),
        "closeToReconciledSeconds": distribution(lags),
        "totalInputEvidenceBytes": sum(row["inputEvidenceBytes"] for row in recent),
        "totalRelevantTransactionsObserved": sum(int(row["relevantTransactionsObserved"] or 0) for row in recent),
        "totalUnresolved": sum(int(row["unresolved"] or 0) for row in recent),
    })


def collect_current_status() -> None:
    source = V3 / "status.json"
    status = json.loads(source.read_text(encoding="utf-8"))
    allowed = [
        "schema", "implementationVersion", "atUtc", "runStartedAtUtc", "runDurationMs",
        "canonicalIndependentEntryEpisodes", "nonPausedIndependentEpisodes",
        "activeCollectionHours", "rawNonPausedEpisodesPerHour", "onlineEligibleN",
        "statusCounts", "exclusionsByReason", "processedExactGenerationThisRun",
        "skippedCachedGenerationThisRun", "readyWorkN", "readyWorkTotalBeforeHourBatch",
        "readyWorkDeferredByHourBatch", "causalUniverseScanRequiredThisRun",
        "causalUniverseCacheUsedThisRun", "causalStatePlaneQueriedThisRun",
        "evidencePathCountThisRun", "evidenceBytesIndexedThisRun", "evidenceScannedThisRun",
        "captureAllFinalizedJoinThisRun", "yellowstoneExactSignatureJoinThisRun",
        "causalCacheHitThisRun", "decisionVersionsN", "currentDecisionsN",
        "supersededDecisionVersionsN", "reconciliationFrontier", "materializationMode",
        "rawCollectionActive", "outcomesRead", "submissions",
    ]
    sanitized = {key: status.get(key) for key in allowed if key in status}
    # Signature lists are unnecessary for throughput analysis.
    for key in ("captureAllFinalizedJoinThisRun", "yellowstoneExactSignatureJoinThisRun"):
        if isinstance(sanitized.get(key), dict):
            sanitized[key].pop("attemptedSignatures", None)
    sanitized["validationRowsIncluded"] = 0
    write_json("pipeline/current-materializer-status-sanitized.json", sanitized)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    collect_system()
    collect_units()
    collect_sqlite_metadata()
    collect_decisions()
    collect_partitions()
    collect_current_status()
    write_json("MANIFEST.json", {
        "schema": "EDGESNIFFER_READONLY_RUNTIME_AUDIT_BUNDLE_V1",
        "createdAtUtc": iso(NOW),
        "host": "sanitized-edgesniffer-vps",
        "readOnlyCollection": True,
        "outcomesRead": 0,
        "pnlIncluded": False,
        "strategyLabelsIncluded": False,
        "validationRowsIncluded": 0,
        "identifiers": "SHA256 pseudonyms truncated to 16 hex characters",
    })
    write_text("UNAVAILABLE.md", """
# Evidence unavailable

- No continuously sampled historical CPU, RSS, swap, disk-latency, or I/O-wait
  time series exists for the full requested seven-day period. The bundle
  contains current snapshots and systemd lifecycle/resource accounting where
  retained by journald.
- The services do not persist a dedicated per-run structured timing ledger for
  every historical reconciler/materializer invocation. Safe systemd lifecycle
  and resource messages are included instead.
- The current database does not persist separate timestamps for every internal
  materializer substage. Acquisition, first decision-version creation, current
  decision update, partition close, and reconciliation completion are available.
- Exact lock-acquisition wait duration is not persisted as a structured metric.
  Timer schedules, service starts/finishes, and current locking configuration
  are included so contention can be bounded but not reconstructed perfectly.
- No transaction payloads, signatures, mints, wallet addresses, PnL, outcomes,
  winner/loss labels, sealed validation rows, or strategy features were exported.
""")


if __name__ == "__main__":
    main()
