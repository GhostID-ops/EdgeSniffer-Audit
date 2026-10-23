#!/usr/bin/env python3

import unittest

import twoCqProspectiveWhenReadinessV1 as subject
from exactCurveEconomicsV1 import CurveState


def event(signature, instruction, received, user, pre_sol, post_sol, pre_token=1_000_000):
    pre = CurveState(pre_sol, pre_token, pre_sol, pre_token)
    post = CurveState(post_sol, pre_token - 100_000, post_sol, pre_token - 100_000)
    return subject.decomposition.CausalEvent(
        mint="mint", signature=signature, slot=10, transaction_index=instruction,
        instruction_index=instruction, received_ns=received, is_buy=True,
        user=user, creator="creator", quote_lamports=1_000_000_000,
        token_raw=100_000, pre_state=pre, post_state=post,
    )


class ProspectiveWhenReadinessTests(unittest.TestCase):
    def test_builds_ordered_temporal_states_without_outcomes(self):
        external_one = event("a", 0, 2_000_000_000, "buyer-a", 30_000_000_000, 31_000_000_000)
        external_two = event(
            "b", 1, 3_000_000_000, "buyer-b", 31_000_000_000,
            32_000_000_000, pre_token=900_000,
        )
        choice = {
            "decisionBoundary": {"arrivalUnixNs": 4_000_000_000, "slot": 11, "transactionIndex": 0},
            "states": [{
                "label": True, "mint": "mint", "stateSignature": "b",
                "stateInstructionIndex": 1, "createReceivedAtUnixNs": 1_000_000_000,
            }],
            "outcomesRead": 0, "submissions": 0,
        }
        decision = {
            "eligibleOrdinal": 1, "signature": "teacher", "mint": "mint",
            "choice": choice, "selectedState": choice["states"][0],
        }
        row, reasons = subject.build_temporal_row(
            decision, [external_one, external_two], []
        )
        self.assertEqual([], reasons)
        self.assertIsNotNone(row)
        self.assertEqual(1, row["controlN"])
        self.assertFalse(row["states"][0]["label"])
        self.assertTrue(row["states"][1]["label"])
        self.assertEqual(0, row["outcomesRead"])
        self.assertTrue(set(subject.decomposition.timing.FULL_FEATURES).issubset(
            row["states"][1]["features"]
        ))

    def test_fails_closed_on_state_mismatch(self):
        external = event("a", 0, 2_000_000_000, "buyer", 30_000_000_000, 31_000_000_000)
        choice = {
            "decisionBoundary": {"arrivalUnixNs": 4_000_000_000, "slot": 11, "transactionIndex": 0},
            "states": [{
                "label": True, "mint": "mint", "stateSignature": "different",
                "stateInstructionIndex": 0, "createReceivedAtUnixNs": 1_000_000_000,
            }],
            "outcomesRead": 0, "submissions": 0,
        }
        row, reasons = subject.build_temporal_row(
            {"eligibleOrdinal": 1, "signature": "teacher", "mint": "mint",
             "choice": choice, "selectedState": choice["states"][0]},
            [external], [],
        )
        self.assertIsNone(row)
        self.assertEqual(["FINAL_SELECTED_STATE_MISMATCH"], reasons)


if __name__ == "__main__":
    unittest.main()
