import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from materialize2CQNativeLaunchLineageV1 import materialize


class NativeLaunchLineageTests(unittest.TestCase):
    def test_missing_creator_and_initial_state_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            source.write_text(json.dumps({"mint": "M", "local_receive_ts_ms": 2000}) + "\n", encoding="utf-8")
            registry = root / "registry.sqlite3"
            connection = sqlite3.connect(registry)
            connection.execute("CREATE TABLE create_registry (mint TEXT, signature TEXT, slot INTEGER, realtime_receipt_ns INTEGER, canonical_order_status TEXT, realtime_status TEXT, decoder_status TEXT, canonical_evidence_hash TEXT)")
            connection.execute("INSERT INTO create_registry VALUES ('M','sig',1,1000000000,'VALID','OK','OK','hash')")
            connection.commit(); connection.close()
            output = root / "lineage.jsonl"; report = root / "report.json"
            result = materialize(source, registry, None, output, report)
            self.assertEqual(result["complete_lineage_mints"], 0)
            self.assertIn("missing_creator_identity", result["missing_reason_counts"])

    def test_exact_decoder_evidence_emits_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            source.write_text(json.dumps({"mint": "M", "local_receive_ts_ms": 2000}) + "\n", encoding="utf-8")
            registry = root / "registry.sqlite3"
            connection = sqlite3.connect(registry)
            connection.execute("CREATE TABLE create_registry (mint TEXT, signature TEXT, slot INTEGER, realtime_receipt_ns INTEGER, canonical_order_status TEXT, realtime_status TEXT, decoder_status TEXT, canonical_evidence_hash TEXT)")
            connection.execute("INSERT INTO create_registry VALUES ('M','sig',1,1000000000,'VALID','OK','OK','hash')")
            connection.commit(); connection.close()
            evidence = root / "create.jsonl"
            evidence.write_text(json.dumps({"mint": "M", "creator": "C", "provenance_status": "EXACT_CREATE_DECODER", "initial_state": {"virtual_sol": 1, "virtual_token": 2, "real_sol": 1, "real_token": 2}}) + "\n", encoding="utf-8")
            output = root / "lineage.jsonl"; report = root / "report.json"
            result = materialize(source, registry, evidence, output, report)
            self.assertEqual(result["complete_lineage_mints"], 1)
            self.assertEqual(json.loads(output.read_text())["creator"], "C")


if __name__ == "__main__":
    unittest.main()
