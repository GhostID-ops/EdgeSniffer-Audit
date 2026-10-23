#!/usr/bin/env python3
"""Dependency-only audit for the retired V8 promotion-window tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def active(unit: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0


def open_fds(root: Path) -> int:
    prefix = str(root) + "/"
    total = 0
    for proc in Path("/proc").glob("[0-9]*"):
        fd_dir = proc / "fd"
        try:
            for item in fd_dir.iterdir():
                try:
                    if os.readlink(item).startswith(prefix):
                        total += 1
                except OSError:
                    pass
        except OSError:
            pass
    return total


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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--termination", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, action="append", required=True)
    parser.add_argument("--exclude-path", type=Path, action="append", default=[])
    args = parser.parse_args()
    expected = Path("/mnt/edgesniffer-kefi-hot/kefi-capture-first-promotions-v8/shared-windows")
    if args.root.resolve() != expected or not args.root.is_dir() or args.output.exists():
        raise SystemExit("FAIL_CLOSED: unexpected root, missing tree, or pre-existing audit")
    if not args.termination.is_file():
        raise SystemExit("FAIL_CLOSED: PRE_H3 termination receipt required")
    needles = str(args.root).encode()
    references: list[str] = []
    excluded = {path.resolve() for path in args.exclude_path if path.exists()}
    for base in args.reference_root:
        if not base.exists():
            continue
        for current, _, names in os.walk(base):
            for name in names:
                path = Path(current) / name
                try:
                    if path.resolve() in excluded:
                        continue
                except OSError:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    # Unit-file symlinks can change while systemd reconciles.
                    # They cannot establish a dependency when unreadable.
                    continue
                if path.suffix in {".zst", ".gz", ".xz", ".tar"} or size > 32 * 1024 * 1024:
                    continue
                try:
                    if needles in path.read_bytes():
                        references.append(str(path))
                except OSError:
                    pass
    result = {
        "schema": "KEFI_V8_PROMOTION_WINDOW_DEPENDENCY_AUDIT_V1",
        "root": str(args.root),
        "termination_receipt_sha256": sha(args.termination),
        "source_active": active("edgesniffer-kefi-full-meta-capture-first-v8.service"),
        "control_active": active("edgesniffer-kefi-capture-first-control-v8.service"),
        "open_file_handles": open_fds(args.root),
        "reference_roots": [str(root) for root in args.reference_root],
        "references_found": sorted(references),
        "classification": "DEPENDENCY_NONE_PROVEN" if not references and not active("edgesniffer-kefi-full-meta-capture-first-v8.service") and not active("edgesniffer-kefi-capture-first-control-v8.service") and open_fds(args.root) == 0 else "DEPENDENCY_PRESENT",
        "outcomes_or_pnl_accessed": False,
    }
    write_once(args.output, result)
    print(json.dumps({"audit": str(args.output), "sha256": sha(args.output), "classification": result["classification"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
