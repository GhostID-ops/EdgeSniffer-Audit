#!/usr/bin/env python3
"""Execute the exact, pre-audited EdgeSniffer deletion manifest."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
from typing import Any


ALLOWLIST = {
    "/var/lib/edgesniffer-storage-offload-v3/research/early-burst-telemetry-v1.jsonl",
    "/root/bot/EdgeSniffer/rust-sidecar/target/debug",
    "/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/ranker-v2-direct-handoff-cache-v2",
    "/root/bot/EdgeSniffer/scripts/__pycache__",
    "/root/bot/EdgeSniffer/.codex_tmp_upload",
    "/var/log/syslog.1",
}


def run(*args: str) -> str:
    return subprocess.run(args, check=False, text=True, capture_output=True).stdout.strip()


def open_fds(path: pathlib.Path) -> list[dict[str, Any]]:
    target = str(path.resolve())
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
                    rows.append({"pid": int(proc.name), "fd": fd.name, "cmd": cmd[:500]})
        except (OSError, PermissionError):
            continue
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if manifest.get("schema") != "EDGESNIFFER_STORAGE_DELETION_MANIFEST_V1":
        raise SystemExit("unexpected manifest schema")
    manifest_paths = {row["absolute_path"] for row in manifest["items"] if row["intended_action"] == "DELETE"}
    if manifest_paths != ALLOWLIST:
        raise SystemExit(f"manifest does not exactly match hard allowlist: {manifest_paths ^ ALLOWLIST}")

    report: dict[str, Any] = {
        "schema": "EDGESNIFFER_STORAGE_CLEANUP_EXECUTION_V1",
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "df_before": run("df", "-B1", "/", "/mnt/HC_Volume_106775198"),
        "items": [],
    }
    for row in manifest["items"]:
        if row["intended_action"] != "DELETE":
            continue
        raw = row["absolute_path"]
        path = pathlib.Path(raw)
        result = {"absolute_path": raw, "manifest_size_bytes": row["size_bytes"]}
        if raw not in ALLOWLIST or path.is_symlink():
            result.update(status="BLOCKED", reason="not allowlisted or target is symlink")
            report["items"].append(result)
            continue
        active_units = []
        for unit in row["active_service_reference_check"].get("units", []):
            state = run("systemctl", "is-active", unit["unit"])
            if state == "active":
                active_units.append(unit["unit"])
        fds = open_fds(path) if path.exists() else []
        if active_units or fds:
            result.update(status="BLOCKED", reason="active reference detected", active_units=active_units, open_fds=fds)
            report["items"].append(result)
            continue
        if not path.exists():
            result.update(status="ALREADY_ABSENT", deleted_bytes=0)
        elif path.is_dir():
            shutil.rmtree(path)
            result.update(status="DELETED", deleted_bytes=row["size_bytes"])
        else:
            path.unlink()
            result.update(status="DELETED", deleted_bytes=row["size_bytes"])
        report["items"].append(result)

    report["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    report["df_after"] = run("df", "-B1", "/", "/mnt/HC_Volume_106775198")
    report["deleted_bytes"] = sum(row.get("deleted_bytes", 0) for row in report["items"])
    report["deleted_open_files"] = run("lsof", "+L1")
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2))
    return 1 if any(row["status"] == "BLOCKED" for row in report["items"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
