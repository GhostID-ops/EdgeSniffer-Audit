#!/usr/bin/env python3
"""Record receive-only exit intents without submitting transactions.

This is deliberately not submitted-exit telemetry.  It is the bridge between
the frozen paper policy and the future executor receipt schema.  Every row
states explicitly that no transaction was submitted or included.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any


SCHEMA = "SHADOW_EXIT_INTENT_TELEMETRY_V1"


def stable_id(anchor_id: str, policy_id: str) -> str:
    return hashlib.sha256(f"{policy_id}:{anchor_id}".encode()).hexdigest()


def read_jsonl(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def load_existing(path: Path) -> set[str]:
    return {
        str(row.get("intent_id"))
        for row in read_jsonl(path)
        if row.get("telemetrySchema") == SCHEMA and row.get("intent_id")
    }


def build_row(anchor: dict[str, Any], *, policy_id: str, entry_delay: int, horizon: int) -> dict[str, Any] | None:
    if anchor.get("record_type") != "ANCHOR_WINDOW":
        return None
    anchor_id = anchor.get("anchor_id")
    anchor_time = anchor.get("anchor_block_time")
    mint = anchor.get("mint")
    if not anchor_id or not mint or not isinstance(anchor_time, (int, float)):
        return None
    target = int(anchor_time) + entry_delay + horizon
    return {
        "telemetrySchema": SCHEMA,
        "schemaVersion": 1,
        "intent_id": stable_id(str(anchor_id), policy_id),
        "policy_id": policy_id,
        "execution_mode": "RECEIVE_ONLY_SHADOW",
        "wallet": anchor.get("wallet"),
        "route": anchor.get("route"),
        "anchor_id": anchor_id,
        "anchor_signature": anchor.get("anchor_signature"),
        "anchor_slot": anchor.get("anchor_slot"),
        "mint": mint,
        "entry_delay_seconds": entry_delay,
        "exit_horizon_seconds": horizon,
        "exit_target_block_time": target,
        "shadow_exit_intent_observed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "exit_submit_monotonic_ns": None,
        "exit_submit_signature": None,
        "exit_inclusion_slot": None,
        "exit_inclusion_transaction_index": None,
        "exit_result": "NOT_SUBMITTED_RECEIVE_ONLY",
        "actual_exit_submission_observed": False,
        "actual_exit_inclusion_observed": False,
        "evidence_boundary": "PAPER_INTENT_ONLY",
    }


def capture(source: Path, output: Path, *, policy_id: str, entry_delay: int, horizon: int) -> dict[str, int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = load_existing(output)
    scanned = emitted = skipped = 0
    with output.open("a", encoding="utf-8") as handle:
        for anchor in read_jsonl(source):
            scanned += 1
            row = build_row(anchor, policy_id=policy_id, entry_delay=entry_delay, horizon=horizon)
            if row is None or row["intent_id"] in existing:
                skipped += 1
                continue
            handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            existing.add(row["intent_id"])
            emitted += 1
    return {"anchors_scanned": scanned, "rows_emitted": emitted, "rows_skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy-id", required=True)
    parser.add_argument("--entry-delay-seconds", type=int, default=1)
    parser.add_argument("--exit-horizon-seconds", type=int, default=5)
    parser.add_argument("--interval-seconds", type=int, default=300)
    args = parser.parse_args()
    while True:
        result = capture(
            args.source,
            args.output,
            policy_id=args.policy_id,
            entry_delay=args.entry_delay_seconds,
            horizon=args.exit_horizon_seconds,
        )
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.interval_seconds <= 0:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
