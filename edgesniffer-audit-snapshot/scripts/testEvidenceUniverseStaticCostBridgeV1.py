#!/usr/bin/env python3

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import evidenceUniverseStaticCostBridgeV1 as study


class EvidenceUniverseStaticCostBridgeTests(unittest.TestCase):
    def test_block_boundaries(self):
        blocks = [[1, 10], [11, 20]]
        self.assertEqual(study.block_for(1, blocks), 1)
        self.assertEqual(study.block_for(20, blocks), 2)

    def test_bridge_aggregation_reconciles(self):
        row = {
            "status": "EXACT", "componentsSol": {"idealized_external_market_opportunity": 0.1, "delivery_overlay": -0.001},
            "rawFeesLamports": {"baseTransactions": 10000}, "bridgeFinalPnlSol": 0.099,
            "reconciliationDifferenceSol": 0.0, "counterfactualFailedTransactions": 0,
        }
        result = study.aggregate_bridge([row])
        self.assertEqual(result["exactN"], 1)
        self.assertAlmostEqual(result["meanIdealizedOpportunitySol"], 0.1)
        self.assertAlmostEqual(result["meanFinalPnlSol"], 0.099)

    def test_historical_state_is_causal(self):
        class S:
            pass
        a, b, c = S(), S(), S()
        groups = [{"arrivalMs": 100, "postState": b}, {"arrivalMs": 200, "postState": c}]
        self.assertIs(study.historical_state_at(groups, 150, a), b)

    def test_creator_only_activity_is_benign(self):
        class Event:
            user = "creator"
            creator = "creator"
            quote_mint = next(iter(study.SOL_QUOTES))

        class Row:
            event = Event()
            arrival_ms = 100

        category, _ = study.classify_mint(
            {"createReceivedAtUnixNs": 0},
            {"status": "EXCLUDED", "decisionStates": []},
            [Row()],
            {
                "rawTargetTradeN": 1, "rawSuccessfulTargetTradeN": 1,
                "finalizedTargetTradeN": 1, "successfulFinalizedTradeN": 1,
                "failedFinalizedTradeN": 0, "explicitGapN": 0,
                "missingArrivalN": 0, "decodeFailureN": 0,
                "missingArrivalExternalEventN": 0,
                "missingArrivalExternalSolEventN": 0,
                "missingArrivalExternalSolMinBlockTimeMs": None,
                "missingArrivalExternalSolUnknownBlockTimeN": 0,
                "hasTradeEventN": 1, "instructionKinds": {},
            },
            30_000,
        )
        self.assertEqual(category, "INERT_OR_NO_EXTERNAL_ACTIVITY")


if __name__ == "__main__":
    unittest.main()
