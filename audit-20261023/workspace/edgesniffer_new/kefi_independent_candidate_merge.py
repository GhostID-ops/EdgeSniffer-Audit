#!/usr/bin/env python3
"""Merge/validate independent kEFi candidate shards without opening outcomes.

The shard files are the authoritative extraction outputs. This script only
audits their inventories and identities; it does not score candidates or
choose a fingerprint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("shard_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--expected", nargs="+", required=True)
    args = parser.parse_args()

    shards: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_rows = 0
    duplicate_rows = 0
    malformed_rows = 0
    missing_shards: list[str] = []
    for name in args.expected:
        path = args.shard_dir / f"{name}.jsonl"
        if not path.exists():
            missing_shards.append(name)
            continue
        rows = 0
        local_duplicates = 0
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    malformed_rows += 1
                    continue
                if not isinstance(row, dict) or not row.get("candidate_signature"):
                    malformed_rows += 1
                    continue
                key = f"{row['candidate_signature']}:{row.get('event_index', '')}"
                if key in seen:
                    duplicate_rows += 1
                    local_duplicates += 1
                else:
                    seen.add(key)
                rows += 1
        total_rows += rows
        shards.append(
            {
                "name": name,
                "path": str(path),
                "rows": rows,
                "duplicate_rows": local_duplicates,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    status = "PASS" if not missing_shards and not duplicate_rows and not malformed_rows else "FAIL"
    report = {
        "schema": "KEFI_INDEPENDENT_CANDIDATE_UNIVERSE_MANIFEST_V1",
        "status": status,
        "outcome_blind": True,
        "selected_signatures_excluded": True,
        "expected_shards": args.expected,
        "missing_shards": missing_shards,
        "shards": shards,
        "candidate_rows": total_rows,
        "unique_candidate_keys": len(seen),
        "duplicate_rows": duplicate_rows,
        "malformed_rows": malformed_rows,
        "source_authority": "closed immutable full-meta partitions; no outcomes/PnL",
        "scientific_use": "independent context universe only; no predictive verdict by this manifest",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.summary.write_text(
        json.dumps(
            {
                "schema": report["schema"],
                "status": status,
                "candidate_rows": total_rows,
                "unique_candidate_keys": len(seen),
                "duplicate_rows": duplicate_rows,
                "malformed_rows": malformed_rows,
                "missing_shards": missing_shards,
                "manifest_sha256": sha256_file(args.output),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
