#!/usr/bin/env python3

import unittest
from pathlib import Path

import twoCqChoiceSetCoverageRepairV1 as subject
from exactCurveEconomicsV1 import base58_encode


class ChoiceSetCoverageRepairTests(unittest.TestCase):
    def test_create_mint_is_resolved_from_instruction_account(self):
        mint = "mint"
        tx = {
            "transaction": {"message": {
                "accountKeys": [subject.tier_a.PUMP_PROGRAM, mint],
                "instructions": [{
                    "programIdIndex": 0,
                    "accounts": [1],
                    "data": base58_encode(next(iter(subject.CREATE_DISCRIMINATORS)) + bytes(24)),
                }],
            }},
            "meta": {"err": None, "innerInstructions": []},
        }
        self.assertEqual({mint}, subject.canonical_create_mints(tx))

    def test_loaded_address_create_mint_is_supported(self):
        mint = "loaded-mint"
        tx = {
            "transaction": {"message": {
                "accountKeys": [subject.tier_a.PUMP_PROGRAM],
                "instructions": [{
                    "programIdIndex": 0,
                    "accounts": [1],
                    "data": base58_encode(next(iter(subject.CREATE_DISCRIMINATORS)) + bytes(24)),
                }],
            }},
            "meta": {
                "err": None,
                "loadedAddresses": {"writable": [mint], "readonly": []},
                "innerInstructions": [],
            },
        }
        self.assertEqual({mint}, subject.canonical_create_mints(tx))

    def test_source_does_not_accept_validation_or_outcomes(self):
        source = Path(subject.__file__).read_text(encoding="utf-8")
        self.assertNotIn("--validation", source)
        self.assertNotIn("validation-sealed/eligible", source)
        self.assertNotIn("PnL", source)

    def test_canonical_scan_has_no_metadata_pump_prefilter(self):
        source = Path(subject.__file__).read_text(encoding="utf-8")
        self.assertNotIn('if not kinds.intersection(decision.TRADE_KINDS', source)
        self.assertIn('for mint in canonical_create_mints(tx)', source)

    def test_zero_recovery_is_a_coverage_blocker(self):
        self.assertEqual(
            "2CQ_CHOICE_SET_EXISTING_EVIDENCE_COVERAGE_BLOCKER",
            subject.coverage_verdict(0, 0),
        )

    def test_positive_nonregressing_recovery_is_repaired(self):
        self.assertEqual(
            "2CQ_CHOICE_SET_COVERAGE_REPAIRED_OUTCOME_BLIND",
            subject.coverage_verdict(1, 0),
        )


if __name__ == "__main__":
    unittest.main()
