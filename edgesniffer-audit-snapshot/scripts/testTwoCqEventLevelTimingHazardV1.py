#!/usr/bin/env python3

import unittest

import twoCqEventLevelTimingHazardV1 as subject


def features(flow: float = 0.0) -> dict[str, float]:
    row = {name: 0.0 for name in subject.timing.FULL_FEATURES}
    row["net_flow_sol_250ms"] = flow
    row["gross_buy_sol_250ms"] = max(0.0, flow)
    return row


def state(signature: str, received_ns: int, label: bool, flow: float) -> dict:
    return {
        "signature": signature,
        "receivedNs": received_ns,
        "label": label,
        "features": features(flow),
    }


class EventLevelTimingHazardTests(unittest.TestCase):
    def test_transition_enrichment_preserves_canonical_source_order(self):
        example = {
            "states": [
                state("z-signature", 2_000_000, False, 1.0),
                state("a-signature", 2_000_000, True, 4.0),
            ]
        }
        result = subject.enrich_canonical_transitions([example])[0]["states"]
        self.assertEqual(["z-signature", "a-signature"], [row["signature"] for row in result])
        self.assertEqual(3.0, result[1]["features"]["delta_net_flow_sol_250ms"])

    def test_proximal_weights_favor_near_teacher_controls(self):
        example = {
            "states": [
                state("1", 1, False, 0),
                state("2", 2, False, 1),
                state("3", 3, False, 2),
                state("4", 4, True, 3),
            ]
        }
        enriched = subject.enrich_canonical_transitions([example])
        _, labels, weights = subject.flatten_proximal(enriched, subject.FEATURES)
        self.assertEqual([0, 0, 0, 1], labels)
        self.assertGreater(weights[2], weights[1])
        self.assertGreater(weights[1], weights[0])
        self.assertAlmostEqual(0.5, sum(weights[:-1]))
        self.assertAlmostEqual(0.5, weights[-1])

    def test_threshold_selection_honors_recall_floor(self):
        rows = [
            {
                "eligibleOrdinal": 1,
                "mint": "a",
                "states": [
                    state("a1", 1, False, 0) | {"hazardScore": 0.2},
                    state("a2", 2, True, 1) | {"hazardScore": 0.8},
                ],
            },
            {
                "eligibleOrdinal": 2,
                "mint": "b",
                "states": [
                    state("b1", 1, False, 0) | {"hazardScore": 0.7},
                    state("b2", 2, True, 1) | {"hazardScore": 0.9},
                ],
            },
        ]
        threshold, metrics = subject.select_threshold(rows, {1: 3, 2: 3}, 100.0)
        self.assertEqual(0.8, threshold)
        self.assertEqual(100.0, metrics["teacherStateRecallPct"])
        self.assertEqual(100.0, metrics["exactFirstTriggerPct"])

    def test_vectorized_fitter_matches_repository_objective(self):
        x = [[-1.0, 0.5], [0.0, -0.5], [1.0, 1.5], [2.0, -1.0]]
        y = [0, 0, 1, 1]
        weights = [0.2, 0.3, 0.25, 0.25]
        expected = subject.timing.fit_logistic(x, y, weights)
        actual = subject.fit_logistic_vectorized(x, y, weights)
        self.assertAlmostEqual(expected[0], actual[0], places=12)
        for expected_value, actual_value in zip(expected[1], actual[1]):
            self.assertAlmostEqual(expected_value, actual_value, places=12)

    def test_source_has_no_validation_or_economic_input(self):
        source = subject.pathlib.Path(subject.__file__).read_text(encoding="utf-8")
        self.assertNotIn("--validation", source)
        self.assertNotIn("PnL", source)
        self.assertNotIn("validation-sealed/eligible", source)
        self.assertNotIn("exactCurveEconomics", source)


if __name__ == "__main__":
    unittest.main()
