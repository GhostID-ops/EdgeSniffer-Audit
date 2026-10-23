#!/usr/bin/env python3
"""Create an immutable closeout manifest for a completed kEfi selection shadow."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canon(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.absolute()
    required = {name: root / name for name in [
        "start-receipt.json", "protocol-amendment.json", "checkpoint.json", "completion-receipt.json", "audit-latest.json",
    ]}
    absent = [name for name, path in required.items() if not path.is_file()]
    if absent:
        raise SystemExit("FAIL_CLOSED: missing " + ", ".join(absent))
    start = json.loads(required["start-receipt.json"].read_text())
    checkpoint = json.loads(required["checkpoint.json"].read_text())
    completion = json.loads(required["completion-receipt.json"].read_text())
    audit = json.loads(required["audit-latest.json"].read_text())
    if completion.get("status") != "COMPLETE" or audit.get("status") != "PASS_READY" or not audit.get("ready_for_final_closeout"):
        raise SystemExit("FAIL_CLOSED: run is not completion/audit ready")
    if any(checkpoint.get(key) is not False for key in ["outcomes_accessed", "pnl_accessed", "execution_enabled"]):
        raise SystemExit("FAIL_CLOSED: scientific safety flags are not clean")
    manifest = {
        "schema": "KEFI_SELECTION_SHADOW_CLOSEOUT_V1",
        "created_utc": now(),
        "status": "CLOSED_OUTCOME_BLIND_SELECTION_SHADOW",
        "run_started_at_utc": start["started_at_utc"],
        "run_completed_at_utc": completion["completed_at_utc"],
        "elapsed_hours": checkpoint["elapsed_hours"],
        "model_sha256": checkpoint["model_sha256"],
        "totals": audit["totals"],
        "rejects": audit["rejects"],
        "control_match_rate": checkpoint["match_rate_on_complete"],
        "scientific_scope": "selection selectivity only; not a pre-entry, profitability, execution, or PnL claim",
        "outcomes_accessed": False,
        "pnl_accessed": False,
        "execution_enabled": False,
        "artifacts": {name: {"path": str(path), "sha256": sha(path)} for name, path in required.items()},
        "next_stage_constraint": "Any future pre-entry study must use only PRE_ENTRY_STATE features under the separate timing contract.",
    }
    output = root / "selection-shadow-closeout.json"
    output.write_bytes(canon(manifest))
    print(json.dumps({"status": "CLOSEOUT_WRITTEN", "output": str(output), "sha256": sha(output),
                      "control_match_rate": manifest["control_match_rate"]}, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
