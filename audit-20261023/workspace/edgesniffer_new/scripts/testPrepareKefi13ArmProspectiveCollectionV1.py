#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepareKefi13ArmProspectiveCollectionV1.py"


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        output_root = Path(temporary) / "prospective"
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(output_root), "--remote-service", "edgesniffer-2cq-capture-first-v1.service", "--remote-source", "allenhark://127.0.0.1:9999"], cwd=ROOT, text=True, capture_output=True)
        output = output_root / "collection-manifest.json"
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["collection_mode"] == "RECEIVE_ONLY_OUTCOME_BLIND"
    assert manifest["status"] == "RUNNING_RECEIVE_ONLY_SOURCE_NOT_YET_PARTITIONED"
    assert len(manifest["partitions"]) == 13
    assert len({row["arm_id"] for row in manifest["partitions"]}) == 13
    assert all(row["allow_pnl_read"] is False for row in manifest["partitions"])
    assert all(row["allow_submissions"] is False for row in manifest["partitions"])
    assert all(row["allow_cross_arm_pooling"] is False for row in manifest["partitions"])
    assert manifest["service_start_performed"] is True
    assert manifest["no_service_restart_performed"] is True
    print("PASS testPrepareKefi13ArmProspectiveCollectionV1")

    with tempfile.TemporaryDirectory() as temporary:
        output_root = Path(temporary) / "prospective"
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(output_root), "--remote-service", "edgesniffer-kefi-full-meta-capture-first-v5.service", "--remote-source", "shyft-yellowstone://grpc.fra.shyft.to", "--service-restart-performed"], cwd=ROOT, text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        restarted = json.loads((output_root / "collection-manifest.json").read_text(encoding="utf-8"))
    assert restarted["no_service_restart_performed"] is False


if __name__ == "__main__":
    main()
