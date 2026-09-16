#!/usr/bin/env python3
"""Validate the isolated 8YCB research deliverables without network access."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
from pathlib import Path


REQUIRED_JSON = [
    "feature_lift.json",
    "model_forensics.json",
    "native_ledger_summary.json",
    "signature_integrity.json",
    "funder_audit.json",
    "stream_overlap_summary.json",
    "create_decode_errors.json",
    "target_trade_parse_rejections.json",
]
REQUIRED_JSONL_GZ = [
    "target_enhanced_transactions.jsonl.gz",
    "target_trade_events.jsonl.gz",
    "target_trade_ledger.jsonl.gz",
    "target_launches.jsonl.gz",
    "ignored_control_launches.jsonl.gz",
    "positive_control_feature_table.jsonl.gz",
    "creator_funder_entities.jsonl.gz",
    "same_slot_participants.jsonl.gz",
    "metadata_features.jsonl.gz",
    "target_outcomes.jsonl.gz",
    "stream_target_outcomes.jsonl.gz",
    "follower_latency_outcomes.jsonl.gz",
]
HEADINGS = [chr(ord("A") + index) for index in range(26)] + ["AA", "AB", "AC"]
ALLOWED_CLASSIFICATIONS = [
    "8YCB_SELECTION_EDGE_EXPLAINED_AND_FOLLOWABLE",
    "8YCB_SELECTION_EDGE_PARTIALLY_EXPLAINED_PROMISING",
    "8YCB_PERSONAL_ALPHA_REAL_BUT_NOT_FOLLOWABLE",
    "8YCB_SELECTION_SIGNAL_APPEARS_REFLEXIVE",
    "8YCB_EDGE_REQUIRES_MISSING_OFFCHAIN_INFORMATION",
    "8YCB_TRACKER_PERFORMANCE_NOT_CONFIRMED",
    "8YCB_DATASET_PARTIAL_BUT_USEFUL",
    "8YCB_DATA_BLOCKER",
]


def main() -> None:
    base = Path(sys.argv[1])
    errors: list[str] = []
    counts: dict[str, int] = {}
    parsed_json: dict[str, object] = {}

    for name in REQUIRED_JSON:
        path = base / name
        try:
            parsed_json[name] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    for name in REQUIRED_JSONL_GZ:
        path = base / name
        count = 0
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    json.loads(line)
                    count += 1
        except Exception as exc:
            errors.append(f"{name}: line {line_number if 'line_number' in locals() else '?'}: {exc}")
        counts[name] = count

    summary = parsed_json.get("native_ledger_summary.json") or {}
    if isinstance(summary, dict):
        expected = {
            "target_launches.jsonl.gz": summary.get("coverage", {}).get("self_created_with_initial_buy"),
            "ignored_control_launches.jsonl.gz": summary.get("coverage", {}).get("self_created_without_initial_buy"),
            "positive_control_feature_table.jsonl.gz": (
                (summary.get("coverage", {}).get("self_created_with_initial_buy") or 0)
                + (summary.get("coverage", {}).get("self_created_without_initial_buy") or 0)
            ),
            "target_trade_ledger.jsonl.gz": (
                (summary.get("integrity", {}).get("closed_positions") or 0)
                + (summary.get("integrity", {}).get("unresolved_positions") or 0)
                + (summary.get("integrity", {}).get("left_censored_sell_only_positions") or 0)
            ),
        }
        for name, expected_count in expected.items():
            if counts.get(name) != expected_count:
                errors.append(f"{name}: expected {expected_count}, got {counts.get(name)}")

    follower_non_null_first_sellable = 0
    with gzip.open(base / "follower_latency_outcomes.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip() and json.loads(line).get("first_sellable_adjusted_result") is not None:
                follower_non_null_first_sellable += 1
    if follower_non_null_first_sellable:
        errors.append(f"follower rows with unsupported first-sellable result: {follower_non_null_first_sellable}")

    report = (base / "report.md").read_text(encoding="utf-8")
    observed_headings = re.findall(r"^## ([A-Z]{1,2})\.", report, flags=re.MULTILINE)
    if observed_headings != HEADINGS:
        errors.append(f"report headings differ: {observed_headings}")
    report_classes = [value for value in ALLOWED_CLASSIFICATIONS if value in report]
    if report_classes != ["8YCB_DATASET_PARTIAL_BUT_USEFUL"]:
        errors.append(f"report classifications differ: {report_classes}")
    if not report_classes or not isinstance(summary, dict) or summary.get("final_classification") != report_classes[0]:
        errors.append("summary/report final classification mismatch")

    checksums = {}
    for path in sorted(base.iterdir()):
        if path.is_file() and path.name not in {
            "validation_manifest.json", "SHA256SUMS",
            "analyze_wallet.latest.stdout.json", "validation.latest.stdout.json",
        }:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            checksums[path.name] = digest.hexdigest()

    manifest = {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "jsonl_row_counts": counts,
        "follower_non_null_first_sellable_rows": follower_non_null_first_sellable,
        "report_headings": observed_headings,
        "report_classifications": report_classes,
        "sha256": checksums,
    }
    (base / "validation_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (base / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in checksums.items()), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
