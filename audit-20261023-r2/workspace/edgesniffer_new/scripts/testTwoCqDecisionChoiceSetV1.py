#!/usr/bin/env python3

import datetime as dt
import pathlib
import unittest

import twoCqDecisionChoiceSetV1 as choices
import twoCqDecisionSystemDecompositionV1 as decision
from exactCurveEconomicsV1 import CurveState


STATE = CurveState(30_000_000_000, 1_000_000_000_000_000, 1, 1)


def event(mint: str, ns: int, user: str = "external", is_buy: bool = True) -> decision.CausalEvent:
    return decision.CausalEvent(
        mint=mint, signature=f"{mint}-{ns}", slot=1, transaction_index=1,
        instruction_index=0, received_ns=ns, is_buy=is_buy, user=user,
        creator="creator", quote_lamports=1_000_000_000, token_raw=10,
        pre_state=STATE, post_state=STATE,
    )


class ChoiceSetTests(unittest.TestCase):
    def test_decision_window_includes_trigger_boundary(self):
        boundaries = [1_000_000_000, 2_000_000_000]
        self.assertTrue(choices.inside_decision_window(999_000_000, boundaries))
        self.assertTrue(choices.inside_decision_window(2_000_000_000, boundaries))

    def test_receive_hours_start_at_containing_utc_hour(self):
        start = int(dt.datetime(2026, 9, 10, 4, tzinfo=dt.timezone.utc).timestamp() * 1e9)
        end = int(dt.datetime(2026, 9, 10, 6, tzinfo=dt.timezone.utc).timestamp() * 1e9)
        hours = choices.receive_hours(start, end)
        self.assertEqual("2026/09/10/04", hours[0])
        self.assertEqual("2026/09/10/06", hours[-1])

    def test_teacher_exposure_tracks_buys_and_sells(self):
        rows = [event("m", 1, decision.WALLET, True), event("m", 2, decision.WALLET, False)]
        self.assertEqual(0, choices.teacher_exposure_raw(rows))

    def test_source_never_accepts_validation(self):
        source = pathlib.Path(choices.__file__).read_text(encoding="utf-8")
        self.assertNotIn("--validation", source)
        self.assertNotIn("validation-sealed/eligible", source)


if __name__ == "__main__":
    unittest.main()
