#!/usr/bin/env python3
"""Seal a compact provenance receipt for a rich pre-entry feasibility result."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reconstruction", type=Path, required=True)
    ap.add_argument("--result", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    reconstruction = json.loads(args.reconstruction.read_text())
    result = json.loads(args.result.read_text())
    if reconstruction.get("status") != "PASS_OUTCOME_BLIND_DESCRIPTIVE" or result.get("status") != "EXPLORATORY_DESCRIPTIVE_NOT_VALIDATED":
        raise SystemExit("FAIL_CLOSED: unexpected input status")
    receipt = {
        "schema": "KEFI_RICH_PREENTRY_FEASIBILITY_RECEIPT_V1",
        "sealed_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "SEALED_EXPLORATORY_PREENTRY_FEASIBILITY",
        "strict_preentry": True,
        "outcomes_accessed": False,
        "pnl_accessed": False,
        "reconstruction_sha256": sha(args.reconstruction),
        "result_sha256": sha(args.result),
        "dataset_sha256": result["inputs"]["dataset_sha256"],
        "chronological_holdout": result["chronological_holdout"],
        "scientific_limit": "Historical discovery only. A separately predeclared fresh prospective shadow remains required before any prediction, paper-execution, profitability, or live-execution claim.",
    }
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "sha256": sha(args.output)}, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
