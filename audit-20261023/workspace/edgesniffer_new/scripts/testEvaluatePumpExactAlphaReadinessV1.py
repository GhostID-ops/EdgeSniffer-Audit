#!/usr/bin/env python3
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from evaluatePumpExactAlphaReadinessV1 import elapsed_hours
from trackPumpExactEvidenceReadinessV1 import aggregate, persist_current_partition_report


class ReadinessGateTests(unittest.TestCase):
    def test_elapsed_hours(self):
        self.assertEqual(24.0, elapsed_hours("2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"))

    def test_invalid_interval_fails_closed(self):
        self.assertEqual(0.0, elapsed_hours(None, "2026-09-02T00:00:00Z"))
        self.assertEqual(0.0, elapsed_hours("bad", "also-bad"))

    def test_changed_reconciliation_fingerprint_replaces_current_and_archives_prior(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "partitions" / "hour-v4.json"
            old = {"sourceReconciliationManifestSha256": "old", "exactAdmittedN": 1}
            new = {"sourceReconciliationManifestSha256": "new", "exactAdmittedN": 2}
            persist_current_partition_report(path, old)
            persist_current_partition_report(path, new)
            self.assertEqual(new, json.loads(path.read_text()))
            archived = path.parent / "superseded" / "hour-v4-old.json"
            self.assertEqual(old, json.loads(archived.read_text()))

    def test_later_admitted_duplicate_replaces_earlier_excluded_occurrence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            reports = []
            cases = [
                ("2026/09/06/05", "2026-09-06T05:00:00Z", False, ["create_not_finalized_successful"], 100),
                ("2026/09/06/21", "2026-09-06T21:00:00Z", True, [], 200),
            ]
            for partition, hour, admitted, reasons, create_ns in cases:
                report = root / f"{partition.replace('/', '-')}.json"
                report.write_text(json.dumps({
                    "partitionKey": partition,
                    "partitionHourUtc": hour,
                    "freshMintsObserved": 1,
                    "exactAdmittedN": int(admitted),
                    "mints": [{
                        "mint": "same-mint",
                        "chainTimestampUtc": None,
                        "createReceivedAtUtc": hour,
                        "createReceivedAtUnixNs": create_ns,
                        "exactAdmitted": admitted,
                        "exclusionReasons": reasons,
                    }],
                }))
                manifest = root / "partitions" / partition / "reconciliation-manifest.json"
                manifest.parent.mkdir(parents=True, exist_ok=True)
                manifest.write_text(json.dumps({"coverage": {}}))
                reports.append(report)

            summary = aggregate(root, reports)
            self.assertEqual(1, summary["exactAdmittedN"])
            self.assertEqual(0, summary["excludedN"])
            self.assertEqual({}, summary["exclusionReasons"])


if __name__ == "__main__":
    unittest.main()
