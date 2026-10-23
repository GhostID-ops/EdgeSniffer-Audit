#!/usr/bin/env python3
"""Artifact-level regression checks for the KEFI-only research surfaces."""
from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CENSUS = ROOT / "research/kefi-13-arm-program-v1/kefi-only-route-census-current-v1.json"
SURFACE = ROOT / "research/kefi-13-arm-program-v1/kefi-route-notional-surface-discovery-current-v1.json"
HYPOTHESES = ROOT / "research/kefi-13-arm-program-v1/kefi-route-entry-exit-hypotheses-discovery-current-v1.json"
REGISTRY = ROOT / "research/kefi-13-arm-program-v1/kefi-only-route-registry-current-v1.json"
FREEZE_REVIEW = ROOT / "research/kefi-13-arm-program-v1/kefi-route-policy-freeze-review-current-v1.json"
CAUSAL_GATE = ROOT / "research/kefi-13-arm-program-v1/kefi-route-causal-coverage-gate-current-v1.json"
CLUE_AUDIT = ROOT / "research/kefi-13-arm-program-v1/kefi-route-clue-audit-current-v1.json"
DISCOVERY_CONTRACT = ROOT / "research/kefi-13-arm-program-v1/kefi-route-causal-discovery-contract-v1.json"


class KefiRouteResearchArtifactTests(unittest.TestCase):
    def test_census_is_keFi_only_and_outcome_blind(self) -> None:
        report = json.loads(CENSUS.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "KEFI_ONLY_ROUTE_CENSUS_V1")
        self.assertTrue(report["read_only"])
        self.assertTrue(report["outcome_blind"])
        self.assertTrue(report["legacy_candidate_labels_ignored"])
        self.assertEqual(report["raw_transactions"], 2548)
        by_route = {row["route"]: row for row in report["routes"]}
        self.assertEqual(by_route["PUMPSWAP_DIRECT"]["genuine_buys"], 938)
        self.assertEqual(by_route["PUMPSWAP_CPI"]["genuine_buys"], 265)
        self.assertEqual(by_route["PUMP_BONDING_CURVE"]["genuine_exact_events"], 0)

    def test_surface_has_realistic_sizes_and_delay(self) -> None:
        report = json.loads(SURFACE.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "KEFI_ROUTE_NOTIONAL_SURFACE_DISCOVERY_V1")
        self.assertTrue(report["read_only"])
        self.assertEqual(report["entry_delay_seconds"], 1)
        for route in ("PUMPSWAP_DIRECT", "PUMPSWAP_CPI"):
            self.assertEqual(set(report["routes"][route]["selected"]["sizes"]), {"0.1", "0.5", "1.0"})

    def test_hypothesis_search_does_not_freeze(self) -> None:
        report = json.loads(HYPOTHESES.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "KEFI_ROUTE_ENTRY_EXIT_HYPOTHESIS_SEARCH_V1")
        self.assertTrue(report["read_only"])
        self.assertIn("hypotheses_only", report["policy_status"])
        self.assertGreaterEqual(len(report["descriptive_rank_fixed30_0.5sol_25bps"]), 2)

    def test_keFi_registry_is_route_derived(self) -> None:
        report = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "KEFI_ONLY_ROUTE_REGISTRY_V1")
        self.assertEqual(report["route_count"], 8)
        self.assertFalse(report["policy_selection_performed"])
        self.assertFalse(report["promotion_performed"])
        self.assertIn("KEFI_ROUTE_PUMPSWAP_DIRECT", {row["id"] for row in report["routes"]})

    def test_freeze_review_is_fail_closed(self) -> None:
        report = json.loads(FREEZE_REVIEW.read_text(encoding="utf-8"))
        self.assertFalse(report["policy_frozen"])
        self.assertFalse(report["policy_selection_performed"])
        self.assertEqual(report["freeze_status"], "BLOCKED_NO_DISCOVERY_POLICY_PASSES")

    def test_native_causal_gate_rejects_legacy_tape(self) -> None:
        report = json.loads(CAUSAL_GATE.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "KEFI_ROUTE_CAUSAL_COVERAGE_GATE_V1")
        self.assertEqual(report["gate_status"], "BLOCKED_NATIVE_CAUSAL_COVERAGE")
        self.assertEqual(report["native_input_rows"], 0)
        self.assertFalse(report["policy_selection_performed"])
        self.assertFalse(report["trading_enabled"])

    def test_clue_audit_is_descriptive_only(self) -> None:
        report = json.loads(CLUE_AUDIT.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "KEFI_ROUTE_CLUE_AUDIT_V1")
        self.assertTrue(report["exploratory_only"])
        self.assertTrue(report["outcome_blind"])
        self.assertFalse(report["policy_selection_performed"])
        self.assertEqual(report["input_rows"], 2399)
        self.assertIn("PUMPSWAP_DIRECT", report["route_stats"])
        self.assertIn("PUMPSWAP_CPI", report["route_stats"])

    def test_causal_discovery_contract_keeps_routes_and_selection_separate(self) -> None:
        report = json.loads(DISCOVERY_CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "KEFI_ROUTE_CAUSAL_DISCOVERY_CONTRACT_V1")
        self.assertEqual({row["route"] for row in report["routes"]}, {"PUMPSWAP_DIRECT", "PUMPSWAP_CPI"})
        self.assertTrue(report["controls"]["route_specific"])
        self.assertFalse(report["controls"]["venue_only_controls_allowed_for_promotion"])
        self.assertFalse(report["freeze"]["policy_frozen"])
        self.assertFalse(report["freeze"]["winner_selected"])


if __name__ == "__main__":
    unittest.main()
