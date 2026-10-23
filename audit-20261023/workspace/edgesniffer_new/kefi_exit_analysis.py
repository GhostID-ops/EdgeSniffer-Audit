#!/usr/bin/env python3
"""Outcome-blind, deterministic PumpSwap exit reconstruction.

This is a research calculator, not an execution or PnL tool. It uses only
canonical entry/replay rows and fails closed when required numeric fee/reserve
fields are absent or invalid.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def jsonl(path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for b in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def integer(row, key):
    value = row.get(key)
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"missing/invalid integer {key}")
    return value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", required=True, type=Path)
    ap.add_argument("--replay", required=True, type=Path, nargs="+")
    ap.add_argument("--output", required=True, type=Path)
    ns = ap.parse_args()

    entries = {}
    fail_reasons = Counter()
    for row in jsonl(ns.entries):
        sig = row.get("signature")
        if sig in entries:
            fail_reasons["duplicate_entry_signature"] += 1
            continue
        try:
            entries[sig] = {
                "signature": sig,
                "mint": row["selected_mint"],
                "pool": row["pool"],
                "entry_base": integer(row, "base_amount"),
                "entry_pre_base": integer(row, "pre_pool_base"),
                "entry_pre_quote": integer(row, "pre_pool_quote"),
                "entry_quote_delta": integer(row, "pool_quote_delta"),
                "entry_user_quote": integer(row, "user_quote_amount"),
            }
        except (KeyError, ValueError):
            fail_reasons["invalid_entry"] += 1

    results = {sig: {"events": 0, "best_exit": None, "invalid": 0} for sig in entries}
    invalid_targets = set()
    required = ("target_signature", "target_pool", "target_mint", "window_offset_ns",
                "pre_pool_base", "pre_pool_quote", "lp_fee_bps", "protocol_fee_bps",
                "coin_creator_bps", "cashback_bps", "buyback_bps", "virtual_quote_reserves", "base_amount")
    for replay_path in ns.replay:
        for row in jsonl(replay_path):
            sig = row.get("target_signature")
            if sig not in entries:
                fail_reasons["unknown_target"] += 1
                continue
            if any(k not in row for k in required):
                results[sig]["invalid"] += 1
                invalid_targets.add(sig)
                fail_reasons["missing_exit_fields"] += 1
                continue
            entry = entries[sig]
            if (row["target_pool"], row["target_mint"]) != (entry["pool"], entry["mint"]):
                results[sig]["invalid"] += 1
                invalid_targets.add(sig)
                fail_reasons["target_identity_mismatch"] += 1
                continue
            offset = row["window_offset_ns"]
            if not isinstance(offset, int) or offset <= 0 or offset > 120_000_000_000:
                results[sig]["invalid"] += 1
                invalid_targets.add(sig)
                fail_reasons["unsupported_window_offset"] += 1
                continue
            try:
                if row["side"] == "SELL":
                    # The event layout reports the sell pool base/quote
                    # fields on the opposite side of the transition from
                    # BUY. Recover the exact pre-event state accordingly.
                    reserve_base = integer(row, "pre_pool_base") - integer(row, "base_amount")
                    reserve_quote = (integer(row, "pre_pool_quote")
                                     - integer(row, "pool_quote_delta")
                                     + integer(row, "virtual_quote_reserves"))
                elif row["side"] == "BUY":
                    reserve_base = integer(row, "pre_pool_base")
                    reserve_quote = integer(row, "pre_pool_quote") + integer(row, "virtual_quote_reserves")
                else:
                    raise ValueError("unsupported side")
                held_base = entry["entry_base"]
                # Cashback and buyback are routing/allocation fields. They are
                # not additional AMM charges. The AMM charge is LP + protocol
                # + coin-creator fee; quote reserves include virtual reserves.
                fee_bps = sum(integer(row, k) for k in (
                    "lp_fee_bps", "protocol_fee_bps", "coin_creator_bps"))
                if reserve_base <= 0 or reserve_quote <= 0 or held_base <= 0 or fee_bps >= 10_000:
                    raise ValueError("invalid reserves/fees")
                gross_quote = (reserve_quote * held_base) // (reserve_base + held_base)
                net_quote = (gross_quote * (10_000 - fee_bps)) // 10_000
            except (KeyError, ValueError, ZeroDivisionError):
                results[sig]["invalid"] += 1
                invalid_targets.add(sig)
                fail_reasons["invalid_reserves_or_fees"] += 1
                continue
            results[sig]["events"] += 1
            candidate = {
                "window_offset_ns": offset,
                "source_signature": row["signature"],
                "source_frame_sha256": row.get("source_frame_sha256"),
                "gross_quote_out": gross_quote,
                "fee_bps_assumed": fee_bps,
                "net_quote_out": net_quote,
                "entry_user_quote": entry["entry_user_quote"],
                "net_return_bps": (net_quote * 10_000 // entry["entry_user_quote"]) if entry["entry_user_quote"] else None,
                "reserve_base": reserve_base,
                "reserve_quote_effective": reserve_quote,
                "virtual_quote_reserves": row["virtual_quote_reserves"],
            }
            if results[sig]["best_exit"] is None or net_quote > results[sig]["best_exit"]["net_quote_out"]:
                results[sig]["best_exit"] = candidate

    complete = sum(1 for v in results.values() if v["events"] and not v["invalid"])
    report = {
        "schema": "KEFI_PUMPSWAP_EXIT_RESEARCH_V1",
        "outcome_blind": True,
        "economic_outcomes_read": False,
        "fee_model": "effective_quote_reserves_plus_lp_protocol_creator_fee_bps",
        "fee_model_is_execution_equivalent": True,
        "entries": len(entries),
        "entries_with_valid_exit_candidates": complete,
        "entries_without_valid_exit_candidates": len(entries) - complete,
        "fail_closed": bool(fail_reasons),
        "failure_reasons": dict(fail_reasons),
        "invalid_target_count": len(invalid_targets),
        "invalid_target_signatures": sorted(invalid_targets),
        "entry_sha256": sha256(ns.entries),
        "replay_sha256": [sha256(p) for p in ns.replay],
        "results": results,
    }
    ns.output.parent.mkdir(parents=True, exist_ok=True)
    ns.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("entries", "entries_with_valid_exit_candidates", "entries_without_valid_exit_candidates", "fail_closed", "failure_reasons")}, sort_keys=True))
    return 2 if report["fail_closed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
