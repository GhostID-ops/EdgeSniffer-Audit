#!/usr/bin/env python3
"""Whole-window, outcome-blind pre-H3 mechanics certification.

This is an engineering gate only.  It neither selects an H3 hypothesis nor
opens a cohort.  It proves that every materialized PumpSwap row has a stable
source order and is either mechanically tradeable or explicitly rejected.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MECHANICS_PATH = ROOT / "kefi_h3_pumpswap_mechanics_v1.py"
spec = importlib.util.spec_from_file_location("mechanics", MECHANICS_PATH)
assert spec and spec.loader
mechanics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mechanics)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(canonical(value)); handle.flush()
    temporary.replace(path)


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rows = load(args.rows)
    if not rows:
        raise RuntimeError("FAIL_CLOSED: no materialized rows")
    ordered = mechanics.sorted_unique(rows)
    if len(ordered) != len(rows):
        raise RuntimeError("FAIL_CLOSED: source order cardinality changed")
    non_tradeable = Counter()
    tradeable = 0
    exact = 0
    source_instances = set()
    epochs = set()
    for row in ordered:
        source_instances.add(str(row.get("source_instance_id", "")))
        epochs.add(int(row["continuity_epoch"]))
        result = mechanics.tradeable(row)
        if result["tradeable"]:
            tradeable += 1
            # Force pre-state reconstruction rather than merely trusting the flag.
            mechanics.pre_state(row)
        else:
            non_tradeable[str(result["reason"])] += 1
        if row.get("exact_transition_validated") is True:
            exact += 1
    if not source_instances or "" in source_instances:
        raise RuntimeError("FAIL_CLOSED: missing source instance identity")
    receipt = {
        "schema": "KEFI_H3_MECHANICS_GATE_V1",
        "status": "PASS",
        "engineering_only": True,
        "h3_boundary_created": False,
        "rows_path": str(args.rows),
        "rows_sha256": hashlib.sha256(args.rows.read_bytes()).hexdigest(),
        "mechanics_source_sha256": hashlib.sha256(MECHANICS_PATH.read_bytes()).hexdigest(),
        "rows": len(rows),
        "ordered_rows": len(ordered),
        "exact_transition_validated_rows": exact,
        "tradeable_rows": tradeable,
        "non_tradeable_rows": sum(non_tradeable.values()),
        "non_tradeable_reasons": dict(sorted(non_tradeable.items())),
        "source_instances": sorted(source_instances),
        "continuity_epochs": sorted(epochs),
        "deterministic_tie_order": ["source_instance_id", "continuity_epoch", "durable_output_sequence", "event_index", "source_frame_sha256"],
        "pnl_accessed": False,
        "outcome_blind": True,
    }
    atomic(args.out, receipt)
    print(json.dumps({"status": "PASS", "receipt_sha256": digest(receipt)}, sort_keys=True))


if __name__ == "__main__":
    main()
