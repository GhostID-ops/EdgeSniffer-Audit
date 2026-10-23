#!/usr/bin/env python3
"""Focused source-separation tests for the future-only dual CREATE lane."""

from __future__ import annotations

import pathlib
import tempfile
import unittest
import hashlib
import json
import datetime as dt

import twoCqDualSourceCreateSymmetricMaterializerV1 as materializer
import twoCqSolPumpCreateProvenanceRegistryV1 as registry
import reconcilePumpExactEvidenceLedgerV1 as exact_ledger


class DualSourceCreateMaterializerTests(unittest.TestCase):
    def raw_acquisition_fixture(self) -> tuple[dict, dict, dict, list[str], dict]:
        signature = "teacher-acquisition"
        mint = "mint"
        frame = {
            "kind": "transaction",
            "rawFormat": materializer.YELLOWSTONE_RAW_FORMAT,
            "signature": signature,
            "slot": 101,
            "receivedAtUnixNs": 123_456_789,
            "evidenceId": "evidence",
            "rawSha256": "raw-hash",
            # Receipt-first frames can legitimately have no decoded mint.
            "discoveredMints": [],
        }
        episode = {
            "signature": signature,
            "mint": mint,
            "slot": 101,
            "transactionIndex": 1,
            "blockTimeMs": 1_700_000_000_000,
        }
        finality = {
            "signature": signature,
            "confirmationStatus": "finalized",
            "successful": True,
            "slot": 101,
        }
        context = {
            "anchors": [{
                "signature": "anchor",
                "yellowstoneDetectedAtMs": 123,
                "yellowstoneMinusAllenharkMs": 0,
            }],
            "anchorTimes": [123],
            "clockDomain": {"hostBootId": "boot"},
            "clockDomainCalibrated": True,
            "calibrationReportSha256": "report",
        }
        return frame, episode, finality, ["other", signature], context

    def test_raw_receipt_plus_canonical_acquisition_promotes(self) -> None:
        frame, episode, finality, block, context = self.raw_acquisition_fixture()
        decision = materializer.raw_acquisition_promotion_decision(
            frame, episode, finality, block, context
        )
        self.assertTrue(decision["promotionPass"])
        self.assertEqual(decision["realtimeReceiptNs"], frame["receivedAtUnixNs"])
        self.assertEqual(decision["canonicalTxIndex"], episode["transactionIndex"])

    def test_raw_receipt_without_finalized_proof_is_rejected(self) -> None:
        frame, episode, _finality, block, context = self.raw_acquisition_fixture()
        decision = materializer.raw_acquisition_promotion_decision(
            frame, episode, None, block, context
        )
        self.assertEqual(decision["reason"], "FINALIZED_ACQUISITION_PROOF_MISSING")

    def test_finalized_proof_without_raw_receipt_is_rejected(self) -> None:
        _frame, episode, finality, block, context = self.raw_acquisition_fixture()
        decision = materializer.raw_acquisition_promotion_decision(
            {}, episode, finality, block, context
        )
        self.assertEqual(decision["reason"], "RETAINED_REALTIME_RECEIPT_MISSING")

    def test_raw_mint_claim_must_match_canonical_mint(self) -> None:
        frame, episode, finality, block, context = self.raw_acquisition_fixture()
        frame["discoveredMints"] = ["another-mint"]
        decision = materializer.raw_acquisition_promotion_decision(
            frame, episode, finality, block, context
        )
        self.assertEqual(decision["reason"], "RAW_REALTIME_MINT_MISMATCH")

    def test_signature_and_canonical_order_conflicts_are_rejected(self) -> None:
        frame, episode, finality, block, context = self.raw_acquisition_fixture()
        frame["signature"] = "other"
        decision = materializer.raw_acquisition_promotion_decision(
            frame, episode, finality, block, context
        )
        self.assertEqual(decision["reason"], "RAW_REALTIME_SIGNATURE_MISMATCH")
        frame["signature"] = episode["signature"]
        decision = materializer.raw_acquisition_promotion_decision(
            frame, episode, finality, [episode["signature"], "other"], context
        )
        self.assertEqual(decision["reason"], "CANONICAL_TRANSACTION_INDEX_CONFLICT")

    def test_raw_adapter_append_is_idempotent_and_preserves_timestamp(self) -> None:
        frame, episode, finality, block, context = self.raw_acquisition_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            evidence = root / "evidence.frames"
            body = json.dumps(frame, separators=(",", ":")).encode()
            with evidence.open("wb") as stream:
                stream.write(exact_ledger.MAGIC)
                stream.write(len(body).to_bytes(4, "little"))
                stream.write((0).to_bytes(4, "little"))
                stream.write(body)
                stream.write(hashlib.sha256(body).digest())
            finality_path = root / "finality.json"
            finality_path.write_text(json.dumps({"signatures": {episode["signature"]: finality}}))
            block_path = root / "block.json"
            block_path.write_text(json.dumps({"blocks": {"101": block}}))
            calibration_path = root / "calibration.json"
            calibration_path.write_text(json.dumps({
                "matchedRows": context["anchors"],
                "clockDomain": {
                    "sameHostSources": True,
                    "hostBootId": "boot",
                    "ntpSynchronized": True,
                    "validExactSignatureAnchors": materializer.CALIBRATION_MIN_EXACT_ANCHORS,
                },
            }))
            ledger_path = root / "candidates.jsonl"
            original_inputs = materializer.raw_acquisition_partition_inputs
            expected_hour = dt.datetime.fromtimestamp(
                episode["blockTimeMs"] / 1000, dt.timezone.utc
            ).strftime("%Y/%m/%d/%H")
            materializer.raw_acquisition_partition_inputs = lambda _root, _episodes: [
                (expected_hour, evidence, finality_path, block_path)
            ]
            try:
                recovered, report = materializer.recover_yellowstone_raw_exact_signature_candidates(
                    root, [episode], {}, None, set(), calibration_path, ledger_path, persist=True
                )
                self.assertEqual(report["persistedNew"], 1)
                self.assertEqual(recovered[episode["signature"]]["receivedAtUnixNs"], frame["receivedAtUnixNs"])
                first = ledger_path.read_bytes()
                existing = {
                    episode["signature"]: materializer.read_jsonl(ledger_path)[0]
                }
                _recovered, rerun = materializer.recover_yellowstone_raw_exact_signature_candidates(
                    root, [episode], existing, None, set(), calibration_path, ledger_path, persist=True
                )
                self.assertEqual(rerun["persistedNew"], 0)
                self.assertEqual(ledger_path.read_bytes(), first)
            finally:
                materializer.raw_acquisition_partition_inputs = original_inputs

    def test_allenhark_is_preferred_without_replacing_yellowstone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "registry.sqlite3"
            connection = registry.open_db(path)
            try:
                connection.execute("""
                    INSERT INTO create_registry VALUES(
                      'create','mint',101,3,'VALID',NULL,NULL,NULL,
                      'FINALIZED_SUCCESS','NOT_SEEN_REALTIME','SUPPORTED','hash','now'
                    )
                """)
                for source, receipt in (
                    (materializer.CREATE_YELLOWSTONE_SOURCE, 110),
                    (materializer.CREATE_ALLENHARK_SOURCE, 100),
                ):
                    self.assertEqual(registry.record_source_arrival(connection, {
                        "signature": "create", "source": source, "slot": 101,
                        "observedOrder": 3, "realtimeReceiptNs": receipt,
                        "continuityEpoch": None, "instructionKinds": [],
                        "sourceFile": "/evidence",
                    }), "inserted")
                connection.commit()
            finally:
                connection.close()
            creates, evidence = materializer.dual_source_registry_creates(path, 0, 100, [(0, None)])
            self.assertEqual(creates["mint"]["realtimeSource"], materializer.CREATE_ALLENHARK_SOURCE)
            self.assertEqual(creates["mint"]["receivedNs"], 100)
            self.assertEqual(creates["mint"]["availableSources"], sorted([
                materializer.CREATE_ALLENHARK_SOURCE,
                materializer.CREATE_YELLOWSTONE_SOURCE,
            ]))
            self.assertEqual(evidence["bothSourceCreateAvailable"], 1)

    def test_uncalibrated_yellowstone_is_not_used(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "registry.sqlite3"
            connection = registry.open_db(path)
            try:
                connection.execute("""
                    INSERT INTO create_registry VALUES(
                      'create','mint',101,3,'VALID',NULL,NULL,NULL,
                      'FINALIZED_SUCCESS','NOT_SEEN_REALTIME','SUPPORTED','hash','now'
                    )
                """)
                registry.record_source_arrival(connection, {
                    "signature": "create", "source": materializer.CREATE_YELLOWSTONE_SOURCE,
                    "slot": 101, "observedOrder": 3, "realtimeReceiptNs": 110,
                    "continuityEpoch": None, "instructionKinds": [], "sourceFile": "/evidence",
                })
                connection.commit()
            finally:
                connection.close()
            creates, evidence = materializer.dual_source_registry_creates(path, 0, 100, [])
            self.assertEqual(creates, {})
            self.assertEqual(evidence["yellowstoneCreateOutsideCalibratedClockDomain"], 1)

    def test_registry_generation_changes_when_retained_create_proof_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "registry.sqlite3"
            connection = registry.open_db(path)
            try:
                connection.execute("""
                    INSERT INTO create_registry VALUES(
                      'create','mint',101,3,'VALID',NULL,NULL,NULL,
                      'FINALIZED_SUCCESS','NOT_SEEN_REALTIME','SUPPORTED','hash','now'
                    )
                """)
                registry.record_source_arrival(connection, {
                    "signature": "create", "source": materializer.CREATE_YELLOWSTONE_SOURCE,
                    "slot": 101, "observedOrder": 3, "realtimeReceiptNs": 110,
                    "continuityEpoch": None, "instructionKinds": ["CREATE"], "sourceFile": "/evidence",
                })
                connection.commit()
                first = materializer.create_registry_generation(path, 0, 100)
                connection.execute(
                    "UPDATE source_arrival_candidates SET realtime_receipt_ns=111 WHERE signature='create'"
                )
                connection.commit()
                second = materializer.create_registry_generation(path, 0, 100)
                self.assertNotEqual(first, second)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
