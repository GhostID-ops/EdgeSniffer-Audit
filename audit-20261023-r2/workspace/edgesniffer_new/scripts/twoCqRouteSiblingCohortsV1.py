#!/usr/bin/env python3
"""Publish separate outcome-blind 2CQ route sibling cohorts.

The route materializer remains the authority.  This publisher only fans its
already-materialized decisions into explicit route populations plus a broad
Tier-B behavioral ledger.  It never reads outcomes and never changes the
official SOL-Pump cohort.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
from typing import Any


SCHEMA = "2CQ_ROUTE_SIBLING_COHORTS_V1"
LANES = {
    "LAUNCHLAB_ACTIVE_5000MS_SAME_SPEND_ASSET": "launchlab",
    "PUMPSWAP_ACTIVE_5000MS_SAME_SPEND_ASSET": "pumpswap",
    "NON_SOL_PUMP_V2_LAUNCHED_120S_SAME_QUOTE": "non_sol_pump_v2",
}


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def row_payload(row: sqlite3.Row, include_choice: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": SCHEMA + "_ROW",
        "signature": str(row["signature"]),
        "mint": str(row["mint"]),
        "lane": str(row["lane"]),
        "spentMint": str(row["spent_mint"]),
        "boundaryUnixNs": int(row["boundary_ns"]),
        "slot": int(row["slot"]),
        "transactionIndex": int(row["tx_index"]),
        "status": str(row["status"]),
        "reasons": json.loads(str(row["reasons_json"])),
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "submissions": 0,
    }
    if include_choice and row["choice_json"]:
        payload["choice"] = json.loads(str(row["choice_json"]))
    return payload


def write_cohort(output: pathlib.Path, name: str, rows: list[sqlite3.Row], source_sha: str) -> dict[str, Any]:
    destination = output / name
    destination.mkdir(parents=True, exist_ok=True)
    row_path = destination / "rows.jsonl"
    temp = row_path.with_suffix(".jsonl.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row_payload(row, str(row["status"]) == "ELIGIBLE"), sort_keys=True) + "\n")
    os.replace(temp, row_path)
    counts = collections.Counter(str(row["status"]) for row in rows)
    status = {
        "schema": SCHEMA + "_STATUS",
        "generatedAtUtc": now_utc(),
        "cohort": name,
        "rowsN": len(rows),
        "uniqueMintsN": len({str(row["mint"]) for row in rows}),
        "eligibleN": counts.get("ELIGIBLE", 0),
        "deferredN": counts.get("DEFERRED", 0),
        "excludedN": counts.get("EXCLUDED", 0),
        "statusCounts": dict(sorted(counts.items())),
        "outcomeBlind": True,
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "submissions": 0,
        "officialSolPumpCohortModified": False,
        "sourceRouteStateSha256": source_sha,
        "rowsSha256": sha256_file(row_path),
    }
    atomic_json(destination / "status.json", status)
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route-state", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    source_sha = sha256_file(args.route_state)
    conn = sqlite3.connect(f"file:{args.route_state}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute("SELECT * FROM decisions ORDER BY slot,tx_index,signature"))
    conn.close()
    route_reports: dict[str, Any] = {}
    for lane, name in LANES.items():
        route_reports[name] = write_cohort(
            args.output, name, [row for row in rows if str(row["lane"]) == lane], source_sha
        )
    broad = write_cohort(args.output, "broad_behavioral_tier_b", rows, source_sha)
    report = {
        "schema": SCHEMA + "_STATUS",
        "generatedAtUtc": now_utc(),
        "sourceRouteStateSha256": source_sha,
        "routeCohorts": route_reports,
        "broadBehavioralTierB": broad,
        "outcomeBlind": True,
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "submissions": 0,
        "officialSolPumpCohortModified": False,
    }
    atomic_json(args.output / "status.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
