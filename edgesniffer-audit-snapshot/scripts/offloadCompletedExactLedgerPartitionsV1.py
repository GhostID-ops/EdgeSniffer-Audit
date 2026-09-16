#!/usr/bin/env python3
"""Hash-verified, path-preserving offload for immutable exact-ledger partitions."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def partition_hour(path: pathlib.Path, root: pathlib.Path) -> dt.datetime:
    parts = path.relative_to(root / "partitions").parts
    return dt.datetime(*map(int, parts[:4]), tzinfo=dt.timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=pathlib.Path, required=True)
    parser.add_argument("--destination-root", type=pathlib.Path, required=True)
    parser.add_argument("--before-utc", required=True)
    parser.add_argument("--minimum-destination-free-gb", type=float, default=20.0)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    args = parser.parse_args()

    boundary = dt.datetime.fromisoformat(args.before_utc.replace("Z", "+00:00"))
    candidates = sorted((args.source_root / "partitions").glob("*/*/*/*/evidence.frames.zst"))
    candidates = [path for path in candidates if not path.is_symlink() and partition_hour(path, args.source_root) < boundary]
    records = []
    for source in candidates:
        destination_free = shutil.disk_usage(args.destination_root.parent if args.destination_root.parent.exists() else "/var/lib").free
        if destination_free - source.stat().st_size < args.minimum_destination_free_gb * 1_000_000_000:
            break
        open_check = subprocess.run(
            ["lsof", "-t", "--", str(source)], capture_output=True, text=True, check=False
        )
        if open_check.stdout.strip():
            records.append({"source": str(source), "status": "SKIPPED_OPEN", "pids": open_check.stdout.split()})
            continue
        relative = source.relative_to(args.source_root)
        destination = args.destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        source_stat = source.stat()
        shutil.copy2(source, temporary)
        source_hash = sha256_file(source)
        destination_hash = sha256_file(temporary)
        if source_hash != destination_hash or temporary.stat().st_size != source_stat.st_size:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"offload verification failed: {source}")
        os.replace(temporary, destination)
        source.unlink()
        source.symlink_to(destination)
        if not source.exists() or sha256_file(source) != source_hash:
            raise RuntimeError(f"path-preserving symlink verification failed: {source}")
        records.append({
            "source": str(source), "destination": str(destination), "status": "OFFLOADED_VERIFIED",
            "size": source_stat.st_size, "sha256": source_hash,
            "sourceMtimeNs": source_stat.st_mtime_ns,
        })

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "PUMP_EXACT_COMPLETED_PARTITION_OFFLOAD_V1",
        "atUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "boundaryUtc": boundary.isoformat().replace("+00:00", "Z"),
        "records": records,
        "offloadedN": sum(row["status"] == "OFFLOADED_VERIFIED" for row in records),
        "offloadedBytes": sum(row.get("size", 0) for row in records if row["status"] == "OFFLOADED_VERIFIED"),
    }
    temporary_manifest = args.manifest.with_suffix(args.manifest.suffix + ".tmp")
    temporary_manifest.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary_manifest, args.manifest)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
