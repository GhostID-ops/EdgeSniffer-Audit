#!/usr/bin/env python3
"""Fail-closed audit for the new-wallet causal discovery snapshot."""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any


def audit(path: Path) -> dict[str, Any]:
    anchors: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []
    malformed = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if row.get("schema") != "WALLET_PROSPECTIVE_CAUSAL_TAPE_V1":
                malformed += 1
            elif row.get("role") == "ANCHOR":
                anchors.append(row)
            elif row.get("role") == "CONTROL_SAME_SLOT":
                controls.append(row)
            else:
                malformed += 1

    anchor_sigs = [str(row.get("anchor_signature") or "") for row in anchors]
    route_counts: collections.Counter[str] = collections.Counter()
    unresolved_reasons: collections.Counter[str] = collections.Counter()
    unknown_program_counts: collections.Counter[str] = collections.Counter()
    unresolved = 0
    for row in anchors:
        route = row.get("route") or {}
        name = str(route.get("route_resolution") or "UNRESOLVED")
        route_counts[name] += 1
        unresolved += int(not route.get("resolved"))
        if not route.get("resolved"):
            venues = route.get("underlying_venues") or []
            if len(venues) == 0:
                unresolved_reasons["NO_KNOWN_VENUE"] += 1
            elif len(venues) > 1:
                unresolved_reasons["MULTIPLE_VENUES"] += 1
            if route.get("unknown_programs"):
                unresolved_reasons["UNKNOWN_PROGRAM_IDS"] += 1
                unknown_program_counts.update(str(item) for item in route.get("unknown_programs") or [])
            if not route.get("top_level_programs") and not route.get("inner_programs"):
                unresolved_reasons["NO_INVOKED_PROGRAMS"] += 1

    control_by_anchor: collections.Counter[str] = collections.Counter(
        str(row.get("anchor_signature") or "") for row in controls
    )
    control_success = sum(1 for row in controls if row.get("successful") is True)
    anchor_slots = {str(row.get("anchor_signature")): int(row.get("anchor_slot") or -1) for row in anchors}
    same_slot_controls = sum(
        1 for row in controls
        if int(row.get("slot") or -2) == anchor_slots.get(str(row.get("anchor_signature")), -1)
    )

    # This tape deliberately contains contemporaneous controls only.  A row
    # with a later timestamp would be an exit/mark candidate, but this schema
    # has no post-entry market-window rows yet; report that explicitly.
    post_entry_mark_rows = 0
    return {
        "schema": "WALLET_PROSPECTIVE_CAUSAL_TAPE_AUDIT_V1",
        "input": str(path),
        "records": len(anchors) + len(controls),
        "anchors": len(anchors),
        "unique_anchor_signatures": len(set(anchor_sigs)),
        "controls": len(controls),
        "successful_controls": control_success,
        "same_slot_controls": same_slot_controls,
        "anchors_with_controls": sum(1 for sig in set(anchor_sigs) if control_by_anchor[sig] > 0),
        "route_counts": dict(route_counts),
        "unresolved_reasons": dict(unresolved_reasons),
        "unknown_program_counts": dict(unknown_program_counts.most_common(30)),
        "unresolved_anchor_routes": unresolved,
        "malformed_records": malformed,
        "post_entry_mark_rows": post_entry_mark_rows,
        "economics_ready": bool(
            anchors
            and len(set(anchor_sigs)) == len(anchor_sigs)
            and malformed == 0
            and unresolved == 0
            and post_entry_mark_rows > 0
        ),
        "blocking_reasons": [
            reason for reason, condition in [
                ("NO_ANCHORS", not anchors),
                ("DUPLICATE_ANCHOR_SIGNATURES", len(set(anchor_sigs)) != len(anchor_sigs)),
                ("MALFORMED_RECORDS", malformed > 0),
                ("UNRESOLVED_ANCHOR_ROUTES", unresolved > 0),
                ("NO_POST_ENTRY_MARKS_IN_TAPE", post_entry_mark_rows == 0),
            ] if condition
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.input)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
