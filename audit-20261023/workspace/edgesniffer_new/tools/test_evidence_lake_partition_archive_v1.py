import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("evidence_lake_partition_archive_v1.py")


def invoke(source, archive, ident, archive_it=False):
    args = [sys.executable, str(SCRIPT), "--source", str(source), "--archive-root", str(archive), "--partition-id", ident]
    if archive_it: args.append("--archive")
    return subprocess.run(args, text=True, capture_output=True)


def test_plan_archive_and_idempotent_replay():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw); source = root / "sealed"; source.mkdir()
        (source / "frames.jsonl").write_text("one\ntwo\n")
        (source / "nested").mkdir(); (source / "nested" / "index.bin").write_bytes(b"abc")
        archive = root / "archive"
        plan = invoke(source, archive, "plane/2026-10-08T00")
        assert plan.returncode == 0 and json.loads(plan.stdout)["mode"] == "PLAN_ONLY"
        done = invoke(source, archive, "plane/2026-10-08T00", True)
        result = json.loads(done.stdout); assert done.returncode == 0 and result["status"] == "NFS_VERIFIED"
        again = invoke(source, archive, "plane/2026-10-08T00", True)
        assert again.returncode == 0 and json.loads(again.stdout)["status"] == "ALREADY_VERIFIED"
