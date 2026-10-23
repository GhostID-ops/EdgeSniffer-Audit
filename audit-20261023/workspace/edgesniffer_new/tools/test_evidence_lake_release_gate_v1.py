import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("evidence_lake_release_gate_v1.py")


def invoke(db: Path, path: Path, receipt: Path):
    return subprocess.run([sys.executable, str(SCRIPT), "--db", str(db), "--path", str(path), "--receipt", str(receipt)], text=True, capture_output=True)


def build_catalog(root: Path):
    db = root / "catalog.sqlite3"
    conn = sqlite3.connect(db)
    try:
        conn.executescript("""
          CREATE TABLE receipts (receipt_path TEXT PRIMARY KEY, receipt_sha256 TEXT, receipt_kind TEXT, recorded_utc TEXT);
          CREATE TABLE pins (experiment_id TEXT, path TEXT, pin_type TEXT, created_utc TEXT, reason TEXT);
        """)
        conn.execute("INSERT INTO receipts VALUES(?,?,?,?)", (str(root / "receipt.json"), "a" * 64, "ARCHIVE", "now"))
        conn.commit()
    finally:
        conn.close()
    return db


def test_blocks_active_pin_and_allows_unpinned_verified_target():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw); active = root / "active"; active.mkdir(); cold = root / "cold"; cold.mkdir()
        receipt = root / "receipt.json"; receipt.write_text("{}")
        db = build_catalog(root)
        conn = sqlite3.connect(db)
        try:
            conn.execute("INSERT INTO pins VALUES(?,?,?,?,?)", ("run", str(active), "PIN_ACTIVE", "now", "live"))
            conn.commit()
        finally:
            conn.close()
        blocked = invoke(db, active, receipt)
        assert blocked.returncode == 2 and json.loads(blocked.stdout)["reason"] == "active-pin-overlap"
        allowed = invoke(db, cold, receipt)
        assert allowed.returncode == 0 and json.loads(allowed.stdout)["status"] == "PREFLIGHT_PASS"
