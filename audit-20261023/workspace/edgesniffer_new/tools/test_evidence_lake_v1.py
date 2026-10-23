import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("evidence_lake_v1.py")


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], check=True, text=True, capture_output=True)


def test_pins_active_data_and_snapshots_catalog():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        active = root / "active"; active.mkdir(); (active / "open.partial").write_text("x")
        closed = root / "closed"; closed.mkdir(); (closed / "result.json").write_text("{}")
        db = root / "catalog.sqlite3"; snap = root / "snapshot.json"
        assert json.loads(run("--db", str(db), "init").stdout)["status"] == "INITIALIZED"
        pinned = json.loads(run("--db", str(db), "pin", "--experiment", "exp", "--cohort", "kefi",
                                "--reason", "live", "--path", str(active)).stdout)
        assert pinned["count"] == 1
        report = json.loads(run("--db", str(db), "inventory", "--active-root", str(active),
                                "--path", str(active), "--path", str(closed)).stdout)
        assert report["objects"][0]["action"] == "PROTECT"
        # Unknown is intentionally protected until a separate verified archive
        # receipt establishes that a release is safe.
        assert report["objects"][1]["action"] == "PROTECT"
        snapshot = json.loads(run("--db", str(db), "snapshot", "--output", str(snap)).stdout)
        assert snapshot["pin_count"] == 1 and len(snapshot["sha256"]) == 64
