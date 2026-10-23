#!/usr/bin/env python3
"""Focused contract tests for the CREATE provenance registry's pure safety rules."""

from __future__ import annotations

import pathlib
import tempfile
import unittest

import twoCqSolPumpCreateProvenanceRegistryV1 as registry


class CreateProvenanceRegistryTests(unittest.TestCase):
    def recovery_frame(self) -> dict[str, object]:
        return {
            "kind": "transaction",
            "rawFormat": registry.YELLOWSTONE_RAW_FORMAT,
            "signature": "recovery-signature",
            "slot": 42,
            "transactionIndex": 1,
            "receivedAtUnixNs": 123_456_789,
            "pumpInstructionKinds": ["CREATE_V2"],
            "discoveredMints": ["recovery-mint"],
            "evidenceId": "raw-evidence-id",
            "rawSha256": "raw-evidence-sha",
        }

    def recovery_finality(self) -> dict[str, object]:
        return {
            "confirmationStatus": "finalized",
            "successful": True,
            "slot": 42,
        }

    def test_recovery_raw_receipt_and_canonical_proof_promotes_once(self) -> None:
        frame = self.recovery_frame()
        decision = registry.recovery_promotion_decision(
            frame, self.recovery_finality(), ["other", "recovery-signature"], "recovery-mint"
        )
        self.assertTrue(decision["promotionPass"])
        self.assertEqual(decision["canonicalTxIndex"], 1)
        self.assertEqual(decision["realtimeReceiptNs"], frame["receivedAtUnixNs"])
        with tempfile.TemporaryDirectory() as temporary:
            connection = registry.open_db(pathlib.Path(temporary) / "registry.sqlite3")
            try:
                first = registry.promote_recovered_create(connection, decision, frame, pathlib.Path("/retained/raw"))
                second = registry.promote_recovered_create(connection, decision, frame, pathlib.Path("/retained/raw"))
                self.assertEqual(first, "promoted")
                self.assertEqual(second, "existing")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM create_registry").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute(
                        "SELECT realtime_receipt_ns FROM source_arrival_candidates WHERE signature=? AND source=?",
                        ("recovery-signature", registry.YELLOWSTONE_SOURCE),
                    ).fetchone()[0],
                    frame["receivedAtUnixNs"],
                )
            finally:
                connection.close()

    def test_recovery_rejects_without_finalized_proof(self) -> None:
        decision = registry.recovery_promotion_decision(
            self.recovery_frame(), None, ["other", "recovery-signature"], "recovery-mint"
        )
        self.assertEqual(decision["reason"], "FINALIZED_RECOVERY_PROOF_MISSING")

    def test_recovery_rejects_without_raw_realtime_create(self) -> None:
        frame = self.recovery_frame()
        frame["pumpInstructionKinds"] = ["BUY_V2"]
        decision = registry.recovery_promotion_decision(
            frame, self.recovery_finality(), ["other", "recovery-signature"], "recovery-mint"
        )
        self.assertEqual(decision["reason"], "RAW_REALTIME_CREATE_KIND_MISSING")

    def test_recovery_rejects_finalized_create_without_realtime_receipt(self) -> None:
        frame = self.recovery_frame()
        del frame["receivedAtUnixNs"]
        decision = registry.recovery_promotion_decision(
            frame, self.recovery_finality(), ["other", "recovery-signature"], "recovery-mint"
        )
        self.assertEqual(decision["reason"], "RAW_REALTIME_CREATE_METADATA_INVALID")

    def test_recovery_rejects_mint_and_signature_mismatches(self) -> None:
        frame = self.recovery_frame()
        mint_mismatch = registry.recovery_promotion_decision(
            frame, self.recovery_finality(), ["other", "recovery-signature"], "other-mint"
        )
        signature_mismatch = registry.recovery_promotion_decision(
            frame, {**self.recovery_finality(), "signature": "wrong-signature"}, ["other", "recovery-signature"], "recovery-mint"
        )
        self.assertEqual(mint_mismatch["reason"], "RAW_REALTIME_MINT_MISMATCH")
        self.assertEqual(signature_mismatch["reason"], "FINALIZED_RECOVERY_SIGNATURE_MISMATCH")

    def test_recovery_rejects_invalid_canonical_order(self) -> None:
        frame = self.recovery_frame()
        decision = registry.recovery_promotion_decision(
            frame, self.recovery_finality(), ["different-signature"], "recovery-mint"
        )
        self.assertEqual(decision["reason"], "CANONICAL_BLOCK_ORDER_MISMATCH")

    def test_recovery_keeps_existing_registry_row_unchanged(self) -> None:
        frame = self.recovery_frame()
        decision = registry.recovery_promotion_decision(
            frame, self.recovery_finality(), ["other", "recovery-signature"], "recovery-mint"
        )
        with tempfile.TemporaryDirectory() as temporary:
            connection = registry.open_db(pathlib.Path(temporary) / "registry.sqlite3")
            try:
                connection.execute(
                    """INSERT INTO create_registry(
                    signature,mint,slot,canonical_tx_index,canonical_order_status,realtime_receipt_ns,
                    source,continuity_epoch,finalized_status,realtime_status,decoder_status,
                    canonical_evidence_hash,first_seen_utc
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("recovery-signature", "recovery-mint", 1, 0, "VALID", 1, registry.SOURCE,
                     "old", "FINALIZED_SUCCESS", "CAPTURED_ALLENHARK", "SUPPORTED", "old-proof", "old-time"),
                )
                self.assertEqual(
                    registry.promote_recovered_create(connection, decision, frame, pathlib.Path("/retained/raw")),
                    "existing",
                )
                row = connection.execute(
                    "SELECT canonical_evidence_hash,first_seen_utc,realtime_receipt_ns FROM create_registry"
                ).fetchone()
                self.assertEqual(tuple(row), ("old-proof", "old-time", 1))
            finally:
                connection.close()

    def test_recovery_keeps_source_observed_order_separate_from_canonical_order(self) -> None:
        frame = self.recovery_frame()
        frame["transactionIndex"] = 99
        source_path = pathlib.Path("/retained/raw")
        decision = registry.recovery_promotion_decision(
            frame, self.recovery_finality(), ["other", "recovery-signature"], "recovery-mint"
        )
        self.assertEqual(decision["canonicalTxIndex"], 1)
        with tempfile.TemporaryDirectory() as temporary:
            connection = registry.open_db(pathlib.Path(temporary) / "registry.sqlite3")
            try:
                self.assertEqual(registry.record_source_arrival(connection, {
                    "signature": "recovery-signature",
                    "source": registry.YELLOWSTONE_SOURCE,
                    "slot": 42,
                    "observedOrder": 7,
                    "realtimeReceiptNs": 123_456_789,
                    "continuityEpoch": None,
                    "instructionKinds": ["CREATE_V2"],
                    "sourceFile": "/original/retained/raw",
                }), "inserted")
                self.assertEqual(
                    registry.promote_recovered_create(connection, decision, frame, source_path),
                    "promoted",
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT observed_order FROM source_arrival_candidates WHERE signature=? AND source=?",
                        ("recovery-signature", registry.YELLOWSTONE_SOURCE),
                    ).fetchone()[0],
                    7,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT source_file FROM source_arrival_candidates WHERE signature=? AND source=?",
                        ("recovery-signature", registry.YELLOWSTONE_SOURCE),
                    ).fetchone()[0],
                    "/original/retained/raw",
                )
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0], 0)
            finally:
                connection.close()

    def test_recovery_refuses_conflicting_existing_source_timestamp(self) -> None:
        frame = self.recovery_frame()
        decision = registry.recovery_promotion_decision(
            frame, self.recovery_finality(), ["other", "recovery-signature"], "recovery-mint"
        )
        with tempfile.TemporaryDirectory() as temporary:
            connection = registry.open_db(pathlib.Path(temporary) / "registry.sqlite3")
            try:
                registry.record_source_arrival(connection, {
                    "signature": "recovery-signature",
                    "source": registry.YELLOWSTONE_SOURCE,
                    "slot": 42,
                    "observedOrder": 1,
                    "realtimeReceiptNs": 123_456_700,
                    "continuityEpoch": None,
                    "instructionKinds": ["CREATE_V2"],
                    "sourceFile": "/old/raw",
                })
                self.assertEqual(
                    registry.promote_recovered_create(connection, decision, frame, pathlib.Path("/retained/raw")),
                    "source_conflict",
                )
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM create_registry").fetchone()[0], 0)
            finally:
                connection.close()

    def test_epoch_mapping_is_stable_across_restart_epochs(self) -> None:
        epochs = [(100, "first"), (200, "second")]
        self.assertEqual(registry.epoch_for(150, epochs), "first")
        self.assertEqual(registry.epoch_for(200, epochs), "second")
        self.assertEqual(registry.epoch_for(99, epochs), None)

    def test_duplicate_arrival_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            journal = root / "arrivals.jsonl"
            journal.write_text(
                '{"receivedAtUnixNs":100,"slot":1,"signature":"sig","pumpInstructionKinds":["CREATE"]}\n'
                '{"receivedAtUnixNs":100,"slot":1,"signature":"sig","pumpInstructionKinds":["CREATE"]}\n',
                encoding="utf-8",
            )
            connection = registry.open_db(root / "registry.sqlite3")
            try:
                counts = registry.ingest_arrivals(connection, journal, 0, [(0, "epoch")])
                self.assertEqual(counts["createCandidates"], 1)
                self.assertEqual(counts["duplicates"], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM arrival_candidates").fetchone()[0], 1)
            finally:
                connection.close()

    def test_sources_coexist_without_clock_merging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            connection = registry.open_db(pathlib.Path(temporary) / "registry.sqlite3")
            try:
                allenhark = {
                    "signature": "same-signature",
                    "source": registry.SOURCE,
                    "slot": 7,
                    "observedOrder": 3,
                    "realtimeReceiptNs": 100,
                    "continuityEpoch": "allenhark-epoch",
                    "instructionKinds": ["CREATE_V2"],
                    "sourceFile": "/arrival/allenhark",
                }
                yellowstone = {
                    **allenhark,
                    "source": registry.YELLOWSTONE_SOURCE,
                    "realtimeReceiptNs": 120,
                    "continuityEpoch": None,
                    "sourceFile": "/arrival/yellowstone",
                }
                self.assertEqual(registry.record_source_arrival(connection, allenhark), "inserted")
                self.assertEqual(registry.record_source_arrival(connection, yellowstone), "inserted")
                rows = list(connection.execute(
                    "SELECT source,realtime_receipt_ns FROM source_arrival_candidates WHERE signature = ? ORDER BY source",
                    ("same-signature",),
                ))
                self.assertEqual(len(rows), 2)
                self.assertEqual({row["realtime_receipt_ns"] for row in rows}, {100, 120})
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
