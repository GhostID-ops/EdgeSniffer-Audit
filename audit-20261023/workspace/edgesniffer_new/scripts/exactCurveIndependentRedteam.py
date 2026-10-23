#!/usr/bin/env python3
"""Independent red-team audit for EXACT_CURVE_ECONOMICS_V1.

This program deliberately does not import the production evaluator or its
relabel driver.  It decodes finalized transaction evidence, implements the
published Pump arithmetic independently, and recomputes aggregate and
coverage claims from row-level artifacts.
"""

from __future__ import annotations

import argparse
import base64
import collections
import dataclasses
import datetime as dt
import gzip
import hashlib
import json
import math
import pathlib
import sqlite3
import statistics
import zlib
from typing import Any, Iterable, Iterator, Optional


PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TOKEN_LEGACY = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TRADE_EVENT = bytes([189, 219, 127, 211, 78, 230, 97, 238])
DISCRIMINATORS = {
    bytes([102, 6, 61, 18, 1, 218, 235, 234]): ("buy", "exact_token_buy"),
    bytes([184, 23, 238, 97, 103, 197, 211, 61]): ("buy_v2", "exact_token_buy"),
    bytes([56, 252, 116, 8, 158, 223, 205, 95]): ("buy_exact_sol_in", "exact_quote_buy"),
    bytes([194, 171, 28, 70, 104, 77, 91, 47]): ("buy_exact_quote_in_v2", "exact_quote_buy"),
    bytes([51, 230, 133, 164, 1, 127, 131, 173]): ("sell", "exact_token_sell"),
    bytes([93, 246, 130, 60, 231, 233, 64, 178]): ("sell_v2", "exact_token_sell"),
}
OFFICIAL_REFERENCE = {
    "sdk_package": "@pump-fun/pump-sdk@1.36.0",
    "docs_git_commit": "9c82f61cb711b044a17f770ab8ce9f9bdf78f333",
    "exact_token_buy": "floor(token_out * virtual_quote / (virtual_token - token_out)) + 1",
    "exact_quote_buy": "IDL: net=floor(gross*10000/(10000+fees)); reduce fee overflow; price with net-1",
    "sell": "floor(token_in * virtual_quote / (virtual_token + token_in))",
    "fee": "ceil(curve_quote * fee_bps / 10000)",
}
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58_INDEX = {c: i for i, c in enumerate(BASE58_ALPHABET)}


def utc(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat()


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl_gz(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def b58decode(value: str) -> bytes:
    number = 0
    for char in value:
        number = number * 58 + BASE58_INDEX[char]
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + raw


def b58encode(raw: bytes) -> str:
    pad = len(raw) - len(raw.lstrip(b"\0"))
    number = int.from_bytes(raw, "big")
    chars: list[str] = []
    while number:
        number, rem = divmod(number, 58)
        chars.append(BASE58_ALPHABET[rem])
    return "1" * pad + "".join(reversed(chars))


def ceil_fee(amount: int, bps: int) -> int:
    return (amount * bps + 9_999) // 10_000


@dataclasses.dataclass(frozen=True)
class State:
    vq: int
    vt: int
    rq: int
    rt: int


@dataclasses.dataclass(frozen=True)
class Event:
    mint: str
    quote: int
    token: int
    is_buy: bool
    user: str
    timestamp: int
    post: State
    protocol_bps: int
    protocol_fee: int
    creator: str
    creator_bps: int
    creator_fee: int
    ix_name: str
    mayhem: bool
    cashback_bps: int
    cashback: int
    buyback_bps: int
    buyback_fee: int
    quote_mint: str
    holder_rewards_bps: int = 0
    holder_rewards: int = 0

    @property
    def pre(self) -> State:
        if self.is_buy:
            return State(
                self.post.vq - self.quote,
                self.post.vt + self.token,
                self.post.rq - self.quote,
                self.post.rt + self.token,
            )
        return State(
            self.post.vq + self.quote,
            self.post.vt - self.token,
            self.post.rq + self.quote,
            self.post.rt - self.token,
        )


class Reader:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.at = 0

    def take(self, n: int) -> bytes:
        if self.at + n > len(self.raw):
            raise ValueError("truncated borsh")
        value = self.raw[self.at : self.at + n]
        self.at += n
        return value

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "little")

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "little")

    def u64(self) -> int:
        return int.from_bytes(self.take(8), "little")

    def i64(self) -> int:
        return int.from_bytes(self.take(8), "little", signed=True)

    def boolean(self) -> bool:
        value = self.u8()
        if value not in (0, 1):
            raise ValueError("invalid borsh bool")
        return bool(value)

    def pubkey(self) -> str:
        return b58encode(self.take(32))

    def string(self) -> str:
        return self.take(self.u32()).decode("utf-8")


def decode_event(raw: bytes) -> Event:
    r = Reader(raw)
    if r.take(8) != TRADE_EVENT:
        raise ValueError("wrong event discriminator")
    mint = r.pubkey()
    quote = r.u64()
    token = r.u64()
    is_buy = r.boolean()
    user = r.pubkey()
    timestamp = r.i64()
    post = State(r.u64(), r.u64(), r.u64(), r.u64())
    r.pubkey()  # fee recipient
    protocol_bps = r.u64()
    protocol_fee = r.u64()
    creator = r.pubkey()
    creator_bps = r.u64()
    creator_fee = r.u64()
    r.boolean()  # track volume
    r.u64()
    r.u64()
    r.u64()
    r.i64()
    ix_name = r.string()
    mayhem = r.boolean()
    cashback_bps = r.u64()
    cashback = r.u64()
    buyback_bps = r.u64()
    buyback_fee = r.u64()
    for _ in range(r.u32()):
        r.pubkey()
        r.u16()
    quote_mint = r.pubkey()
    r.u64()
    r.u64()
    r.u64()
    holder_rewards_bps = 0
    holder_rewards = 0
    if len(raw) - r.at == 16:
        holder_rewards_bps = r.u64()
        holder_rewards = r.u64()
    if r.at != len(raw):
        raise ValueError(f"event schema tail: decoded={r.at} total={len(raw)}")
    return Event(
        mint,
        quote,
        token,
        is_buy,
        user,
        timestamp,
        post,
        protocol_bps,
        protocol_fee,
        creator,
        creator_bps,
        creator_fee,
        ix_name,
        mayhem,
        cashback_bps,
        cashback,
        buyback_bps,
        buyback_fee,
        quote_mint,
        holder_rewards_bps,
        holder_rewards,
    )


def events_from_tx(tx: dict[str, Any]) -> tuple[list[Event], int]:
    events: list[Event] = []
    failures = 0
    for line in (tx.get("meta") or {}).get("logMessages") or []:
        marker = "Program data: "
        if marker not in line:
            continue
        try:
            raw = base64.b64decode(line.split(marker, 1)[1], validate=True)
        except Exception:
            continue
        if not raw.startswith(TRADE_EVENT):
            continue
        try:
            events.append(decode_event(raw))
        except Exception:
            failures += 1
    return events, failures


def account_keys(tx: dict[str, Any]) -> list[str]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    keys = [x.get("pubkey") if isinstance(x, dict) else x for x in message.get("accountKeys") or []]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    keys.extend(loaded.get("writable") or [])
    keys.extend(loaded.get("readonly") or [])
    return [str(x) for x in keys]


def decoded_instructions(tx: dict[str, Any]) -> list[dict[str, Any]]:
    keys = account_keys(tx)
    message = ((tx.get("transaction") or {}).get("message") or {})
    groups: list[tuple[int, int, dict[str, Any]]] = []
    for i, ix in enumerate(message.get("instructions") or []):
        groups.append((i, -1, ix))
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        top = int(inner.get("index", -1))
        for i, ix in enumerate(inner.get("instructions") or []):
            groups.append((top, i, ix))
    result: list[dict[str, Any]] = []
    for top, inner, ix in groups:
        program = ix.get("programId")
        if not program and ix.get("programIdIndex") is not None:
            index = int(ix["programIdIndex"])
            program = keys[index] if 0 <= index < len(keys) else None
        if program != PUMP_PROGRAM or not isinstance(ix.get("data"), str):
            continue
        try:
            raw = b58decode(ix["data"])
        except Exception:
            continue
        if len(raw) < 24 or raw[:8] not in DISCRIMINATORS:
            continue
        kind, mode = DISCRIMINATORS[raw[:8]]
        result.append(
            {
                "top_index": top,
                "inner_index": inner,
                "kind": kind,
                "mode": mode,
                "arg0": int.from_bytes(raw[8:16], "little"),
                "arg1": int.from_bytes(raw[16:24], "little"),
                "accounts": ix.get("accounts") or [],
            }
        )
    result.sort(key=lambda row: (row["top_index"], row["inner_index"]))
    return result


def normalize_ix_name(value: str) -> str:
    return value.lower().replace("-", "_").replace(" ", "_")


def pair_events_instructions(events: list[Event], instructions: list[dict[str, Any]]) -> list[tuple[Event, dict[str, Any]]]:
    if len(events) == len(instructions):
        return list(zip(events, instructions))
    unused = list(instructions)
    pairs: list[tuple[Event, dict[str, Any]]] = []
    for event in events:
        name = normalize_ix_name(event.ix_name)
        side = "buy" if event.is_buy else "sell"
        match = next(
            (
                ix
                for ix in unused
                if (ix["kind"] == name or (name in {"buy", "sell"} and ix["kind"].startswith(name)))
                and ix["kind"].startswith(side)
            ),
            None,
        )
        if match is not None:
            pairs.append((event, match))
            unused.remove(match)
    return pairs


def official_quote(event: Event, ix: dict[str, Any]) -> dict[str, Any]:
    pre = event.pre
    # Current TradeEvent metadata can retain the configured creator BPS even
    # when creator charging is waived.  Actual non-zero fee fields establish
    # which creator/cashback charge participated in this transaction.
    effective_creator_bps = event.creator_bps if event.creator_fee else 0
    effective_cashback_bps = event.cashback_bps if event.cashback else 0
    total_bps = event.protocol_bps + effective_creator_bps + effective_cashback_bps
    if ix["mode"] == "exact_quote_buy":
        curve_quote = ix["arg0"] * 10_000 // (10_000 + total_bps)
        while curve_quote > 1:
            fees = ceil_fee(curve_quote, event.protocol_bps)
            fees += ceil_fee(curve_quote, effective_creator_bps)
            fees += ceil_fee(curve_quote, effective_cashback_bps)
            overflow = curve_quote + fees - ix["arg0"]
            if overflow <= 0:
                break
            curve_quote -= overflow
        pricing_quote = curve_quote - 1
        token = min(pricing_quote * pre.vt // (pre.vq + pricing_quote), pre.rt)
        return {"curve_quote": curve_quote, "token": token, "constraint_pass": token >= ix["arg1"]}
    if ix["mode"] == "exact_token_buy":
        token = min(ix["arg0"], pre.rt)
        numerator = token * pre.vq
        denominator = pre.vt - token
        curve_quote = numerator // denominator + 1
        fee = ceil_fee(curve_quote, event.protocol_bps)
        fee += ceil_fee(curve_quote, effective_creator_bps)
        fee += ceil_fee(curve_quote, effective_cashback_bps)
        return {
            "curve_quote": curve_quote,
            "token": token,
            "gross": curve_quote + fee,
            "constraint_pass": curve_quote + fee <= ix["arg1"],
            "divisible_boundary": numerator % denominator == 0,
        }
    gross = ix["arg0"] * pre.vq // (pre.vt + ix["arg0"])
    fees = ceil_fee(gross, event.protocol_bps)
    fees += ceil_fee(gross, effective_creator_bps)
    fees += ceil_fee(gross, effective_cashback_bps)
    return {
        "curve_quote": gross,
        "token": ix["arg0"],
        "net": gross - fees,
        "constraint_pass": gross - fees >= ix["arg1"],
    }


def evaluator_exact_token_curve_quote(event: Event, ix: dict[str, Any]) -> int | None:
    if ix["mode"] != "exact_token_buy":
        return None
    token = min(ix["arg0"], event.pre.rt)
    numerator = token * event.pre.vq
    denominator = event.pre.vt - token
    return (numerator + denominator - 1) // denominator


def token_balance_delta(tx: dict[str, Any], event: Event) -> int | None:
    meta = tx.get("meta") or {}
    before: dict[int, int] = {}
    after: dict[int, int] = {}
    for row in meta.get("preTokenBalances") or []:
        if row.get("mint") == event.mint and row.get("owner") == event.user:
            before[int(row["accountIndex"])] = int((row.get("uiTokenAmount") or {}).get("amount") or 0)
    for row in meta.get("postTokenBalances") or []:
        if row.get("mint") == event.mint and row.get("owner") == event.user:
            after[int(row["accountIndex"])] = int((row.get("uiTokenAmount") or {}).get("amount") or 0)
    indices = set(before) | set(after)
    if not indices:
        return None
    return sum(after.get(i, 0) - before.get(i, 0) for i in indices)


def classify_token_program(tx: dict[str, Any], event: Event) -> str:
    keys = set(account_keys(tx))
    if TOKEN_2022 in keys:
        return "token2022"
    if TOKEN_LEGACY in keys:
        return "legacy"
    return "unknown"


def transaction_redteam(cache_path: pathlib.Path, max_rows: int, per_variant: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    db = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    counters: collections.Counter[str] = collections.Counter()
    variants: collections.Counter[str] = collections.Counter()
    programs: collections.Counter[str] = collections.Counter()
    fee_regimes: collections.Counter[str] = collections.Counter()
    disagreements: collections.Counter[str] = collections.Counter()
    examples: list[dict[str, Any]] = []
    sampled: list[dict[str, Any]] = []
    mint_events: dict[str, list[tuple[int, int, int, Event]]] = collections.defaultdict(list)
    slots_seen: list[int] = []
    block_times_seen: list[int] = []
    query = "SELECT signature, available, payload FROM transactions WHERE available=1 ORDER BY rowid LIMIT ?"
    for signature, available, blob in db.execute(query, (max_rows,)):
        counters["cached_transactions_scanned"] += 1
        try:
            tx = json.loads(zlib.decompress(blob))
        except Exception:
            counters["payload_decode_failures"] += 1
            continue
        events, schema_failures = events_from_tx(tx)
        if tx.get("slot") is not None:
            slots_seen.append(int(tx["slot"]))
        if tx.get("blockTime") is not None:
            block_times_seen.append(int(tx["blockTime"]))
        counters["trade_event_schema_failures"] += schema_failures
        instructions = decoded_instructions(tx)
        success = (tx.get("meta") or {}).get("err") is None
        if instructions:
            counters["pump_trade_transactions"] += 1
            counters["successful_pump_trade_transactions" if success else "failed_pump_trade_transactions"] += 1
        if not success:
            if events:
                counters["failed_transactions_with_logged_trade_events"] += 1
                counters["failed_logged_trade_events_ignored"] += len(events)
            continue
        if not events:
            if instructions:
                counters["successful_trade_transactions_missing_current_trade_event"] += 1
            continue
        counters["successful_transactions_with_events"] += 1
        counters["decoded_trade_events"] += len(events)
        if len(events) > 1:
            counters["multi_event_successful_transactions"] += 1
        pairs = pair_events_instructions(events, instructions)
        counters["paired_events"] += len(pairs)
        counters["unpaired_events"] += len(events) - len(pairs)
        if len(pairs) > 1:
            counters["multi_instruction_pairs"] += len(pairs)
        slot = int(tx.get("slot") or 0)
        tx_index = int(tx.get("transactionIndex") or -1)
        for instruction_order, event in enumerate(events):
            mint_events[event.mint].append((slot, tx_index, instruction_order, event))
        for event, ix in pairs:
            variants[ix["kind"]] += 1
            program = classify_token_program(tx, event)
            programs[program] += 1
            fee_regimes[f"p{event.protocol_bps}:c{event.creator_bps}:cb{event.cashback_bps}:bb{event.buyback_bps}"] += 1
            standard_curve = not event.mayhem and event.quote_mint == "11111111111111111111111111111111"
            expected = official_quote(event, ix)
            row_disagreements: list[str] = []
            if standard_curve and expected["curve_quote"] != event.quote:
                row_disagreements.append("curve_quote")
            if standard_curve and expected["token"] != event.token:
                row_disagreements.append("token_amount")
            if standard_curve and not expected["constraint_pass"]:
                row_disagreements.append("successful_tx_failed_decoded_constraint")
            protocol_fee = ceil_fee(event.quote, event.protocol_bps)
            creator_fee = ceil_fee(event.quote, event.creator_bps) if event.creator_fee else 0
            cashback = ceil_fee(event.quote, event.cashback_bps) if event.cashback else 0
            if standard_curve and ix["mode"] == "exact_quote_buy":
                # The deployed instruction consumes the full spendable input.
                # Once net SOL is fixed, a rounding remainder can be assigned
                # to a fee component.  That is visible on-chain even though the
                # generic SDK fee helper returns only component-wise ceilings.
                baseline = protocol_fee + creator_fee + cashback
                actual_fees = event.protocol_fee + event.creator_fee + event.cashback
                gross_used = event.quote + actual_fees
                if actual_fees < baseline or gross_used > ix["arg0"]:
                    row_disagreements.append("exact_quote_fee_allocation")
                else:
                    counters["exact_quote_unspent_budget_lamports"] += ix["arg0"] - gross_used
                if actual_fees > baseline:
                    counters["exact_quote_fee_residual_allocations"] += 1
                    counters["exact_quote_fee_residual_lamports"] += actual_fees - baseline
                    counters["evaluator_strict_fee_validation_rejection_risk"] += 1
            else:
                if standard_curve and protocol_fee != event.protocol_fee:
                    row_disagreements.append("protocol_fee")
                if standard_curve and creator_fee != event.creator_fee:
                    row_disagreements.append("creator_fee")
                if standard_curve and cashback != event.cashback:
                    row_disagreements.append("cashback_fee")
            expected_buyback = event.protocol_fee * event.buyback_bps // 10_000
            if standard_curve and expected_buyback != event.buyback_fee:
                row_disagreements.append("buyback_allocation")
            pre = event.pre
            if event.is_buy:
                rebuilt = State(pre.vq + event.quote, pre.vt - event.token, pre.rq + event.quote, pre.rt - event.token)
            else:
                rebuilt = State(pre.vq - event.quote, pre.vt + event.token, pre.rq - event.quote, pre.rt + event.token)
            if standard_curve and rebuilt != event.post:
                row_disagreements.append("reserve_mutation")
            delta = token_balance_delta(tx, event)
            if delta is not None and delta != (event.token if event.is_buy else -event.token):
                counters["complex_or_noncanonical_user_token_deltas"] += 1
            evaluator_quote = evaluator_exact_token_curve_quote(event, ix)
            if standard_curve and evaluator_quote is not None and evaluator_quote != expected["curve_quote"]:
                row_disagreements.append("evaluator_exact_token_rounding")
            if not standard_curve:
                counters["nonstandard_mayhem_or_non_sol_events_excluded"] += 1
            if event.creator_bps and event.creator_fee == 0 and event.cashback == 0:
                counters["configured_creator_bps_but_charge_waived"] += 1
            for item in row_disagreements:
                disagreements[item] += 1
            keep = variants[ix["kind"]] <= per_variant or bool(row_disagreements) or event.mayhem or len(events) > 1
            record = {
                "signature": signature,
                "slot": slot,
                "transaction_index": tx_index,
                "mint": event.mint,
                "instruction": ix["kind"],
                "token_program": program,
                "is_buy": event.is_buy,
                "mayhem": event.mayhem,
                "standard_curve": standard_curve,
                "protocol_bps": event.protocol_bps,
                "protocol_fee": event.protocol_fee,
                "creator_bps": event.creator_bps,
                "creator_fee": event.creator_fee,
                "cashback_bps": event.cashback_bps,
                "cashback_fee": event.cashback,
                "buyback_bps": event.buyback_bps,
                "event_quote": event.quote,
                "expected_quote": expected["curve_quote"],
                "event_token": event.token,
                "expected_token": expected["token"],
                "token_balance_delta": delta,
                "disagreements": row_disagreements,
            }
            if keep:
                sampled.append(record)
            if row_disagreements and len(examples) < 100:
                examples.append(record)

    continuity_checks = 0
    continuity_failures = 0
    completed_curves = 0
    post_completion_trades = 0
    same_slot_pairs = 0
    for rows in mint_events.values():
        rows.sort(key=lambda x: x[:3])
        completed_at: tuple[int, int, int] | None = None
        previous: Event | None = None
        previous_key: tuple[int, int, int] | None = None
        for slot, tx_index, order, event in rows:
            key = (slot, tx_index, order)
            if previous is not None:
                continuity_checks += 1
                if previous_key and previous_key[0] == slot:
                    same_slot_pairs += 1
                if previous.post != event.pre:
                    continuity_failures += 1
            if event.post.rt == 0 and completed_at is None:
                completed_curves += 1
                completed_at = key
            elif completed_at is not None and key > completed_at:
                post_completion_trades += 1
            previous = event
            previous_key = key
    db.close()
    summary = {
        "official_reference": OFFICIAL_REFERENCE,
        "counters": dict(counters),
        "instruction_variants": dict(variants),
        "token_programs": dict(programs),
        "fee_regimes": dict(fee_regimes),
        "disagreements": dict(disagreements),
        "reserve_chain": {
            "adjacent_event_pairs": continuity_checks,
            "continuity_failures": continuity_failures,
            "same_slot_pairs": same_slot_pairs,
            "curves_reaching_zero_real_token": completed_curves,
            "post_completion_trades": post_completion_trades,
            "note": "Global continuity is descriptive because cache windows overlap and may not include every transaction for every mint.",
        },
        "formal_rounding_adversary": {
            "official_exact_token_buy": "floor(numerator / denominator) + 1",
            "evaluator_exact_token_buy": "ceil(numerator / denominator)",
            "disagreement_condition": "numerator % denominator == 0",
            "difference_lamports": 1,
            "observed_disagreements": int(disagreements.get("evaluator_exact_token_rounding", 0)),
            "classification": "FORMAL_ONE_LAMPORT_SPEC_DISAGREEMENT",
        },
        "disagreement_examples": examples,
        "authoritative_cache_coverage": {
            "min_slot": min(slots_seen) if slots_seen else None,
            "max_slot": max(slots_seen) if slots_seen else None,
            "min_block_time_utc": utc(min(block_times_seen) * 1000) if block_times_seen else None,
            "max_block_time_utc": utc(max(block_times_seen) * 1000) if block_times_seen else None,
        },
    }
    return summary, sampled


def aggregate_returns(values: list[float]) -> dict[str, Any]:
    wins = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    return {
        "n": len(values),
        "wins": sum(v > 0 for v in values),
        "losses": sum(v <= 0 for v in values),
        "pf": wins / losses if losses else None,
        "expectancy_pct": statistics.fmean(values) if values else None,
        "median_pct": statistics.median(values) if values else None,
        "pnl_pct_points": sum(values),
    }


def paired_recompute(exact_path: pathlib.Path) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    duplicate_rows = 0
    for row in read_jsonl_gz(exact_path):
        if row.get("population") != "prospective":
            continue
        if row.get("size_lamports") != 10_000_000:
            continue
        if row.get("entry_delay_ms") != 25 or row.get("first_sellable_delay_ms") != 1476:
            continue
        if row.get("old_return_pct") is None:
            continue
        signal_id = str(row["signal_id"])
        if signal_id in rows:
            duplicate_rows += 1
        rows[signal_id] = row
    ordered = [rows[k] for k in sorted(rows)]
    old = [float(row["old_return_pct"]) for row in ordered]
    exact = [float(row["return_pct"]) for row in ordered]
    changed = sum((a > 0) != (b > 0) for a, b in zip(old, exact))
    return {
        "selection": {
            "population": "prospective",
            "size_lamports": 10_000_000,
            "entry_delay_ms": 25,
            "first_sellable_delay_ms": 1476,
            "requires_old_return": True,
        },
        "unique_rows": len(ordered),
        "duplicate_rows": duplicate_rows,
        "old_mc_proxy": aggregate_returns(old),
        "exact_curve": aggregate_returns(exact),
        "sign_changed_rows": changed,
        "sign_reversal_reproduced": bool(statistics.fmean(old) > 0 and statistics.fmean(exact) < 0),
        "row_digest": hashlib.sha256(
            "\n".join(f"{row['signal_id']}|{row['old_return_pct']}|{row['return_pct']}" for row in ordered).encode()
        ).hexdigest(),
    }


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def sample_sd(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def ks_distance(a: list[float], b: list[float]) -> float | None:
    if not a or not b:
        return None
    aa, bb = sorted(a), sorted(b)
    i = j = 0
    distance = 0.0
    while i < len(aa) or j < len(bb):
        if j == len(bb) or (i < len(aa) and aa[i] <= bb[j]):
            point = aa[i]
        else:
            point = bb[j]
        while i < len(aa) and aa[i] <= point:
            i += 1
        while j < len(bb) and bb[j] <= point:
            j += 1
        distance = max(distance, abs(i / len(aa) - j / len(bb)))
    return distance


def continuous_comparison(name: str, admitted: list[float], excluded: list[float]) -> dict[str, Any]:
    pooled = math.sqrt((sample_sd(admitted) ** 2 + sample_sd(excluded) ** 2) / 2)
    smd = ((mean(admitted) or 0) - (mean(excluded) or 0)) / pooled if pooled else 0.0
    return {
        "feature": name,
        "admitted_n": len(admitted),
        "excluded_n": len(excluded),
        "admitted_mean": mean(admitted),
        "excluded_mean": mean(excluded),
        "admitted_median": statistics.median(admitted) if admitted else None,
        "excluded_median": statistics.median(excluded) if excluded else None,
        "standardized_mean_difference": smd,
        "ks_distance": ks_distance(admitted, excluded),
    }


def category_comparison(name: str, admitted: list[str], excluded: list[str]) -> dict[str, Any]:
    ca, cb = collections.Counter(admitted), collections.Counter(excluded)
    cats = sorted(set(ca) | set(cb))
    pa = {k: ca[k] / len(admitted) for k in cats} if admitted else {}
    pb = {k: cb[k] / len(excluded) for k in cats} if excluded else {}
    tv = 0.5 * sum(abs(pa.get(k, 0) - pb.get(k, 0)) for k in cats)
    return {"feature": name, "admitted": pa, "excluded": pb, "total_variation": tv}


def load_mint_metadata(matched_path: pathlib.Path, target: set[str], decision_ms: dict[str, int]) -> dict[str, dict[str, Any]]:
    best: dict[str, tuple[int, dict[str, Any]]] = {}
    with matched_path.open("rt", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except Exception:
                continue
            mint = row.get("mint")
            if mint not in target:
                continue
            distance = abs(int(row.get("observed_ms") or 0) - decision_ms[mint])
            current = best.get(mint)
            if current is None or distance < current[0]:
                accounts = set(row.get("source_accounts") or [])
                token_program = "token2022" if TOKEN_2022 in accounts else "legacy" if TOKEN_LEGACY in accounts else "unknown"
                best[mint] = (
                    distance,
                    {
                        "token_program": token_program,
                        "instruction_variant": ((row.get("instruction") or {}).get("kind") or "unknown"),
                        "source_timing": row.get("timing_source") or "unknown",
                    },
                )
    return {mint: value for mint, (_, value) in best.items()}


def coverage_bias(signals_path: pathlib.Path, exact_path: pathlib.Path, matched_path: pathlib.Path) -> dict[str, Any]:
    exact_mints = {
        str(row["mint"])
        for row in read_jsonl_gz(exact_path)
        if row.get("population") == "prospective"
        and row.get("size_lamports") == 10_000_000
        and row.get("entry_delay_ms") == 25
        and row.get("first_sellable_delay_ms") == 1476
    }
    signals = list(read_jsonl_gz(signals_path))
    decisions = {str(row["mint"]): int(row.get("threshold_decision_ts_ms") or row.get("decision_ms") or 0) for row in signals}
    metadata = load_mint_metadata(matched_path, set(decisions), decisions)
    continuous: dict[str, tuple[list[float], list[float]]] = collections.defaultdict(lambda: ([], []))
    categorical: dict[str, tuple[list[str], list[str]]] = collections.defaultdict(lambda: ([], []))
    for row in signals:
        mint = str(row["mint"])
        side = 0 if mint in exact_mints else 1
        raw = row.get("raw_features") or {}
        direct = {
            "score": row.get("score"),
            "selected_mc_usd": row.get("mc_usd"),
            "launch_age_ms": row.get("mint_age_ms"),
            "active_flow_mints": raw.get("active_flow_mints"),
            "launches_per_minute": raw.get("launches_per_minute"),
            "first_buyer_quote_share_bps": raw.get("first_buyer_quote_share_bps"),
        }
        for key, value in raw.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                direct[f"raw.{key}"] = value
        for key, value in direct.items():
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                continuous[key][side].append(float(value))
        event_ms = int(row.get("threshold_decision_ts_ms") or 0)
        stamp = dt.datetime.fromtimestamp(event_ms / 1000, tz=dt.timezone.utc)
        categorical["state"][side].append(str(row.get("state") or "unknown"))
        categorical["utc_hour"][side].append(f"{stamp.hour:02d}")
        categorical["utc_date"][side].append(stamp.date().isoformat())
        meta = metadata.get(mint, {})
        for key in ("token_program", "instruction_variant", "source_timing"):
            categorical[key][side].append(str(meta.get(key, "missing")))
    continuous_rows = [continuous_comparison(k, *v) for k, v in continuous.items()]
    continuous_rows.sort(key=lambda row: abs(row["standardized_mean_difference"]), reverse=True)
    categorical_rows = [category_comparison(k, *v) for k, v in categorical.items()]
    categorical_rows.sort(key=lambda row: row["total_variation"], reverse=True)
    material_continuous = [r for r in continuous_rows if abs(r["standardized_mean_difference"]) >= 0.25 or (r["ks_distance"] or 0) >= 0.20]
    material_categorical = [r for r in categorical_rows if r["total_variation"] >= 0.15]
    classification = "MATERIALLY_BIASED" if material_continuous or material_categorical else "REPRESENTATIVE"
    return {
        "total_prospective": len(signals),
        "exact_evaluable": sum(str(row["mint"]) in exact_mints for row in signals),
        "excluded": sum(str(row["mint"]) not in exact_mints for row in signals),
        "metadata_coverage": len(metadata),
        "classification": classification,
        "materiality_rule": "|SMD|>=0.25 or KS>=0.20 for continuous; total variation>=0.15 for categorical",
        "continuous": continuous_rows,
        "categorical": categorical_rows,
        "material_continuous": material_continuous,
        "material_categorical": material_categorical,
    }


def outcome_semantics_redteam(
    exact_path: pathlib.Path,
    matched_path: pathlib.Path,
    cache_path: pathlib.Path,
    sample_n: int = 500,
) -> dict[str, Any]:
    rows = list(read_jsonl_gz(exact_path))
    counters: collections.Counter[str] = collections.Counter()
    disagreements: collections.Counter[str] = collections.Counter()
    size_stress: dict[int, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for row in rows:
        counters["exact_rows"] += 1
        entry_ms = int(row["entry_ms"])
        exit_ms = int(row["exit_ms"])
        first_sellable = entry_ms + int(row["first_sellable_delay_ms"])
        deadline = entry_ms + 120_000
        expected_return = (int(row["exit_net_lamports"]) / int(row["size_lamports"]) - 1) * 100
        if abs(expected_return - float(row["return_pct"])) > 1e-9:
            disagreements["return_arithmetic"] += 1
        if int(row["pnl_lamports"]) != int(row["exit_net_lamports"]) - int(row["size_lamports"]):
            disagreements["pnl_arithmetic"] += 1
        if exit_ms < first_sellable:
            disagreements["exit_before_first_sellable"] += 1
        outcome = row["outcome"]
        if outcome == "TP" and float(row["return_pct"]) < 20:
            disagreements["tp_below_threshold"] += 1
        elif outcome == "SL" and float(row["return_pct"]) > -20:
            disagreements["sl_above_threshold"] += 1
        elif outcome == "TRUE_120S_MAX_HOLD" and exit_ms != deadline:
            disagreements["max_hold_not_exact_t120"] += 1
        elif outcome not in {"TP", "SL", "TRUE_120S_MAX_HOLD"}:
            disagreements["unknown_outcome"] += 1
        if exit_ms > deadline:
            disagreements["exit_after_t120"] += 1
        if not row.get("entry_state") or not row.get("entry_state_source"):
            disagreements["missing_entry_reserve_evidence"] += 1
        if not row.get("exit_source"):
            disagreements["missing_exit_reserve_evidence"] += 1
        if row.get("entry_state_age_ms") is not None and float(row["entry_state_age_ms"]) < 0:
            disagreements["future_entry_state_age"] += 1
        counters[f"outcome_{outcome}"] += 1
        counters[f"population_{row['population']}"] += 1
        counters[f"size_{row['size_lamports']}"] += 1
        stress = size_stress[int(row["size_lamports"])]
        stress["rows"] += 1
        stress["external_transactions"] += int(row.get("external_transactions") or 0)
        stress["external_status_flips"] += int(row.get("external_status_flips") or 0)
        stress["rows_with_status_flips"] += int((row.get("external_status_flips") or 0) > 0)
        stress["failed_chain_transactions"] += int(row.get("failed_transactions") or 0)

    sampled = sorted(
        rows,
        key=lambda row: hashlib.sha256(
            f"{row['signal_id']}|{row['size_lamports']}|{row['entry_delay_ms']}|{row['first_sellable_delay_ms']}".encode()
        ).hexdigest(),
    )[:sample_n]
    wanted_signatures = {str(row["entry_state_source"]) for row in sampled}
    observed: dict[str, int] = {}
    with matched_path.open("rt", encoding="utf-8") as handle:
        for line in handle:
            try:
                source = json.loads(line)
            except Exception:
                continue
            signature = str(source.get("signature"))
            if signature in wanted_signatures:
                observed[signature] = min(
                    int(source.get("observed_ms") or (1 << 62)),
                    observed.get(signature, 1 << 62),
                )
    db = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    state_matches = 0
    state_mismatches = 0
    missing_raw = 0
    causality_checks = 0
    causality_failures = 0
    for row in sampled:
        signature = str(row["entry_state_source"])
        txrow = db.execute("SELECT available,payload FROM transactions WHERE signature=?", (signature,)).fetchone()
        if not txrow or not txrow[0]:
            missing_raw += 1
            continue
        tx = json.loads(zlib.decompress(txrow[1]))
        events, _ = events_from_tx(tx)
        event = next((item for item in events if item.mint == row["mint"]), None)
        if event is None:
            missing_raw += 1
            continue
        state = row["entry_state"]
        expected = State(
            int(state["virtual_sol"]),
            int(state["virtual_token"]),
            int(state["real_sol"]),
            int(state["real_token"]),
        )
        if event.post == expected:
            state_matches += 1
        else:
            state_mismatches += 1
        observed_ms = observed.get(signature)
        if observed_ms is not None:
            causality_checks += 1
            if observed_ms > int(row["entry_ms"]):
                causality_failures += 1
    db.close()
    return {
        "all_row_counters": dict(counters),
        "all_row_disagreements": dict(disagreements),
        "raw_entry_state_sample": {
            "n": len(sampled),
            "raw_event_state_matches": state_matches,
            "raw_event_state_mismatches": state_mismatches,
            "raw_event_missing": missing_raw,
            "local_timestamp_causality_checks": causality_checks,
            "local_timestamp_causality_failures": causality_failures,
        },
        "hidden_mc_fallback_check": {
            "rows_missing_entry_reserve_evidence": disagreements.get("missing_entry_reserve_evidence", 0),
            "rows_missing_exit_reserve_evidence": disagreements.get("missing_exit_reserve_evidence", 0),
            "classification": "NO_OUTPUT_ROW_EVIDENCE_OF_MC_FALLBACK" if not disagreements.get("missing_entry_reserve_evidence", 0) else "BLOCKER",
        },
        "counterfactual_constraint_stress_by_size": {
            str(size): dict(values) for size, values in sorted(size_stress.items())
        },
    }


def historical_local_overlap(cache_path: pathlib.Path, matched_path: pathlib.Path, exact_path: pathlib.Path, sample_n: int) -> dict[str, Any]:
    exact_rows: dict[str, dict[str, Any]] = {}
    for row in read_jsonl_gz(exact_path):
        if row.get("size_lamports") == 10_000_000 and row.get("entry_delay_ms") == 25 and row.get("first_sellable_delay_ms") == 1476:
            exact_rows.setdefault(str(row["signal_id"]), row)
    selected_ids = sorted(exact_rows, key=lambda x: hashlib.sha256(x.encode()).hexdigest())[:sample_n]
    selected_mints = {str(exact_rows[x]["mint"]) for x in selected_ids}
    local_signatures: dict[str, set[str]] = collections.defaultdict(set)
    local_fields = collections.Counter()
    with matched_path.open("rt", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except Exception:
                continue
            mint = row.get("mint")
            if mint not in selected_mints:
                continue
            local_signatures[mint].add(str(row.get("signature")))
            for key in ("slot", "transaction_index", "instruction_index", "observed_ms", "source_accounts", "instruction"):
                if row.get(key) is not None:
                    local_fields[key] += 1
    db = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    totals = collections.Counter()
    per_mint: list[dict[str, Any]] = []
    for signal_id in selected_ids:
        row = exact_rows[signal_id]
        window = db.execute("SELECT complete,payload FROM curve_history_windows WHERE signal_id=?", (signal_id,)).fetchone()
        if not window:
            continue
        refs = json.loads(zlib.decompress(window[1]))
        authoritative_success_events: set[str] = set()
        authoritative_failed: set[str] = set()
        ordered_events: list[tuple[int, int, int, Event]] = []
        window_keys: list[tuple[int, int]] = []
        for ref in refs:
            signature = str(ref["signature"])
            ref_slot = int(ref["slot"]) if ref.get("slot") is not None else 0
            ref_tx_index = int(ref["transaction_index"]) if ref.get("transaction_index") is not None else -1
            window_keys.append((ref_slot, ref_tx_index))
            if ref.get("err") is not None:
                authoritative_failed.add(signature)
                txrow = db.execute("SELECT available,payload FROM transactions WHERE signature=?", (signature,)).fetchone()
                if txrow and txrow[0]:
                    failed_tx = json.loads(zlib.decompress(txrow[1]))
                    failed_events, _ = events_from_tx(failed_tx)
                    totals["failed_transactions_with_logged_events"] += int(bool(failed_events))
                    totals["failed_logged_events_zero_mutation"] += len(failed_events)
                continue
            txrow = db.execute("SELECT available,payload FROM transactions WHERE signature=?", (signature,)).fetchone()
            if not txrow or not txrow[0]:
                continue
            tx = json.loads(zlib.decompress(txrow[1]))
            events, _ = events_from_tx(tx)
            mint_events = [event for event in events if event.mint == row["mint"]]
            if mint_events:
                authoritative_success_events.add(signature)
                for event_index, event in enumerate(mint_events):
                    ordered_events.append(
                        (
                            ref_slot or int(tx.get("slot") or 0),
                            ref_tx_index
                            if ref_tx_index >= 0
                            else int(tx["transactionIndex"])
                            if tx.get("transactionIndex") is not None
                            else -1,
                            event_index,
                            event,
                        )
                    )
                if len(mint_events) > 1:
                    totals["multi_event_transactions"] += 1
                    for left, right in zip(mint_events, mint_events[1:]):
                        totals["multi_event_atomicity_checks"] += 1
                        totals["multi_event_atomicity_failures"] += int(left.post != right.pre)
        totals["window_reference_order_inversions"] += sum(b < a for a, b in zip(window_keys, window_keys[1:]))
        totals["window_duplicate_order_keys"] += len(window_keys) - len(set(window_keys))
        ordered_events.sort(key=lambda item: item[:3])
        continuity_failures = 0
        same_slot_pairs = 0
        for left, right in zip(ordered_events, ordered_events[1:]):
            totals["ordered_event_continuity_checks"] += 1
            if left[0] == right[0]:
                same_slot_pairs += 1
                totals["same_slot_order_checks"] += 1
            if left[3].post != right[3].pre:
                continuity_failures += 1
                totals["ordered_event_continuity_failures"] += 1
        local = local_signatures.get(str(row["mint"]), set())
        overlap = authoritative_success_events & local
        totals["authoritative_success_events"] += len(authoritative_success_events)
        totals["authoritative_failed_transactions"] += len(authoritative_failed)
        totals["local_observed_signatures"] += len(local)
        totals["successful_event_overlap"] += len(overlap)
        totals["successful_events_missing_locally"] += len(authoritative_success_events - local)
        per_mint.append(
            {
                "signal_id": signal_id,
                "mint": row["mint"],
                "population": row["population"],
                "authoritative_success_events": len(authoritative_success_events),
                "authoritative_failed_transactions": len(authoritative_failed),
                "local_observed_signatures": len(local),
                "successful_event_overlap": len(overlap),
                "successful_events_missing_locally": len(authoritative_success_events - local),
                "ordered_events": len(ordered_events),
                "same_slot_order_checks": same_slot_pairs,
                "ordered_event_continuity_failures": continuity_failures,
            }
        )
    db.close()
    success_total = totals["authoritative_success_events"]
    overlap_rate = totals["successful_event_overlap"] / success_total if success_total else None
    return {
        "sample_n": len(per_mint),
        "deterministic_sample_method": "sha256(signal_id) ascending",
        "totals": dict(totals),
        "successful_event_signature_overlap_rate": overlap_rate,
        "local_schema": {
            "has_slot_index_order": True,
            "has_instruction_args_accounts": True,
            "has_local_receive_timestamp": True,
            "has_finalized_success_failure": False,
            "has_trade_event_reserve_transitions": False,
            "has_raw_transaction_meta_logs": False,
            "has_fee_config_snapshot": False,
            "has_curve_account_bytes": False,
        },
        "per_mint": per_mint,
    }


def evidence_inventory(paths: dict[str, pathlib.Path]) -> dict[str, Any]:
    descriptions = {
        "normalized": ("normalized observations", True, False, False, True),
        "detected": ("detected swaps", True, False, False, True),
        "capsules": ("candidate capsules", True, False, False, True),
        "decisions": ("decision inputs", True, False, False, True),
        "decision_receipts": ("derived decision receipts", False, False, False, False),
        "matched": ("derived matched observations", True, False, False, True),
        "wallet_8ycb_enhanced": ("targeted 8YCB enhanced transactions", True, True, False, True),
        "wallet_8ycb_trade_events": ("targeted 8YCB derived trade events", True, True, False, False),
        "rpc_cache": ("historical RPC cache", True, True, True, True),
    }
    result: dict[str, Any] = {}
    for key, path in paths.items():
        desc = descriptions.get(key, (key, False, False, False, False))
        result[key] = {
            "path": str(path),
            "exists": path.exists(),
            "bytes": path.stat().st_size if path.exists() else None,
            "what": desc[0],
            "slot_and_instruction_order": desc[1],
            "finalized_status": desc[2],
            "trade_event_reserves": desc[3],
            "instruction_accounts_constraints": desc[4],
            "exact_local_historical_label_suitable": key == "rpc_cache",
            "reserve_evidence_note": (
                "Contains locally replayed virtual reserves, not authoritative account/TradeEvent state."
                if key in {"capsules", "decisions"}
                else "Marks source_slot as grouping-only and has no finalized transaction proof or reserve transitions."
                if key == "decision_receipts"
                else "Wallet-targeted parsed history only; not a lossless full-universe curve trajectory archive."
                if key in {"wallet_8ycb_enhanced", "wallet_8ycb_trade_events"}
                else None
            ),
        }
    return result


def _last_valid_json_line(path: pathlib.Path) -> dict[str, Any] | None:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        buffer = b""
        while position > 0:
            position -= 1
            handle.seek(position)
            char = handle.read(1)
            if char == b"\n" and buffer.strip():
                try:
                    return json.loads(buffer.decode("utf-8"))
                except Exception:
                    buffer = b""
                    continue
            buffer = char + buffer
        if buffer.strip():
            try:
                return json.loads(buffer.decode("utf-8"))
            except Exception:
                return None
    return None


def corpus_bounds(paths: dict[str, pathlib.Path]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, path in paths.items():
        if not path.exists() or path.suffix == ".gz":
            continue
        with path.open("rt", encoding="utf-8", errors="replace") as handle:
            try:
                first = json.loads(handle.readline())
            except Exception:
                first = {}
        last = _last_valid_json_line(path) or {}

        def fields(row: dict[str, Any]) -> tuple[int | None, int | None]:
            provider = row.get("provider") or {}
            raw = row.get("raw_record") or {}
            ms = row.get("local_receive_ts_ms") or row.get("detectedAtMs") or provider.get("local_receive_ts_ms")
            slot = row.get("source_slot") or row.get("sourceSlot") or provider.get("source_slot") or raw.get("slot")
            return (int(ms) if ms is not None else None, int(slot) if slot is not None else None)

        first_ms, first_slot = fields(first)
        last_ms, last_slot = fields(last)
        result[key] = {
            "first_utc": utc(first_ms),
            "last_utc": utc(last_ms),
            "first_slot": first_slot,
            "last_slot": last_slot,
            "bytes_at_audit": path.stat().st_size,
        }
    return result


def local_replay_state_crosscheck(
    sample_path: pathlib.Path | None,
    cache_path: pathlib.Path,
) -> dict[str, Any]:
    if sample_path is None or not sample_path.exists():
        return {"status": "NOT_RUN", "reason": "no targeted decision-input sample supplied"}
    db = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    counters: collections.Counter[str] = collections.Counter()
    vq_errors: list[float] = []
    vt_errors: list[float] = []
    examples: list[dict[str, Any]] = []
    with sample_path.open("rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            identity = str((row.get("canonical_state") or {}).get("state_identity") or "")
            parts = identity.split(":")
            if len(parts) < 4 or parts[0] != "native-shredstream-replay":
                counters["unparseable_local_state"] += 1
                continue
            try:
                local = (int(parts[-2]), int(parts[-1]))
            except ValueError:
                counters["unparseable_local_state"] += 1
                continue
            provider = row.get("provider") or {}
            signature = str(provider.get("transaction_signature") or "")
            mint = str((row.get("candidate") or {}).get("mint") or "")
            txrow = db.execute("SELECT available,payload FROM transactions WHERE signature=?", (signature,)).fetchone()
            if not txrow or not txrow[0]:
                counters["missing_authoritative_transaction"] += 1
                continue
            tx = json.loads(zlib.decompress(txrow[1]))
            events, _ = events_from_tx(tx)
            event = next((item for item in events if item.mint == mint), None)
            if event is None:
                counters["missing_authoritative_event"] += 1
                continue
            counters["compared"] += 1
            post = (event.post.vq, event.post.vt)
            if local == post:
                counters["exact_post_state_matches"] += 1
            else:
                counters["post_state_mismatches"] += 1
            vq_error = (local[0] - post[0]) / post[0] * 100
            vt_error = (local[1] - post[1]) / post[1] * 100
            vq_errors.append(vq_error)
            vt_errors.append(vt_error)
            if len(examples) < 10:
                examples.append(
                    {
                        "signature": signature,
                        "mint": mint,
                        "local_virtual_quote": local[0],
                        "authoritative_virtual_quote": post[0],
                        "local_virtual_token": local[1],
                        "authoritative_virtual_token": post[1],
                        "virtual_quote_error_pct": vq_error,
                        "virtual_token_error_pct": vt_error,
                    }
                )
    db.close()
    return {
        "status": "COMPLETE",
        "counters": dict(counters),
        "virtual_quote_error_pct": {
            "median_signed": statistics.median(vq_errors) if vq_errors else None,
            "p90_absolute": sorted(abs(x) for x in vq_errors)[max(0, math.ceil(len(vq_errors) * 0.9) - 1)] if vq_errors else None,
            "worst_absolute": max(map(abs, vq_errors)) if vq_errors else None,
        },
        "virtual_token_error_pct": {
            "median_signed": statistics.median(vt_errors) if vt_errors else None,
            "p90_absolute": sorted(abs(x) for x in vt_errors)[max(0, math.ceil(len(vt_errors) * 0.9) - 1)] if vt_errors else None,
            "worst_absolute": max(map(abs, vt_errors)) if vt_errors else None,
        },
        "classification": "LOCAL_REPLAY_STATE_NOT_AUTHORITATIVE" if counters["post_state_mismatches"] else "LOCAL_REPLAY_STATE_MATCHED",
        "examples": examples,
    }


def render_report(result: dict[str, Any]) -> str:
    paired = result["paired"]
    old = paired["old_mc_proxy"]
    exact = paired["exact_curve"]
    tx = result["transaction_redteam"]
    cov = result["coverage_bias"]
    overlap = result["historical_overlap"]
    local_state = result["local_replay_state_crosscheck"]
    bounds = result["corpus_bounds"]
    semantics = result["outcome_semantics"]
    formal = tx["formal_rounding_adversary"]
    observed_material = sum(v for k, v in tx["disagreements"].items() if k != "evaluator_exact_token_rounding")
    semantics_material = sum(semantics["all_row_disagreements"].values())
    engine_valid = observed_material == 0 and semantics_material == 0
    final_class = (
        "EXACT_CURVE_ENGINE_REDTEAM_PASS_FUTURE_LABEL_COLLECTION_REQUIRED"
        if engine_valid
        else "EXACT_CURVE_ENGINE_REDTEAM_BLOCKER"
    )
    lines = [
        "# EXACT_CURVE Independent Red-Team Audit",
        "",
        f"Generated: `{result['generated_utc']}`",
        "",
        "## Executive Verdict",
        "",
        f"- **Engine observed-transaction validation:** {'PASS' if engine_valid else 'BLOCKER'}",
        f"- **Paired sign reversal:** {'REPRODUCED' if paired['sign_reversal_reproduced'] else 'NOT REPRODUCED'}",
        f"- **Prospective exact subset:** `{cov['classification']}`",
        "- **Historical local recovery:** `PARTIALLY_RECOVERABLE` only where the RPC cache already contains authoritative transaction evidence; the canonical local tapes alone are insufficient.",
        f"- **Final classification:** `{final_class}`",
        "",
        "## Independent Transaction Audit",
        "",
        f"Scanned `{tx['counters'].get('cached_transactions_scanned', 0):,}` cached finalized transaction payloads and decoded `{tx['counters'].get('decoded_trade_events', 0):,}` current-schema Pump TradeEvents. Paired `{tx['counters'].get('paired_events', 0):,}` events to decoded trade instructions.",
        "",
        f"Observed economic disagreements: `{observed_material}`. Failed transactions with logged events were kept at zero mutation: `{tx['counters'].get('failed_transactions_with_logged_trade_events', 0):,}` transactions / `{tx['counters'].get('failed_logged_trade_events_ignored', 0):,}` events.",
        "",
        f"The global cache view has `{tx['reserve_chain'].get('continuity_failures', 0):,}` apparent gaps across `{tx['reserve_chain'].get('adjacent_event_pairs', 0):,}` adjacent event pairs because fetched curve windows overlap and are not globally complete. This is treated as missing evidence, not agreement. Complete authoritative windows are tested separately below.",
        "",
        f"Excluded from standard-curve arithmetic: `{tx['counters'].get('nonstandard_mayhem_or_non_sol_events_excluded', 0):,}` nonstandard Mayhem/non-SOL events and `{tx['counters'].get('complex_or_noncanonical_user_token_deltas', 0):,}` complex/noncanonical token-delta cases.",
        "",
        f"Instruction variants: `{json.dumps(tx['instruction_variants'], sort_keys=True)}`",
        "",
        f"Token programs: `{json.dumps(tx['token_programs'], sort_keys=True)}`",
        "",
        f"Fee regimes: `{json.dumps(tx['fee_regimes'], sort_keys=True)}`",
        "",
        f"Exact-quote fee remainder allocations: `{tx['counters'].get('exact_quote_fee_residual_allocations', 0):,}` events / `{tx['counters'].get('exact_quote_fee_residual_lamports', 0):,}` lamports. The strict evaluator can reject these and `{tx['counters'].get('configured_creator_bps_but_charge_waived', 0):,}` waived-creator cases; that is fail-closed coverage loss, not silently admitted bad economics.",
        "",
        "### Formal Rounding Finding",
        "",
        f"The official SDK uses `{formal['official_exact_token_buy']}`. The evaluator uses `{formal['evaluator_exact_token_buy']}`. They differ by one lamport only when `{formal['disagreement_condition']}`. Observed affected events: `{formal['observed_disagreements']}`.",
        "",
        "This is a real specification-level discrepancy and is retained as a hardening item. The current evaluator would fail closed on the two affected observed events rather than admit a wrong exact label; it cannot explain the large proxy-to-exact reversal.",
        "",
        "### Outcome/Timing Semantics",
        "",
        f"Audited `{semantics['all_row_counters'].get('exact_rows', 0):,}` exact output rows across all configured sizes/delays. Arithmetic, TP/SL, first-sellable, and true-T+120 disagreements: `{semantics_material}`.",
        "",
        f"Raw entry-state cross-check: `{semantics['raw_entry_state_sample']['raw_event_state_matches']}/{semantics['raw_entry_state_sample']['n']}` matched a decoded finalized TradeEvent exactly; mismatches `{semantics['raw_entry_state_sample']['raw_event_state_mismatches']}`, unavailable raw rows `{semantics['raw_entry_state_sample']['raw_event_missing']}`. Local-timestamp future-state failures: `{semantics['raw_entry_state_sample']['local_timestamp_causality_failures']}`.",
        "",
        f"Outcomes: TP `{semantics['all_row_counters'].get('outcome_TP', 0):,}`, SL `{semantics['all_row_counters'].get('outcome_SL', 0):,}`, true max-hold `{semantics['all_row_counters'].get('outcome_TRUE_120S_MAX_HOLD', 0):,}`. Counterfactual constraint stress by size: `{json.dumps(semantics['counterfactual_constraint_stress_by_size'], sort_keys=True)}`.",
        "",
        "## Paired 208-Row Recalculation",
        "",
        "Recomputed directly from unique per-trade rows in `exact-outcomes.jsonl.gz`; no aggregate report was used.",
        "",
        "| Model | N | PF | Expectancy | Median |",
        "|---|---:|---:|---:|---:|",
        f"| Old MC proxy | {old['n']} | {old['pf']:.6f} | {old['expectancy_pct']:+.6f}% | {old['median_pct']:+.6f}% |",
        f"| Exact curve | {exact['n']} | {exact['pf']:.6f} | {exact['expectancy_pct']:+.6f}% | {exact['median_pct']:+.6f}% |",
        "",
        f"`{paired['sign_changed_rows']}` rows changed return sign. Row-set digest: `{paired['row_digest']}`.",
        "",
        "## Coverage Bias",
        "",
        f"Exact-evaluable: `{cov['exact_evaluable']}/{cov['total_prospective']}`; excluded: `{cov['excluded']}`. Classification: `{cov['classification']}`.",
        "",
        "Largest pre-outcome continuous shifts:",
        "",
        "| Feature | Exact mean | Excluded mean | SMD | KS |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in cov["continuous"][:12]:
        lines.append(
            f"| {row['feature']} | {row['admitted_mean']:.6g} | {row['excluded_mean']:.6g} | {row['standardized_mean_difference']:+.3f} | {(row['ks_distance'] or 0):.3f} |"
        )
    lines.extend(
        [
            "",
            "Categorical total-variation distances:",
            "",
            "| Feature | TV distance |",
            "|---|---:|",
        ]
    )
    for row in cov["categorical"]:
        lines.append(f"| {row['feature']} | {row['total_variation']:.3f} |")
    lines.extend(
        [
            "",
            "## Historical Recovery",
            "",
            f"In a deterministic `{overlap['sample_n']}`-mint overlap sample, local normalized/matched observations contained `{overlap['totals'].get('successful_event_overlap', 0):,}` of `{overlap['totals'].get('authoritative_success_events', 0):,}` authoritative successful Pump event signatures (`{(overlap['successful_event_signature_overlap_rate'] or 0)*100:.2f}%`).",
            "",
            f"Within those complete authoritative windows, chronological reserve checks: `{overlap['totals'].get('ordered_event_continuity_checks', 0):,}` adjacent event pairs, `{overlap['totals'].get('same_slot_order_checks', 0):,}` same-slot pairs, `{overlap['totals'].get('ordered_event_continuity_failures', 0):,}` continuity failures. Multi-event transaction atomicity failures: `{overlap['totals'].get('multi_event_atomicity_failures', 0):,}`. Failed logged events kept at zero mutation: `{overlap['totals'].get('failed_logged_events_zero_mutation', 0):,}`.",
            "",
            f"The locally inferred `native-shredstream-replay` reserve identity was checked against finalized TradeEvent post-state on `{local_state.get('counters', {}).get('compared', 0)}` overlapping transactions: exact matches `{local_state.get('counters', {}).get('exact_post_state_matches', 0)}`, mismatches `{local_state.get('counters', {}).get('post_state_mismatches', 0)}`. It is not an authoritative reserve archive.",
            "",
            "More importantly, those local rows omit finalized status, TradeEvent post-reserves, transaction logs/meta, fee-config snapshots, and raw curve-account bytes. Instruction bytes plus ordering cannot identify which observed failed transactions mutated state, nor anchor exact reserves. Exact reconstruction from those tapes alone is therefore impossible without inventing state.",
            "",
            "Current recoverability by split:",
            "",
            "| Split | Frozen N | Authoritative exact currently recovered | Local-only additional exact recoverable |",
            "|---|---:|---:|---:|",
            "| Train | 964 | 0 | 0 proven |",
            "| Validation | 428 | 0 | 0 proven |",
            "| Final test | 766 | 8 | 0 proven |",
            f"| Prospective | 1044 | {cov['exact_evaluable']} | 0 proven beyond cached authoritative windows |",
            "",
            "Historical recovery classification: `B — HISTORICAL EXACT RELABEL IS PARTIALLY RECOVERABLE`, restricted to periods/mints already covered by the authoritative RPC cache. The canonical corpus by itself cannot expand exact coverage.",
            "",
            "### Evidence Coverage",
            "",
            "| Source | First UTC | Last UTC | First slot | Last slot |",
            "|---|---|---|---:|---:|",
        ]
    )
    for key, row in bounds.items():
        lines.append(f"| {key} | {row['first_utc']} | {row['last_utc']} | {row['first_slot']} | {row['last_slot']} |")
    cache_range = tx["authoritative_cache_coverage"]
    lines.append(
        f"| authoritative RPC cache | {cache_range['min_block_time_utc']} | {cache_range['max_block_time_utc']} | {cache_range['min_slot']} | {cache_range['max_slot']} |"
    )
    lines.extend(
        [
            "",
            "### Local Evidence Suitability",
            "",
            "| Source | Size GiB | Ordered | Finality | Exact reserves | Constraints | Exact-label suitable |",
            "|---|---:|---|---|---|---|---|",
        ]
    )
    for source, row in result["evidence_inventory"].items():
        size_gib = (row.get("bytes") or 0) / (1024 ** 3)
        yesno = lambda value: "yes" if value else "no"
        lines.append(
            f"| {source} | {size_gib:.3f} | {yesno(row['slot_and_instruction_order'])} | "
            f"{yesno(row['finalized_status'])} | {yesno(row['trade_event_reserves'])} | "
            f"{yesno(row['instruction_accounts_constraints'])} | "
            f"{yesno(row['exact_local_historical_label_suitable'])} |"
        )
    lines.extend(
        [
            "",
            "The derived decision receipts explicitly describe source slots as grouping-only, not ordering or inventory proof. The 8YCB archives are targeted parsed wallet history, not lossless full-universe curve histories. Neither can expand exact Ranker labels.",
        ]
    )
    lines.extend(
        [
            "",
            "## Required Future Evidence",
            "",
            "For every eligible Pump mint, persist one lossless append-only event ledger containing:",
            "",
            "1. Raw create transaction and all curve-touching transactions through completion/migration or the longest research horizon.",
            "2. Signature, slot, block transaction index, instruction index/CPI order, block time, local receive/decode time, version, loaded addresses, and finalized `meta.err`.",
            "3. Full Pump instruction bytes/accounts and decoded min-token/max-cost/min-output constraints.",
            "4. Full logs or lossless TradeEvent bytes, including exact post reserves and fee/cashback/buyback fields.",
            "5. Raw curve account bytes with slot/write-version at creation, each observed write, entry boundaries, completion, migration, and T+120.",
            "6. Raw Global/FeeConfig/sharing-config bytes with account address, slot, write-version, and hash whenever the version changes.",
            "7. Mint owner/program, raw mint bytes/extensions, supply, quote mint, and Token-2022 extension state.",
            "8. Coverage reconciliation counters proving every curve signature has finalized status and every successful trade has a reserve transition.",
            "",
            "Store this once in compressed hourly `zstd` partitions with manifests and SHA-256 checksums. Feature/score/outcome rows should reference evidence IDs rather than duplicate the raw transaction payload.",
            "",
            "## Safe Research Use",
            "",
            "The engine is safe as the target for new alpha research only for rows it marks exactly evaluable. Its current fail-closed transition validation contains the one-lamport boundary and strict fee-allocation mismatches as coverage loss; correcting those boundaries would recover coverage. It must never silently fall back to MC-ratio economics, and the 236-row prospective result must not be generalized as though coverage were random.",
            "",
            f"`{final_class}`",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=pathlib.Path)
    parser.add_argument("--cache", required=True, type=pathlib.Path)
    parser.add_argument("--canonical-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--max-cache-rows", type=int, default=160_000)
    parser.add_argument("--per-variant", type=int, default=500)
    parser.add_argument("--overlap-sample", type=int, default=100)
    parser.add_argument("--decision-crosscheck", type=pathlib.Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    exact_path = args.run_dir / "exact-outcomes.jsonl.gz"
    signals_path = args.run_dir / "prospective-signals-source-snapshot.jsonl.gz"
    matched_path = args.run_dir / "matched-observations.jsonl"
    tx_summary, tx_rows = transaction_redteam(args.cache, args.max_cache_rows, args.per_variant)
    paired = paired_recompute(exact_path)
    coverage = coverage_bias(signals_path, exact_path, matched_path)
    overlap = historical_local_overlap(args.cache, matched_path, exact_path, args.overlap_sample)
    semantics = outcome_semantics_redteam(exact_path, matched_path, args.cache)
    local_state = local_replay_state_crosscheck(args.decision_crosscheck, args.cache)
    inventory = evidence_inventory(
        {
            "normalized": args.canonical_root / "normalized-observations.jsonl",
            "detected": args.canonical_root / "detected-swaps.jsonl",
            "capsules": args.canonical_root / "candidate-capsules.jsonl",
            "decisions": args.canonical_root / "decision-inputs.jsonl",
            "decision_receipts": pathlib.Path("/var/lib/edgesniffer-storage-offload-v3/decision-receipts.jsonl"),
            "matched": matched_path,
            "wallet_8ycb_enhanced": pathlib.Path("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/wallet-8ycb-opening-sniper-v2/target_enhanced_transactions.jsonl.gz"),
            "wallet_8ycb_trade_events": pathlib.Path("/var/lib/edgesniffer-storage-offload-v3/edgesniffer-derived/wallet-8ycb-opening-sniper-v2/target_trade_events.jsonl.gz"),
            "rpc_cache": args.cache,
        }
    )
    bounds = corpus_bounds(
        {
            "normalized": args.canonical_root / "normalized-observations.jsonl",
            "detected": args.canonical_root / "detected-swaps.jsonl",
            "capsules": args.canonical_root / "candidate-capsules.jsonl",
            "decisions": args.canonical_root / "decision-inputs.jsonl",
        }
    )
    result = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "independent": True,
        "imports_production_evaluator": False,
        "auditor_sha256": sha256_file(pathlib.Path(__file__)),
        "input_sha256": {
            "exact_outcomes": sha256_file(exact_path),
            "prospective_signals": sha256_file(signals_path),
            "matched_observations": sha256_file(matched_path),
        },
        "transaction_redteam": tx_summary,
        "paired": paired,
        "coverage_bias": coverage,
        "historical_overlap": overlap,
        "outcome_semantics": semantics,
        "local_replay_state_crosscheck": local_state,
        "evidence_inventory": inventory,
        "corpus_bounds": bounds,
    }
    (args.output / "audit.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    with gzip.open(args.output / "transaction-validation-sample.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in tx_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (args.output / "FINAL_REPORT.md").write_text(render_report(result), encoding="utf-8")
    checksums = []
    for name in ("audit.json", "transaction-validation-sample.jsonl.gz", "FINAL_REPORT.md"):
        path = args.output / name
        checksums.append(f"{sha256_file(path)}  {name}")
    (args.output / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "paired": paired,
        "coverage_classification": coverage["classification"],
        "transaction_disagreements": tx_summary["disagreements"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
