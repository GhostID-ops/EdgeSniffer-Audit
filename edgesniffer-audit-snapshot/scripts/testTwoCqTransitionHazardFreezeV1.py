#!/usr/bin/env python3

import unittest

import twoCqTransitionHazardFreezeV1 as subject


def state(received, label, flow, price, buys):
    features = {name: 0.0 for name in subject.timing.FULL_FEATURES}
    features.update({
        "net_flow_sol_250ms": flow,
        "gross_buy_sol_250ms": max(flow, 0),
        "buy_count_250ms": buys,
        "distance_from_launch_log_price": price,
    })
    return {"receivedNs": received, "signature": str(received), "label": label, "features": features}


class TransitionHazardTests(unittest.TestCase):
    def test_transition_features_are_causal_differences(self):
        example = {"controlN": 1, "states": [
            state(1_000_000, False, 1, 0.1, 1),
            state(3_000_000, True, 4, 0.3, 3),
        ]}
        rows = subject.add_transition_features([example])[0]["states"]
        self.assertEqual(3, rows[1]["features"]["delta_net_flow_sol_250ms"])
        self.assertEqual(2, rows[1]["features"]["delta_buy_count_250ms"])
        self.assertAlmostEqual(subject.math.log1p(2), rows[1]["features"]["transition_log_interarrival_ms"])
        self.assertEqual(1, rows[1]["features"]["transition_new_price_high"])

    def test_family_selection_applies_recall_floor_then_behavioral_order(self):
        def row(recall, exact, lead, top1):
            return {"trigger": {
                "teacherStateRecallPct": recall,
                "firstRisingIsTeacherStatePct": exact,
                "firstRisingLeadMs": {"p75": lead},
            }, "model": {"validation": {"withinMint": {"actualRankedFirstPct": top1}}}}
        rows = {"low-recall": row(70, 99, 1, 99), "a": row(80, 60, 10, 50), "b": row(80, 60, 5, 40)}
        self.assertEqual("b", subject.choose_family(rows))

    def test_source_does_not_accept_validation_or_outcomes(self):
        source = subject.pathlib.Path(subject.__file__).read_text(encoding="utf-8")
        self.assertNotIn("--validation", source)
        self.assertNotIn("validation-sealed/eligible", source)
        self.assertNotIn("PnL", source)


if __name__ == "__main__":
    unittest.main()
