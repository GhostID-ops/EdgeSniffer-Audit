#!/usr/bin/env python3
"""Emit the explicit Meteora DLMM no-admission economics surface."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

NOTIONALS = (0.1, 0.5, 1.0)
HORIZONS = (5, 10, 15, 30, 60, 120, 300)
STRESSES = (0, 25, 50)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def evaluate(source: Path) -> dict:
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    surface = [{
        "venue": "METEORA_DLMM", "notional_sol": notional, "stress_bps": stress,
        "horizon_seconds": horizon, "entry_delay_seconds": 1,
        "status": "BLOCKED_NO_NATIVE_DLMM_BUY_ORIENTATION",
        "pnl_sol": None, "roi": None, "sample_size": 0,
        "controls_attached": False,
        "reason": "No canonical wallet buy is admitted; DLMM Swap2 event orientation/bin state is not sufficient to treat incidental venue accounts as a buy.",
    } for notional in NOTIONALS for stress in STRESSES for horizon in HORIZONS]
    return {
        "schema": "WALLET_METEORA_DLMM_CAUSAL_ECONOMICS_SURFACE_V1",
        "venue": "METEORA_DLMM", "read_only": True, "entry_delay_seconds": 1,
        "notionals_sol": list(NOTIONALS), "horizons_seconds": list(HORIZONS), "stress_bps": list(STRESSES),
        "windows_loaded": 0, "market_transactions_loaded": 0, "surface": surface,
        "controls_attached": False, "policy_frozen": False, "trading_enabled": False,
        "promotion_eligible": False, "blocker": "NO_CANONICAL_METEORA_DLMM_BUY",
        "source_sha256": sha256_file(source),
    }


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--source", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); result = evaluate(args.source); args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "METEORA_DLMM_CAUSAL_SURFACE_COMPLETE", "windows_loaded": 0, "blocked_rows": len(result["surface"])}, sort_keys=True))


if __name__ == "__main__":
    main()
