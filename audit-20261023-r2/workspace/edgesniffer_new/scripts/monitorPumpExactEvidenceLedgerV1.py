#!/usr/bin/env python3
"""Independent liveness guard for the observation-only exact evidence ledger."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import time
from typing import Any


SCHEMA = "PUMP_EXACT_EVIDENCE_LEDGER_WATCHDOG_V1"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_utc(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def service_state(service: str) -> dict[str, str]:
    result = subprocess.run(
        [
            "systemctl", "show", service,
            "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
            "-p", "NRestarts", "-p", "InvocationID", "-p", "Environment",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )


def normalized_uuid(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "")


def expected_endpoint_identity(service: dict[str, str]) -> str | None:
    for value in service.get("Environment", "").split():
        if value.startswith("PUMP_EXACT_LEDGER_GRPC_URL="):
            endpoint = value.split("=", 1)[1]
            label = endpoint.split("://", 1)[-1].split("/", 1)[0]
            digest = hashlib.sha256(endpoint.encode()).hexdigest()[:16]
            return f"{label}#{digest}"
    return None


def read_slot(root: pathlib.Path) -> int | None:
    try:
        return int((root / "state/last-finalized-slot").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def evaluate(
    now: dt.datetime,
    service: dict[str, str],
    health: dict[str, Any],
    stale_seconds: int,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    active = service.get("ActiveState") == "active" and service.get("SubState") == "running"
    if not active:
        reasons.append("PROCESS_NOT_ACTIVE")
    main_pid = int(service.get("MainPID") or 0)
    health_pid = int(health.get("pid") or 0)
    if active and (main_pid <= 0 or health_pid != main_pid):
        reasons.append("HEALTH_WRITER_PID_MISMATCH")
    invocation_id = normalized_uuid(service.get("InvocationID"))
    health_instance = normalized_uuid(health.get("processInstanceUuid"))
    if active and (not invocation_id or health_instance != invocation_id):
        reasons.append("HEALTH_WRITER_INSTANCE_MISMATCH")
    endpoint_identity = expected_endpoint_identity(service)
    if endpoint_identity and health.get("endpointIdentity") != endpoint_identity:
        reasons.append("HEALTH_WRITER_ENDPOINT_MISMATCH")
    if not parse_utc(health.get("startupTimeUtc")):
        reasons.append("HEALTH_WRITER_STARTUP_MISSING")
    health_at = parse_utc(health.get("atUtc"))
    if health_at is None:
        reasons.append("HEALTH_RECORD_MISSING")
    elif (now - health_at).total_seconds() > stale_seconds:
        reasons.append("HEALTH_RECORD_STALE")
    if health and health.get("evidenceFlowHealthy") is not True:
        reasons.append("EVIDENCE_FLOW_UNHEALTHY")
    raw_at = parse_utc(health.get("lastRawStreamUpdateUtc"))
    append_at = parse_utc(health.get("lastPartitionAppendUtc"))
    if raw_at and (now - raw_at).total_seconds() > stale_seconds:
        reasons.append("RAW_STREAM_STALE")
    if append_at and (now - append_at).total_seconds() > stale_seconds:
        reasons.append("PARTITION_APPEND_STALE")
    return not reasons, sorted(set(reasons))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path("/var/lib/pump-exact-evidence-ledger-v1"))
    parser.add_argument("--service", default="pump-exact-evidence-ledger-v1.service")
    parser.add_argument("--stale-seconds", type=int, default=180)
    parser.add_argument("--sustain-seconds", type=int, default=60)
    parser.add_argument("--cooldown-seconds", type=int, default=300)
    parser.add_argument("--max-restarts-per-hour", type=int, default=3)
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()

    now = utc_now()
    output = args.root / "operations-v1"
    state_path = output / "watchdog-state.json"
    state = read_json(state_path)
    service = service_state(args.service)
    health = read_json(args.root / "health.json")
    healthy, reasons = evaluate(now, service, health, args.stale_seconds)
    restart_history = [
        value
        for value in state.get("restartHistoryUtc", [])
        if (timestamp := parse_utc(value)) is not None
        and (now - timestamp).total_seconds() <= 3600
    ]
    unhealthy_since = parse_utc(state.get("unhealthySinceUtc"))
    if healthy:
        unhealthy_since = None
    elif unhealthy_since is None:
        unhealthy_since = now

    restarted = False
    restart_blocker = None
    last_restart = parse_utc(state.get("lastRestartUtc"))
    sustained = unhealthy_since is not None and (now - unhealthy_since).total_seconds() >= args.sustain_seconds
    cooldown_met = last_restart is None or (now - last_restart).total_seconds() >= args.cooldown_seconds
    if not healthy and sustained and not args.no_restart:
        if len(restart_history) >= args.max_restarts_per_hour:
            restart_blocker = "RESTART_RATE_LIMIT"
        elif not cooldown_met:
            restart_blocker = "RESTART_COOLDOWN"
        elif service.get("ActiveState") == "active":
            incident = {
                "schema": SCHEMA,
                "detectedAtUtc": now.isoformat().replace("+00:00", "Z"),
                "reasons": reasons,
                "before": {
                    "service": service,
                    "health": health,
                    "lastFinalizedSlot": read_slot(args.root),
                },
            }
            result = subprocess.run(["systemctl", "restart", args.service], check=False, capture_output=True, text=True)
            incident["restartExitCode"] = result.returncode
            incident["restartStderr"] = result.stderr.strip()
            restarted = result.returncode == 0
            if restarted:
                stamp = now.strftime("%Y%m%dT%H%M%SZ")
                atomic_json(output / "incidents" / f"{stamp}.json", incident)
                restart_history.append(now.isoformat().replace("+00:00", "Z"))
                last_restart = now
                unhealthy_since = None
            else:
                restart_blocker = "SYSTEMD_RESTART_FAILED"
        else:
            restart_blocker = "PROCESS_INACTIVE_SYSTEMD_OWNS_RECOVERY"

    current = {
        "schema": SCHEMA,
        "atUtc": now.isoformat().replace("+00:00", "Z"),
        "processState": "PROCESS_ALIVE" if service.get("ActiveState") == "active" else "PROCESS_NOT_ALIVE",
        "evidenceFlowState": "EVIDENCE_FLOW_HEALTHY" if healthy else "EVIDENCE_FLOW_UNHEALTHY",
        "reasons": reasons,
        "unhealthySinceUtc": unhealthy_since.isoformat().replace("+00:00", "Z") if unhealthy_since else None,
        "restarted": restarted,
        "restartBlocker": restart_blocker,
        "lastRestartUtc": last_restart.isoformat().replace("+00:00", "Z") if last_restart else None,
        "restartHistoryUtc": restart_history,
        "service": service,
        "lastFinalizedSlot": read_slot(args.root),
        "reconciliationBacklog": health.get("reconciliationBacklog"),
        "lastExactCompletePartitionUtc": health.get("lastExactCompletePartitionUtc"),
    }
    atomic_json(state_path, current)
    print(json.dumps(current, indent=2, sort_keys=True))
    return 0 if healthy or restarted else 2


if __name__ == "__main__":
    raise SystemExit(main())
