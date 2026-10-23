#!/usr/bin/env python3
"""Separate, fail-closed local release gate for a verified cold archive.

The default mode only writes a preflight receipt.  ``--release`` additionally
replaces the local immutable source with a symlink to the verified NFS object.
It never acts without an archive receipt and a zero-reference preflight.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "THROTTLED_COLD_RELEASE_V1"
BLOCK_BYTES = 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(canonical(value)); handle.flush(); os.fsync(handle.fileno())
        temp = Path(handle.name)
    os.replace(temp, path)


class RateLimiter:
    def __init__(self, mib_per_second: float, max_io_pressure_avg10: float) -> None:
        self.rate = max(mib_per_second, 0.25) * 1024 * 1024
        self.max_io_pressure_avg10 = max_io_pressure_avg10
        self.started = time.monotonic(); self.transferred = 0

    def wait_for_io_headroom(self) -> None:
        if self.max_io_pressure_avg10 <= 0:
            return
        while True:
            try:
                line = Path("/proc/pressure/io").read_text().splitlines()[0]
                avg10 = float(next(item.split("=", 1)[1] for item in line.split() if item.startswith("avg10=")))
            except (OSError, StopIteration, ValueError):
                return
            if avg10 <= self.max_io_pressure_avg10:
                return
            time.sleep(5)

    def account(self, size: int) -> None:
        self.transferred += size
        sleep = self.transferred / self.rate - (time.monotonic() - self.started)
        if sleep > 0: time.sleep(sleep)
        self.wait_for_io_headroom()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(BLOCK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_rate_limited(path: Path, limiter: RateLimiter) -> tuple[str, int]:
    digest = hashlib.sha256(); size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(BLOCK_BYTES), b""):
            digest.update(block); size += len(block); limiter.account(len(block))
    return digest.hexdigest(), size


def under(path: Path, ancestor: Path) -> bool:
    try:
        path.resolve().relative_to(ancestor.resolve())
        return True
    except (ValueError, OSError):
        return False


def process_handles(source: Path) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    for pid in Path("/proc").iterdir():
        if not pid.name.isdigit(): continue
        try:
            for fd in (pid / "fd").iterdir():
                try:
                    target = Path(os.readlink(fd))
                    if under(target, source):
                        command = (pid / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
                        matches.append({"pid": int(pid.name), "fd": fd.name, "target": str(target), "cmdline": command})
                except OSError: continue
        except OSError: continue
    return matches


def active_unit_refs(source: Path) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    units = subprocess.run(["systemctl", "list-units", "--type=service", "--state=running", "--no-legend"], text=True, capture_output=True, check=False).stdout.splitlines()
    needle = str(source)
    for line in units:
        unit = line.split()[0] if line.split() else ""
        if not unit: continue
        text = subprocess.run(["systemctl", "show", "-p", "ExecStart", "--value", unit], text=True, capture_output=True, check=False).stdout.strip()
        if needle in text: result.append({"unit": unit, "execStart": text})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive-receipt", type=Path, required=True)
    parser.add_argument("--preflight-output", type=Path, required=True)
    parser.add_argument("--max-mib-per-second", type=float, default=2.0)
    parser.add_argument("--max-io-pressure-avg10", type=float, default=15.0)
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    source = args.source.absolute()
    if source.is_symlink() or not source.is_dir(): raise SystemExit("FAIL_CLOSED_SOURCE_NOT_LOCAL_DIRECTORY")
    receipt_path = args.archive_receipt.absolute()
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("schema") != "THROTTLED_COLD_ARCHIVE_V1" or receipt.get("localReleased"):
        raise SystemExit("FAIL_CLOSED_INVALID_ARCHIVE_RECEIPT")
    if Path(receipt.get("source", "")).resolve() != source.resolve(): raise SystemExit("FAIL_CLOSED_RECEIPT_SOURCE_MISMATCH")
    archive = Path(receipt.get("archive", ""))
    manifest = receipt.get("files")
    if not archive.is_dir() or not isinstance(manifest, list): raise SystemExit("FAIL_CLOSED_ARCHIVE_NOT_PUBLISHED")

    handles = process_handles(source); units = active_unit_refs(source)
    mismatches: list[str] = []; bytes_verified = 0; limiter = RateLimiter(args.max_mib_per_second, args.max_io_pressure_avg10)
    for item in manifest:
        rel = Path(str(item["path"])); local = source / rel; remote = archive / "payload" / rel
        if not local.is_file() or not remote.is_file(): mismatches.append(str(rel)); continue
        local_hash, local_size = hash_rate_limited(local, limiter)
        if local_hash != item["sha256"] or local_size != item["bytes"]: mismatches.append(str(rel)); continue
        if remote.stat().st_size != item["bytes"]: mismatches.append(str(rel)); continue
        bytes_verified += local_size
    extra = sorted(str(p.relative_to(source)) for p in source.rglob("*") if p.is_file())
    expected = sorted(str(x["path"]) for x in manifest)
    if extra != expected: mismatches.append("inventory-mismatch")
    pass_gate = not handles and not units and not mismatches
    preflight = {
        "schema": SCHEMA, "recordedAtUtc": utc_now(), "source": str(source),
        "archiveReceipt": str(receipt_path), "archiveReceiptSha256": sha256(receipt_path),
        "archive": str(archive), "bytesVerified": bytes_verified,
        "openFileHandles": handles, "activeUnitReferences": units, "mismatches": mismatches,
        "outcome": "PASS" if pass_gate else "FAIL_CLOSED", "releaseRequested": args.release,
    }
    atomic_json(args.preflight_output, preflight)
    if not pass_gate or not args.release:
        print(json.dumps({"status": preflight["outcome"], "preflight": str(args.preflight_output)}, sort_keys=True))
        return 0 if pass_gate else 2

    # Swap path atomically before deleting the old tree. A complete verified NFS
    # copy remains reachable through the original path throughout cleanup.
    backup = source.with_name(source.name + ".released-pending-delete")
    if backup.exists() or backup.is_symlink(): raise SystemExit("FAIL_CLOSED_BACKUP_PATH_EXISTS")
    os.replace(source, backup)
    os.symlink(str(archive / "payload"), source)
    try:
        shutil.rmtree(backup)
    except Exception:
        # Leave the symlink serving verified evidence and preserve the old tree
        # for explicit remediation if cleanup is interrupted.
        raise
    release = dict(preflight)
    release.update({"releasedAtUtc": utc_now(), "localReleased": True, "releasedBytes": bytes_verified})
    release_path = args.preflight_output.with_name(args.preflight_output.stem + ".release.json")
    atomic_json(release_path, release)
    print(json.dumps({"status": "LOCAL_RELEASED", "releaseReceipt": str(release_path), "releasedBytes": bytes_verified}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
