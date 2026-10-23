#!/usr/bin/env python3
"""Exact fixed-notional economics for the sealed PumpSwap selection cohort.

This consumes the already sealed snapshot-bound actionability assignments.  It
does not search for a best exit: the exit is the first valid observed state at
or after the fixed horizon.  It uses integer PumpSwap arithmetic only and
fails closed on unsupported fee layouts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path


SCHEMA = "KEFI_FROZEN_PUMPSWAP_SELECTION_ECONOMICS_V1"
WSOL = "So11111111111111111111111111111111111111112"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def row_key(row: dict) -> tuple[str, int, int, int, str]:
    return (
        str(row["source_instance_id"]),
        int(row["continuity_epoch"]),
        int(row["durable_output_sequence"]),
        int(row.get("event_index", row.get("log_index"))),
        str(row["source_frame_sha256"]),
    )


def ceil_fee(amount: int, bps: int) -> int:
    if amount < 0 or bps < 0:
        raise ValueError("negative fee input")
    return (amount * bps + 9_999) // 10_000 if amount and bps else 0


def fee_bps(row: dict) -> tuple[int, int, int]:
    if any(int(row.get(name, 0)) != 0 for name in ("cashback_bps", "cashback_raw")):
        raise ValueError("UNSUPPORTED_CASHBACK_FEE_LAYOUT")
    values = tuple(int(row[name]) for name in ("lp_fee_bps", "protocol_fee_bps", "coin_creator_bps"))
    if any(value < 0 for value in values) or sum(values) >= 10_000:
        raise ValueError("INVALID_PUMPSWAP_FEE_LAYOUT")
    return values


def effective_quote(state: dict, row: dict) -> int:
    raw = int(state["pool_quote_raw"])
    virtual = int(row["virtual_quote_reserves_raw"])
    value = raw + virtual
    if raw < 0 or value <= 0:
        raise ValueError("INVALID_EFFECTIVE_QUOTE_RESERVE")
    return value


def quote_buy_exact_quote_in(gross_quote_in: int, state: dict, row: dict) -> dict:
    if gross_quote_in <= 0:
        raise ValueError("NONPOSITIVE_PAPER_NOTIONAL")
    lp, protocol, creator = fee_bps(row)
    total = lp + protocol + creator
    charged = gross_quote_in * 10_000 // (10_000 + total)
    fees = (ceil_fee(charged, lp), ceil_fee(charged, protocol), ceil_fee(charged, creator))
    fee_total = sum(fees)
    net = gross_quote_in - fee_total
    curve_in = net - 1
    base_reserve = int(state["pool_base_raw"])
    quote_reserve = effective_quote(state, row)
    if base_reserve <= 0 or curve_in <= 0:
        raise ValueError("INVALID_BUY_RESERVES_OR_FEE_RESULT")
    token_out = base_reserve * curve_in // (quote_reserve + curve_in)
    if token_out <= 0 or token_out >= base_reserve:
        raise ValueError("INVALID_BUY_TOKEN_OUTPUT")
    return {
        "gross_quote_in": gross_quote_in,
        "charged_fee_base": charged,
        "lp_fee": fees[0],
        "protocol_fee": fees[1],
        "creator_fee": fees[2],
        "fee_total": fee_total,
        "curve_quote_in": curve_in,
        "token_out": token_out,
        "pre_pool_base": base_reserve,
        "effective_quote_reserve": quote_reserve,
    }


def quote_sell_exact_base(token_in: int, state: dict, row: dict) -> dict:
    if token_in <= 0:
        raise ValueError("NONPOSITIVE_TOKEN_POSITION")
    lp, protocol, creator = fee_bps(row)
    base_reserve = int(state["pool_base_raw"])
    quote_reserve = effective_quote(state, row)
    if base_reserve <= 0 or token_in >= base_reserve:
        raise ValueError("INVALID_SELL_RESERVES_OR_POSITION")
    gross = quote_reserve * token_in // (base_reserve + token_in)
    fees = (ceil_fee(gross, lp), ceil_fee(gross, protocol), ceil_fee(gross, creator))
    net = gross - sum(fees)
    if net < 0:
        raise ValueError("SELL_FEE_EXCEEDS_GROSS_OUTPUT")
    return {
        "token_in": token_in,
        "gross_quote_out": gross,
        "lp_fee": fees[0],
        "protocol_fee": fees[1],
        "creator_fee": fees[2],
        "fee_total": sum(fees),
        "net_quote_out": net,
        "pre_pool_base": base_reserve,
        "effective_quote_reserve": quote_reserve,
    }


def read_tape(root: Path) -> tuple[list[dict], str]:
    chunks = sorted((root / "chunks").glob("*.jsonl"))
    checkpoint = root / "consumer-checkpoint.json"
    if not chunks or not checkpoint.is_file():
        raise RuntimeError("FAIL_CLOSED: incomplete PumpSwap tape")
    rows: list[dict] = []
    for path in chunks:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    if row.get("quote_mint") != WSOL or row.get("exact_transition_validated") is not True:
                        raise RuntimeError("FAIL_CLOSED: unsupported row in PumpSwap tape")
                    rows.append(row)
    rows.sort(key=lambda row: (int(row["received_at_unix_ns"]), row_key(row)))
    return rows, sha256(checkpoint)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assignments", type=Path, required=True)
    parser.add_argument("--tape", type=Path, required=True)
    parser.add_argument("--quote-in-lamports", type=int, default=100_000_000)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit("FAIL_CLOSED: economics output already exists")
    if args.quote_in_lamports <= 0:
        raise SystemExit("FAIL_CLOSED: quote notional must be positive")

    assignments = json.loads(args.assignments.read_text(encoding="utf-8"))
    if assignments.get("venue") != "pumpswap" or assignments.get("pnl_accessed") is not False:
        raise SystemExit("FAIL_CLOSED: assignments are not the sealed PumpSwap outcome-blind output")
    rows, checkpoint_hash = read_tape(args.tape)
    by_key = {row_key(row): row for row in rows}
    if len(by_key) != len(rows):
        raise SystemExit("FAIL_CLOSED: duplicate PumpSwap transition key")

    results = []
    failures = Counter()
    for assignment in assignments.get("assignments", []):
        result = {
            "mint": assignment.get("mint"),
            "pool": assignment.get("pool"),
            "target_status": assignment.get("target_status"),
            "target_id": assignment.get("target_id"),
            "status": "NO_ECONOMICS",
            "reason": "",
        }
        if assignment.get("target_status") != "MARK_AVAILABLE":
            result["status"] = "NO_ENTRY" if assignment.get("target_status") == "NO_ENTRY" else "NON_ACTIONABLE"
            result["reason"] = assignment.get("target_reason", "TARGET_NOT_MARK_AVAILABLE")
            results.append(result)
            continue
        try:
            entry_key = tuple(assignment["target_entry"]["entry_event_key"])
            exit_key = tuple(assignment["target_exit"]["mark_event_key"])
            entry_row = by_key[entry_key]
            exit_row = by_key[exit_key]
            if entry_row["mint"] != assignment["mint"] or entry_row["pool"] != assignment["pool"]:
                raise ValueError("ENTRY_IDENTITY_MISMATCH")
            if exit_row["mint"] != assignment["mint"] or exit_row["pool"] != assignment["pool"]:
                raise ValueError("EXIT_IDENTITY_MISMATCH")
            entry_quote = quote_buy_exact_quote_in(
                args.quote_in_lamports, assignment["target_entry"]["pre_state"], entry_row
            )
            exit_quote = quote_sell_exact_base(
                entry_quote["token_out"], assignment["target_exit"]["pre_state"], exit_row
            )
            pnl = exit_quote["net_quote_out"] - args.quote_in_lamports
            result.update({
                "status": "ECONOMICS_AVAILABLE",
                "entry_event_key": list(entry_key),
                "exit_event_key": list(exit_key),
                "entry_observation_side": entry_row["side"],
                "exit_observation_side": exit_row["side"],
                "entry_received_at_unix_ns": int(entry_row["received_at_unix_ns"]),
                "exit_received_at_unix_ns": int(exit_row["received_at_unix_ns"]),
                "entry": entry_quote,
                "exit": exit_quote,
                "pnl_quote_lamports": pnl,
                "roi_bps": pnl * 10_000 / args.quote_in_lamports,
            })
        except (KeyError, TypeError, ValueError) as error:
            result["status"] = "ECONOMICS_UNAVAILABLE"
            result["reason"] = str(error)
            failures[str(error)] += 1
        results.append(result)

    status_counts = Counter(result["status"] for result in results)
    economic = [result for result in results if result["status"] == "ECONOMICS_AVAILABLE"]
    pnl_values = [result["pnl_quote_lamports"] for result in economic]
    payload = {
        "schema": SCHEMA,
        "venue": "PUMPSWAP",
        "quote_mint": WSOL,
        "paper_quote_in_lamports": args.quote_in_lamports,
        "paper_quote_in_sol": args.quote_in_lamports / 1_000_000_000,
        "entry_rule": "first_tradeable_same_epoch_PumpSwap_state_at_or_after_target_plus_1s",
        "exit_rule": "first_tradeable_same_epoch_observed_state_at_or_after_entry_plus_120s",
        "cashflow_model": "integer_effective_quote_reserve_constant_product_with_separate_ceil_lp_protocol_creator_fees",
        "unsupported_layouts": ["nonzero_cashback_bps_or_cashback_raw"],
        "assignment_sha256": sha256(args.assignments),
        "tape_checkpoint_sha256": checkpoint_hash,
        "pnl_accessed": True,
        "status_counts": dict(status_counts),
        "economic_sample": len(economic),
        "total_pnl_quote_lamports": sum(pnl_values),
        "total_pnl_sol": sum(pnl_values) / 1_000_000_000,
        "positive_count": sum(value > 0 for value in pnl_values),
        "negative_count": sum(value < 0 for value in pnl_values),
        "zero_count": sum(value == 0 for value in pnl_values),
        "profit_factor": (
            sum(value for value in pnl_values if value > 0)
            / -sum(value for value in pnl_values if value < 0)
            if any(value < 0 for value in pnl_values)
            else None
        ),
        "failure_reasons": dict(failures),
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, args.out)
    print(json.dumps({"status": "ECONOMICS_SEALED", "status_counts": dict(status_counts), "output_sha256": sha256(args.out)}, sort_keys=True))


if __name__ == "__main__":
    main()
