#!/usr/bin/env python3
"""Fail-closed, rate-limited archival for cold immutable evidence trees.

Copies one source tree to an NFS staging area, hashes every regular file on
both sides, atomically publishes only a complete match, and never removes the
local source.  A separate release gate must consume the receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "THROTTLED_COLD_ARCHIVE_V1"
BLOCK_BYTES = 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(canonical(value))
        handle.flush()
        os.fsync(handle.fileno())
        temp = Path(handle.name)
    os.replace(temp, path)


class RateLimiter:
    def __init__(self, mib_per_second: float, max_io_pressure_avg10: float) -> None:
        self.bytes_per_second = max(mib_per_second, 0.25) * 1024 * 1024
        self.max_io_pressure_avg10 = max_io_pressure_avg10
        self.started = time.monotonic()
        self.transferred = 0

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
        target_elapsed = self.transferred / self.bytes_per_second
        pause = target_elapsed - (time.monotonic() - self.started)
        if pause > 0:
            time.sleep(pause)
        self.wait_for_io_headroom()


def list_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise SystemExit(f"FAIL_CLOSED_SYMLINK:{item}")
        if item.is_file():
            result.append(item)
        elif not item.is_dir():
            raise SystemExit(f"FAIL_CLOSED_UNSUPPORTED_TYPE:{item}")
    return result


def copy_and_hash(source: Path, destination: Path, limiter: RateLimiter) -> tuple[str, int]:
    digest = hashlib.sha256()
    written = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("xb") as writer:
        while chunk := reader.read(BLOCK_BYTES):
            writer.write(chunk)
            digest.update(chunk)
            written += len(chunk)
            limiter.account(len(chunk))
        writer.flush()
        os.fsync(writer.fileno())
    shutil.copystat(source, destination, follow_symlinks=False)
    return digest.hexdigest(), written


def hash_file(path: Path, limiter: RateLimiter) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as reader:
        while chunk := reader.read(BLOCK_BYTES):
            digest.update(chunk)
            size += len(chunk)
            limiter.account(len(chunk))
    return digest.hexdigest(), size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--object-id", required=True)
    parser.add_argument("--max-mib-per-second", type=float, default=4.0)
    parser.add_argument("--max-io-pressure-avg10", type=float, default=15.0)
    args = parser.parse_args()

    source = args.source.resolve()
    if not source.is_dir():
        raise SystemExit("FAIL_CLOSED_SOURCE_NOT_DIRECTORY")
    source_files = list_files(source)
    before = {str(p.relative_to(source)): (p.stat().st_size, p.stat().st_mtime_ns) for p in source_files}

    safe_id = args.object_id.replace("/", "-").replace(":", "-")
    staging = args.archive_root / "staging" / f"{safe_id}.partial"
    published = args.archive_root / "objects" / safe_id
    if staging.exists() or published.exists():
        raise SystemExit("FAIL_CLOSED_DESTINATION_ALREADY_EXISTS")
    staging.mkdir(parents=True)

    source_limiter = RateLimiter(args.max_mib_per_second, args.max_io_pressure_avg10)
    manifest: list[dict[str, object]] = []
    for source_file in source_files:
        rel = source_file.relative_to(source)
        dest_file = staging / "payload" / rel
        source_sha, copied = copy_and_hash(source_file, dest_file, source_limiter)
        manifest.append({"path": str(rel), "bytes": copied, "sha256": source_sha})

    # Re-read NFS payload at the same bounded rate. Any mismatch leaves only an
    # unpublished staging object; the local source remains untouched.
    verify_limiter = RateLimiter(args.max_mib_per_second, args.max_io_pressure_avg10)
    for item in manifest:
        digest, size = hash_file(staging / "payload" / str(item["path"]), verify_limiter)
        if digest != item["sha256"] or size != item["bytes"]:
            raise SystemExit(f"FAIL_CLOSED_DESTINATION_MISMATCH:{item['path']}")

    after_files = list_files(source)
    after = {str(p.relative_to(source)): (p.stat().st_size, p.stat().st_mtime_ns) for p in after_files}
    if before != after:
        raise SystemExit("FAIL_CLOSED_SOURCE_CHANGED_DURING_ARCHIVE")

    tree_digest = hashlib.sha256(canonical(manifest)).hexdigest()
    receipt = {
        "schema": SCHEMA,
        "recordedAtUtc": utc_now(),
        "objectId": args.object_id,
        "source": str(source),
        "archive": str(published),
        "fileCount": len(manifest),
        "bytes": sum(int(row["bytes"]) for row in manifest),
        "treeSha256": tree_digest,
        "verification": "source-copy-sha256-and-bounded-nfs-readback",
        "localReleased": False,
        "files": manifest,
    }
    atomic_json(staging / "manifest.json", {"schema": SCHEMA, "files": manifest})
    atomic_json(staging / "receipt.json", receipt)
    published.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, published)
    print(json.dumps({"status": "ARCHIVED_VERIFIED", "receipt": str(published / "receipt.json"), **receipt}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
