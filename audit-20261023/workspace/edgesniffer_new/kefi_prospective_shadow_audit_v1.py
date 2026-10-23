#!/usr/bin/env python3
"""Read-only structural audit for the prospective kEFi shadow run."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/kefi-work/out/kefi-prospective-shadow-v1")
CONTROL_DIRS = [
    Path("/root/kefi-work/out/kefi-market-controls-v1"),
    Path("/root/kefi-work/out/kefi-market-controls-catchup-v1"),
]
EXPECTED_MODEL = "e13ceacd57462addf03ee2c1c7ad40ca0db1aa9936c64ceaa44e2cfca06eead4"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def partition_epoch(value: str) -> float:
    return datetime.strptime(value, "%Y/%m/%d/%H").replace(tzinfo=timezone.utc).timestamp()


def utc_epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def main() -> int:
    findings: list[str] = []
    start_path = ROOT / "start-receipt.json"
    amendment_path = ROOT / "protocol-amendment.json"
    checkpoint_path = ROOT / "checkpoint.json"
    for path in (start_path, amendment_path, checkpoint_path):
        if not path.exists():
            findings.append(f"missing required artifact: {path}")
    if findings:
        print(json.dumps({"status": "FAIL", "findings": findings}, indent=2))
        return 1

    start = load(start_path)
    amendment = load(amendment_path)
    checkpoint = load(checkpoint_path)
    start_epoch = utc_epoch(start["started_at_utc"])
    min_source_epoch = utc_epoch(amendment["minimum_source_partition_start_utc"])
    target_hours = int(amendment["target_hours"])
    minimum_windows = int(amendment["minimum_complete_control_windows"])

    # The start receipt intentionally stores its external hash in a sidecar;
    # requiring that sidecar avoids a self-referential JSON hash.
    hash_path = ROOT / "start-receipt.sha256"
    if not hash_path.exists() or hash_path.read_text().strip() != sha(start_path):
        findings.append("start receipt hash sidecar mismatch")
    if sha(Path("/root/kefi-work/out/independent-v1/fingerprint-discovery-v1.json")) != EXPECTED_MODEL:
        findings.append("frozen model hash mismatch")
    if start.get("model_sha256") != EXPECTED_MODEL or amendment.get("start_receipt_sha256") != sha(start_path):
        findings.append("start/amendment model or receipt identity mismatch")
    if amendment.get("status") != "FROZEN_BEFORE_SHADOW_OBSERVATIONS":
        findings.append("amendment was not frozen before observations")

    seen: dict[str, str] = {}
    batches = checkpoint.get("batches", [])
    totals = {"controls_selected": 0, "complete_windows": 0, "paper_matches": 0, "paper_non_matches": 0}
    reject_totals: dict[str, int] = {}
    for batch in batches:
        key = batch.get("key")
        if not key:
            findings.append("batch without key")
            continue
        receipt_path = Path(batch["receipt_path"])
        summary_path = Path(batch["summary_path"])
        for path in (receipt_path, summary_path):
            if not path.exists():
                findings.append(f"missing batch artifact: {path}")
        if not receipt_path.exists() or not summary_path.exists():
            continue
        receipt = load(receipt_path)
        summary = load(summary_path)
        if key in seen and seen[key] != str(receipt_path):
            findings.append(f"duplicate batch key across namespaces: {key}")
        seen[key] = str(receipt_path)
        if batch.get("summary_sha256") != sha(summary_path) or receipt.get("summary_sha256") != sha(summary_path):
            findings.append(f"summary hash mismatch: {key}")
        signal_path = receipt_path.with_name(receipt_path.name.replace(".receipt.json", ".signals.jsonl"))
        if not signal_path.exists() or receipt.get("signals_sha256") != sha(signal_path):
            findings.append(f"signal hash mismatch: {key}")
        if receipt.get("model_sha256") != EXPECTED_MODEL or summary.get("model_sha256") != EXPECTED_MODEL:
            findings.append(f"model drift: {key}")
        guards = summary.get("scientific_guards", {})
        if receipt.get("outcome_blind") is not True or guards.get("outcomes_accessed") is not False or guards.get("pnl_accessed") is not False or guards.get("execution_enabled") is not False:
            findings.append(f"scientific safety flag failure: {key}")
        parts = receipt.get("partitions", [])
        if not parts or partition_epoch(parts[0]) < min_source_epoch:
            findings.append(f"pre-boundary source partition admitted: {key}")
        if int(receipt.get("complete_windows", -1)) != int(summary.get("complete_windows", -2)):
            findings.append(f"receipt/summary count mismatch: {key}")
        for field in totals:
            totals[field] += int(receipt.get(field, summary.get(field, 0))) if field == "controls_selected" else int(summary.get(field, 0))
        for reason, count in summary.get("rejects", {}).items():
            reject_totals[reason] = reject_totals.get(reason, 0) + int(count)

    for field, value in totals.items():
        if int(checkpoint.get(field, -1)) != value:
            findings.append(f"checkpoint total mismatch: {field}")
    if checkpoint.get("model_sha256") != EXPECTED_MODEL:
        findings.append("checkpoint model hash mismatch")
    if checkpoint.get("outcomes_accessed") is not False or checkpoint.get("pnl_accessed") is not False or checkpoint.get("execution_enabled") is not False:
        findings.append("checkpoint safety flag failure")

    elapsed = float(checkpoint.get("elapsed_hours", 0.0))
    ready = elapsed >= target_hours and totals["complete_windows"] >= minimum_windows
    result = {
        "schema": "KEFI_PROSPECTIVE_SHADOW_AUDIT_V1",
        "audited_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PASS_READY" if ready and not findings else ("PASS_NOT_READY" if not findings else "FAIL"),
        "findings": findings,
        "ready_for_final_closeout": ready and not findings,
        "elapsed_hours": elapsed,
        "target_hours": target_hours,
        "minimum_complete_control_windows": minimum_windows,
        "totals": totals,
        "rejects": reject_totals,
        "batches_audited": len(batches),
        "model_sha256": EXPECTED_MODEL,
        "outcomes_accessed": False,
        "pnl_accessed": False,
        "execution_enabled": False,
    }
    out = ROOT / "audit-latest.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    result["audit_sha256"] = sha(out)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
