#!/usr/bin/env python3
"""Create an immutable, self-contained replacement-holdout launch contract."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path

SCHEMA = "KEFI_REPLACEMENT_HOLDOUT_LAUNCH_MANIFEST_V1"

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def write_once(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "wb") as f:
        f.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        f.flush(); os.fsync(f.fileno())

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--cohort-root", type=Path, required=True)
    p.add_argument("--boundary", type=Path, required=True)
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--ledger-manifest", type=Path, required=True)
    p.add_argument("--tail-sync-receipt", type=Path, required=True)
    p.add_argument("--source-health", type=Path, required=True)
    p.add_argument("--observer-binary", type=Path, required=True)
    p.add_argument("--finalizer", type=Path, required=True)
    p.add_argument("--launcher", type=Path, required=True)
    p.add_argument("--target", type=int, default=20)
    a = p.parse_args()
    for x in (a.boundary, a.ledger, a.ledger_manifest, a.tail_sync_receipt, a.source_health, a.observer_binary, a.finalizer, a.launcher):
        if not x.is_file(): raise SystemExit(f"FAIL_CLOSED: missing bound input {x}")
    b, ledger, tail, health = (json.loads(x.read_text()) for x in (a.boundary, a.ledger_manifest, a.tail_sync_receipt, a.source_health))
    if b.get("schema") != "KEFI_REPLACEMENT_UNTOUCHED_SELECTION_HOLDOUT_V1_BOUNDARY" or b.get("status") != "OPEN": raise SystemExit("FAIL_CLOSED: boundary is not OPEN replacement holdout")
    if a.boundary.parent != a.cohort_root: raise SystemExit("FAIL_CLOSED: boundary parent/root mismatch")
    if int(b.get("target_valid_first_observed_selections", -1)) != a.target or a.target != 20: raise SystemExit("FAIL_CLOSED: exact target must be 20")
    if ledger.get("ledger_path") != str(a.ledger) or ledger.get("ledger_sha256") != sha(a.ledger): raise SystemExit("FAIL_CLOSED: ledger manifest mismatch")
    if tail.get("actions_sha256") != ledger.get("wallet_actions_sha256") or int(tail.get("replay_overlap_slots", 0)) < 32: raise SystemExit("FAIL_CLOSED: tail sync is not ledger-bound with 32 slot overlap")
    if health.get("sourceInstanceId") != b.get("source_instance_id") or health.get("currentEpoch") != b.get("source_continuity_epoch"): raise SystemExit("FAIL_CLOSED: source identity/epoch differs from boundary")
    if any(int(health.get(k, -1)) != 0 for k in ("reconnects", "receiveErrors", "continuityGaps")): raise SystemExit("FAIL_CLOSED: source health has continuity failure")
    value = {"schema": SCHEMA, "cohort_root": str(a.cohort_root), "boundary_path": str(a.boundary), "boundary_sha256": sha(a.boundary), "ledger_path": str(a.ledger), "ledger_manifest_path": str(a.ledger_manifest), "ledger_manifest_sha256": sha(a.ledger_manifest), "ledger_sha256": sha(a.ledger), "ledger_initial_bytes": a.ledger.stat().st_size, "ledger_initial_record_count": ledger["record_count"], "tail_sync_receipt_path": str(a.tail_sync_receipt), "tail_sync_receipt_sha256": sha(a.tail_sync_receipt), "source_health_path": str(a.source_health), "source_instance_id": health["sourceInstanceId"], "continuity_epoch": health["currentEpoch"], "observer_binary": str(a.observer_binary), "observer_binary_sha256": sha(a.observer_binary), "finalizer_path": str(a.finalizer), "finalizer_sha256": sha(a.finalizer), "launcher_path": str(a.launcher), "launcher_sha256": sha(a.launcher), "wallet_history_boundary_unix_ns": ledger["ledger_boundary_unix_ns"], "source_replay_from_slot": tail["replay_from_slot"], "target_valid_first_observed_selections": 20, "outcomes_or_pnl_accessed": False}
    write_once(a.output, value)
    print(json.dumps({"manifest": str(a.output), "sha256": sha(a.output)}, sort_keys=True))
    return 0
if __name__ == "__main__": raise SystemExit(main())
