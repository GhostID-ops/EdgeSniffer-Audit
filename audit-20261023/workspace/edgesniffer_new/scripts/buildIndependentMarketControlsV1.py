#!/usr/bin/env python3
"""Build a deterministic, non-overlapping same-period control sample.

The independent lab's market-wide windows are already admitted without a
teacher-wallet trigger.  This script creates a separate control tape from the
same windows, pairing nearby observations in the same UTC five-minute bucket
and route.  Pairing uses only signal time, route, mint, and signature; it does
not inspect PnL, exits, or any future outcome.

The controls are a contemporaneous market baseline, not a causal treatment
control.  They are sufficient for discovery diagnostics, but promotion remains
blocked until native state and prospective validation gates pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


BUCKET_MS = 5 * 60 * 1000


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def timestamp(row: dict[str, Any]) -> int | None:
    for key in ("signal_receive_ts_ms", "receive_ts_ms", "local_receive_ts_ms"):
        value = row.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def signature(row: dict[str, Any]) -> str:
    return str(row.get("signal_signature") or row.get("signature") or "")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(input_path: Path, output_path: Path, report_path: Path) -> dict[str, Any]:
    rows = [
        row for row in load_rows(input_path)
        if row.get("record_type") in {"INDEPENDENT_MARKET_WINDOW", "MARKET_WINDOW"}
        and timestamp(row) is not None
        and row.get("route")
    ]
    buckets: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        ts = timestamp(row)
        assert ts is not None
        buckets[(str(row["route"]), ts // BUCKET_MS)].append(row)

    controls: list[dict[str, Any]] = []
    pair_count = 0
    for (route, bucket), members in sorted(buckets.items()):
        members.sort(key=lambda row: (timestamp(row) or 0, str(row.get("mint") or ""), signature(row)))
        used_mints: set[str] = set()
        for index in range(0, len(members) - 1, 2):
            left, right = members[index], members[index + 1]
            if str(left.get("mint") or "") == str(right.get("mint") or ""):
                continue
            control = dict(right)
            control["record_type"] = "INDEPENDENT_MARKET_CONTROL_WINDOW"
            control["control_group"] = "SAME_PERIOD_MARKET_BASELINE_V1"
            control["control_match_bucket_ms"] = BUCKET_MS
            control["matched_candidate_signal_signature"] = signature(left)
            control["matched_candidate_mint"] = left.get("mint")
            control["control_selection"] = "adjacent_same_route_utc_5m_pair"
            controls.append(control)
            used_mints.add(str(right.get("mint") or ""))
            pair_count += 1

    controls.sort(key=lambda row: (timestamp(row) or 0, str(row.get("mint") or ""), signature(row)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in controls:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    temporary.replace(output_path)
    report = {
        "schema": "EDGESNIFFER_INDEPENDENT_MARKET_CONTROLS_V1",
        "read_only": True,
        "trading_enabled": False,
        "selection_uses_outcomes": False,
        "control_kind": "SAME_PERIOD_MARKET_BASELINE",
        "bucket_ms": BUCKET_MS,
        "source_windows": len(rows),
        "control_windows": len(controls),
        "matched_pairs": pair_count,
        "routes": sorted({str(row.get("route")) for row in controls}),
        "source_sha256": sha256(input_path),
        "output_sha256": sha256(output_path),
        "promotion_note": "Baseline controls support discovery diagnostics; they do not authorize freeze or promotion.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.input, args.output, args.report), sort_keys=True))


if __name__ == "__main__":
    main()
