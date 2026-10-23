#!/usr/bin/env python3
"""Materialize an independent event-driven 2CQ fingerprint lane.

The older independent lane admitted an early Pump-curve buy and only then
tested broad continuation filters.  This lane keeps the raw evidence intact
and instead reconstructs the short burst immediately before the candidate
buy.  It is discovery-only: it does not use the teacher wallet as an
admission condition, select a policy, or enable execution.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
from collections import Counter, deque
from pathlib import Path
from typing import Any, Iterable

import runIndependentStrategyLabV1 as lab


WINDOWS_MS = (250, 1_000, 2_000, 5_000)
MAX_WINDOW_MS = 300_000
ENTRY_DELAY_MS = 1_000
POST_ENTRY_WINDOW_MS = 300_000
CORE_BUYS_250MS = 2
CORE_NET_FLOW_250MS = 0.0
CORE_PRICE_CHANGE_250MS = 0.0
CORE_CONSECUTIVE_BUYS = 2


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def sort_key(row: dict[str, Any]) -> tuple[int, int, int, str]:
    ts = timestamp(row) or 2**63 - 1
    try:
        slot = int(row.get("source_slot") or row.get("sourceSlot") or 0)
    except (TypeError, ValueError):
        slot = 0
    try:
        index = int(row.get("source_transaction_index") or row.get("sourceTransactionIndex") or 0)
    except (TypeError, ValueError):
        index = 0
    return (ts, slot, index, str(row.get("observation_id") or row.get("transaction_signature") or ""))


def side_of(row: dict[str, Any]) -> str:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    return str(row.get("side") or raw.get("side") or "").lower()


def quote_lamports_for_flow(row: dict[str, Any]) -> int | None:
    """Return executed quote amount in lamports, preserving unknown as None."""
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    candidates = (
        row.get("quote_lamports"), raw.get("quoteLamports"),
        row.get("sell_quote_lamports"), raw.get("sellQuoteLamports"),
    )
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            parsed = abs(int(candidate))
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def quote_sol(row: dict[str, Any]) -> float | None:
    value = quote_lamports_for_flow(row)
    return value / 1_000_000_000 if value is not None else None


def state_price(row: dict[str, Any]) -> float | None:
    state = lab.event_state(row)
    if state is None or state.virtual_token <= 0:
        return None
    return state.virtual_sol / state.virtual_token


def wallet_of(row: dict[str, Any]) -> str | None:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    value = row.get("wallet") or raw.get("wallet")
    return str(value) if value else None


def timestamp(row: dict[str, Any]) -> int | None:
    return lab.timestamp_ms(row)


def flow(row: dict[str, Any]) -> float | None:
    value = quote_sol(row)
    if value is None:
        return None if side_of(row) in {"buy", "sell"} else 0.0
    return value if side_of(row) == "buy" else -value if side_of(row) == "sell" else 0.0


def sum_known(values: list[float | None]) -> float | None:
    return sum(values) if values and all(value is not None for value in values) else None


def consecutive(rows: list[dict[str, Any]], wanted: str) -> int:
    count = 0
    for row in reversed(rows):
        if side_of(row) != wanted:
            break
        count += 1
    return count


def feature_window(rows: list[dict[str, Any]], signal_ts: int, duration_ms: int) -> dict[str, Any]:
    selected = [row for row in rows if signal_ts - duration_ms <= (timestamp(row) or -1) <= signal_ts]
    buys = [row for row in selected if side_of(row) == "buy"]
    sells = [row for row in selected if side_of(row) == "sell"]
    prices = [(timestamp(row), state_price(row)) for row in selected]
    prices = [(ts, price) for ts, price in prices if ts is not None and price is not None and price > 0]
    price_change = None
    if len(prices) >= 2:
        first = min(prices, key=lambda item: item[0])[1]
        last = max(prices, key=lambda item: item[0])[1]
        price_change = math.log(last / first) if first > 0 and last > 0 else None
    wallets = {wallet_of(row) for row in buys}
    wallets.discard(None)
    return {
        f"gross_buy_sol_{duration_ms}ms": sum_known([quote_sol(row) for row in buys]) if buys else 0.0,
        f"gross_sell_sol_{duration_ms}ms": sum_known([quote_sol(row) for row in sells]) if sells else 0.0,
        f"net_flow_sol_{duration_ms}ms": sum_known([flow(row) for row in selected]) if selected else 0.0,
        f"unknown_quote_count_{duration_ms}ms": sum(quote_sol(row) is None for row in selected if side_of(row) in {"buy", "sell"}),
        f"buy_count_{duration_ms}ms": len(buys),
        f"sell_count_{duration_ms}ms": len(sells),
        f"unique_buyers_{duration_ms}ms": len(wallets),
        f"log_price_change_{duration_ms}ms": price_change,
    }


def build_features(rows: list[dict[str, Any]], signal: dict[str, Any]) -> dict[str, Any]:
    signal_ts = timestamp(signal)
    if signal_ts is None:
        return {}
    features: dict[str, Any] = {}
    for duration in WINDOWS_MS:
        features.update(feature_window(rows, signal_ts, duration))
    recent = [row for row in rows if signal_ts - 5_000 <= (timestamp(row) or -1) <= signal_ts]
    timestamps = sorted(ts for ts in (timestamp(row) for row in recent) if ts is not None)
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:]) if b >= a]
    features["consecutive_buys"] = consecutive(recent, "buy")
    features["consecutive_sells"] = consecutive(recent, "sell")
    features["event_index"] = len(rows)
    features["median_interarrival_ms_5s"] = sorted(gaps)[len(gaps) // 2] if gaps else None
    features["signal_side"] = side_of(signal)
    features["signal_quote_sol"] = quote_sol(signal)
    return features


def core_gate(features: dict[str, Any]) -> bool:
    return (
        int(features.get("buy_count_250ms") or 0) >= CORE_BUYS_250MS
        and float(features.get("net_flow_sol_250ms") or 0.0) > CORE_NET_FLOW_250MS
        and float(features.get("log_price_change_250ms") or 0.0) > CORE_PRICE_CHANGE_250MS
        and int(features.get("consecutive_buys") or 0) >= CORE_CONSECUTIVE_BUYS
    )


def phase_ok(row: dict[str, Any], max_virtual_sol_sol: float) -> bool:
    state = lab.event_state(row)
    return state is not None and state.virtual_sol / 1_000_000_000 <= max_virtual_sol_sol


def native_complete(row: dict[str, Any]) -> bool:
    return (
        row.get("native_economics_state_complete") is True
        and all(isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)
                for key in ("fee_basis_points", "protocol_fee_basis_points", "creator_fee_basis_points"))
        and lab.event_state(row) is not None
    )


def materialize(input_path: Path, fingerprint_path: Path, output_path: Path,
                start_at_receive_ms: int | None = None) -> dict[str, Any]:
    fingerprint = json.loads(fingerprint_path.read_text(encoding="utf-8"))
    phase = fingerprint["derived_fingerprint"]["curve_phase_observations"]["virtual_sol_sol"]
    max_phase = float(phase["p75"])
    pending: dict[str, list[dict[str, Any]]] = {}
    history: dict[str, deque[dict[str, Any]]] = {}
    expiry: list[tuple[int, str]] = []
    last_signal: dict[str, int] = {}
    rejected: Counter[str] = Counter()
    matched = 0
    written = 0
    latest_source_ts_ms: int | None = None
    source_rows = list(read_rows(input_path))
    source_rows_sorted = sorted(source_rows, key=sort_key)
    reordered_rows = sum(left is not right for left, right in zip(source_rows, source_rows_sorted))

    def finalize(mint: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        nonlocal written
        signal = next((row for row in rows if row.get("_event_driven_signal") is True), None)
        if signal is None:
            rejected["no_event_driven_signal"] += 1
            return None
        signal_ts = timestamp(signal)
        if signal_ts is None:
            rejected["missing_signal_receive_clock"] += 1
            return None
        entry_ts = signal_ts + ENTRY_DELAY_MS
        entry_rows = [row for row in rows if (timestamp(row) or -1) <= entry_ts and lab.event_state(row) is not None]
        entry_state_row = entry_rows[-1] if entry_rows else None
        marks = [row for row in rows if (timestamp(row) or -1) >= entry_ts and lab.event_state(row) is not None]
        if entry_state_row is None:
            rejected["missing_causal_entry_state"] += 1
            return None
        if not marks:
            rejected["missing_post_entry_marks"] += 1
            return None
        if not native_complete(entry_state_row):
            rejected["native_economics_fields_incomplete"] += 1
            return None
        entry_state = lab.event_state(entry_state_row)
        signal_state = lab.event_state(signal)
        if entry_state is None or signal_state is None:
            rejected["invalid_state"] += 1
            return None
        delay_rows = [row for row in rows if signal_ts <= (timestamp(row) or -1) < entry_ts]
        row = {
            "record_type": "INDEPENDENT_EVENT_DRIVEN_MARKET_WINDOW",
            "fingerprint_id": "2CQ_EVENT_DRIVEN_AVAILABLE_FEATURES_V1",
            "fingerprint_source": "preserved_2cq_event_driven_evidence",
            "mint": mint,
            "route": "PUMP_BONDING_CURVE_TOP_LEVEL",
            "signal_receive_ts_ms": signal_ts,
            "signal_signature": signal.get("transaction_signature") or signal.get("signature"),
            "signal_wallet": wallet_of(signal),
            "teacher_wallet_match": False,
            "entry_state": {"virtual_sol": entry_state.virtual_sol, "virtual_token": entry_state.virtual_token,
                             "real_sol": entry_state.real_sol, "real_token": entry_state.real_token},
            "fee_basis_points": entry_state_row.get("fee_basis_points"),
            "protocol_fee_basis_points": entry_state_row.get("protocol_fee_basis_points"),
            "creator_fee_basis_points": entry_state_row.get("creator_fee_basis_points"),
            "entry_quote_lamports": entry_state_row.get("quote_lamports"),
            "effective_quote_reserve_sol": entry_state.virtual_sol / 1_000_000_000,
            "net_flow_sol": sum_known([flow(item) for item in delay_rows]) if delay_rows else 0.0,
            "price_change_bps": ((entry_state.virtual_sol / entry_state.virtual_token) /
                                  (signal_state.virtual_sol / signal_state.virtual_token) - 1.0) * 10_000,
            "entry_impact_bps": None,
            "marks": marks,
            "observation_as_of_ts_ms": latest_source_ts_ms,
            "event_driven_features": signal.get("_event_driven_features"),
            "native_economics_state_complete": True,
            "lineage": {
                "source_receive_clock": True,
                "entry_state_source": entry_state_row.get("observation_id") or entry_state_row.get("transaction_signature"),
                "entry_state_receive_ts_ms": timestamp(entry_state_row),
                "entry_delay_ms": ENTRY_DELAY_MS,
                "native_state_source": "native_replay",
                "teacher_label_used_for_admission": False,
            },
        }
        written += 1
        return row

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        for source_row in source_rows_sorted:
            ts = timestamp(source_row)
            mint = lab.mint_of(source_row)
            if ts is None or not mint:
                rejected["missing_receive_clock_or_mint"] += 1
                continue
            if start_at_receive_ms is not None and ts < start_at_receive_ms:
                continue
            latest_source_ts_ms = max(latest_source_ts_ms or ts, ts)
            while expiry and expiry[0][0] < ts:
                _, expired_mint = heapq.heappop(expiry)
                rows = pending.pop(expired_mint, None)
                if rows:
                    result = finalize(expired_mint, rows)
                    if result:
                        output.write(json.dumps(result, sort_keys=True) + "\n")
            bucket = history.setdefault(mint, deque())
            while bucket and ts - (timestamp(bucket[0]) or ts) > MAX_WINDOW_MS:
                bucket.popleft()
            admitted_current = False
            if side_of(source_row) == "buy" and lab.route_of(source_row) in {"PUMP_CURVE", "PUMP_BONDING_CURVE_TOP_LEVEL"}:
                if phase_ok(source_row, max_phase):
                    context = list(bucket) + [source_row]
                    features = build_features(context, source_row)
                    if core_gate(features):
                        if mint in pending or ts - last_signal.get(mint, -10**18) < POST_ENTRY_WINDOW_MS:
                            rejected["duplicate_signal"] += 1
                        else:
                            signal = dict(source_row)
                            signal["_event_driven_signal"] = True
                            signal["_event_driven_features"] = features
                            pending[mint] = list(bucket) + [signal]
                            heapq.heappush(expiry, (ts + POST_ENTRY_WINDOW_MS, mint))
                            last_signal[mint] = ts
                            matched += 1
                            admitted_current = True
                    else:
                        rejected["event_core_gate"] += 1
                else:
                    rejected["curve_not_early_enough"] += 1
            if mint in pending and not admitted_current:
                pending[mint].append(source_row)
            bucket.append(source_row)
        for mint, rows in list(pending.items()):
            result = finalize(mint, rows)
            if result:
                output.write(json.dumps(result, sort_keys=True) + "\n")

    return {"matched_signals": matched, "windows_written": written,
            "rejected": dict(rejected), "max_virtual_sol_sol": max_phase,
            "source_rows_loaded": len(source_rows), "reordered_rows": reordered_rows,
            "gate": {"buy_count_250ms_min": CORE_BUYS_250MS,
                      "net_flow_250ms_min": CORE_NET_FLOW_250MS,
                      "log_price_change_250ms_min": CORE_PRICE_CHANGE_250MS,
                      "consecutive_buys_min": CORE_CONSECUTIVE_BUYS}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--fingerprint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-at-receive-ms", type=int)
    args = parser.parse_args()
    result = materialize(args.input, args.fingerprint, args.output, args.start_at_receive_ms)
    result.update({"schema": "EDGESNIFFER_2CQ_EVENT_DRIVEN_INDEPENDENT_WINDOWS_V1",
                   "source_sha256": sha256_file(args.input),
                   "fingerprint_sha256": sha256_file(args.fingerprint),
                   "read_only": True, "trading_enabled": False,
                   "policy_frozen": False, "promotion_blocked": True})
    report = args.output.with_suffix(args.output.suffix + ".report.json")
    report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
