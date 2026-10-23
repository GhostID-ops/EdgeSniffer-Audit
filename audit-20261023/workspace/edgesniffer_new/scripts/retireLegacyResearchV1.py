#!/usr/bin/env python3
"""Manifest and execute a narrowly allowlisted legacy research retirement."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any


LEDGER_BINARY_HASH = "8de9ec9713cb25ab2fd3e14d6123b47eb2bbb16fba36c4dc377537c338c0ca21"
MANIFEST_ROOT = pathlib.Path(
    "/var/lib/edgesniffer-storage-offload-v3/manifests/main-lane-legacy-retirement-v1"
)

RETIRE_UNITS = (
    "edgesniffer-ranker-v2-n25-tiny-live-v3-emitter.service",
    "edgesniffer-ranker-v2-n25-tiny-live-v3.service",
    "edgesniffer-ranker-v2-n25-v3-disk-guard.service",
    "edgesniffer-ranker-v2-production-translation-v1.service",
    "edgesniffer-ranker-v2-prospective-shadow-v2.service",
    "edgesniffer-model-a-1s-prospective-v2.service",
    "edgesniffer-teacher-impulse-bc100-ge2-mc5000-prospective-v1.service",
    "edgesniffer-teacher-impulse-bc100-ge2-net60-ge-0p5-v1.service",
    "edgesniffer-teacher-impulse-bc100-ge2-net60-ge-1p0-v1.service",
    "edgesniffer-teacher-impulse-bc100-ge2-net60-ge-2p0-v1.service",
    "edgesniffer-teacher-impulse-fast-flow-detector-v1.service",
    "edgesniffer-teacher-impulse-top3-flow-prospective-v1.service",
    "edgesniffer-teacher-impulse-parity-v1-observation-emitter.service",
    "edgesniffer-teacher-impulse-parity-v1-capsule-normalizer.service",
    "edgesniffer-teacher-impulse-parity-v1-nosubmit.service",
    "edgesniffer-teacher-impulse-mc5000-selected-event-adapter.service",
    "edgesniffer-teacher-impulse-production-rule-a-v1.service",
    "edgesniffer-teacher-impulse-parity-v1-child-mc5000-nosubmit.service",
    "edgesniffer-teacher-impulse-parity-v1-child-mc5000-dom55-nosubmit.service",
    "edgesniffer-teacher-impulse-parity-v1-child-mc5000-fb5000-nosubmit.service",
)

KEEP_UNITS = (
    "pump-exact-evidence-ledger-v1.service",
    "edgesniffer-teacher-impulse-parity-v1-source-feed.service",
    "edgesniffer-teacher-impulse-parity-v1-detected-swap-adapter.service",
    "edgesniffer-teacher-impulse-parity-v1-mayhem-supply-enricher.service",
    "edgesniffer-8ycb-prospective-shadow-v3.service",
    "edgesniffer-8ycb-production-translation-v1.service",
    "8ycb-sniper.service",
)

DELETE_PATHS = (
    "/var/lib/edgesniffer-teacher-impulse-parity-v1/candidate-capsules.jsonl",
    "/var/lib/edgesniffer-teacher-impulse-parity-v1/decision-inputs.jsonl",
    "/var/lib/edgesniffer-storage-offload-v3/decision-receipts.jsonl",
    "/var/lib/edgesniffer-teacher-impulse-parity-v1/live-mc5000-selected-events.jsonl",
    "/var/lib/edgesniffer-storage-offload-v3/research/bc100_ge2_mc5000_prospective_v1",
    "/var/lib/edgesniffer-storage-offload-v3/research/bc100_ge2_net60_ge_0p5_v1",
    "/var/lib/edgesniffer-storage-offload-v3/research/bc100_ge2_net60_ge_1p0_v1",
    "/var/lib/edgesniffer-storage-offload-v3/research/bc100_ge2_net60_ge_2p0_v1",
    "/var/lib/edgesniffer-storage-offload-v3/research/fast-flow-detector-v1",
    "/var/lib/edgesniffer-storage-offload-v3/research/top3-flow-prospective-v1",
    "/var/lib/edgesniffer-storage-offload-v3/research/full-universe-top3-flow-validation-v1",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-prospective-shadow-v2/service_health.jsonl",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-prospective-shadow-v2/all_scores.jsonl.gz",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-production-translation-v1",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/model-a-1s-prospective-v2-20260902T221912Z/all_1s_scores.jsonl.gz",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/model-a-1s-prospective-v2-20260902T221912Z/status.jsonl",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-scorer-direct-emitter-v1",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/executable-alpha-enrichment-v1",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/memecoin-executable-alpha-atlas-v1",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/durable-executable-fingerprint-v2",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/reacceleration-v1-forensics-v1",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/executable-alpha-ranking-engine-v2-full-universe",
)

PRESERVE_PATHS = (
    ("/var/lib/edgesniffer-teacher-impulse-parity-v1/normalized-observations.jsonl", "KEEP_UNIQUE_RAW", "Point-in-time normalized source evidence and active 8YCB input."),
    ("/var/lib/edgesniffer-teacher-impulse-parity-v1/detected-swaps.jsonl", "KEEP_UNIQUE_RAW", "Source swap evidence feeding normalized observations."),
    ("/root/bot/EdgeSniffer/runtime/research-cache/ranker-v2-exact-curve-rpc-v1.sqlite", "KEEP_UNIQUE_RAW", "Historical finalized transaction cache used by exact validation."),
    ("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/exact-curve-economics-v1", "KEEP_IMMUTABLE_AUDIT", "Validated exact evaluator evidence."),
    ("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/exact-curve-independent-redteam-v1", "KEEP_IMMUTABLE_AUDIT", "Independent red-team evidence and checksums."),
    ("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-exact-curve-relabel-v1", "KEEP_IMMUTABLE_AUDIT", "MC-proxy sign-reversal and retirement proof."),
    ("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-n25-tiny-live-v1", "KEEP_LIVE_EVIDENCE", "Ranker N25 V1 live evidence."),
    ("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-n25-tiny-live-v2", "KEEP_LIVE_EVIDENCE", "Ranker N25 V2 live evidence."),
    ("/root/bot/EdgeSniffer/runtime/ranker-v2-n25-tiny-live-v3", "KEEP_LIVE_EVIDENCE", "Ranker N25 V3 transaction, inventory, and exit evidence."),
    ("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/executable-alpha-ranker-v2-canonical-freeze", "KEEP_IMMUTABLE_AUDIT", "Frozen scorer and canonical specification."),
    ("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-prospective-shadow-v2/signals.jsonl.gz", "KEEP_IMMUTABLE_AUDIT", "Compact prospective Ranker signal evidence."),
    ("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-prospective-shadow-v2/outcomes.jsonl.gz", "KEEP_IMMUTABLE_AUDIT", "Compact prospective Ranker outcome evidence."),
    ("/var/lib/pump-exact-evidence-ledger-v1", "KEEP_ACTIVE_RUNTIME", "Active exact evidence ledger."),
    ("/root/bot/8ycb-sniper", "KEEP_ACTIVE_RUNTIME", "8YCB project, explicitly out of scope."),
    ("/var/lib/8ycb-sniper", "KEEP_ACTIVE_RUNTIME", "8YCB runtime, explicitly out of scope."),
)


def run(*args: str) -> str:
    return subprocess.run(args, text=True, capture_output=True, check=False).stdout.strip()


def size(path: pathlib.Path) -> int:
    if not path.exists() and not path.is_symlink():
        return 0
    if path.is_file() or path.is_symlink():
        return path.lstat().st_size
    output = run("du", "-sb", str(path)).split(maxsplit=1)
    return int(output[0]) if output else 0


def sha256(path: pathlib.Path) -> str | None:
    if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def state(unit: str) -> dict[str, str]:
    return {
        "unit": unit,
        "active": run("systemctl", "is-active", unit) or "unknown",
        "enabled": run("systemctl", "is-enabled", unit) or "unknown",
        "mainPid": run("systemctl", "show", unit, "-p", "MainPID", "--value") or "0",
    }


def open_fds(path: pathlib.Path) -> list[dict[str, Any]]:
    target = str(path.resolve()) if path.exists() else str(path)
    rows: list[dict[str, Any]] = []
    for fd in pathlib.Path("/proc").glob("[0-9]*/fd/*"):
        try:
            resolved = os.path.realpath(fd)
            if resolved == target or resolved.startswith(target.rstrip("/") + "/"):
                rows.append({"pid": int(fd.parts[2]), "fd": fd.name, "path": resolved})
        except (OSError, ValueError):
            continue
    return rows


def wallet_flat() -> bool:
    log = pathlib.Path(
        "/root/bot/EdgeSniffer/runtime/ranker-v2-n25-tiny-live-v3/pumpfun-sidecar.stdout.jsonl"
    )
    if not log.exists():
        return False
    last = None
    with log.open(errors="replace") as handle:
        for line in handle:
            if '"kind":"rustSelectedCandidateWalletReconcile"' in line:
                last = json.loads(line)
    return bool(last and last.get("managed") == 0 and last.get("positions") == 0 and last.get("unmanaged") == 0)


def build_manifest() -> dict[str, Any]:
    ledger_pid = int(run("systemctl", "show", "pump-exact-evidence-ledger-v1.service", "-p", "MainPID", "--value") or 0)
    keep_states = [state(unit) for unit in KEEP_UNITS]
    report = {
        "schema": "EDGESNIFFER_LEGACY_RESEARCH_RETIREMENT_V1",
        "generatedUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dfBefore": run("df", "-B1", "/", "/mnt/HC_Volume_106775198"),
        "ledger": {
            "pid": ledger_pid,
            "binarySha256": sha256(pathlib.Path("/usr/local/bin/pump-exact-evidence-ledger-v1")),
            "active": run("systemctl", "is-active", "pump-exact-evidence-ledger-v1.service"),
        },
        "walletFlat": wallet_flat(),
        "retireUnits": [state(unit) for unit in RETIRE_UNITS],
        "keepUnits": keep_states,
        "preservation": [
            {
                "path": raw,
                "classification": classification,
                "reason": reason,
                "exists": pathlib.Path(raw).exists(),
                "sizeBytes": size(pathlib.Path(raw)),
                "sha256": sha256(pathlib.Path(raw)),
            }
            for raw, classification, reason in PRESERVE_PATHS
        ],
        "deletions": [
            {
                "path": raw,
                "classification": "SUPERSEDED_PROXY_DATA",
                "reason": "Retired Ranker/MC-proxy derived output, reproducible from preserved normalized evidence and frozen source/specification.",
                "reproducible": True,
                "sizeBytes": size(pathlib.Path(raw)),
                "symlink": pathlib.Path(raw).is_symlink(),
                "openFdsBeforeRetirement": open_fds(pathlib.Path(raw)),
                "intendedAction": "DELETE_AFTER_WRITER_RETIREMENT",
            }
            for raw in DELETE_PATHS
        ],
    }
    report["totalDeleteBytes"] = sum(item["sizeBytes"] for item in report["deletions"])
    return report


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def execute(manifest: dict[str, Any]) -> int:
    if manifest["ledger"]["active"] != "active" or manifest["ledger"]["binarySha256"] != LEDGER_BINARY_HASH:
        raise SystemExit("exact ledger identity/health blocker")
    if not manifest["walletFlat"]:
        raise SystemExit("wallet is not authoritatively flat")
    for item in manifest["preservation"]:
        if not item["exists"]:
            raise SystemExit(f"preservation blocker: {item['path']}")
    keep_before = {item["unit"]: item for item in manifest["keepUnits"]}
    for unit in RETIRE_UNITS:
        subprocess.run(["systemctl", "disable", "--now", unit], check=False, capture_output=True)
    stopped = [state(unit) for unit in RETIRE_UNITS]
    if any(item["active"] not in ("inactive", "failed", "unknown") for item in stopped):
        raise SystemExit("one or more retirement units remain active")
    deleted: list[dict[str, Any]] = []
    for expected, item in zip(DELETE_PATHS, manifest["deletions"], strict=True):
        if item["path"] != expected:
            raise SystemExit("deletion manifest/order mismatch")
        path = pathlib.Path(expected)
        if path.is_symlink():
            raise SystemExit(f"refusing symlink deletion target: {path}")
        refs = open_fds(path) if path.exists() else []
        if refs:
            raise SystemExit(f"open file blocker for {path}: {refs[:5]}")
        before = size(path)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        deleted.append({"path": expected, "deletedBytes": before, "status": "DELETED" if before else "ABSENT"})
    keep_after = [state(unit) for unit in KEEP_UNITS]
    ledger_pid_after = int(run("systemctl", "show", "pump-exact-evidence-ledger-v1.service", "-p", "MainPID", "--value") or 0)
    blockers = []
    for item in keep_after:
        before = keep_before[item["unit"]]
        if before["active"] == "active" and item["active"] != "active":
            blockers.append(f"kept service stopped: {item['unit']}")
        if item["unit"] == "pump-exact-evidence-ledger-v1.service" and item["mainPid"] != str(manifest["ledger"]["pid"]):
            blockers.append("exact ledger PID changed")
    result = {
        "schema": "EDGESNIFFER_LEGACY_RESEARCH_RETIREMENT_EXECUTION_V1",
        "finishedUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "retiredUnits": stopped,
        "deleted": deleted,
        "deletedBytes": sum(item["deletedBytes"] for item in deleted),
        "dfAfter": run("df", "-B1", "/", "/mnt/HC_Volume_106775198"),
        "keepUnitsAfter": keep_after,
        "ledgerPidBefore": manifest["ledger"]["pid"],
        "ledgerPidAfter": ledger_pid_after,
        "walletFlatAfter": wallet_flat(),
        "deletedOpenFiles": run("lsof", "+L1"),
        "blockers": blockers,
        "status": "PASS" if not blockers else "BLOCKED",
    }
    write_json(MANIFEST_ROOT / "execution.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not blockers else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("audit", "execute"))
    args = parser.parse_args()
    MANIFEST_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_path = MANIFEST_ROOT / "manifest.json"
    if args.mode == "audit":
        manifest = build_manifest()
        write_json(manifest_path, manifest)
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    if not manifest_path.exists():
        raise SystemExit("manifest missing; run audit first")
    return execute(json.loads(manifest_path.read_text()))


if __name__ == "__main__":
    sys.exit(main())
