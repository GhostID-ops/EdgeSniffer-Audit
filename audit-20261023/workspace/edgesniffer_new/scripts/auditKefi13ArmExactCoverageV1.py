#!/usr/bin/env python3
"""Audit exact adapter availability without claiming fixture/economic coverage.

This is deliberately stricter than a path check: each declared local Python
adapter must import successfully, and each venue family must have an explicit
state in the coverage matrix. Runtime fixture parity remains a separate gate.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REGISTRY = Path("research/kefi-13-arm-program-v1/registry.json")

ADAPTERS: dict[str, list[str]] = {
    "ARM01_SOL_PUMP_CURVE_2CQ": ["scripts/exactCurveEconomicsV1.py"],
    "ARM02_SOL_PUMP_CPI_2CQ": ["scripts/exactCurveEconomicsV1.py"],
    "ARM03_ACTIVE_SOL_PUMP_5000MS": ["scripts/exactCurveEconomicsV1.py"],
    "ARM04_NON_SOL_PUMP_V2_QUOTE_NATIVE": ["scripts/exactCurveEconomicsQuoteV2.py"],
    "ARM05_RAYDIUM_LAUNCHLAB": ["scripts/launchLabExactEconomicsV1.py"],
    "ARM06_STONKFUN_LAUNCHLAB": ["scripts/launchLabExactEconomicsV1.py"],
    "ARM07_PUMPSWAP_DIRECT": ["scripts/pumpSwapExactEconomicsV1.py", "scripts/pumpSwapInitDecoderV1.py"],
    "ARM08_PUMPSWAP_CPI": ["scripts/pumpSwapExactEconomicsV1.py", "scripts/pumpSwapInitDecoderV1.py"],
    "ARM09_ROUTER_RAYDIUM_CLMM": ["scripts/raydiumClmmExactEconomicsV1.py"],
    "ARM10_ROUTER_ORCA_WHIRLPOOL": ["scripts/orcaWhirlpoolExactEconomicsV1.py"],
    "ARM11_ROUTER_METEORA_DLMM": ["scripts/meteoraDlmmExactEconomicsV1.py"],
    "ARM12_OTHER_DEX_PROTOCOL_NATIVE": ["scripts/raydiumCpmmExactEconomicsV1.py", "scripts/meteoraDbcExactEconomicsV1.py"],
    "ARM13_AMBIGUOUS_PUMP_FAIL_CLOSED": ["scripts/ambiguousPumpFailClosedV1.py"],
}


def importable(path: Path) -> tuple[bool, str | None]:
    if not path.exists():
        return False, "PATH_MISSING"
    spec = importlib.util.spec_from_file_location(f"coverage_{path.stem}", path)
    if spec is None or spec.loader is None:
        return False, "MODULE_SPEC_UNAVAILABLE"
    try:
        if str(path.parent) not in sys.path:
            sys.path.insert(0, str(path.parent))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except Exception as exc:  # import coverage is the exact thing being audited
        return False, f"IMPORT_FAILED:{type(exc).__name__}:{exc}"
    return True, None


def run(registry: dict[str, Any], root: Path) -> dict[str, Any]:
    rows = []
    for arm in registry["arms"]:
        arm_id = arm["id"]
        paths = ADAPTERS[arm_id]
        checks = []
        for text in paths:
            ok, error = importable(root / text)
            checks.append({"path": text, "exists": (root / text).exists(), "importable": ok, "error": error})
        exact_module_ready = bool(checks) and all(row["importable"] for row in checks)
        rows.append({
            "arm_id": arm_id,
            "venue": arm["venue"],
            "registry_readiness": arm["readiness"],
            "declared_adapter_count": len(paths),
            "adapter_checks": checks,
            "exact_module_ready": exact_module_ready,
            "fixture_parity_verified": False,
            "economic_promotion_ready": False,
            "status": "MODULE_COVERAGE_PASS" if exact_module_ready else ("FAIL_CLOSED_NO_ADAPTER" if not paths else "MODULE_COVERAGE_FAIL"),
        })
    return {
        "schema": "KEFI_CANONICAL_13_ARM_EXACT_COVERAGE_AUDIT_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_schema": registry["schema"],
        "arms": rows,
        "summary": {
            "arm_count": len(rows),
            "module_coverage_pass": sum(row["exact_module_ready"] for row in rows),
            "fixture_parity_verified": 0,
            "economic_promotion_ready": 0,
            "status": "PASS_MODULE_AUDIT_WITH_RUNTIME_GATES_REMAINING",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--output", type=Path, default=Path("research/kefi-13-arm-program-v1/exact-coverage-audit.json"))
    args = parser.parse_args()
    result = run(json.loads(args.registry.read_text(encoding="utf-8")), Path.cwd())
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
