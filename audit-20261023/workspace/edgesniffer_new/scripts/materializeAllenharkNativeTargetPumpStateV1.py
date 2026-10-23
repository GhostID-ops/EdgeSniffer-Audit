#!/usr/bin/env python3
"""Materialize causal Pump state from the native target transaction tape.

This is deliberately fail-closed.  A target receipt clock is not enough: a
row is emitted only when the native transaction tape identifies a canonical
Pump instruction, a pre-receipt native curve account snapshot contains all
four reserves, and the fee schedule is present in that native snapshot.  No
RPC, finalized event, or post-finality value is used to manufacture native
state.
"""
from __future__ import annotations

import argparse
import base64
from bisect import bisect_right
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_EXECUTOR_PROGRAM_ID = "2eFuLSK4McKonBcB6XLax8gSgxwR56zNfrjHsvkNkftj"
SCHEMA = "ALLENHARK_NATIVE_TARGET_PUMP_STATE_V1"
ROUTE_CANDIDATE_SCHEMA = "ALLENHARK_NATIVE_TARGET_PUMP_CPI_CANDIDATE_V1"

# The materializer is refreshed repeatedly.  Keep the per-curve observation
# times beside the rows so causal lookup does not rescan a curve's complete
# history for every transaction.
_SEED_OBSERVED_INDEX: dict[str, list[int]] = {}


def load_curve_module() -> Any:
    path = Path(__file__).with_name("exactCurveEconomicsV1.py")
    spec = importlib.util.spec_from_file_location("exact_curve_native_target", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


curve = load_curve_module()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def number(row: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = row.get(name)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def seed_reserves(seed: dict[str, Any]) -> curve.CurveState | None:
    virtual_sol = number(seed, "virtualSolReserves", "virtual_sol_reserves", "virtual_sol")
    virtual_token = number(seed, "virtualTokenReserves", "virtual_token_reserves", "virtual_token")
    real_sol = number(seed, "realSolReserves", "real_sol_reserves", "real_sol")
    real_token = number(seed, "realTokenReserves", "real_token_reserves", "real_token")
    if None in (virtual_sol, virtual_token, real_sol, real_token):
        return None
    state = curve.CurveState(virtual_sol, virtual_token, real_sol, real_token)
    state.validate()
    return state


def seed_fees(seed: dict[str, Any]) -> curve.Fees | None:
    nested = seed.get("fees") if isinstance(seed.get("fees"), dict) else {}
    protocol = number(seed, "protocolFeeBps", "protocol_fee_bps")
    creator = number(seed, "creatorFeeBps", "creator_fee_bps")
    lp = number(seed, "lpFeeBps", "lp_fee_bps")
    if protocol is None:
        protocol = number(nested, "protocolBps", "protocol_bps")
    if creator is None:
        creator = number(nested, "creatorBps", "creator_bps")
    if lp is None:
        lp = number(nested, "lpBps", "lp_bps")
    if protocol is None or creator is None:
        return None
    return curve.Fees(protocol_bps=protocol, creator_bps=creator, lp_bps=lp or 0)


def load_seeds(path: Path) -> dict[str, list[dict[str, Any]]]:
    _SEED_OBSERVED_INDEX.clear()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in read_jsonl(path):
        curve_id = str(row.get("bondingCurve") or row.get("bonding_curve") or "")
        if not curve_id:
            continue
        result.setdefault(curve_id, []).append(row)
    for curve_id in result:
        result[curve_id].sort(key=lambda row: (number(row, "observedAtMs", "observed_at_ms") or 0, number(row, "slot") or 0))
        _SEED_OBSERVED_INDEX[curve_id] = [
            number(row, "observedAtMs", "observed_at_ms") or 0
            for row in result[curve_id]
        ]
    return result


def load_fee_configs(path: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if path is None or not path.exists():
        return result
    for row in read_jsonl(path):
        address = str(row.get("feeConfig") or "")
        encoded = row.get("accountDataBase64")
        if not address or not isinstance(encoded, str):
            continue
        try:
            result[address] = (curve.decode_fee_config(address, base64.b64decode(encoded)), row)
        except Exception:
            continue
    return result


def latest_causal_seed(
    seed_rows: list[dict[str, Any]],
    receive_ms: int,
    tx_slot: int,
    tx_signature: str = "",
    require_same_slot: bool = False,
    require_native_seed_lineage: bool = False,
    receive_ns: int | None = None,
) -> dict[str, Any] | None:
    if not seed_rows:
        return None
    observed_index = _SEED_OBSERVED_INDEX.get(str(seed_rows[0].get("bondingCurve") or seed_rows[0].get("bonding_curve") or ""))
    upper = bisect_right(observed_index, receive_ms) if observed_index is not None else len(seed_rows)
    for row in reversed(seed_rows[:upper]):
        observed = number(row, "observedAtMs", "observed_at_ms")
        if observed is None or observed > receive_ms:
            continue
        seed_receive_ns = number(row, "native_receive_unix_ns", "nativeReceiveUnixNs")
        if receive_ns is not None and seed_receive_ns is not None and seed_receive_ns > receive_ns:
            # Millisecond provider timestamps are only an index. When both
            # sides have native clocks, use nanoseconds to reject an account
            # update received later in the same millisecond.
            continue
        slot = number(row, "slot") or 0
        if require_same_slot and tx_slot and slot != tx_slot:
            continue
        if require_native_seed_lineage:
            native_receive_unix_ns = number(
                row, "native_receive_unix_ns", "nativeReceiveUnixNs"
            )
            native_receive_monotonic_ns = number(
                row, "native_receive_monotonic_ns", "nativeReceiveMonotonicNs"
            )
            native_receive_source = row.get("native_receive_source") or row.get(
                "nativeReceiveSource"
            )
            account_txn_signature = row.get("account_txn_signature") or row.get(
                "accountTxnSignature"
            )
            write_version = number(row, "write_version", "writeVersion")
            if (
                native_receive_unix_ns is None
                or native_receive_monotonic_ns is None
                or not native_receive_source
                or not account_txn_signature
                or write_version is None
            ):
                continue
        account_txn_signature = str(
            row.get("accountTxnSignature")
            or row.get("account_txn_signature")
            or ""
        )
        # An account notification carrying the evaluated transaction's own
        # signature is the post-transaction write. It cannot be the causal
        # pre-state for that transaction, even when provider clocks are coarse.
        if tx_signature and account_txn_signature == tx_signature:
            continue
        # Same-slot account updates can be the actual pre-trade state. The
        # native receive clock is the ordering authority within a slot; only
        # strictly future-slot observations are impossible causally.
        if tx_slot and slot and slot > tx_slot:
            continue
        return row
    return None


def materialize(
    transactions_path: Path,
    seeds_path: Path,
    output: Path,
    report_path: Path,
    route_candidates_output: Path | None = None,
    fee_config_snapshots_path: Path | None = None,
    min_native_receive_unix_ns: int | None = None,
    require_same_slot_seed: bool = False,
    require_native_seed_lineage: bool = False,
) -> dict[str, Any]:
    transactions = read_jsonl(transactions_path)
    seeds = load_seeds(seeds_path)
    fee_configs = load_fee_configs(fee_config_snapshots_path)
    output_rows: list[dict[str, Any]] = []
    route_candidate_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    stats = {
        "native_transactions": len(transactions),
        "pump_instructions": 0,
        "emitted_rows": 0,
        "skipped_non_pump": 0,
        "skipped_decode": 0,
        "skipped_curve_ambiguity": 0,
        "skipped_no_causal_seed": 0,
        "skipped_incomplete_reserves": 0,
        "skipped_missing_fee_schedule": 0,
        "skipped_non_exact_input": 0,
        "duplicate_rows": 0,
        "executor_cpi_candidates": 0,
        "executor_cpi_unique_curve_candidates": 0,
        "executor_cpi_unresolved_curve": 0,
        "used_fee_config_snapshot": 0,
        "skipped_before_receive_cutoff": 0,
    }
    for tx in transactions:
        signature = str(tx.get("signature") or "")
        if not signature:
            continue
        receive_ns = number(tx, "same_source_receive_unix_ns", "native_receive_unix_ns")
        if min_native_receive_unix_ns is not None and (
            receive_ns is None or receive_ns < min_native_receive_unix_ns
        ):
            stats["skipped_before_receive_cutoff"] += 1
            continue
        receive_ms = receive_ns // 1_000_000 if receive_ns is not None else None
        tx_slot = number(tx, "same_source_slot", "slot", "native_receive_slot") or 0
        keys = [str(value) for value in tx.get("account_keys") or []]
        for ix_index, instruction in enumerate(tx.get("instructions") or []):
            program_id = instruction.get("program_id")
            if isinstance(program_id, dict):
                program_id = program_id.get("Some") or program_id.get("value")
            indices = [int(value) for value in instruction.get("accounts") or []]
            accounts = [keys[index] for index in indices if 0 <= index < len(keys)]
            if program_id == PUMP_EXECUTOR_PROGRAM_ID and PUMP_PROGRAM_ID in accounts:
                curve_candidates = sorted({account for account in accounts if account in seeds})
                stats["executor_cpi_candidates"] += 1
                if len(curve_candidates) == 1:
                    stats["executor_cpi_unique_curve_candidates"] += 1
                    route_candidate_rows.append({
                        "schema": ROUTE_CANDIDATE_SCHEMA,
                        "signature": signature,
                        "instruction_index": ix_index,
                        "slot": tx_slot,
                        "wallet": tx.get("wallet"),
                        "native_receive_unix_ns": receive_ns,
                        "native_receive_monotonic_ns": tx.get("native_receive_monotonic_ns"),
                        "native_receive_source": tx.get("native_receive_source"),
                        "same_source_receive_unix_ns": tx.get("same_source_receive_unix_ns"),
                        "same_source_receive_monotonic_ns": tx.get("same_source_receive_monotonic_ns"),
                        "same_source_receive_source": tx.get("same_source_receive_source"),
                        "same_source_slot": tx.get("same_source_slot"),
                        "same_source_transaction_index": tx.get("same_source_transaction_index"),
                        "causal_ordering_source": tx.get("causal_ordering_source"),
                        "route": "PUMP_CPI_EXECUTOR",
                        "executor_program_id": program_id,
                        "pump_program_id": PUMP_PROGRAM_ID,
                        "curve_candidates": curve_candidates,
                        "source_accounts": accounts,
                        "instruction_data_hex": str(instruction.get("data_hex") or ""),
                        "state_available": False,
                        "state_source": "NATIVE_TRANSACTION_ONLY_PENDING_ACCOUNT_STATE",
                    })
                else:
                    stats["executor_cpi_unresolved_curve"] += 1
                continue
            if program_id != PUMP_PROGRAM_ID:
                stats["skipped_non_pump"] += 1
                continue
            stats["pump_instructions"] += 1
            try:
                payload = bytes.fromhex(str(instruction.get("data_hex") or ""))
                decoded = curve.decode_pump_instruction(payload)
            except Exception:
                stats["skipped_decode"] += 1
                continue
            identity = (signature, ix_index)
            if identity in seen:
                stats["duplicate_rows"] += 1
                continue
            seen.add(identity)
            mint_index = 1 if decoded.kind.endswith(("_v2", "_v3")) else 2
            mint = accounts[mint_index] if len(accounts) > mint_index else None
            curve_candidates = [account for account in accounts if account in seeds]
            if len(set(curve_candidates)) != 1:
                stats["skipped_curve_ambiguity"] += 1
                continue
            curve_id = curve_candidates[0]
            if receive_ms is None:
                stats["skipped_no_causal_seed"] += 1
                continue
            seed = latest_causal_seed(
                seeds[curve_id],
                receive_ms,
                tx_slot,
                signature,
                require_same_slot_seed,
                require_native_seed_lineage,
                receive_ns,
            )
            if seed is None:
                stats["skipped_no_causal_seed"] += 1
                continue
            state = seed_reserves(seed)
            if state is None:
                stats["skipped_incomplete_reserves"] += 1
                continue
            fees = seed_fees(seed)
            fee_config_address = next((account for account in accounts if account in fee_configs), None)
            fee_config_snapshot = fee_configs.get(fee_config_address) if fee_config_address else None
            if fees is None and fee_config_snapshot is not None:
                fee_config, _snapshot_row = fee_config_snapshot
                mint_supply = number(seed, "tokenTotalSupply", "token_total_supply") or 1_000_000_000_000_000
                fees = fee_config.fees_for(state, mint_supply)
                stats["used_fee_config_snapshot"] += 1
            if fees is None:
                stats["skipped_missing_fee_schedule"] += 1
                continue
            if not decoded.exact_quote_in and decoded.side == "buy":
                stats["skipped_non_exact_input"] += 1
                continue
            try:
                quote = curve.apply_instruction(state, decoded, fees)
            except Exception:
                stats["skipped_decode"] += 1
                continue
            post = quote.post_state
            output_rows.append({
                "schema": SCHEMA,
                "signature": signature,
                "instruction_index": ix_index,
                "slot": tx_slot,
                "wallet": tx.get("wallet"),
                "native_receive_unix_ns": receive_ns,
                "native_receive_monotonic_ns": tx.get("native_receive_monotonic_ns"),
                "native_receive_source": tx.get("native_receive_source"),
                "same_source_receive_unix_ns": tx.get("same_source_receive_unix_ns"),
                "same_source_receive_monotonic_ns": tx.get("same_source_receive_monotonic_ns"),
                "same_source_receive_source": tx.get("same_source_receive_source"),
                "same_source_slot": tx.get("same_source_slot"),
                "same_source_transaction_index": tx.get("same_source_transaction_index"),
                "causal_ordering_source": tx.get("causal_ordering_source"),
                "route": "PUMP_BONDING_CURVE_TOP_LEVEL",
                "side": decoded.side.upper(),
                "instruction_kind": decoded.kind,
                "mint": mint,
                "bonding_curve": curve_id,
                "source_accounts": accounts,
                "native_state": {
                    "pre_state": {"virtual_sol": state.virtual_sol, "virtual_token": state.virtual_token, "real_sol": state.real_sol, "real_token": state.real_token},
                    "post_state": {"virtual_sol": post.virtual_sol, "virtual_token": post.virtual_token, "real_sol": post.real_sol, "real_token": post.real_token},
                    "fee_basis_points": fees.total_bps,
                    "protocol_fee_basis_points": fees.protocol_bps,
                    "creator_fee_basis_points": fees.creator_bps,
                    "lp_fee_basis_points": fees.lp_bps,
                    "fee_config_address": fee_config_address,
                    "fee_config_source": "RPC_ACCOUNT_SNAPSHOT_NONCAUSAL_STATIC_CONFIG" if fee_config_snapshot else "NATIVE_SEED",
                    "state_source": "NATIVE_ACCOUNT_STATE_PLUS_NATIVE_TRANSACTION",
                    "seed_observed_at_ms": number(seed, "observedAtMs", "observed_at_ms"),
                    "seed_slot": number(seed, "slot") or 0,
                    "seed_native_receive_unix_ns": number(seed, "native_receive_unix_ns", "nativeReceiveUnixNs"),
                    "seed_native_receive_monotonic_ns": number(seed, "native_receive_monotonic_ns", "nativeReceiveMonotonicNs"),
                    "seed_native_receive_source": seed.get("native_receive_source") or seed.get("nativeReceiveSource"),
                    "seed_account_txn_signature": seed.get("account_txn_signature") or seed.get("accountTxnSignature"),
                    "seed_write_version": number(seed, "write_version", "writeVersion"),
                    "causal_ordering_source": tx.get("causal_ordering_source"),
                },
            })
            stats["emitted_rows"] += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in output_rows), encoding="utf-8")
    os.replace(temporary, output)
    if route_candidates_output is not None:
        route_candidates_output.parent.mkdir(parents=True, exist_ok=True)
        candidate_temporary = route_candidates_output.with_suffix(route_candidates_output.suffix + ".tmp")
        candidate_temporary.write_text(
            "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in route_candidate_rows),
            encoding="utf-8",
        )
        os.replace(candidate_temporary, route_candidates_output)
    report = {
        "schema": "ALLENHARK_NATIVE_TARGET_PUMP_STATE_AUDIT_V1",
        "generated_at_ms": int(time.time() * 1000),
        "read_only": True,
        "trading_enabled": False,
        "inputs": {
            "native_transactions": str(transactions_path),
            "native_seeds": str(seeds_path),
            "fee_config_snapshots": str(fee_config_snapshots_path) if fee_config_snapshots_path else None,
            "route_candidates_output": str(route_candidates_output) if route_candidates_output else None,
            "min_native_receive_unix_ns": min_native_receive_unix_ns,
            "require_same_slot_seed": require_same_slot_seed,
            "require_native_seed_lineage": require_native_seed_lineage,
        },
        "stats": stats,
        "output_rows": len(output_rows),
        "route_candidate_rows": len(route_candidate_rows),
        "parity_status": "NATIVE_STATE_ROWS_AVAILABLE" if output_rows else "BLOCKED_NATIVE_STATE_FIELDS_OR_INPUTS",
        "economics_input_allowed": False,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-transactions", type=Path, required=True)
    parser.add_argument("--native-seeds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--route-candidates-output", type=Path)
    parser.add_argument("--fee-config-snapshots", type=Path)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--min-native-receive-unix-ns", type=int)
    parser.add_argument("--require-same-slot-seed", action="store_true")
    parser.add_argument("--require-native-seed-lineage", action="store_true")
    args = parser.parse_args()
    while True:
        report = materialize(
            args.native_transactions,
            args.native_seeds,
            args.output,
            args.report,
            args.route_candidates_output,
            args.fee_config_snapshots,
            args.min_native_receive_unix_ns,
            args.require_same_slot_seed,
            args.require_native_seed_lineage,
        )
        print(json.dumps(report["stats"], sort_keys=True), flush=True)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
