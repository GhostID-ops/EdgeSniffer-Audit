#!/usr/bin/env python3
"""Validate the completed, read-only 8YCB V3 research artifact set."""

from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_CLASSIFICATIONS = {
    "8YCB_PUBLIC_EVENT_STRONG_THIRD_PARTY_ALPHA",
    "8YCB_PUBLIC_EVENT_PROMISING_NEEDS_PROSPECTIVE_SHADOW",
    "8YCB_PERSONAL_ALPHA_REAL_BUT_THIRD_PARTY_NOT_FOLLOWABLE",
    "8YCB_PROFIT_PRIMARILY_CREATOR/REFLEXIVE",
    "8YCB_FOLLOWER_UPPER_BOUND_NOT_EXECUTABLE",
    "8YCB_DATA_BLOCKER",
}

JSON_FILES = (
    "generic_control_selection_summary.json",
    "generic_control_metadata_summary.json",
    "generic_control_metadata_errors.json",
    "first_sellable_summary.json",
    "native_pnl_decomposition.json",
    "target_exit_timing.json",
    "external_buyer_arrival_summary.json",
    "repeat_follower_cohorts.json",
    "follower_economics.json",
    "target_sell_collisions.json",
    "reflexivity_analysis.json",
    "metadata_generalization.json",
    "signal_component_attribution.json",
    "v3_summary.json",
)

JSONL_EXPECTED = {
    "generic_control_selection.jsonl.gz": 1095,
    "generic_control_metadata.jsonl.gz": 1095,
    "generic_control_enhanced_transactions.jsonl.gz": 1095,
    "first_sellable_telemetry.jsonl.gz": 52,
    "external_buyer_arrivals.jsonl.gz": 1313,
    "signal_component_rows.jsonl.gz": 1313,
    "follower_latency_outcomes.jsonl.gz": 23640,
    "reflexivity_counterfactual_outcomes.jsonl.gz": 657,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def count_and_parse_jsonl_gz(path: Path) -> int:
    count = 0
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"invalid JSON in {path.name}:{line_number}: {exc}") from exc
            count += 1
    return count


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    checks: dict[str, object] = {}

    parsed = {}
    for name in JSON_FILES:
        path = root / name
        require(path.is_file() and path.stat().st_size > 0, f"missing/empty {name}")
        parsed[name] = load_json(path)
    checks["json_files_parsed"] = len(JSON_FILES)

    jsonl_counts = {}
    for name, expected in JSONL_EXPECTED.items():
        path = root / name
        require(path.is_file() and path.stat().st_size > 0, f"missing/empty {name}")
        actual = count_and_parse_jsonl_gz(path)
        require(actual == expected, f"{name}: expected {expected} rows, got {actual}")
        jsonl_counts[name] = actual
    checks["jsonl_rows"] = jsonl_counts

    summary = parsed["v3_summary.json"]
    coverage = summary["coverage"]
    require(coverage["complete_target_120s_horizons"] == 219, "target horizon count mismatch")
    require(coverage["complete_time_matched_generic_120s_horizons"] == 1094, "generic horizon count mismatch")
    require(summary["stream_integrity"]["selected_unique_rows"] == 32269, "selected flow-row count mismatch")
    require(summary["stream_integrity"]["compact_rows_scanned"] == 3879386, "compact scan count mismatch")
    require(abs(coverage["frozen_stream_duration_days"] - 5.420034340277778) < 1e-12, "frozen window mismatch")
    require(summary["first_sellable"]["unique_safe_first_sellable_trades"] == 52, "first-sellable count mismatch")

    selection = parsed["generic_control_selection_summary.json"]
    require(selection["selected_unique_generic_controls"] == 1095, "raw generic-control count mismatch")
    metadata = parsed["generic_control_metadata_summary.json"]
    require(metadata["decoded_create_rows"] == 1095 and metadata["decode_errors"] == 0, "metadata decode incomplete")

    classification = summary["final_classification"]
    require(classification in ALLOWED_CLASSIFICATIONS, "invalid final classification")
    report_path = root / "report.md"
    require(report_path.is_file() and report_path.stat().st_size > 0, "missing/empty report.md")
    report = report_path.read_text(encoding="utf-8")
    require(f"`{classification}`" in report, "report/summary classification mismatch")
    checks["final_classification"] = classification

    compact = root / "all_launch_stream_120s.tsv"
    require(compact.is_file() and compact.stat().st_size > 0, "missing compact stream extract")
    with compact.open("rb") as handle:
        header = handle.readline().decode("utf-8").rstrip("\r\n")
    require(
        header == "ts_ms\tslot\ttx_index\tix_index\tsignature\tmint\tside\twallet\tquote_lamports\tsell_token_raw",
        "unexpected compact stream header",
    )
    checks["compact_stream_bytes"] = compact.stat().st_size
    checks["compact_stream_rows_from_extractor"] = 3879386

    checks["coverage"] = {
        "target_complete_horizons": coverage["complete_target_120s_horizons"],
        "generic_complete_horizons": coverage["complete_time_matched_generic_120s_horizons"],
        "selected_unique_flow_rows": summary["stream_integrity"]["selected_unique_rows"],
    }
    manifest = {
        "schema": "8ycb-causal-followability-v3-validation-v1",
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS",
        "root": str(root),
        "checks": checks,
    }
    out = root / "validation_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
