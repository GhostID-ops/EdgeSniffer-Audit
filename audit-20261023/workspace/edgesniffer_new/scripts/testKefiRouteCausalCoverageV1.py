#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auditKefiRouteCausalCoverageV1 as gate


def row(sequence: int, timestamp: int, *, teacher: bool, side: str = "BUY", pool: str = "pool", mint: str = "mint") -> dict:
    return {
        "schema": "KEFI_ROUTE_CAUSAL_TAPE_V1",
        "route": "PUMPSWAP_CPI",
        "route_certainty": "EXACT_ROUTE",
        "underlying_venue": "PUMPSWAP",
        "route_evidence_native": True,
        "source_frame_sha256": "frame",
        "source_instance_id": "instance",
        "continuity_epoch": 1,
        "durable_output_sequence": sequence,
        "exact_transition_validated": True,
        "event_timestamp": timestamp,
        "is_tracked_teacher": teacher,
        "side": side,
        "pool": pool,
        "mint": mint,
        "post_state": {"base": 100, "quote": 100},
    }


class NativeCoverageTests(unittest.TestCase):
    def test_gate_requires_native_route_and_controls(self) -> None:
        rows = [row(0, 1000, teacher=True), row(1, 1002, teacher=False), row(2, 1003, teacher=False)]
        report = gate.build_report(rows, Path("synthetic.jsonl"))
        self.assertEqual(report["gate_status"], "BLOCKED_NATIVE_CAUSAL_COVERAGE")
        self.assertFalse(report["routes"]["PUMPSWAP_CPI"]["eligible_for_native_causal_economics"])

    def test_t_plus_1_and_lineage_are_measured(self) -> None:
        rows = [
            row(0, 1000, teacher=True),
            row(1, 1002, teacher=False),
            row(2, 1003, teacher=False),
        ]
        metrics = gate.route_rows(rows)["PUMPSWAP_CPI"]
        self.assertEqual(metrics["genuine_keFi_buys"], 1)
        self.assertEqual(metrics["native_same_route_controls"], 2)
        self.assertEqual(metrics["teacher_entries_with_any_post_entry_mark"], 1)
        self.assertEqual(metrics["teacher_entries_with_mark_within_30s"], 1)
        self.assertTrue(metrics["lineage_complete"])

    def test_late_mark_does_not_count_as_causal_entry(self) -> None:
        rows = [row(0, 1000, teacher=True), row(1, 1040, teacher=False)]
        metrics = gate.route_rows(rows)["PUMPSWAP_CPI"]
        self.assertEqual(metrics["teacher_entries_with_any_post_entry_mark"], 1)
        self.assertEqual(metrics["teacher_entries_with_mark_within_30s"], 0)

    def test_lineage_gate_rejects_duplicate_durable_sequence(self) -> None:
        rows = [row(4, 1000, teacher=True), row(4, 1002, teacher=False)]
        metrics = gate.route_rows(rows)["PUMPSWAP_CPI"]
        self.assertFalse(metrics["lineage_complete"])


if __name__ == "__main__":
    unittest.main()
