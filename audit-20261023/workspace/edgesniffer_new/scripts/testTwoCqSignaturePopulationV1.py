#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest import mock

import twoCqTrueActivityDenominatorAuditV1 as subject


def row(signature: str, slot: int, block_time: int) -> dict:
    return {"signature": signature, "slot": slot, "blockTime": block_time}


class SignaturePopulationTests(unittest.TestCase):
    def test_boundary_reached_normally(self) -> None:
        with mock.patch.object(subject, "rpc", return_value=[row("a", 2, 20), row("b", 1, 9)]):
            self.assertEqual([x["signature"] for x in subject.fetch_signatures("rpc", 10)], ["b", "a"])

    def test_multiple_pages_and_cursor_progression(self) -> None:
        batches = [[row("c", 3, 30), row("b", 2, 20)], [row("a", 1, 9)]]
        with mock.patch.object(subject, "rpc", side_effect=batches) as rpc:
            rows = subject.fetch_signatures("rpc", 10)
        self.assertEqual([x["signature"] for x in rows], ["a", "b", "c"])
        self.assertEqual(rpc.call_args_list[1].args[2][1]["before"], "b")

    def test_remote_history_without_boundary_fails_closed(self) -> None:
        with mock.patch.object(subject, "rpc", side_effect=[[row("a", 2, 20)], []]):
            with self.assertRaisesRegex(RuntimeError, "BOUNDARY_NOT_REACHED"):
                subject.fetch_signatures("rpc", 10)

    def test_cursor_repetition_fails_closed(self) -> None:
        with mock.patch.object(subject, "rpc", side_effect=[[row("a", 2, 20)], [row("a", 2, 20)]]):
            with self.assertRaisesRegex(RuntimeError, "CURSOR_REPEATED"):
                subject.fetch_signatures("rpc", 10)

    def test_persisted_baseline_appends_new_rows_and_preserves_order(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            ledger = pathlib.Path(raw) / "rows.jsonl.gz"
            subject.write_signature_ledger(ledger, [row("a", 1, 10), row("b", 2, 20)], 10, {"source": "test"})
            batch = [row("d", 4, 40), row("c", 3, 30), row("b", 2, 20)]
            with mock.patch.object(subject, "rpc", return_value=batch):
                rows = subject.fetch_signatures_incremental("rpc", 10, ledger)
            self.assertEqual([x["signature"] for x in rows], ["a", "b", "c", "d"])
            loaded, manifest = subject.load_signature_ledger(ledger, 10)
            self.assertEqual(loaded, rows)
            self.assertEqual(manifest["rowN"], 4)
            self.assertEqual(manifest["outcomesRead"], 0)

    def test_duplicate_signatures_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            ledger = pathlib.Path(raw) / "rows.jsonl.gz"
            subject.write_signature_ledger(ledger, [row("a", 1, 10), row("a", 1, 10)], 10, {"source": "test"})
            rows, _ = subject.load_signature_ledger(ledger, 10)
            self.assertEqual(len(rows), 1)

    def test_missing_baseline_overlap_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            ledger = pathlib.Path(raw) / "rows.jsonl.gz"
            subject.write_signature_ledger(ledger, [row("a", 1, 10)], 10, {"source": "test"})
            with mock.patch.object(subject, "rpc", side_effect=[[row("b", 2, 20)], []]):
                with self.assertRaisesRegex(RuntimeError, "OVERLAP_NOT_REACHED"):
                    subject.fetch_signatures_incremental("rpc", 10, ledger)

    def test_late_indexed_historical_rows_are_merged_with_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            ledger = pathlib.Path(raw) / "rows.jsonl.gz"
            subject.write_signature_ledger(ledger, [row("a", 1, 10), row("c", 3, 30)], 10, {"source": "test"})
            with mock.patch.object(subject, "rpc", return_value=[row("d", 4, 40), row("b", 2, 20), row("c", 3, 30)]):
                rows = subject.fetch_signatures_incremental("rpc", 10, ledger)
            self.assertEqual([x["signature"] for x in rows], ["a", "b", "c", "d"])
            _, manifest = subject.load_signature_ledger(ledger, 10)
            self.assertEqual(manifest["provenance"]["historicalBackfillN"], 1)

    def test_existing_signature_slot_change_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            ledger = pathlib.Path(raw) / "rows.jsonl.gz"
            subject.write_signature_ledger(ledger, [row("a", 1, 10), row("c", 3, 30)], 10, {"source": "test"})
            with mock.patch.object(subject, "rpc", return_value=[row("d", 4, 40), row("c", 2, 30)]):
                with self.assertRaisesRegex(RuntimeError, "OVERLAP_SLOT_MISMATCH"):
                    subject.fetch_signatures_incremental("rpc", 10, ledger)

    def test_bootstrap_from_successful_cache(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            cache = root / "cache"
            cache.mkdir()
            status = root / "status.json"
            tx = {"slot": 1, "blockTime": 11, "meta": {"err": None}, "transaction": {"signatures": ["a"]}}
            (cache / "tx-a.json").write_text(json.dumps(tx))
            status.write_text(json.dumps({
                "schema": "2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_MATERIALIZER_V3_STATUS",
                "atUtc": "2026-01-01T00:00:00Z", "runStartedAtUtc": "2026-01-01T00:00:00Z",
                "outcomesRead": 0, "submissions": 0,
            }))
            ledger = root / "ledger.jsonl.gz"
            manifest = subject.bootstrap_signature_ledger_from_transaction_cache(cache, ledger, 10, status)
            self.assertEqual(manifest["rowN"], 1)
            self.assertEqual(manifest["provenance"]["source"], "PRIOR_SUCCESSFUL_MATERIALIZER_TRANSACTION_CACHE")

    def test_invalid_bootstrap_proof_does_not_read_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            cache = root / "cache"
            cache.mkdir()
            status = root / "status.json"
            status.write_text(json.dumps({
                "schema": "2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_MATERIALIZER_V3_STATUS",
                "outcomesRead": 1, "submissions": 0,
            }))
            with self.assertRaisesRegex(RuntimeError, "NOT_OUTCOME_BLIND"):
                subject.bootstrap_signature_ledger_from_transaction_cache(
                    cache, root / "ledger.jsonl.gz", 10, status,
                )


if __name__ == "__main__":
    unittest.main()
