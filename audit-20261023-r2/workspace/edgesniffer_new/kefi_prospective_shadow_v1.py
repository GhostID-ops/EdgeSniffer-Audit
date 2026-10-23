#!/usr/bin/env python3
"""Outcome-blind prospective coordinator for the frozen kEFi control lane.

This process does not scan source evidence, score new rows, or submit trades.
It consumes only newly sealed, hash-verified control receipts produced by the
existing market-control lane and records a prospective comparison window.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

CONTROL_OUT = Path("/root/kefi-work/out/kefi-market-controls-v1")
CONTROL_OUTS = [Path(p) for p in os.environ.get("KEFI_CONTROL_OUTS", str(CONTROL_OUT)).split(",") if p]
OUT = Path("/root/kefi-work/out/kefi-prospective-shadow-v1")
MODEL = Path("/root/kefi-work/out/independent-v1/fingerprint-discovery-v1.json")
EXPECTED_MODEL = "e13ceacd57462addf03ee2c1c7ad40ca0db1aa9936c64ceaa44e2cfca06eead4"
POLL_SECONDS = 60
TARGET_HOURS = 72
EARLY_REVIEW_HOURS = 24
AMENDMENT = OUT / "protocol-amendment.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def parse_partition_start(value: str) -> float:
    return datetime.strptime(value, "%Y/%m/%d/%H").replace(tzinfo=timezone.utc).timestamp()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def make_start_receipt() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    start = now_utc()
    existing = []
    for receipt_path in sorted(CONTROL_OUT.glob("*.receipt.json")):
        try:
            receipt = load_json(receipt_path)
            if receipt.get("status") == "COMPLETE":
                existing.append({"path": str(receipt_path), "sha256": sha256(receipt_path)})
        except (OSError, ValueError, TypeError):
            continue
    receipt = {
        "schema": "KEFI_PROSPECTIVE_SHADOW_START_V1",
        "status": "RUNNING",
        "started_at_utc": start,
        "target_hours": TARGET_HOURS,
        "early_review_hours": EARLY_REVIEW_HOURS,
        "control_receipt_cutoff": existing,
        "model_path": str(MODEL),
        "model_sha256": sha256(MODEL),
        "expected_model_sha256": EXPECTED_MODEL,
        "outcome_blind": True,
        "execution_enabled": False,
        "outcomes_accessed": False,
        "pnl_accessed": False,
        "selection_or_threshold_tuning": False,
        "scientific_scope": "prospective market-wide frozen-model signal shadow; no trade submission",
    }
    if receipt["model_sha256"] != EXPECTED_MODEL:
        raise RuntimeError("frozen model hash mismatch")
    atomic_json(OUT / "start-receipt.json", receipt)
    (OUT / "start-receipt.sha256").write_text(sha256(OUT / "start-receipt.json") + "\n")
    return receipt


def verify_batch(summary_path: Path, receipt_path: Path, start_epoch: float, min_source_epoch: float) -> dict | None:
    summary = load_json(summary_path)
    receipt = load_json(receipt_path)
    created = summary.get("created_at_utc")
    if not created or parse_utc(created) <= start_epoch:
        return None
    if receipt.get("status") != "COMPLETE":
        raise RuntimeError(f"receipt is not complete: {receipt_path.name}")
    if receipt.get("model_sha256") != EXPECTED_MODEL or summary.get("model_sha256") != EXPECTED_MODEL:
        raise RuntimeError(f"model hash mismatch: {receipt_path.name}")
    guards = summary.get("scientific_guards", {})
    outcome_blind = receipt.get("outcome_blind", summary.get("outcome_blind"))
    outcomes_accessed = receipt.get("outcomes_accessed", summary.get("outcomes_accessed", guards.get("outcomes_accessed")))
    pnl_accessed = receipt.get("pnl_accessed", summary.get("pnl_accessed", guards.get("pnl_accessed")))
    execution_enabled = receipt.get("execution_enabled", summary.get("execution_enabled", guards.get("execution_enabled")))
    if outcome_blind is not True or outcomes_accessed is not False:
        raise RuntimeError(f"outcome guard failed: {receipt_path.name}")
    if pnl_accessed is not False or execution_enabled is not False:
        raise RuntimeError(f"execution/outcome guard failed: {receipt_path.name}")
    partitions = receipt.get("partitions", [])
    if not partitions or parse_partition_start(partitions[0]) < min_source_epoch:
        return None
    if receipt.get("summary_sha256") != sha256(summary_path):
        raise RuntimeError(f"summary hash mismatch: {receipt_path.name}")
    signal_path = receipt_path.parent / receipt_path.name.replace(".receipt.json", ".signals.jsonl")
    if not signal_path.exists() or receipt.get("signals_sha256") != sha256(signal_path):
        raise RuntimeError(f"signal hash mismatch or missing: {receipt_path.name}")
    key = receipt_path.name.removesuffix(".receipt.json")
    return {
        "key": key,
        "summary_path": str(summary_path),
        "receipt_path": str(receipt_path),
        "summary_sha256": sha256(summary_path),
        "receipt_sha256": sha256(receipt_path),
        "created_at_utc": created,
        "partitions": receipt.get("partitions", []),
        "controls_selected": int(receipt.get("controls_selected", summary.get("candidate_rows", 0))),
        "complete_windows": int(summary.get("complete_windows", 0)),
        "paper_matches": int(summary.get("paper_matches", 0)),
        "paper_non_matches": int(summary.get("paper_non_matches", 0)),
        "rejects": summary.get("rejects", {}),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    start_path = OUT / "start-receipt.json"
    if start_path.exists():
        start = load_json(start_path)
    else:
        start = make_start_receipt()
    if start.get("model_sha256") != EXPECTED_MODEL:
        raise RuntimeError("start receipt model hash mismatch")
    protocol = load_json(AMENDMENT) if AMENDMENT.exists() else {
        "target_hours": int(start.get("target_hours", TARGET_HOURS)),
        "early_review_hours": EARLY_REVIEW_HOURS,
    }
    target_hours = int(protocol["target_hours"])
    early_review_hours = int(protocol.get("early_review_hours", EARLY_REVIEW_HOURS))
    minimum_complete_windows = int(protocol.get("minimum_complete_control_windows", 0))
    minimum_source_partition_start_utc = protocol.get("minimum_source_partition_start_utc")
    min_source_epoch = parse_utc(minimum_source_partition_start_utc) if minimum_source_partition_start_utc else start_epoch
    start_epoch = parse_utc(start["started_at_utc"])
    started = start_epoch
    processed = set()
    state_path = OUT / "checkpoint.json"
    if state_path.exists():
        state = load_json(state_path)
        processed.update(state.get("processed_receipts", []))
    batches: list[dict] = []
    if state_path.exists():
        batches.extend(load_json(state_path).get("batches", []))
    while True:
        summary_paths = []
        for control_dir in CONTROL_OUTS:
            summary_paths.extend(control_dir.glob("*.summary.json"))
        for summary_path in sorted(summary_paths):
            key = summary_path.name.removesuffix(".summary.json")
            if key in processed:
                continue
            receipt_path = summary_path.parent / f"{key}.receipt.json"
            if not receipt_path.exists():
                continue
            batch = verify_batch(summary_path, receipt_path, start_epoch, min_source_epoch)
            if batch is None:
                processed.add(key)
                continue
            batches.append(batch)
            processed.add(key)
        complete = sum(b["complete_windows"] for b in batches)
        matches = sum(b["paper_matches"] for b in batches)
        non_matches = sum(b["paper_non_matches"] for b in batches)
        selected = sum(b["controls_selected"] for b in batches)
        reject_counts: dict[str, int] = {}
        for batch in batches:
            for reason, count in batch["rejects"].items():
                reject_counts[reason] = reject_counts.get(reason, 0) + int(count)
        elapsed = max(0.0, time.time() - started)
        checkpoint = {
            "schema": "KEFI_PROSPECTIVE_SHADOW_CHECKPOINT_V1",
            "status": "RUNNING" if elapsed < target_hours * 3600 or complete < minimum_complete_windows else "COMPLETE",
            "started_at_utc": start["started_at_utc"],
            "updated_at_utc": now_utc(),
            "elapsed_hours": elapsed / 3600.0,
            "target_hours": target_hours,
            "early_review_hours": early_review_hours,
            "minimum_complete_control_windows": minimum_complete_windows,
            "protocol_amendment": str(AMENDMENT) if AMENDMENT.exists() else None,
            "protocol_amendment_sha256": sha256(AMENDMENT) if AMENDMENT.exists() else None,
            "early_review_ready": elapsed >= early_review_hours * 3600,
            "model_sha256": EXPECTED_MODEL,
            "outcome_blind": True,
            "execution_enabled": False,
            "outcomes_accessed": False,
            "pnl_accessed": False,
            "controls_selected": selected,
            "complete_windows": complete,
            "paper_matches": matches,
            "paper_non_matches": non_matches,
            "rejects": reject_counts,
            "match_rate_on_complete": (matches / complete) if complete else None,
            "processed_receipts": sorted(processed),
            "batches": batches,
        }
        atomic_json(state_path, checkpoint)
        if checkpoint["status"] == "COMPLETE":
            atomic_json(OUT / "completion-receipt.json", {
                "schema": "KEFI_PROSPECTIVE_SHADOW_COMPLETION_V1",
                "status": "COMPLETE",
                "checkpoint_sha256": sha256(state_path),
                "completed_at_utc": now_utc(),
                "model_sha256": EXPECTED_MODEL,
                "outcome_blind": True,
                "execution_enabled": False,
                "outcomes_accessed": False,
                "pnl_accessed": False,
            })
            return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
