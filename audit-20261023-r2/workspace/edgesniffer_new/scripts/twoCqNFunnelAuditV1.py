#!/usr/bin/env python3
"""Read-only route/admission funnel audit for the frozen 2CQ cohort."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sqlite3
from typing import Any


SCHEMA = "2CQ_N_FUNNEL_AUDIT_V1"
OFFICIAL = {"SOL_PUMP_CURVE", "SOL_PUMP_CPI"}


def connect(path: pathlib.Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def json_value(value: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def json_object(value: Any) -> dict[str, Any]:
    parsed = json_value(value)
    return parsed if isinstance(parsed, dict) else {}


def key_paths(value: Any, prefix: str = "") -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.add(path)
            result.update(key_paths(child, path))
    elif isinstance(value, list):
        for child in value[:5]:
            result.update(key_paths(child, prefix + "[]"))
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    source = connect(args.source_state)
    rows = list(source.execute(
        "SELECT signature,mint,route,economic_family,status,reasons_json,detail_json,"
        "choice_json,forensic_json,updated_utc FROM current_decisions "
        "ORDER BY block_time_ms,slot,transaction_index,signature"
    ))
    source.close()

    route_rows: dict[str, sqlite3.Row] = {}
    sibling_rows: dict[str, sqlite3.Row] = {}
    if args.extended_route_state:
        route = connect(args.extended_route_state)
        route_rows = {
            str(row["signature"]): row for row in route.execute(
                "SELECT signature,classification,resolved_venue,exact_json,error "
                "FROM route_evidence"
            )
        }
        route.close()
    if args.sibling_state:
        sibling = connect(args.sibling_state)
        sibling_rows = {
            str(row["signature"]): row for row in sibling.execute(
                "SELECT signature,lane,status,reasons_json,choice_json FROM decisions"
            )
        }
        sibling.close()

    family_status: collections.Counter[str] = collections.Counter()
    family_reason: collections.Counter[str] = collections.Counter()
    official_reason: collections.Counter[str] = collections.Counter()
    official_status: collections.Counter[str] = collections.Counter()
    official_route: collections.Counter[str] = collections.Counter()
    official_exact: collections.Counter[str] = collections.Counter()
    official_sibling: collections.Counter[str] = collections.Counter()
    detail_keys: collections.Counter[str] = collections.Counter()
    forensic_keys: collections.Counter[str] = collections.Counter()
    unresolved_rows: list[dict[str, Any]] = []

    for row in rows:
        family = str(row["economic_family"])
        status = str(row["status"])
        reason_list_value = json_value(row["reasons_json"])
        reason_list = reason_list_value if isinstance(reason_list_value, list) else []
        family_status[f"{family}|{status}"] += 1
        for reason in reason_list:
            family_reason[f"{family}|{reason}"] += 1
        detail = json_object(row["detail_json"])
        forensic = json_object(row["forensic_json"])
        detail_keys.update(key_paths(detail))
        forensic_keys.update(key_paths(forensic))
        if family not in OFFICIAL:
            continue
        official_status[status] += 1
        for reason in reason_list:
            official_reason[reason] += 1
        route = route_rows.get(str(row["signature"]))
        if route is None:
            official_route["NO_ROUTE_EVIDENCE_ROW"] += 1
        else:
            official_route[str(route["resolved_venue"] or "UNRESOLVED")] += 1
            official_exact[str(route["classification"])] += 1
        sibling = sibling_rows.get(str(row["signature"]))
        if sibling is None:
            official_sibling["NO_SIBLING_ROW"] += 1
        else:
            official_sibling[f"{sibling['lane']}|{sibling['status']}"] += 1
        if status == "DEFERRED" or any("NOT_READY" in str(reason) for reason in reason_list):
            unresolved_rows.append({
                "signature": str(row["signature"]),
                "mint": str(row["mint"]),
                "family": family,
                "status": status,
                "reasons": reason_list,
                "routeEvidence": None if route is None else {
                    "classification": str(route["classification"]),
                    "resolvedVenue": route["resolved_venue"],
                    "error": route["error"],
                },
                "sibling": None if sibling is None else {
                    "lane": str(sibling["lane"]),
                    "status": str(sibling["status"]),
                    "reasons": json_value(sibling["reasons_json"]),
                },
            })

    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "sourceRowsN": len(rows),
        "officialRowsN": sum(official_status.values()),
        "officialStatusCounts": dict(sorted(official_status.items())),
        "officialReasonCounts": dict(sorted(official_reason.items())),
        "officialResolvedVenueCounts": dict(sorted(official_route.items())),
        "officialExactEvidenceCounts": dict(sorted(official_exact.items())),
        "officialSiblingDecisionCounts": dict(sorted(official_sibling.items())),
        "allFamilyStatusCounts": dict(sorted(family_status.items())),
        "allFamilyReasonCounts": dict(sorted(family_reason.items())),
        "unresolvedOfficialN": len(unresolved_rows),
        "unresolvedOfficialRows": unresolved_rows,
        "storedDetailKeyPaths": [key for key, _ in detail_keys.most_common()],
        "storedForensicKeyPaths": [key for key, _ in forensic_keys.most_common()],
        "methodology": {
            "readOnly": True,
            "outcomesRead": 0,
            "validationRowsRead": 0,
            "submissions": 0,
            "officialCohortModified": False,
            "unknownStagesAreNotInferred": True,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-state", type=pathlib.Path, required=True)
    parser.add_argument("--extended-route-state", type=pathlib.Path)
    parser.add_argument("--sibling-state", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
