#!/usr/bin/env python3
"""Preserve and remove one incomplete final JSONL record from a source tape."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
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
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    path = args.path.resolve()
    if not path.exists() or path.is_symlink():
        raise SystemExit("FAIL_CLOSED: source path missing or symlink")
    if path.stat().st_size <= 0:
        raise SystemExit("FAIL_CLOSED: source is empty")
    data = path.read_bytes()
    original_bytes = len(data)
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    if args.backup.exists():
        raise SystemExit("FAIL_CLOSED: backup path already exists")
    shutil.copyfile(path, args.backup)
    original_sha = sha256(args.backup)
    temporary = path.with_name(path.name + ".repair-tmp")
    repair_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    invalid_path = path.with_name(path.name + f".invalid-lines-{repair_stamp}.jsonl")
    invalid_rows: list[dict[str, object]] = []
    valid_rows = 0
    with path.open("rb") as source, temporary.open("wb") as target:
        for line_number, raw_line in enumerate(source, 1):
            if not raw_line.strip():
                continue
            try:
                json.loads(raw_line)
            except json.JSONDecodeError as error:
                invalid_rows.append({
                    "line_number": line_number,
                    "bytes": len(raw_line),
                    "sha256": hashlib.sha256(raw_line).hexdigest(),
                    "raw_base64": base64.b64encode(raw_line).decode("ascii"),
                    "error": str(error),
                })
                continue
            target.write(raw_line)
            valid_rows += 1
        target.flush()
        os.fsync(target.fileno())
    if not invalid_rows:
        temporary.unlink(missing_ok=True)
        raise SystemExit("FAIL_CLOSED: no malformed records found; original preserved")
    invalid_path.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in invalid_rows),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    repaired_sha = sha256(path)
    report = {
        "schema": "WALLET_FINALIZED_ROUTE_SOURCE_TAIL_REPAIR_V1",
        "read_only_evidence_preserved": True,
        "source": str(path),
        "backup": str(args.backup.resolve()),
        "original_bytes": original_bytes,
        "repaired_bytes": path.stat().st_size,
        "removed_invalid_record_count": len(invalid_rows),
        "removed_invalid_record_bytes": sum(int(row["bytes"]) for row in invalid_rows),
        "invalid_records_artifact": str(invalid_path.resolve()),
        "valid_rows_after_repair": valid_rows,
        "repair_type": "FILTER_INVALID_JSONL_RECORDS_ATOMIC_REPLACE",
        "original_sha256": original_sha,
        "repaired_sha256": repaired_sha,
        "repaired_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
