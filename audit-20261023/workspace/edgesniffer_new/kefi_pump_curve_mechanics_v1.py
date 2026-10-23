"""Fail-closed actionability mechanics for Pump bonding-curve rows.

This module intentionally contains no PumpSwap reserve or fee arithmetic and
does not produce a cash-flow or PnL estimate.  It only identifies exact Pump
curve observations available after a delayed entry boundary and a later mark.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Iterable


SCHEMA = "KEFI_PUMP_CURVE_MECHANICS_V1"
WSOL = "So11111111111111111111111111111111111111112"
CONTROL_BUCKET_NS = 20_000_000_000


class NonTradeable(ValueError):
    pass


def event_key(row: dict) -> tuple[str, int, int, int, str]:
    try:
        key = (str(row["source_instance_id"]), int(row["continuity_epoch"]),
               int(row["durable_output_sequence"]), int(row["log_index"]),
               str(row["source_frame_sha256"]))
    except (KeyError, TypeError, ValueError) as error:
        raise NonTradeable("MISSING_DETERMINISTIC_SOURCE_ORDER") from error
    if key[1] < 0 or key[2] <= 0 or key[3] < 0 or not key[4]:
        raise NonTradeable("INVALID_DETERMINISTIC_SOURCE_ORDER")
    return key


def availability_key(row: dict) -> tuple[int, str, int, int, int, str]:
    try:
        received = int(row["received_at_unix_ns"])
    except (KeyError, TypeError, ValueError) as error:
        raise NonTradeable("MISSING_AVAILABILITY_CLOCK") from error
    instance, epoch, sequence, log_index, frame = event_key(row)
    return received, instance, epoch, sequence, log_index, frame


def sorted_unique(rows: Iterable[dict]) -> list[dict]:
    ordered = sorted(rows, key=availability_key)
    seen = set()
    for row in ordered:
        key = event_key(row)
        if key in seen:
            raise NonTradeable("DUPLICATE_DETERMINISTIC_EVENT_ORDER")
        seen.add(key)
    return ordered


def _identity(row: dict) -> tuple[str, str]:
    curve = str(row.get("curve", row.get("pool", "")))
    mint = str(row.get("mint", ""))
    if not curve or not mint:
        raise NonTradeable("MISSING_PUMP_CURVE_IDENTITY")
    return curve, mint


def tradeable(row: dict) -> dict[str, object]:
    try:
        key = event_key(row)
        if row.get("schema") != "KEFI_FROZEN_PUMP_CURVE_TRANSITION_TAPE_ROW_V1":
            raise NonTradeable("UNSUPPORTED_PUMP_CURVE_SCHEMA")
        if row.get("venue") != "PUMP_BONDING_CURVE":
            raise NonTradeable("UNSUPPORTED_PUMP_CURVE_VENUE")
        if str(row.get("quote_mint", "")) != WSOL:
            raise NonTradeable("NONCANONICAL_PUMP_QUOTE_LAYOUT")
        if row.get("exact_transition_validated") is not True:
            raise NonTradeable("UNVALIDATED_PUMP_CURVE_TRANSITION")
        curve, mint = _identity(row)
        if mint == WSOL or curve == mint:
            raise NonTradeable("INVALID_PUMP_CURVE_IDENTITY")
        if str(row.get("side", "")).upper() not in {"BUY", "SELL"}:
            raise NonTradeable("UNSUPPORTED_PUMP_CURVE_SIDE")
        if int(row["sol_amount_raw"]) <= 0 or int(row["token_amount_raw"]) <= 0:
            raise NonTradeable("NONPOSITIVE_PUMP_CURVE_AMOUNT")
        return {"tradeable": True, "reason": "", "event_key": key}
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, NonTradeable):
            return {"tradeable": False, "reason": str(error), "event_key": None}
        return {"tradeable": False, "reason": "MALFORMED_PUMP_CURVE_ROW", "event_key": None}


def delayed_entry_state(rows: Iterable[dict], signal: dict, entry_delay_ns: int) -> dict:
    if entry_delay_ns <= 0:
        raise NonTradeable("NONPOSITIVE_ENTRY_DELAY")
    signal_time, instance, epoch, *_ = availability_key(signal)
    curve, mint = _identity(signal)
    target_time = signal_time + entry_delay_ns
    for row in sorted_unique(rows):
        received, row_instance, row_epoch, *_ = availability_key(row)
        row_curve, row_mint = _identity(row)
        if (row_instance, row_epoch) != (instance, epoch) or received < target_time:
            continue
        if (row_curve, row_mint) != (curve, mint):
            continue
        result = tradeable(row)
        if result["tradeable"]:
            return {"status": "TRADEABLE", "entry_available_at_unix_ns": received,
                    "entry_event_key": result["event_key"]}
        return {"status": "NON_TRADEABLE", "reason": result["reason"]}
    return {"status": "NON_TRADEABLE", "reason": "NO_SUPPORTED_POST_DELAY_ENTRY_STATE"}


def observed_exit_mark(rows: Iterable[dict], entry: dict, exit_after_ns: int) -> dict:
    if exit_after_ns <= 0:
        raise NonTradeable("NONPOSITIVE_EXIT_DELAY")
    entry_time, instance, epoch, *_ = availability_key(entry)
    curve, mint = _identity(entry)
    target_time = entry_time + exit_after_ns
    for row in sorted_unique(rows):
        received, row_instance, row_epoch, *_ = availability_key(row)
        row_curve, row_mint = _identity(row)
        if (row_instance, row_epoch) != (instance, epoch) or received < target_time:
            continue
        if (row_curve, row_mint) != (curve, mint):
            continue
        result = tradeable(row)
        if result["tradeable"]:
            return {"status": "MARK_AVAILABLE", "mark_available_at_unix_ns": received,
                    "mark_event_key": result["event_key"]}
        return {"status": "NON_TRADEABLE", "reason": result["reason"]}
    return {"status": "NON_TRADEABLE", "reason": "NO_SUPPORTED_EXIT_MARK"}


def deterministic_controls(targets: Iterable[dict], candidates: Iterable[dict], *, bucket_ns: int,
                           per_target: int, seed: str) -> dict[tuple, list[dict]]:
    if bucket_ns != CONTROL_BUCKET_NS or per_target <= 0:
        raise ValueError("invalid Pump curve control contract")
    indexed: dict[tuple, list[dict]] = defaultdict(list)
    candidate_rows = []
    for row in candidates:
        try:
            event_key(row)
            availability_key(row)
        except NonTradeable:
            continue
        candidate_rows.append(row)
    for row in sorted_unique(candidate_rows):
        result = tradeable(row)
        if not result["tradeable"]:
            continue
        received, instance, epoch, *_ = availability_key(row)
        indexed[(instance, epoch, received // bucket_ns, str(row["side"]), str(row["quote_mint"]))].append(row)
    selected = {}
    for target in sorted_unique(targets):
        received, instance, epoch, *_ = availability_key(target)
        key = (instance, epoch, received // bucket_ns, str(target["side"]), str(target["quote_mint"]))
        identity = "|".join(map(str, event_key(target)))
        options = [row for row in indexed.get(key, []) if str(row.get("mint")) != str(target.get("mint"))]
        options.sort(key=lambda row: hashlib.sha256(f"{seed}|{identity}|{'|'.join(map(str, event_key(row)))}".encode()).hexdigest())
        selected[event_key(target)] = options[:per_target]
    return selected
