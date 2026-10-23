import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
SCRIPT = Path(__file__).with_name("evidence_lake_legacy_tree_audit_v1.py")


def test_inactive_tree_becomes_archive_candidate():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        tree = root / "old"
        tree.mkdir()
        (tree / "x").write_text("x")
        output = root / "audit.json"
        catalog = root / "catalog.sqlite3"
        db = sqlite3.connect(catalog)
        try:
            db.execute("CREATE TABLE pins (experiment_id TEXT,path TEXT,pin_type TEXT,reason TEXT)")
            db.commit()
        finally:
            db.close()
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--path", str(tree), "--output", str(output),
             "--catalog", str(catalog), "--settle-seconds", "0"],
            text=True,
            capture_output=True,
        )
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result["classification"] == "INACTIVE_ARCHIVE_CANDIDATE"
        assert result["outcome"] == "DEPENDENCY_NONE_PROVEN"


def test_invalid_ignored_pid_fails_closed():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        tree = root / "old"
        tree.mkdir()
        (tree / "x").write_text("x")
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--path", str(tree), "--output", str(root / "audit.json"),
             "--ignore-process-pid", "not-a-pid"],
            text=True,
            capture_output=True,
        )
        assert completed.returncode != 0
