#!/usr/bin/env python3
"""Seal the outcome-blind independent kEFi comparison inputs and result."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--controls-manifest", required=True, type=Path)
    ap.add_argument("--comparison", required=True, type=Path)
    ap.add_argument("--selected", required=True, type=Path)
    ap.add_argument("--controls", required=True, type=Path)
    ap.add_argument("--control-replay", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ns = ap.parse_args()
    manifest = json.loads(ns.manifest.read_text(encoding="utf-8"))
    control_manifest = json.loads(ns.controls_manifest.read_text(encoding="utf-8"))
    comparison = json.loads(ns.comparison.read_text(encoding="utf-8"))
    receipt = {
        "schema": "KEFI_INDEPENDENT_COMPARISON_RECEIPT_V1",
        "status": "SEALED_DESCRIPTIVE_COMPARISON",
        "sealed_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "outcome_blind": True,
        "economic_outcomes_read": False,
        "pnl_read": False,
        "selected_sample": "sealed kEFi 1656-entry sample",
        "candidate_manifest": manifest,
        "control_manifest": control_manifest,
        "comparison_summary": {
            "selected_profile": comparison.get("selected_profile"),
            "independent_control_profile": comparison.get("independent_control_profile"),
            "selected_exit_profile": comparison.get("selected_exit_profile"),
            "control_exit_profile": comparison.get("control_exit_profile"),
        },
        "inputs_sha256": {
            "selected_entries": sha256(ns.selected),
            "matched_controls": sha256(ns.controls),
            "control_replay": sha256(ns.control_replay),
            "candidate_manifest": sha256(ns.manifest),
            "control_manifest": sha256(ns.controls_manifest),
            "comparison": sha256(ns.comparison),
        },
        "scientific_limitations": [
            "This is an outcome-blind descriptive context contrast, not a validated predictive fingerprint.",
            "Controls are matched by source partition and UTC hour and selected by deterministic hash rank.",
            "The 120-second best-exit statistic is hindsight-selected and is not live-tradable performance.",
            "No model, threshold, PnL claim, or execution claim is produced by this receipt.",
        ],
    }
    ns.output.parent.mkdir(parents=True, exist_ok=True)
    ns.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(ns.output), "sha256": sha256(ns.output), "status": receipt["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
