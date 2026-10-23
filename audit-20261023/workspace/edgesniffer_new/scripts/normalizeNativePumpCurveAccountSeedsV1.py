#!/usr/bin/env python3
"""Normalize native Pump bonding-curve account observations into replay seeds."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any

EXPECTED_PUMPFUN_TOTAL_SUPPLY = 1_000_000_000_000_000

def u64_le(data: bytes, offset: int) -> int | None:
    if len(data) < offset + 8:
        return None
    return int.from_bytes(data[offset:offset + 8], "little")


def decode_account_data(value: Any) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None


def extract(row: dict[str, Any]) -> dict[str, Any] | None:
    curve = row.get("bondingCurve") or row.get("bonding_curve") or row.get("accountPubkey") or row.get("account")
    if isinstance(curve, dict):
        curve = curve.get("pubkey") or curve.get("address")
    curve = str(curve or "")
    if not curve:
        return None
    state = row.get("state") if isinstance(row.get("state"), dict) else row
    sol = state.get("virtualSolReserves", state.get("virtual_sol_reserves"))
    token = state.get("virtualTokenReserves", state.get("virtual_token_reserves"))
    complete = state.get("complete")
    if complete is True:
        return None
    total_supply = state.get("tokenTotalSupply", state.get("token_total_supply"))
    if row.get("schema") == "NATIVE_PUMP_CURVE_ACCOUNT_SEED_V1" and total_supply is None:
        return None
    if total_supply is not None:
        try:
            if int(total_supply) != EXPECTED_PUMPFUN_TOTAL_SUPPLY:
                return None
        except (TypeError, ValueError):
            return None
    if sol is None or token is None:
        account = row.get("account") if isinstance(row.get("account"), dict) else row
        data = decode_account_data(account.get("data") or row.get("data"))
        if data is None:
            return None
        sol = u64_le(data, 16)
        token = u64_le(data, 8)
        decoded_real_token = u64_le(data, 24)
        decoded_real_sol = u64_le(data, 32)
        if state.get("realTokenReserves") is None and decoded_real_token is not None:
            state = {**state, "realTokenReserves": decoded_real_token}
        if state.get("realSolReserves") is None and decoded_real_sol is not None:
            state = {**state, "realSolReserves": decoded_real_sol}
        complete = bool(data[48]) if len(data) > 48 else False
    try:
        sol, token = int(sol), int(token)
    except (TypeError, ValueError):
        return None
    if sol <= 0 or token <= 0:
        return None
    receive_ns = row.get("nativeReceiveUnixNs", row.get("native_receive_unix_ns"))
    try:
        receive_ns = int(receive_ns) if receive_ns is not None else None
    except (TypeError, ValueError):
        receive_ns = None
    observed = row.get("observedAtMs", row.get("observed_at_ms", row.get("local_receive_ts_ms", row.get("atMs"))))
    if receive_ns is not None and receive_ns > 0:
        observed = receive_ns // 1_000_000
    try:
        observed = int(observed)
    except (TypeError, ValueError):
        return None
    result = {"schema": "NATIVE_PUMP_CURVE_ACCOUNT_SEED_V1", "bondingCurve": curve,
              "virtualSolReserves": sol, "virtualTokenReserves": token,
              "complete": bool(complete), "observedAtMs": observed,
              "slot": int(row.get("slot") or row.get("source_slot") or 0),
              "source": "native-account-state"}
    # Preserve ingestion lineage.  These fields are evidence about when the
    # account update reached this process; they are not substituted for a
    # transaction's receive clock and do not by themselves certify pre-state.
    if receive_ns is not None:
        result["native_receive_unix_ns"] = receive_ns
    for output_name, input_names in (
        ("native_receive_monotonic_ns", ("nativeReceiveMonotonicNs", "native_receive_monotonic_ns")),
        ("native_receive_source", ("nativeReceiveSource", "native_receive_source")),
        ("account_txn_signature", ("accountTxnSignature", "account_txn_signature")),
        ("write_version", ("writeVersion", "write_version")),
        ("server_created_at", ("serverCreatedAt", "server_created_at")),
    ):
        value = next((row.get(name) for name in input_names if row.get(name) is not None), None)
        if value is not None:
            result[output_name] = value
    # Preserve the real reserves when the upstream account observer supplies
    # them. Older rows remain valid virtual-only seeds and are not upgraded by
    # inference.
    for output_name, input_names in (
        ("realSolReserves", ("realSolReserves", "real_sol_reserves", "real_sol")),
        ("realTokenReserves", ("realTokenReserves", "real_token_reserves", "real_token")),
    ):
        value = next((state.get(name) for name in input_names if state.get(name) is not None), None)
        if value is not None:
            try:
                result[output_name] = int(value)
            except (TypeError, ValueError):
                return None
    if total_supply is not None:
        try:
            result["tokenTotalSupply"] = int(total_supply)
        except (TypeError, ValueError):
            return None
    return result


def run(input_path: Path, output_path: Path) -> dict[str, int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_seeds: dict[tuple[Any, ...], dict[str, Any]] = {}
    stats = {"read": 0, "accepted": 0, "rejected": 0, "duplicates": 0}
    with input_path.open(encoding="utf-8") as source:
        for line in source:
            stats["read"] += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                stats["rejected"] += 1
                continue
            seed = extract(row)
            if not seed:
                stats["rejected"] += 1
                continue
            key = (seed["bondingCurve"], seed["observedAtMs"], seed["slot"],
                   seed["virtualSolReserves"], seed["virtualTokenReserves"])
            if key in all_seeds:
                stats["duplicates"] += 1
                continue
            all_seeds[key] = seed
            stats["accepted"] += 1
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as output:
        for seed in sorted(all_seeds.values(), key=lambda row: (row["observedAtMs"], row["bondingCurve"])):
            output.write(json.dumps(seed, sort_keys=True) + "\n")
    temp_path.replace(output_path)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.input, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
