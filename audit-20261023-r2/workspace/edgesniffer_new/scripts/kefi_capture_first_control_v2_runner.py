#!/usr/bin/env python3
"""Low-impact materializer for a fresh, bounded kEfi paper replication."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time


SOURCE_ROOT = pathlib.Path(os.environ["KEFI_SOURCE_ROOT"])
PROMOTION_ROOT = pathlib.Path(os.environ["KEFI_PROMOTION_ROOT"])
OUT = pathlib.Path(os.environ["KEFI_CONTROL_OUT"])
COLLECTOR_UNIT = os.environ["KEFI_COLLECTOR_UNIT"]
BOUNDARY_PATH = pathlib.Path(os.environ.get("KEFI_BOUNDARY_PATH", SOURCE_ROOT / "scientific-boundaries/paper-boundary-v1.json"))
DECODER = pathlib.Path(os.environ.get("KEFI_DECODER", "/root/kefi-work/unified-decoder-build/target/release/kefi-unified-decoder"))
MAX_WINDOWS_PER_CYCLE = int(os.environ.get("KEFI_MAX_WINDOWS_PER_CYCLE", "4"))
PRIORITY_WINDOW_NOT_BEFORE_ID = os.environ.get("KEFI_PRIORITY_WINDOW_NOT_BEFORE_ID", "")


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def boundary() -> dict:
    value = json.loads(BOUNDARY_PATH.read_text())
    if value.get("status") != "ACTIVE" or not value.get("windowNotBeforeId"):
        raise ValueError("active frozen paper boundary is absent")
    return value


def fresh_source() -> tuple[bool, str]:
    health_path = SOURCE_ROOT / "health.json"
    if not health_path.is_file():
        return False, "no source health"
    try:
        active = subprocess.run(
            ["/bin/systemctl", "is-active", "--quiet", COLLECTOR_UNIT], check=False
        ).returncode == 0
        if not active:
            raise ValueError("collector inactive")
        health = json.loads(health_path.read_text())
        received_at = dt.datetime.fromisoformat(health["latestReceivedAtUtc"].replace("Z", "+00:00"))
        if (dt.datetime.now(dt.timezone.utc) - received_at).total_seconds() > 45:
            raise ValueError("source stale")
        for field in ("continuityGaps", "receiveErrors", "reconnects"):
            if int(health.get(field, 0)) != 0:
                raise ValueError(f"source {field}")
    except Exception as exc:
        return False, str(exc)
    return True, "healthy"


def run_materializer(arguments: list[str]) -> dict:
    command = [
        "/usr/bin/python3", "/root/bot/EdgeSniffer/scripts/kefi_capture_first_control_materializer_v1.py",
        "--root", str(PROMOTION_ROOT), "--out", str(OUT), "--decoder", str(DECODER), *arguments,
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "materializer failed")
    return json.loads(next((line for line in reversed(completed.stdout.splitlines()) if line.strip()), "{}"))


def available_window_ids(start: str) -> list[str]:
    state_path = OUT / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {"processed": []}
    processed = set(state.get("processed", []))
    windows_root = PROMOTION_ROOT / "shared-windows"
    if not windows_root.is_dir():
        return []
    return sorted(
        item.name for item in windows_root.iterdir()
        if item.is_dir() and not item.name.startswith(".partial-") and item.name >= start and item.name not in processed
    )


def main() -> int:
    try:
        contract = boundary()
    except Exception as exc:
        print(json.dumps({"status": "SKIP", "reason": str(exc)}, sort_keys=True))
        return 0
    healthy, reason = fresh_source()
    if not healthy:
        print(json.dumps({"status": "SKIP", "reason": reason}, sort_keys=True))
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    cache_root = OUT / "partition-cache"
    queued = available_window_ids(contract["windowNotBeforeId"])
    # Prioritize a separately preregistered fresh cohort without deleting or
    # changing older windows. This is scheduling only; every window remains
    # subject to the same sealed-window verification and eventual replay.
    if PRIORITY_WINDOW_NOT_BEFORE_ID:
        queued.sort(key=lambda window_id: (window_id < PRIORITY_WINDOW_NOT_BEFORE_ID, window_id))
    queued = queued[:MAX_WINDOWS_PER_CYCLE]
    processed = 0
    for window_id in queued:
        healthy, reason = fresh_source()
        if not healthy:
            print(json.dumps({"status": "SKIP", "reason": reason, "processed": processed}, sort_keys=True))
            return 0
        result = run_materializer(["--window-id", window_id, "--cache-root", str(cache_root), "--skip-deduplicated-market"])
        if int(result.get("new", 0)) == 0:
            raise RuntimeError(f"FAIL_CLOSED: selected window was not materialized: {window_id}")
        processed += int(result["new"])
        time.sleep(1)
    if processed:
        healthy, _ = fresh_source()
        if healthy:
            run_materializer(["--rebuild-deduplicated-market"])
    print(json.dumps({"status": "DRAINED" if len(queued) < MAX_WINDOWS_PER_CYCLE else "CYCLE_LIMIT", "processed": processed, "queued": len(queued), "boundary_sha256": sha256(BOUNDARY_PATH)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
