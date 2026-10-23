#!/usr/bin/env python3
"""Drain a small number of sealed kEfi control windows without competing with capture.

Systemd supplies the low CPU and idle-I/O limits.  This runner keeps the
scientific safety gate at every serial window boundary: a stale or interrupted
source ends the cycle before another control window is decoded.
"""

import datetime as dt
import json
import pathlib
import subprocess
import sys
import time


SOURCE_ROOT = pathlib.Path("/mnt/edgesniffer-kefi-hot/kefi-capture-first-live-v5")
ROOT = pathlib.Path("/mnt/edgesniffer-expansion/kefi-capture-first-promotions-v5")
OUT = pathlib.Path("/mnt/edgesniffer-kefi-hot/kefi-capture-first-control-v5")
CACHE_ROOT = OUT / "partition-cache"
DECODER = pathlib.Path("/root/kefi-work/unified-decoder-build/target/release/kefi-unified-decoder")
BOUNDARY = "kefi-20261010T213547710Z"
MAX_WINDOWS_PER_CYCLE = 8


def fresh_source() -> tuple[bool, str]:
    health_path = SOURCE_ROOT / "health.json"
    if not health_path.is_file():
        return False, "no source health"
    try:
        health = json.loads(health_path.read_text())
        value = (
            health.get("latestReceivedAtUtc")
            or health.get("latestReceivedAt")
            or health.get("lastReceivedAt")
            or health.get("latest_received_at")
        )
        if not value:
            raise ValueError("missing freshness timestamp")
        received_at = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        age = (dt.datetime.now(dt.timezone.utc) - received_at).total_seconds()
        if age > 45:
            raise ValueError(f"source stale {age:.1f}s")
        gaps = int(health.get("continuityGaps", health.get("continuityFailures", health.get("continuity_failures", 0))))
        if gaps != 0:
            raise ValueError("continuity failure")
        if int(health.get("receiveErrors", 0)) != 0 or int(health.get("reconnects", 0)) != 0:
            raise ValueError("source reconnect/error")
    except Exception as exc:
        return False, str(exc)
    return True, "healthy"


def run_materializer(arguments: list[str]) -> dict:
    command = [
        "/usr/bin/python3",
        "/root/bot/EdgeSniffer/scripts/kefi_capture_first_control_materializer_v1.py",
        "--root", str(ROOT), "--out", str(OUT), "--decoder", str(DECODER),
        *arguments,
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "materializer failed")
    line = next((line for line in reversed(completed.stdout.splitlines()) if line.strip()), "{}")
    return json.loads(line)


def available_window_ids() -> list[str]:
    """Use a cheap directory listing; each selected window is then verified directly.

    Re-scanning every historical promotion receipt over NFS before every batch
    turned a one-window worker into a metadata-bound worker.  This is only a
    work queue hint, never scientific proof: materialize_one still verifies the
    selected immutable manifest and every associated COMPLETE receipt.
    """
    state_path = OUT / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {"processed": []}
    processed = set(state.get("processed", []))
    windows_root = ROOT / "shared-windows"
    return sorted(
        entry.name
        for entry in windows_root.iterdir()
        if entry.is_dir() and entry.name >= BOUNDARY and entry.name not in processed
    )


def materialize_one(window_id: str) -> dict:
    return run_materializer([
        "--window-id", window_id,
        "--cache-root", str(CACHE_ROOT),
        "--skip-deduplicated-market",
    ])


def rebuild_deduplicated_market() -> dict:
    return run_materializer(["--rebuild-deduplicated-market"])


def main() -> int:
    processed = 0
    healthy, reason = fresh_source()
    if not healthy:
        print(json.dumps({"status": "SKIP", "reason": reason, "processed": processed}, sort_keys=True))
        return 0
    queued = available_window_ids()[:MAX_WINDOWS_PER_CYCLE]
    for window_id in queued:
        healthy, reason = fresh_source()
        if not healthy:
            print(json.dumps({"status": "SKIP", "reason": reason, "processed": processed}, sort_keys=True))
            return 0
        try:
            result = materialize_one(window_id)
        except RuntimeError as exc:
            message = str(exc)
            if any(marker in message for marker in ("missing promotion receipt", "incomplete promotion receipt", "no sealed partitions")):
                print(json.dumps({"status": "PENDING_SEAL", "window": window_id, "processed": processed}, sort_keys=True))
                break
            raise
        new = int(result.get("new", 0))
        processed += new
        if new == 0:
            raise RuntimeError(f"FAIL_CLOSED: direct window was not materialized: {window_id}")
        time.sleep(1)
    healthy, reason = fresh_source()
    if healthy and processed:
        rebuild_deduplicated_market()
    print(json.dumps({"status": "DRAINED" if len(queued) < MAX_WINDOWS_PER_CYCLE else "CYCLE_LIMIT", "processed": processed, "queued": len(queued)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
