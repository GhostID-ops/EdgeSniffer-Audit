#!/usr/bin/env python3
"""Create a deterministic SHA-256 manifest for an evidence archive root."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    if not source.is_dir():
        raise SystemExit(f"source is not a directory: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    with temporary.open("w", encoding="utf-8", newline="\n") as manifest:
        for file_path in sorted(path for path in source.rglob("*") if path.is_file()):
            relative = file_path.relative_to(source).as_posix()
            manifest.write(f"{sha256(file_path)}\t{file_path.stat().st_size}\t{relative}\n")
    temporary.replace(output)


if __name__ == "__main__":
    main()
