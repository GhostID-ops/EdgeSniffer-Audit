#!/usr/bin/env python3
"""Outcome sandbox for the StonkFun/LaunchLab sibling lane.

This is deliberately separate from the official SOL-Pump cohort.  It reads
only the sibling choice ledger and the canonical LaunchLab event database,
replays the causal pool state after each sibling decision, and reports
normalized quote-asset returns.  It never reads official outcome artifacts,
never submits, and fails closed when the route evidence is incomplete.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
from typing import Any


SCHEMA = "2CQ_STONKFUN_LAUNCHLAB_ECONOMICS_SANDBOX_V1"
ENTRY_BPS = 10  # 0.10% of effective quote reserve; comparable pressure, not SOL sizing.
TP = 0.20
SL = -0.20
MAX_HOLD_NS = 120 * 1_000_000_000


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def state(row: sqlite3.Row, column: str) -> dict[str, int]:
    value = json.loads(row[column])
    return {k: int(v) for k, v in value.items()}


def order_key(row: sqlite3.Row) -> tuple[int, int, int]:
    return int(row["slot"]), int(row["canonical_tx_index"]), int(row["event_index"])


def in_or_before(row: sqlite3.Row, slot: int, tx_index: int) -> bool:
    return (int(row["slot"]), int(row["canonical_tx_index"])) <= (slot, tx_index)


def fee_profile(row: sqlite3.Row | None, side: str) -> dict[str, float]:
    if row is None or int(row["curve_amount_in_raw"]) <= 0:
        return {"curveFeeRate": 0.0, "baseTransferFeeRate": 0.0, "quoteTransferFeeRate": 0.0}
    amount_in = int(row["curve_amount_in_raw"])
    amount_out = max(1, int(row["curve_amount_out_raw"]))
    curve_fee = int(row["curve_fee_raw"])
    base_transfer = int(row["base_transfer_fee_raw"] or 0)
    quote_transfer = int(row["quote_transfer_fee_raw"] or 0)
    curve_fee_denominator = amount_in if side == "BUY" else amount_out + curve_fee
    return {
        "curveFeeRate": curve_fee / max(1, curve_fee_denominator),
        "baseTransferFeeRate": max(0, base_transfer) / (amount_out if side == "BUY" else amount_in),
        # The LaunchLab evidence table does not contain a reliable native-SOL
        # wallet delta; its sell-side quote field can therefore equal the full
        # curve output.  Do not mislabel that as a 100% transfer fee.
        "quoteTransferFeeRate": 0.0,
    }


def buy_output(pre: dict[str, int], quote_in: int, profile: dict[str, float]) -> tuple[int, int]:
    x = pre["virtual_base"] - pre["real_base"]
    y = pre["virtual_quote"] + pre["real_quote"]
    fee = min(quote_in, max(0, int(quote_in * profile["curveFeeRate"])))
    net = quote_in - fee
    if x <= 0 or y <= 0 or net <= 0:
        return 0, 0
    curve_out = (x * net) // (y + net)
    received = max(0, int(curve_out * (1.0 - profile["baseTransferFeeRate"])))
    return curve_out, received


def sell_output(pre: dict[str, int], inventory: int, profile: dict[str, float]) -> int:
    x = pre["virtual_base"] - pre["real_base"]
    y = pre["virtual_quote"] + pre["real_quote"]
    curve_in = max(0, int(inventory * (1.0 - profile["baseTransferFeeRate"])))
    if x <= 0 or y <= 0 or curve_in <= 0:
        return 0
    gross = (y * curve_in) // (x + curve_in)
    fee = min(gross, max(0, int(gross * profile["curveFeeRate"])))
    received = gross - fee
    received = max(0, int(received * (1.0 - profile["quoteTransferFeeRate"])))
    return received


def selected_rows(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            choice = row.get("choice") or {}
            selected = [s for s in choice.get("states") or [] if s.get("label")]
            if len(selected) != 1:
                continue
            row["selectedState"] = selected[0]
            rows.append(row)
    return rows


def evaluate(row: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    chosen = row["selectedState"]
    boundary_slot = int(row["choice"]["boundarySlot"])
    boundary_tx = int(row["choice"]["boundaryTransactionIndex"])
    boundary_ns = int(row["boundaryUnixNs"])
    mint = str(row["mint"])
    pool = str(chosen["pool"])
    events = conn.execute(
        "SELECT * FROM events WHERE pool_state=? ORDER BY slot, canonical_tx_index, event_index",
        (pool,),
    ).fetchall()
    if not events:
        return {"mint": mint, "pool": pool, "status": "UNSUPPORTED", "reason": "NO_POOL_EVENTS"}

    prior = [e for e in events if in_or_before(e, boundary_slot, boundary_tx)]
    if not prior:
        return {"mint": mint, "pool": pool, "status": "UNSUPPORTED", "reason": "NO_CAUSAL_PRE_BOUNDARY_STATE"}
    anchor = prior[-1]
    pre = state(anchor, "post_state_json")
    prior_buys = [e for e in prior if str(e["side"]) == "BUY"]
    prior_sells = [e for e in prior if str(e["side"]) == "SELL"]
    if not prior_buys or not prior_sells:
        return {"mint": mint, "pool": pool, "status": "UNSUPPORTED", "reason": "MISSING_PRE_ENTRY_FEE_PROFILE"}
    buy_profile = fee_profile(prior_buys[-1], "BUY")
    sell_profile = fee_profile(prior_sells[-1], "SELL")
    reserve = pre["virtual_quote"] + pre["real_quote"]
    quote_in = max(1, reserve * ENTRY_BPS // 10_000)
    _, inventory = buy_output(pre, quote_in, buy_profile)
    if inventory <= 0:
        return {"mint": mint, "pool": pool, "status": "UNSUPPORTED", "reason": "HYPOTHETICAL_BUY_ZERO_OUTPUT"}

    future = [
        e for e in events
        if order_key(e) > order_key(anchor)
        and int(e["received_at_unix_ns"]) >= boundary_ns
    ]
    if not future:
        return {"mint": mint, "pool": pool, "status": "UNSUPPORTED", "reason": "NO_POST_ENTRY_ROUTE_EVENTS"}

    def mark(e: sqlite3.Row) -> tuple[int, float]:
        proceeds = sell_output(state(e, "post_state_json"), inventory, sell_profile)
        return proceeds, proceeds / quote_in - 1.0

    exit_event = None
    exit_reason = None
    exit_proceeds = None
    exit_roi = None
    for e in future:
        proceeds, roi = mark(e)
        if roi >= TP:
            exit_event, exit_reason, exit_proceeds, exit_roi = e, "TP20", proceeds, roi
            break
        if roi <= SL:
            exit_event, exit_reason, exit_proceeds, exit_roi = e, "SL20", proceeds, roi
            break
        if int(e["received_at_unix_ns"]) >= boundary_ns + MAX_HOLD_NS:
            exit_event, exit_reason, exit_proceeds, exit_roi = e, "MAX_HOLD_120S", proceeds, roi
            break
    if exit_event is None:
        return {
            "mint": mint,
            "pool": pool,
            "status": "UNSUPPORTED",
            "reason": "NO_EXIT_STATE_WITHIN_120S",
            "quoteInputRaw": quote_in,
            "inventoryRaw": inventory,
        }
    return {
        "mint": mint,
        "pool": pool,
        "signature": row.get("signature"),
        "boundarySlot": boundary_slot,
        "boundaryTransactionIndex": boundary_tx,
        "boundaryUnixNs": boundary_ns,
        "status": "EXACT_ROUTE_SANDBOX",
        "entrySizeMode": "EFFECTIVE_QUOTE_RESERVE_BPS",
        "entrySizeBps": ENTRY_BPS,
        "quoteInputRaw": quote_in,
        "inventoryRaw": inventory,
        "exitReason": exit_reason,
        "exitSignature": str(exit_event["signature"]),
        "exitSlot": int(exit_event["slot"]),
        "exitReceivedAtUnixNs": int(exit_event["received_at_unix_ns"]),
        "exitProceedsRaw": exit_proceeds,
        "normalizedReturn": exit_roi,
        "normalizedReturnBps": exit_roi * 10_000,
        "baseFeeIncluded": False,
        "deliveryCostIncluded": False,
        "officialSolPumpOutcomesRead": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sibling", type=pathlib.Path, required=True)
    parser.add_argument("--launchlab-db", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(args.sibling)
    conn = sqlite3.connect(args.launchlab_db)
    conn.row_factory = sqlite3.Row
    rows = selected_rows(args.sibling)
    evaluated = [evaluate(row, conn) for row in rows]
    supported = [r for r in evaluated if r["status"] == "EXACT_ROUTE_SANDBOX"]
    reason_counts = collections.Counter(r.get("reason") for r in evaluated if r["status"] != "EXACT_ROUTE_SANDBOX")
    counts = collections.Counter(r["exitReason"] for r in supported)
    returns = [float(r["normalizedReturn"]) for r in supported]
    wins = [x for x in returns if x > 0]
    gains = sum(x for x in returns if x > 0)
    losses = -sum(x for x in returns if x < 0)
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": now_utc(),
        "outcomeBlind": True,
        "officialSolPumpOutcomesRead": False,
        "sourceSibling": str(args.sibling),
        "sourceSiblingSha256": source_hash,
        "launchLabDatabase": str(args.launchlab_db),
        "entrySizeMode": "EFFECTIVE_QUOTE_RESERVE_BPS",
        "entrySizeBps": ENTRY_BPS,
        "exitPolicy": {"tp": TP, "sl": SL, "maxHoldSeconds": 120},
        "selectedRows": len(rows),
        "exactRouteSandboxRows": len(supported),
        "unsupportedRows": len(evaluated) - len(supported),
        "unsupportedReasons": dict(reason_counts),
        "exitCounts": dict(counts),
        "winRate": (len(wins) / len(returns)) if returns else None,
        "profitFactor": (gains / losses) if losses else None,
        "meanNormalizedReturn": (sum(returns) / len(returns)) if returns else None,
        "medianNormalizedReturn": (sorted(returns)[len(returns) // 2] if returns else None),
        "sumNormalizedReturn": sum(returns),
        "baseFeesIncluded": False,
        "deliveryCostsIncluded": False,
        "interpretation": "NON_AUTHORITATIVE_ROUTE_SANDBOX_ONLY",
    }
    rows_path = args.output / "rows.jsonl"
    with rows_path.open("w") as f:
        for row in evaluated:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    (args.output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (args.output / "status.json").write_text(json.dumps({
        "schema": SCHEMA + "_STATUS",
        "generatedAtUtc": report["generatedAtUtc"],
        "outcomeBlind": True,
        "outcomesRead": len(supported),
        "submissions": 0,
        "report": "report.json",
        "rows": "rows.jsonl",
    }, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
