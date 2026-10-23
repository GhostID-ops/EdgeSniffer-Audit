#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--directory", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--expected", nargs="+", required=True)
    ns = ap.parse_args()
    reports = []
    missing = []
    unsupported = set()
    for name in ns.expected:
        path = ns.directory / f"{name}.summary.json"
        if not path.exists():
            missing.append(name)
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        reports.append(report)
        unsupported.update(report.get("unsupported_target_signatures", []))
    merged = {
        "schema": "KEFI_PUMPSWAP_120S_REPLAY_SHARDED_V1",
        "status": "PASS" if not missing else "INCOMPLETE",
        "targets": reports[0].get("targets") if reports else 0,
        "source_frames_scanned": sum(r.get("source_frames_scanned", 0) for r in reports),
        "protobuf_transactions_decoded": sum(r.get("protobuf_transactions_decoded", 0) for r in reports),
        "unsupported_replay_events": sum(r.get("unsupported_replay_events", 0) for r in reports),
        "unsupported_target_signatures": sorted(unsupported),
        "missing_shards": missing,
        "shards": [
            {
                "partition": name,
                "summary_sha256": __import__("hashlib").sha256((ns.directory / f"{name}.summary.json").read_bytes()).hexdigest(),
            }
            for name in ns.expected if (ns.directory / f"{name}.summary.json").exists()
        ],
        "outcome_blind": True,
    }
    ns.output.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(merged, sort_keys=True))
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
