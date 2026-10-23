import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("evidence_lake_partition_dependency_audit_v1.py")


def test_incomplete_manifest_fails_closed_as_unknown():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw); part = root / "00"; part.mkdir(); (part / "evidence.frames.zst").write_bytes(b"bytes")
        (part / "manifest.json").write_text(json.dumps({"openedAtUtc": "2026-10-07T00:00:00Z", "closedAtUtc": "2026-10-07T01:00:00Z", "temporalHourClosed": True, "exactReplayComplete": False}))
        db = root / "catalog.sqlite3"; db.write_bytes(b"")
        result = subprocess.run([sys.executable, str(SCRIPT), "--partition", str(part), "--partition-id", "p/00", "--boundary-utc", "2026-10-07T13:44:00Z", "--catalog", str(db), "--output-dir", str(root / "audits"), "--settle-seconds", "0"], text=True, capture_output=True)
        assert result.returncode == 0
        assert json.loads(result.stdout)["outcome"] == "DEPENDENCY_UNKNOWN"
