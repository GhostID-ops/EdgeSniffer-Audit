#!/usr/bin/env python3
"""Fail-closed gate for native route-specific causal coverage.

This is a coverage gate, not an economics evaluator.  It proves that a route
has enough native KEFI entries, native same-route controls, exact lineage, and
post-entry state marks to justify a later preregistered economics study.  It
does not select or freeze a policy and it cannot validate a strategy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "KEFI_ROUTE_CAUSAL_COVERAGE_GATE_V1"
MIN_GENUINE_BUYS = 50
MIN_NATIVE_CONTROLS = 50
MIN_CAUSAL_ENTRIES = 50
MIN_T1_COVERAGE = 0.80
CONTROL_WINDOW_SECONDS = 300
MAX_ENTRY_DELAY_SECONDS = 30


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def native_lineage_ok(row: dict[str, Any]) -> bool:
    return (
        row.get("schema") == "KEFI_ROUTE_CAUSAL_TAPE_V1"
        and row.get("route_evidence_native") is True
        and bool(row.get("source_frame_sha256"))
        and bool(row.get("source_instance_id"))
        and int(row.get("continuity_epoch") or 0) > 0
        and row.get("durable_output_sequence") is not None
        and row.get("exact_transition_validated") is True
    )


def route_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("route_certainty") == "EXACT_ROUTE" and row.get("underlying_venue") == "PUMPSWAP":
            grouped[str(row.get("route"))].append(row)
    result: dict[str, dict[str, Any]] = {}
    for route, items in sorted(grouped.items()):
        items.sort(key=lambda row: (int(row.get("event_timestamp") or 0), int(row.get("slot") or 0), int(row.get("durable_output_sequence") or 0)))
        teacher_buys = [row for row in items if row.get("is_tracked_teacher") and row.get("side") == "BUY"]
        teacher_sells = [row for row in items if row.get("is_tracked_teacher") and row.get("side") == "SELL"]
        controls = [row for row in items if not row.get("is_tracked_teacher") and row.get("side") == "BUY"]
        teacher_mints = {str(row.get("mint")) for row in teacher_buys}
        t1_marks = 0
        causal_entries = 0
        delays: list[int] = []
        control_matches = 0
        for entry in teacher_buys:
            timestamp = int(entry.get("event_timestamp") or 0)
            pool = str(entry.get("pool"))
            mint = str(entry.get("mint"))
            later = [
                row for row in items
                if str(row.get("pool")) == pool
                and str(row.get("mint")) == mint
                and int(row.get("event_timestamp") or 0) >= timestamp + 1
                and row.get("post_state")
            ]
            if later:
                delay = int(later[0].get("event_timestamp") or 0) - timestamp
                delays.append(delay)
                t1_marks += 1
                if delay <= MAX_ENTRY_DELAY_SECONDS:
                    causal_entries += 1
            if any(
                str(row.get("pool")) == pool
                and abs(int(row.get("event_timestamp") or 0) - timestamp) <= CONTROL_WINDOW_SECONDS
                for row in controls
            ):
                control_matches += 1
        lineage_total = len(items)
        lineage_valid = sum(native_lineage_ok(row) for row in items)
        unique_sequences = len({int(row["durable_output_sequence"]) for row in items if row.get("durable_output_sequence") is not None})
        t1_coverage = t1_marks / len(teacher_buys) if teacher_buys else 0.0
        result[route] = {
            "native_rows": len(items),
            "genuine_keFi_buys": len(teacher_buys),
            "genuine_keFi_sells": len(teacher_sells),
            "native_same_route_controls": len(controls),
            "teacher_entries_with_any_post_entry_mark": t1_marks,
            "teacher_entries_with_mark_within_30s": causal_entries,
            "t_plus_1_mark_coverage": t1_coverage,
            "entry_delay_seconds": {"min": min(delays) if delays else None, "median": sorted(delays)[len(delays) // 2] if delays else None, "max": max(delays) if delays else None},
            "teacher_entries_with_contemporaneous_control": control_matches,
            "unique_mints": len(teacher_mints),
            "lineage_rows": lineage_total,
            "lineage_valid_rows": lineage_valid,
            "lineage_complete": lineage_total > 0 and lineage_valid == lineage_total and unique_sequences == lineage_total,
            "unique_durable_sequences": unique_sequences,
            "thresholds": {
                "minimum_genuine_keFi_buys": MIN_GENUINE_BUYS,
                "minimum_native_controls": MIN_NATIVE_CONTROLS,
                "minimum_causal_entries": MIN_CAUSAL_ENTRIES,
                "minimum_t_plus_1_mark_coverage": MIN_T1_COVERAGE,
                "maximum_entry_mark_delay_seconds": MAX_ENTRY_DELAY_SECONDS,
            },
        }
        result[route]["eligible_for_native_causal_economics"] = all([
            result[route]["genuine_keFi_buys"] >= MIN_GENUINE_BUYS,
            result[route]["native_same_route_controls"] >= MIN_NATIVE_CONTROLS,
            result[route]["teacher_entries_with_mark_within_30s"] >= MIN_CAUSAL_ENTRIES,
            result[route]["t_plus_1_mark_coverage"] >= MIN_T1_COVERAGE,
            result[route]["lineage_complete"],
        ])
    return result


def build_report(rows: list[dict[str, Any]], source: Path) -> dict[str, Any]:
    routes = route_rows(rows)
    input_schemas = dict(Counter(str(row.get("schema") or "MISSING") for row in rows))
    native_input_rows = sum(native_lineage_ok(row) for row in rows)
    return {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "research_only": True,
        "source_path": str(source),
        "source_sha256": sha256_file(source) if source.exists() else None,
        "input_rows": len(rows),
        "input_schemas": input_schemas,
        "native_input_rows": native_input_rows,
        "native_source_required": True,
        "policy_selection_performed": False,
        "policy_frozen": False,
        "validation_started": False,
        "trading_enabled": False,
        "routes": routes,
        "eligible_route_count": sum(bool(row["eligible_for_native_causal_economics"]) for row in routes.values()),
        "gate_status": "READY_FOR_NATIVE_CAUSAL_ECONOMICS" if any(row["eligible_for_native_causal_economics"] for row in routes.values()) else "BLOCKED_NATIVE_CAUSAL_COVERAGE",
        "interpretation": {
            "purpose": "Prove that route-specific native evidence can support realistic t+1 economics before searching policies.",
            "controls": "Controls are same-route non-KEFI PumpSwap buys captured by the native route source, not venue-only tape rows.",
            "t_plus_1": "A mark is usable only when the same pool/mint has a native post-entry state at least one second after the KEFI buy; the causal threshold additionally requires that mark within 30 seconds.",
            "next_step": "Only eligible routes may enter the preregistered entry/exit hypothesis surface; any selected policy still requires a separate fresh prospective cohort.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-tape", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = load_jsonl(args.native_tape)
    report = build_report(rows, args.native_tape)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["gate_status"], "routes": len(report["routes"]), "eligible": report["eligible_route_count"]}, sort_keys=True))


if __name__ == "__main__":
    main()
