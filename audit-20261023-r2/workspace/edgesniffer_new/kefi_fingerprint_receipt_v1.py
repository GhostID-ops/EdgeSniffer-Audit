#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, type=Path)
    ap.add_argument("--comparison", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ns = ap.parse_args()
    model = json.loads(ns.model.read_text(encoding="utf-8"))
    comparison = json.loads(ns.comparison.read_text(encoding="utf-8"))
    receipt = {
        "schema": "KEFI_ENTRY_FINGERPRINT_DISCOVERY_RECEIPT_V1",
        "status": "SEALED_EXPLORATORY_FINGERPRINT",
        "sealed_utc": datetime.now(timezone.utc).isoformat(),
        "outcome_blind": True,
        "economic_outcomes_read": False,
        "pnl_read": False,
        "model_sha256": sha256(ns.model),
        "comparison_sha256": sha256(ns.comparison),
        "unseen_temporal_metrics": model["unseen_temporal_metrics"],
        "fit_metrics": model["fit_metrics"],
        "features": model["features"],
        "features_not_available_yet": model["features_not_available_yet"],
        "scientific_status": "entry-selection imitation only; not profitability, autonomous WHEN, or execution validation",
        "next_gate": "prospective paper capture on later unseen kEFi events with the frozen entry-context model",
    }
    ns.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(ns.output), "sha256": sha256(ns.output), "status": receipt["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
