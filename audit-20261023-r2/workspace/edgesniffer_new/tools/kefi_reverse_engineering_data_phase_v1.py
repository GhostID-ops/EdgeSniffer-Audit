#!/usr/bin/env python3
"""Outcome-blind compact kEFi state and abstention-evidence reconstruction.

The tool accepts direct wallet actions plus, optionally, a *proven independent*
chronological public-event stream.  It refuses to call selected-centred data an
abstention universe.  It never computes PnL, exits, actionability, or any
PRE_H3 evaluator result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def action_order(row: dict) -> tuple[int, int, str, str]:
    return (int(row.get("slot") or 0), int(row.get("event_index") or 0), str(row.get("signature") or ""), str(row.get("action_id") or ""))


def reconstruct_wallet(rows: list[dict]) -> tuple[list[dict], dict]:
    by_mint: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("mint"):
            by_mint[row["mint"]].append(row)
    emitted: list[dict] = []
    counts: Counter[str] = Counter()
    for mint, actions in sorted(by_mint.items()):
        known_flat = False
        prior_slot: int | None = None
        prior_actions = 0
        buys = sells = 0
        for row in sorted(actions, key=action_order):
            side, before, after = row.get("side"), row.get("pre_wallet_token_raw"), row.get("post_wallet_token_raw")
            if side == "BUY":
                state = "TRUE_REENTRY_AFTER_CONFIRMED_FLAT" if known_flat else ("FIRST_OBSERVED_ENTRY" if prior_actions == 0 else "LATER_BUY_UNKNOWN_POSITION_STATE")
                known_flat = False
                buys += 1
            elif side == "SELL":
                state = "CONFIRMED_FLAT_EXIT" if after == 0 and before not in (None, 0) else "PARTIAL_EXIT"
                known_flat = state == "CONFIRMED_FLAT_EXIT"
                sells += 1
            elif side in {"TRANSFER_OUT", "TRANSFER_IN"}:
                state = "UNKNOWN_EXIT_STATE" if side == "TRANSFER_OUT" else "TRANSFER_UNCERTAINTY"
                known_flat = False
            else:
                state = "UNKNOWN_WALLET_ACTION"
            slot = int(row.get("slot") or 0)
            emitted.append({
                "schema": "KEFI_WALLET_STATE_INDEX_V2",
                "mint": mint,
                "signature": row.get("signature"),
                "slot": slot,
                "action_id": row.get("action_id"),
                "direct_action_side": side,
                "wallet_state_class": state,
                "prior_observed_wallet_actions": prior_actions,
                "time_since_prior_wallet_action_slots": slot - prior_slot if prior_slot is not None else None,
                "cumulative_observed_buys": buys,
                "cumulative_observed_sells": sells,
                "pre_wallet_token_raw": before,
                "post_wallet_token_raw": after,
                "source": row.get("provenance", {}).get("source"),
            })
            counts[state] += 1
            prior_actions += 1
            prior_slot = slot
    return emitted, {"distinct_mints": len(by_mint), "state_counts": dict(sorted(counts.items())), "actions": len(emitted)}


def build_candidate_universe(rows: list[dict], bucket_seconds: int) -> list[dict]:
    buckets: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        timestamp = row.get("received_at_unix_ns")
        pool = row.get("pool")
        if timestamp is None or not pool:
            continue
        buckets[(pool, int(timestamp) // 1_000_000_000 // bucket_seconds)].append(row)
    output = []
    for (pool, bucket), values in sorted(buckets.items()):
        values.sort(key=lambda row: (int(row["received_at_unix_ns"]), str(row.get("candidate_signature", "")), int(row.get("event_index", 0))))
        first, last = values[0], values[-1]
        buys = sum(1 for row in values if row.get("side") == "BUY")
        sells = sum(1 for row in values if row.get("side") == "SELL")
        output.append({
            "schema": "KEFI_INDEPENDENT_PUBLIC_CANDIDATE_V1",
            "candidate_id": hashlib.sha256(canonical({"pool": pool, "bucket": bucket})).hexdigest(),
            "pool": pool,
            "mint": first.get("mint"),
            "bucket_seconds": bucket_seconds,
            "bucket_start_unix": bucket * bucket_seconds,
            "first_public_received_at_unix_ns": first["received_at_unix_ns"],
            "last_public_received_at_unix_ns": last["received_at_unix_ns"],
            "source_instance": first.get("source_instance"),
            "continuity_epoch": first.get("continuity_epoch"),
            "event_count": len(values),
            "buy_count": buys,
            "sell_count": sells,
            "buy_quote_native": sum(int(row.get("user_quote_amount") or 0) for row in values if row.get("side") == "BUY"),
            "sell_quote_native": sum(int(row.get("user_quote_amount") or 0) for row in values if row.get("side") == "SELL"),
            "latest_pool_quote_raw": last.get("pool_quote_raw"),
            "latest_virtual_quote_reserves": last.get("virtual_quote_reserves"),
            "fee_tuple": [last.get("lp_fee_bps"), last.get("protocol_fee_bps"), last.get("coin_creator_bps"), last.get("buyback_bps"), last.get("cashback_bps")],
            "definition": "pre-action public state only; future teacher action is not used to admit this candidate",
        })
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet-actions", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--independent-public-events", type=Path)
    parser.add_argument("--independent-public-manifest", type=Path)
    parser.add_argument("--bucket-seconds", type=int, default=20)
    args = parser.parse_args()
    if args.bucket_seconds != 20:
        raise SystemExit("the V1 compact candidate universe is frozen to 20-second buckets")
    root = args.out_root
    root.mkdir(parents=True, exist_ok=True)
    wallet_rows = read_jsonl(args.wallet_actions)
    wallet_index, wallet_summary = reconstruct_wallet(wallet_rows)
    wallet_out = root / "wallet-state-index.jsonl"
    with wallet_out.open("w", encoding="utf-8") as handle:
        for row in wallet_index:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    candidate_status: dict[str, object] = {"status": "NOT_AVAILABLE", "reason": "No independent public-event manifest supplied; selected-centred data is explicitly rejected."}
    if args.independent_public_events or args.independent_public_manifest:
        if not (args.independent_public_events and args.independent_public_manifest):
            raise SystemExit("public events and manifest must be supplied together")
        manifest = json.loads(args.independent_public_manifest.read_text(encoding="utf-8"))
        if manifest.get("independent_candidate_universe") is not True or manifest.get("uses_future_outcomes") is not False:
            raise SystemExit("FAIL_CLOSED: public stream is not proven independent and outcome-blind")
        candidate_rows = build_candidate_universe(read_jsonl(args.independent_public_events), args.bucket_seconds)
        candidate_path = root / "independent-candidates.jsonl"
        with candidate_path.open("w", encoding="utf-8") as handle:
            for row in candidate_rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        candidate_status = {"status": "BUILT", "count": len(candidate_rows), "path": str(candidate_path), "sha256": digest_file(candidate_path), "manifest_sha256": digest_file(args.independent_public_manifest)}

    summary = {
        "schema": "KEFI_REVERSE_ENGINEERING_DATA_PHASE_V1",
        "wallet_action_input_sha256": digest_file(args.wallet_actions),
        "wallet_state_index_sha256": digest_file(wallet_out),
        "wallet_state": wallet_summary,
        "candidate_universe": candidate_status,
        "prohibitions": {"pnl": "not_read", "outcomes": "not_read", "pre_h3": "not_read_or_modified", "model_fit": False},
    }
    write_json(root / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
