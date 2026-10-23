#!/usr/bin/env python3
"""Emit one fresh non-teacher market candidate for executor calibration.

This is a calibration adapter, not a strategy selector.  It consumes the
independent native Pump-state stream, requires a fresh clean observation, and
emits at most one candidate.  It never signs or submits anything itself.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOKEN_LEGACY = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
PUMP_PROGRAM = "6EF8rrecthR5DkzonN8wu78hRvfCKubJ14M5uBEwF6P"
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
BUY_V2_DISCRIMINATORS = {
    bytes([184, 23, 238, 97, 103, 197, 211, 61]),
    bytes([194, 171, 28, 70, 104, 77, 91, 47]),
}


def read_rows(path: Path):
    if not path.exists():
        return
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 2_000_000))
        if handle.tell() > 0:
            handle.readline()
        for line in handle:
            try:
                row = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def tokens_out(state: dict[str, Any], sol_in: int, fee_bps: int) -> int:
    x = int(state.get("virtual_sol", state.get("virtual_sol_reserves", 0)))
    y = int(state.get("virtual_token", state.get("virtual_token_reserves", 0)))
    net = sol_in * (10_000 - fee_bps) // 10_000
    if x <= 0 or y <= 0 or net <= 0:
        return 0
    return y - ((x * y) // (x + net))


def candidate(row: dict[str, Any], *, now_ms: int, buy_lamports: int, max_age_ms: int, mode: str) -> dict[str, Any] | None:
    raw = row.get("raw_record") or {}
    state = (row.get("canonical_state") or {}).get("post_state") or {}
    if raw.get("side") != "buy" or not row.get("mint") or not row.get("quote_lamports"):
        return None
    token_program = row.get("token_program")
    if mode == "legacy" and token_program != TOKEN_LEGACY:
        return None
    if mode == "token2022-v2":
        if token_program != TOKEN_2022:
            return None
        if not token2022_v2_source(row, accounts=(row.get("extra") or {}).get("sourceIxAccounts") or []):
            return None
    if row.get("mayhem", {}).get("status") != "clean":
        return None
    if row.get("raw_record", {}).get("teacherWalletMatched") is True:
        return None
    received = int(row.get("local_receive_ts_ms") or 0)
    if received <= 0 or now_ms - received < 0 or now_ms - received > max_age_ms:
        return None
    if int(state.get("virtual_sol", state.get("virtual_sol_reserves", 0)) or 0) <= 0 or int(state.get("virtual_token", state.get("virtual_token_reserves", 0)) or 0) <= 0:
        return None
    fee_bps = int((row.get("canonical_state") or {}).get("fee_basis_points") or 95)
    out = tokens_out(state, buy_lamports, fee_bps)
    if out <= 0:
        return None
    accounts = (row.get("extra") or {}).get("sourceIxAccounts") or []
    curve = (row.get("extra") or {}).get("bondingCurve")
    if not curve or len(accounts) < 4:
        return None
    virtual_sol = int(state.get("virtual_sol", state.get("virtual_sol_reserves", 0)))
    virtual_token = int(state.get("virtual_token", state.get("virtual_token_reserves", 0)))
    market_cap_usd = (virtual_sol / virtual_token) * 160.0 * 1_000_000_000 if virtual_token else None
    signal_id = f"independent-calibration:{row['mint']}:{received}"
    return {
        "schemaVersion": "two-cq-continuous-shadow-event-1",
        "event": "candidate_selected",
        "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "atMs": now_ms,
        "entryAtMs": received,
        "lane": "independent-execution-calibration-token2022-v1" if mode == "token2022-v2" else "independent-execution-calibration-v1",
        "policy": "independent_calibration_token2022_v2_no_strategy_promotion_v1" if mode == "token2022-v2" else "independent_calibration_no_strategy_promotion_v1",
        "key": signal_id,
        "mint": row["mint"],
        "entryMarketCapUsd": market_cap_usd,
        "sourceSignature": raw.get("signature") or row.get("transaction_signature"),
        "sourceSlot": raw.get("slot") or row.get("source_slot"),
        "features": {
            "mint": row["mint"],
            "sourceSignature": raw.get("signature") or row.get("transaction_signature"),
            "sourceIxAccounts": accounts,
            "sourceIxDataBase64": row.get("source_ix_data_base64"),
            "tokenProgramId": row.get("token_program"),
            "creationProven": False,
            "nativeReceiveClock": True,
            "teacherWalletMatched": False,
            "calibrationOnly": True,
        },
        "directEntryQuote": {
            "status": "ok",
            "solInLamports": buy_lamports,
            "tokensOutRaw": out,
            "minTokensOutRaw": out * 9_500 // 10_000,
            "marketCapUsd": market_cap_usd,
            "curveSnapshot": {
                "pda": curve,
                "bondingCurve": curve,
                "creator": "source-template-only" if mode == "token2022-v2" else (accounts[4] if len(accounts) > 4 else accounts[1]),
                "tokenProgramId": row.get("token_program"),
                "quoteMint": (row.get("canonical_state") or {}).get("quote_mint"),
                "virtualSolReserves": int(state.get("virtual_sol", state.get("virtual_sol_reserves", 0))),
                "virtualTokenReserves": int(state.get("virtual_token", state.get("virtual_token_reserves", 0))),
                "slot": int((row.get("canonical_state") or {}).get("slot") or row.get("source_slot") or 0),
                "observedAtMs": int(row.get("decode_complete_ts_ms") or received),
                "complete": mode == "token2022-v2",
            },
        },
        "mayhemStatus": "clean",
        "calibration": {
            "mode": "EXECUTION_TELEMETRY_ONLY",
            "teacher_conditioned": False,
            "strategy_promotion_allowed": False,
            "buy_sol": buy_lamports / 1_000_000_000,
            "timing_quality": "DEGRADED_STALE_OBSERVATION_CALIBRATION_ONLY" if now_ms - received > 5_000 else "NATIVE_FRESH_OBSERVATION",
            "observation_age_ms": now_ms - received,
            "token_program_mode": mode,
            "token2022_sell_template": "canonical_source_v2" if mode == "token2022-v2" else "legacy",
        },
    }


def token2022_v2_source(row: dict[str, Any], *, accounts: list[str]) -> bool:
    mint = row.get("mint")
    quote_mint = (row.get("canonical_state") or {}).get("quote_mint")
    token_state = row.get("token2022_state") or {}
    source_data = (row.get("source_ix_data_base64") or "")
    try:
        import base64
        data = base64.b64decode(source_data, validate=True)
    except Exception:
        return False
    return (
        bool(mint)
        and len(accounts) >= 27
        and accounts[1] == mint
        and accounts[2] in {WSOL_MINT, USDC_MINT}
        and accounts[3] == TOKEN_2022
        and accounts[26] == PUMP_PROGRAM
        and quote_mint in {WSOL_MINT, USDC_MINT}
        and token_state.get("complete") is True
        and data[:8] in BUY_V2_DISCRIMINATORS
    )


def run(source: Path, output: Path, *, buy_lamports: int, max_age_ms: int, poll_ms: int, mode: str) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    started = int(time.time() * 1000)
    last_report_ms = 0
    scanned = 0
    while True:
        now_ms = int(time.time() * 1000)
        latest = None
        recent_buys = 0
        recent_legacy_buys = 0
        recent_token2022_buys = 0
        for row in read_rows(source):
            scanned += 1
            received = int(row.get("local_receive_ts_ms") or 0)
            age_ms = now_ms - received if received else None
            if row.get("raw_record", {}).get("side") == "buy" and age_ms is not None and 0 <= age_ms <= max_age_ms:
                recent_buys += 1
                if row.get("token_program") == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
                    recent_legacy_buys += 1
                elif row.get("token_program") == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb":
                    recent_token2022_buys += 1
            latest = candidate(row, now_ms=now_ms, buy_lamports=buy_lamports, max_age_ms=max_age_ms, mode=mode) or latest
        if latest is not None:
            output.write_text(json.dumps(latest, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
            return {"status": "EMITTED", "scanned": scanned, "mint": latest["mint"], "age_ms": now_ms - latest["entryAtMs"]}
        if now_ms - last_report_ms >= 30_000:
            print(json.dumps({
                "status": "WAITING_FOR_ELIGIBLE_CANDIDATE",
                "scanned": scanned,
                "fresh_buy_rows": recent_buys,
                "fresh_legacy_buy_rows": recent_legacy_buys,
                "fresh_token2022_buy_rows_rejected": recent_token2022_buys,
                "mode": mode,
                "max_age_ms": max_age_ms,
            }, sort_keys=True), flush=True)
            last_report_ms = now_ms
        if int(time.time() * 1000) - started > 0:
            time.sleep(max(poll_ms, 10) / 1000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--buy-lamports", type=int, default=1_000_000)
    parser.add_argument("--max-age-ms", type=int, default=10_000)
    parser.add_argument("--poll-ms", type=int, default=250)
    parser.add_argument("--mode", choices=("legacy", "token2022-v2"), default="legacy")
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.output, buy_lamports=args.buy_lamports, max_age_ms=args.max_age_ms, poll_ms=args.poll_ms, mode=args.mode), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
