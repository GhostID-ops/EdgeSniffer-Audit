#!/usr/bin/env python3
"""Remove malformed JSONL records in-place after an external full backup."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--external-backup", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    path = args.path.resolve()
    if not path.exists() or path.is_symlink():
        raise SystemExit("FAIL_CLOSED: source path missing or symlink")
    original_size = path.stat().st_size
    original_sha = sha256(path)
    if args.external_backup.stat().st_size != original_size or sha256(args.external_backup) != original_sha:
        raise SystemExit("FAIL_CLOSED: external backup does not match source before repair")

    invalid: list[dict[str, object]] = []
    offset = 0
    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            start = offset
            offset += len(raw_line)
            if not raw_line.strip():
                continue
            try:
                json.loads(raw_line)
            except json.JSONDecodeError as error:
                invalid.append({
                    "line_number": line_number,
                    "start": start,
                    "end": offset,
                    "bytes": len(raw_line),
                    "sha256": hashlib.sha256(raw_line).hexdigest(),
                    "raw_base64": base64.b64encode(raw_line).decode("ascii"),
                    "error": str(error),
                })
    if not invalid:
        raise SystemExit("FAIL_CLOSED: no malformed records found")

    current_size = original_size
    with path.open("r+b") as handle:
        for row in reversed(invalid):
            start = int(row["start"])
            end = int(row["end"])
            read_pos = end
            write_pos = start
            while read_pos < current_size:
                handle.seek(read_pos)
                chunk = handle.read(min(1024 * 1024, current_size - read_pos))
                if not chunk:
                    break
                handle.seek(write_pos)
                handle.write(chunk)
                read_pos += len(chunk)
                write_pos += len(chunk)
            deleted = end - start
            current_size -= deleted
            handle.truncate(current_size)
            handle.flush()
    repaired_sha = sha256(path)
    report = {
        "schema": "WALLET_FINALIZED_ROUTE_SOURCE_INVALID_RECORD_REPAIR_V1",
        "external_backup": str(args.external_backup.resolve()),
        "external_backup_sha256": original_sha,
        "source": str(path),
        "original_bytes": original_size,
        "repaired_bytes": current_size,
        "original_sha256": original_sha,
        "repaired_sha256": repaired_sha,
        "invalid_record_count": len(invalid),
        "invalid_records": invalid,
        "repair_method": "IN_PLACE_BACKED_UP_RECORD_REMOVAL",
        "read_only_collectors_only": True,
        "trading_enabled": False,
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
