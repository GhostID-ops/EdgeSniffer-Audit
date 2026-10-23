#!/usr/bin/env python3
"""Regression tests for memory-bounded 2CQ cumulative reconciliation."""

from __future__ import annotations

import json
import pathlib
import tempfile

import twoCqCaptureFirstDetectorRepairV1 as capture
import twoCqProspectiveTierAEntryCensusV3 as census


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        first = root / "tx-first.json"
        second = root / "tx-second.json"
        first.write_text(json.dumps({"slot": 1}), encoding="utf-8")
        second.write_text("null", encoding="utf-8")
        store = capture.CachedTransactionStore({"first": first, "second": second})

        assert len(store) == 2
        assert set(store) == {"first", "second"}
        assert store["first"] == {"slot": 1}
        assert store.get("first") == {"slot": 1}
        assert store.get("second") is None
        assert store.get("missing") is None

        first.write_text(json.dumps({"slot": 2}), encoding="utf-8")
        # The one-entry cache was displaced by the second lookup, proving that
        # the store does not retain the full historical transaction population.
        assert store["first"] == {"slot": 2}

        episodes = [
            {"signature": "eligible"},
            {"signature": "excluded"},
            {"signature": "new"},
        ]
        assert census.pending_episode_rows(
            episodes,
            {"eligible"},
            {"excluded": {"eligible": False}},
        ) == [{"signature": "new"}]

        cohort = root / "cohort"
        (cohort / "development").mkdir(parents=True)
        (cohort / "validation-sealed").mkdir(parents=True)
        state = {"eligibleSignatures": [], "lastStatuses": {}}
        eligible = {
            "signature": "new",
            "eligibleOrdinal": 1,
            "split": "DEVELOPMENT",
        }
        status = {"new": {"eligible": True, "reasons": []}}
        census.checkpoint_batch(cohort, state, [eligible], status)
        census.checkpoint_batch(cohort, state, [eligible], status)
        assert len(census.read_jsonl(cohort / "development" / "eligible.jsonl")) == 1
        assert state["eligibleSignatures"] == ["new"]
        assert state["lastStatuses"] == status
        assert census.SUPPORTED_TIER_A_ROUTES == {"PUMP_BONDING_CURVE", "PUMP_CPI"}

        original_transaction = census.tier_a.transaction
        original_block_indexes = census.tier_a.block_transaction_indexes
        fetched: list[str] = []
        indexed: list[int] = []
        try:
            def fake_transaction(url, signature, cache):
                fetched.append(signature)
                return {
                    "slot": 42,
                    "meta": {
                        "err": None,
                        "logMessages": [f"Program {census.tier_a.PUMP_PROGRAM} invoke [1]"],
                    },
                }

            def fake_block_indexes(url, slot, cache):
                indexed.append(slot)
                return {}

            census.tier_a.transaction = fake_transaction
            census.tier_a.block_transaction_indexes = fake_block_indexes
            census.prewarm_finalized_history(
                "rpc",
                [{"signature": "a"}, {"signature": "a"}, {"signature": "b"}],
                root / "cache",
                max_workers=2,
            )
            assert sorted(fetched) == ["a", "b"]
            assert indexed == [42]
        finally:
            census.tier_a.transaction = original_transaction
            census.tier_a.block_transaction_indexes = original_block_indexes

    print("PASS: reconciliation is disk-backed, incremental, and checkpoint-idempotent")


if __name__ == "__main__":
    main()
