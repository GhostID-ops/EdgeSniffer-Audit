#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runIndependentStrategyLabV1 import evaluate, fee_for
from materializeIndependentStrategyWindowsV1 import materialize, materialize_stream


def s(vs: int, vt: int) -> dict[str, int]:
    return {"virtual_sol": vs, "virtual_token": vt, "real_sol": vs, "real_token": vt}


class IndependentStrategyLabTests(unittest.TestCase):
    def test_fee_for_respects_explicit_split_without_double_counting(self):
        explicit = fee_for({
            "fee_basis_points": 125,
            "protocol_fee_basis_points": 95,
            "creator_fee_basis_points": 30,
        }, 0)
        self.assertEqual(explicit.protocol_bps, 95)
        self.assertEqual(explicit.creator_bps, 30)
        self.assertEqual(explicit.total_bps, 125)

        aggregate = fee_for({"fee_basis_points": 125, "creator_fee_basis_points": 30}, 0)
        self.assertEqual(aggregate.protocol_bps, 95)
        self.assertEqual(aggregate.creator_bps, 30)
        self.assertEqual(aggregate.total_bps, 125)

    def test_materializer_does_not_use_teacher_match_as_trigger(self):
        rows = [{
            "mint": "mint-a", "route": "PUMP_CURVE", "side": "buy", "local_receive_ts_ms": 1000,
            "raw_record": {"teacherWalletMatched": True}, "canonical_state": {"market_cap_usd": 5000},
            "state": s(2_000_000_000, 1_000_000_000), "quote_sol": 0.5,
        }, {
            "mint": "mint-a", "route": "PUMP_CURVE", "side": "buy", "local_receive_ts_ms": 1500,
            "state": s(2_100_000_000, 1_000_000_000), "quote_sol": 0.2,
        }, {
            "mint": "mint-a", "route": "PUMP_CURVE", "side": "sell", "local_receive_ts_ms": 7000,
            "state": s(2_300_000_000, 1_000_000_000), "quote_sol": 0.1,
        }]
        windows, rejected = materialize(rows)
        self.assertEqual(len(windows), 1)
        self.assertTrue(windows[0]["teacher_wallet_match"])
        self.assertEqual(rejected, {})

    def test_native_materializer_rejects_incomplete_replay_mint(self):
        rows = [{
            "mint": "mint-a", "route": "PUMP_CURVE", "side": "buy", "local_receive_ts_ms": 1000,
            "canonical_state": {"route": "PUMP_CURVE"}, "native_economics_state_complete": False,
        }, {
            "mint": "mint-a", "route": "PUMP_CURVE", "side": "buy", "local_receive_ts_ms": 1500,
            "canonical_state": {"route": "PUMP_CURVE"}, "native_economics_state_complete": True,
        }]
        windows, rejected = materialize(rows, require_native_economics_state=True)
        self.assertEqual(windows, [])
        self.assertEqual(rejected["native_economics_state_incomplete"], 1)

    def test_native_materializer_accepts_complete_replay_mint(self):
        rows = [{
            "mint": "mint-a", "route": "PUMP_CURVE", "side": "buy", "local_receive_ts_ms": 1000,
            "canonical_state": {"route": "PUMP_CURVE"}, "native_economics_state_complete": True,
            "fee_basis_points": 125, "protocol_fee_basis_points": 95, "creator_fee_basis_points": 30,
            "raw_record": {"quoteLamports": 100_000_000},
            "state": s(2_000_000_000, 1_000_000_000),
        }, {
            "mint": "mint-a", "route": "PUMP_CURVE", "side": "buy", "local_receive_ts_ms": 1500,
            "canonical_state": {"route": "PUMP_CURVE"}, "native_economics_state_complete": True,
            "fee_basis_points": 125, "protocol_fee_basis_points": 95, "creator_fee_basis_points": 30,
            "state": s(2_100_000_000, 1_000_000_000),
        }, {
            "mint": "mint-a", "route": "PUMP_CURVE", "side": "sell", "local_receive_ts_ms": 7000,
            "canonical_state": {"route": "PUMP_CURVE"}, "native_economics_state_complete": True,
            "fee_basis_points": 125, "protocol_fee_basis_points": 95, "creator_fee_basis_points": 30,
            "state": s(2_300_000_000, 1_000_000_000),
        }]
        windows, rejected = materialize(rows, require_native_economics_state=True)
        self.assertEqual(len(windows), 1)
        self.assertEqual(rejected, {})
        self.assertTrue(windows[0]["lineage"]["native_economics_state"])

    def test_native_materializer_rejects_claimed_complete_row_without_fee_fields(self):
        rows = [{
            "mint": "mint-a", "route": "PUMP_CURVE", "side": "buy", "local_receive_ts_ms": 1000,
            "canonical_state": {"route": "PUMP_CURVE"}, "native_economics_state_complete": True,
            "state": s(2_000_000_000, 1_000_000_000),
        }]
        windows, rejected = materialize(rows, require_native_economics_state=True)
        self.assertEqual(windows, [])
        self.assertEqual(rejected["native_economics_state_incomplete"], 1)

    def test_missing_state_is_not_imputed(self):
        rows = [{"record_type": "INDEPENDENT_MARKET_WINDOW", "mint": "mint-a", "route": "PUMP_CURVE",
                 "signal_receive_ts_ms": 1000, "entry_state": None, "marks": []}]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "windows.jsonl"
            source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            result = evaluate(source)
        self.assertEqual(result["admitted_windows"], 1)
        self.assertEqual(result["outcomes"], [])
        self.assertTrue(result["promotion_blocked"])

    def test_surface_is_three_sizes_and_fixed_exits(self):
        base = {
            "record_type": "INDEPENDENT_MARKET_WINDOW", "mint": "mint-a", "route": "PUMP_CURVE",
            "signal_receive_ts_ms": 1000, "entry_state": s(2_000_000_000, 1_000_000_000),
            "market_cap_usd": 5000, "effective_quote_reserve_sol": 2.0,
            "net_flow_sol": 0.5, "price_change_bps": 5.0,
            "marks": [{"receive_ts_ms": 7000, "state": s(2_300_000_000, 1_000_000_000),
                       "fee_basis_points": 100, "creator_fee_basis_points": 0}],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "windows.jsonl"
            source.write_text(json.dumps(base) + "\n", encoding="utf-8")
            result = evaluate(source)
        self.assertEqual(result["admitted_windows"], 1)
        self.assertGreater(len(result["outcomes"]), 0)
        self.assertFalse(result["policy_frozen"])
        self.assertTrue(result["promotion_blocked"])
        self.assertTrue(any("FIXED_5S" in row["strategy_id"] for row in result["surface"]))
        self.assertEqual(set(result["notionals_sol"]), {0.1, 0.5, 1.0})

    def test_immature_horizon_is_not_counted_as_failed_exit(self):
        base = {
            "record_type": "INDEPENDENT_MARKET_WINDOW", "mint": "mint-a", "route": "PUMP_CURVE",
            "signal_receive_ts_ms": 1000, "observation_as_of_ts_ms": 5000,
            "entry_state": s(2_000_000_000, 1_000_000_000), "market_cap_usd": 5000,
            "effective_quote_reserve_sol": 2.0, "net_flow_sol": 0.5, "price_change_bps": 5.0,
            "marks": [{"receive_ts_ms": 3000, "state": s(2_100_000_000, 1_000_000_000),
                       "fee_basis_points": 100, "creator_fee_basis_points": 0}],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "windows.jsonl"
            source.write_text(json.dumps(base) + "\n", encoding="utf-8")
            result = evaluate(source)
        fixed = next(x for x in result["outcomes"] if "FIXED_5S|1|stress0" in x["strategy_id"])
        self.assertEqual(fixed["status"], "IMMATURE_WINDOW")
        metrics = next(x["metrics"] for x in result["surface"] if x["strategy_id"] == "ALL_EXACT_ROUTE|FIXED_5S|1|stress0")
        self.assertEqual(metrics["immature"], 1)
        self.assertEqual(metrics["missed_or_failed"], 0)

    def test_carry_forward_is_separate_from_strict_fixed_exit(self):
        base = {
            "record_type": "INDEPENDENT_MARKET_WINDOW", "mint": "mint-a", "route": "PUMP_CURVE",
            "signal_receive_ts_ms": 1000, "observation_as_of_ts_ms": 9000,
            "entry_state": s(2_000_000_000, 1_000_000_000), "market_cap_usd": 5000,
            "effective_quote_reserve_sol": 2.0, "net_flow_sol": 0.5, "price_change_bps": 5.0,
            "marks": [{"receive_ts_ms": 3000, "state": s(2_100_000_000, 1_000_000_000),
                       "fee_basis_points": 100, "creator_fee_basis_points": 0}],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "windows.jsonl"
            source.write_text(json.dumps(base) + "\n", encoding="utf-8")
            result = evaluate(source)
        strict = next(x for x in result["outcomes"] if "FIXED_5S|1|stress0" in x["strategy_id"])
        carry = next(x for x in result["outcomes"] if "CARRY_5S|1|stress0" in x["strategy_id"])
        self.assertEqual(strict["status"], "MISSED_EXIT_MARK")
        self.assertEqual(carry["status"], "ACTIONABLE")
        self.assertEqual(carry["exit_reason"], "CARRY_FORWARD_LAST_NATIVE_STATE")

    def test_native_receipt_boundary_mode_rejects_missing_lineage(self):
        base = {
            "record_type": "INDEPENDENT_MARKET_WINDOW", "mint": "mint-a", "route": "PUMP_CURVE",
            "signal_receive_ts_ms": 1000, "entry_state": s(2_000_000_000, 1_000_000_000),
            "market_cap_usd": 5000, "effective_quote_reserve_sol": 2.0,
            "net_flow_sol": 0.5, "price_change_bps": 5.0,
            "marks": [{"receive_ts_ms": 7000, "state": s(2_300_000_000, 1_000_000_000),
                       "fee_basis_points": 100, "creator_fee_basis_points": 0}],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "windows.jsonl"
            source.write_text(json.dumps(base) + "\n", encoding="utf-8")
            result = evaluate(source, require_native_receipt_boundary=True)
        self.assertEqual(result["admitted_windows"], 0)
        self.assertEqual(result["boundary_rejections"], 1)
        self.assertEqual(result["causal_boundary_mode"], "NATIVE_RECEIPT_PLUS_1000MS")

    def test_native_receipt_boundary_mode_accepts_explicit_lineage(self):
        base = {
            "record_type": "INDEPENDENT_MARKET_WINDOW", "mint": "mint-a", "route": "PUMP_CURVE",
            "signal_receive_ts_ms": 1000, "entry_state": s(2_000_000_000, 1_000_000_000),
            "lineage": {"source_receive_clock": True, "entry_state_source": "native-observation-1"},
            "market_cap_usd": 5000, "effective_quote_reserve_sol": 2.0,
            "net_flow_sol": 0.5, "price_change_bps": 5.0,
            "marks": [{"receive_ts_ms": 7000, "state": s(2_300_000_000, 1_000_000_000),
                       "fee_basis_points": 100, "creator_fee_basis_points": 0}],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "windows.jsonl"
            source.write_text(json.dumps(base) + "\n", encoding="utf-8")
            result = evaluate(source, require_native_receipt_boundary=True)
        self.assertEqual(result["admitted_windows"], 1)
        self.assertEqual(result["boundary_rejections"], 0)

    def test_streaming_materializer_uses_route_identity_but_requires_state(self):
        rows = [
            {"mint": "mint-a", "side": "buy", "local_receive_ts_ms": 1000,
             "raw_record": {"kind": "teacherImpulseNativeObservation", "bondingCurve": "curve-a", "sourceIxAccounts": ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"]},
             "state": s(2_000_000_000, 1_000_000_000)},
            {"mint": "mint-a", "side": "buy", "local_receive_ts_ms": 1500,
             "raw_record": {"kind": "teacherImpulseNativeObservation", "bondingCurve": "curve-a", "sourceIxAccounts": ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"]},
             "state": s(2_100_000_000, 1_000_000_000)},
            {"mint": "mint-a", "side": "sell", "local_receive_ts_ms": 7000,
             "raw_record": {"kind": "teacherImpulseNativeObservation", "bondingCurve": "curve-a", "sourceIxAccounts": ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"]},
             "state": s(2_300_000_000, 1_000_000_000)},
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            output = Path(directory) / "windows.jsonl"
            source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            count, rejected = materialize_stream(source, output)
            self.assertEqual(count, 1)
            self.assertEqual(rejected, {})
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
