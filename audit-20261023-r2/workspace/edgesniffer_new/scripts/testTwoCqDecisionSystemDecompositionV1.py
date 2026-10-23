#!/usr/bin/env python3

import json
import pathlib
import tempfile
import unittest

import twoCqDecisionSystemDecompositionV1 as study
from exactCurveEconomicsV1 import CurveState


STATE_0 = CurveState(30_000_000_000, 1_000_000_000_000_000, 0, 0)
STATE_1 = CurveState(31_000_000_000, 970_000_000_000_000, 0, 0)


def event(signature: str, received_ns: int, user: str = "external") -> study.CausalEvent:
    return study.CausalEvent(
        mint="mint", signature=signature, slot=1, transaction_index=1,
        instruction_index=0, received_ns=received_ns, is_buy=True, user=user,
        creator="creator", quote_lamports=1_000_000_000, token_raw=1,
        pre_state=STATE_0, post_state=STATE_1,
    )


class DecisionSystemTests(unittest.TestCase):
    def test_development_requires_exact_frozen_n(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "development.jsonl"
            path.write_text(json.dumps({"eligibleOrdinal": 1, "split": "DEVELOPMENT", "outcomesRead": 0}) + "\n")
            with self.assertRaisesRegex(RuntimeError, "DEVELOPMENT_NOT_FROZEN_N350"):
                study.load_development(path)

    def test_internal_split_drops_repeat_mint_from_assessment(self):
        rows = [
            {"eligibleOrdinal": i, "mint": f"mint-{i}", "states": []}
            for i in range(1, 351)
        ]
        rows[-1]["mint"] = "mint-1"
        train, assessment, dropped = study.internal_split(rows)
        self.assertEqual(245, len(train))
        self.assertEqual(104, len(assessment))
        self.assertEqual(1, dropped)

    def test_snapshot_excludes_teacher_from_external_flow(self):
        external = event("external", 1_000_000_000)
        teacher = event("teacher", 1_100_000_000, study.WALLET)
        features = study.state_features(
            STATE_1, 1_200_000_000, "mint", "creator", [external, teacher], 900_000_000
        )
        self.assertEqual(1.0, features["buy_count_250ms"])
        self.assertEqual(1.0, features["gross_buy_sol_250ms"])

    def test_post_event_control_uses_post_state(self):
        control = event("external", 1_000_000_000)
        features = study.state_features(
            control.post_state, control.received_ns, "mint", "creator", [control], 900_000_000
        )
        expected_mc = study.timing.safe_log((STATE_1.virtual_sol / STATE_1.virtual_token) * 1_000_000)
        self.assertAlmostEqual(expected_mc, features["log_market_cap_sol"])
        self.assertEqual(1.0, features["buy_count_250ms"])

    def test_contract_has_no_validation_input(self):
        source = pathlib.Path(study.__file__).read_text(encoding="utf-8")
        self.assertNotIn("validation-sealed/eligible", source)
        self.assertNotIn("--validation", source)

    def test_fit_control_sample_is_bounded_and_keeps_endpoints(self):
        controls = [{"label": False, "index": index} for index in range(100)]
        sampled = study.fit_control_sample({"controlN": 100, "states": controls + [{"label": True}]})
        selected = [row["index"] for row in sampled["states"] if not row["label"]]
        self.assertEqual(study.MAX_FIT_CONTROLS_PER_DECISION, len(selected))
        self.assertEqual(0, selected[0])
        self.assertEqual(99, selected[-1])

    def test_cached_transaction_uses_v3_wallet_cache_for_entry(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            tier_cache = root / "tier-a-rpc-cache"
            wallet_cache = root / "wallet-rpc-cache"
            tier_cache.mkdir()
            wallet_cache.mkdir()
            (wallet_cache / "tx-entry.json").write_text(
                json.dumps({"slot": 42}), encoding="utf-8"
            )

            self.assertEqual(42, study.cached_transaction(tier_cache, "entry")["slot"])

    def test_temporal_example_contains_conviction_size_tier(self):
        external = event("external", 1_000_000_000)
        teacher = event("teacher", 1_100_000_000, study.WALLET)
        teacher = study.CausalEvent(
            **{**teacher.__dict__, "quote_lamports": 2_000_000_000}
        )
        development = [{
            "eligibleOrdinal": 1, "mint": "mint", "signature": "teacher",
            "route": "PUMP_BONDING_CURVE", "acquisitionClass": "NEW_FROM_FLAT",
        }]
        examples, tape = study.build_temporal_examples(
            development, {"mint": [external, teacher]}, {"mint": 900_000_000},
            {"teacher": teacher},
        )

        self.assertEqual(study.timing.size_tier(2.0), examples[0]["sizeTier"])
        selected = next(row for row in examples[0]["states"] if row["label"])
        self.assertEqual(external.received_ns, selected["receivedNs"])
        self.assertEqual(external.received_ns, tape[0]["decisionStateReceivedAtUnixNs"])
        self.assertEqual(0, examples[0]["controlN"])

    def test_targeted_arrival_fallback_keeps_earliest_match(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            journal = root / "arrivals.bin"
            wanted = bytes(range(32))
            other = bytes(reversed(range(32)))
            journal.write_bytes(
                study.tier_a.COMPACT_MAGIC
                + study.tier_a.COMPACT_RECORD.pack(wanted, 200, 2)
                + study.tier_a.COMPACT_RECORD.pack(other, 100, 1)
                + study.tier_a.COMPACT_RECORD.pack(wanted, 150, 1)
            )
            prior_filter = study.COMPACT_FILTER
            study.COMPACT_FILTER = root / "missing-filter"
            try:
                arrivals = study.targeted_compact_arrivals(journal, {wanted})
            finally:
                study.COMPACT_FILTER = prior_filter

            self.assertEqual({wanted: (150, 1)}, arrivals)


if __name__ == "__main__":
    unittest.main()
