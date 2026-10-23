#!/usr/bin/env python3
"""Build causal Pump curve reserve state from native ShredStream observations.

This is a receive-only research materializer. It only emits state after a
creation seed has been observed, preserves source order, and never fetches
RPC state or submits transactions. It is intentionally separate from the
finalized exact-state bridge so delayed finality cannot be mistaken for the
native entry state.
"""
from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path
from typing import Any

INITIAL_SOL = 30_000_000_000
INITIAL_TOKEN = 1_073_000_000_000_000
FEE_BPS = 125
MAX_CURVES = 5000

BUY_DISCRIMINATOR = bytes([102, 6, 61, 18, 1, 218, 235, 234])
BUY_EXACT_SOL_IN_DISCRIMINATOR = bytes([56, 252, 116, 8, 158, 223, 205, 95])
BUY_V2_DISCRIMINATOR = bytes([184, 23, 238, 97, 103, 197, 211, 61])
BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR = bytes([194, 171, 28, 70, 104, 77, 91, 47])
BUY_V3_DISCRIMINATOR = bytes([7, 5, 29, 196, 245, 23, 101, 80])
BUY_EXACT_QUOTE_IN_V3_DISCRIMINATOR = bytes([225, 247, 80, 30, 213, 179, 132, 136])

BUY_QUOTE_SEMANTICS = {
    BUY_DISCRIMINATOR: ("buy", "MAX_SOL_COST_INTENT"),
    BUY_EXACT_SOL_IN_DISCRIMINATOR: ("buy_exact_sol_in", "EXACT_SOL_IN_INTENT"),
    BUY_V2_DISCRIMINATOR: ("buy_v2", "MAX_SOL_COST_INTENT"),
    BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR: ("buy_exact_quote_in_v2", "EXACT_QUOTE_IN_INTENT"),
    BUY_V3_DISCRIMINATOR: ("buy_v3", "MAX_SOL_COST_INTENT"),
    BUY_EXACT_QUOTE_IN_V3_DISCRIMINATOR: ("buy_exact_quote_in_v3", "EXACT_QUOTE_IN_INTENT"),
}


def source_fields(row: dict[str, Any]) -> tuple[dict[str, Any], str, str, str, int, int, int]:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    curve = str(raw.get("bondingCurve") or extra.get("bondingCurve") or "")
    mint = str(row.get("mint") or raw.get("mint") or "")
    side = str(row.get("side") or raw.get("side") or "").lower()
    slot = int(row.get("source_slot") or raw.get("slot") or 0)
    tx_index = int(row.get("source_transaction_index") or raw.get("transactionIndex") or 0)
    ix_index = int(raw.get("instructionIndex") or 0)
    return raw, curve, mint, side, slot, tx_index, ix_index


def execution_status(row: dict[str, Any], raw: dict[str, Any] | None = None) -> str:
    """Classify execution without treating an intent or budget as a fill."""
    raw = raw if raw is not None else (row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {})
    execution = row.get("execution") if isinstance(row.get("execution"), dict) else raw.get("execution")
    if not isinstance(execution, dict) or "success" not in execution:
        return "UNKNOWN"
    if execution.get("success") is True:
        return "SUCCESS"
    return "FAILED" if execution.get("success") is False else "UNKNOWN"


def quote_semantics(row: dict[str, Any], raw: dict[str, Any] | None = None) -> tuple[str, str]:
    """Return the instruction variant and whether its quote is a limit or input.

    A Pump buy's ordinary `maxSolCost` is not an executed spend. Exact-input
    variants carry an intended input amount, but without success telemetry they
    still remain conditional rather than authoritative.
    """
    raw = raw if raw is not None else (row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {})
    explicit = row.get("quote_semantics") or raw.get("quoteSemantics") or raw.get("quote_semantics")
    variant = row.get("native_instruction_variant") or raw.get("nativeInstructionVariant")
    if explicit:
        normalized = str(explicit).strip().upper()
        aliases = {
            "MAX_SOL_COST_INTENT": "MAX_SOL_COST_INTENT",
            "MAX_SOL_COST": "MAX_SOL_COST_INTENT",
            "EXACT_SOL_IN_INTENT": "EXACT_SOL_IN_INTENT",
            "EXACT_QUOTE_IN_INTENT": "EXACT_QUOTE_IN_INTENT",
        }
        return str(variant or "UNKNOWN"), aliases.get(normalized, normalized)
    encoded = row.get("source_ix_data_base64") or raw.get("sourceIxDataBase64")
    if not encoded:
        return str(variant or "UNKNOWN"), "UNKNOWN"
    try:
        discriminator = base64.b64decode(str(encoded), validate=True)[:8]
    except (ValueError, TypeError):
        return str(variant or "UNKNOWN"), "UNKNOWN"
    known = BUY_QUOTE_SEMANTICS.get(discriminator)
    return known if known else (str(variant or "UNKNOWN"), "UNKNOWN")


def buy_quote_for_replay(row: dict[str, Any], raw: dict[str, Any], status: str) -> tuple[int | None, str, str, str]:
    """Select only a quote amount whose semantics are explicit.

    The ordinary Pump buy amount is a max-cost limit. It is never used as
    executed cash flow. An explicit realized quote may be supplied by a
    processed/finalized execution join; exact-input intent amounts are allowed
    only for conditional replay and are still excluded from authoritative
    economics by the caller.
    """
    variant, semantics = quote_semantics(row, raw)
    execution = row.get("execution") if isinstance(row.get("execution"), dict) else raw.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    realized = None
    realized_source = ""
    for key in ("executed_quote_lamports", "executedQuoteLamports", "actual_quote_lamports", "actualQuoteLamports"):
        if execution.get(key) is not None:
            realized = execution.get(key)
            realized_source = f"execution.{key}"
            break
        if raw.get(key) is not None:
            realized = raw.get(key)
            realized_source = f"raw.{key}"
            break
    if realized is not None:
        try:
            amount = int(realized)
        except (TypeError, ValueError):
            return None, variant, semantics, "invalid_realized_quote"
        return (amount if amount > 0 else None), variant, semantics, realized_source
    if semantics in {"EXACT_SOL_IN_INTENT", "EXACT_QUOTE_IN_INTENT"}:
        value = row.get("quote_lamports")
        if value is None:
            value = raw.get("quoteLamports")
        try:
            amount = int(value)
        except (TypeError, ValueError):
            return None, variant, semantics, "missing_exact_input"
        return (amount if amount > 0 else None), variant, semantics, "exact_input_intent"
    if status == "SUCCESS" and semantics == "UNKNOWN":
        return None, variant, semantics, "unknown_quote_semantics"
    return None, variant, semantics, "max_cost_or_unknown_intent"


def apply_flow(state: dict[str, int], side: str, quote_lamports: int | None,
               sell_tokens: int | None) -> tuple[dict[str, int], int | None]:
    x, y = state["virtual_sol"], state["virtual_token"]
    if side == "buy":
        if not quote_lamports or quote_lamports <= 0:
            raise ValueError("buy_quote_missing")
        fee = quote_lamports * FEE_BPS // 10_000
        net = quote_lamports - fee
        new_tokens = (x * y) // (x + net)
        tokens_out = y - new_tokens
        if tokens_out <= 0 or new_tokens >= y:
            raise ValueError("buy_math_invalid")
        post = {"virtual_sol": x + net, "virtual_token": new_tokens}
        if "real_sol" in state and "real_token" in state:
            post["real_sol"] = state["real_sol"] + net
            post["real_token"] = state["real_token"] - tokens_out
            if post["real_token"] < 0:
                raise ValueError("buy_real_token_reserve_underflow")
        return post, tokens_out
    if side == "sell":
        if not sell_tokens or sell_tokens <= 0:
            raise ValueError("sell_tokens_missing")
        new_token = y + sell_tokens
        new_sol = (x * y) // new_token
        if new_sol <= 0:
            raise ValueError("sell_math_invalid")
        post = {"virtual_sol": new_sol, "virtual_token": new_token}
        if "real_sol" in state and "real_token" in state:
            gross_out = x - new_sol
            post["real_sol"] = state["real_sol"] - gross_out
            post["real_token"] = state["real_token"] + sell_tokens
            if post["real_sol"] < 0:
                raise ValueError("sell_real_sol_reserve_underflow")
        return post, None
    raise ValueError("unsupported_side")


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"input_offset": 0, "curves": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def offset_at_receive_cutoff(source: Any, cutoff_ms: int) -> int:
    """Find the first source line at/after a fixed receive-time cutoff."""
    while True:
        line_offset = source.tell()
        line = source.readline()
        if not line:
            return source.tell()
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            receive_ms = int(row.get("local_receive_ts_ms") or row.get("receive_ts_ms") or 0)
        except (TypeError, ValueError):
            continue
        if receive_ms >= cutoff_ms:
            return line_offset


def load_account_seeds(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    seeds: dict[str, list[dict[str, Any]]] = {}
    if not path or not path.exists():
        return seeds
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            curve = str(row.get("bondingCurve") or row.get("bonding_curve") or "")
            if not curve:
                continue
            virtual_sol = row.get("virtualSolReserves", row.get("virtual_sol_reserves"))
            virtual_token = row.get("virtualTokenReserves", row.get("virtual_token_reserves"))
            observed = row.get("observedAtMs", row.get("observed_at_ms"))
            try:
                seed = {"virtual_sol": int(virtual_sol), "virtual_token": int(virtual_token),
                        "observed_at_ms": int(observed), "slot": int(row.get("slot") or 0),
                        "source": str(row.get("source") or "native-account-state")}
                real_sol = row.get("realSolReserves", row.get("real_sol_reserves", row.get("real_sol")))
                real_token = row.get("realTokenReserves", row.get("real_token_reserves", row.get("real_token")))
                if real_sol is not None and real_token is not None:
                    seed["real_sol"] = int(real_sol)
                    seed["real_token"] = int(real_token)
            except (TypeError, ValueError):
                continue
            seeds.setdefault(curve, []).append(seed)
    for curve in seeds:
        unique = {(s["observed_at_ms"], s["slot"], s["virtual_sol"], s["virtual_token"]): s for s in seeds[curve]}
        seeds[curve] = sorted(unique.values(), key=lambda s: (s["observed_at_ms"], s["slot"]))
    return seeds


def append_normalized_seed_rows(path: Path | None, seeds: dict[str, list[dict[str, Any]]],
                                offset: int = 0) -> int:
    """Append normalized seed rows after offset without rescanning the baseline."""
    if not path or not path.exists():
        return offset
    with path.open("r", encoding="utf-8") as handle:
        handle.seek(max(0, offset))
        while True:
            line = handle.readline()
            if not line:
                return handle.tell()
            try:
                row = json.loads(line)
                curve = str(row.get("bondingCurve") or row.get("bonding_curve") or "")
                seed = {
                    "virtual_sol": int(row["virtualSolReserves"]),
                    "virtual_token": int(row["virtualTokenReserves"]),
                    "observed_at_ms": int(row["observedAtMs"]),
                    "slot": int(row.get("slot") or 0),
                    "source": str(row.get("source") or "native-account-state"),
                }
                if row.get("realSolReserves") is not None and row.get("realTokenReserves") is not None:
                    seed["real_sol"] = int(row["realSolReserves"])
                    seed["real_token"] = int(row["realTokenReserves"])
                if not curve:
                    continue
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            seeds.setdefault(curve, []).append(seed)


def run(args: argparse.Namespace) -> None:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    state_doc = read_state(args.state)
    offset = int(state_doc.get("input_offset") or 0)
    curves: dict[str, dict[str, Any]] = state_doc.get("curves") or {}
    account_seeds = load_account_seeds(getattr(args, "account_seed_input", None))
    account_seed_delta = getattr(args, "account_seed_delta_input", None)
    account_seed_delta_offset = append_normalized_seed_rows(account_seed_delta, account_seeds, 0)
    last_seed_refresh = time.monotonic()
    stats = {"read": 0, "seeds": 0, "emitted": 0, "skipped_no_seed": 0, "rejected": 0, "rejected_failed_execution": 0, "rejected_quote_semantics": 0, "unknown_execution": 0, "duplicates": 0}
    writes_since_state = 0
    existing_observation_ids: set[str] = set()
    if args.output.exists():
        with args.output.open("r", encoding="utf-8") as prior:
            for line in prior:
                try:
                    prior_row = json.loads(line)
                    identity = prior_row.get("observation_id") or prior_row.get("raw_record_identity")
                    if identity:
                        existing_observation_ids.add(str(identity))
                except json.JSONDecodeError:
                    continue
    with args.input.open("r", encoding="utf-8") as source, args.output.open("a", encoding="utf-8") as output:
        start_at_receive_ms = getattr(args, "start_at_receive_ms", None)
        if start_at_receive_ms is not None and not args.state.exists():
            offset = offset_at_receive_cutoff(source, start_at_receive_ms)
        elif args.start_at_end and not args.state.exists():
            source.seek(0, 2)
            offset = source.tell()
        source.seek(offset)
        while True:
            if account_seed_delta and time.monotonic() - last_seed_refresh >= 5:
                delta_size = account_seed_delta.stat().st_size if account_seed_delta.exists() else 0
                if delta_size < account_seed_delta_offset:
                    account_seeds = load_account_seeds(getattr(args, "account_seed_input", None))
                    account_seed_delta_offset = 0
                account_seed_delta_offset = append_normalized_seed_rows(
                    account_seed_delta, account_seeds, account_seed_delta_offset
                )
                last_seed_refresh = time.monotonic()
            line = source.readline()
            if not line:
                if args.once:
                    break
                time.sleep(args.poll_ms / 1000)
                continue
            stats["read"] += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw, curve, mint, side, slot, tx_index, ix_index = source_fields(row)
            if not curve or not mint:
                continue
            order = (slot, tx_index, ix_index)
            if raw.get("nativeKind") == "create" or raw.get("kind") in {"create", "token_create"}:
                if curve not in curves:
                    if len(curves) >= MAX_CURVES:
                        curves.pop(next(iter(curves)))
                    curves[curve] = {"mint": mint, "virtual_sol": INITIAL_SOL, "virtual_token": INITIAL_TOKEN,
                                     "last_order": order, "seed_slot": slot, "seed_receive_ms": row.get("local_receive_ts_ms")}
                    stats["seeds"] += 1
                offset = source.tell()
                continue
            if side not in {"buy", "sell"}:
                offset = source.tell()
                continue
            status = execution_status(row, raw)
            if status == "FAILED":
                stats["rejected_failed_execution"] += 1
                offset = source.tell()
                continue
            if status == "UNKNOWN":
                stats["unknown_execution"] += 1
            current = curves.get(curve)
            seed_history = account_seeds.get(curve, [])
            receive_ms = int(row.get("local_receive_ts_ms") or 0)
            seed = next((candidate for candidate in reversed(seed_history)
                         if candidate["observed_at_ms"] <= receive_ms
                         and (not candidate["slot"] or not slot or candidate["slot"] < slot)), None)
            seed_is_causal = bool(seed and seed["observed_at_ms"] <= receive_ms
                                  and (not seed["slot"] or not slot or seed["slot"] < slot))
            if seed_is_causal and (not current or seed["observed_at_ms"] > int(current.get("seed_receive_ms") or 0)):
                current = curves[curve] = {"mint": mint, "virtual_sol": seed["virtual_sol"],
                                           "virtual_token": seed["virtual_token"], "last_order": (0, 0, 0),
                                           "seed_slot": seed["slot"], "seed_receive_ms": seed["observed_at_ms"],
                                           "seed_source": seed["source"]}
                if "real_sol" in seed and "real_token" in seed:
                    current["real_sol"] = seed["real_sol"]
                    current["real_token"] = seed["real_token"]
            if not current:
                stats["skipped_no_seed"] += 1
                offset = source.tell()
                continue
            if order <= tuple(current.get("last_order") or (0, 0, 0)):
                stats["duplicates"] += 1
                offset = source.tell()
                continue
            pre = {"virtual_sol": int(current["virtual_sol"]), "virtual_token": int(current["virtual_token"])}
            if "real_sol" in current and "real_token" in current:
                pre["real_sol"] = int(current["real_sol"])
                pre["real_token"] = int(current["real_token"])
            variant, semantics = "UNKNOWN", "UNKNOWN"
            quote_source = ""
            quote_lamports = None
            if side == "buy":
                quote_lamports, variant, semantics, quote_source = buy_quote_for_replay(row, raw, status)
                if quote_lamports is None:
                    stats["rejected_quote_semantics"] += 1
                    offset = source.tell()
                    continue
            try:
                post, tokens_out = apply_flow(pre, side, quote_lamports,
                                               row.get("sell_token_amount_raw") or raw.get("sellTokenAmountRaw"))
            except (TypeError, ValueError):
                stats["rejected"] += 1
                offset = source.tell()
                continue
            current.update(post, last_order=order)
            receive_ms = int(row.get("local_receive_ts_ms") or 0)
            output_row = dict(row)
            output_row["canonical_state"] = {
                "schema": "PUMP_CURVE_NATIVE_REPLAY_STATE_V1",
                "route": "PUMP_BONDING_CURVE_TOP_LEVEL",
                "state_source": "NATIVE_SHRED_REPLAY",
                "state_observed_at_ms": receive_ms,
                "slot": slot,
                "source_transaction_index": tx_index,
                "source_instruction_index": ix_index,
                "state_identity": f"native-pre-finality:{curve}:{post['virtual_sol']}:{post['virtual_token']}",
                "pre_state": pre,
                "post_state": post,
                "fee_basis_points": FEE_BPS,
                "quote_mint": "11111111111111111111111111111111",
                "native_pre_finality": True,
                "execution_status": status,
                "state_transition_authority": "CONDITIONAL_INTENT_REPLAY",
                "native_instruction_variant": variant,
                "quote_semantics": semantics,
                "quote_amount_source": quote_source,
                "replay_seed_slot": current.get("seed_slot"),
                "replay_seed_source": current.get("seed_source", "native-creation"),
                "tokens_out_raw": tokens_out,
            }
            output_row["fee_basis_points"] = FEE_BPS
            output_row["protocol_fee_basis_points"] = 95
            output_row["creator_fee_basis_points"] = 30
            output_row["execution_status"] = status
            output_row["state_transition_authority"] = "CONDITIONAL_INTENT_REPLAY"
            output_row["native_instruction_variant"] = variant
            output_row["quote_semantics"] = semantics
            output_row["quote_amount_source"] = quote_source
            # Fixed fees/math and predecessor continuity are unverified even
            # when this individual transaction has an explicit success flag.
            output_row["native_economics_state_complete"] = False
            output_row["economics_incomplete_reason"] = "unverified_replay_fees_math_and_state_continuity"
            output_row["native_state_bridge"] = {"schema": "NATIVE_PUMP_REPLAY_V1", "causal": True,
                                                  "receive_ts_ms": receive_ms, "state_age_ms": 0}
            identity = output_row.get("observation_id") or output_row.get("raw_record_identity")
            if identity and str(identity) in existing_observation_ids:
                stats["duplicates"] += 1
                offset = source.tell()
                continue
            if identity:
                existing_observation_ids.add(str(identity))
            output.write(json.dumps(output_row, sort_keys=True) + "\n")
            output.flush()
            stats["emitted"] += 1
            offset = source.tell()
            writes_since_state += 1
            if writes_since_state >= 100:
                state_doc = {"schema": "NATIVE_PUMP_REPLAY_STATE_V1", "input_offset": offset, "curves": curves,
                             "stats": stats}
                args.state.write_text(json.dumps(state_doc, sort_keys=True), encoding="utf-8")
                writes_since_state = 0
    state_doc = {"schema": "NATIVE_PUMP_REPLAY_STATE_V1", "input_offset": offset, "curves": curves, "stats": stats}
    args.state.write_text(json.dumps(state_doc, sort_keys=True), encoding="utf-8")
    print(json.dumps(stats, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--poll-ms", type=int, default=100)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--start-at-end", action="store_true")
    parser.add_argument("--start-at-receive-ms", type=int)
    parser.add_argument("--account-seed-input", type=Path)
    parser.add_argument("--account-seed-delta-input", type=Path)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
