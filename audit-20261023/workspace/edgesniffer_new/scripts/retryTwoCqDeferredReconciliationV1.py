#!/usr/bin/env python3
"""Retry one old exact partition still blocking outcome-blind 2CQ decisions."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sqlite3
import subprocess


CURRENT_RECONCILIATION_VERSION = "PUMP_EXACT_EVIDENCE_LEDGER_V1_RECONCILIATION_V11_CANONICAL_FORK_SLOTS"


def read_status(root: pathlib.Path, hour: str) -> dict:
    path = root / "partitions" / hour / "reconciliation-status.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def exact(status: dict) -> bool:
    return bool(
        status.get("temporalHourClosed") is True
        and status.get("inputStableDuringRun") is True
        and status.get("exactReplayComplete") is True
    )


def retryable(status: dict) -> bool:
    if not status:
        return True
    if status.get("reconciliationVersion") == CURRENT_RECONCILIATION_VERSION:
        return False
    coverage = status.get("coverage") or {}
    structural_keys = (
        "missingRawTx", "missingTradeEvent", "missingReserveTransition",
        "missingCanonicalOrdering", "missingFeeEvidence", "decodeFailures",
        "successFailureMismatch",
    )
    return not any(int(coverage.get(key) or 0) for key in structural_keys)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-db", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--reconciler", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--telemetry-jsonl", type=pathlib.Path, required=True)
    parser.add_argument("--dispatch-stamp", type=pathlib.Path)
    parser.add_argument("--minimum-age-hours", type=float, default=5.0)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.state_db}?mode=ro", uri=True)
    blocked: collections.Counter[str] = collections.Counter()
    for (detail_json,) in connection.execute(
        "SELECT detail_json FROM current_decisions WHERE status='DEFERRED'"
    ):
        detail = json.loads(detail_json)
        for item in detail.get("hours") or []:
            hour = item.get("hour")
            if hour:
                blocked[str(hour)] += 1

    now = dt.datetime.now(dt.timezone.utc)
    eligible = []
    for hour, affected in blocked.items():
        try:
            hour_dt = dt.datetime.strptime(hour, "%Y/%m/%d/%H").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        age_hours = (now - hour_dt).total_seconds() / 3600
        status = read_status(args.exact_root, hour)
        if age_hours < args.minimum_age_hours or exact(status) or not retryable(status):
            continue
        manifest = args.exact_root / "partitions" / hour / "manifest.json"
        if not manifest.exists():
            continue
        eligible.append((-affected, hour_dt, hour, affected))

    if not eligible:
        print(json.dumps({
            "schema": "2CQ_DEFERRED_RECONCILIATION_RETRY_V1",
            "atUtc": now.isoformat().replace("+00:00", "Z"),
            "selectedPartition": None,
            "blockedPartitionN": len(blocked),
        }, sort_keys=True))
        return 0

    _, _, hour, affected = sorted(eligible)[0]
    command = [
        "/usr/bin/python3", str(args.reconciler), str(args.exact_root),
        "--grace-seconds", "90", "--env", str(args.env),
        "--partition", hour, "--max-partitions", "1",
        "--telemetry-jsonl", str(args.telemetry_jsonl),
    ]
    if args.dispatch_stamp:
        command.extend(["--dispatch-stamp", str(args.dispatch_stamp)])
    print(json.dumps({
        "schema": "2CQ_DEFERRED_RECONCILIATION_RETRY_V1",
        "atUtc": now.isoformat().replace("+00:00", "Z"),
        "selectedPartition": hour,
        "affectedCurrentDeferredDecisions": affected,
        "eligibleStalePartitionN": len(eligible),
    }, sort_keys=True), flush=True)
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
