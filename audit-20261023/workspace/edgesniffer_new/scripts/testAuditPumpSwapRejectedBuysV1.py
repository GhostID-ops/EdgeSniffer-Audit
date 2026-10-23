import json
from pathlib import Path

from auditPumpSwapRejectedBuysV1 import sha256_file


def test_hash(tmp_path: Path):
    path = tmp_path / "x.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    assert len(sha256_file(path)) == 64
