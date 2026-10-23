#!/usr/bin/env python3
import datetime as dt
import hashlib
import json
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from reconcilePumpExactEvidenceLedgerV1 import MAGIC, ReconciliationTelemetry, reconcile


def write_frames(path, rows):
    with path.open("wb") as handle:
        handle.write(MAGIC)
        for metadata, raw in rows:
            metadata_bytes = json.dumps(metadata).encode()
            body = metadata_bytes + raw
            handle.write(struct.pack("<II", len(metadata_bytes), len(raw)))
            handle.write(body)
            handle.write(hashlib.sha256(body).digest())


class ReconcileTests(unittest.TestCase):
    def test_optional_telemetry_is_append_only_and_does_not_change_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            partition = root / "partitions/2026/09/06/00"
            partition.mkdir(parents=True)
            rows = [
                ({"kind":"transaction","signature":"sig","evidenceId":"early","slot":10,"successful":True}, b"early"),
                ({"kind":"rpc_finalized_transaction","signature":"sig","evidenceId":"final","slot":10,"transactionIndex":0,"finalized":True,"successful":True,"failed":False,"missingRawTx":False,"missingTradeEvent":False,"missingReserveTransition":False,"missingFeeEvidence":False,"decodeFailed":False}, b"final"),
                ({"kind":"rpc_finalized_block","evidenceId":"block","slot":10}, json.dumps({"result":{"signatures":["sig"]}}).encode()),
            ]
            write_frames(partition / "evidence.frames.partial", rows)
            (partition / "manifest.json").write_text(json.dumps({
                "schemaVersion":"test","schemaSha256":"schema",
                "partitionKey":"2026/09/06/00","temporalHourClosed":True,
            }))
            telemetry_path = root / "telemetry.jsonl"
            telemetry = ReconciliationTelemetry(telemetry_path)
            result = reconcile(
                root, dt.datetime(2026,9,6,2,tzinfo=dt.timezone.utc), 90,
                telemetry=telemetry,
            )[0]
            row = telemetry.finish("SUCCESS")
            self.assertTrue(result["exactReplayComplete"])
            self.assertEqual("SUCCESS", row["status"])
            self.assertEqual("2026/09/06/00", row["partitions"][0]["partitionKey"])
            self.assertEqual(3, row["partitions"][0]["counters"]["targetFrames"])
            self.assertEqual(1, len(telemetry_path.read_text().splitlines()))

    def test_streaming_reconciliation_is_exact_and_skips_unchanged_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            partition = root / "partitions/2026/09/06/00"
            partition.mkdir(parents=True)
            rows = [
                (
                    {
                        "kind": "transaction",
                        "signature": "sig",
                        "evidenceId": "early",
                        "slot": 10,
                        "successful": True,
                    },
                    b"early",
                ),
                (
                    {
                        "kind": "rpc_finalized_transaction",
                        "signature": "sig",
                        "evidenceId": "final",
                        "slot": 10,
                        "transactionIndex": None,
                        "finalized": True,
                        "successful": True,
                        "failed": False,
                        "missingRawTx": False,
                        "missingTradeEvent": False,
                        "missingReserveTransition": False,
                        "missingFeeEvidence": False,
                        "decodeFailed": False,
                    },
                    b"final",
                ),
                (
                    {"kind": "rpc_finalized_block", "evidenceId": "block", "slot": 10},
                    json.dumps({"result": {"signatures": ["sig"]}}).encode(),
                ),
            ]
            write_frames(partition / "evidence.frames.partial", rows)
            (partition / "manifest.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "test",
                        "schemaSha256": "schema",
                        "partitionKey": "2026/09/06/00",
                        "temporalHourClosed": True,
                        "compressedSha256": None,
                    }
                )
            )
            now = dt.datetime(2026, 9, 6, 2, tzinfo=dt.timezone.utc)
            first = reconcile(root, now, 90)[0]
            self.assertTrue(first["exactReplayComplete"])
            self.assertEqual(1, first["coverage"]["successful"])
            self.assertEqual(3, first["joinedEvidenceIdCount"])
            self.assertIn("inputFingerprintSha256", first)
            second = reconcile(root, now + dt.timedelta(minutes=1), 90)[0]
            self.assertEqual(first["reconciledAtUtc"], second["reconciledAtUtc"])

    def test_legacy_final_manifest_without_input_provenance_is_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            partition = root / "partitions/2026/09/06/00"
            partition.mkdir(parents=True)
            rows = [
                ({"kind":"transaction","signature":"sig","evidenceId":"early","slot":10,"successful":True}, b"early"),
                ({"kind":"rpc_finalized_transaction","signature":"sig","evidenceId":"final","slot":10,"transactionIndex":0,"finalized":True,"successful":True,"failed":False,"missingRawTx":False,"missingTradeEvent":False,"missingReserveTransition":False,"missingFeeEvidence":False,"decodeFailed":False}, b"final"),
                ({"kind":"rpc_finalized_block","evidenceId":"block","slot":10}, json.dumps({"result":{"signatures":["sig"]}}).encode()),
            ]
            write_frames(partition / "evidence.frames.partial", rows)
            (partition / "manifest.json").write_text(json.dumps({"schemaVersion":"test","schemaSha256":"schema","partitionKey":"2026/09/06/00","temporalHourClosed":True}))
            (partition / "reconciliation-manifest.json").write_text(json.dumps({"exactReplayComplete":True,"manifestSha256":"legacy"}))
            result = reconcile(root, dt.datetime(2026,9,6,2,tzinfo=dt.timezone.utc), 90)[0]
            self.assertTrue(result["exactReplayComplete"])
            self.assertTrue(result["inputEvidence"])
            self.assertEqual(result["inputFingerprintSha256"], result["postRunInputFingerprintSha256"])
            self.assertNotEqual("legacy", result["manifestSha256"])

    def test_modern_exact_status_promotes_over_legacy_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            partition = root / "partitions/2026/09/06/00"
            partition.mkdir(parents=True)
            rows = [
                ({"kind":"transaction","signature":"sig","evidenceId":"early","slot":10,"successful":True}, b"early"),
                ({"kind":"rpc_finalized_transaction","signature":"sig","evidenceId":"final","slot":10,"transactionIndex":0,"finalized":True,"successful":True,"failed":False,"missingRawTx":False,"missingTradeEvent":False,"missingReserveTransition":False,"missingFeeEvidence":False,"decodeFailed":False}, b"final"),
                ({"kind":"rpc_finalized_block","evidenceId":"block","slot":10}, json.dumps({"result":{"signatures":["sig"]}}).encode()),
            ]
            data_path = partition / "evidence.frames.partial"
            write_frames(data_path, rows)
            (partition / "manifest.json").write_text(json.dumps({"schemaVersion":"test","schemaSha256":"schema","partitionKey":"2026/09/06/00","temporalHourClosed":True}))
            (partition / "reconciliation-manifest.json").write_text(json.dumps({"exactReplayComplete":True,"manifestSha256":"legacy"}))
            now = dt.datetime(2026,9,6,2,tzinfo=dt.timezone.utc)
            modern = reconcile(root, now, 90)[0]
            (partition / "reconciliation-manifest.json").write_text(json.dumps({"exactReplayComplete":True,"manifestSha256":"legacy-again"}))
            reused = reconcile(root, now + dt.timedelta(minutes=1), 90)[0]
            promoted = json.loads((partition / "reconciliation-manifest.json").read_text())
            self.assertEqual(modern["manifestSha256"], reused["manifestSha256"])
            self.assertEqual(modern["manifestSha256"], promoted["manifestSha256"])

    def test_active_future_partial_is_not_an_immutable_support_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            target = root / "partitions/2026/09/06/00"
            future = root / "partitions/2026/09/06/01"
            target.mkdir(parents=True)
            future.mkdir(parents=True)
            rows = [
                ({"kind":"transaction","signature":"sig","evidenceId":"early","slot":10,"successful":True}, b"early"),
                ({"kind":"rpc_finalized_transaction","signature":"sig","evidenceId":"final","slot":10,"transactionIndex":0,"finalized":True,"successful":True,"failed":False,"missingRawTx":False,"missingTradeEvent":False,"missingReserveTransition":False,"missingFeeEvidence":False,"decodeFailed":False}, b"final"),
                ({"kind":"rpc_finalized_block","evidenceId":"block","slot":10}, json.dumps({"result":{"signatures":["sig"]}}).encode()),
            ]
            write_frames(target / "evidence.frames.partial", rows)
            write_frames(future / "evidence.frames.partial", [])
            (target / "manifest.json").write_text(json.dumps({"schemaVersion":"test","schemaSha256":"schema","partitionKey":"2026/09/06/00","temporalHourClosed":True}))
            result = reconcile(root, dt.datetime(2026,9,6,2,tzinfo=dt.timezone.utc), 90)[0]
            self.assertEqual(1, len(result["inputEvidence"]))
            self.assertNotIn("2026/09/06/01", result["inputEvidence"][0]["path"])

    def test_missing_finality_is_recovered_from_canonical_block_and_rpc_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            partition = root / "partitions/2026/09/06/00"
            partition.mkdir(parents=True)
            rows = [
                ({"kind":"transaction","signature":"sig","evidenceId":"early","slot":10,
                  "successful":None,"missingRawTx":False,"missingTradeEvent":False,
                  "missingReserveTransition":False,"missingFeeEvidence":False,"decodeFailed":False}, b"raw"),
                ({"kind":"rpc_finalized_block","evidenceId":"block","slot":10},
                 json.dumps({"result":{"signatures":["sig"]}}).encode()),
            ]
            write_frames(partition / "evidence.frames.partial", rows)
            (partition / "manifest.json").write_text(json.dumps({
                "schemaVersion":"test","schemaSha256":"schema",
                "partitionKey":"2026/09/06/00","temporalHourClosed":True,
            }))
            recovered = {"sig": {"slot":10,"successful":True,"err":None,
                                  "confirmationStatus":"finalized"}}
            with mock.patch(
                "reconcilePumpExactEvidenceLedgerV1.fetch_finalized_statuses",
                return_value=recovered,
            ):
                result = reconcile(
                    root, dt.datetime(2026,9,6,2,tzinfo=dt.timezone.utc), 90,
                    "https://unused.invalid", "2026/09/06/00",
                )[0]
            self.assertTrue(result["exactReplayComplete"])
            self.assertEqual(1, result["coverage"]["successful"])
            self.assertEqual(1, result["canonicalFinalityRecovery"]["recoveredSignatureCount"])
            self.assertTrue((partition / "canonical-finality-recovery-v1.json").exists())

    def test_unchanged_incomplete_status_retries_transient_finality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            partition = root / "partitions/2026/09/06/00"
            partition.mkdir(parents=True)
            rows = [
                ({"kind":"transaction","signature":"sig","evidenceId":"early","slot":10,
                  "successful":None,"missingRawTx":False,"missingTradeEvent":False,
                  "missingReserveTransition":False,"missingFeeEvidence":False,"decodeFailed":False}, b"raw"),
                ({"kind":"rpc_finalized_block","evidenceId":"block","slot":10},
                 json.dumps({"result":{"signatures":["sig"]}}).encode()),
            ]
            write_frames(partition / "evidence.frames.partial", rows)
            (partition / "manifest.json").write_text(json.dumps({
                "schemaVersion":"test","schemaSha256":"schema",
                "partitionKey":"2026/09/06/00","temporalHourClosed":True,
            }))
            now = dt.datetime(2026,9,6,2,tzinfo=dt.timezone.utc)
            first = reconcile(root, now, 90)[0]
            self.assertFalse(first["exactReplayComplete"])
            self.assertEqual(1, first["coverage"]["missingFinality"])
            recovered = {"sig": {"slot":10,"successful":True,"err":None,
                                  "confirmationStatus":"finalized"}}
            with mock.patch(
                "reconcilePumpExactEvidenceLedgerV1.fetch_finalized_statuses",
                return_value=recovered,
            ) as fetch:
                second = reconcile(root, now + dt.timedelta(minutes=5), 90,
                                   ["https://unused.invalid"])[0]
            self.assertTrue(second["exactReplayComplete"])
            fetch.assert_called_once()

    def test_finalized_block_absence_classifies_realtime_fork_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            partition = root / "partitions/2026/09/06/00"
            partition.mkdir(parents=True)
            rows = [
                ({"kind":"transaction","signature":"fork-sig","evidenceId":"early",
                  "slot":10,"successful":None,"missingRawTx":False,
                  "missingTradeEvent":False,"missingReserveTransition":False,
                  "missingFeeEvidence":False,"decodeFailed":False}, b"raw"),
                ({"kind":"rpc_finalized_block","evidenceId":"block","slot":10},
                 json.dumps({"result":{"signatures":["canonical-other"]}}).encode()),
            ]
            write_frames(partition / "evidence.frames.partial", rows)
            (partition / "manifest.json").write_text(json.dumps({
                "schemaVersion":"test","schemaSha256":"schema",
                "partitionKey":"2026/09/06/00","temporalHourClosed":True,
            }))
            not_finalized = {
                "fork-sig": {"slot":None,"successful":None,"err":None,
                             "confirmationStatus":None,"notFinalizedAtRecovery":True}
            }
            with mock.patch(
                "reconcilePumpExactEvidenceLedgerV1.fetch_finalized_statuses",
                return_value=not_finalized,
            ):
                result = reconcile(
                    root, dt.datetime(2026,9,6,2,tzinfo=dt.timezone.utc), 90,
                    ["https://unused.invalid"],
                )[0]
            self.assertTrue(result["exactReplayComplete"])
            self.assertEqual(1, result["coverage"]["nonCanonicalObservations"])
            self.assertEqual(0, result["coverage"]["successful"])
            self.assertEqual(0, result["coverage"]["failed"])
            self.assertEqual(0, result["coverage"]["unresolved"])
            self.assertEqual(1, result["nonCanonicalObservationProof"]["count"])

    def test_archival_transaction_recovers_canonical_signature_with_expired_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            partition = root / "partitions/2026/09/06/00"
            partition.mkdir(parents=True)
            rows = [
                ({"kind":"transaction","signature":"sig","evidenceId":"early",
                  "slot":10,"successful":None,"missingRawTx":False,
                  "missingTradeEvent":False,"missingReserveTransition":False,
                  "missingFeeEvidence":False,"decodeFailed":False}, b"raw"),
                ({"kind":"rpc_finalized_block","evidenceId":"block","slot":10},
                 json.dumps({"result":{"signatures":["sig"]}}).encode()),
            ]
            write_frames(partition / "evidence.frames.partial", rows)
            (partition / "manifest.json").write_text(json.dumps({
                "schemaVersion":"test","schemaSha256":"schema",
                "partitionKey":"2026/09/06/00","temporalHourClosed":True,
            }))
            expired = {
                "sig": {"slot":None,"successful":None,"err":None,
                        "confirmationStatus":None,"notFinalizedAtRecovery":True}
            }
            archival = {
                "sig": {"slot":10,"successful":True,"err":None,
                        "confirmationStatus":"finalized","source":"getTransaction(finalized)"}
            }
            with mock.patch(
                "reconcilePumpExactEvidenceLedgerV1.fetch_finalized_statuses",
                return_value=expired,
            ), mock.patch(
                "reconcilePumpExactEvidenceLedgerV1.fetch_finalized_transactions",
                return_value=archival,
            ) as fetch:
                result = reconcile(
                    root, dt.datetime(2026,9,6,2,tzinfo=dt.timezone.utc), 90,
                    ["https://unused.invalid"],
                )[0]
            self.assertTrue(result["exactReplayComplete"])
            self.assertEqual(1, result["coverage"]["successful"])
            self.assertEqual(1, result["canonicalTransactionRecovery"]["recoveredSignatureCount"])
            fetch.assert_called_once()

    def test_unavailable_finalized_slot_and_absent_status_prove_fork(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            partition = root / "partitions/2026/09/06/00"
            partition.mkdir(parents=True)
            rows = [
                ({"kind":"transaction","signature":"fork-sig","evidenceId":"early",
                  "slot":10,"successful":None,"missingRawTx":False,
                  "missingTradeEvent":False,"missingReserveTransition":False,
                  "missingFeeEvidence":False,"decodeFailed":False}, b"raw"),
            ]
            write_frames(partition / "evidence.frames.partial", rows)
            (partition / "manifest.json").write_text(json.dumps({
                "schemaVersion":"test","schemaSha256":"schema",
                "partitionKey":"2026/09/06/00","temporalHourClosed":True,
            }))
            (partition / "canonical-block-recovery-v1.json").write_text(json.dumps({
                "blocks": {}, "unavailableSlots": [10],
            }))
            (partition / "canonical-finality-recovery-v1.json").write_text(json.dumps({
                "signatures": {"fork-sig": {
                    "slot":None,"successful":None,"err":None,
                    "confirmationStatus":None,"notFinalizedAtRecovery":True,
                }},
            }))
            result = reconcile(
                root, dt.datetime(2026,9,6,2,tzinfo=dt.timezone.utc), 90,
            )[0]
            self.assertTrue(result["exactReplayComplete"])
            self.assertEqual(1, result["coverage"]["nonCanonicalObservations"])
            self.assertEqual(
                1, result["canonicalBlockRecovery"]["unavailableFinalizedSlotCount"]
            )

    def test_old_unchanged_incomplete_generation_is_exhausted_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            partition = root / "partitions/2026/09/06/00"
            partition.mkdir(parents=True)
            rows = [
                ({"kind":"transaction","signature":"sig","evidenceId":"early",
                  "slot":10,"successful":None,"missingRawTx":False,
                  "missingTradeEvent":False,"missingReserveTransition":False,
                  "missingFeeEvidence":False,"decodeFailed":False}, b"raw"),
                ({"kind":"rpc_finalized_block","evidenceId":"block","slot":10},
                 json.dumps({"result":{"signatures":["sig"]}}).encode()),
            ]
            write_frames(partition / "evidence.frames.partial", rows)
            (partition / "manifest.json").write_text(json.dumps({
                "schemaVersion":"test","schemaSha256":"schema",
                "partitionKey":"2026/09/06/00","temporalHourClosed":True,
            }))
            expired = {"sig": {"slot":None,"successful":None,"err":None,
                                "confirmationStatus":None,"notFinalizedAtRecovery":True}}
            with mock.patch(
                "reconcilePumpExactEvidenceLedgerV1.fetch_finalized_statuses",
                return_value=expired,
            ), mock.patch(
                "reconcilePumpExactEvidenceLedgerV1.fetch_finalized_transactions",
                return_value={},
            ):
                first = reconcile(
                    root, dt.datetime(2026,9,6,10,tzinfo=dt.timezone.utc), 90,
                    ["https://unused.invalid"],
                )[0]
            self.assertFalse(first["exactReplayComplete"])
            self.assertTrue(first["reconciliationRetryExhausted"])
            with mock.patch(
                "reconcilePumpExactEvidenceLedgerV1.fetch_finalized_statuses",
            ) as finality, mock.patch(
                "reconcilePumpExactEvidenceLedgerV1.fetch_finalized_transactions",
            ) as archival:
                second = reconcile(
                    root, dt.datetime(2026,9,6,11,tzinfo=dt.timezone.utc), 90,
                    ["https://unused.invalid"],
                )[0]
            self.assertEqual(first["manifestSha256"], second["manifestSha256"])
            finality.assert_not_called()
            archival.assert_not_called()


if __name__ == "__main__":
    unittest.main()
