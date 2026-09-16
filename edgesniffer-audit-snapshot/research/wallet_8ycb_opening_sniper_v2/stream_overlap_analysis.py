#!/usr/bin/env python3
"""Bounded same-slot and follower audit over the retained native stream overlap."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


WALLET = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ"
INITIAL_VSOL = 30_000_000_000
INITIAL_VTOK = 1_073_000_000_000_000
FEE_BPS = 125
FOLLOW_SOL_LAMPORTS = 100_000_000
DELAYS_MS = [25, 50, 100, 250, 500, 1000]


def read_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_gz(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def iso_ms(value: int | None) -> str | None:
    if value is None:
        return None
    return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def instruction_index(row: dict[str, Any]) -> int:
    raw = row.get("raw_record") or {}
    return int(raw.get("instructionIndex") or 0)


def order_key(row: dict[str, Any]) -> tuple[int, int, int, int]:
    canonical = row.get("_canonical_tx_index")
    if canonical is not None:
        return (
            int(row.get("source_slot") or 0),
            0,
            int(canonical),
            instruction_index(row),
        )
    return (
        int(row.get("source_slot") or 0),
        1,
        int(row.get("local_receive_ts_ms") or 0),
        instruction_index(row),
    )


def buy_tokens(vsol: int, vtok: int, gross_lamports: int) -> int:
    if gross_lamports <= 0:
        return 0
    post = (vsol * vtok) // (vsol + gross_lamports)
    return max(0, vtok - post)


def apply_buy(vsol: int, vtok: int, gross_lamports: int) -> tuple[int, int, int]:
    tokens = buy_tokens(vsol, vtok, gross_lamports)
    reserve_quote = gross_lamports - (gross_lamports * FEE_BPS // 10_000)
    return vsol + reserve_quote, vtok - tokens, tokens


def sell_value(vsol: int, vtok: int, tokens: int) -> int:
    if tokens <= 0:
        return 0
    post_sol = (vsol * vtok) // (vtok + tokens)
    gross = max(0, vsol - post_sol)
    return gross - gross * FEE_BPS // 10_000


def apply_sell(vsol: int, vtok: int, tokens: int) -> tuple[int, int]:
    if tokens <= 0:
        return vsol, vtok
    return (vsol * vtok) // (vtok + tokens), vtok + tokens


def apply_event(state: tuple[int, int], row: dict[str, Any]) -> tuple[int, int]:
    vsol, vtok = state
    if row.get("side") == "buy" and row.get("quote_lamports") is not None:
        vsol, vtok, _ = apply_buy(vsol, vtok, int(row["quote_lamports"]))
    elif row.get("side") == "sell" and row.get("sell_token_amount_raw") is not None:
        vsol, vtok = apply_sell(vsol, vtok, int(row["sell_token_amount_raw"]))
    return vsol, vtok


def roi_pct(value_lamports: int, spend_lamports: int) -> float:
    return 100.0 * (value_lamports / spend_lamports - 1.0)


def simulate(
    mint: str,
    target: dict[str, Any],
    rows: list[dict[str, Any]],
    delay_ms: int,
    worse_factor: float,
) -> dict[str, Any] | None:
    decision = int(target["local_receive_ts_ms"]) + delay_ms
    state = (INITIAL_VSOL, INITIAL_VTOK)
    future = []
    for row in rows:
        if row.get("side") not in ("buy", "sell"):
            continue
        if int(row["local_receive_ts_ms"]) <= decision:
            state = apply_event(state, row)
        else:
            future.append(row)
    vsol, vtok = state
    vsol, vtok, tokens = apply_buy(vsol, vtok, FOLLOW_SOL_LAMPORTS)
    adjusted_tokens = max(1, int(tokens / worse_factor))
    entry_price = (FOLLOW_SOL_LAMPORTS / 1e9) / (adjusted_tokens / 1e6)
    start_value = sell_value(vsol, vtok, adjusted_tokens)
    start_roi = roi_pct(start_value, FOLLOW_SOL_LAMPORTS)
    mfe = start_roi
    mae = start_roi
    exit_reason = "TIMEOUT_120S"
    exit_ts = int(target["local_receive_ts_ms"]) + 120_000
    exit_roi = start_roi
    observations = 0
    for row in future:
        ts = int(row["local_receive_ts_ms"])
        if ts > int(target["local_receive_ts_ms"]) + 120_000:
            break
        vsol, vtok = apply_event((vsol, vtok), row)
        value = sell_value(vsol, vtok, adjusted_tokens)
        current_roi = roi_pct(value, FOLLOW_SOL_LAMPORTS)
        mfe = max(mfe, current_roi)
        mae = min(mae, current_roi)
        exit_roi = current_roi
        exit_ts = ts
        observations += 1
        if current_roi >= 20.0:
            exit_reason = "TP20"
            break
        if current_roi <= -20.0:
            exit_reason = "SL20"
            break
    return {
        "mint": mint,
        "target_signature": target.get("transaction_signature"),
        "target_ts_ms": target.get("local_receive_ts_ms"),
        "target_at": iso_ms(target.get("local_receive_ts_ms")),
        "delay_ms": delay_ms,
        "entry_worse_factor": worse_factor,
        "follower_principal_sol": FOLLOW_SOL_LAMPORTS / 1e9,
        "entry_tokens_raw": adjusted_tokens,
        "entry_average_price_sol_per_token": entry_price,
        "start_roundtrip_roi_pct": start_roi,
        "mfe_roi_pct": mfe,
        "mae_roi_pct": mae,
        "exit_roi_pct": exit_roi,
        "exit_reason": exit_reason,
        "exit_ts_ms": exit_ts,
        "exit_at": iso_ms(exit_ts),
        "future_observations_used": observations,
        "first_sellable_adjusted_result": None,
        "first_sellable_status": "unavailable_no_account_ready_telemetry",
        "economics_scope": "native_stream_curve_only_pump_fee_no_network_or_tip_cost",
    }


def economics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["exit_roi_pct"]) for row in rows]
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return {
        "n": len(rows),
        "wr": sum(value > 0 for value in values) / len(values) if values else None,
        "tp_rate": sum(row["exit_reason"] == "TP20" for row in rows) / len(rows) if rows else None,
        "sl_rate": sum(row["exit_reason"] == "SL20" for row in rows) / len(rows) if rows else None,
        "profit_factor_on_roi": gains / losses if losses else None,
        "expectancy_roi_pct": statistics.mean(values) if values else None,
        "median_roi_pct": statistics.median(values) if values else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    args = parser.parse_args()
    base = Path(args.dir)
    launches = read_gz(base / "target_launches.jsonl.gz")
    launches_by_mint = {row["mint"]: row for row in launches}
    snapshot = json.loads((base / "stream_snapshot.json").read_text())
    with gzip.open(base / "canonical_block_signature_order.json.gz", "rt") as handle:
        block_order = json.load(handle)
    canonical_index = {
        (int(slot), signature): index
        for slot, block in block_order.items()
        for index, signature in enumerate(block.get("signatures") or [])
    }
    observations = []
    with (base / "target_stream_observations.jsonl").open() as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                row["_canonical_tx_index"] = canonical_index.get((int(row.get("source_slot") or 0), row.get("transaction_signature")))
                observations.append(row)
    by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        by_mint[row["mint"]].append(row)
    for rows in by_mint.values():
        rows.sort(key=order_key)

    participants = []
    target_outcomes = []
    follower = []
    completeness = Counter()
    for mint, launch in launches_by_mint.items():
        if int(launch["create_block_time"]) * 1000 < int(snapshot["first_observation_ts_ms"]):
            continue
        rows = by_mint.get(mint, [])
        creates = [row for row in rows if row.get("side") == "create" and row.get("transaction_signature") == launch["create_signature"]]
        targets = [
            row for row in rows
            if row.get("side") == "buy"
            and row.get("wallet") == WALLET
            and row.get("transaction_signature") == launch["create_signature"]
        ]
        if not creates:
            completeness["missing_create"] += 1
            continue
        if not targets:
            create = creates[0]
            target = {
                "side": "buy",
                "wallet": WALLET,
                "transaction_signature": launch["create_signature"],
                "source_slot": create.get("source_slot"),
                "source_transaction_index": create.get("source_transaction_index"),
                "local_receive_ts_ms": create.get("local_receive_ts_ms"),
                "quote_lamports": int(round(float(launch["initial_buy_principal_sol"]) * 1_000_000_000)),
                "raw_record": {"instructionIndex": 65535, "syntheticFromNativeCreateLedger": True},
                "synthetic_initial_buy": True,
                "_canonical_tx_index": canonical_index.get((int(create.get("source_slot") or 0), launch["create_signature"])),
            }
            completeness["synthetic_target_initial_buy_from_native_ledger"] += 1
        else:
            target = targets[0]
            completeness["direct_target_buy_observation"] += 1
        completeness["complete_identity"] += 1
        rows_with_target = list(rows)
        if target.get("synthetic_initial_buy"):
            rows_with_target.append(target)
            rows_with_target.sort(key=order_key)
        target_order = order_key(target)
        same_slot = [row for row in rows_with_target if int(row.get("source_slot") or -1) == int(target.get("source_slot") or -2) and row.get("side") in ("buy", "sell")]
        before_wallets = set()
        after_wallets = set()
        ambiguous_wallets = set()
        for row in same_slot:
            if row.get("transaction_signature") == target.get("transaction_signature") and row.get("wallet") == WALLET:
                continue
            target_canonical = target.get("_canonical_tx_index")
            participant_canonical = row.get("_canonical_tx_index")
            if target_canonical is None or participant_canonical is None:
                relation = "AMBIGUOUS_SAME_SLOT"
            else:
                relation = "BEFORE_8YCB" if int(participant_canonical) < int(target_canonical) else "AFTER_8YCB"
            wallet = row.get("wallet")
            if wallet:
                if relation == "BEFORE_8YCB":
                    before_wallets.add(wallet)
                elif relation == "AFTER_8YCB":
                    after_wallets.add(wallet)
                else:
                    ambiguous_wallets.add(wallet)
            participants.append({
                "mint": mint,
                "target_signature": target.get("transaction_signature"),
                "target_slot": target.get("source_slot"),
                "relation": relation,
                "participant_wallet": wallet,
                "side": row.get("side"),
                "quote_sol": row.get("quote_sol"),
                "participant_signature": row.get("transaction_signature"),
                "participant_transaction_index": row.get("source_transaction_index"),
                "participant_instruction_index": instruction_index(row),
                "target_canonical_transaction_index": target_canonical,
                "participant_canonical_transaction_index": participant_canonical,
            })

        tape = [
            row for row in rows_with_target
            if row.get("side") in ("buy", "sell")
            and int(row["local_receive_ts_ms"]) >= int(target["local_receive_ts_ms"])
            and int(row["local_receive_ts_ms"]) <= int(target["local_receive_ts_ms"]) + 120_000
        ]
        # Marginal curve path for descriptive target-tape MFE/MAE.
        state = (INITIAL_VSOL, INITIAL_VTOK)
        target_tokens = None
        target_spend = int(target.get("quote_lamports") or 0)
        path = []
        for row in rows_with_target:
            if row.get("side") not in ("buy", "sell"):
                continue
            if target_tokens and int(row["local_receive_ts_ms"]) > int(target["local_receive_ts_ms"]) + 120_000:
                break
            pre = state
            state = apply_event(state, row)
            if row is target:
                target_tokens = buy_tokens(pre[0], pre[1], target_spend)
            if target_tokens and int(row["local_receive_ts_ms"]) >= int(target["local_receive_ts_ms"]):
                path.append(roi_pct(sell_value(state[0], state[1], target_tokens), target_spend))
        target_outcomes.append({
            "mint": mint,
            "target_signature": target.get("transaction_signature"),
            "target_ts_ms": target.get("local_receive_ts_ms"),
            "target_slot": target.get("source_slot"),
            "target_transaction_index": target.get("source_transaction_index"),
            "target_instruction_index": instruction_index(target),
            "create_to_buy_local_ms": int(target["local_receive_ts_ms"]) - int(creates[0]["local_receive_ts_ms"]),
            "same_slot_before_wallet_count": len(before_wallets),
            "same_slot_after_wallet_count": len(after_wallets),
            "same_slot_ambiguous_wallet_count": len(ambiguous_wallets),
            "tape_buy_count_120s": sum(row.get("side") == "buy" for row in tape),
            "tape_sell_count_120s": sum(row.get("side") == "sell" for row in tape),
            "marginal_mfe_roi_pct_120s": max(path) if path else None,
            "marginal_mae_roi_pct_120s": min(path) if path else None,
            "scope": "retained_native_stream_overlap",
        })
        for delay in DELAYS_MS:
            for worse in [1.0, 1.05]:
                result = simulate(mint, target, rows_with_target, delay, worse)
                if result:
                    follower.append(result)

    write_gz(base / "same_slot_participants.jsonl.gz", participants)
    write_gz(base / "stream_target_outcomes.jsonl.gz", target_outcomes)
    write_gz(base / "follower_latency_outcomes.jsonl.gz", follower)

    repeated_before = Counter(row["participant_wallet"] for row in participants if row["relation"] == "BEFORE_8YCB" and row.get("participant_wallet"))
    repeated_after = Counter(row["participant_wallet"] for row in participants if row["relation"] == "AFTER_8YCB" and row.get("participant_wallet"))
    by_boundary = {}
    for delay in DELAYS_MS:
        for worse in [1.0, 1.05]:
            key = f"delay_{delay}ms" + ("_entry_5pct_worse" if worse > 1 else "")
            by_boundary[key] = economics([row for row in follower if row["delay_ms"] == delay and row["entry_worse_factor"] == worse])
    summary = {
        "source_snapshot": snapshot,
        "extracted_observations": len(observations),
        "extracted_mints": len(by_mint),
        "positive_launches_in_overlap_by_block_time": sum(int(row["create_block_time"]) * 1000 >= int(snapshot["first_observation_ts_ms"]) for row in launches),
        "completeness": dict(completeness),
        "target_outcomes": len(target_outcomes),
        "same_slot_participant_rows": len(participants),
        "before_relation_rows": sum(row["relation"] == "BEFORE_8YCB" for row in participants),
        "after_relation_rows": sum(row["relation"] == "AFTER_8YCB" for row in participants),
        "ambiguous_same_slot_rows": sum(row["relation"] == "AMBIGUOUS_SAME_SLOT" for row in participants),
        "canonical_block_order_slots_available": sum(bool(row.get("signatures")) for row in block_order.values()),
        "canonical_block_order_slots_missing": sum(not row.get("signatures") for row in block_order.values()),
        "repeated_before_wallets_top20": repeated_before.most_common(20),
        "repeated_after_wallets_top20": repeated_after.most_common(20),
        "follower_curve_only_economics": by_boundary,
        "limitations": [
            "Retained stream is under seven days and does not satisfy the requested complete-period minimum.",
            "Completeness is established only for rows present in the source feed; routed inner-instruction coverage is not independently proven.",
            "Follower simulation uses 0.1 SOL, Pump curve plus 125 bps trade fees, and excludes network/tip/account-ready costs.",
            "First-sellable-adjusted outcomes are unavailable because account-ready telemetry was not retained.",
        ],
    }
    (base / "stream_overlap_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
