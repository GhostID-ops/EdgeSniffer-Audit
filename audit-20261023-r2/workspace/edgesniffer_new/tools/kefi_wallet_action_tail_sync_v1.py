#!/usr/bin/env python3
"""Close the RPC-to-live-stream history handoff before a new observer starts.

This is an operational provenance tool. It only fetches finalized direct wallet
actions, never public market outcomes, feature values, rankings, or PnL.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path

from kefi_wallet_action_index_v1 import (
    action_index_lock,
    atomic_json,
    canonical,
    load_seen,
    read_endpoint,
    records_for_transaction,
    rpc,
    utc_now,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--rpc-env", type=Path, required=True)
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--replay-overlap-slots", type=int, default=32)
    parser.add_argument("--max-slot-drift", type=int, default=32)
    parser.add_argument("--max-workers", type=int, default=16)
    parser.add_argument("--launch-env-output", type=Path)
    args = parser.parse_args()
    if args.replay_overlap_slots < 1 or args.max_slot_drift < args.replay_overlap_slots or not 1 <= args.max_workers <= 32:
        raise SystemExit("bounded replay overlap, drift, and worker count required")
    if args.receipt.exists():
        raise SystemExit("FAIL_CLOSED: tail-sync receipt path already exists")

    actions_path = args.index_root / "wallet-actions.jsonl"
    if not actions_path.is_file():
        raise SystemExit("FAIL_CLOSED: canonical wallet action index missing")
    endpoint = read_endpoint(args.rpc_env)
    with action_index_lock(args.index_root):
        start_slot = int(rpc(endpoint, "getSlot", [{"commitment": "finalized"}]))
        prior_rows = [json.loads(line) for line in actions_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        known_ids = {row["action_id"] for row in prior_rows}
        known_time = max((int(row.get("block_time") or 0) for row in prior_rows), default=0)
        signature_rows = rpc(endpoint, "getSignaturesForAddress", [args.wallet, {"limit": 500, "commitment": "finalized"}])
        needed = [row for row in signature_rows if int(row.get("blockTime") or 0) >= known_time]

        def fetch(row: dict) -> tuple[dict, object]:
            return row, rpc(endpoint, "getTransaction", [row["signature"], {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 1}])

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            fetched = list(executor.map(fetch, needed))
        additions: list[dict] = []
        for row, transaction in fetched:
            if transaction:
                additions.extend(record for record in records_for_transaction(args.wallet, row, transaction) if record["action_id"] not in known_ids)
        additions.sort(key=lambda row: (int(row.get("block_time") or 0), int(row.get("slot") or 0), row["signature"], row["mint"], row["action_id"]))
        if additions:
            with actions_path.open("a", encoding="utf-8") as handle:
                for record in additions:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        end_slot = int(rpc(endpoint, "getSlot", [{"commitment": "finalized"}]))
        if end_slot - start_slot > args.max_slot_drift:
            raise SystemExit(f"FAIL_CLOSED: tail sync crossed {end_slot - start_slot} slots; overlap is only {args.replay_overlap_slots}")
        result = {
            "schema": "KEFI_WALLET_ACTION_TAIL_SYNC_V1",
            "wallet": args.wallet,
            "started_finalized_slot": start_slot,
            "completed_finalized_slot": end_slot,
            "replay_from_slot": max(0, start_slot - args.replay_overlap_slots),
            "replay_overlap_slots": args.replay_overlap_slots,
            "actions_added": len(additions),
            "actions_sha256": sha256_file(actions_path),
            "source_signature_rows": len(signature_rows),
            "queried_signature_rows": len(needed),
            "outcomes_or_pnl_accessed": False,
            "generated_at_utc": utc_now(),
        }
        atomic_json(args.receipt, result)
        if args.launch_env_output:
            if args.launch_env_output.exists():
                raise SystemExit("FAIL_CLOSED: launch environment path already exists")
            args.launch_env_output.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.launch_env_output.with_suffix(args.launch_env_output.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(f"KEFI_SOURCE_REPLAY_FROM_SLOT={result['replay_from_slot']}\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o640)
            os.replace(temporary, args.launch_env_output)
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
