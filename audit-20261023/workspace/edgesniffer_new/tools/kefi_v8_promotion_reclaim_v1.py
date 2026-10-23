#!/usr/bin/env python3
"""Release the audited obsolete V8 promotion windows with a compact receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path


EXPECTED = Path("/mnt/edgesniffer-kefi-hot/kefi-capture-first-promotions-v8/shared-windows")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root: Path) -> tuple[int, int, list[dict]]:
    files = 0
    total = 0
    entries: list[dict] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        entry_files = 0
        entry_bytes = 0
        for current, _, names in os.walk(entry):
            for name in names:
                path = Path(current) / name
                try:
                    entry_files += 1
                    entry_bytes += path.stat().st_size
                except FileNotFoundError:
                    raise RuntimeError("FAIL_CLOSED: window changed during reclaim inventory")
        files += entry_files
        total += entry_bytes
        entries.append({"entry": entry.name, "files": entry_files, "bytes": entry_bytes})
    return files, total, entries


def write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dependency-audit", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.root.resolve() != EXPECTED or not args.root.is_dir() or args.receipt.exists():
        raise SystemExit("FAIL_CLOSED: reclaim target or receipt state invalid")
    audit = json.loads(args.dependency_audit.read_text())
    if audit.get("classification") != "DEPENDENCY_NONE_PROVEN" or audit.get("root") != str(EXPECTED):
        raise SystemExit("FAIL_CLOSED: dependency absence was not proven")
    files, total, entries = inventory(args.root)
    receipt = {"schema": "KEFI_V8_PROMOTION_WINDOW_RECLAIM_V1", "root": str(args.root), "dependency_audit_sha256": sha(args.dependency_audit), "file_count": files, "released_bytes": total, "entries": entries, "release_reason": "retired source promotion windows; active source/control stopped and dependency audit proved no remaining frozen or live reference", "outcomes_or_pnl_accessed": False}
    write_once(args.receipt, receipt)
    shutil.rmtree(args.root)
    args.root.mkdir(mode=0o750)
    print(json.dumps({"receipt": str(args.receipt), "sha256": sha(args.receipt), "released_bytes": total}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
