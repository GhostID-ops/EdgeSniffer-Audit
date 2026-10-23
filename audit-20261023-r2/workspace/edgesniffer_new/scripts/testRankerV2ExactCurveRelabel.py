#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest
from types import SimpleNamespace


ROOT = pathlib.Path(__file__).resolve().parent
EVALUATOR = ROOT / "exactCurveEconomicsV1.py"
if not EVALUATOR.exists():
    EVALUATOR = pathlib.Path(
        "/mnt/HC_Volume_106775198/edgesniffer-derived/"
        "exact-curve-economics-v1/validated-v1/exactCurveEconomicsV1.py"
    )


def load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ev = load("exact_curve_test", EVALUATOR)
ranker = load("ranker_exact_test", ROOT / "rankerV2ExactCurveRelabel.py")


class RankerExactCurveRelabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = ev.CurveState(
            virtual_sol=30_000_000_000,
            virtual_token=1_073_000_000_000_000,
            real_sol=20_000_000_000,
            real_token=793_100_000_000_000,
        )
        self.fees = ev.Fees(protocol_bps=100, creator_bps=50, lp_bps=0)
        self.config = ev.FeeConfig("fee", self.fees, (), ())

    def test_frozen_evaluator_hash(self) -> None:
        self.assertEqual(
            ranker.sha256_path(EVALUATOR),
            ranker.EXPECTED_EVALUATOR_SHA256,
        )

    def test_buy_impact_is_nonlinear_with_size(self) -> None:
        small = ev.quote_exact_quote_in_buy(self.state, 10_000_000, self.fees)
        large = ev.quote_exact_quote_in_buy(self.state, 1_000_000_000, self.fees)
        small_no_impact = ranker.marginal_entry_tokens(self.state, small.pricing_quote_in)
        large_no_impact = ranker.marginal_entry_tokens(self.state, large.pricing_quote_in)
        small_impact = small_no_impact / small.token_out - 1
        large_impact = large_no_impact / large.token_out - 1
        self.assertGreater(large_impact, small_impact)

    def test_rpc_confirmed_null_is_distinct_from_transient_error(self) -> None:
        class FakeRpc:
            _next_id = 1

            def __init__(self) -> None:
                self.calls = 0

            def _post(self, payload):
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("temporary")
                return {"jsonrpc": "2.0", "id": payload["id"], "result": None}

        rpc = FakeRpc()
        rows, retries = ranker.rpc_calls_exact_with_retries(rpc, [("getTransaction", ["sig"])])
        self.assertEqual(rows[0][0], "confirmed_null")
        self.assertEqual(retries["transport_error"], 1)

    def test_invalid_supply_parameter_is_explicit_not_transient(self) -> None:
        class FakeRpc:
            _next_id = 1

            def _post(self, payload):
                return {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "error": {"code": -32602, "message": "not a Token mint"},
                }

        rows, retries = ranker.rpc_calls_exact_with_retries(
            FakeRpc(), [("getTokenSupply", ["bad-mint"])], allow_invalid_param=True
        )
        self.assertEqual(rows[0][0], "deterministic_rpc_error")
        self.assertEqual(sum(retries.values()), 0)

    def test_marginal_sell_ablation_does_not_mutate_curve(self) -> None:
        quote = ranker.marginal_sell_quote(
            ev, self.state, 1_000_000_000, self.fees, 0, 0
        )
        self.assertEqual(quote.post_state, self.state)
        self.assertEqual(quote.token_in, 1_000_000_000)

    def test_external_transaction_is_atomic_on_constraint_failure(self) -> None:
        first_ix = ev.PumpInstruction("buy_exact_sol_in", 10_000_000, 1, True)
        second_ix = ev.PumpInstruction("buy_exact_sol_in", 10_000_000, 2**63, True)
        first_quote = ev.apply_instruction(self.state, first_ix, self.fees)
        event = SimpleNamespace(
            creator_fee_basis_points=50,
            cashback_fee_basis_points=0,
            buyback_fee_basis_points=0,
            token_amount=first_quote.token_out,
            sol_amount=first_quote.curve_quote_in,
        )
        rows = [
            {"source": SimpleNamespace(instruction=first_ix, signature="tx"), "event": event},
            {"source": SimpleNamespace(instruction=second_ix, signature="tx"), "event": event},
        ]
        with self.assertRaises(Exception):
            ranker.apply_external_transaction(
                ev, rows, self.state, self.config, 1_000_000_000_000_000,
                honor_constraints=True,
            )
        self.assertEqual(self.state.virtual_sol, 30_000_000_000)

    def test_lifecycle_uses_latest_state_at_first_sellable(self) -> None:
        buy = ev.quote_exact_quote_in_buy(self.state, 10_000_000, self.fees)
        pump = ev.quote_exact_quote_in_buy(buy.post_state, 10_000_000_000, self.fees)
        replay = {
            "buy": buy,
            "inventory": buy.token_out,
            "snapshots": [
                ranker.StateSnapshot(1_000, 1_000, buy.post_state, 50, 0, 0, 1, "entry", "exact"),
                ranker.StateSnapshot(1_100, 1_100, pump.post_state, 50, 0, 0, 2, "pump", "exact"),
            ],
        }
        outcome = ranker.lifecycle_outcome(ev, replay, 250, self.config, 1_000_000_000_000_000)
        self.assertEqual(outcome["outcome"], "TP")
        self.assertEqual(outcome["exit_ms"], 1_250)

    def test_max_open_one_is_chronological(self) -> None:
        signals = [
            ranker.Signal("a", "historical", "mint-a", 1_000, 0.6, "state", "train", None, None),
            ranker.Signal("b", "historical", "mint-b", 1_010, 0.6, "state", "train", None, None),
        ]
        outcomes = []
        for signal in signals:
            outcomes.append(
                {
                    "signal_id": signal.signal_id,
                    "size_lamports": 10_000_000,
                    "entry_delay_ms": 25,
                    "first_sellable_delay_ms": 250,
                    "exit_ms": 1_200 if signal.signal_id == "a" else 1_300,
                    "pnl_lamports": 1,
                    "return_pct": 1.0,
                    "outcome": "TRUE_120S_MAX_HOLD",
                    "entry_curve_impact_pct": 0.0,
                    "exit_curve_impact_pct": 0.0,
                    "fee_burden_pct_of_size": 0.0,
                    "external_transactions": 0,
                    "external_status_flips": 0,
                }
            )
        result = ranker.max_open_one(signals, outcomes, [])
        row = next(
            item for item in result
            if item["population"] == "train"
            and item["size_lamports"] == 10_000_000
            and item["entry_delay_ms"] == 25
            and item["first_sellable_delay_ms"] == 250
        )
        self.assertEqual(row["accepted"], 1)
        self.assertEqual(row["capacity_rejected"], 1)


if __name__ == "__main__":
    unittest.main()
