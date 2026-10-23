#!/usr/bin/env python3
"""Fail closed before the kEfi full-meta lane exhausts or loses hot storage."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import tempfile


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic(path: pathlib.Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = pathlib.Path(handle.name)
    os.replace(temporary, path)


def volume(path: pathlib.Path) -> dict:
    stat = os.statvfs(path)
    return {"path": str(path), "freeGiB": round(stat.f_bavail * stat.f_frsize / 1024 ** 3, 3)}


def safe_volume(path: pathlib.Path) -> tuple[dict | None, dict | None]:
    try:
        return volume(path), None
    except OSError as error:
        return None, {"path": str(path), "reason": "STATVFS_FAILED", "error": str(error)}


def unit_state(command: list[str], unit: str) -> dict[str, str]:
    result = subprocess.run(
        [*command, "show", "--property=LoadState", "--property=ActiveState",
         "--property=SubState", "--property=MainPID", unit],
        check=False,
        capture_output=True,
        text=True,
    )
    state = {"showReturncode": str(result.returncode)}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            state[key] = value
    return state


def verified_stop(command: list[str], unit: str) -> dict:
    stopped = subprocess.run([*command, "stop", unit], check=False, capture_output=True, text=True)
    state = unit_state(command, unit)
    verified = (
        stopped.returncode == 0
        and state.get("showReturncode") == "0"
        and state.get("LoadState") == "loaded"
        and state.get("ActiveState") == "inactive"
        and state.get("SubState") in {"dead", "exited", "failed"}
        and state.get("MainPID") == "0"
    )
    return {
        "action": "stop",
        "unit": unit,
        "stopReturncode": stopped.returncode,
        "stopStderr": stopped.stderr.strip(),
        "stateAfter": state,
        "verifiedStopped": verified,
    }


def mounted_source(mount: pathlib.Path) -> dict:
    result = subprocess.run(
        ["findmnt", "-no", "SOURCE", "--target", str(mount)],
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "mount": str(mount),
        "findmntReturncode": result.returncode,
        "source": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hot-root", type=pathlib.Path, required=True)
    parser.add_argument("--host-mount", type=pathlib.Path, required=True)
    parser.add_argument("--state", type=pathlib.Path, required=True)
    parser.add_argument("--collector-unit", required=True)
    parser.add_argument("--materializer-unit")  # Compatibility alias for older unit files.
    parser.add_argument("--materializer-timer-unit")
    parser.add_argument("--materializer-service-unit")
    parser.add_argument("--hot-mount", type=pathlib.Path)
    parser.add_argument("--expected-hot-source")
    parser.add_argument("--systemd-scope", choices=("user", "system"), default="user")
    parser.add_argument("--warning-free-gib", type=float, default=180.0)
    parser.add_argument("--emergency-free-gib", type=float, default=150.0)
    args = parser.parse_args()
    if args.emergency_free_gib >= args.warning_free_gib:
        raise SystemExit("INVALID_WATERMARKS")
    if bool(args.hot_mount) != bool(args.expected_hot_source):
        raise SystemExit("HOT_MOUNT_IDENTITY_ARGUMENTS_REQUIRED_TOGETHER")

    command = ["systemctl"]
    if args.systemd_scope == "user":
        command.append("--user")
    actions: list[dict] = []
    mount_identity = None
    mount_failed = False
    if args.hot_mount:
        mount_identity = mounted_source(args.hot_mount)
        mount_failed = (
            mount_identity["findmntReturncode"] != 0
            or mount_identity["source"] != args.expected_hot_source
        )

    hot_volume, hot_error = safe_volume(args.hot_root)
    host_volume, host_error = safe_volume(args.host_mount)
    volumes = [item for item in (hot_volume, host_volume) if item]
    emergency = [item for item in volumes if item["freeGiB"] < args.emergency_free_gib]
    warning = [item for item in volumes if item["freeGiB"] < args.warning_free_gib]
    for error in (hot_error, host_error):
        if error:
            emergency.append(error)
            warning.append(error)
    if mount_failed:
        emergency = [*emergency, {"path": str(args.hot_mount), "reason": "HOT_MOUNT_IDENTITY_FAILED"}]
        warning = [*warning, {"path": str(args.hot_mount), "reason": "HOT_MOUNT_IDENTITY_FAILED"}]

    status = "HEALTHY" if not warning else "WARNING"
    paused = False
    materializer_units = [unit for unit in (
        args.materializer_timer_unit,
        args.materializer_service_unit,
        args.materializer_unit,
    ) if unit]
    materializer_stop_failed = False
    if warning:
        for unit in dict.fromkeys(materializer_units):
            action = verified_stop(command, unit)
            actions.append(action)
            materializer_stop_failed = materializer_stop_failed or not action["verifiedStopped"]
        if materializer_stop_failed and not emergency:
            status = "WARNING_MATERIALIZER_STOP_FAILED"
    if emergency:
        collector_action = verified_stop(command, args.collector_unit)
        actions.append(collector_action)
        stopped = collector_action["verifiedStopped"]
        status = "EMERGENCY_PAUSED_VERIFIED" if stopped else "EMERGENCY_STOP_FAILED"
        if materializer_stop_failed:
            status += "_MATERIALIZER_STOP_FAILED"
        if mount_failed:
            status = "MOUNT_IDENTITY_FAILED_" + status
        atomic(args.state.parent / "capacity-pause.json", {
            "schema": "KEFI_LOCAL_FULL_META_CAPACITY_PAUSE_V2",
            "recordedAtUtc": now(),
            "reason": "HOT_MOUNT_IDENTITY_FAILED" if mount_failed else "LOCAL_STORAGE_BELOW_EMERGENCY_WATERMARK",
            "affectedVolumes": emergency,
            "mountIdentity": mount_identity,
            "collectorUnit": args.collector_unit,
            "collectorStoppedVerified": stopped,
            "systemdActions": actions,
            "manualResumeRequired": True,
        })
        paused = stopped
    atomic(args.state, {
        "schema": "KEFI_LOCAL_FULL_META_CAPACITY_GUARD_V2",
        "generatedAtUtc": now(),
        "volumes": volumes,
        "mountIdentity": mount_identity,
        "status": status,
        "paused": paused,
        "systemdActions": actions,
        "warningFreeGiB": args.warning_free_gib,
        "emergencyFreeGiB": args.emergency_free_gib,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
