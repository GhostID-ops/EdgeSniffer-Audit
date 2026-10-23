#!/usr/bin/env python3
"""Freeze a completed compact wallet snapshot and audit choice-set provenance.

This tool is outcome-blind. It never opens PRE_H3 artifacts and refuses to
label teacher-window-derived market rows as a contemporaneous choice universe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def freeze_ledger_prefix(source: Path, expected_sha256: str, destination: Path) -> int:
    """Copy the exact append-only prefix whose bytes match a completed hash."""
    digest = hashlib.sha256()
    count = 0
    found = False
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with source.open("rb") as incoming, temporary.open("wb") as outgoing:
        for line in incoming:
            outgoing.write(line)
            digest.update(line)
            count += 1
            if digest.hexdigest() == expected_sha256:
                outgoing.flush()
                os.fsync(outgoing.fileno())
                found = True
                break
    if not found:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("FAIL_CLOSED: completed ledger hash is not an append-only prefix of current ledger")
    # Windows cannot replace a destination while its temporary source handle is
    # still open; publication therefore occurs after both streams close.
    os.replace(temporary, destination)
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--ledger-sha256", required=True)
    parser.add_argument("--wallet-state", type=Path, required=True)
    parser.add_argument("--wallet-state-sha256", required=True)
    parser.add_argument("--source-health", type=Path, required=True)
    parser.add_argument("--market-checkpoint", type=Path, required=True)
    parser.add_argument("--market-state", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    args = parser.parse_args()

    if sha256_file(args.wallet_state) != args.wallet_state_sha256:
        raise RuntimeError("FAIL_CLOSED: wallet-state hash does not match completed reconstruction")
    out = args.out_root
    out.mkdir(parents=True, exist_ok=False)
    frozen_ledger = out / "wallet-actions.jsonl"
    action_rows = freeze_ledger_prefix(args.ledger, args.ledger_sha256, frozen_ledger)
    ledger_rows = [json.loads(line) for line in frozen_ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    block_times = [int(row["block_time"]) for row in ledger_rows if row.get("block_time") is not None]
    frozen_state = out / "wallet-state-index.jsonl"
    frozen_state.write_bytes(args.wallet_state.read_bytes())
    health = json.loads(args.source_health.read_text(encoding="utf-8"))
    checkpoint = json.loads(args.market_checkpoint.read_text(encoding="utf-8"))
    market_state = json.loads(args.market_state.read_text(encoding="utf-8"))

    # The materializer lists promotion-window roots, proving it is selected
    # centred. It can be outcome-blind yet still fail choice-set independence.
    processed = market_state.get("processed", [])
    result = {
        "schema": "KEFI_INITIAL_SELECTION_CHOICESET_AUDIT_SNAPSHOT_V1",
        "wallet_ledger": {
            "path": str(frozen_ledger), "sha256": sha256_file(frozen_ledger), "rows": action_rows,
            "earliest_block_time": min(block_times) if block_times else None,
            "latest_block_time": max(block_times) if block_times else None,
            "usable_direct_wallet_actions": len(ledger_rows),
        },
        "wallet_state": {"path": str(frozen_state), "sha256": sha256_file(frozen_state)},
        "capture": {
            "source_instance": health.get("sourceInstanceId"),
            "continuity_epoch": health.get("currentEpoch"),
            "reconnects": health.get("reconnects"),
            "receive_errors": health.get("receiveErrors"),
            "continuity_gaps": health.get("continuityGaps"),
            "latest_received_at_utc": health.get("latestReceivedAtUtc"),
        },
        "market_input": {
            "checkpoint_schema": checkpoint.get("schema"),
            "deduplicated_market_rows": checkpoint.get("deduplicated_market_rows"),
            "processed_promotion_windows": len(processed),
            "outcome_blind": checkpoint.get("outcome_blind"),
            "choice_set_status": "INVALID_SELECTED_CENTRED",
            "reason": "Market rows were materialized from tracked-wallet promotion windows, not from all mechanically eligible pools at each decision time.",
        },
        "outcomes_or_pnl_read": False,
        "pre_h3_accessed": False,
        "verdict": "CHOICESET_RECONSTRUCTION_NOT_YET_VALID",
    }
    atomic_json(out / "snapshot-audit.json", result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
