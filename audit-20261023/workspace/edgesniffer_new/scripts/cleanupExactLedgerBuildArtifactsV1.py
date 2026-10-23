#!/usr/bin/env python3
"""Delete only the archived, rebuildable exact-ledger Cargo target directory."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys


TARGET = pathlib.Path("/root/bot/EdgeSniffer/rust-exact-ledger/target")
DEPLOYED = pathlib.Path("/usr/local/bin/pump-exact-evidence-ledger-v1")
ARCHIVE = pathlib.Path(
    "/root/bot/EdgeSniffer/artifacts/pump-exact-evidence-ledger-v1/"
    "8de9ec9713cb25ab2fd3e14d6123b47eb2bbb16fba36c4dc377537c338c0ca21/"
    "pump-exact-evidence-ledger-v1"
)
EXPECTED = "8de9ec9713cb25ab2fd3e14d6123b47eb2bbb16fba36c4dc377537c338c0ca21"


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def size(path: pathlib.Path) -> int:
    output = subprocess.check_output(["du", "-sb", str(path)], text=True)
    return int(output.split()[0])


def open_fds(path: pathlib.Path) -> list[str]:
    target = str(path.resolve())
    found: list[str] = []
    for fd in pathlib.Path("/proc").glob("[0-9]*/fd/*"):
        try:
            resolved = os.path.realpath(fd)
        except OSError:
            continue
        if resolved == target or resolved.startswith(target.rstrip("/") + "/"):
            found.append(str(fd))
    return found


def main() -> int:
    if not TARGET.exists() or TARGET.is_symlink():
        raise SystemExit("target absent or unsafe")
    deployed_hash = sha256(DEPLOYED)
    archive_hash = sha256(ARCHIVE)
    refs = open_fds(TARGET)
    unit = subprocess.check_output(
        ["systemctl", "show", "pump-exact-evidence-ledger-v1.service", "-p", "ExecStart"],
        text=True,
    ).strip()
    manifest = {
        "schema": "PUMP_EXACT_LEDGER_BUILD_CLEANUP_V1",
        "generatedUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "items": [
            {
                "absolutePath": str(TARGET),
                "sizeBytes": size(TARGET),
                "classification": "REGENERABLE",
                "reason": "Cargo build artifacts; source, lockfile, deployed binary, and immutable binary archive preserved.",
                "reproducible": True,
                "deployedSha256": deployed_hash,
                "archiveSha256": archive_hash,
                "activeOpenFds": refs,
                "activeServiceExecStart": unit,
                "intendedAction": "DELETE",
            }
        ],
    }
    output = pathlib.Path(
        "/var/lib/edgesniffer-storage-offload-v3/manifests/"
        "main-lane-storage-ledger-v1/ledger-build-cleanup.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if deployed_hash != EXPECTED or archive_hash != EXPECTED or refs or str(TARGET) in unit:
        print(json.dumps({"status": "BLOCKED", "manifest": manifest}, indent=2))
        return 2
    deleted_bytes = manifest["items"][0]["sizeBytes"]
    shutil.rmtree(TARGET)
    print(json.dumps({"status": "DELETED", "deletedBytes": deleted_bytes}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
