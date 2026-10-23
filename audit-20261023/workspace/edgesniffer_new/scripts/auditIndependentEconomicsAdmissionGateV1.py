#!/usr/bin/env python3
"""Audit the independent strategy surface without promoting a policy.

The strategy lab is allowed to calculate exploratory surfaces at research
notionals.  This gate checks that the surface is actually the intended
read-only 1-second-delay, 0.1/0.5/1.0 SOL experiment and keeps validation
blocked until controls and native state parity exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any


EXPECTED_NOTIONALS = {"0.1", "0.5", "1"}
EXPECTED_DELAY_MS = 1000


def sha256_file(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def strategy_notional(strategy_id: Any) -> str | None:
    parts = str(strategy_id or "").split("|")
    if len(parts) < 3:
        return None
    try:
        return format(float(parts[-2]), "g")
    except (TypeError, ValueError):
        return None


def summarize_surface(surface: list[Any]) -> dict[str, Any]:
    by_notional: dict[str, list[dict[str, Any]]] = {key: [] for key in EXPECTED_NOTIONALS}
    for row in surface:
        if not isinstance(row, dict):
            continue
        notional = strategy_notional(row.get("strategy_id"))
        metrics = row.get("metrics")
        if notional in by_notional and isinstance(metrics, dict):
            by_notional[notional].append(metrics)

    result: dict[str, Any] = {}
    for notional, metrics_rows in by_notional.items():
        pnls = [float(row["pnl_sol"]) for row in metrics_rows if row.get("pnl_sol") is not None]
        pfs = [float(row["profit_factor"]) for row in metrics_rows if row.get("profit_factor") is not None]
        result[notional] = {
            "surface_rows": len(metrics_rows),
            "exploratory_positive_pnl_rows": sum(pnl > 0 for pnl in pnls),
            "exploratory_pnl_range_sol": [min(pnls), max(pnls)] if pnls else None,
            "exploratory_pf_range": [min(pfs), max(pfs)] if pfs else None,
            "not_a_policy_selection": True,
        }
    return result


def audit(report_path: Path, parity_path: Path | None) -> dict[str, Any]:
    report = read_json(report_path)
    parity = read_json(parity_path)
    surface = report.get("surface") if isinstance(report.get("surface"), list) else []
    report_notionals = {format(float(value), "g") for value in (report.get("notionals_sol") or [])}
    surface_notionals = {key for key in EXPECTED_NOTIONALS if any(strategy_notional(row.get("strategy_id")) == key for row in surface if isinstance(row, dict))}
    contract_checks = {
        "schema": report.get("schema") == "EDGESNIFFER_INDEPENDENT_STRATEGY_LAB_V1",
        "read_only": report.get("read_only") is True,
        "trading_disabled": report.get("trading_enabled") is False,
        "policy_not_frozen": report.get("policy_frozen") is False,
        "selection_not_performed": report.get("selection_performed") is False,
        "promotion_blocked_by_lab": report.get("promotion_blocked") is True,
        "one_second_delay": report.get("entry_delay_ms") == EXPECTED_DELAY_MS,
        "all_research_notionals_declared": report_notionals == EXPECTED_NOTIONALS,
        "all_research_notionals_present_in_surface": surface_notionals == EXPECTED_NOTIONALS,
    }
    discovery_pass = all(contract_checks.values()) and int(report.get("admitted_windows") or 0) > 0 and bool(surface)
    controls = int(report.get("control_windows_loaded") or 0)
    parity_status = parity.get("gate_status") if parity else "MISSING_PARITY_GATE"
    validation_allowed = discovery_pass and controls > 0 and parity_status == "PASS_NATIVE_RECEIPT_AND_STATE_PARITY"
    return {
        "schema": "INDEPENDENT_ECONOMICS_ADMISSION_GATE_V1",
        "generated_at_ms": int(time.time() * 1000),
        "read_only": True,
        "trading_enabled": False,
        "policy_frozen": False,
        "promotion_allowed": False,
        "validation_allowed": validation_allowed,
        "discovery_surface_status": "PASS_EXPLORATORY_SURFACE" if discovery_pass else "BLOCKED_INVALID_DISCOVERY_SURFACE",
        "validation_status": "PASS_ADMISSION_PREREQUISITES" if validation_allowed else "BLOCKED_CONTROLS_OR_NATIVE_PARITY",
        "inputs": {
            "strategy_report": {"path": str(report_path), "sha256": sha256_file(report_path)},
            "native_parity_gate": {"path": str(parity_path) if parity_path else None, "sha256": sha256_file(parity_path)},
        },
        "contract_checks": contract_checks,
        "surface": {
            "admitted_windows": report.get("admitted_windows", 0),
            "outcome_rows": len(report.get("outcomes") or []) if isinstance(report.get("outcomes"), list) else 0,
            "surface_rows": len(surface),
            "control_windows_loaded": controls,
            "notionals_sol": sorted(report_notionals),
            "by_notional": summarize_surface(surface),
        },
        "native_parity": {
            "gate_status": parity_status,
            "economics_input_allowed": parity.get("economics_input_allowed") is True if parity else False,
        },
        "reason": "Exploratory PnL surfaces are retained as hypotheses; no current-cohort result can freeze or promote a policy.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--native-parity", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.report, args.native_parity)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("discovery_surface_status", "validation_status", "validation_allowed", "surface")}, sort_keys=True))


if __name__ == "__main__":
    main()
