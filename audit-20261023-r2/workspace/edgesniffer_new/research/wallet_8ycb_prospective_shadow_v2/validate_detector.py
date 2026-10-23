#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from shadow_observer import Shadow, integrity_defaults


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    fixture_path = Path(args.fixture)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    detected = []
    with tempfile.TemporaryDirectory() as directory:
        shadow = Shadow.__new__(Shadow)
        shadow.integrity_counts = integrity_defaults()
        shadow.decoder_errors = 0
        shadow.dropped_rows = 0
        shadow.rejection_path = Path(directory) / "rejections.jsonl"
        for case in fixture["cases"]:
            detected.append({
                "id": case["id"], "expected": case["expected"],
                "detected": shadow.atomic_matches(case["create"], case["atomic"]),
            })
    expected_positives = sum(row["expected"] for row in detected)
    detected_positives = sum(row["detected"] for row in detected)
    false_negatives = [row["id"] for row in detected if row["expected"] and not row["detected"]]
    false_positives = [row["id"] for row in detected if not row["expected"] and row["detected"]]
    result = {
        "schema": "8ycb-canonical-matcher-parity-v2",
        "fixture": str(fixture_path), "fixture_schema": fixture["schema"],
        "fixture_provenance": fixture["provenance"], "case_count": len(detected),
        "expected_positives": expected_positives, "detected_positives": detected_positives,
        "false_negatives": len(false_negatives), "false_negative_ids": false_negatives,
        "false_positives": len(false_positives), "false_positive_ids": false_positives,
        "status": "PASS" if not false_negatives and not false_positives else "FAIL",
        "cases": detected,
    }
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
