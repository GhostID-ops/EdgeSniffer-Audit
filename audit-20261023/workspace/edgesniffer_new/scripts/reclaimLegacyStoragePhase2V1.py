#!/usr/bin/env python3
"""Manifest and reclaim a second allowlist of rebuildable legacy storage."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil


OUTPUT = pathlib.Path(
    "/var/lib/edgesniffer-storage-offload-v3/manifests/main-lane-legacy-retirement-v1"
)
DELETE_PATHS = (
    "/root/bot/FinalBot-cya/rust-sidecar/target/debug",
    "/root/bot/EdgeSniffer/rust-sidecar/target",
    "/root/.cargo/registry",
    "/var/lib/edgesniffer-teacher-impulse-parity-v1/research/production-candidate-path-v1",
    "/var/lib/edgesniffer-storage-offload-v3/research",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/executable-alpha-static-threshold-forensics-v1",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/executable-model-score-economics-audit-v1",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/executable-model-score-economics-audit-v1-smoke",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/executable-alpha-ranking-engine-v2-full-universe-smoke",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/durable-executable-fingerprint-v2-smoke",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/memecoin-executable-alpha-atlas-v1-smoke",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-prospective-shadow-v1-aborted-execution-semantics-20260903T054115Z",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-prospective-shadow-v1-aborted-semantic-blockers-20260903T054438Z",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-canonical-event-gated-reconciliation",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-source-recovery-parity-v1",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-scorer-direct-emitter-v1-attempt-20260904T195518Z",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-scorer-direct-emitter-v1-attempt-20260904T203622Z",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-scorer-direct-emitter-v1-attempt-20260904T220109Z-grpc-timeout-cache-blocker",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/model-a-1s-prospective-v2-20260902T221912Z",
)


def size(path: pathlib.Path) -> int:
    if not path.exists():
        return 0
    if path.is_file() or path.is_symlink():
        return path.lstat().st_size
    return sum(entry.stat().st_size for entry in path.rglob("*") if entry.is_file())


def open_users(target: pathlib.Path) -> list[dict[str, str]]:
    users: list[dict[str, str]] = []
    target_text = str(target.resolve())
    for process in pathlib.Path("/proc").glob("[0-9]*"):
        for descriptor in (process / "fd").glob("*"):
            try:
                linked = str(descriptor.resolve())
            except OSError:
                continue
            if linked == target_text or linked.startswith(target_text + os.sep):
                try:
                    command = (process / "cmdline").read_bytes().replace(b"\0", b" ").decode()
                except OSError:
                    command = ""
                users.append({"pid": process.name, "fd": descriptor.name, "command": command})
    return users


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUTPUT / "phase2-preservation-deletion-manifest.json"
    if not args.execute:
        paths = []
        for value in DELETE_PATHS:
            path = pathlib.Path(value)
            paths.append(
                {
                    "path": value,
                    "sizeBytes": size(path),
                    "classification": "SAFE_DELETE_REBUILDABLE_OR_SUPERSEDED_PROXY_DATA",
                    "openUsers": open_users(path),
                }
            )
        report = {
            "schema": "MAIN_LANE_LEGACY_RETIREMENT_PHASE2_V1",
            "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "deletePaths": paths,
            "preserved": [
                "/usr/local/bin/pump-exact-evidence-ledger-v1",
                "/root/bot/FinalBot-cya/rust-sidecar/target/release",
                "/var/lib/pump-exact-evidence-ledger-v1",
                "/root/bot/EdgeSniffer/runtime/research-cache/ranker-v2-exact-curve-rpc-v1.sqlite",
                "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/exact-curve-economics-v1",
                "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/exact-curve-independent-redteam-v1",
                "/root/bot/8ycb-sniper",
                "/var/lib/8ycb-sniper",
            ],
            "allDeletePathsUnused": all(not row["openUsers"] for row in paths),
            "expectedReclaimBytes": sum(row["sizeBytes"] for row in paths),
        }
        manifest_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    manifest = json.loads(manifest_path.read_text())
    blockers = [row for row in manifest["deletePaths"] if open_users(pathlib.Path(row["path"]))]
    if blockers:
        print(json.dumps({"classification": "BLOCKED_OPEN_USERS", "blockers": blockers}, indent=2))
        return 2
    deleted = []
    for row in manifest["deletePaths"]:
        path = pathlib.Path(row["path"])
        before = size(path)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
        deleted.append({"path": row["path"], "deletedBytes": before})
    report = {
        "schema": "MAIN_LANE_LEGACY_RETIREMENT_PHASE2_V1_EXECUTION",
        "executedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "classification": "PASS",
        "deleted": deleted,
        "reclaimedBytes": sum(row["deletedBytes"] for row in deleted),
    }
    (OUTPUT / "phase2-execution.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
