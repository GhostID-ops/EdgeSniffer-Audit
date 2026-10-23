#!/usr/bin/env python3
"""Exact, fail-closed mechanics primitives for a future kEfi H3 paper lane.

This module is not an H3 strategy and never opens a cohort.  It only defines
the evidence rules a reviewed H3 protocol must use: writer-sequence ordering,
canonical SOL-quote PumpSwap layouts, delayed entry state, observed exit mark,
deterministic controls, and dependence clusters.  It intentionally refuses to
invent hypothetical fills from BPS fields.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable


SCHEMA = "KEFI_H3_PUMPSWAP_MECHANICS_V1"
WSOL = "So11111111111111111111111111111111111111112"
CONTROL_BUCKET_NS = 20_000_000_000
POOL_AGE_BUCKET_SECONDS = (30, 120, 300, 900)
LIQUIDITY_LOG10_BUCKET_WIDTH = 0.5
FEE_FIELDS = ("lp_fee_bps", "protocol_fee_bps", "coin_creator_bps", "cashback_bps", "buyback_bps")


class NonTradeable(ValueError):
    pass


def event_key(row: dict) -> tuple[str, int, int, int, str]:
    try:
        epoch = int(row["continuity_epoch"])
        sequence = int(row["durable_output_sequence"])
        event_index = int(row["event_index"])
        frame = str(row["source_frame_sha256"])
        instance = str(row["source_instance_id"])
    except (KeyError, TypeError, ValueError) as error:
        raise NonTradeable("MISSING_DETERMINISTIC_SOURCE_ORDER") from error
    if epoch < 0 or sequence <= 0 or event_index < 0 or not frame or not instance:
        raise NonTradeable("INVALID_DETERMINISTIC_SOURCE_ORDER")
    return instance, epoch, sequence, event_index, frame


def availability_key(row: dict) -> tuple[int, str, int, int, int, str]:
    try:
        received = int(row["received_at_unix_ns"])
    except (KeyError, TypeError, ValueError) as error:
        raise NonTradeable("MISSING_AVAILABILITY_CLOCK") from error
    instance, epoch, sequence, event_index, frame = event_key(row)
    return received, instance, epoch, sequence, event_index, frame


def pre_state(row: dict) -> dict[str, int]:
    """Recover the pre-event state from an observed exact transition only."""
    try:
        if row.get("exact_transition_validated") is not True:
            raise NonTradeable("UNVALIDATED_PUMPSWAP_TRANSITION")
        side = str(row["side"]).upper()
        base = int(row["pool_base_raw"])
        quote = int(row["pool_quote_raw"])
        amount = int(row["base_amount"])
        delta = int(row["pool_quote_delta"])
    except (KeyError, TypeError, ValueError) as error:
        raise NonTradeable("MISSING_RAW_PUMPSWAP_TRANSITION") from error
    if min(base, quote, amount, delta) < 0:
        raise NonTradeable("NEGATIVE_RAW_PUMPSWAP_TRANSITION")
    if side == "BUY":
        if quote < delta:
            raise NonTradeable("BUY_PRE_QUOTE_UNDERFLOW")
        return {"pool_base_raw": base + amount, "pool_quote_raw": quote - delta}
    if side == "SELL":
        if base < amount:
            raise NonTradeable("SELL_PRE_BASE_UNDERFLOW")
        return {"pool_base_raw": base - amount, "pool_quote_raw": quote + delta}
    raise NonTradeable("UNSUPPORTED_PUMPSWAP_SIDE")


def tradeable(row: dict) -> dict[str, object]:
    """Return a canonical layout or one stable fail-closed rejection reason."""
    try:
        key = event_key(row)
        if str(row.get("quote_mint", "")) != WSOL or str(row.get("mint", "")) == WSOL:
            raise NonTradeable("NONCANONICAL_QUOTE_LAYOUT")
        if str(row.get("amount_mode", "")) not in {"EXACT_BASE", "EXACT_QUOTE_IN"}:
            raise NonTradeable("UNSUPPORTED_PUMPSWAP_AMOUNT_MODE")
        state = pre_state(row)
        return {"tradeable": True, "reason": "", "event_key": key, "pre_state": state}
    except NonTradeable as error:
        return {"tradeable": False, "reason": str(error), "event_key": None, "pre_state": None}


def sorted_unique(rows: Iterable[dict]) -> list[dict]:
    ordered = sorted(rows, key=availability_key)
    previous = None
    for row in ordered:
        key = event_key(row)
        if key == previous:
            raise NonTradeable("DUPLICATE_DETERMINISTIC_EVENT_ORDER")
        previous = key
    return ordered


def delayed_entry_state(rows: Iterable[dict], signal: dict, entry_delay_ns: int) -> dict:
    if entry_delay_ns <= 0:
        raise NonTradeable("NONPOSITIVE_ENTRY_DELAY")
    signal_time, instance, epoch, _, _, _ = availability_key(signal)
    target = signal_time + entry_delay_ns
    pool, mint = str(signal["pool"]), str(signal["mint"])
    for row in sorted_unique(rows):
        received, row_instance, row_epoch, *_ = availability_key(row)
        if row_instance != instance or row_epoch != epoch:
            continue
        if received < target or str(row.get("pool")) != pool or str(row.get("mint")) != mint:
            continue
        result = tradeable(row)
        if result["tradeable"]:
            return {"status": "TRADEABLE", "entry_available_at_unix_ns": received,
                    "entry_event_key": result["event_key"], "pre_state": result["pre_state"]}
        return {"status": "NON_TRADEABLE", "reason": result["reason"]}
    return {"status": "NON_TRADEABLE", "reason": "NO_SUPPORTED_POST_DELAY_ENTRY_STATE"}


def observed_exit_mark(rows: Iterable[dict], entry: dict, exit_after_ns: int) -> dict:
    """Find a same-epoch observed state mark; never synthesize an exit fill."""
    if exit_after_ns <= 0:
        raise NonTradeable("NONPOSITIVE_EXIT_DELAY")
    entry_time, instance, epoch, _, _, _ = availability_key(entry)
    target = entry_time + exit_after_ns
    pool, mint = str(entry["pool"]), str(entry["mint"])
    for row in sorted_unique(rows):
        received, row_instance, row_epoch, *_ = availability_key(row)
        if row_instance != instance or row_epoch != epoch or received < target:
            continue
        if str(row.get("pool")) != pool or str(row.get("mint")) != mint:
            continue
        result = tradeable(row)
        if result["tradeable"]:
            return {"status": "MARK_AVAILABLE", "mark_available_at_unix_ns": received,
                    "mark_event_key": result["event_key"], "pre_state": result["pre_state"]}
        return {"status": "NON_TRADEABLE", "reason": result["reason"]}
    return {"status": "NON_TRADEABLE", "reason": "NO_SUPPORTED_EXIT_MARK"}


def cluster_key(row: dict, bucket_ns: int) -> str:
    if bucket_ns <= 0:
        raise ValueError("bucket_ns must be positive")
    received, instance, epoch, *_ = availability_key(row)
    return f"source={instance}|epoch={epoch}|pool={row['pool']}|bucket={received // bucket_ns}"


def observed_pool_first_seen(rows: Iterable[dict]) -> dict[tuple[str, int, str], int]:
    """First source-observed timestamp per pool, never a chain-history claim."""
    result: dict[tuple[str, int, str], int] = {}
    for row in sorted_unique(rows):
        received, instance, epoch, *_ = availability_key(row)
        key = (instance, epoch, str(row.get("pool", "")))
        if not key[-1]:
            raise NonTradeable("MISSING_POOL_IDENTITY")
        result.setdefault(key, received)
    return result


def bucket_pool_age(observed_seconds: float) -> str:
    if observed_seconds < 0:
        raise NonTradeable("NEGATIVE_OBSERVED_POOL_AGE")
    lower = 0
    for upper in POOL_AGE_BUCKET_SECONDS:
        if observed_seconds < upper:
            return f"[{lower},{upper})"
        lower = upper
    return f"[{lower},+inf)"


def fee_tuple(row: dict) -> tuple[int, ...]:
    try:
        return tuple(int(row[field]) for field in FEE_FIELDS)
    except (KeyError, TypeError, ValueError) as error:
        raise NonTradeable("MISSING_PREENTRY_FEE_LAYOUT") from error


def control_context(row: dict, first_seen: dict[tuple[str, int, str], int]) -> tuple:
    """Frozen matching state derived only from the candidate's pre-event view.

    The input row can contain later trajectory fields, but this function never
    reads them.  ``amount_mode`` and fee fields are the exact immutable
    PumpSwap layout attached to the candidate update; pool age and liquidity
    are reconstructed before that update's state transition.
    """
    if not tradeable(row)["tradeable"]:
        raise NonTradeable("UNSUPPORTED_PREENTRY_LAYOUT")
    received, instance, epoch, *_ = availability_key(row)
    pool = str(row.get("pool", ""))
    seen = first_seen.get((instance, epoch, pool))
    if seen is None:
        raise NonTradeable("MISSING_OBSERVED_POOL_AGE")
    state = pre_state(row)
    quote = int(state["pool_quote_raw"])
    if quote <= 0:
        raise NonTradeable("NONPOSITIVE_PREENTRY_LIQUIDITY")
    liquidity_bucket = math.floor(math.log10(quote) / LIQUIDITY_LOG10_BUCKET_WIDTH)
    age_seconds = (received - seen) / 1_000_000_000
    return (
        instance,
        epoch,
        received // CONTROL_BUCKET_NS,
        "CANONICAL_WSOL_NON_WSOL_BASE",
        str(row["amount_mode"]),
        fee_tuple(row),
        bucket_pool_age(age_seconds),
        liquidity_bucket,
    )


def deterministic_controls(targets: Iterable[dict], candidates: Iterable[dict], *, bucket_ns: int,
                           per_target: int, seed: str,
                           first_seen: dict[tuple[str, int, str], int] | None = None) -> dict[tuple, list[dict]]:
    """Choose frozen-stratum, pre-entry-only controls reproducibly.

    ``bucket_ns`` is retained as an explicit compatibility/assertion argument;
    the qualification contract fixes it at exactly 20 seconds.
    """
    if per_target <= 0:
        raise ValueError("per_target must be positive")
    if bucket_ns != CONTROL_BUCKET_NS:
        raise ValueError("qualification control bucket must be exactly 20 seconds")
    target_rows, candidate_rows = list(targets), list(candidates)
    unique_rows: dict[tuple[str, int, int, int, str], dict] = {}
    for row in [*target_rows, *candidate_rows]:
        key = event_key(row)
        existing = unique_rows.setdefault(key, row)
        if existing != row:
            raise NonTradeable("CONFLICTING_DUPLICATE_CANDIDATE")
    ordered_candidates = sorted_unique(candidate_rows)
    # Qualification callers must provide the first-seen map from the complete
    # ordered scientific stream.  The fallback exists only for compatibility
    # with the standalone mechanics primitive; it is not sufficient cohort
    # provenance because eligibility/warm-up filtering can hide earlier rows.
    if first_seen is None:
        first_seen = observed_pool_first_seen(unique_rows.values())
    indexed: dict[tuple, list[dict]] = defaultdict(list)
    for row in ordered_candidates:
        try:
            indexed[control_context(row, first_seen)].append(row)
        except NonTradeable:
            continue
    chosen: dict[tuple, list[dict]] = {}
    for target in sorted_unique(target_rows):
        try:
            target_context = control_context(target, first_seen)
        except NonTradeable:
            continue
        options = [row for row in indexed[target_context]
                   if str(row.get("mint")) != str(target.get("mint"))]
        identity = "|".join(map(str, event_key(target)))
        options.sort(key=lambda row: hashlib.sha256(
            f"{seed}|{identity}|{'|'.join(map(str, event_key(row)))}".encode()
        ).hexdigest())
        chosen[event_key(target)] = [{**row, "dependency_cluster": cluster_key(row, bucket_ns)}
                                     for row in options[:per_target]]
    return chosen


def contract() -> dict:
    return {
        "schema": SCHEMA,
        "availability_clock": "received_at_unix_ns",
        "deterministic_tie_order": ["source_instance_id", "continuity_epoch", "durable_output_sequence", "event_index", "source_frame_sha256"],
        "entry_delay": "POSITIVE_PREREGISTERED_NS; entry is first supported same-epoch state at or after delay",
        "entry_transition": "RAW_INSTRUCTION_EVENT_VALIDATED_ONLY",
        "exit": "SAME_EPOCH_NEXT_OBSERVED_SUPPORTED_STATE_MARK; no inferred hypothetical fill",
        "non_tradeable": "FAIL_CLOSED_WITH_STABLE_REASON",
        "controls": "DETERMINISTIC_SAME_EPOCH_SAME_QUOTE_LAYOUT_DIFFERENT_MINT",
        "clustering": "DEPENDENCY_CLUSTER=EPOCH_POOL_FIXED_TIME_BUCKET",
        "pnl_accessed": False,
        "outcome_blind": True,
    }
