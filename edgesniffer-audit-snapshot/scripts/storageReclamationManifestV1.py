#!/usr/bin/env python3
"""Build immutable storage preservation/deletion manifests for EdgeSniffer.

This script is deliberately read-only. Deletion is performed by a separate,
allowlisted executor after these manifests have been reviewed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
from typing import Any


def run(*args: str) -> str:
    return subprocess.run(args, check=False, text=True, capture_output=True).stdout.strip()


def size_bytes(path: pathlib.Path) -> int:
    if not path.exists() and not path.is_symlink():
        return 0
    if path.is_file() or path.is_symlink():
        return path.lstat().st_size
    value = run("du", "-sb", str(path)).split(maxsplit=1)
    return int(value[0]) if value else 0


def sha256(path: pathlib.Path) -> str | None:
    if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_fds(path: pathlib.Path) -> list[dict[str, Any]]:
    target = str(path.resolve()) if path.exists() else str(path)
    rows: list[dict[str, Any]] = []
    for proc in pathlib.Path("/proc").glob("[0-9]*"):
        try:
            cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            for fd in (proc / "fd").iterdir():
                try:
                    resolved = os.path.realpath(fd)
                except OSError:
                    continue
                if resolved == target or resolved.startswith(target.rstrip("/") + "/"):
                    rows.append({"pid": int(proc.name), "fd": fd.name, "cmd": cmd[:500], "path": resolved})
        except (OSError, PermissionError):
            continue
    return rows


def unit_state(units: str | list[str] | None) -> dict[str, Any]:
    names = [units] if isinstance(units, str) else list(units or [])
    states = [{"unit": unit, "state": run("systemctl", "is-active", unit) or "unknown"} for unit in names]
    return {"units": states, "active": any(row["state"] == "active" for row in states)}


def candidate(
    path: str,
    classification: str,
    reason: str,
    reproducible: bool,
    source: str | None,
    unit: str | list[str] | None = None,
) -> dict[str, Any]:
    target = pathlib.Path(path)
    fds = open_fds(target)
    service = unit_state(unit)
    safe = target.exists() and not fds and not service["active"]
    return {
        "absolute_path": path,
        "resolved_path": str(target.resolve()) if target.exists() else None,
        "size_bytes": size_bytes(target),
        "classification": classification if safe else "UNKNOWN",
        "reason_safe_to_delete": reason,
        "reproducible": reproducible,
        "source_artifact": source,
        "active_service_reference_check": service,
        "open_fds": fds,
        "symlink": target.is_symlink(),
        "intended_action": "DELETE" if safe else "KEEP",
    }


def preserved(path: str, classification: str, reason: str) -> dict[str, Any]:
    target = pathlib.Path(path)
    return {
        "absolute_path": path,
        "resolved_path": str(target.resolve()) if target.exists() else None,
        "exists": target.exists(),
        "size_bytes": size_bytes(target),
        "classification": classification,
        "reason": reason,
        "sha256": sha256(target),
        "open_fds": open_fds(target),
        "intended_action": "KEEP",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    before = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "df_bytes": run("df", "-B1", "/", "/mnt/HC_Volume_106775198"),
        "largest_root": run("du", "-xhd1", "/"),
        "largest_var_lib": run("du", "-xhd1", "/var/lib"),
        "largest_root_bot": run("du", "-xhd1", "/root/bot"),
        "largest_volume": run("du", "-xhd1", "/mnt/HC_Volume_106775198"),
    }

    preservation = [
        preserved("/var/lib/edgesniffer-teacher-impulse-parity-v1", "KEEP_UNIQUE_RAW_EVIDENCE", "Canonical source/capsule/decision corpus and active feed state."),
        preserved("/root/bot/EdgeSniffer/runtime/research-cache/ranker-v2-exact-curve-rpc-v1.sqlite", "KEEP_UNIQUE_RAW_EVIDENCE", "Authoritative finalized transaction cache supporting exact labels."),
        preserved("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/exact-curve-economics-v1/validated-v1", "KEEP_IMMUTABLE_AUDIT", "Validated EXACT_CURVE_ECONOMICS_V1 evaluator and evidence."),
        preserved("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/exact-curve-independent-redteam-v1/run-20260906T0330Z", "KEEP_IMMUTABLE_AUDIT", "Independent exact-curve red-team report, machine evidence, sample, and checksums."),
        preserved("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-exact-curve-relabel-v1/run-20260906T0118Z", "KEEP_IMMUTABLE_AUDIT", "Frozen MC-proxy versus exact sign-reversal evidence."),
        preserved("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-n25-tiny-live-v1", "KEEP_IMMUTABLE_AUDIT", "Ranker N25 V1 transaction/inventory/exit evidence."),
        preserved("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-n25-tiny-live-v2", "KEEP_IMMUTABLE_AUDIT", "Ranker N25 V2 transaction/inventory/exit evidence."),
        preserved("/root/bot/EdgeSniffer/runtime/ranker-v2-n25-tiny-live-v3", "KEEP_ACTIVE_RUNTIME", "Ranker N25 V3 state and live execution evidence; safety process remains active."),
        preserved("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-prospective-shadow-v2", "KEEP_IMMUTABLE_AUDIT", "Frozen prospective Ranker scorer evidence."),
        preserved("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/executable-alpha-ranker-v2-canonical-freeze", "KEEP_CRITICAL", "Canonical scorer/spec/hash freeze."),
        preserved("/mnt/HC_Volume_106775198/edgesniffer-bin", "KEEP_CRITICAL", "Known deployed and recovery binaries."),
        preserved("/root/bot/EdgeSniffer", "KEEP_CRITICAL", "Main EdgeSniffer source and active runtime."),
        preserved("/root/bot/8ycb-sniper", "KEEP_CRITICAL", "Separate 8YCB project; explicitly out of cleanup scope."),
        preserved("/root/bot/FinalBot-cya", "KEEP_CRITICAL", "Shared production source/build tree, including active feed binaries and 8YCB dependencies."),
        preserved("/var/lib/8ycb-sniper", "KEEP_ACTIVE_RUNTIME", "Separate active 8YCB runtime."),
        preserved("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/wallet-8ycb-causal-followability-v3", "KEEP_UNIQUE_RAW_EVIDENCE", "8YCB research data; out of scope."),
        preserved("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/wallet-8ycb-opening-sniper-v2", "KEEP_UNIQUE_RAW_EVIDENCE", "8YCB transaction archive; out of scope."),
    ]

    deletions = [
        candidate(
            "/var/lib/edgesniffer-storage-offload-v3/research/early-burst-telemetry-v1.jsonl",
            "REGENERABLE",
            "Inactive derived feature tape; deterministic source feed and generator are preserved.",
            True,
            "/var/lib/edgesniffer-teacher-impulse-parity-v1/normalized-observations.jsonl + /root/bot/FinalBot-cya/scripts/teacherImpulseEarlyBurstTelemetryV1.py",
            "edgesniffer-teacher-impulse-early-burst-telemetry-v1.service",
        ),
        candidate(
            "/root/bot/EdgeSniffer/rust-sidecar/target/debug",
            "REGENERABLE",
            "Unused Rust debug build artifacts; release binaries and source are preserved.",
            True,
            "/root/bot/EdgeSniffer/rust-sidecar/Cargo.lock + source",
        ),
        candidate(
            "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-direct-handoff-cache-v2",
            "SUPERSEDED",
            "Superseded no-submit calibration output; both referencing units are inactive and N25 evidence is preserved separately.",
            True,
            "Frozen source/scorer plus N25 V1/V2/V3 execution evidence",
            [
                "edgesniffer-ranker-v2-direct-handoff-cache-v2-nosubmit.service",
                "edgesniffer-ranker-v2-direct-handoff-cache-v2-adapter.service",
            ],
        ),
        candidate(
            "/root/bot/EdgeSniffer/scripts/__pycache__",
            "SAFE_DELETE",
            "Python bytecode cache.",
            True,
            "/root/bot/EdgeSniffer/scripts",
        ),
        candidate(
            "/root/bot/EdgeSniffer/.codex_tmp_upload",
            "SAFE_DELETE",
            "Stale Codex transfer scratch directory.",
            True,
            None,
        ),
        candidate(
            "/var/log/syslog.1",
            "SAFE_DELETE",
            "Rotated system log; current journal and active logs remain.",
            False,
            None,
        ),
    ]

    preservation_doc = {
        "schema": "EDGESNIFFER_STORAGE_PRESERVATION_MANIFEST_V1",
        "generated_utc": before["generated_utc"],
        "items": preservation,
    }
    deletion_doc = {
        "schema": "EDGESNIFFER_STORAGE_DELETION_MANIFEST_V1",
        "generated_utc": before["generated_utc"],
        "items": deletions,
        "total_intended_delete_bytes": sum(row["size_bytes"] for row in deletions if row["intended_action"] == "DELETE"),
    }
    for name, payload in (
        ("storage-before.json", before),
        ("preservation-manifest.json", preservation_doc),
        ("deletion-manifest.json", deletion_doc),
    ):
        (args.output / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "delete_bytes": deletion_doc["total_intended_delete_bytes"], "items": deletions}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
