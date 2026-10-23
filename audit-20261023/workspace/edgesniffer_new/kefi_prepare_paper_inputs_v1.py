#!/usr/bin/env python3
"""Prepare outcome-blind kEFi gate inputs from canonical decoded BUY events.

The live journal is transaction-level metadata. This adapter only admits a
signature when the closed-partition decoder finds exactly one BUY event for it;
ambiguous layouts fail closed instead of guessing an event index.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def rows(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", type=Path, required=True)
    ap.add_argument("--decoded", type=Path, required=True)
    ap.add_argument("--fresh-candidates", type=Path, required=True)
    ap.add_argument("--replay-targets", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)
    ns = ap.parse_args()

    journal = list(rows(ns.journal))
    wanted = {r["signature"]: r for r in journal if r.get("rollingWindowWarmupComplete") is True}
    decoded = {}
    ambiguous = set()
    for r in rows(ns.decoded):
        sig = r.get("candidate_signature")
        if sig not in wanted:
            continue
        if sig in decoded:
            ambiguous.add(sig)
        else:
            decoded[sig] = r
    admitted = []
    for sig, r in sorted(decoded.items()):
        if sig in ambiguous:
            continue
        base = {
            "signature": sig,
            "event_index": int(r["event_index"]),
            "side": "BUY",
            "selected_mint": r["mint"],
            "pool": r["pool"],
            "source_partition": r["source_partition"],
            "received_at_unix_ns": int(r["received_at_unix_ns"]),
            "base_amount": int(r["base_amount"]),
            "pre_pool_base": int(r["pool_base_raw"]) + int(r["base_amount"]),
            "pre_pool_quote": int(r["pool_quote_raw"]) - int(r["pool_quote_delta"]),
            "pool_quote_delta": int(r["pool_quote_delta"]),
            "user_quote_amount": int(r["user_quote_amount"]),
            "amm_fee_bps": int(r["lp_fee_bps"]) + int(r["protocol_fee_bps"]) + int(r["coin_creator_bps"]),
        }
        admitted.append((base, r))

    ns.fresh_candidates.parent.mkdir(parents=True, exist_ok=True)
    ns.replay_targets.parent.mkdir(parents=True, exist_ok=True)
    with ns.fresh_candidates.open("w", encoding="utf-8") as f, ns.replay_targets.open("w", encoding="utf-8") as t:
        for base, r in admitted:
            f.write(json.dumps(base, sort_keys=True) + "\n")
            target = {
                "source_partition": r["source_partition"],
                "source_frame_sha256": r["source_frame_sha256"],
                "received_at_unix_ns": base["received_at_unix_ns"],
                "slot": int(r["slot"]),
                "signature": base["signature"],
                "event_index": base["event_index"],
                "selected_mint": base["selected_mint"],
                "pool": base["pool"],
                "event_timestamp": int(r.get("event_timestamp") or 0),
                "pre_pool_base": base["pre_pool_base"],
                "pre_pool_quote": base["pre_pool_quote"],
                "base_amount": base["base_amount"],
                "pool_quote_delta": base["pool_quote_delta"],
                "lp_fee_bps": int(r["lp_fee_bps"]),
                "lp_fee": int(r["lp_fee"]),
                "protocol_fee_bps": int(r["protocol_fee_bps"]),
                "protocol_fee": int(r["protocol_fee"]),
                "user_quote_amount": base["user_quote_amount"],
                "virtual_quote_reserves": int(r["virtual_quote_reserves"]),
                "coin_creator_bps": int(r["coin_creator_bps"]),
                "coin_creator_fee": int(r["coin_creator_fee"]),
                "cashback_bps": int(r["cashback_bps"]),
                "cashback": int(r["cashback"]),
                "buyback_bps": int(r["buyback_bps"]),
                "buyback_fee": int(r["buyback_fee"]),
            }
            t.write(json.dumps(target, sort_keys=True) + "\n")
    summary = {
        "schema": "KEFI_PROSPECTIVE_PAPER_INPUTS_V1",
        "journal_rows": len(journal),
        "warmup_eligible_journal_rows": len(wanted),
        "decoded_rows_for_journal": len(decoded),
        "ambiguous_signatures_fail_closed": len(ambiguous),
        "admitted_rows": len(admitted),
        "outcome_blind": True,
        "selection_rule": "exactly one decoded BUY event per journal signature",
    }
    ns.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
