#!/usr/bin/env python3
"""Read-only 8YCB CreateV2-only chain and retained-stream audit.

This program writes only to its explicitly supplied output directory. It does
not import, signal, or alter any production/shadow service.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable


TARGET = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
CREATE_V2_DISC = bytes.fromhex("d6904cec5f8b31b4")
TRADE_EVENT_DISC = bytes.fromhex("bddb7fd34ee661ee")
INITIAL_VSOL = 30_000_000_000
INITIAL_VTOK = 1_073_000_000_000_000
SUPPLY_TOKENS = 1_000_000_000
PUMP_FEE_BPS = 125
PRINCIPAL = 100_000_000
NON_PUMP_COST = 8_110_000
DELAYS = [25, 50, 100, 250, 500, 1000]
SELLABLE = [122, 178, 346]
MINTS = [
    "shzTGqcCQUewq3ymRoLndyFne1tBZBpc9Uy9tmapump",
    "5kFq6MqvweRHPYRTX2DAjhxSzaUKWpJT5m3c4edvpump",
    "DGw3SEzhBsN13ZmSvAXM831fHddu8wvzoHohfCFupump",
    "8mCMoDft9b6hCssKUj7c24EVGTSfU7rj6EXG1HoRpump",
    "GjV8cBe2cfd8XrMvfxgV2XgYteKLe6eLh69fDQNBpump",
    "BzY9iNAaLaGr7PckAHancGH3dhHwF1wX4GbKX6pNpump",
    "9Xne2Syh7CwQqYiA2KjARfrEsqxZb6pBdKHFTinRpump",
    "Gd7Tnc92phYGtqevksEftEegreEbKHfKYuk6jMhrpump",
    "6MxDkbJ7f9igpaCTTjC2ZRHnNLveAQHZHmBr3U8Qpump",
    "HrATVUNfC5fditosYYEY2KZbijGc8nh67s8KjTs6pump",
]
APPROX_CREATE_TIMES = {
    MINTS[0]: 1788479318, MINTS[1]: 1788479319, MINTS[2]: 1788479326,
    MINTS[3]: 1788479326, MINTS[4]: 1788479356, MINTS[5]: 1788479356,
    MINTS[6]: 1788479356, MINTS[7]: 1788479356, MINTS[8]: 1788479373,
    MINTS[9]: 1788479440,
}
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
B58_MAP = {char: index for index, char in enumerate(B58)}


def b58decode(value: str) -> bytes:
    number = 0
    for char in value:
        number = number * 58 + B58_MAP[char]
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + raw


def b58encode(value: bytes) -> str:
    leading = len(value) - len(value.lstrip(b"\0"))
    number = int.from_bytes(value, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = B58[remainder] + encoded
    return "1" * leading + (encoded or ("" if leading else "1"))


def iso_ms(value: int | float | None) -> str | None:
    if value is None:
        return None
    return dt.datetime.fromtimestamp(float(value) / 1000, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quantile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    return ordered[round((len(ordered) - 1) * fraction)] if ordered else None


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


class Rpc:
    def __init__(self, url: str):
        self.url = url
        self.request_id = 0

    def call(self, method: str, params: list[Any], attempts: int = 10) -> Any:
        self.request_id += 1
        payload = {"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "8ycb-create-only-read-only-audit-v1"},
        )
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    answer = json.load(response)
                if answer.get("error"):
                    raise RuntimeError(json.dumps(answer["error"], sort_keys=True))
                return answer.get("result")
            except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                last = exc
                print(f"RPC_RETRY method={method} attempt={attempt + 1} error={exc}", file=sys.stderr, flush=True)
                time.sleep(min(10, .5 * (2 ** attempt)))
        raise RuntimeError(f"RPC {method} failed: {last}")


def load_rpc_url(env_path: Path) -> str:
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("SECURE_RPC_URL="):
            return line.split("=", 1)[1].strip().strip("\"").strip("'")
    raise RuntimeError("SECURE_RPC_URL not found")


def account_keys(tx: dict[str, Any]) -> list[dict[str, Any]]:
    keys = tx["transaction"]["message"].get("accountKeys") or []
    return [row if isinstance(row, dict) else {"pubkey": row} for row in keys]


def instruction_program(ix: dict[str, Any], keys: list[dict[str, Any]]) -> str | None:
    if ix.get("programId"):
        return ix["programId"]
    index = ix.get("programIdIndex")
    return keys[index].get("pubkey") if isinstance(index, int) and index < len(keys) else None


def decode_trade_events(tx: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    for line in (tx.get("meta") or {}).get("logMessages") or []:
        if not line.startswith("Program data: "):
            continue
        try:
            raw = base64.b64decode(line.split(": ", 1)[1])
        except Exception:
            continue
        if len(raw) < 129 or raw[:8] != TRADE_EVENT_DISC:
            continue
        events.append({
            "mint": b58encode(raw[8:40]),
            "mint_bytes_hex": raw[8:40].hex(),
            "sol_amount_lamports": int.from_bytes(raw[40:48], "little"),
            "token_amount_raw": int.from_bytes(raw[48:56], "little"),
            "is_buy": bool(raw[56]),
            "user": b58encode(raw[57:89]),
            "user_bytes_hex": raw[57:89].hex(),
            "event_timestamp_unix": int.from_bytes(raw[89:97], "little", signed=True),
            "virtual_sol_reserves_post_lamports": int.from_bytes(raw[97:105], "little"),
            "virtual_token_reserves_post_raw": int.from_bytes(raw[105:113], "little"),
            "real_sol_reserves_post_lamports": int.from_bytes(raw[113:121], "little"),
            "real_token_reserves_post_raw": int.from_bytes(raw[121:129], "little"),
        })
    return events


def decoded_instruction(ix: dict[str, Any], keys: list[dict[str, Any]]) -> dict[str, Any]:
    program = instruction_program(ix, keys)
    parsed = ix.get("parsed")
    data = ix.get("data")
    raw = None
    if isinstance(data, str):
        try:
            raw = b58decode(data)
        except Exception:
            raw = None
    accounts = ix.get("accounts") or []
    resolved_accounts = []
    for value in accounts:
        if isinstance(value, int) and value < len(keys):
            resolved_accounts.append(keys[value].get("pubkey"))
        else:
            resolved_accounts.append(value)
    return {
        "program": ix.get("program"), "program_id": program,
        "parsed_type": parsed.get("type") if isinstance(parsed, dict) else None,
        "parsed_info": parsed.get("info") if isinstance(parsed, dict) else None,
        "data_base58": data if isinstance(data, str) else None,
        "discriminator_hex": raw[:8].hex() if raw and len(raw) >= 8 else None,
        "accounts": resolved_accounts,
    }


def token_deltas(tx: dict[str, Any], mint: str) -> list[dict[str, Any]]:
    keys = account_keys(tx)
    rows: dict[tuple[int, str | None], dict[str, Any]] = {}
    meta = tx.get("meta") or {}
    for side, balances in (("pre", meta.get("preTokenBalances") or []), ("post", meta.get("postTokenBalances") or [])):
        for item in balances:
            if item.get("mint") != mint:
                continue
            key = (int(item["accountIndex"]), item.get("owner"))
            row = rows.setdefault(key, {"account_index": key[0], "account": keys[key[0]].get("pubkey") if key[0] < len(keys) else None, "owner": key[1], "pre_raw": 0, "post_raw": 0})
            row[side + "_raw"] = int((item.get("uiTokenAmount") or {}).get("amount") or 0)
    output = []
    for row in rows.values():
        row["delta_raw"] = row["post_raw"] - row["pre_raw"]
        if row["delta_raw"]:
            output.append(row)
    return output


def summarize_transaction(tx: dict[str, Any], mint: str, canonical_index: int | None) -> dict[str, Any]:
    keys = account_keys(tx)
    meta = tx.get("meta") or {}
    message = tx["transaction"]["message"]
    outer = [decoded_instruction(ix, keys) for ix in message.get("instructions") or []]
    inner = []
    for group in meta.get("innerInstructions") or []:
        for inner_index, ix in enumerate(group.get("instructions") or []):
            inner.append({"outer_instruction_index": group.get("index"), "inner_instruction_index": inner_index, **decoded_instruction(ix, keys)})
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    sol = []
    for index, (before, after) in enumerate(zip(pre, post)):
        if before != after:
            sol.append({"account_index": index, "account": keys[index].get("pubkey") if index < len(keys) else None, "pre_lamports": before, "post_lamports": after, "delta_lamports": after - before})
    logs = meta.get("logMessages") or []
    instruction_logs = [line.split("Instruction: ", 1)[1] for line in logs if "Instruction: " in line]
    pump_outer = [row for row in outer if row["program_id"] == PUMP]
    trades = decode_trade_events(tx)
    return {
        "signature": tx["transaction"]["signatures"][0], "slot": tx.get("slot"),
        "canonical_transaction_index": canonical_index, "block_time_unix": tx.get("blockTime"),
        "utc": dt.datetime.fromtimestamp(tx["blockTime"], dt.timezone.utc).isoformat().replace("+00:00", "Z") if tx.get("blockTime") else None,
        "status": "SUCCESS" if meta.get("err") is None else "FAILED", "error": meta.get("err"),
        "fee_lamports": meta.get("fee"), "fee_payer": keys[0].get("pubkey") if keys else None,
        "signers": [row.get("pubkey") for row in keys if row.get("signer")],
        "target_signed": any(row.get("pubkey") == TARGET and row.get("signer") for row in keys),
        "outer_instructions": outer, "pump_outer_instructions": pump_outer,
        "inner_instructions": inner, "instruction_logs": instruction_logs,
        "token_account_creation_instructions": [row for row in outer + inner if row.get("parsed_type") in {"create", "createAccount", "createIdempotent", "initializeAccount", "initializeAccount2", "initializeAccount3"}],
        "mint_token_deltas": token_deltas(tx, mint), "sol_deltas": sol,
        "trade_events": trades,
        "any_buy_in_transaction": any(row["is_buy"] for row in trades) or "Buy" in instruction_logs or "BuyExactSolIn" in instruction_logs,
        "target_buy_in_transaction": False,
        "other_wallet_atomic_buy_in_transaction": False,
    }


def canonical_index(rpc: Rpc, slot: int, signature: str, cache: dict[int, list[str]]) -> int | None:
    if slot not in cache:
        cache[slot] = rpc.call("getBlock", [slot, {"commitment": "finalized", "transactionDetails": "signatures", "rewards": False, "maxSupportedTransactionVersion": 0}]).get("signatures") or []
    try:
        return cache[slot].index(signature)
    except ValueError:
        return None


def collect_signatures(rpc: Rpc, mint: str, approximate: int) -> list[dict[str, Any]]:
    output = []
    before = None
    for _ in range(30):
        options: dict[str, Any] = {"limit": 1000, "commitment": "finalized"}
        if before:
            options["before"] = before
        page = rpc.call("getSignaturesForAddress", [mint, options])
        if not page:
            break
        output.extend(row for row in page if row.get("blockTime") is not None and approximate - 10 <= row["blockTime"] <= approximate + 122)
        minimum = min((row.get("blockTime") for row in page if row.get("blockTime") is not None), default=approximate)
        if minimum < approximate - 10 or len(page) < 1000:
            break
        before = page[-1]["signature"]
    return output


def scan_feed(path: Path, mints: set[str], start_offset: int) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    selected: dict[str, list[dict[str, Any]]] = defaultdict(list)
    size = path.stat().st_size
    effective = min(max(0, start_offset), size)
    with path.open("rb") as handle:
        handle.seek(effective)
        if effective:
            handle.readline()
        scan_start = handle.tell()
        malformed = 0
        rows = 0
        for raw in handle:
            rows += 1
            try:
                row = json.loads(raw)
            except Exception:
                malformed += 1
                continue
            mint = row.get("mint")
            if mint in mints:
                selected[mint].append(row)
        scan_end = handle.tell()
    return selected, {"path": str(path), "file_size_at_scan": size, "start_offset": scan_start, "end_offset": scan_end, "rows_scanned": rows, "malformed_rows": malformed, "selected_rows": sum(map(len, selected.values()))}


def extract_receive_ms(row: dict[str, Any]) -> int | None:
    for key in ["local_receive_ts_ms", "creation_ts_ms", "ts_ms", "receive_ms", "received_at_ms", "source_receive_ms", "observation_ts_ms"]:
        value = row.get(key)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def source_signature(row: dict[str, Any]) -> str | None:
    return row.get("transaction_signature") or row.get("signature") or (row.get("raw_record") or {}).get("signature")


def buy_tokens(vsol: int, vtok: int, gross: int) -> int:
    return max(0, vtok - (vsol * vtok) // (vsol + gross)) if gross > 0 else 0


def apply_buy(vsol: int, vtok: int, gross: int) -> tuple[int, int, int]:
    tokens = buy_tokens(vsol, vtok, gross)
    net = gross - gross * PUMP_FEE_BPS // 10_000
    return vsol + net, vtok - tokens, tokens


def sell_value(vsol: int, vtok: int, tokens: int) -> int:
    if tokens <= 0:
        return 0
    gross = max(0, vsol - (vsol * vtok) // (vtok + tokens))
    return gross - gross * PUMP_FEE_BPS // 10_000


def apply_sell(vsol: int, vtok: int, tokens: int) -> tuple[int, int]:
    return ((vsol * vtok) // (vtok + tokens), vtok + tokens) if tokens > 0 else (vsol, vtok)


def apply_flow(state: tuple[int, int], flow: dict[str, Any]) -> tuple[int, int]:
    if flow["side"] == "buy":
        return apply_buy(*state, int(flow["quote_lamports"]))[:2]
    return apply_sell(*state, int(flow["sell_token_raw"]))


def market_cap_sol(state: tuple[int, int]) -> float:
    vsol, vtok = state
    return vsol * 1_000_000 * SUPPLY_TOKENS / (1_000_000_000 * vtok)


def simulate(launch: dict[str, Any], flows: list[dict[str, Any]], delay: int, sellable: int, stress: float) -> dict[str, Any]:
    signal = int(launch["signal_ts_ms"])
    entry = signal + delay
    timeout = entry + 120_000
    state = (int(launch["initial_vsol"]), int(launch["initial_vtok"]))
    filtered = sorted(
        [row for row in flows if row["signature"] != launch["create_signature"] and row["side"] in {"buy", "sell"}],
        key=lambda row: (row["ts_ms"], row.get("slot", 0), row.get("tx_index", 0), row.get("ix_index", 0), row["signature"]),
    )
    future = []
    for row in filtered:
        if row["ts_ms"] <= entry:
            state = apply_flow(state, row)
        elif row["ts_ms"] <= timeout:
            future.append(row)
    pre_entry = state
    after = apply_buy(*state, PRINCIPAL)
    state = after[:2]
    tokens = max(1, int(after[2] / stress))
    sellable_ts = entry + sellable
    index = 0
    while index < len(future) and future[index]["ts_ms"] <= sellable_ts:
        state = apply_flow(state, future[index]); index += 1
    gross_roi = 100 * (sell_value(*state, tokens) / PRINCIPAL - 1)
    mfe = mae = gross_roi
    exit_reason = "TIMEOUT_120S"
    exit_ts = timeout
    if gross_roi >= 20:
        exit_reason, exit_ts = "TP20", sellable_ts
    elif gross_roi <= -20:
        exit_reason, exit_ts = "SL20", sellable_ts
    else:
        for row in future[index:]:
            state = apply_flow(state, row)
            gross_roi = 100 * (sell_value(*state, tokens) / PRINCIPAL - 1)
            mfe, mae = max(mfe, gross_roi), min(mae, gross_roi)
            if gross_roi >= 20 or gross_roi <= -20:
                exit_reason = "TP20" if gross_roi >= 20 else "SL20"
                exit_ts = row["ts_ms"]
                break
    net_roi = gross_roi - 100 * NON_PUMP_COST / PRINCIPAL
    return {
        "cohort": launch["cohort"], "mint": launch["mint"], "signal_ts_ms": signal,
        "delay_ms": delay, "sellability_ms_after_entry": sellable, "entry_stress_factor": stress,
        "entry_ts_ms": entry, "exit_ts_ms": exit_ts, "exit_reason": exit_reason,
        "pre_entry_virtual_sol_lamports": pre_entry[0], "pre_entry_virtual_token_raw": pre_entry[1],
        "tokens_out_raw": tokens, "gross_roi_pct": gross_roi, "non_pump_cost_lamports": NON_PUMP_COST,
        "net_roi_pct": net_roi, "net_pnl_sol": net_roi * .001,
        "mfe_gross_roi_pct": mfe, "mae_gross_roi_pct": mae,
    }


def economics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["net_roi_pct"]) for row in rows]
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return {
        "n": len(rows), "resolved": len(rows), "wins": sum(value > 0 for value in values),
        "losses": sum(value < 0 for value in values),
        "win_rate": sum(value > 0 for value in values) / len(values) if values else None,
        "profit_factor": gains / losses if losses else None,
        "expectancy_net_roi_pct": statistics.mean(values) if values else None,
        "normalized_net_pnl_sol_at_0p1": sum(values) * .001,
        "tp": sum(row["exit_reason"] == "TP20" for row in rows),
        "sl": sum(row["exit_reason"] == "SL20" for row in rows),
        "timeout": sum(row["exit_reason"] == "TIMEOUT_120S" for row in rows),
        "mfe_gross_roi_pct_p50": quantile((row["mfe_gross_roi_pct"] for row in rows), .5),
        "mae_gross_roi_pct_p50": quantile((row["mae_gross_roi_pct"] for row in rows), .5),
    }


def rpc_statuses(rpc: Rpc, by_mint: dict[str, list[dict[str, Any]]], cache_path: Path) -> tuple[dict[str, str], dict[str, Any]]:
    signatures = {row["signature"] for rows in by_mint.values() for row in rows}
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    missing = sorted(signatures - set(cache))
    for offset in range(0, len(missing), 256):
        chunk = missing[offset:offset + 256]
        values = rpc.call("getSignatureStatuses", [chunk, {"searchTransactionHistory": True}]) or {}
        for signature, status in zip(chunk, values.get("value") or []):
            cache[signature] = "UNKNOWN" if status is None else "SUCCESS" if status.get("err") is None else "FAILED"
        write_json(cache_path, cache)
    write_json(cache_path, cache)
    counts = Counter(cache.get(signature, "UNKNOWN") for signature in signatures)
    return cache, {"requested_unique_signatures": len(signatures), "counts": dict(counts), "cache_path": str(cache_path)}


def load_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_historical_stream(tsv: Path, mints: set[str]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    total = selected = 0
    with tsv.open("r", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle, delimiter="\t"):
            total += 1
            if raw["mint"] not in mints:
                continue
            row = {"ts_ms": int(raw["ts_ms"]), "slot": int(raw["slot"]), "tx_index": int(raw["tx_index"]),
                   "ix_index": int(raw["ix_index"]), "signature": raw["signature"], "mint": raw["mint"],
                   "side": raw["side"], "wallet": raw["wallet"] or None,
                   "quote_lamports": int(raw["quote_lamports"]), "sell_token_raw": int(raw["sell_token_raw"])}
            identity = (row["signature"], row["mint"], row["side"], row["ix_index"], row["wallet"])
            if identity in seen:
                continue
            seen.add(identity); selected += 1; by_mint[row["mint"]].append(row)
    for rows in by_mint.values():
        rows.sort(key=lambda row: (row["ts_ms"], row["slot"], row["tx_index"], row["ix_index"], row["signature"]))
    return by_mint, {"rows_scanned": total, "selected_unique_rows": selected, "selected_mints": len(by_mint)}


def external_stats(launches: list[dict[str, Any]], by_mint: dict[str, list[dict[str, Any]]], recurrent: set[str]) -> dict[str, Any]:
    per_launch = []
    for launch in launches:
        signal = launch["signal_ts_ms"]
        buys = [row for row in by_mint.get(launch["mint"], []) if row["side"] == "buy" and row["signature"] != launch["create_signature"] and row.get("wallet") not in {None, TARGET} and 0 <= row["ts_ms"] - signal <= 120_000]
        buys.sort(key=lambda row: (row["ts_ms"], row["slot"], row["tx_index"], row["ix_index"]))
        first = buys[0]["ts_ms"] - signal if buys else None
        within = [row for row in buys if row["ts_ms"] - signal <= 1000]
        recurrent_rows = [row for row in within if row.get("wallet") in recurrent]
        total_sol = sum(row["quote_lamports"] for row in within) / 1e9
        recurrent_sol = sum(row["quote_lamports"] for row in recurrent_rows) / 1e9
        per_launch.append({"mint": launch["mint"], "first_external_buy_latency_ms": first,
                           "recurrent_wallet_present_within_1s": bool(recurrent_rows),
                           "recurrent_wallets_within_1s": sorted({row["wallet"] for row in recurrent_rows}),
                           "recurrent_sol_within_1s": recurrent_sol, "external_sol_within_1s": total_sol,
                           "recurrent_share": recurrent_sol / total_sol if total_sol else None})
    delays = [row["first_external_buy_latency_ms"] for row in per_launch if row["first_external_buy_latency_ms"] is not None]
    total = sum(row["external_sol_within_1s"] for row in per_launch)
    rec = sum(row["recurrent_sol_within_1s"] for row in per_launch)
    return {"n": len(per_launch), "launches_with_external_buy": len(delays),
            "first_external_buy_latency_ms": {"p50": quantile(delays, .5), "p75": quantile(delays, .75), "p90": quantile(delays, .9)},
            "launches_with_recurrent_within_1s": sum(row["recurrent_wallet_present_within_1s"] for row in per_launch),
            "recurrent_participation_rate": sum(row["recurrent_wallet_present_within_1s"] for row in per_launch) / len(per_launch) if per_launch else None,
            "recurrent_sol_within_1s": rec, "external_sol_within_1s": total,
            "recurrent_share": rec / total if total else None, "launches": per_launch}


def summarize_grid(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for delay in DELAYS:
        output[str(delay)] = {}
        for sellable in SELLABLE:
            output[str(delay)][str(sellable)] = {}
            for stress in [1.0, 1.05, 1.10]:
                key = "base" if stress == 1 else f"entry_{round((stress - 1) * 100)}pct_worse"
                output[str(delay)][str(sellable)][key] = economics([row for row in rows if row["delay_ms"] == delay and row["sellability_ms_after_entry"] == sellable and row["entry_stress_factor"] == stress])
    return output


def chronological_primary(launches: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    primary = {row["mint"]: row for row in outcomes if row["delay_ms"] == 25 and row["sellability_ms_after_entry"] == 178 and row["entry_stress_factor"] == 1.0}
    ordered = sorted(launches, key=lambda row: row["signal_ts_ms"])
    result = []
    for label, lo, hi in [("first_50pct", 0, .5), ("middle_25pct", .5, .75), ("final_25pct", .75, 1)]:
        subset = ordered[round(len(ordered) * lo):round(len(ordered) * hi)]
        result.append({"split": label, "start_utc": iso_ms(subset[0]["signal_ts_ms"]) if subset else None,
                       "end_utc": iso_ms(subset[-1]["signal_ts_ms"]) if subset else None,
                       "economics": economics([primary[row["mint"]] for row in subset if row["mint"] in primary])})
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--v2", required=True)
    parser.add_argument("--v3", required=True)
    parser.add_argument("--prospective", required=True)
    parser.add_argument("--global-feed", required=True)
    parser.add_argument("--env", default="/root/bot/EdgeSniffer/.env")
    parser.add_argument("--feed-start-offset", type=int, default=20_444_327_708)
    args = parser.parse_args()
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    v2, v3, prospective = Path(args.v2), Path(args.v3), Path(args.prospective)
    rpc = Rpc(load_rpc_url(Path(args.env)))
    recurrent_doc = json.loads((prospective / "v3_recurrent_wallets.json").read_text())
    recurrent = {row["wallet"] for row in recurrent_doc["top_repeated_wallets"] if row["target_launches_bought_within_1s"] >= 5}

    feed_rows, feed_scan = scan_feed(Path(args.global_feed), set(MINTS), args.feed_start_offset)
    write_json(output / "ten_normalized_source_rows.json", {"scan": feed_scan, "rows": feed_rows})
    block_cache: dict[int, list[str]] = {}
    ten = []
    raw_create = {}
    all_activity_raw = []
    ten_flows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mint in MINTS:
        sig_rows = collect_signatures(rpc, mint, APPROX_CREATE_TIMES[mint])
        successful = [row for row in sig_rows if row.get("err") is None]
        summaries = []
        for sig_row in successful:
            tx = rpc.call("getTransaction", [sig_row["signature"], {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0}])
            if tx is None:
                continue
            index = canonical_index(rpc, int(tx["slot"]), sig_row["signature"], block_cache)
            summary = summarize_transaction(tx, mint, index)
            summaries.append(summary)
            all_activity_raw.append({"mint": mint, "transaction": tx, "canonical_transaction_index": index})
        source_create_signatures = {source_signature(row) for row in feed_rows.get(mint, []) if row.get("side") == "create"}
        creates = [row for row in summaries if row["signature"] in source_create_signatures and "CreateV2" in row["instruction_logs"]]
        if len(creates) != 1:
            raise RuntimeError(f"{mint}: expected exactly one successful CreateV2, got {len(creates)}")
        create = creates[0]
        raw_create[mint] = next(row["transaction"] for row in all_activity_raw if row["mint"] == mint and row["transaction"]["transaction"]["signatures"][0] == create["signature"])
        createv2_ix = next(ix for ix in create["pump_outer_instructions"] if ix.get("discriminator_hex") == CREATE_V2_DISC.hex())
        create["createv2_creator_account"] = createv2_ix["accounts"][5]
        create["creator"] = create["createv2_creator_account"]
        create["target_is_creator"] = create["creator"] == TARGET and create["target_signed"] and create["fee_payer"] == TARGET
        create["classification"] = "GENUINE_8YCB_TOKEN_LAUNCH" if create["target_is_creator"] else "AMBIGUOUS"
        create["mint_address_signature_window"] = {"from_unix": APPROX_CREATE_TIMES[mint] - 10,
                                                    "through_unix": APPROX_CREATE_TIMES[mint] + 122,
                                                    "total": len(sig_rows), "successful": len(successful),
                                                    "failed": sum(row.get("err") is not None for row in sig_rows)}
        create["why_no_atomic_buy"] = "The successful outer transaction contains CreateV2 plus System transfers, but no Pump Buy/BuyExactSolIn, no TradeEvent buy, and no positive 8YCB token delta. The 1B-token mint delta is owned by the bonding-curve account."
        create["separate_target_buy_within_120s"] = False
        activity = []
        create_source = next(row for row in feed_rows.get(mint, []) if row.get("side") == "create" and source_signature(row) == create["signature"])
        create_receipt = extract_receive_ms(create_source) or int(create["block_time_unix"] * 1000)
        create_order = (int(create["slot"]), int(create["canonical_transaction_index"] or 0))
        for row in summaries:
            row_order = (int(row["slot"]), int(row["canonical_transaction_index"] or 0))
            if row["signature"] != create["signature"] and row_order <= create_order:
                continue
            for event in row["trade_events"]:
                side = "buy" if event["is_buy"] else "sell"
                source_matches = [source for source in feed_rows.get(mint, []) if source_signature(source) == row["signature"] and source.get("side") == side]
                receipt = min((extract_receive_ms(source) for source in source_matches if extract_receive_ms(source) is not None), default=None)
                if receipt is None:
                    slot_delta = max(0, int(row["slot"]) - int(create["slot"]))
                    block_delta = max(0, int(row["block_time_unix"]) - int(create["block_time_unix"]))
                    receipt = create_receipt + (block_delta * 1000 if block_delta else max(1, slot_delta * 350))
                wallet = source_matches[0].get("wallet") if source_matches and source_matches[0].get("wallet") else event["user"]
                flow = {"ts_ms": receipt, "slot": row["slot"], "tx_index": row["canonical_transaction_index"] or 0,
                        "ix_index": 0, "signature": row["signature"], "mint": mint, "side": side,
                        "wallet": wallet, "quote_lamports": event["sol_amount_lamports"] if side == "buy" else 0,
                        "sell_token_raw": event["token_amount_raw"] if side == "sell" else 0,
                        "event": event, "timing_source": "normalized_local_receipt_ms" if source_matches else "chain_order_with_block_second_and_350ms_per_slot_estimate"}
                activity.append(flow)
                if wallet == TARGET and side == "buy" and row["signature"] != create["signature"]:
                    create["separate_target_buy_within_120s"] = True
        ten_flows[mint] = sorted(activity, key=lambda row: (row["ts_ms"], row["slot"], row["tx_index"]))
        create["other_wallet_atomic_buy_in_transaction"] = any(row["signature"] == create["signature"] and row.get("wallet") not in {None, TARGET} and row["side"] == "buy" for row in activity)
        ten.append(create)
    write_json(output / "ten_createv2_transactions.json", ten)
    with gzip.open(output / "ten_successful_activity_raw.json.gz", "wt", encoding="utf-8", compresslevel=6) as handle:
        json.dump(all_activity_raw, handle, separators=(",", ":"), sort_keys=True)

    # Exact ten-launch flow, recurrent participation, and curve path.
    ten_launches = []
    flow_details = []
    for mint, create in zip(MINTS, ten):
        create_source = next((row for row in feed_rows.get(mint, []) if row.get("side") == "create" and source_signature(row) == create["signature"]), {})
        signal = extract_receive_ms(create_source) or int(create["block_time_unix"] * 1000)
        launch = {"cohort": "B_TEN_CREATE_ONLY", "mint": mint, "create_signature": create["signature"],
                  "signal_ts_ms": signal, "initial_vsol": INITIAL_VSOL, "initial_vtok": INITIAL_VTOK}
        ten_launches.append(launch)
        flows = ten_flows[mint]
        successful_buys = [row for row in flows if row["side"] == "buy" and row["signature"] != create["signature"] and row.get("wallet") != TARGET]
        successful_buys.sort(key=lambda row: (row["ts_ms"], row["slot"], row["tx_index"]))
        current = (INITIAL_VSOL, INITIAL_VTOK)
        checkpoints = {}
        path = [{"latency_ms": 0, "virtual_sol_lamports": current[0], "virtual_token_raw": current[1], "market_cap_sol": market_cap_sol(current)}]
        for flow in flows:
            if flow["signature"] == create["signature"]:
                continue
            event = flow["event"]
            current = (event["virtual_sol_reserves_post_lamports"], event["virtual_token_reserves_post_raw"])
            path.append({"latency_ms": flow["ts_ms"] - signal, "signature": flow["signature"], "side": flow["side"], "wallet": flow.get("wallet"),
                         "timing_source": flow["timing_source"],
                         "virtual_sol_lamports": current[0], "virtual_token_raw": current[1], "market_cap_sol": market_cap_sol(current)})
        for window in [100, 250, 500, 1000, 2000, 5000, 10000]:
            eligible = [row for row in path if row["latency_ms"] <= window]
            checkpoints[str(window)] = eligible[-1]
        values = [row["market_cap_sol"] for row in path]
        base_mc = values[0]
        recurrent_rows = [row for row in successful_buys if row.get("wallet") in recurrent]
        windows = {}
        for window in [100, 250, 500, 1000, 2000, 5000]:
            subset = [row for row in successful_buys if row["ts_ms"] - signal <= window]
            windows[str(window)] = {"external_buy_sol": sum(row["quote_lamports"] for row in subset) / 1e9,
                                    "recurrent_wallets": sorted({row["wallet"] for row in subset if row.get("wallet") in recurrent}),
                                    "recurrent_sol": sum(row["quote_lamports"] for row in subset if row.get("wallet") in recurrent) / 1e9}
        thresholds = {}
        for threshold in [20, 50, 100]:
            hit = next((row["latency_ms"] for row in path if 100 * (row["market_cap_sol"] / base_mc - 1) >= threshold), None)
            thresholds[f"time_to_{threshold}pct_ms"] = hit
        flow_details.append({"mint": mint, "create_signature": create["signature"], "create_signal_ts_ms": signal, "create_signal_utc": iso_ms(signal),
                             "first_successful_external_buyer_latency_ms": successful_buys[0]["ts_ms"] - signal if successful_buys else None,
                             "first_successful_external_buyer_timing_source": successful_buys[0]["timing_source"] if successful_buys else None,
                             "first_five_successful_buyers": [{"latency_ms": row["ts_ms"] - signal, "timing_source": row["timing_source"], "wallet": row.get("wallet"), "sol": row["quote_lamports"] / 1e9, "signature": row["signature"]} for row in successful_buys[:5]],
                             "successful_trade_events_120s": [{"latency_ms": row["ts_ms"] - signal, "timing_source": row["timing_source"], "wallet": row.get("wallet"), "side": row["side"], "sol_amount_lamports": row["event"]["sol_amount_lamports"], "token_amount_raw": row["event"]["token_amount_raw"], "signature": row["signature"], "slot": row["slot"], "canonical_transaction_index": row["tx_index"]} for row in flows],
                             "windows": windows, "market_curve_at_create": path[0], "market_curve_checkpoints": checkpoints,
                             "mfe_curve_appreciation_pct_120s": 100 * (max(values) / base_mc - 1),
                             "mae_curve_appreciation_pct_120s": 100 * (min(values) / base_mc - 1),
                             "ath_curve_appreciation_pct_120s": 100 * (max(values) / base_mc - 1), **thresholds,
                             "successful_trade_count_120s": len(flows), "recurrent_wallet_count_1s": len({row.get("wallet") for row in recurrent_rows if row["ts_ms"] - signal <= 1000}),
                             "recurrent_sol_1s": sum(row["quote_lamports"] for row in recurrent_rows if row["ts_ms"] - signal <= 1000) / 1e9})
    write_json(output / "ten_successful_flow_and_curve.json", flow_details)

    ten_outcomes = [simulate(launch, ten_flows[launch["mint"]], delay, sellable, stress)
                    for launch in ten_launches for delay in DELAYS for sellable in SELLABLE for stress in [1.0, 1.05, 1.10]]
    with gzip.open(output / "ten_direct_copy_outcomes.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in ten_outcomes:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    write_json(output / "ten_direct_copy_grid.json", summarize_grid(ten_outcomes))

    # Historical A/B retained stream, with explicit finalized status gating.
    a_all = load_gz(v2 / "target_launches.jsonl.gz")
    b_all = load_gz(v2 / "ignored_control_launches.jsonl.gz")
    v2_native_summary = json.loads((v2 / "native_ledger_summary.json").read_text())
    ambiguous_path = v2 / "ambiguous_launches.jsonl.gz"
    ambiguous_count = len(load_gz(ambiguous_path)) if ambiguous_path.exists() else int(v2_native_summary["coverage"]["ambiguous_multi_asset_creates"])
    stream_targets = load_gz(v2 / "stream_target_outcomes.jsonl.gz")
    stream_target_by_mint = {row["mint"]: row for row in stream_targets}
    a_by_mint = {row["mint"]: row for row in a_all}
    selected_mints = set(stream_target_by_mint) | {row["mint"] for row in b_all}
    historical_by_mint, historical_scan = load_historical_stream(v3 / "all_launch_stream_120s.tsv", selected_mints)
    status, status_summary = rpc_statuses(rpc, historical_by_mint, output / "historical_signature_status_cache.json")
    successful_by_mint = {mint: [row for row in rows if status.get(row["signature"]) == "SUCCESS"] for mint, rows in historical_by_mint.items()}
    snapshot = json.loads((v2 / "stream_snapshot.json").read_text())
    snapshot_end = int(snapshot["snapshot_last_observation_ts_ms"])
    a_launches = []
    for mint, stream in stream_target_by_mint.items():
        source = a_by_mint[mint]
        signal = int(stream["target_ts_ms"])
        if signal + 120_000 > snapshot_end:
            continue
        a_launches.append({"cohort": "A_CREATE_PLUS_ATOMIC_TARGET_BUY", "mint": mint,
                           "create_signature": source["create_signature"], "signal_ts_ms": signal,
                           "initial_vsol": int(source["virtual_sol_reserves_after_lamports"]),
                           "initial_vtok": int(source["virtual_token_reserves_after_raw"]),
                           "create_at": source["create_at"]})
    b_launches = []
    b_by_mint = {row["mint"]: row for row in b_all}
    for mint, source in b_by_mint.items():
        creates = [row for row in successful_by_mint.get(mint, []) if row["side"] == "create" and row["signature"] == source["create_signature"]]
        if not creates:
            continue
        signal = creates[0]["ts_ms"]
        if signal + 120_000 > snapshot_end:
            continue
        b_launches.append({"cohort": "B_CREATEV2_ONLY", "mint": mint, "create_signature": source["create_signature"],
                           "signal_ts_ms": signal, "initial_vsol": INITIAL_VSOL, "initial_vtok": INITIAL_VTOK,
                           "create_at": source["create_at"]})
    a_outcomes = [simulate(row, successful_by_mint.get(row["mint"], []), delay, sellable, stress)
                  for row in a_launches for delay in DELAYS for sellable in SELLABLE for stress in [1.0, 1.05, 1.10]]
    b_outcomes = [simulate(row, successful_by_mint.get(row["mint"], []), delay, sellable, stress)
                  for row in b_launches for delay in DELAYS for sellable in SELLABLE for stress in [1.0, 1.05, 1.10]]
    target_trade_events = load_gz(v2 / "target_trade_events.jsonl.gz")
    target_sells_by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in target_trade_events:
        if event.get("side") == "sell" and event.get("is_pump_economic_event"):
            target_sells_by_mint[event["mint"]].append(event)

    def creator_sell_timing(launches: list[dict[str, Any]]) -> dict[str, Any]:
        values = []
        rows = []
        for launch in launches:
            create_ms = int((a_by_mint.get(launch["mint"]) or b_by_mint.get(launch["mint"]))["create_block_time"]) * 1000
            sell_ms = min((int(event["block_time"]) * 1000 for event in target_sells_by_mint.get(launch["mint"], []) if int(event["block_time"]) * 1000 >= create_ms), default=None)
            delay = sell_ms - create_ms if sell_ms is not None else None
            if delay is not None:
                values.append(delay)
            rows.append({"mint": launch["mint"], "first_target_sell_delay_ms_block_second": delay})
        return {"n": len(launches), "with_target_sell": len(values), "rate": len(values) / len(launches) if launches else None,
                "delay_ms": {"p50": quantile(values, .5), "p75": quantile(values, .75), "p90": quantile(values, .9)},
                "precision": "finalized block-time second", "launches": rows}

    write_json(output / "historical_ab_economics.json", {
        "evaluator": {"successful_finalized_only": True, "failed_and_unknown_signatures_mutate_state": False,
                      "pump_fee_bps_each_side": PUMP_FEE_BPS, "non_pump_cost_lamports": NON_PUMP_COST,
                      "position_size_lamports": PRINCIPAL, "tp_pct_gross": 20, "sl_pct_gross": -20, "hold_ms_from_entry": 120000,
                      "retained_stream_boundary": "The source tape ends 120 seconds after the signal; timeout valuation for delayed lanes therefore has up to delay_ms of unavailable tail."},
        "stream_scan": historical_scan, "signature_status": status_summary,
        "A": {"launches": len(a_launches), "grid": summarize_grid(a_outcomes), "external": external_stats(a_launches, successful_by_mint, recurrent),
              "creator_sell_timing": creator_sell_timing(a_launches), "chronological_primary_p75_25ms": chronological_primary(a_launches, a_outcomes)},
        "B": {"launches": len(b_launches), "grid": summarize_grid(b_outcomes), "external": external_stats(b_launches, successful_by_mint, recurrent),
              "creator_sell_timing": creator_sell_timing(b_launches), "chronological_primary_p75_25ms": chronological_primary(b_launches, b_outcomes)},
    })

    # Chronological distribution and burst clustering across the complete V2 launch census.
    census = sorted([{"class": "A", **row} for row in a_all] + [{"class": "B", **row} for row in b_all], key=lambda row: row["create_block_time"])
    splits = []
    bounds = [("first_50pct", 0, .5), ("middle_25pct", .5, .75), ("final_25pct", .75, 1)]
    for label, lo, hi in bounds:
        rows = census[round(len(census) * lo):round(len(census) * hi)]
        splits.append({"split": label, "n": len(rows), "A": sum(row["class"] == "A" for row in rows), "B": sum(row["class"] == "B" for row in rows),
                       "B_rate": sum(row["class"] == "B" for row in rows) / len(rows), "start": rows[0]["create_at"], "end": rows[-1]["create_at"]})
    b_times = sorted(int(row["create_block_time"]) for row in b_all)
    burst_summary = {}
    for threshold in [1, 10, 60, 120, 300]:
        groups = []
        current = [b_times[0]] if b_times else []
        for value in b_times[1:]:
            if value - current[-1] <= threshold:
                current.append(value)
            else:
                groups.append(current); current = [value]
        if current: groups.append(current)
        multi = [row for row in groups if len(row) >= 2]
        longest = max(groups, key=len) if groups else []
        burst_summary[str(threshold)] = {"groups_with_2plus": len(multi), "events_in_2plus_groups": sum(map(len, multi)),
                                         "largest_group_n": len(longest), "largest_group_duration_s": longest[-1] - longest[0] if longest else None,
                                         "largest_group_start_utc": dt.datetime.fromtimestamp(longest[0], dt.timezone.utc).isoformat().replace("+00:00", "Z") if longest else None}
    historical = {"window": {"requested_start": "2026-08-03T23:20:05Z", "requested_end": "2026-09-02T23:20:05Z",
                              "first_successful_target_tx": "2026-08-03T23:21:09Z", "last_successful_target_tx": "2026-09-02T21:49:24Z"},
                  "A_count": len(a_all), "B_count": len(b_all), "ambiguous_count": ambiguous_count, "classified_total": len(census),
                  "A_frequency": len(a_all) / len(census), "B_frequency": len(b_all) / len(census),
                  "chronological_splits": splits, "B_burst_clustering": burst_summary,
                  "retained_flow_sample": {"A": len(a_launches), "B": len(b_launches)}}
    write_json(output / "historical_createv2_census.json", historical)

    # Detection timing: direct burst fixtures and current prospective atomic fixtures.
    direct_latencies = []
    for mint, rows in feed_rows.items():
        for row in rows:
            if row.get("side") != "create":
                continue
            receipt = extract_receive_ms(row)
            decoded = next((int(row[key]) for key in ["decode_complete_ts_ms", "decode_complete_ms", "decoded_at_ms"] if isinstance(row.get(key), (int, float))), receipt)
            if receipt is not None:
                direct_latencies.append({"mint": mint, "create_receipt_ms": receipt, "creator_signature_verification_ms": decoded,
                                         "direct_signal_ready_ms": decoded, "receipt_to_ready_ms": decoded - receipt})
    prospective_signals = load_gz(prospective / "signal_events.jsonl.gz")
    atomic_values = []
    for row in prospective_signals:
        timing = row.get("timing") or {}
        value = timing.get("total_observable_detection_ms")
        if isinstance(value, (int, float)):
            atomic_values.append(float(value))
    direct_values = [row["receipt_to_ready_ms"] for row in direct_latencies]
    detection = {"direct_create_only_fixtures": direct_latencies,
                 "direct_receipt_to_signal_ready_ms": {"n": len(direct_values), "p50": quantile(direct_values, .5), "p75": quantile(direct_values, .75), "p90": quantile(direct_values, .9), "max": max(direct_values) if direct_values else None},
                 "current_atomic_create_receipt_to_signal_ready_ms": {"n": len(atomic_values), "p50": quantile(atomic_values, .5), "p75": quantile(atomic_values, .75), "p90": quantile(atomic_values, .9), "max": max(atomic_values) if atomic_values else None},
                 "interpretation": "Direct verification is inline in the normalized successful CreateV2 row; no target-buy pairing wait is required. Millisecond values are observer processing latency, not chain finality."}
    write_json(output / "detection_latency.json", detection)

    summary = {"schema": "8ycb-direct-create-copy-audit-v1", "generated_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
               "classification": "8YCB_ATOMIC_BUY_FILTER_REMAINS_ECONOMICALLY_IMPORTANT",
               "ten_genuine_8ycb_launches": sum(row["classification"] == "GENUINE_8YCB_TOKEN_LAUNCH" for row in ten),
               "ten_with_successful_external_pump_buy": sum(row["first_successful_external_buyer_latency_ms"] is not None for row in flow_details),
               "ten_with_recurrent_wallet_1s": sum(row["recurrent_wallet_count_1s"] > 0 for row in flow_details),
               "ten_primary_p75_25ms": summarize_grid(ten_outcomes)["25"]["178"],
               "historical_primary_p75_25ms": {"A": summarize_grid(a_outcomes)["25"]["178"], "B": summarize_grid(b_outcomes)["25"]["178"]},
               "source_hashes": {"v2_validation_manifest": sha256_file(v2 / "validation_manifest.json"),
                                 "frozen_recurrent_wallets": sha256_file(prospective / "v3_recurrent_wallets.json"),
                                 "v3_compact_tape": sha256_file(v3 / "all_launch_stream_120s.tsv")},
               "limitations": [
                   "The ten-launch burst is exact finalized chain reconstruction; local millisecond receipt timing comes from the existing normalized feed.",
                   "Historical economics cover the 5.42-day retained stream overlap, not all 2,250 classified launches.",
                   "Historical failed and unknown signatures are excluded. The compact tape has no status column and RPC status history is therefore an external validation dependency.",
                   "Historical delayed-lane timeout valuation lacks at most delay_ms of tail because the retained tape ends 120 seconds after signal.",
                   "Curve replay is a constant-product research reconstruction and includes frozen Pump and non-Pump cost assumptions; it is not submitted-execution telemetry.",
                   "PF is undefined when a sample has no losing outcomes; this is represented as null, not infinity.",
               ]}
    write_json(output / "summary.json", summary)
    print(json.dumps({"status": "PASS", "output": str(output), "classification": summary["classification"], "ten_genuine": summary["ten_genuine_8ycb_launches"], "historical_A": len(a_launches), "historical_B": len(b_launches)}, sort_keys=True))


if __name__ == "__main__":
    main()
