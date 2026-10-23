#!/usr/bin/env python3
"""Permanently void a replacement holdout that failed before any snapshot existed."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_once(path: Path, value: dict) -> None:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--validation", type=Path, required=True)
    args = p.parse_args()
    boundary = args.root / "boundary.json"
    output = args.root / "void-receipt-v1.json"
    if output.exists() or not boundary.is_file() or not args.validation.is_file():
        raise SystemExit("FAIL_CLOSED: invalid void inputs")
    snapshots = list(args.root.rglob("durable-snapshots/*.json"))
    validation = json.loads(args.validation.read_text())
    if snapshots or validation.get("snapshots") != 0 or validation.get("status") != "FAIL_CLOSED":
        raise SystemExit("FAIL_CLOSED: nonzero evidence cannot use pre-snapshot void path")
    failure = str(validation.get("failure", ""))
    if "broadcast from" not in failure or "not available" not in failure:
        raise SystemExit("FAIL_CLOSED: unexpected failure cause")
    value = {
        "schema": "KEFI_REPLACEMENT_UNTOUCHED_SELECTION_HOLDOUT_V1_VOID",
        "status": "VOIDED_PERMANENTLY",
        "reason": "REPLAY_HORIZON_EXCEEDED_BEFORE_WARMUP",
        "boundary_sha256": sha(boundary),
        "validation_sha256": sha(args.validation),
        "snapshot_count": 0,
        "reuse_prohibited": True,
        "outcomes_or_pnl_accessed": False,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    write_once(output, value)
    print(json.dumps({"void_receipt": str(output), "sha256": sha(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
