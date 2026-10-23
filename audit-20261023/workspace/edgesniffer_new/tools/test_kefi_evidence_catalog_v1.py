import json
import subprocess
import sys
from pathlib import Path


CATALOG = Path(__file__).parents[1] / "scripts" / "kefi_evidence_catalog_v1.py"


def sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_catalog_is_reference_only_and_immutable(tmp_path):
    control = tmp_path / "control"
    rows = control / "market-rows" / "window.jsonl"
    rows.parent.mkdir(parents=True)
    rows.write_text('{"event":1}\n')
    market = control / "market-deduplicated.jsonl"
    market.write_text('{"event":1}\n')
    (control / "state.json").write_text('{"processed":["window"]}')
    (control / "checkpoint.json").write_text(json.dumps({"deduplicated_market_sha256": sha256(market), "deduplicated_market_rows": 1, "overlap_duplicates_verified_identical": 0}))
    receipt = control / "receipts" / "window.json"
    receipt.parent.mkdir()
    receipt.write_text(json.dumps({"window_id":"window","window_manifest_sha256":"manifest","market_rows_path":str(rows),"market_rows_sha256":sha256(rows),"candidate_signatures":["teacher"]}))
    source = tmp_path / "source" / "scientific-boundaries"
    source.mkdir(parents=True)
    (source / "paper-boundary-v1.json").write_text('{"status":"ACTIVE"}')
    command = [sys.executable, str(CATALOG), "--control-root", str(control), "--source-root", str(tmp_path / "source"), "--catalog-root", str(tmp_path / "catalog"), "--catalog-id", "test", "--outcome-open-cutoff-utc", "2026-10-11T20:23:53Z", "--verify-market-rows"]
    first = subprocess.run(command, text=True, capture_output=True)
    second = subprocess.run(command, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    assert second.returncode != 0
    assert "FAIL_CLOSED: catalog already exists" in second.stderr
    catalog = json.loads((tmp_path / "catalog" / "test.json").read_text())
    assert catalog["research_contract"]["reference_only"] is True
    assert catalog["research_contract"]["raw_capture_copy_created"] is False
