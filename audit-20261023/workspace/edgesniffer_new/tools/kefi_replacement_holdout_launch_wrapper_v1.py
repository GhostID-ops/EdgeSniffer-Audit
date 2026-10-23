#!/usr/bin/env python3
"""Fail-closed manifest verifier and only allowed observer launcher."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path

def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""): h.update(b)
    return h.hexdigest()
def fail(msg: str): raise SystemExit(f"FAIL_CLOSED: {msg}")
def verify(manifest_path: Path, allow_ledger_growth: bool = False) -> dict:
    m=json.loads(manifest_path.read_text())
    required=("cohort_root","boundary_path","ledger_path","ledger_manifest_path","tail_sync_receipt_path","source_health_path","observer_binary","finalizer_path","launcher_path")
    if any(not m.get(k) for k in required): fail("launch manifest missing required binding")
    if sha(Path(m["launcher_path"])) != m["launcher_sha256"]: fail("launcher hash mismatch")
    for key, digest in (("boundary_path","boundary_sha256"),("ledger_manifest_path","ledger_manifest_sha256"),("tail_sync_receipt_path","tail_sync_receipt_sha256"),("observer_binary","observer_binary_sha256"),("finalizer_path","finalizer_sha256")):
        if sha(Path(m[key])) != m[digest]: fail(f"{key} hash mismatch")
    ledger_path=Path(m["ledger_path"])
    if allow_ledger_growth:
        initial_bytes=int(m.get("ledger_initial_bytes", -1))
        if initial_bytes < 1 or ledger_path.stat().st_size < initial_bytes: fail("ledger growth prefix unavailable")
        if hashlib.sha256(ledger_path.read_bytes()[:initial_bytes]).hexdigest() != m["ledger_sha256"]: fail("ledger initial prefix mismatch")
    elif sha(ledger_path) != m["ledger_sha256"]: fail("ledger_path hash mismatch")
    b=json.loads(Path(m["boundary_path"]).read_text()); ledger=json.loads(Path(m["ledger_manifest_path"]).read_text()); health=json.loads(Path(m["source_health_path"]).read_text())
    if Path(m["boundary_path"]).parent != Path(m["cohort_root"]): fail("root/boundary mismatch")
    if b.get("status") != "OPEN" or int(b.get("target_valid_first_observed_selections",-1)) != 20: fail("boundary not open target=20")
    if ledger.get("ledger_path") != m["ledger_path"] or ledger.get("ledger_sha256") != m["ledger_sha256"]: fail("ledger binding mismatch")
    if health.get("sourceInstanceId") != m["source_instance_id"] or health.get("currentEpoch") != m["continuity_epoch"]: fail("source instance/epoch mismatch")
    if any(int(health.get(k,-1)) != 0 for k in ("reconnects","receiveErrors","continuityGaps")): fail("continuity failure")
    expected={"KEFI_POOL_STATE_ROOT":m["cohort_root"],"KEFI_OBSERVED_WALLET_LEDGER_PATH":m["ledger_path"],"KEFI_OBSERVED_WALLET_LEDGER_BOUNDARY_UNIX_NS":str(m["wallet_history_boundary_unix_ns"]),"KEFI_SOURCE_REPLAY_FROM_SLOT":str(m["source_replay_from_slot"]),"KEFI_POOL_STATE_SNAPSHOT_TARGET":"20"}
    for k,v in expected.items():
        if os.environ.get(k) not in (None,v): fail(f"environment {k} conflicts with launch manifest")
    return m
def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--manifest",type=Path); p.add_argument("--verify-only",action="store_true"); p.add_argument("--allow-ledger-growth",action="store_true"); a=p.parse_args()
    if a.manifest is None:
        if "KEFI_REPLACEMENT_HOLDOUT_MANIFEST" not in os.environ: fail("KEFI_REPLACEMENT_HOLDOUT_MANIFEST is required")
        a.manifest=Path(os.environ["KEFI_REPLACEMENT_HOLDOUT_MANIFEST"])
    m=verify(a.manifest, a.allow_ledger_growth)
    if a.verify_only: print(json.dumps({"status":"VERIFIED","manifest":str(a.manifest)})); return 0
    env=os.environ.copy(); env.update({"KEFI_POOL_STATE_ROOT":m["cohort_root"],"KEFI_OBSERVED_WALLET_LEDGER_PATH":m["ledger_path"],"KEFI_OBSERVED_WALLET_LEDGER_BOUNDARY_UNIX_NS":str(m["wallet_history_boundary_unix_ns"]),"KEFI_SOURCE_REPLAY_FROM_SLOT":str(m["source_replay_from_slot"]),"KEFI_POOL_STATE_SNAPSHOT_TARGET":"20"})
    os.execve(m["observer_binary"],[m["observer_binary"]],env)
if __name__=="__main__": raise SystemExit(main())
