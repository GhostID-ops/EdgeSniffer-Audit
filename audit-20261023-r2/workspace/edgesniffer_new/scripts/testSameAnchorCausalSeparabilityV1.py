#!/usr/bin/env python3

import types
import unittest

from exactCurveEconomicsV1 import CurveState
from sameAnchorCausalSeparabilityV1 import (
    add_trailing_context,
    apply_bh,
    auc_and_pvalue,
    block_for_ordinal,
    extract_features,
    gross_external_distance_to_return,
    labels_from_anchor,
)


def fee_event(**overrides):
    values = {
        "fee_basis_points": 100,
        "creator_fee_basis_points": 50,
        "cashback_fee_basis_points": 0,
        "buyback_fee_basis_points": 0,
        "creator_fee": 1,
        "cashback": 0,
    }
    values.update(overrides)
    return types.SimpleNamespace(**values)


def event_row(arrival, signature, is_buy, amount, user, state, *, evidence="e", tx_index=1):
    event = fee_event()
    event.quote_mint = "11111111111111111111111111111111"
    event.mayhem_mode = False
    event.user = user
    event.creator = "creator"
    event.is_buy = is_buy
    event.immediate_quote_amount = amount
    event.pre_state = state
    event.post_state = state
    return types.SimpleNamespace(
        arrival_ms=arrival,
        slot=1,
        tx_index=tx_index,
        instruction_index=0,
        signature=signature,
        observation_evidence_id=evidence,
        canonical_evidence_id="canonical-" + evidence,
        event=event,
    )


class SameAnchorCausalSeparabilityTests(unittest.TestCase):
    def test_auc_handles_ties(self):
        auc, pvalue = auc_and_pvalue([1, 1, 2, 3], [False, True, True, True])
        self.assertAlmostEqual(5 / 6, auc)
        self.assertIsNotNone(pvalue)

    def test_bh_is_monotone(self):
        rows = [
            {"mannWhitneyTwoSidedP": 0.01, "benjaminiHochbergQ": None},
            {"mannWhitneyTwoSidedP": 0.04, "benjaminiHochbergQ": None},
            {"mannWhitneyTwoSidedP": 0.03, "benjaminiHochbergQ": None},
        ]
        apply_bh(rows)
        ordered = sorted(rows, key=lambda row: row["mannWhitneyTwoSidedP"])
        self.assertLessEqual(ordered[0]["benjaminiHochbergQ"], ordered[1]["benjaminiHochbergQ"])
        self.assertLessEqual(ordered[1]["benjaminiHochbergQ"], ordered[2]["benjaminiHochbergQ"])

    def test_trailing_context_never_uses_same_timestamp(self):
        rows = []
        for index, timestamp in enumerate([1000, 2000, 2000]):
            rows.append({
                "anchor": "A",
                "mint": str(index),
                "decisionUnixMs": timestamp,
                "features": {
                    "mint_age_ms": float(10 + index),
                    "unique_external_buyers": float(index + 1),
                    "successful_transaction_count": float(index + 1),
                    "actual_cumulative_external_gross_buy_sol": float(index + 1),
                    "trailing15m_anchor_speed_percentile": None,
                    "trailing15m_unique_buyers_percentile": None,
                    "trailing15m_tx_intensity_percentile": None,
                    "trailing15m_external_flow_percentile": None,
                },
            })
        add_trailing_context(rows, 10_000, 1)
        self.assertEqual(1.0, rows[1]["features"]["trailing15m_unique_buyers_percentile"])
        self.assertEqual(1.0, rows[2]["features"]["trailing15m_unique_buyers_percentile"])

    def test_post_decision_event_is_excluded(self):
        state = CurveState(30_000_000_000, 1_000_000_000_000_000, 10_000_000_000, 800_000_000_000_000)
        events = [
            event_row(1000, "trigger", True, 2_100_000_000, "buyer1", state),
            event_row(1001, "future", False, 100_000_000, "buyer2", state),
        ]
        anchor = {
            "decisionUnixMs": 1000,
            "mintAgeMs": 100,
            "triggerSignature": "trigger",
            "triggerSlot": 1,
            "anchorMetadata": {
                "overshootSol": 0.1,
                "decisionState": {
                    "virtualSol": state.virtual_sol,
                    "virtualToken": state.virtual_token,
                    "realSol": state.real_sol,
                    "realToken": state.real_token,
                },
            },
        }
        features, audit = extract_features(anchor, events, 0.001)
        self.assertEqual(1.0, features["external_buy_event_count"])
        self.assertEqual(0.0, features["external_sell_event_count"])
        self.assertEqual(1000, audit["maxSourceArrivalMs"])
        self.assertTrue(audit["causalAvailabilityPass"])

    def test_same_timestamp_post_trigger_transaction_is_excluded(self):
        state = CurveState(30_000_000_000, 1_000_000_000_000_000, 10_000_000_000, 800_000_000_000_000)
        events = [
            event_row(1000, "before", True, 500_000_000, "buyer0", state, tx_index=0),
            event_row(1000, "trigger", True, 1_600_000_000, "buyer1", state, tx_index=1),
            event_row(1000, "future-same-ms", True, 50_000_000_000, "buyer2", state, tx_index=2),
        ]
        anchor = {
            "decisionUnixMs": 1000,
            "mintAgeMs": 100,
            "triggerSignature": "trigger",
            "triggerSlot": 1,
            "anchorMetadata": {
                "overshootSol": 0.1,
                "decisionState": {
                    "virtualSol": state.virtual_sol,
                    "virtualToken": state.virtual_token,
                    "realSol": state.real_sol,
                    "realToken": state.real_token,
                },
            },
        }
        features, audit = extract_features(anchor, events, 0.001)
        self.assertEqual(2.0, features["external_buy_event_count"])
        self.assertAlmostEqual(2.1, features["actual_cumulative_external_gross_buy_sol"])
        self.assertEqual(1, audit["sameTimestampPostTriggerRowsExcluded"])
        self.assertTrue(audit["causalAvailabilityPass"])

    def test_delivery_overlay_is_applied_to_policy_label(self):
        anchor = {
            "cells": {"P50|0.5": {
                "status": "EXACT",
                "counterfactualFailedTransactions": 0,
                "policies": {"TP20_SL20_MAX120": {"status": "EXACT", "returnPct": 10.1}},
                "opportunity": {"0.0010": {"thresholds": {"10": {"reached": True}}}},
            }}
        }
        labels = labels_from_anchor(anchor, 0.001)
        self.assertFalse(labels["POLICY_GE_10"])
        self.assertTrue(labels["POLICY_POSITIVE"])
        self.assertTrue(labels["OPPORTUNITY_GE_10"])

    def test_exact_distance_increases_with_target(self):
        state = CurveState(30_500_000_000, 990_000_000_000_000, 10_500_000_000, 790_000_000_000_000)
        event = fee_event()
        from exactCurveEconomicsV1 import quote_exact_quote_in_buy
        from validatePumpExactEvidenceLedgerV1 import event_fees
        own = quote_exact_quote_in_buy(state, 500_000_000, event_fees(event))
        five = gross_external_distance_to_return(own.post_state, own.token_out, event, 5, 0.001)
        ten = gross_external_distance_to_return(own.post_state, own.token_out, event, 10, 0.001)
        self.assertIsNotNone(five)
        self.assertIsNotNone(ten)
        self.assertGreaterEqual(ten, five)

    def test_chronological_boundaries(self):
        blocks = [[1, 1370], [1371, 2740], [2741, 4110], [4111, 5480], [5481, 6848]]
        self.assertEqual(1, block_for_ordinal(1, blocks))
        self.assertEqual(1, block_for_ordinal(1370, blocks))
        self.assertEqual(2, block_for_ordinal(1371, blocks))
        self.assertEqual(5, block_for_ordinal(6848, blocks))
        self.assertIsNone(block_for_ordinal(6849, blocks))


if __name__ == "__main__":
    unittest.main()
