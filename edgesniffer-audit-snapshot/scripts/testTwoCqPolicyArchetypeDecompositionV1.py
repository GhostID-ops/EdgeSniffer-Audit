#!/usr/bin/env python3

import unittest

import twoCqPolicyArchetypeDecompositionV1 as study


def row(sell_fractions, hold=3):
    total = 100_000
    amounts = [round(total * fraction) for fraction in sell_fractions[:-1]]
    amounts.append(total - sum(amounts))
    return {
        "sellCount": len(amounts),
        "openedTimestamp": 100,
        "actions": [
            {"kind": "ECONOMIC_SELL", "tokenDeltaRaw": -amount, "timestamp": 100 + hold}
            for amount in amounts
        ],
    }


class ArchetypeTests(unittest.TestCase):
    def test_single_exit(self):
        self.assertEqual(study.exit_archetype(row([1.0])), "RAPID_SINGLE_FULL_EXIT")

    def test_two_stage_modes(self):
        self.assertEqual(study.exit_archetype(row([0.4, 0.6])), "TWO_STAGE_BACK_LOADED_35_40_FIRST")
        self.assertEqual(study.exit_archetype(row([0.6, 0.4])), "TWO_STAGE_FRONT_LOADED_60_FIRST")

    def test_six_step_template(self):
        self.assertEqual(
            study.exit_archetype(row([0.30, 0.17, 0.17, 0.17, 0.17, 0.02])),
            "SIX_STEP_30_17_RESIDUAL_LADDER",
        )

    def test_size_boundary_assignment(self):
        boundaries = [1.5, 2.5, 4.0]
        self.assertEqual(study.size_band_index(1.2, boundaries), 0)
        self.assertEqual(study.size_band_index(1.5, boundaries), 1)
        self.assertEqual(study.size_band_index(8.0, boundaries), 3)


if __name__ == "__main__":
    unittest.main()
