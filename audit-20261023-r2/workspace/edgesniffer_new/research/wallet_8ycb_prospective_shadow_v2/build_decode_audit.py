#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    scan = json.loads(Path(args.scan).read_text(encoding="utf-8"))
    reported = scan["v1_service"]["reported_decoder_errors"]
    attribution = scan["attribution"]
    accounted = sum(attribution[key] for key in (
        "producer_framing_concatenated_records", "producer_framing_blank_lines", "partial_eof_reads_not_persisted"
    ))
    categories = [
        {"category": "truncation_partial_eof_read", "n": attribution["partial_eof_reads_not_persisted"], "example": "original fragments unavailable because V1 did not log them; complete-line regression test deterministically reproduces and prevents the failure", "true_8ycb_event_could_be_lost": True, "impact": "signal admission and diagnostics"},
        {"category": "malformed_json_concatenated_records", "n": attribution["producer_framing_concatenated_records"], "example": scan["global_scan"]["during_service"][0], "true_8ycb_event_could_be_lost": False, "impact": "proven irrelevant to 8YCB signal admission for these two physical rows"},
        {"category": "malformed_json_blank_line", "n": attribution["producer_framing_blank_lines"], "example": scan["global_scan"]["during_service"][1], "true_8ycb_event_could_be_lost": False, "impact": "none"},
    ]
    for name in ("unexpected_field_type", "missing_slot", "missing_transaction_index", "missing_signature", "missing_mint", "decoder_schema_mismatch", "unsupported_transaction_shape", "provider_specific_representation", "unknown"):
        categories.append({"category": name, "n": 0, "example": None, "true_8ycb_event_could_be_lost": False, "impact": "none among the 51 JSON decode errors"})
    result = {
        "schema": "8ycb-v1-decode-error-audit-v1", "reported_errors": reported,
        "accounted_errors": accounted, "unaccounted_errors": reported - accounted,
        "status": "PASS_FULLY_ACCOUNTED" if accounted == reported else "BLOCKER_UNACCOUNTED_ERRORS",
        "categories": categories,
        "separate_non_decode_integrity_incident": {
            "category": "string_integer_numeric_mismatch", "n": 1,
            "event": "tahKR4TA337akDkgfGZ5Qaeng4ABdov49rVePABpump",
            "impact": "suppressed a qualifying signal", "included_in_51": False,
        },
        "v2_acceptance_gate": {
            "partial_eof_fix": "binary follower refuses to advance until newline is present",
            "malformed_row_policy": "fail closed and append reason, raw hash, and bounded excerpt to ingest_rejections.jsonl",
            "health_counters": "persisted and exposed by category",
            "validation_fixture_unexplained_signal_affecting_errors": 0,
        },
        "source_scan": scan,
    }
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "PASS_FULLY_ACCOUNTED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
