#!/usr/bin/env python3
"""External, fail-closed freshness watchdog for the 2CQ full-meta collector."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import time


SCHEMA = "2CQ_FULL_META_FRESHNESS_WATCHDOG_V1"


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: pathlib.Path, value: dict, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    if immutable:
        path.chmod(0o444)


def read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def newest_partial(root: pathlib.Path) -> pathlib.Path | None:
    paths = list(root.glob("partitions/*/*/*/*/evidence.frames.partial"))
    return max(paths, key=lambda path: path.stat().st_mtime_ns) if paths else None


def snapshot(root: pathlib.Path) -> dict:
    health = read_json(root / "health.json")
    partial = newest_partial(root)
    if partial is None:
        raise RuntimeError("NO_ACTIVE_EVIDENCE_PARTIAL")
    stat = partial.stat()
    return {
        "observedAtUtc": utcnow(),
        "framesReceived": int(health["framesReceived"]),
        "latestReceivedAtUtc": health.get("latestReceivedAtUtc"),
        "lastSyncAtUtc": health.get("lastSyncAtUtc"),
        "latestSlot": health.get("latestSlot"),
        "sourceEpoch": health.get("currentEpoch"),
        "partialPath": str(partial),
        "partialBytes": stat.st_size,
        "partialMtimeNs": stat.st_mtime_ns,
    }


def parse_utc(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def progressed(previous: dict, current: dict) -> bool:
    return (
        current["framesReceived"] > previous["framesReceived"]
        and (
            current["partialPath"] != previous["partialPath"]
            or current["partialBytes"] > previous["partialBytes"]
            or current["partialMtimeNs"] > previous["partialMtimeNs"]
        )
    )


def restart(service: str) -> None:
    subprocess.run(["/usr/bin/systemctl", "restart", service], check=True, timeout=30)


def await_fresh(root: pathlib.Path, baseline: dict, timeout_seconds: int) -> dict | None:
    end = time.monotonic() + timeout_seconds
    while time.monotonic() < end:
        time.sleep(2)
        candidate = snapshot(root)
        if progressed(baseline, candidate):
            return candidate
    return None


def emit_gap(root: pathlib.Path, watch_root: pathlib.Path, baseline: dict, detection: dict, service: str) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    preliminary = {
        "schema": "2CQ_FULL_META_WATCHDOG_PRELIMINARY_STALL_V1",
        "lastKnownGood": baseline,
        "detection": detection,
        "targetService": service,
        "backfillPerformed": False,
        "scientificTreatment": "FAIL_CLOSED_UNCOVERED_INTERVAL",
    }
    preliminary_path = root / "incidents" / f"full-meta-watchdog-preliminary-{stamp}.json"
    atomic_json(preliminary_path, preliminary, immutable=True)
    restarted_at = utcnow()
    try:
        restart(service)
        result = "RESTART_REQUESTED"
    except Exception as exc:  # Keep a durable receipt even when systemd rejects it.
        result = f"RESTART_FAILED:{type(exc).__name__}:{exc}"
    fresh = await_fresh(root, baseline, 120) if result == "RESTART_REQUESTED" else None
    receipt = {
        "schema": "2CQ_FULL_META_WATCHDOG_CONTINUITY_GAP_V1",
        "preliminaryReceipt": str(preliminary_path),
        "lastKnownGood": baseline,
        "watchdogDetection": detection,
        "restartTimestampUtc": restarted_at,
        "targetService": service,
        "restartResult": result,
        "firstFresh": fresh,
        "backfillPerformed": False,
        "scientificTreatment": "FAIL_CLOSED_UNCOVERED_INTERVAL",
    }
    receipt_path = root / "incidents" / f"full-meta-watchdog-gap-{stamp}.json"
    atomic_json(receipt_path, receipt, immutable=True)
    # This append-only event index survives collector health/epoch resets and is
    # the continuity marker that downstream prospective lanes must consult.
    marker = watch_root / "continuity-events.jsonl"
    with marker.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"receipt": str(receipt_path), "event": "FULL_META_GAP", "detectedAtUtc": detection["observedAtUtc"]}, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run(args: argparse.Namespace) -> None:
    state_path = args.watch_root / "state.json"
    health_path = args.watch_root / "health.json"
    previous = None
    confirmations = 0
    while True:
        current = snapshot(args.root)
        if previous is None:
            previous = current
            status = "BASELINE"
        else:
            last_sync = parse_utc(current["lastSyncAtUtc"])
            stale_age = (dt.datetime.now(dt.timezone.utc) - last_sync).total_seconds() if last_sync else float("inf")
            if progressed(previous, current):
                confirmations = 0
                status = "FRESH"
            elif stale_age >= args.stale_seconds:
                confirmations += 1
                status = "STALE_CONFIRMATION"
            else:
                confirmations = 0
                status = "WAITING"
            if confirmations >= args.confirmations:
                detection = {**current, "staleAgeSeconds": stale_age, "confirmations": confirmations}
                emit_gap(args.root, args.watch_root, previous, detection, args.service)
                previous = None
                confirmations = 0
                status = "INTERVENED"
            else:
                previous = current
        state = {"schema": SCHEMA, "updatedAtUtc": utcnow(), "status": status, "confirmations": confirmations, "checkIntervalSeconds": args.interval, "staleThresholdSeconds": args.stale_seconds, "requiredConfirmations": args.confirmations, "latestSnapshot": current}
        atomic_json(state_path, state)
        atomic_json(health_path, state)
        if args.once:
            return
        time.sleep(args.interval)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--watch-root", type=pathlib.Path, required=True)
    parser.add_argument("--service", default="edgesniffer-2cq-full-meta-prospective-v1.service")
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--stale-seconds", type=int, default=90)
    parser.add_argument("--confirmations", type=int, default=2)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval != 15 or args.stale_seconds != 90 or args.confirmations != 2:
        raise RuntimeError("FROZEN_WATCHDOG_POLICY_MISMATCH")
    args.watch_root.mkdir(parents=True, exist_ok=True)
    run(args)


if __name__ == "__main__":
    main()
