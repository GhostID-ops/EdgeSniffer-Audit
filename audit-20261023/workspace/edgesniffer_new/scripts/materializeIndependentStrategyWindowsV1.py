#!/usr/bin/env python3
"""Build independent market-wide strategy windows from normalized observations.

The input is the receive-only normalized observation stream.  The script does
not use the teacher wallet as a trigger.  It admits only rows with explicit
Pump curve route and exact reserve state; all other observations are counted
as unresolved rather than approximated.  It is safe to run alongside the
native-matched anchor collector because it only reads the source and writes a
new research artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
import heapq
from pathlib import Path
from typing import Any

from runIndependentStrategyLabV1 import event_state, mint_of, route_of, timestamp_ms


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    opener = path.open
    if path.suffix == ".gz":
        import gzip
        opener = lambda *args, **kwargs: gzip.open(path, "rt", encoding="utf-8")  # type: ignore[assignment]
    with opener(encoding="utf-8") as handle:  # type: ignore[operator]
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def iter_rows(path: Path):
    opener = path.open
    if path.suffix == ".gz":
        import gzip
        opener = lambda *args, **kwargs: gzip.open(path, "rt", encoding="utf-8")  # type: ignore[assignment]
    with opener(encoding="utf-8") as handle:  # type: ignore[operator]
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def native_economics_state_complete(row: dict[str, Any]) -> bool:
    if row.get("native_economics_state_complete") is not True:
        return False
    # Do not trust the marker alone.  Strict economics requires the explicit
    # total/protocol/creator fee fields emitted by the native replay.
    for key in ("fee_basis_points", "protocol_fee_basis_points", "creator_fee_basis_points"):
        value = row.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return False
    state = event_state(row)
    if state is None:
        return False
    return all(getattr(state, key, None) is not None for key in ("real_sol", "real_token"))


def flow_quote_sol(row: dict[str, Any]) -> float:
    """Return signed observed flow using native lamport fields first."""
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    side = str(row.get("side") or raw.get("side") or "").lower()
    candidates = (
        row.get("quote_lamports"),
        raw.get("quoteLamports"),
        row.get("sell_quote_lamports") if side == "sell" else None,
        raw.get("sellQuoteLamports") if side == "sell" else None,
    )
    for value in candidates:
        try:
            if value is not None:
                amount = float(value) / 1_000_000_000
                return amount if side == "buy" else -amount
        except (TypeError, ValueError):
            continue
    for value in (row.get("quote_sol"), row.get("quoteSol"), raw.get("quoteSol")):
        try:
            if value is not None:
                amount = float(value)
                return amount if side == "buy" else -amount
        except (TypeError, ValueError):
            continue
    return 0.0


def materialize(rows: list[dict[str, Any]], require_native_economics_state: bool = False) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejected = Counter()
    for row in rows:
        mint = mint_of(row)
        if not mint:
            rejected["missing_mint"] += 1; continue
        if route_of(row) not in {"PUMP_BONDING_CURVE_TOP_LEVEL", "PUMP_CURVE"}:
            rejected["route_not_explicit_pump_curve"] += 1; continue
        ts = timestamp_ms(row)
        if ts is None:
            rejected["missing_receive_time"] += 1; continue
        by_mint[mint].append(row)
    output: list[dict[str, Any]] = []
    for mint, mint_rows in by_mint.items():
        mint_rows.sort(key=lambda row: timestamp_ms(row) or 0)
        # First explicit market-wide buy is the candidate signal.  A wallet
        # match is a label only; it never controls admission.
        signal = next((row for row in mint_rows if str(row.get("side") or row.get("raw_record", {}).get("side") or "").lower() == "buy"), None)
        if signal is None:
            rejected["no_market_buy_signal"] += 1; continue
        if require_native_economics_state and any(not native_economics_state_complete(row) for row in mint_rows):
            rejected["native_economics_state_incomplete"] += 1; continue
        signal_ts = timestamp_ms(signal)
        entry_ts = signal_ts + 1000  # type: ignore[operator]
        pre = [row for row in mint_rows if signal_ts <= (timestamp_ms(row) or 0) < entry_ts]
        marks = [row for row in mint_rows if (timestamp_ms(row) or 0) >= entry_ts and event_state(row) is not None]
        entry_state_row = next((row for row in reversed([r for r in mint_rows if (timestamp_ms(r) or 0) <= entry_ts]) if event_state(row) is not None), None)
        if entry_state_row is None:
            rejected["missing_causal_entry_state"] += 1; continue
        if not marks:
            rejected["missing_post_entry_marks"] += 1; continue
        entry_state = event_state(entry_state_row)
        first_state = event_state(signal)
        if entry_state is None or first_state is None:
            rejected["invalid_state"] += 1; continue
        price_before = first_state.virtual_sol / first_state.virtual_token
        price_entry = entry_state.virtual_sol / entry_state.virtual_token
        flow = 0.0
        for row in pre:
            flow += flow_quote_sol(row)
        canonical = signal.get("canonical_state") if isinstance(signal.get("canonical_state"), dict) else {}
        output.append({
            "record_type": "INDEPENDENT_MARKET_WINDOW",
            "mint": mint, "route": route_of(signal),
            "signal_receive_ts_ms": signal_ts, "signal_signature": signal.get("transaction_signature") or signal.get("signature"),
            "signal_wallet": signal.get("wallet") or signal.get("raw_record", {}).get("wallet"),
            "teacher_wallet_match": bool(signal.get("raw_record", {}).get("teacherWalletMatched") or signal.get("teacher_wallet_matched")),
            "entry_state": {"virtual_sol": entry_state.virtual_sol, "virtual_token": entry_state.virtual_token,
                             "real_sol": entry_state.real_sol, "real_token": entry_state.real_token},
            "market_cap_usd": canonical.get("market_cap_usd") or signal.get("market_cap_usd"),
            "effective_quote_reserve_sol": entry_state.virtual_sol / 1_000_000_000,
            "net_flow_sol": flow,
            "price_change_bps": ((price_entry / price_before) - 1.0) * 10_000 if price_before else None,
            "marks": marks,
            "lineage": {"source_observation_count": len(mint_rows), "entry_state_source": entry_state_row.get("observation_id") or entry_state_row.get("transaction_signature"),
                         "source_receive_clock": True,
                         "native_economics_state": require_native_economics_state},
        })
    return output, dict(rejected)


def materialize_stream(path: Path, output_path: Path, require_native_economics_state: bool = False) -> tuple[int, dict[str, int]]:
    """Bound memory to the active 300-second market window.

    The native observation file is multi-gigabyte and must not be loaded into
    a Python list.  The source is receive-time ordered; expired mints are
    finalized through a min-heap and discarded after their longest research
    horizon.
    """
    pending: dict[str, list[dict[str, Any]]] = {}
    expiry_heap: list[tuple[int, str]] = []
    rejected = Counter()
    written = 0

    def finalize(mint: str, mint_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        signal = next((row for row in mint_rows if str(row.get("side") or row.get("raw_record", {}).get("side") or "").lower() == "buy"), None)
        if signal is None:
            rejected["no_market_buy_signal"] += 1
            return None
        if require_native_economics_state and any(not native_economics_state_complete(row) for row in mint_rows):
            rejected["native_economics_state_incomplete"] += 1
            return None
        signal_ts = timestamp_ms(signal)
        if signal_ts is None:
            rejected["missing_receive_time"] += 1
            return None
        entry_ts = signal_ts + 1000
        entry_state_row = next((row for row in reversed([r for r in mint_rows if (timestamp_ms(r) or 0) <= entry_ts]) if event_state(row) is not None), None)
        marks = [row for row in mint_rows if (timestamp_ms(row) or 0) >= entry_ts and event_state(row) is not None]
        if entry_state_row is None:
            rejected["missing_causal_entry_state"] += 1
            return None
        if not marks:
            rejected["missing_post_entry_marks"] += 1
            return None
        entry_state = event_state(entry_state_row)
        first_state = event_state(signal)
        if entry_state is None or first_state is None:
            rejected["invalid_state"] += 1
            return None
        pre = [row for row in mint_rows if signal_ts <= (timestamp_ms(row) or 0) < entry_ts]
        flow = 0.0
        for row in pre:
            flow += flow_quote_sol(row)
        canonical = signal.get("canonical_state") if isinstance(signal.get("canonical_state"), dict) else {}
        return {
            "record_type": "INDEPENDENT_MARKET_WINDOW", "mint": mint, "route": route_of(signal),
            "signal_receive_ts_ms": signal_ts, "signal_signature": signal.get("transaction_signature") or signal.get("signature"),
            "signal_wallet": signal.get("wallet") or signal.get("raw_record", {}).get("wallet"),
            "teacher_wallet_match": bool(signal.get("raw_record", {}).get("teacherWalletMatched") or signal.get("teacher_wallet_matched")),
            "entry_state": {"virtual_sol": entry_state.virtual_sol, "virtual_token": entry_state.virtual_token,
                             "real_sol": entry_state.real_sol, "real_token": entry_state.real_token},
            "market_cap_usd": canonical.get("market_cap_usd") or signal.get("market_cap_usd"),
            "effective_quote_reserve_sol": entry_state.virtual_sol / 1_000_000_000,
            "net_flow_sol": flow,
            "price_change_bps": ((entry_state.virtual_sol / entry_state.virtual_token) /
                                  (first_state.virtual_sol / first_state.virtual_token) - 1.0) * 10_000,
            "marks": marks,
            "lineage": {"source_observation_count": len(mint_rows), "entry_state_source": entry_state_row.get("observation_id") or entry_state_row.get("transaction_signature"),
                         "source_receive_clock": True,
                         "native_economics_state": require_native_economics_state},
        }

    with output_path.open("w", encoding="utf-8") as output:
        for row in iter_rows(path):
            mint = mint_of(row)
            ts = timestamp_ms(row)
            if not mint:
                rejected["missing_mint"] += 1; continue
            if route_of(row) not in {"PUMP_BONDING_CURVE_TOP_LEVEL", "PUMP_CURVE"}:
                rejected["route_not_explicit_pump_curve"] += 1; continue
            if ts is None:
                rejected["missing_receive_time"] += 1; continue
            while expiry_heap and expiry_heap[0][0] < ts:
                _, expired_mint = heapq.heappop(expiry_heap)
                rows_for_mint = pending.pop(expired_mint, None)
                if rows_for_mint:
                    window = finalize(expired_mint, rows_for_mint)
                    if window:
                        output.write(json.dumps(window, sort_keys=True) + "\n")
                        written += 1
            if mint not in pending:
                if str(row.get("side") or row.get("raw_record", {}).get("side") or "").lower() != "buy":
                    continue
                pending[mint] = []
                heapq.heappush(expiry_heap, (ts + 300_000, mint))
            pending[mint].append(row)
        for mint, mint_rows in list(pending.items()):
            window = finalize(mint, mint_rows)
            if window:
                output.write(json.dumps(window, sort_keys=True) + "\n")
                written += 1
    return written, dict(rejected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-native-economics-state", action="store_true")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    windows, rejected = materialize_stream(args.input, args.output, args.require_native_economics_state)
    print(json.dumps({"windows": windows, "rejected": rejected,
                      "source_sha256": sha256_file(args.input), "trading_enabled": False}, sort_keys=True))


if __name__ == "__main__":
    main()
