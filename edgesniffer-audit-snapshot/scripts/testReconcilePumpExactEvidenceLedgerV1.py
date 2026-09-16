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
from reconcilePumpExactEvidenceLedgerV1 import MAGIC, reconcile


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


if __name__ == "__main__":
    unittest.main()

