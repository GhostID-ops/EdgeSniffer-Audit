import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("evidence_lake_tree_transparent_release_v1.py")


def tree_identity(root: Path) -> str:
    import hashlib
    entries = []
    for item in sorted(root.rglob("*")):
        if item.is_file():
            entries.append({"path": str(item.relative_to(root)), "bytes": item.stat().st_size,
                            "sha256": hashlib.sha256(item.read_bytes()).hexdigest()})
    return hashlib.sha256((json.dumps({"schema":"partition-tree-manifest-v1","files":entries}, sort_keys=True,
                                       separators=(",", ":")) + "\n").encode()).hexdigest()


def test_directory_release_keeps_verified_same_path_link():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        local = root / "local"; archive = root / "archive"
        local.mkdir(); archive.mkdir()
        for target in (local, archive):
            (target / "nested").mkdir(); (target / "nested" / "x").write_text("proof")
        identity = tree_identity(local)
        receipt = root / "archive.json"
        receipt.write_text(json.dumps({"status":"NFS_VERIFIED", "source":str(local),
                                       "source_manifest_sha256":identity}))
        audit = root / "audit.json"; audit.write_text(json.dumps({"outcome":"DEPENDENCY_NONE_PROVEN"}))
        result = subprocess.run([sys.executable, str(SCRIPT), "--local-tree", str(local), "--archive-tree", str(archive),
                                 "--archive-receipt", str(receipt), "--dependency-audit", str(audit),
                                 "--release-receipt-dir", str(root / "receipts"), "--mode", "RELEASE"],
                                text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert local.is_symlink()
        assert (local / "nested" / "x").read_text() == "proof"
