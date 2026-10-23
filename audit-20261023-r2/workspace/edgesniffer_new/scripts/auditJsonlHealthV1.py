#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from collections import Counter
from pathlib import Path

for value in sys.argv[1:]:
    path = Path(value)
    valid = malformed = 0
    examples = []
    record_types = Counter()
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict) and row.get("record_type"):
                    record_types[str(row["record_type"])] += 1
                valid += 1
            except Exception as error:
                malformed += 1
                examples.append({"line": number, "bytes": len(line.encode()), "error": type(error).__name__})
                examples = examples[-3:]
    print(json.dumps({"path": str(path), "valid": valid, "malformed": malformed, "record_types": dict(record_types), "examples": examples}, sort_keys=True))
