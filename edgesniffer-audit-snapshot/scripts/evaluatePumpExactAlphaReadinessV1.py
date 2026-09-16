#!/usr/bin/env python3
"""Conservative N=10k operational gate for exact-evidence alpha discovery."""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import pathlib
from typing import Any


SCHEMA = "PUMP_EXACT_ALPHA_READINESS_GATE_V1"
GIB = 1024**3


def load(path: pathlib.Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def free_bytes(path: pathlib.Path) -> int:
    try:
        return os.statvfs(path).f_bavail * os.statvfs(path).f_frsize
    except OSError:
        return 0


def elapsed_hours(start: str | None, end: str | None) -> float:
    if not start or not end:
        return 0.0
    try:
        left = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        right = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max((right - left).total_seconds() / 3600, 0.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=pathlib.Path)
    parser.add_argument("--volume", type=pathlib.Path, default=pathlib.Path("/mnt/HC_Volume_106775198"))
    args = parser.parse_args()
    root = args.root
    readiness = load(root / "readiness-v1/current.json")
    health = load(root / "health.json")
    watchdog = load(root / "operations-v1/watchdog-state.json")
    coverage = load(root / "audits-v1/admission-coverage-current.json")
    mayhem = load(root / "audits-v1/mayhem-v2-current.json")
    quote = load(root / "audits-v1/quote-v2-current.json")
    gaps = [load(pathlib.Path(path)) for path in glob.glob(str(root / "operations-v1/gaps/*.json"))]

    exact_n = int(readiness.get("exactAdmittedN") or 0)
    utc = readiness.get("utcCoverage") or {}
    represented_hours = int(utc.get("admittedMintHoursRepresented") or 0)
    continuous_span = elapsed_hours(utc.get("firstCreateUtc"), utc.get("lastCreateUtc"))
    exact_hours = int(utc.get("exactPartitionHours") or 0)
    operational_healthy = (
        health.get("processAlive") is True
        and health.get("evidenceFlowHealthy") is True
        and health.get("streamConnected") is True
        and watchdog.get("evidenceFlowState") == "EVIDENCE_FLOW_HEALTHY"
        and not watchdog.get("reasons")
    )
    reconciliation_clean = (
        bool(health.get("lastExactCompletePartitionUtc"))
        and (health.get("finalizedReconciliationAgeMs") is None
             or int(health.get("finalizedReconciliationAgeMs")) < 90_000)
    )
    bias = coverage.get("biasClassification")
    bias_understood = bias in {"REPRESENTATIVE", "MILDLY_BIASED", "MATERIALLY_BIASED", "INSUFFICIENT_N"}
    broad_support = (
        bias in {"REPRESENTATIVE", "MILDLY_BIASED"}
        and mayhem.get("classification") == "CURRENT_LEDGER_SUFFICIENT_FOR_MAYHEM_EXACT_REPLAY"
        and quote.get("classification") == "CURRENT_LEDGER_SUPPORTS_EXACT_QUOTE_NATIVE_REPLAY"
    )

    research = readiness.get("researchPlan") or {}
    exact_per_hour = research.get("observedExactAdmissionRatePerHour")
    growth_per_day = health.get("estimatedBytesPerDay")
    root_free = free_bytes(pathlib.Path("/"))
    volume_free = free_bytes(args.volume)
    usable_combined = volume_free + max(root_free - 10 * GIB, 0)
    remaining_to_30k = max(30_000 - exact_n, 0)
    days_to_30k = (
        remaining_to_30k / float(exact_per_hour) / 24
        if isinstance(exact_per_hour, (int, float)) and exact_per_hour > 0
        else None
    )
    required_to_30k = (
        days_to_30k * float(growth_per_day) * 1.25
        if days_to_30k is not None and isinstance(growth_per_day, (int, float))
        else None
    )
    # The bind-mounted ledger cannot spill onto root automatically. Root remains
    # runtime/cache reserve, so the gate requires the volume alone to reach N30k.
    storage_safe = required_to_30k is not None and volume_free >= required_to_30k

    blockers = []
    if exact_n < 10_000:
        blockers.append(f"EXACT_N_LT_10000:{exact_n}")
    if represented_hours < 24 or exact_hours < 24 or continuous_span < 24:
        blockers.append(
            f"HEALTHY_SOURCE_CHAIN_HOURS_LT_24:represented={represented_hours},exact={exact_hours},span={continuous_span:.3f}"
        )
    if not operational_healthy:
        blockers.append("CURRENT_EVIDENCE_FLOW_UNHEALTHY")
    if not reconciliation_clean:
        blockers.append("COVERAGE_RECONCILIATION_NOT_CLEAN")
    if not bias_understood:
        blockers.append("ADMISSION_BIAS_NOT_AUDITED")
    if not broad_support:
        blockers.append(f"SUPPORTED_EXACT_POPULATION_NOT_BROAD:bias={bias},mayhem={mayhem.get('classification')}")
    if not storage_safe:
        blockers.append("STORAGE_RUNWAY_TO_N30000_NOT_PROVEN")

    report = {
        "schema": SCHEMA,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "classification": "READY_FOR_EXACT_ALPHA_DISCOVERY" if not blockers else "BLOCKED",
        "exactAdmittedN": exact_n,
        "sourceChainHours": {
            "admittedMintHoursRepresented": represented_hours,
            "exactReconciledPartitionHours": exact_hours,
            "firstToLastCreateSpanHours": continuous_span,
            "minimumRequired": 24,
        },
        "operationalHealth": {
            "healthy": operational_healthy,
            "processAlive": health.get("processAlive"),
            "evidenceFlowHealthy": health.get("evidenceFlowHealthy"),
            "streamConnected": health.get("streamConnected"),
            "watchdogState": watchdog.get("evidenceFlowState"),
            "reconciliationBacklog": health.get("reconciliationBacklog"),
        },
        "knownEvidenceGaps": {
            "count": len(gaps),
            "documentedAndExcludedFromContinuityClaims": True,
            "historicalGapExactlyRecovered": False if gaps else None,
        },
        "coverage": {
            "reconciliationClean": reconciliation_clean,
            "admissionBias": bias,
            "biasUnderstood": bias_understood,
            "broadExactReplaySupport": broad_support,
            "mayhem": mayhem.get("classification"),
            "quoteNative": quote.get("classification"),
        },
        "storage": {
            "rootFreeGiB": root_free / GIB,
            "volumeFreeGiB": volume_free / GIB,
            "usableCombinedGiBAfter10GiBRootReserve": usable_combined / GIB,
            "estimatedGrowthGiBPerDay": float(growth_per_day) / GIB if isinstance(growth_per_day, (int, float)) else None,
            "observedExactAdmissionRatePerHour": exact_per_hour,
            "estimatedDaysToN30000": days_to_30k,
            "requiredGiBToN30000With25PctMargin": required_to_30k / GIB if required_to_30k is not None else None,
            "runwaySafeToN30000": storage_safe,
        },
        "blockers": blockers,
        "alphaScanStarted": False,
    }
    output = root / "readiness-v1/alpha-discovery-gate-current.json"
    atomic_json(output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
