#!/usr/bin/env python3
"""Outcome-blind classification of deferred 2CQ symmetric observations."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
from typing import Any


SCHEMA = "2CQ_DEFERRED_OBSERVATION_CLASSIFICATION_V1"
BASELINE_DEFERRED_N = 264


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def partition_state(root: pathlib.Path, hour: str, now: dt.datetime) -> dict[str, Any]:
    directory = root / "partitions" / hour
    manifest_path = directory / "manifest.json"
    status_path = directory / "reconciliation-status.json"
    final_path = directory / "reconciliation-manifest.json"
    manifest = read_json(manifest_path)
    status = read_json(status_path)
    final = read_json(final_path)
    coverage = (status or {}).get("coverage") or {}
    evidence_paths = []
    missing_evidence_paths = []
    for item in (status or {}).get("inputEvidence") or []:
        relative = str(item.get("path") or "")
        if not relative:
            continue
        evidence_paths.append(relative)
        if not (root / relative).exists():
            missing_evidence_paths.append(relative)
    exact_complete = bool(
        status
        and status.get("temporalHourClosed") is True
        and status.get("inputStableDuringRun") is True
        and status.get("exactReplayComplete") is True
    )
    try:
        hour_dt = dt.datetime.strptime(hour, "%Y/%m/%d/%H").replace(tzinfo=dt.timezone.utc)
        age_hours = (now - hour_dt).total_seconds() / 3600
    except ValueError:
        age_hours = None
    return {
        "hour": hour,
        "ageHours": age_hours,
        "manifestPresent": manifest is not None,
        "temporalHourClosed": (manifest or {}).get("temporalHourClosed", (manifest or {}).get("completeUtcHour")),
        "statusPresent": status is not None,
        "finalManifestPresent": final is not None,
        "exactReplayComplete": exact_complete,
        "reconciliationRetryExhausted": bool((status or {}).get("reconciliationRetryExhausted")),
        "inputStableDuringRun": (status or {}).get("inputStableDuringRun"),
        "reconciliationVersion": (status or {}).get("reconciliationVersion"),
        "reconciledAtUtc": (status or {}).get("reconciledAtUtc"),
        "coverage": {
            key: int(coverage.get(key) or 0)
            for key in (
                "relevantTransactionsObserved", "unresolved", "missingFinality",
                "missingRawTx", "missingTradeEvent", "missingReserveTransition",
                "missingCanonicalOrdering", "missingFeeEvidence", "decodeFailures",
                "successFailureMismatch",
            )
        },
        "evidencePathN": len(evidence_paths),
        "missingEvidencePaths": missing_evidence_paths,
    }


def classify(hours: list[dict[str, Any]]) -> tuple[str, str]:
    if hours and all(row["exactReplayComplete"] and not row["missingEvidencePaths"] for row in hours):
        return "RECOVERABLE_READY_FOR_REMATERIALIZATION", "All required exact partitions are complete now."
    if any(row["missingEvidencePaths"] for row in hours):
        return "UNKNOWN_EVIDENCE_PATH_MISSING", "A recorded immutable evidence path is absent."
    if any(not row["manifestPresent"] for row in hours):
        return "UNKNOWN_PARTITION_MANIFEST_MISSING", "At least one required receive-hour manifest is absent."
    if any(not row["statusPresent"] for row in hours):
        return "WAITING_RECONCILIATION_STATUS", "A closed receive hour has no reconciliation status yet."
    incomplete = [row for row in hours if not row["exactReplayComplete"]]
    if not incomplete:
        return "UNKNOWN", "No exact incompleteness was identified."
    freshest = min((row["ageHours"] for row in incomplete if row["ageHours"] is not None), default=None)
    if freshest is not None and freshest <= 5:
        return "FRESH_WAITING_SUPPORT_OR_FINALITY", "A required partition is still inside the three-hour support horizon plus scheduling margin."
    structural_keys = (
        "missingRawTx", "missingTradeEvent", "missingReserveTransition",
        "missingCanonicalOrdering", "missingFeeEvidence", "decodeFailures",
        "successFailureMismatch",
    )
    if any(
        any(row["coverage"][key] for key in structural_keys)
        for row in incomplete
    ):
        return (
            "TERMINAL_STRUCTURAL_EXACT_EVIDENCE_INCOMPLETE",
            "An old required partition has irreversible raw/decode/canonical evidence loss; retry cannot make this observation exact.",
        )
    if any(row["reconciliationRetryExhausted"] for row in incomplete):
        return (
            "TERMINAL_EXACT_RECONCILIATION_EXHAUSTED",
            "A bounded canonical/finality/archive recovery pass completed without enough exact evidence; unchanged input will not be retried.",
        )
    unresolved = sum(row["coverage"]["unresolved"] for row in incomplete)
    if unresolved:
        return "STALE_RECONCILIATION_INCOMPLETE_RETRYABLE", "Old exact partitions remain incomplete because canonical finality is unresolved."
    return "STALE_RECONCILIATION_INCOMPLETE_OTHER", "Old exact partitions remain incomplete for a non-finality exactness condition."


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-db", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    generated = now_utc()
    now = parse_utc(generated)
    connection = sqlite3.connect(f"file:{args.state_db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    decisions = connection.execute(
        """SELECT signature,mint,route,economic_family,block_time_ms,slot,
                  transaction_index,generation_sha,reasons_json,detail_json,updated_utc
             FROM current_decisions WHERE status='DEFERRED'
             ORDER BY block_time_ms,slot,transaction_index,signature"""
    ).fetchall()
    first_deferred = {
        row[0]: row[1] for row in connection.execute(
            """SELECT signature,MIN(created_utc) FROM decision_versions
                 WHERE status='DEFERRED' GROUP BY signature"""
        )
    }
    deferred_versions = {
        row[0]: int(row[1]) for row in connection.execute(
            """SELECT signature,COUNT(*) FROM decision_versions
                 WHERE status='DEFERRED' GROUP BY signature"""
        )
    }
    rows = []
    category_counts: collections.Counter[str] = collections.Counter()
    hour_counts: collections.Counter[str] = collections.Counter()
    for index, decision in enumerate(decisions):
        detail = json.loads(decision["detail_json"])
        required = sorted({str(item.get("hour")) for item in detail.get("hours") or [] if item.get("hour")})
        states = [partition_state(args.exact_root, hour, now) for hour in required]
        category, explanation = classify(states)
        category_counts[category] += 1
        hour_counts.update(required)
        first = first_deferred.get(decision["signature"])
        rows.append({
            "signature": decision["signature"],
            "mint": decision["mint"],
            "route": decision["route"],
            "economicFamily": decision["economic_family"],
            "acquisitionUtc": dt.datetime.fromtimestamp(
                decision["block_time_ms"] / 1000, dt.timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "ageHours": (now.timestamp() * 1000 - decision["block_time_ms"]) / 3_600_000,
            "firstDeferredUtc": first,
            "latestRetryUtc": decision["updated_utc"],
            "deferredVersionN": deferred_versions.get(decision["signature"], 0),
            "generationSha256": decision["generation_sha"],
            "auditBaselineMembership": "BASELINE_OLDEST_264" if index < BASELINE_DEFERRED_N else "POST_AUDIT_ADDITION",
            "classification": category,
            "explanation": explanation,
            "requiredPartitions": states,
        })
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": generated,
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "stateDb": str(args.state_db),
        "stateDbSha256": sha256_file(args.state_db),
        "currentDeferredN": len(rows),
        "auditorFrozenDeferredN": BASELINE_DEFERRED_N,
        "postAuditDeferredDeltaN": max(0, len(rows) - BASELINE_DEFERRED_N),
        "baselineMembershipCaveat": "The audit bundle did not preserve row identities; the oldest 264 current deferred rows are an explicit approximation of the frozen baseline.",
        "classificationCounts": dict(sorted(category_counts.items())),
        "requiredPartitionCounts": dict(sorted(hour_counts.items())),
        "rows": rows,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = [
        "# 2CQ Deferred Observation Classification V1",
        "",
        f"Generated: `{generated}`",
        f"Current deferred: **{len(rows)}**",
        f"Auditor baseline: **{BASELINE_DEFERRED_N}**",
        "",
        "This report is outcome-blind and opens the materializer state database read-only.",
        "",
        "## Classification",
        "",
        "| Classification | N |",
        "|---|---:|",
    ]
    summary.extend(f"| {key} | {value} |" for key, value in sorted(category_counts.items()))
    summary.extend(["", "See `report.json` for the de-duplicated entry-level table.", ""])
    (args.output / "REPORT.md").write_text("\n".join(summary), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "schema", "generatedAtUtc", "currentDeferredN", "auditorFrozenDeferredN",
        "postAuditDeferredDeltaN", "classificationCounts",
    )}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
