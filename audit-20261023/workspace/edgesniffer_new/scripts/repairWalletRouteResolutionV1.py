#!/usr/bin/env python3
"""Repair route labels without inventing an unknown venue identity.

Existing exact single-venue evidence is promoted to an explicit repaired label.
Multi-venue rows become COMPOSITE_ROUTE. Rows with only unknown programs remain
UNRESOLVED_PROGRAM and carry the exact IDs requiring an adapter.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any


SCHEMA = "WALLET_PROSPECTIVE_CAUSAL_TAPE_REPAIRED_V1"


def repair_route(route: dict[str, Any]) -> tuple[dict[str, Any], str]:
    venues = route.get("underlying_venues") or []
    if len(venues) == 1:
        venue = venues[0]
        label = f"{venue.get('venue')}:{venue.get('invocation')}"
        return route | {
            "repaired": True,
            "repaired_route": label,
            "repaired_route_certainty": "EXACT_SINGLE_VENUE",
        }, "EXACT_SINGLE_VENUE"
    if len(venues) > 1:
        labels = sorted(f"{row.get('venue')}:{row.get('invocation')}" for row in venues)
        return route | {
            "repaired": True,
            "repaired_route": "COMPOSITE_ROUTE:" + "+".join(labels),
            "repaired_route_certainty": "COMPOSITE_ROUTE",
        }, "COMPOSITE_ROUTE"
    unknown = sorted(str(row) for row in route.get("unknown_programs") or [])
    return route | {
        "repaired": False,
        "repaired_route": "UNRESOLVED_PROGRAM:" + "+".join(unknown),
        "repaired_route_certainty": "UNRESOLVED_PROGRAM",
    }, "UNRESOLVED_PROGRAM"


def repair(input_path: Path, output_path: Path, report_path: Path) -> dict[str, Any]:
    counts: collections.Counter[str] = collections.Counter()
    rows = 0
    repaired_rows = 0
    with input_path.open(encoding="utf-8") as source, output_path.open("w", encoding="utf-8") as target:
        for line in source:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("role") == "ANCHOR":
                repaired, category = repair_route(row.get("route") or {})
                row["route"] = repaired
                row["route_repair_schema"] = SCHEMA
                row["route_repair_category"] = category
                counts[category] += 1
                repaired_rows += int(category != "UNRESOLVED_PROGRAM")
            target.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            rows += 1
    report = {
        "schema": SCHEMA + "_REPORT",
        "input": str(input_path),
        "output": str(output_path),
        "records": rows,
        "anchor_categories": dict(counts),
        "anchors_reclassified_without_false_venue_claim": repaired_rows,
        "economics_exact_anchor_eligible": counts["EXACT_SINGLE_VENUE"],
        "unresolved_program_anchors": counts["UNRESOLVED_PROGRAM"],
        "composite_anchors": counts["COMPOSITE_ROUTE"],
        "promotion_ready": False,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    print(json.dumps(repair(args.input, args.output, args.report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
