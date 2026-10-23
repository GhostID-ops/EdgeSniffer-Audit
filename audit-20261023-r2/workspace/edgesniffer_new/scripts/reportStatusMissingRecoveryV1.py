#!/usr/bin/env python3
"""Report post-materialization status for a frozen recovery manifest."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
from collections import Counter, defaultdict


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pre", required=True, type=pathlib.Path)
    parser.add_argument("--state", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()

    before = json.loads(args.pre.read_text(encoding="utf-8"))
    signatures = [row["signature"] for row in before]
    if len(signatures) != 16 or len(set(signatures)) != 16:
        raise RuntimeError("EXPECTED_EXACTLY_16_UNIQUE_SIGNATURES")

    connection = sqlite3.connect(f"file:{args.state}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in signatures)
    rows = connection.execute(
        f"""
        SELECT signature, mint, status, reasons_json, detail_json,
               version_sha, updated_utc
          FROM current_decisions
         WHERE signature IN ({placeholders})
        """,
        signatures,
    ).fetchall()
    connection.close()

    after_by_signature = {row["signature"]: dict(row) for row in rows}
    if set(after_by_signature) != set(signatures):
        missing = sorted(set(signatures) - set(after_by_signature))
        raise RuntimeError(f"MISSING_POST_ROWS:{missing}")

    transitions: list[dict] = []
    counts: Counter[str] = Counter()
    by_partition: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for prior in before:
        current = after_by_signature[prior["signature"]]
        prior_detail = json.loads(prior["detail_json"])
        hours = [item["hour"] for item in prior_detail.get("hours", [])]
        partition = hours[0] if len(hours) == 1 else ",".join(hours)
        status = current["status"]
        counts[status] += 1
        by_partition[partition][status] += 1
        transitions.append(
            {
                "signature": prior["signature"],
                "mint": current["mint"],
                "partition": partition,
                "beforeStatus": prior["status"],
                "afterStatus": status,
                "afterReasons": json.loads(current["reasons_json"]),
                "afterDetail": json.loads(current["detail_json"]),
                "beforeVersionSha": prior["version_sha"],
                "afterVersionSha": current["version_sha"],
                "updatedUtc": current["updated_utc"],
            }
        )

    document = {
        "schema": "2CQ_STATUS_MISSING_RECOVERY_V1_POST_MATERIALIZER",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "preManifest": str(args.pre),
        "stateDatabase": str(args.state),
        "caseN": len(transitions),
        "statusCounts": dict(sorted(counts.items())),
        "partitionStatusCounts": {
            key: dict(sorted(value.items())) for key, value in sorted(by_partition.items())
        },
        "transitions": transitions,
        "outcomeFieldsRead": 0,
        "validationRowsRead": 0,
    }
    document["reportSha256"] = hashlib.sha256(canonical_json(document)).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
