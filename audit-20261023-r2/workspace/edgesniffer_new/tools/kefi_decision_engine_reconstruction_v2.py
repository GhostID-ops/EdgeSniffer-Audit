#!/usr/bin/env python3
"""Outcome-blind kEFi decision-engine reconstruction from canonical wallet actions.

The input must contain directly attributable wallet actions. This tool never
turns generic market SELL events into wallet exits. It labels uncertain inventory
conservatively and emits only descriptive state/size timing fields.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            required = {"selected_mint", "pool", "signature", "event_index", "side", "received_at_unix_ns"}
            missing = required - row.keys()
            if missing:
                raise ValueError(f"{path}:{line_no}: missing {sorted(missing)}")
            rows.append(row)
    return sorted(rows, key=lambda r: (int(r["received_at_unix_ns"]), r["signature"], int(r["event_index"])))


def episode_class(delta_seconds: float | None) -> str:
    if delta_seconds is None:
        return "FIRST_OBSERVED_ENTRY"
    if delta_seconds <= 60:
        return "IMMEDIATE_ADD_CANDIDATE"
    if delta_seconds <= 900:
        return "STAGED_ADD_CANDIDATE"
    return "LATER_BUY_UNKNOWN_POSITION_STATE"


def reconstruct(rows: list[dict]) -> tuple[list[dict], dict]:
    by_pool: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_pool[row["pool"]].append(row)

    index = []
    counters = Counter()
    for pool, actions in by_pool.items():
        cumulative_observed_buys = 0
        cumulative_observed_sells = 0
        prior = None
        for action_no, row in enumerate(actions):
            received = int(row["received_at_unix_ns"])
            delta = None if prior is None else (received - int(prior["received_at_unix_ns"])) / 1e9
            side = row["side"]
            if side == "BUY":
                state = "FIRST_OBSERVED_ENTRY" if prior is None else "LATER_BUY_UNKNOWN_POSITION_STATE"
                # No direct exit ledger means observed prior buys do not prove current inventory.
                market_label = episode_class(delta)
                cumulative_observed_buys += int(row.get("user_quote_amount", row.get("pool_quote_delta", 0)) or 0)
            elif side == "SELL":
                state = "DIRECT_WALLET_SELL_OBSERVED"
                market_label = "WALLET_EXIT_ACTION"
                cumulative_observed_sells += int(row.get("user_quote_amount", row.get("pool_quote_delta", 0)) or 0)
            else:
                state = "NON_TRADE_WALLET_ACTION"
                market_label = "NON_TRADE_ACTION"

            pre_quote = row.get("pre_pool_quote")
            quote = row.get("user_quote_amount")
            index_row = {
                "pool": pool,
                "mint": row["selected_mint"],
                "signature": row["signature"],
                "event_index": row["event_index"],
                "wallet_action_side": side,
                "received_at_unix_ns": received,
                "slot": row.get("slot"),
                "source_frame_sha256": row.get("source_frame_sha256"),
                "wallet_state_class": state,
                "market_episode_proxy": market_label,
                "prior_observed_wallet_actions": action_no,
                "time_since_prior_wallet_action_seconds": delta,
                "cumulative_observed_buys_native": cumulative_observed_buys,
                "cumulative_observed_sells_native": cumulative_observed_sells,
                "inventory_state": "UNKNOWN_LEFT_CENSORED_NO_WALLET_EXIT_LEDGER",
                "transaction_quote_native": quote,
                "pre_pool_quote_native": pre_quote,
                "transaction_to_pre_pool_quote": (quote / pre_quote if quote and pre_quote else None),
            }
            index.append(index_row)
            counters[state] += 1
            prior = row

    summary = {
        "schema": "KEFI_DECISION_ENGINE_RECONSTRUCTION_V2_INDEX_V1",
        "input_actions": len(rows),
        "distinct_pools": len(by_pool),
        "direct_wallet_sells_observed": sum(1 for row in rows if row["side"] == "SELL"),
        "wallet_state_classes": dict(counters),
        "inventory_contract": "No true re-entry or confirmed flat classification without direct wallet-linked exit evidence.",
        "market_episode_contract": "Labels are wallet-action interval proxies only; public-market revival requires an independent pre-action full market stream.",
    }
    return index, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet-actions", type=Path, required=True)
    parser.add_argument("--out-index", type=Path, required=True)
    parser.add_argument("--out-summary", type=Path, required=True)
    args = parser.parse_args()

    index, summary = reconstruct(load_jsonl(args.wallet_actions))
    args.out_index.parent.mkdir(parents=True, exist_ok=True)
    with args.out_index.open("w", encoding="utf-8") as handle:
        for row in index:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    args.out_summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
