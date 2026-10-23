#!/usr/bin/env python3

import pathlib
import sys
import json
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from twoCqProspectiveTierAEntryCensusV1 import (
    Event, PUMP_PROGRAM, WALLET, evaluate_candidate, freeze_study_design,
    parse_candidate, wallet_token_amount,
)


class ProspectiveTierATest(unittest.TestCase):
    def test_candidate_gate_is_future_teacher_pump_buy(self):
        row = {
            "kind": "swap", "detectedAtMs": 2000, "wallet": WALLET,
            "isNativeInput": True, "outputMint": "mint", "signature": "signature",
            "sourceIxAccounts": [PUMP_PROGRAM], "sourcePumpSwapInstruction": None,
            "sourceSlot": 9, "sourceTransactionIndex": 3,
        }
        self.assertEqual(parse_candidate(row, 1000)["mint"], "mint")
        self.assertIsNone(parse_candidate(row, 3000))
        self.assertIsNone(parse_candidate({**row, "wallet": "other"}, 1000))
        self.assertIsNone(parse_candidate({**row, "sourceIxAccounts": []}, 1000))

    def test_wallet_balance_proof_sums_owned_accounts_only(self):
        tx = {"meta": {"preTokenBalances": [
            {"mint": "mint", "owner": WALLET, "uiTokenAmount": {"amount": "10"}},
            {"mint": "mint", "owner": WALLET, "uiTokenAmount": {"amount": "5"}},
            {"mint": "mint", "owner": "other", "uiTokenAmount": {"amount": "99"}},
        ]}}
        self.assertEqual(wallet_token_amount(tx, "preTokenBalances", "mint"), 15)
        self.assertIsNone(wallet_token_amount(tx, "postTokenBalances", "mint"))

    def test_entry_is_excluded_from_preentry_controls(self):
        state = {"virtual_sol": 1, "virtual_token": 2, "real_sol": 3, "real_token": 4}
        prior = Event("mint", "prior", 8, 1, 2, 1000, "arrival1", "canonical1", True, "other", "creator", 5, 6, state, state)
        entry = Event("mint", "entry", 9, 1, 2, 1100, "arrival2", "canonical2", True, WALLET, "creator", 7, 8, state, state)
        transaction = {"meta": {
            "preTokenBalances": [],
            "postTokenBalances": [{"mint": "mint", "owner": WALLET, "uiTokenAmount": {"amount": "8"}}],
        }}
        row, reasons = evaluate_candidate(
            {"signature": "entry", "mint": "mint", "observedAtUtc": "future"},
            [prior, entry], {"entry": transaction}, 1,
            {"createReceivedAtUnixNs": 500_000_000, "createSignature": "create", "createSlot": 7},
        )
        self.assertEqual(reasons, [])
        self.assertEqual([item["signature"] for item in row["preEntryEvents"]], ["prior"])
        self.assertEqual(row["preEntryFeatureBoundary"]["teacherEntryContribution"], 0)
        self.assertEqual(row["creation"]["observedUnixMs"], 500)
        self.assertEqual(row["initialCurveState"], state)

    def test_missing_prior_arrival_fails_closed(self):
        state = {"virtual_sol": 1, "virtual_token": 2, "real_sol": 3, "real_token": 4}
        prior = Event("mint", "prior", 8, 1, 2, 0, "", "canonical1", True, "other", "creator", 5, 6, state, state)
        entry = Event("mint", "entry", 9, 1, 2, 1100, "arrival2", "canonical2", True, WALLET, "creator", 7, 8, state, state)
        transaction = {"meta": {
            "postTokenBalances": [{"mint": "mint", "owner": WALLET, "uiTokenAmount": {"amount": "8"}}],
        }}
        row, reasons = evaluate_candidate(
            {"signature": "entry", "mint": "mint", "observedAtUtc": "future"},
            [prior, entry], {"entry": transaction}, 1,
            {"createReceivedAtUnixNs": 500_000_000},
        )
        self.assertIsNone(row)
        self.assertIn("PREENTRY_CAUSAL_ARRIVAL_MISSING", reasons)

    def test_missing_create_arrival_fails_closed(self):
        state = {"virtual_sol": 1, "virtual_token": 2, "real_sol": 3, "real_token": 4}
        prior = Event("mint", "prior", 8, 1, 2, 1000, "arrival1", "canonical1", True, "other", "creator", 5, 6, state, state)
        entry = Event("mint", "entry", 9, 1, 2, 1100, "arrival2", "canonical2", True, WALLET, "creator", 7, 8, state, state)
        transaction = {"meta": {
            "postTokenBalances": [{"mint": "mint", "owner": WALLET, "uiTokenAmount": {"amount": "8"}}],
        }}
        row, reasons = evaluate_candidate(
            {"signature": "entry", "mint": "mint", "observedAtUtc": "future"},
            [prior, entry], {"entry": transaction}, 1, {},
        )
        self.assertIsNone(row)
        self.assertIn("CREATE_CAUSAL_ARRIVAL_MISSING_OR_INVALID", reasons)

    def test_study_freeze_is_parameter_only_and_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            script = root / "historical.py"
            report = root / "report.json"
            script.write_text("frozen = True\n", encoding="utf-8")
            report.write_text(json.dumps({
                "entryTiming": {
                    "progressionBaseline": {"featureNames": ["age"]},
                    "predeclaredFamilyAblations": {"progressionPlusFlow": {}},
                    "fullCausalModel": {
                        "featureNames": ["age", "flow"],
                        "coefficients": [
                            {"feature": "flow", "standardizedCoefficient": 2.0},
                            {"feature": "age", "standardizedCoefficient": 1.0},
                        ],
                        "trainMeans": {"age": 3.0, "flow": 4.0},
                        "trainScales": {"age": 5.0, "flow": 6.0},
                        "intercept": 0.5,
                    },
                },
                "conviction": {"tierBoundariesSol": [1.0, 2.0, 3.0, 4.0]},
            }), encoding="utf-8")
            frozen = freeze_study_design(root / "cohort", script, report)
            model = json.loads((root / "cohort" / "locked-v1-entry-timing-model.json").read_text())
            self.assertEqual(model["coefficients"], [1.0, 2.0])
            self.assertFalse(model["performanceMetricsIncluded"])
            self.assertFalse(frozen["futureOutcomesPermitted"])
            report.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "HISTORICAL_MODEL_REPORT_CHANGED"):
                freeze_study_design(root / "cohort", script, report)


if __name__ == "__main__":
    unittest.main()
