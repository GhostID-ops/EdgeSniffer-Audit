#!/usr/bin/env python3
"""Regression checks for replacement-holdout boundary and freeze plumbing."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOUNDARY = ROOT / "tools" / "kefi_replacement_holdout_boundary_v1.py"
FREEZE = ROOT / "tools" / "kefi_replacement_holdout_freeze_v1.py"


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value), encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        inputs = {}
        for name in ("development", "old", "baseline", "model", "freeze", "binary"):
            path = tmp / name
            path.write_bytes(name.encode())
            inputs[name] = path
        source_health = tmp / "source-health.json"
        write(source_health, {"sourceInstanceId": "fresh-source", "currentEpoch": 1, "reconnects": 0, "receiveErrors": 0, "continuityGaps": 0})
        ledger_root = tmp / "ledger"
        ledger_root.mkdir()
        ledger_record = {"record_sha256": "record-1", "sequence": 1, "mint": "Mint1"}
        write(ledger_root / "ledger.jsonl", ledger_record)
        ledger_manifest = ledger_root / "manifest.json"
        write(ledger_manifest, {"schema": "KEFI_OBSERVED_WALLET_MINT_LEDGER_MANIFEST_V1", "boundary_ambiguous_actions": 0})
        cohort_root = tmp / "cohort"
        command = [sys.executable, str(BOUNDARY), "--root", str(cohort_root), "--development-validity", str(inputs["development"]), "--old-holdout-failure", str(inputs["old"]), "--ledger-manifest", str(ledger_manifest), "--frozen-baseline", str(inputs["baseline"]), "--expanded-model", str(inputs["model"]), "--development-freeze", str(inputs["freeze"]), "--observer-binary", str(inputs["binary"]), "--source-health", str(source_health), "--boundary-utc", "2026-10-14T02:00:00Z"]
        subprocess.run(command, check=True, capture_output=True, text=True)
        assert (cohort_root / "boundary.json").is_file()
        rerun = subprocess.run(command, capture_output=True, text=True)
        assert rerun.returncode != 0 and "already exists" in rerun.stderr
        bad_health = tmp / "bad-source-health.json"
        write(bad_health, {"sourceInstanceId": "broken", "currentEpoch": 2, "reconnects": 1, "receiveErrors": 0, "continuityGaps": 1})
        bad = [*command]
        bad[bad.index(str(cohort_root))] = str(tmp / "bad-cohort")
        bad[bad.index(str(source_health))] = str(bad_health)
        rejected = subprocess.run(bad, capture_output=True, text=True)
        assert rejected.returncode != 0 and "clean fresh continuity epoch" in rejected.stderr

        sealed = tmp / "sealed"
        write(sealed / "manifest.json", {"validation_status": "SNAPSHOT_TARGET_REACHED"})
        write(sealed / "validation.json", {"status": "SNAPSHOT_TARGET_REACHED"})
        ledger_lines = []
        for number in range(1, 21):
            record = {"record_sha256": f"record-{number}", "sequence": number, "mint": f"Mint{number}"}
            ledger_lines.append(canonical(record))
            snapshot = {"teacher_signature": f"signature-{number}", "chosen_mint": f"Mint{number}", "wallet_ledger_record_sha256": f"record-{number}", "wallet_ledger_sequence_before": number - 1, "candidates": [{"mint": f"Mint{number}"}, {"mint": "Alternative"}]}
            snapshot_path = sealed / "durable-snapshots" / f"{number:020}.json"
            write(snapshot_path, snapshot)
            write(sealed / "snapshot-receipts" / f"{number:020}.json", {"snapshot_sha256": sha(snapshot_path)})
        ledger_path = ledger_root / "ledger.jsonl"
        ledger_path.write_text("".join(ledger_lines), encoding="utf-8")
        subprocess.run([sys.executable, str(FREEZE), "--boundary", str(cohort_root / "boundary.json"), "--sealed-observer-root", str(sealed), "--ledger", str(ledger_path)], check=True, capture_output=True, text=True)
        result = json.loads((sealed / "replacement-holdout-manifest.json").read_text())
        assert result["selection_count"] == 20
        assert result["evaluation_status"] == "SEALED_UNOPENED"
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
