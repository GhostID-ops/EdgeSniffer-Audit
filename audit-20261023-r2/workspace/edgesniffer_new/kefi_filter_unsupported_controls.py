#!/usr/bin/env python3
"""Remove only controls whose replay summary marked an unsupported event."""

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
    ap.add_argument("--controls", required=True, type=Path)
    ap.add_argument("--summary", required=True, type=Path)
    ap.add_argument("--replay", required=True, type=Path)
    ap.add_argument("--controls-out", required=True, type=Path)
    ap.add_argument("--replay-out", required=True, type=Path)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--invalid-report", type=Path)
    ns = ap.parse_args()
    summary = json.loads(ns.summary.read_text(encoding="utf-8"))
    unsupported = set(summary.get("unsupported_target_signatures", []))
    if ns.invalid_report and ns.invalid_report.exists():
        invalid_report = json.loads(ns.invalid_report.read_text(encoding="utf-8"))
        unsupported.update(invalid_report.get("invalid_target_signatures", []))
    allowed: set[str] = set()
    controls_in = 0
    controls_out = 0
    ns.controls_out.parent.mkdir(parents=True, exist_ok=True)
    with ns.controls.open(encoding="utf-8") as source, ns.controls_out.open("w", encoding="utf-8") as target:
        for line in source:
            if not line.strip():
                continue
            row = json.loads(line)
            controls_in += 1
            if row.get("signature") in unsupported:
                continue
            allowed.add(row["signature"])
            target.write(line)
            controls_out += 1
    replay_in = 0
    replay_out = 0
    with ns.replay.open(encoding="utf-8") as source, ns.replay_out.open("w", encoding="utf-8") as target:
        for line in source:
            if not line.strip():
                continue
            replay_in += 1
            row = json.loads(line)
            if row.get("target_signature") not in allowed:
                continue
            target.write(line)
            replay_out += 1
    manifest = {
        "schema": "KEFI_CONTROL_FAIL_CLOSED_FILTER_V1",
        "unsupported_target_count": len(unsupported),
        "unsupported_target_signatures": sorted(unsupported),
        "controls_in": controls_in,
        "controls_out": controls_out,
        "replay_rows_in": replay_in,
        "replay_rows_out": replay_out,
        "controls_sha256": sha256(ns.controls),
        "replay_sha256": sha256(ns.replay),
        "outcome_blind": True,
        "rule": "exclude any control window with an unsupported decoded replay event; never impute it",
    }
    ns.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
