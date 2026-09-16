#!/usr/bin/env python3
"""Extract empirical Rust safe-first-sellable timing from immutable dust archives."""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import tarfile
from pathlib import Path


KINDS = {
    "rustSelectedCandidateLandedSafeExitActivated",
    "rustSelectedCandidateProvisionalFirstSellable",
}


def quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v3", required=True)
    parser.add_argument("archives", nargs="+")
    args = parser.parse_args()
    base = Path(args.v3)
    events: dict[tuple, dict] = {}
    for archive_name in args.archives:
        archive = Path(archive_name)
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                if not member.isfile() or not member.name.endswith((".jsonl", ".log")):
                    continue
                extracted = bundle.extractfile(member)
                if extracted is None:
                    continue
                for raw in extracted:
                    try:
                        row = json.loads(raw)
                    except Exception:
                        continue
                    if row.get("kind") not in KINDS or row.get("rustAcceptedToFirstSellableMs") is None:
                        continue
                    if row["kind"] == "rustSelectedCandidateLandedSafeExitActivated" and not row.get("activationAllowed"):
                        continue
                    row = dict(row)
                    row["source_archive"] = str(archive)
                    row["source_member"] = member.name
                    key = (row.get("signature"), row.get("kind"), row.get("atMs"), row.get("rustAcceptedToFirstSellableMs"))
                    events.setdefault(key, row)

    by_trade: dict[str, dict] = {}
    for row in events.values():
        identity = row.get("signature") or row.get("key") or f"{row.get('mint')}:{row.get('atMs')}"
        current = by_trade.get(identity)
        if current is None or float(row["rustAcceptedToFirstSellableMs"]) < float(current["rustAcceptedToFirstSellableMs"]):
            by_trade[identity] = row
    rows = sorted(by_trade.values(), key=lambda row: (int(row.get("atMs") or 0), row.get("signature") or ""))
    values = [float(row["rustAcceptedToFirstSellableMs"]) for row in rows]
    with gzip.open(base / "first_sellable_telemetry.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    summary = {
        "unique_safe_first_sellable_trades": len(rows),
        "p50_rust_accepted_to_first_sellable_ms": quantile(values, 0.50),
        "p75_rust_accepted_to_first_sellable_ms": quantile(values, 0.75),
        "p90_rust_accepted_to_first_sellable_ms": quantile(values, 0.90),
        "minimum_ms": min(values) if values else None,
        "maximum_ms": max(values) if values else None,
        "mean_ms": statistics.mean(values) if values else None,
        "proof_kind_counts": {
            kind: sum(row.get("kind") == kind for row in rows) for kind in sorted(KINDS)
        },
        "archives": args.archives,
        "scope_note": "Empirical Rust dust-execution telemetry from separate historical launches; it is a latency distribution for our execution path, not 8YCB-specific execution evidence.",
    }
    (base / "first_sellable_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
