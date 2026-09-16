#!/usr/bin/env python3
"""Read-only inventory of EdgeSniffer storage writers and observed growth."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import time
from typing import Any


ROOTS = (
    pathlib.Path("/var/lib/edgesniffer-storage-offload-v3"),
    pathlib.Path("/var/lib/edgesniffer-teacher-impulse-parity-v1"),
    pathlib.Path("/root/bot/EdgeSniffer/runtime"),
    pathlib.Path("/mnt/HC_Volume_106775198/edgesniffer-derived"),
)


def run(*args: str) -> str:
    return subprocess.run(args, text=True, capture_output=True, check=False).stdout.strip()


def active_units() -> list[str]:
    output = run(
        "systemctl",
        "list-units",
        "--type=service",
        "--state=running",
        "--no-legend",
        "--plain",
    )
    return sorted(
        line.split()[0]
        for line in output.splitlines()
        if line and any(token in line.lower() for token in ("edgesniffer", "ranker", "pump-exact"))
    )


def systemd_show(unit: str) -> dict[str, str]:
    fields = ("MainPID", "NRestarts", "ActiveState", "SubState", "ExecStart", "FragmentPath")
    output = run("systemctl", "show", unit, *sum((["-p", field] for field in fields), []))
    result: dict[str, str] = {"unit": unit}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def writable_fds(pid: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    fd_root = pathlib.Path(f"/proc/{pid}/fd")
    for fd in fd_root.iterdir() if fd_root.exists() else ():
        try:
            target = pathlib.Path(os.readlink(fd))
            flags_line = next(
                line for line in pathlib.Path(f"/proc/{pid}/fdinfo/{fd.name}").read_text().splitlines()
                if line.startswith("flags:")
            )
            flags = int(flags_line.split()[1], 8)
            if flags & os.O_ACCMODE not in (os.O_WRONLY, os.O_RDWR):
                continue
            if not target.is_absolute() or not target.exists() or not target.is_file():
                continue
            stat = target.stat()
            rows.append({
                "fd": int(fd.name),
                "path": str(target),
                "sizeBytes": stat.st_size,
                "modifiedUtc": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
            })
        except (OSError, StopIteration, ValueError):
            continue
    return sorted(rows, key=lambda row: row["sizeBytes"], reverse=True)


def largest_files(limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root in ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    size = path.stat().st_size
                    if size >= 10 * 1024 * 1024:
                        rows.append({"path": str(path), "sizeBytes": size})
            except OSError:
                continue
    return sorted(rows, key=lambda row: row["sizeBytes"], reverse=True)[:limit]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-seconds", type=int, default=60)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    units = [systemd_show(unit) for unit in active_units()]
    writers: list[dict[str, Any]] = []
    for unit in units:
        pid = int(unit.get("MainPID", "0") or 0)
        for row in writable_fds(pid):
            row.update(unit=unit["unit"], pid=pid, execStart=unit.get("ExecStart"))
            writers.append(row)
    initial = {(row["unit"], row["path"]): row["sizeBytes"] for row in writers}
    time.sleep(max(1, args.sample_seconds))
    for row in writers:
        path = pathlib.Path(row["path"])
        final_size = path.stat().st_size if path.exists() else row["sizeBytes"]
        delta = max(0, final_size - initial[(row["unit"], row["path"])])
        row["finalSizeBytes"] = final_size
        row["sampleGrowthBytes"] = delta
        row["estimatedBytesPerDay"] = delta * 86_400 / max(1, args.sample_seconds)
    report = {
        "schema": "EDGESNIFFER_LEGACY_STORAGE_WRITER_AUDIT_V1",
        "generatedUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sampleSeconds": args.sample_seconds,
        "dfBytes": run("df", "-B1", "/", "/mnt/HC_Volume_106775198"),
        "activeUnits": units,
        "writers": sorted(writers, key=lambda row: row["estimatedBytesPerDay"], reverse=True),
        "largestFiles": largest_files(100),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
