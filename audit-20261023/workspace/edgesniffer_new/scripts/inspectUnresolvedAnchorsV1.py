#!/usr/bin/env python3
"""Read-only summary of unresolved causal anchor rows."""
from __future__ import annotations

import collections
import json
import sys


path = sys.argv[1]
counts: collections.Counter[str] = collections.Counter()
families: collections.Counter[str] = collections.Counter()
examples = []
rows = 0
for line in open(path, encoding="utf-8"):
    if not line.strip():
        continue
    row = json.loads(line)
    if row.get("role") != "ANCHOR":
        continue
    route = row.get("route") or {}
    if route.get("repaired_route_certainty") != "UNRESOLVED_PROGRAM":
        continue
    rows += 1
    for program in route.get("unknown_programs") or []:
        counts[str(program)] += 1
    families[str(row.get("program_family") or "")] += 1
    if len(examples) < 12:
        examples.append({
            "signature": row.get("signature"),
            "slot": row.get("slot"),
            "mint": row.get("mint"),
            "side": row.get("side"),
            "program_family": row.get("program_family"),
            "unknown_programs": route.get("unknown_programs"),
            "top_level_programs": route.get("top_level_programs"),
            "inner_programs": route.get("inner_programs"),
            "anchor_keys": sorted(row.keys()),
        })
print(json.dumps({"rows": rows, "unknown_programs": dict(counts), "program_families": dict(families), "examples": examples}, indent=2, sort_keys=True))
