#!/usr/bin/env python3
"""Select time-nearest generic launch controls from the frozen stream universe."""

from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import json
from pathlib import Path


def read_gz(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v2", required=True)
    parser.add_argument("--v3", required=True)
    parser.add_argument("--per-target", type=int, default=5)
    args = parser.parse_args()
    v2 = Path(args.v2)
    v3 = Path(args.v3)
    target_rows = read_gz(v2 / "stream_target_outcomes.jsonl.gz")
    target_mints = {row["mint"] for row in target_rows}

    creates = []
    with (v3 / "all_launch_stream_120s.tsv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row["side"] == "create":
                creates.append({
                    "create_ts_ms": int(row["ts_ms"]),
                    "create_slot": int(row["slot"]),
                    "create_transaction_index": int(row["tx_index"]),
                    "create_signature": row["signature"],
                    "mint": row["mint"],
                })
    creates.sort(key=lambda row: (row["create_ts_ms"], row["create_signature"], row["mint"]))
    times = [row["create_ts_ms"] for row in creates]
    used: set[str] = set()
    selected = []
    for target in sorted(target_rows, key=lambda row: (int(row["target_ts_ms"]), row["mint"])):
        target_ts = int(target["target_ts_ms"])
        pivot = bisect.bisect_left(times, target_ts)
        candidates = []
        radius = 0
        while len(candidates) < args.per_target and radius < len(creates):
            radius += 1
            for index in (pivot - radius, pivot + radius - 1):
                if index < 0 or index >= len(creates):
                    continue
                candidate = creates[index]
                if candidate["mint"] in target_mints or candidate["create_signature"] in used:
                    continue
                candidates.append(candidate)
                used.add(candidate["create_signature"])
                if len(candidates) >= args.per_target:
                    break
        for rank, candidate in enumerate(sorted(candidates, key=lambda row: abs(row["create_ts_ms"] - target_ts)), 1):
            selected.append({
                **candidate,
                "matched_target_mint": target["mint"],
                "matched_target_signature": target["target_signature"],
                "matched_target_ts_ms": target_ts,
                "absolute_time_difference_ms": abs(candidate["create_ts_ms"] - target_ts),
                "signed_time_difference_ms": candidate["create_ts_ms"] - target_ts,
                "within_target_stream_capture": True,
                "match_rank": rank,
            })

    with gzip.open(v3 / "generic_control_selection.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in selected:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    (v3 / "generic_control_signatures.txt").write_text(
        "".join(row["create_signature"] + "\n" for row in selected), encoding="utf-8"
    )
    summary = {
        "frozen_stream_create_rows": len(creates),
        "target_launches_with_complete_stream_identity": len(target_rows),
        "controls_per_target_requested": args.per_target,
        "selected_unique_generic_controls": len(selected),
        "maximum_absolute_time_difference_ms": max((row["absolute_time_difference_ms"] for row in selected), default=None),
        "median_absolute_time_difference_ms": sorted(row["absolute_time_difference_ms"] for row in selected)[len(selected) // 2] if selected else None,
    }
    (v3 / "generic_control_selection_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
