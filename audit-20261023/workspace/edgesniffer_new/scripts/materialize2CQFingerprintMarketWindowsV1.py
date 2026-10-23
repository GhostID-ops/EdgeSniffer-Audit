#!/usr/bin/env python3
"""Detect the frozen structural 2CQ fingerprint in a market-wide native tape.

The detector never uses the teacher wallet as an admission condition.  It uses
only the fingerprint contract plus native rows available in the stream.  It
keeps the causal receipt timestamp, applies a fixed +1,000 ms entry boundary,
and emits explicit failures when state or fee fields are missing.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from collections import Counter
from pathlib import Path
from typing import Any

import runIndependentStrategyLabV1 as lab

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SOL_MINTS = {"11111111111111111111111111111111", "So11111111111111111111111111111111111111112"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_rows(path: Path):
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def side_of(row: dict[str, Any]) -> str:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    return str(row.get("side") or raw.get("side") or "").lower()


def accounts_of(row: dict[str, Any]) -> list[str]:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    value = raw.get("sourceIxAccounts") or extra.get("sourceIxAccounts") or row.get("source_ix_accounts") or []
    return [str(item) for item in value] if isinstance(value, list) else []


def quote_asset_of(row: dict[str, Any]) -> str:
    accounts = accounts_of(row)
    # V3 compact Pump layout: quote mint is account 2. Legacy native rows use
    # the wrapped-SOL quote implicitly.
    if len(accounts) >= 17 and accounts[16] == PUMP_PROGRAM:
        return accounts[2]
    return "11111111111111111111111111111111"


def fingerprint_match(row: dict[str, Any], max_virtual_sol_sol: float) -> tuple[bool, str]:
    if side_of(row) != "buy":
        return False, "not_buy"
    if lab.route_of(row) not in {"PUMP_BONDING_CURVE_TOP_LEVEL", "PUMP_CURVE"}:
        return False, "route_not_pump_curve"
    if quote_asset_of(row) not in SOL_MINTS:
        return False, "unsupported_quote_asset"
    state = pre_state_of(row) or lab.event_state(row)
    if state is None:
        return False, "missing_native_state"
    if state.virtual_sol / 1_000_000_000 > max_virtual_sol_sol:
        return False, "curve_not_early_enough"
    return True, "matched"


def pre_state_of(row: dict[str, Any]):
    values = [row.get("pre_state")]
    canonical = row.get("canonical_state")
    if isinstance(canonical, dict):
        values.append(canonical.get("pre_state"))
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    values.extend([raw.get("pre_state"), raw.get("state")])
    for value in values:
        parsed = lab.state(value)
        if parsed is not None:
            return parsed
    return None


def native_economics_complete(row: dict[str, Any]) -> bool:
    if row.get("native_economics_state_complete") is not True:
        return False
    for key in ("fee_basis_points", "protocol_fee_basis_points", "creator_fee_basis_points"):
        value = row.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return False
    state = lab.event_state(row)
    return state is not None and state.real_sol is not None and state.real_token is not None


def flow_quote_sol(row: dict[str, Any]) -> float:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    side = side_of(row)
    for value in (row.get("quote_lamports"), raw.get("quoteLamports"),
                  row.get("sell_quote_lamports") if side == "sell" else None,
                  raw.get("sellQuoteLamports") if side == "sell" else None):
        try:
            if value is not None:
                amount = float(value) / 1_000_000_000
                return amount if side == "buy" else -amount
        except (TypeError, ValueError):
            continue
    return 0.0


def flow_features(rows: list[dict[str, Any]], signal_ts: int) -> dict[str, Any]:
    windows: dict[str, int] = {"250ms": 250, "1s": 1_000, "2s": 2_000, "5s": 5_000, "20s": 20_000}
    result: dict[str, Any] = {}
    for label, duration in windows.items():
        recent = [row for row in rows if signal_ts - duration <= (lab.timestamp_ms(row) or -1) <= signal_ts]
        buys = [row for row in recent if side_of(row) == "buy"]
        sells = [row for row in recent if side_of(row) == "sell"]
        net_flow = sum(flow_quote_sol(row) for row in recent)
        wallets = {str(row.get("wallet") or (row.get("raw_record") or {}).get("wallet")) for row in buys}
        wallets.discard("None")
        result[f"buy_count_{label}"] = len(buys)
        result[f"sell_count_{label}"] = len(sells)
        result[f"unique_buyers_{label}"] = len(wallets)
        result[f"net_flow_sol_{label}"] = net_flow
    return result


def materialize(input_path: Path, fingerprint_path: Path, output_path: Path, start_at_receive_ms: int | None = None) -> dict[str, Any]:
    fingerprint = json.loads(fingerprint_path.read_text(encoding="utf-8"))
    phase = fingerprint["derived_fingerprint"]["curve_phase_observations"]["virtual_sol_sol"]
    max_phase = float(phase["p75"])
    pending: dict[str, list[dict[str, Any]]] = {}
    expiry: list[tuple[int, str]] = []
    last_signal: dict[str, int] = {}
    rejected = Counter()
    matched = 0
    written = 0
    latest_source_ts_ms: int | None = None

    def finalize(mint: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        nonlocal written
        signal = next((row for row in rows if row.get("_fingerprint_match") is True), None)
        if signal is None:
            rejected["no_fingerprint_signal"] += 1
            return None
        signal_ts = lab.timestamp_ms(signal)
        if signal_ts is None:
            rejected["missing_signal_receive_clock"] += 1
            return None
        entry_ts = signal_ts + 1_000
        entry_state_row = next((row for row in reversed(rows) if (lab.timestamp_ms(row) or -1) <= entry_ts and lab.event_state(row) is not None), None)
        marks = [row for row in rows if (lab.timestamp_ms(row) or -1) >= entry_ts and lab.event_state(row) is not None]
        if entry_state_row is None:
            rejected["missing_causal_entry_state"] += 1
        if not marks:
            rejected["missing_post_entry_marks"] += 1
        if entry_state_row is None or not marks:
            return None
        entry_state = lab.event_state(entry_state_row)
        signal_state = lab.event_state(signal)
        if entry_state is None or signal_state is None:
            rejected["invalid_state"] += 1
            return None
        complete = native_economics_complete(entry_state_row)
        if not complete:
            rejected["native_economics_fields_incomplete"] += 1
        pre_rows = [row for row in rows if signal_ts <= (lab.timestamp_ms(row) or -1) < entry_ts]
        row = {
            "record_type": "INDEPENDENT_MARKET_WINDOW",
            "fingerprint_id": fingerprint["derived_fingerprint"]["name"],
            "fingerprint_source": "preserved_2cq_evidence",
            "mint": mint,
            "route": "PUMP_BONDING_CURVE_TOP_LEVEL",
            "signal_receive_ts_ms": signal_ts,
            "signal_signature": signal.get("transaction_signature") or signal.get("signature"),
            "signal_wallet": signal.get("wallet") or (signal.get("raw_record") or {}).get("wallet"),
            "teacher_wallet_match": bool((signal.get("raw_record") or {}).get("teacherWalletMatched")),
            "entry_state": {"virtual_sol": entry_state.virtual_sol, "virtual_token": entry_state.virtual_token,
                             "real_sol": entry_state.real_sol, "real_token": entry_state.real_token},
            # Preserve the fee schedule from the native state row that defines
            # the causal entry boundary.  The strategy lab must not infer
            # entry fees from post-entry marks or silently default to zero.
            "fee_basis_points": entry_state_row.get("fee_basis_points"),
            "protocol_fee_basis_points": entry_state_row.get("protocol_fee_basis_points"),
            "creator_fee_basis_points": entry_state_row.get("creator_fee_basis_points"),
            "entry_quote_lamports": entry_state_row.get("quote_lamports") or
                                    (entry_state_row.get("raw_record") or {}).get("quoteLamports"),
            "market_cap_usd": signal.get("market_cap_usd"),
            "effective_quote_reserve_sol": entry_state.virtual_sol / 1_000_000_000,
            "net_flow_sol": sum(flow_quote_sol(item) for item in pre_rows),
            "price_change_bps": ((entry_state.virtual_sol / entry_state.virtual_token) /
                                  (signal_state.virtual_sol / signal_state.virtual_token) - 1.0) * 10_000,
            "entry_impact_bps": None,
            "marks": marks,
            # The replay cutoff is required to distinguish an absent exit mark
            # from a horizon that has not elapsed yet.
            "observation_as_of_ts_ms": latest_source_ts_ms,
            "pre_entry_features": flow_features(rows, signal_ts),
            "native_economics_state_complete": complete,
            "lineage": {
                "source_receive_clock": True,
                "entry_state_source": entry_state_row.get("observation_id") or entry_state_row.get("transaction_signature"),
                "entry_state_receive_ts_ms": lab.timestamp_ms(entry_state_row),
                "entry_delay_ms": 1_000,
                "native_state_source": "native_replay",
                "teacher_label_used_for_admission": False,
            },
        }
        written += 1
        return row

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        for source_row in iter_rows(input_path):
            ts = lab.timestamp_ms(source_row)
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
                    window = finalize(expired_mint, rows)
                    if window:
                        output.write(json.dumps(window, sort_keys=True) + "\n")
            if side_of(source_row) == "buy":
                matched_signal, reason = fingerprint_match(source_row, max_phase)
                if not matched_signal:
                    rejected[f"signal_{reason}"] += 1
                    # Non-signals still matter as market context only after a
                    # mint has an admitted candidate.
                    if mint not in pending:
                        continue
                else:
                    if mint in pending or ts - last_signal.get(mint, -10**18) < 300_000:
                        rejected["duplicate_signal"] += 1
                        continue
                    source_row = dict(source_row)
                    source_row["_fingerprint_match"] = True
                    pending[mint] = []
                    heapq.heappush(expiry, (ts + 300_000, mint))
                    last_signal[mint] = ts
                    matched += 1
            if mint in pending:
                pending[mint].append(source_row)
        for mint, rows in list(pending.items()):
            window = finalize(mint, rows)
            if window:
                output.write(json.dumps(window, sort_keys=True) + "\n")

    return {"matched_signals": matched, "windows_written": written, "rejected": dict(rejected),
            "max_virtual_sol_sol": max_phase}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--fingerprint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-at-receive-ms", type=int)
    args = parser.parse_args()
    result = materialize(args.input, args.fingerprint, args.output, args.start_at_receive_ms)
    result.update({"schema": "EDGESNIFFER_2CQ_INDEPENDENT_FINGERPRINT_WINDOWS_V1",
                   "source_sha256": sha256_file(args.input), "fingerprint_sha256": sha256_file(args.fingerprint),
                   "read_only": True, "trading_enabled": False})
    report = args.output.with_suffix(args.output.suffix + ".report.json")
    report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
