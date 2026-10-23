#!/usr/bin/env python3
"""One-shot coordinator: archive, independently validate, then release run-1818.

This intentionally controls one known inactive historical tree only.  It never
touches the active kEfi root, its source partitions, or any service.
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


ARCHIVE_UNIT = "edgesniffer-evidence-archive-run1818-v1.service"
SOURCE = Path("/root/v3-data-plane-v2-lab/run-1818")
ARCHIVE_ROOT = Path("/mnt/edgesniffer-expansion/evidence-lake-v1")
IDENTITY = "b622a4144c48f9565707952042b74cccc6a90eac42042b6e357496b149fe749a"
WORK = Path("/root/kefi-work/evidence-lake-v1")
CATALOG = WORK / "catalog.sqlite3"
AUDIT = WORK / "dependency-audits" / "legacy-v3-data-plane-v2-run-1818-release.json"
STATUS = ARCHIVE_ROOT / "release-coordination" / "legacy-v3-data-plane-v2-run-1818.json"
LIVE_UNITS = [
    "edgesniffer-kefi-full-meta-v1.service",
    "edgesniffer-kefi-finalizer-v1.service",
    "edgesniffer-kefi-market-control-v1.service",
    "edgesniffer-kefi-market-catchup-v1.service",
    "edgesniffer-kefi-prospective-shadow-v1.service",
    "edgesniffer-kefi-full-meta-watchdog-v1.service",
]


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_status(status, **extra):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({"schema": "archive-release-coordinator-v1", "updated_utc": utcnow(),
                                  "status": status, **extra}, sort_keys=True) + "\n")


def run(args):
    return subprocess.run(args, text=True, capture_output=True)


def main():
    deadline = time.monotonic() + 12 * 3600
    receipt = ARCHIVE_ROOT / "receipts" / f"{IDENTITY}.json"
    write_status("WAITING_FOR_ARCHIVE_RECEIPT", source=str(SOURCE), archive_unit=ARCHIVE_UNIT)
    while not receipt.is_file():
        if time.monotonic() > deadline:
            write_status("FAIL_CLOSED", reason="archive receipt deadline exceeded")
            return 2
        state = run(["systemctl", "is-failed", "--quiet", ARCHIVE_UNIT])
        if state.returncode == 0:
            write_status("FAIL_CLOSED", reason="archive unit failed before receipt")
            return 2
        time.sleep(30)
    write_status("VALIDATION_CYCLE", receipt=str(receipt), validation_wait_seconds=360)
    time.sleep(360)
    unhealthy = [unit for unit in LIVE_UNITS if run(["systemctl", "is-active", "--quiet", unit]).returncode != 0]
    if unhealthy:
        write_status("FAIL_CLOSED", reason="live kEfi service unhealthy", unhealthy_units=unhealthy)
        return 2
    audit = run(["/usr/bin/python3", "/root/kefi-work/bin/evidence_lake_legacy_tree_audit_v1.py",
                 "--path", str(SOURCE), "--output", str(AUDIT), "--catalog", str(CATALOG), "--settle-seconds", "5"])
    if audit.returncode != 0:
        write_status("FAIL_CLOSED", reason="dependency audit failed", stderr=audit.stderr[-1000:])
        return 2
    audit_result = json.loads(audit.stdout)
    if audit_result.get("outcome") != "DEPENDENCY_NONE_PROVEN":
        write_status("FAIL_CLOSED", reason="dependency audit did not prove absence", audit=audit_result)
        return 2
    archive = json.loads(receipt.read_text())["archive"]
    verify = run(["/usr/bin/python3", "/root/kefi-work/bin/evidence_lake_tree_transparent_release_v1.py",
                  "--local-tree", str(SOURCE), "--archive-tree", archive, "--archive-receipt", str(receipt),
                  "--dependency-audit", str(AUDIT), "--release-receipt-dir", str(ARCHIVE_ROOT / "release-receipts"),
                  "--mode", "VERIFY"])
    if verify.returncode != 0:
        write_status("FAIL_CLOSED", reason="release verification failed", stderr=verify.stderr[-1000:])
        return 2
    release = run(["/usr/bin/python3", "/root/kefi-work/bin/evidence_lake_tree_transparent_release_v1.py",
                   "--local-tree", str(SOURCE), "--archive-tree", archive, "--archive-receipt", str(receipt),
                   "--dependency-audit", str(AUDIT), "--release-receipt-dir", str(ARCHIVE_ROOT / "release-receipts"),
                   "--mode", "RELEASE"])
    if release.returncode != 0:
        write_status("FAIL_CLOSED", reason="release failed", stderr=release.stderr[-1000:])
        return 2
    write_status("RELEASED", audit=audit_result, release=json.loads(release.stdout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
