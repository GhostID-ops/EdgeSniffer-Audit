#!/usr/bin/env python3
"""Exact Pump bonding-curve economics and live Ranker replay.

This evaluator deliberately has no market-cap-ratio fallback. A result is
produced only from finalized transaction status, Pump TradeEvent reserve
states, decoded instruction constraints, and integer curve/fee arithmetic.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pathlib
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Iterator, Optional


EVALUATOR_NAME = "EXACT_CURVE_ECONOMICS_V1"
EVALUATOR_VERSION = "1.0.0"
LAMPORTS_PER_SOL = 1_000_000_000
FIRST_SELLABLE_SCENARIOS_MS = {"P50": 964, "P75": 1476, "P90": 1989}
PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SOL_QUOTE_MINTS = {
    "11111111111111111111111111111111",
    "So11111111111111111111111111111111111111112",
}
TRADE_EVENT_DISCRIMINATOR = bytes([189, 219, 127, 211, 78, 230, 97, 238])

BUY = bytes([102, 6, 61, 18, 1, 218, 235, 234])
BUY_EXACT_SOL_IN = bytes([56, 252, 116, 8, 158, 223, 205, 95])
BUY_V2 = bytes([184, 23, 238, 97, 103, 197, 211, 61])
BUY_EXACT_QUOTE_IN_V2 = bytes([194, 171, 28, 70, 104, 77, 91, 47])
SELL = bytes([51, 230, 133, 164, 1, 127, 131, 173])
SELL_V2 = bytes([93, 246, 130, 60, 231, 233, 64, 178])

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58_INDEX = {c: i for i, c in enumerate(BASE58_ALPHABET)}


class ExactCurveError(RuntimeError):
    """An input cannot support an exact executable replay."""


def utc_iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat()


def observation_interval_ms(source: "SourceObservation") -> tuple[int, int]:
    """Return the closed interval in which the observation could have arrived."""
    if source.observed_ms <= 0:
        raise ExactCurveError(
            f"missing observation timestamp for {source.signature} ({source.timing_source})"
        )
    if source.timing_source == "chain_block_time_only":
        return source.observed_ms, source.observed_ms + 999
    return source.observed_ms, source.observed_ms


def temporal_side(source: "SourceObservation", point_ms: int) -> str:
    start_ms, end_ms = observation_interval_ms(source)
    if end_ms <= point_ms:
        return "before"
    if start_ms > point_ms:
        return "after"
    return "ambiguous"


def ceil_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ExactCurveError("non-positive denominator")
    return (numerator + denominator - 1) // denominator


def fee_ceil(amount: int, bps: int) -> int:
    if amount < 0 or bps < 0:
        raise ExactCurveError("negative fee input")
    return ceil_div(amount * bps, 10_000) if amount and bps else 0


def base58_encode(raw: bytes) -> str:
    value = int.from_bytes(raw, "big")
    chars: list[str] = []
    while value:
        value, rem = divmod(value, 58)
        chars.append(BASE58_ALPHABET[rem])
    zeroes = len(raw) - len(raw.lstrip(b"\0"))
    return "1" * zeroes + ("".join(reversed(chars)) or ("" if zeroes else "1"))


def base58_decode(value: str) -> bytes:
    number = 0
    for char in value:
        if char not in BASE58_INDEX:
            raise ExactCurveError(f"invalid base58 character {char!r}")
        number = number * 58 + BASE58_INDEX[char]
    payload = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + payload


@dataclasses.dataclass(frozen=True)
class CurveState:
    virtual_sol: int
    virtual_token: int
    real_sol: int
    real_token: int

    def validate(self) -> None:
        if min(dataclasses.astuple(self)) < 0:
            raise ExactCurveError(f"negative reserve state: {self}")
        if self.virtual_sol == 0 or self.virtual_token == 0:
            raise ExactCurveError(f"zero virtual reserve: {self}")


@dataclasses.dataclass(frozen=True)
class Fees:
    protocol_bps: int
    creator_bps: int
    lp_bps: int = 0

    @property
    def total_bps(self) -> int:
        return self.protocol_bps + self.creator_bps + self.lp_bps


@dataclasses.dataclass(frozen=True)
class FeeTier:
    market_cap_lamports_threshold: int
    fees: Fees


@dataclasses.dataclass(frozen=True)
class FeeConfig:
    address: str
    flat_fees: Fees
    fee_tiers: tuple[FeeTier, ...]
    stable_fee_tiers: tuple[FeeTier, ...]

    def fees_for(self, state: CurveState, mint_supply: int, stable: bool = False) -> Fees:
        tiers = self.stable_fee_tiers if stable else self.fee_tiers
        if not tiers:
            return self.flat_fees
        market_cap = state.virtual_sol * mint_supply // state.virtual_token
        selected = tiers[0]
        for tier in reversed(tiers):
            if market_cap >= tier.market_cap_lamports_threshold:
                selected = tier
                break
        return selected.fees


@dataclasses.dataclass(frozen=True)
class BuyQuote:
    gross_quote_in: int
    curve_quote_in: int
    pricing_quote_in: int
    token_out: int
    protocol_fee: int
    creator_fee: int
    lp_fee: int
    post_state: CurveState


@dataclasses.dataclass(frozen=True)
class SellQuote:
    token_in: int
    gross_quote_out: int
    immediate_quote_out: int
    protocol_fee: int
    creator_fee: int
    lp_fee: int
    cashback_accrual: int
    buyback_fee_allocation: int
    post_state: CurveState


def _fees_on_curve_amount(curve_amount: int, fees: Fees) -> tuple[int, int, int]:
    return (
        fee_ceil(curve_amount, fees.protocol_bps),
        fee_ceil(curve_amount, fees.creator_bps),
        fee_ceil(curve_amount, fees.lp_bps),
    )


def quote_exact_quote_in_buy(state: CurveState, gross_quote_in: int, fees: Fees) -> BuyQuote:
    state.validate()
    if gross_quote_in <= 0:
        raise ExactCurveError("buy quote input must be positive")
    curve_in = gross_quote_in * 10_000 // (10_000 + fees.total_bps)
    if curve_in <= 1:
        raise ExactCurveError("buy quote input too small after fees")
    while curve_in > 1:
        protocol, creator, lp = _fees_on_curve_amount(curve_in, fees)
        overflow = curve_in + protocol + creator + lp - gross_quote_in
        if overflow <= 0:
            break
        curve_in -= overflow
    protocol, creator, lp = _fees_on_curve_amount(curve_in, fees)
    if curve_in + protocol + creator + lp > gross_quote_in:
        raise ExactCurveError("fee rounding cannot fit exact quote input")
    pricing_in = curve_in - 1
    token_out = pricing_in * state.virtual_token // (state.virtual_sol + pricing_in)
    if token_out <= 0 or token_out > state.real_token:
        raise ExactCurveError("buy output is zero or exceeds real token reserves")
    post = CurveState(
        virtual_sol=state.virtual_sol + curve_in,
        virtual_token=state.virtual_token - token_out,
        real_sol=state.real_sol + curve_in,
        real_token=state.real_token - token_out,
    )
    post.validate()
    return BuyQuote(gross_quote_in, curve_in, pricing_in, token_out, protocol, creator, lp, post)


def quote_exact_token_buy(
    state: CurveState,
    token_amount: int,
    max_quote_cost: int,
    fees: Fees,
) -> BuyQuote:
    state.validate()
    if token_amount <= 0 or token_amount >= state.virtual_token or token_amount > state.real_token:
        raise ExactCurveError("invalid exact-token buy amount")
    # Exact-token buy/buy_v2 uses the direct ceiling. The extra +1 belongs to
    # the reverse quote for the exact-quote-in instruction, whose forward path
    # deliberately prices with net_quote - 1.
    curve_in = ceil_div(token_amount * state.virtual_sol, state.virtual_token - token_amount)
    protocol, creator, lp = _fees_on_curve_amount(curve_in, fees)
    gross = curve_in + protocol + creator + lp
    if gross > max_quote_cost:
        raise ExactCurveError(f"buy max-cost constraint failed: required={gross} max={max_quote_cost}")
    post = CurveState(
        virtual_sol=state.virtual_sol + curve_in,
        virtual_token=state.virtual_token - token_amount,
        real_sol=state.real_sol + curve_in,
        real_token=state.real_token - token_amount,
    )
    post.validate()
    return BuyQuote(gross, curve_in, curve_in - 1, token_amount, protocol, creator, lp, post)


def quote_exact_token_sell(
    state: CurveState,
    token_amount: int,
    min_quote_output: int,
    fees: Fees,
    *,
    cashback_bps: int = 0,
    buyback_share_bps: int = 0,
) -> SellQuote:
    state.validate()
    if token_amount <= 0 or token_amount >= state.virtual_token:
        raise ExactCurveError("invalid sell token amount")
    gross = token_amount * state.virtual_sol // (state.virtual_token + token_amount)
    if gross <= 0 or gross > state.real_sol:
        raise ExactCurveError("sell output is zero or exceeds real SOL reserves")
    protocol, creator, lp = _fees_on_curve_amount(gross, fees)
    immediate = gross - protocol - creator - lp
    if immediate < min_quote_output:
        raise ExactCurveError(
            f"sell min-output constraint failed: output={immediate} min={min_quote_output}"
        )
    cashback = fee_ceil(gross, cashback_bps) if cashback_bps else 0
    buyback = protocol * buyback_share_bps // 10_000 if buyback_share_bps else 0
    post = CurveState(
        virtual_sol=state.virtual_sol - gross,
        virtual_token=state.virtual_token + token_amount,
        real_sol=state.real_sol - gross,
        real_token=state.real_token + token_amount,
    )
    post.validate()
    return SellQuote(
        token_amount,
        gross,
        immediate,
        protocol,
        creator,
        lp,
        cashback,
        buyback,
        post,
    )


class BorshReader:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def take(self, count: int) -> bytes:
        end = self.offset + count
        if end > len(self.payload):
            raise ExactCurveError("truncated Borsh payload")
        result = self.payload[self.offset:end]
        self.offset = end
        return result

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "little")

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "little")

    def u64(self) -> int:
        return int.from_bytes(self.take(8), "little")

    def u128(self) -> int:
        return int.from_bytes(self.take(16), "little")

    def i64(self) -> int:
        return int.from_bytes(self.take(8), "little", signed=True)

    def boolean(self) -> bool:
        value = self.u8()
        if value not in (0, 1):
            raise ExactCurveError(f"invalid Borsh bool {value}")
        return bool(value)

    def pubkey(self) -> str:
        return base58_encode(self.take(32))

    def string(self) -> str:
        size = self.u32()
        return self.take(size).decode("utf-8")


@dataclasses.dataclass(frozen=True)
class TradeEvent:
    mint: str
    sol_amount: int
    token_amount: int
    is_buy: bool
    user: str
    timestamp: int
    post_state: CurveState
    fee_recipient: str
    fee_basis_points: int
    fee: int
    creator: str
    creator_fee_basis_points: int
    creator_fee: int
    track_volume: bool
    ix_name: str
    mayhem_mode: bool
    cashback_fee_basis_points: int
    cashback: int
    buyback_fee_basis_points: int
    buyback_fee: int
    shareholders: tuple[tuple[str, int], ...]
    quote_mint: str
    quote_amount: int
    virtual_quote_reserves: int
    real_quote_reserves: int
    holder_rewards_basis_points: int = 0
    holder_rewards: int = 0

    @property
    def pre_state(self) -> CurveState:
        if self.is_buy:
            return CurveState(
                self.post_state.virtual_sol - self.sol_amount,
                self.post_state.virtual_token + self.token_amount,
                self.post_state.real_sol - self.sol_amount,
                self.post_state.real_token + self.token_amount,
            )
        return CurveState(
            self.post_state.virtual_sol + self.sol_amount,
            self.post_state.virtual_token - self.token_amount,
            self.post_state.real_sol + self.sol_amount,
            self.post_state.real_token - self.token_amount,
        )

    @property
    def immediate_quote_amount(self) -> int:
        if self.is_buy:
            return self.sol_amount + self.fee + self.creator_fee + self.cashback
        return self.sol_amount - self.fee - self.creator_fee - self.cashback


def decode_trade_event(payload: bytes) -> TradeEvent:
    reader = BorshReader(payload)
    if reader.take(8) != TRADE_EVENT_DISCRIMINATOR:
        raise ExactCurveError("not a Pump TradeEvent")
    mint = reader.pubkey()
    sol_amount = reader.u64()
    token_amount = reader.u64()
    is_buy = reader.boolean()
    user = reader.pubkey()
    timestamp = reader.i64()
    legacy_post_state = CurveState(reader.u64(), reader.u64(), reader.u64(), reader.u64())
    fee_recipient = reader.pubkey()
    fee_bps = reader.u64()
    fee = reader.u64()
    creator = reader.pubkey()
    creator_fee_bps = reader.u64()
    creator_fee = reader.u64()
    track_volume = reader.boolean()
    reader.u64()  # total_unclaimed_tokens
    reader.u64()  # total_claimed_tokens
    reader.u64()  # current_sol_volume
    reader.i64()  # last_update_timestamp
    ix_name = reader.string()
    mayhem_mode = reader.boolean()
    cashback_bps = reader.u64()
    cashback = reader.u64()
    buyback_bps = reader.u64()
    buyback_fee = reader.u64()
    shareholders = tuple((reader.pubkey(), reader.u16()) for _ in range(reader.u32()))
    quote_mint = reader.pubkey()
    quote_amount = reader.u64()
    virtual_quote = reader.u64()
    real_quote = reader.u64()
    holder_rewards_bps = 0
    holder_rewards = 0
    remaining = len(payload) - reader.offset
    if remaining == 16:
        holder_rewards_bps = reader.u64()
        holder_rewards = reader.u64()
    if reader.offset != len(payload):
        raise ExactCurveError(
            f"TradeEvent schema mismatch: decoded={reader.offset} payload={len(payload)}"
        )
    quote_post_state = CurveState(
        virtual_quote, legacy_post_state.virtual_token,
        real_quote, legacy_post_state.real_token,
    )
    if quote_mint in SOL_QUOTE_MINTS:
        if sol_amount not in (0, quote_amount):
            raise ExactCurveError(
                f"conflicting SOL/quote trade amounts: legacy={sol_amount} quote={quote_amount}"
            )
        if legacy_post_state.virtual_sol not in (0, virtual_quote):
            raise ExactCurveError("conflicting legacy/quote virtual reserves")
        if legacy_post_state.real_sol not in (0, real_quote):
            raise ExactCurveError("conflicting legacy/quote real reserves")
        sol_amount = quote_amount
        post_state = quote_post_state
    else:
        post_state = quote_post_state
    post_state.validate()
    return TradeEvent(
        mint,
        sol_amount,
        token_amount,
        is_buy,
        user,
        timestamp,
        post_state,
        fee_recipient,
        fee_bps,
        fee,
        creator,
        creator_fee_bps,
        creator_fee,
        track_volume,
        ix_name,
        mayhem_mode,
        cashback_bps,
        cashback,
        buyback_bps,
        buyback_fee,
        shareholders,
        quote_mint,
        quote_amount,
        virtual_quote,
        real_quote,
        holder_rewards_bps,
        holder_rewards,
    )


def decode_trade_events(log_messages: Iterable[str]) -> list[TradeEvent]:
    events: list[TradeEvent] = []
    for line in log_messages:
        marker = "Program data: "
        if marker not in line:
            continue
        encoded = line.split(marker, 1)[1].strip()
        try:
            payload = base64.b64decode(encoded, validate=True)
        except Exception:
            continue
        if payload.startswith(TRADE_EVENT_DISCRIMINATOR):
            events.append(decode_trade_event(payload))
    return events


@dataclasses.dataclass(frozen=True)
class PumpInstruction:
    kind: str
    arg0: int
    arg1: int
    exact_quote_in: bool

    @property
    def side(self) -> str:
        return "sell" if self.kind.startswith("sell") else "buy"


def decode_pump_instruction(payload: bytes) -> PumpInstruction:
    if len(payload) < 24:
        raise ExactCurveError("Pump trade instruction shorter than 24 bytes")
    discriminator = payload[:8]
    arg0 = int.from_bytes(payload[8:16], "little")
    arg1 = int.from_bytes(payload[16:24], "little")
    kinds = {
        BUY: ("buy", False),
        BUY_V2: ("buy_v2", False),
        BUY_EXACT_SOL_IN: ("buy_exact_sol_in", True),
        BUY_EXACT_QUOTE_IN_V2: ("buy_exact_quote_in_v2", True),
        SELL: ("sell", False),
        SELL_V2: ("sell_v2", False),
    }
    if discriminator not in kinds:
        raise ExactCurveError(f"unsupported Pump discriminator {discriminator.hex()}")
    kind, exact_quote_in = kinds[discriminator]
    return PumpInstruction(kind, arg0, arg1, exact_quote_in)


def apply_instruction(
    state: CurveState,
    instruction: PumpInstruction,
    fees: Fees,
    *,
    cashback_bps: int = 0,
    buyback_share_bps: int = 0,
) -> BuyQuote | SellQuote:
    if instruction.side == "sell":
        return quote_exact_token_sell(
            state,
            instruction.arg0,
            instruction.arg1,
            fees,
            cashback_bps=cashback_bps,
            buyback_share_bps=buyback_share_bps,
        )
    if instruction.exact_quote_in:
        quote = quote_exact_quote_in_buy(state, instruction.arg0, fees)
        if quote.token_out < instruction.arg1:
            raise ExactCurveError(
                f"buy min-token constraint failed: output={quote.token_out} min={instruction.arg1}"
            )
        return quote
    return quote_exact_token_buy(state, instruction.arg0, instruction.arg1, fees)


def decode_fee_config(address: str, payload: bytes) -> FeeConfig:
    reader = BorshReader(payload)
    expected = bytes([143, 52, 146, 187, 219, 123, 76, 155])
    if reader.take(8) != expected:
        raise ExactCurveError("FeeConfig discriminator mismatch")
    reader.u8()
    reader.pubkey()

    def read_fees() -> Fees:
        return Fees(lp_bps=reader.u64(), protocol_bps=reader.u64(), creator_bps=reader.u64())

    flat = read_fees()

    def read_tiers() -> tuple[FeeTier, ...]:
        count = reader.u32()
        if count > 128:
            raise ExactCurveError(f"implausible FeeConfig tier count {count}")
        return tuple(FeeTier(reader.u128(), read_fees()) for _ in range(count))

    tiers = read_tiers()
    stable = read_tiers()
    trailing = payload[reader.offset :]
    if any(trailing):
        raise ExactCurveError(
            f"FeeConfig non-zero schema tail: decoded={reader.offset} payload={len(payload)}"
        )
    return FeeConfig(address, flat, tiers, stable)


class RpcClient:
    def __init__(self, url: str, timeout: float = 30.0, retries: int = 4):
        if not url:
            raise ExactCurveError("RPC URL is empty")
        self.url = url
        self.timeout = timeout
        self.retries = retries
        self._next_id = 1

    def _post(self, payload: Any) -> Any:
        body = json.dumps(payload, separators=(",", ":")).encode()
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        last_error: Optional[Exception] = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                time.sleep(0.4 * (2**attempt))
        raise ExactCurveError(f"RPC request failed after retries: {last_error}")

    def call(self, method: str, params: list[Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        result = self._post({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        if result.get("error") is not None:
            raise ExactCurveError(f"RPC {method} error: {result['error']}")
        return result.get("result")

    def batch(self, calls: list[tuple[str, list[Any]]], size: int = 40) -> list[Any]:
        # The production RPC intentionally rejects JSON-RPC batch envelopes.
        # Bounded concurrent single calls preserve exact per-signature status
        # without requiring a second provider or changing live infrastructure.
        request_ids = list(range(self._next_id, self._next_id + len(calls)))
        self._next_id += len(calls)

        def invoke(item: tuple[int, tuple[str, list[Any]]]) -> Any:
            request_id, (method, params) = item
            try:
                response = self._post(
                    {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
                )
            except ExactCurveError:
                return None
            if response.get("error") is not None:
                return None
            return response.get("result")

        with ThreadPoolExecutor(max_workers=min(12, max(1, len(calls)))) as executor:
            return list(executor.map(invoke, zip(request_ids, calls)))


def rpc_url_from_env_file(path: str, key: str) -> str:
    for raw in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    raise ExactCurveError(f"{key} not found in {path}")


def parse_json_line_tolerant(line: str) -> Optional[dict[str, Any]]:
    stripped = line.strip().rstrip(".")
    if not stripped:
        return None
    candidates = [stripped]
    if not stripped.startswith("{") and stripped.startswith('"'):
        candidates.append("{" + stripped)
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            start = candidate.find("{")
            if start >= 0:
                try:
                    value, _ = decoder.raw_decode(candidate[start:])
                    return value if isinstance(value, dict) else None
                except json.JSONDecodeError:
                    pass
    return None


def read_jsonl(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            value = parse_json_line_tolerant(line)
            if value is not None:
                yield value


@dataclasses.dataclass
class LiveTrade:
    session: str
    mint: str
    buy_signature: str
    buy_result_ms: int
    selected_ms: int
    sol_in: int
    bonding_curve: Optional[str] = None
    token_program: Optional[str] = None
    expected_tokens: Optional[int] = None
    actual_tokens: Optional[int] = None
    first_sellable_ms: Optional[int] = None
    buy_finality: Optional[str] = None
    sell_signature: Optional[str] = None
    sell_finality: Optional[str] = None
    exit_reason: Optional[str] = None
    exit_decision_ms: Optional[int] = None
    live_expected_roi_pct: Optional[float] = None
    curve_slot_observed_ms: dict[int, int] = dataclasses.field(default_factory=dict)
    history_boundary_complete: bool = False
    history_transaction_count: int = 0


def discover_live_trades(audit_dirs: list[pathlib.Path]) -> tuple[list[LiveTrade], list[dict[str, Any]]]:
    trades: list[LiveTrade] = []
    exclusions: list[dict[str, Any]] = []
    for directory in audit_dirs:
        audit_path = directory / "rust-selected-candidate-audit.jsonl"
        if not audit_path.exists():
            exclusions.append({"session": directory.name, "reason": "audit_file_missing"})
            continue
        rows = list(read_jsonl(audit_path))
        rows.sort(key=lambda row: int(row.get("atMs") or 0))
        session_trades: list[LiveTrade] = []
        context_by_mint: dict[str, dict[str, Any]] = defaultdict(dict)
        slot_times_by_curve: dict[str, dict[int, int]] = defaultdict(dict)
        for row in rows:
            curve = row.get("bondingCurve")
            slot = row.get("slot") or row.get("lastSlot")
            observed_ms = row.get("observedAtMs") or row.get("atMs")
            if curve and slot and observed_ms and row.get("kind") in {
                "rustSelectedCandidateCurveCacheExitBridge",
                "rustSelectedCandidateYellowstoneTxReplayCurveUpdate",
                "rustSelectedCandidateReserveObservationExitBridge",
            }:
                slot = int(slot)
                observed_ms = int(observed_ms)
                previous = slot_times_by_curve[curve].get(slot)
                if previous is None or observed_ms < previous:
                    slot_times_by_curve[curve][slot] = observed_ms
        buy_by_signature: dict[str, LiveTrade] = {}
        for row in rows:
            mint = row.get("mint")
            if mint:
                context = context_by_mint[mint]
                for key in ("bondingCurve", "tokenProgramId"):
                    if row.get(key):
                        context[key] = row[key]
            kind = row.get("kind")
            if kind == "rustSelectedCandidateBuyResult" and row.get("ok") is True and row.get("signature"):
                trade = LiveTrade(
                    session=directory.name,
                    mint=str(mint),
                    buy_signature=str(row["signature"]),
                    buy_result_ms=int(row.get("buyResultAtMs") or row.get("atMs")),
                    selected_ms=int(row.get("candidateSelectedAtMs") or row.get("atMs")),
                    sol_in=int(row.get("solInLamports") or row.get("submittedBuyMaxSolCostLamports") or 0),
                    bonding_curve=row.get("bondingCurve") or context_by_mint[mint].get("bondingCurve"),
                    token_program=(row.get("buyInstructionAudit") or {}).get("tokenProgramId")
                    or context_by_mint[mint].get("tokenProgramId"),
                    expected_tokens=row.get("finalEconomicGuardExpectedTokensRaw"),
                    actual_tokens=row.get("tokensOutRaw"),
                )
                trades.append(trade)
                session_trades.append(trade)
                buy_by_signature[trade.buy_signature] = trade
                continue
            if kind == "rustSelectedCandidateBuyFinality" and row.get("signature") in buy_by_signature:
                buy_by_signature[row["signature"]].buy_finality = row.get("status")
                continue
            if kind in {
                "rustSelectedCandidateStreamingExactBalanceActivation",
                "rustSelectedCandidateEarlyBalanceUpdate",
                "rustSelectedCandidateBalanceCacheSubmittedBuyExactProofUpdate",
            } and mint:
                candidates = [t for t in session_trades if t.mint == mint and t.buy_result_ms <= int(row.get("atMs") or 0)]
                if candidates:
                    trade = candidates[-1]
                    at_ms = int(row.get("observedAtMs") or row.get("atMs") or 0)
                    if at_ms and (trade.first_sellable_ms is None or at_ms < trade.first_sellable_ms):
                        trade.first_sellable_ms = at_ms
                    if row.get("amountRaw") is not None and row.get("exact") is not False:
                        trade.actual_tokens = int(row["amountRaw"])
                continue
            if kind == "rustExitDecision" and mint:
                candidates = [t for t in session_trades if t.mint == mint and t.sell_signature is None]
                if candidates:
                    trade = candidates[-1]
                    trade.exit_reason = row.get("reason") or row.get("exitReason")
                    trade.exit_decision_ms = int(row.get("atMs") or 0)
                    for key in ("netPnlPct", "feeAdjustedPnlPct", "pnlPct"):
                        if row.get(key) is not None:
                            trade.live_expected_roi_pct = float(row[key])
                            break
                continue
            if kind == "rustExitSellFinality" and mint and row.get("signature"):
                candidates = [t for t in session_trades if t.mint == mint and t.sell_signature is None]
                if candidates:
                    trade = candidates[-1]
                    trade.sell_signature = str(row["signature"])
                    trade.sell_finality = row.get("status")
        for trade in session_trades:
            context = context_by_mint[trade.mint]
            trade.bonding_curve = trade.bonding_curve or context.get("bondingCurve")
            trade.token_program = trade.token_program or context.get("tokenProgramId")
            if trade.bonding_curve:
                trade.curve_slot_observed_ms = dict(slot_times_by_curve[trade.bonding_curve])
    unique: dict[str, LiveTrade] = {}
    for trade in trades:
        unique.setdefault(trade.buy_signature, trade)
    return sorted(unique.values(), key=lambda item: item.selected_ms), exclusions


@dataclasses.dataclass(frozen=True)
class SourceObservation:
    mint: str
    signature: str
    side: str
    slot: int
    transaction_index: int
    instruction_index: int
    observed_ms: int
    instruction: PumpInstruction
    source_accounts: tuple[str, ...]
    timing_source: str = "canonical_local_receive"


def scan_observations(
    corpus_path: pathlib.Path,
    mints: set[str],
    output_dir: pathlib.Path,
) -> tuple[dict[str, list[SourceObservation]], dict[str, Any]]:
    cache_key = hashlib.sha256(
        (str(corpus_path.resolve()) + "\n" + "\n".join(sorted(mints))).encode()
    ).hexdigest()
    cache_path = output_dir / "matched-observations.jsonl"
    cache_meta_path = output_dir / "matched-observations.meta.json"
    if cache_path.exists() and cache_meta_path.exists():
        try:
            cache_meta = json.loads(cache_meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache_meta = {}
        if cache_meta.get("cache_key") == cache_key:
            rows_by_mint: dict[str, list[SourceObservation]] = defaultdict(list)
            for row in read_jsonl(cache_path):
                rows_by_mint[row["mint"]].append(
                    SourceObservation(
                        mint=row["mint"],
                        signature=row["signature"],
                        side=row["side"],
                        slot=int(row["slot"]),
                        transaction_index=int(row["transaction_index"]),
                        instruction_index=int(row["instruction_index"]),
                        observed_ms=int(row["observed_ms"]),
                        instruction=PumpInstruction(**row["instruction"]),
                        source_accounts=tuple(row.get("source_accounts") or ()),
                        timing_source=row.get("timing_source", "canonical_local_receive"),
                    )
                )
            return rows_by_mint, {**cache_meta["scan_stats"], "cache_reused": True}
    patterns = output_dir / "target-mints.txt"
    patterns.write_text("".join(f"{mint}\n" for mint in sorted(mints)), encoding="ascii")
    process = subprocess.Popen(
        ["grep", "-F", "-f", str(patterns), str(corpus_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "LC_ALL": "C"},
    )
    assert process.stdout is not None
    rows_by_mint: dict[str, list[SourceObservation]] = defaultdict(list)
    malformed = 0
    unsupported = 0
    matched = 0
    for line in process.stdout:
        row = parse_json_line_tolerant(line)
        if row is None:
            malformed += 1
            continue
        mint = row.get("mint")
        if mint not in mints or row.get("side") not in {"buy", "sell"}:
            continue
        encoded = row.get("source_ix_data_base64")
        signature = row.get("transaction_signature")
        if not encoded or not signature:
            unsupported += 1
            continue
        try:
            instruction = decode_pump_instruction(base64.b64decode(encoded))
        except Exception:
            unsupported += 1
            continue
        extra = row.get("extra") or {}
        raw = row.get("raw_record") or {}
        rows_by_mint[mint].append(
            SourceObservation(
                mint=mint,
                signature=str(signature),
                side=str(row["side"]),
                slot=int(row.get("source_slot") or raw.get("slot") or 0),
                transaction_index=int(
                    row.get("source_transaction_index")
                    if row.get("source_transaction_index") is not None
                    else raw.get("transactionIndex", -1)
                ),
                instruction_index=int(raw.get("instructionIndex", -1)),
                observed_ms=int(row.get("local_receive_ts_ms") or 0),
                instruction=instruction,
                source_accounts=tuple(extra.get("sourceIxAccounts") or ()),
                timing_source="canonical_local_receive",
            )
        )
        matched += 1
    stderr = process.stderr.read() if process.stderr else ""
    return_code = process.wait()
    if return_code not in (0, 1):
        raise ExactCurveError(f"corpus grep failed ({return_code}): {stderr[-1000:]}")
    for rows in rows_by_mint.values():
        dedup: dict[tuple[str, int], SourceObservation] = {}
        for row in rows:
            dedup[(row.signature, row.instruction_index)] = row
        rows[:] = sorted(
            dedup.values(),
            key=lambda item: (item.slot, item.transaction_index, item.instruction_index),
        )
    scan_stats = {
        "matched_trade_observations": matched,
        "malformed_matching_rows": malformed,
        "unsupported_matching_rows": unsupported,
        "grep_return_code": return_code,
        "cache_reused": False,
    }
    with cache_path.open("w", encoding="utf-8") as handle:
        for mint in sorted(rows_by_mint):
            for row in rows_by_mint[mint]:
                handle.write(
                    json.dumps(
                        {
                            "mint": row.mint,
                            "signature": row.signature,
                            "side": row.side,
                            "slot": row.slot,
                            "transaction_index": row.transaction_index,
                            "instruction_index": row.instruction_index,
                            "observed_ms": row.observed_ms,
                            "instruction": dataclasses.asdict(row.instruction),
                            "source_accounts": row.source_accounts,
                            "timing_source": row.timing_source,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
    cache_meta_path.write_text(
        json.dumps({"cache_key": cache_key, "scan_stats": scan_stats}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return rows_by_mint, scan_stats


def transaction_event_records(
    observations: list[SourceObservation],
    transaction_results: dict[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[SourceObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.signature].append(observation)
    records: list[dict[str, Any]] = []
    for signature, source_rows in grouped.items():
        tx = transaction_results.get(signature)
        if tx is None:
            for source in source_rows:
                records.append({"source": source, "rpc_missing": True, "success": None, "event": None})
            continue
        meta = tx.get("meta") or {}
        success = meta.get("err") is None
        events = decode_trade_events(meta.get("logMessages") or []) if success else []
        unused = list(events)
        for source in sorted(source_rows, key=lambda item: item.instruction_index):
            match_index = next(
                (
                    index
                    for index, event in enumerate(unused)
                    if event.mint == source.mint
                    and ((source.side == "buy") == event.is_buy)
                ),
                None,
            )
            event = unused.pop(match_index) if match_index is not None else None
            records.append(
                {
                    "source": source,
                    "rpc_missing": False,
                    "success": success,
                    "rpc_error": meta.get("err"),
                    "event": event,
                    "block_time_ms": int(tx.get("blockTime") or 0) * 1000,
                    "network_fee": int(meta.get("fee") or 0),
                }
            )
    return sorted(
        records,
        key=lambda item: (
            item["source"].slot,
            item["source"].transaction_index,
            item["source"].instruction_index,
        ),
    )


def order_successful_rows_by_reserve_continuity(
    rows: list[dict[str, Any]],
    *,
    anchor_signature: str,
    terminal_signature: str,
) -> list[dict[str, Any]]:
    """Order successful events by their authoritative reserve transitions.

    Jito's source transaction index is local to a shred/batch and is not a
    reliable block-global ordering key. TradeEvent pre/post states form the
    authoritative chain and make ordering independent of that index.
    """
    if not rows:
        return []
    anchors = [row for row in rows if row["source"].signature == anchor_signature]
    if len(anchors) != 1:
        raise ExactCurveError(f"reserve chain has {len(anchors)} matching live-buy anchors")
    anchor = anchors[0]
    remaining = list(rows)
    remaining.remove(anchor)

    predecessors: list[dict[str, Any]] = []
    current = anchor["event"].pre_state
    while True:
        matches = [row for row in remaining if row["event"].post_state == current]
        if not matches:
            break
        if len(matches) != 1:
            details = ",".join(
                f"{row['source'].signature}@{row['source'].slot}" for row in matches[:8]
            )
            raise ExactCurveError(
                f"ambiguous reserve predecessors before live buy: matches={len(matches)} "
                f"sample={details}"
            )
        selected = matches[0]
        predecessors.append(selected)
        remaining.remove(selected)
        current = selected["event"].pre_state

    ordered = list(reversed(predecessors)) + [anchor]
    current = anchor["event"].post_state
    while ordered[-1]["source"].signature != terminal_signature:
        matches = [row for row in remaining if row["event"].pre_state == current]
        if len(matches) != 1:
            details = ",".join(
                f"{row['source'].signature}@{row['source'].slot}" for row in remaining[:8]
            )
            raise ExactCurveError(
                f"reserve chain break after {ordered[-1]['source'].signature}: "
                f"matches={len(matches)} remaining={len(remaining)} sample={details}"
            )
        selected = matches[0]
        ordered.append(selected)
        remaining.remove(selected)
        current = selected["event"].post_state
    # Disconnected rows before the live buy are irrelevant. Reaching the
    # finalized sell proves that the complete causal post-entry chain exists.
    return ordered


def fetch_transactions(rpc: RpcClient, signatures: list[str]) -> dict[str, Any]:
    calls = [
        (
            "getTransaction",
            [
                signature,
                {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 0},
            ],
        )
        for signature in signatures
    ]
    results = rpc.batch(calls)
    return dict(zip(signatures, results))


def fetch_curve_history_between(
    rpc: RpcClient,
    curve: str,
    buy_signature: str,
    sell_signature: str,
) -> tuple[list[dict[str, Any]], bool]:
    history: list[dict[str, Any]] = []
    before = sell_signature
    reached_boundary = False
    for _ in range(20):
        options: dict[str, Any] = {
            "before": before,
            "until": buy_signature,
            "limit": 1000,
            "commitment": "finalized",
        }
        page = rpc.call("getSignaturesForAddress", [curve, options]) or []
        history.extend(page)
        if len(page) < 1000:
            reached_boundary = True
            break
        before = page[-1]["signature"]
    return history, reached_boundary


def resolved_account_keys(tx: dict[str, Any]) -> list[str]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    keys: list[str] = []
    for value in message.get("accountKeys") or []:
        keys.append(value if isinstance(value, str) else value.get("pubkey"))
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    keys.extend(loaded.get("writable") or [])
    keys.extend(loaded.get("readonly") or [])
    return keys


def extract_pump_source_observations(
    tx: dict[str, Any],
    signature: str,
    mint: str,
    timing_ms: int,
    timing_source: str,
) -> list[SourceObservation]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    keys = resolved_account_keys(tx)
    slot = int(tx.get("slot") or 0)
    compiled: list[tuple[int, dict[str, Any]]] = []
    for index, instruction in enumerate(message.get("instructions") or []):
        compiled.append((index, instruction))
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        parent = int(group.get("index") or 0)
        for offset, instruction in enumerate(group.get("instructions") or []):
            compiled.append((parent * 1000 + offset + 1, instruction))
    observations: list[SourceObservation] = []
    for instruction_index, instruction in compiled:
        program_index = instruction.get("programIdIndex")
        if program_index is None or int(program_index) >= len(keys):
            continue
        if keys[int(program_index)] != PUMP_PROGRAM_ID:
            continue
        encoded = instruction.get("data")
        if not encoded:
            continue
        try:
            decoded = decode_pump_instruction(base58_decode(encoded))
        except ExactCurveError:
            continue
        accounts = tuple(
            keys[int(index)]
            for index in instruction.get("accounts") or []
            if int(index) < len(keys)
        )
        instruction_mint = None
        if decoded.kind.endswith("_v2") and len(accounts) >= 2:
            instruction_mint = accounts[1]
        elif len(accounts) >= 3:
            instruction_mint = accounts[2]
        if instruction_mint != mint:
            continue
        observations.append(
            SourceObservation(
                mint=mint,
                signature=signature,
                side=decoded.side,
                slot=slot,
                transaction_index=-1,
                instruction_index=instruction_index,
                observed_ms=timing_ms,
                instruction=decoded,
                source_accounts=accounts,
                timing_source=timing_source,
            )
        )
    return observations


def augment_with_curve_history(
    trades: list[LiveTrade],
    observations: dict[str, list[SourceObservation]],
    histories: dict[str, list[dict[str, Any]]],
    tx_results: dict[str, Any],
) -> dict[str, Any]:
    added = 0
    block_time_only = 0
    for trade in trades:
        existing = {
            (row.signature, row.instruction.kind, row.instruction.arg0, row.instruction.arg1)
            for row in observations.get(trade.mint, [])
        }
        slot_hints = dict(trade.curve_slot_observed_ms)
        for row in observations.get(trade.mint, []):
            if row.observed_ms:
                slot_hints.setdefault(row.slot, row.observed_ms)
        signatures = [trade.buy_signature, trade.sell_signature] + [
            item["signature"] for item in histories.get(trade.buy_signature, [])
        ]
        for signature in dict.fromkeys(value for value in signatures if value):
            tx = tx_results.get(signature)
            if tx is None:
                continue
            slot = int(tx.get("slot") or 0)
            if slot in slot_hints:
                timing_ms = slot_hints[slot]
                timing_source = "yellowstone_curve_slot"
            else:
                timing_ms = int(tx.get("blockTime") or 0) * 1000
                timing_source = "chain_block_time_only"
            extracted = extract_pump_source_observations(
                tx, signature, trade.mint, timing_ms, timing_source
            )
            for source in extracted:
                key = (
                    source.signature,
                    source.instruction.kind,
                    source.instruction.arg0,
                    source.instruction.arg1,
                )
                if key in existing:
                    continue
                observations[trade.mint].append(source)
                existing.add(key)
                added += 1
                if timing_source == "chain_block_time_only":
                    block_time_only += 1
        observations[trade.mint].sort(
            key=lambda item: (item.slot, item.observed_ms, item.instruction_index)
        )
    return {
        "history_observations_added": added,
        "history_observations_chain_block_time_only": block_time_only,
    }


def fetch_mint_supply(rpc: RpcClient, mint: str) -> int:
    result = rpc.call("getTokenSupply", [mint, {"commitment": "finalized"}])
    return int(result["value"]["amount"])


def fetch_fee_config(rpc: RpcClient, address: str) -> FeeConfig:
    result = rpc.call("getAccountInfo", [address, {"encoding": "base64", "commitment": "finalized"}])
    value = result.get("value") if result else None
    if not value:
        raise ExactCurveError(f"FeeConfig account unavailable: {address}")
    return decode_fee_config(address, base64.b64decode(value["data"][0]))


def effective_event_fees(config_fees: Fees, event: TradeEvent) -> Fees:
    if config_fees.protocol_bps != event.fee_basis_points or config_fees.lp_bps != 0:
        raise ExactCurveError(
            f"protocol fee tier mismatch: config={config_fees.protocol_bps} "
            f"event={event.fee_basis_points}"
        )
    effective_creator_bps = (
        event.creator_fee_basis_points + event.cashback_fee_basis_points
    )
    if effective_creator_bps not in (0, config_fees.creator_bps):
        raise ExactCurveError(
            f"unsupported effective creator fee: config={config_fees.creator_bps} "
            f"event_creator={event.creator_fee_basis_points} "
            f"event_cashback={event.cashback_fee_basis_points}"
        )
    fees = Fees(
        protocol_bps=config_fees.protocol_bps,
        creator_bps=effective_creator_bps,
        lp_bps=config_fees.lp_bps,
    )
    expected_protocol = fee_ceil(event.sol_amount, fees.protocol_bps)
    expected_creator = fee_ceil(event.sol_amount, fees.creator_bps)
    if event.fee != expected_protocol or event.creator_fee + event.cashback != expected_creator:
        raise ExactCurveError(
            f"event fee amount mismatch: protocol={event.fee}/{expected_protocol} "
            f"creator+cashback={event.creator_fee + event.cashback}/{expected_creator}"
        )
    if event.buyback_fee > event.fee:
        raise ExactCurveError("buyback allocation exceeds protocol fee")
    if event.buyback_fee_basis_points:
        expected_buyback = event.fee * event.buyback_fee_basis_points // 10_000
        if event.buyback_fee != expected_buyback:
            raise ExactCurveError(
                f"buyback allocation mismatch: {event.buyback_fee}/{expected_buyback}"
            )
    return fees


def validate_actual_trade_transition(
    source: SourceObservation,
    event: TradeEvent,
    fees: Fees,
) -> BuyQuote | SellQuote:
    if event.quote_mint not in SOL_QUOTE_MINTS:
        raise ExactCurveError(
            f"unsupported non-SOL Pump quote mint {event.quote_mint}: {source.signature}"
        )
    applied = apply_instruction(
        event.pre_state,
        source.instruction,
        fees,
        cashback_bps=event.cashback_fee_basis_points,
        buyback_share_bps=event.buyback_fee_basis_points,
    )
    if applied.post_state != event.post_state:
        raise ExactCurveError(
            f"decoded instruction does not reproduce TradeEvent reserves: {source.signature}"
        )
    if isinstance(applied, BuyQuote):
        if applied.token_out != event.token_amount or applied.curve_quote_in != event.sol_amount:
            raise ExactCurveError(
                f"decoded buy does not reproduce TradeEvent amounts: {source.signature}"
            )
    elif applied.token_in != event.token_amount or applied.gross_quote_out != event.sol_amount:
        raise ExactCurveError(
            f"decoded sell does not reproduce TradeEvent amounts: {source.signature}"
        )
    return applied


def state_to_dict(state: CurveState) -> dict[str, int]:
    return dataclasses.asdict(state)


def quote_to_dict(quote: BuyQuote | SellQuote) -> dict[str, Any]:
    value = dataclasses.asdict(quote)
    value["post_state"] = state_to_dict(quote.post_state)
    return value


def event_to_dict(event: TradeEvent) -> dict[str, Any]:
    value = dataclasses.asdict(event)
    value["post_state"] = state_to_dict(event.post_state)
    value["pre_state"] = state_to_dict(event.pre_state)
    return value


def infer_fee_config_address(
    observations: list[SourceObservation], preferred_signature: Optional[str] = None
) -> Optional[str]:
    # These positions are frozen by the official Pump IDL. Resolve the live
    # buy first so a later transaction with a different layout cannot supply
    # the wrong account.
    fee_config_index = {
        "buy": 14,
        "buy_exact_sol_in": 14,
        "buy_v2": 22,
        "buy_exact_quote_in_v2": 22,
        "sell": 12,
        "sell_v2": 21,
    }
    preferred = [row for row in observations if row.signature == preferred_signature]
    ordered = preferred + [row for row in observations if row.signature != preferred_signature]
    for observation in ordered:
        index = fee_config_index.get(observation.instruction.kind)
        if index is not None and len(observation.source_accounts) > index:
            return observation.source_accounts[index]
    return None


def find_actual_event(
    signature: str,
    mint: str,
    transaction_results: dict[str, Any],
    is_buy: bool,
) -> tuple[TradeEvent, dict[str, Any]]:
    tx = transaction_results.get(signature)
    if tx is None:
        raise ExactCurveError(f"RPC transaction unavailable: {signature}")
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        raise ExactCurveError(f"transaction failed on-chain: {signature}: {meta['err']}")
    matches = [
        event
        for event in decode_trade_events(meta.get("logMessages") or [])
        if event.mint == mint and event.is_buy == is_buy
    ]
    if len(matches) != 1:
        raise ExactCurveError(
            f"expected exactly one matching TradeEvent for {signature}, found {len(matches)}"
        )
    return matches[0], tx


def load_canonical_outcomes(path: pathlib.Path, mints: set[str]) -> dict[str, dict[str, Any]]:
    outcomes: dict[str, dict[str, Any]] = {}
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not any(mint in line for mint in mints):
                    continue
                row = parse_json_line_tolerant(line)
                if row and row.get("mint") in mints:
                    outcomes[row["mint"]] = row
    except (EOFError, OSError):
        pass
    return outcomes


def summarize_returns(values: list[float]) -> dict[str, Any]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "n": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": (100 * len(wins) / len(values)) if values else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss else (math.inf if gross_win else None),
        "expectancy_pct": statistics.fmean(values) if values else None,
        "pnl_sol_at_1_sol_linear": sum(values) / 100,
        "median_return_pct": statistics.median(values) if values else None,
    }


def summarize_deltas(values: list[float]) -> dict[str, Any]:
    absolute = [abs(value) for value in values]
    return {
        "n": len(values),
        "mean_signed_pct_points": statistics.fmean(values) if values else None,
        "median_signed_pct_points": statistics.median(values) if values else None,
        "mean_absolute_pct_points": statistics.fmean(absolute) if absolute else None,
        "median_absolute_pct_points": statistics.median(absolute) if absolute else None,
        "worst_absolute_pct_points": max(absolute) if absolute else None,
    }


def evaluate_trade(
    trade: LiveTrade,
    observations: list[SourceObservation],
    transaction_results: dict[str, Any],
    fee_config: FeeConfig,
    mint_supply: int,
    canonical: Optional[dict[str, Any]],
    wallet: str,
    first_sellable_label: str,
    first_sellable_delay_ms: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if trade.buy_finality != "landed_ok":
        raise ExactCurveError(f"buy finality is not landed_ok: {trade.buy_finality}")
    if trade.sell_signature is None or trade.sell_finality != "landed_ok":
        raise ExactCurveError("completed finalized sell is unavailable")
    if trade.sol_in <= 0:
        raise ExactCurveError("live buy spend is unavailable")
    if trade.first_sellable_ms is None:
        raise ExactCurveError("first usable exact inventory proof is unavailable")
    if not trade.history_boundary_complete:
        raise ExactCurveError("bonding-curve transaction history did not reach the buy boundary")

    buy_event, buy_tx = find_actual_event(
        trade.buy_signature, trade.mint, transaction_results, True
    )
    sell_event, sell_tx = find_actual_event(
        trade.sell_signature, trade.mint, transaction_results, False
    )
    if buy_event.user != wallet or sell_event.user != wallet:
        raise ExactCurveError("live buy/sell TradeEvent user does not match wallet")
    if buy_event.token_amount != trade.actual_tokens:
        raise ExactCurveError(
            f"inventory mismatch: event={buy_event.token_amount} audit={trade.actual_tokens}"
        )

    intended_entry_ms = trade.selected_ms + 25
    deadline_ms = intended_entry_ms + 120_000
    modeled_first_sellable_ms = intended_entry_ms + first_sellable_delay_ms
    rows = transaction_event_records(observations, transaction_results)
    missing_status = [
        row
        for row in rows
        if row["success"] is None
        and temporal_side(row["source"], deadline_ms) != "after"
    ]
    if missing_status:
        raise ExactCurveError(
            "missing finalized transaction status in replay window: "
            + ",".join(row["source"].signature for row in missing_status[:5])
        )
    buy_source = next(
        (row["source"] for row in rows if row["source"].signature == trade.buy_signature),
        None,
    )
    if buy_source is None:
        raise ExactCurveError("live buy missing from source observation tape")
    successful_rows_unordered = [
        row for row in rows if row["success"] is True and row["event"] is not None
    ]
    if not successful_rows_unordered:
        raise ExactCurveError("no successful event tape")
    successful_rows = order_successful_rows_by_reserve_continuity(
        successful_rows_unordered,
        anchor_signature=trade.buy_signature,
        terminal_signature=trade.sell_signature,
    )

    # Validate the actual event chain before using it as causal state evidence.
    chain_errors: list[str] = []
    previous: Optional[CurveState] = None
    for row in successful_rows:
        event: TradeEvent = row["event"]
        calculated_fees = fee_config.fees_for(event.pre_state, mint_supply)
        try:
            event_fees = effective_event_fees(calculated_fees, event)
            validate_actual_trade_transition(row["source"], event, event_fees)
        except ExactCurveError as exc:
            chain_errors.append(
                f"fee_validation:{row['source'].signature}:{exc}"
            )
        if previous is not None and event.pre_state != previous:
            chain_errors.append(
                f"reserve_continuity_mismatch:{row['source'].signature}:"
                f"slot={row['source'].slot}:tx={row['source'].transaction_index}:"
                f"previous={dataclasses.astuple(previous)}:"
                f"next_pre={dataclasses.astuple(event.pre_state)}"
            )
        previous = event.post_state
    if chain_errors:
        raise ExactCurveError(";".join(chain_errors[:10]))

    pre_entry_rows: list[dict[str, Any]] = []
    future: list[dict[str, Any]] = []
    crossed_entry = False
    for row in successful_rows:
        side = temporal_side(row["source"], intended_entry_ms)
        if side == "ambiguous":
            raise ExactCurveError(
                "entry state timing is ambiguous within a block-time-only interval: "
                f"{row['source'].signature}"
            )
        if side == "before":
            if crossed_entry:
                raise ExactCurveError(
                    f"non-monotonic local observation order before entry: {row['source'].signature}"
                )
            pre_entry_rows.append(row)
        else:
            crossed_entry = True
            future.append(row)
    if not pre_entry_rows:
        raise ExactCurveError("no authoritative causal reserve observation at intended entry")
    if any(
        row["source"].signature in {trade.buy_signature, trade.sell_signature}
        for row in pre_entry_rows
    ):
        raise ExactCurveError("own live transaction preceded the intended counterfactual entry")

    entry_state_source = pre_entry_rows[-1]
    entry_state = entry_state_source["event"].post_state
    effective_creator_bps = (
        buy_event.creator_fee_basis_points + buy_event.cashback_fee_basis_points
    )
    effective_cashback_bps = buy_event.cashback_fee_basis_points
    effective_buyback_share_bps = buy_event.buyback_fee_basis_points
    pre_entry_replay = [
        {
            "signature": row["source"].signature,
            "observed_ms": row["source"].observed_ms,
            "observed_interval_ms": observation_interval_ms(row["source"]),
            "timing_source": row["source"].timing_source,
            "slot": row["source"].slot,
            "actual_event": event_to_dict(row["event"]),
        }
        for row in pre_entry_rows
    ]
    reserve_chain_index = {
        (row["source"].signature, row["source"].instruction_index): index
        for index, row in enumerate(successful_rows)
    }

    entry_config_fees = fee_config.fees_for(entry_state, mint_supply)
    entry_fees = Fees(
        protocol_bps=entry_config_fees.protocol_bps,
        creator_bps=effective_creator_bps,
        lp_bps=entry_config_fees.lp_bps,
    )
    hypothetical_buy = quote_exact_quote_in_buy(entry_state, trade.sol_in, entry_fees)
    state = hypothetical_buy.post_state
    inventory = hypothetical_buy.token_out
    state_observed_ms = intended_entry_ms
    state_observed_interval = (intended_entry_ms, intended_entry_ms)
    state_timing_source = "hypothetical_entry"
    state_slot = entry_state_source["source"].slot
    tape: list[dict[str, Any]] = [
        {
            "kind": "actual_pre_entry_trade",
            "signature": row["source"].signature,
            "side": row["source"].side,
            "instruction_kind": row["source"].instruction.kind,
            "instruction_arg0": row["source"].instruction.arg0,
            "instruction_arg1": row["source"].instruction.arg1,
            "slot": row["source"].slot,
            "transaction_index": row["source"].transaction_index,
            "instruction_index": row["source"].instruction_index,
            "reserve_chain_index": reserve_chain_index[
                (row["source"].signature, row["source"].instruction_index)
            ],
            "observed_ms": row["source"].observed_ms,
            "observed_interval_ms": observation_interval_ms(row["source"]),
            "observed_utc": utc_iso(row["source"].observed_ms),
            "timing_source": row["source"].timing_source,
            "chain_block_time_ms": row.get("block_time_ms"),
            "actual_event": event_to_dict(row["event"]),
            "counterfactual_mutation": "actual_chain_state_before_hypothetical_entry",
        }
        for row in pre_entry_rows
    ] + [
        {
            "kind": "hypothetical_buy",
            "at_ms": intended_entry_ms,
            "at_utc": utc_iso(intended_entry_ms),
            "source_slot": state_slot,
            "pre_state": state_to_dict(entry_state),
            "fees": dataclasses.asdict(entry_fees),
            "quote": quote_to_dict(hypothetical_buy),
            "pre_entry_external_replay": pre_entry_replay,
        }
    ]
    failed_rows = [
        row
        for row in rows
        if row["success"] is False
        and temporal_side(row["source"], intended_entry_ms) != "before"
        and temporal_side(row["source"], deadline_ms) != "after"
    ]
    state_snapshots: list[dict[str, Any]] = [
        {
            "interval": (intended_entry_ms, intended_entry_ms),
            "state": state,
            "creator_bps": effective_creator_bps,
            "cashback_bps": effective_cashback_bps,
            "buyback_share_bps": effective_buyback_share_bps,
            "slot": state_slot,
            "source": "hypothetical_entry",
        }
    ]

    outcome: Optional[str] = None
    exit_quote: Optional[SellQuote] = None
    exit_ms: Optional[int] = None
    exit_slot: Optional[int] = None
    state_age_ms: Optional[int] = None
    state_age_min_ms: Optional[int] = None
    state_age_max_ms: Optional[int] = None
    exit_timing_source: Optional[str] = None
    trigger_roi_pct: Optional[float] = None

    def inspect_exit(at_ms: int, slot: int) -> None:
        nonlocal outcome, exit_quote, exit_ms, exit_slot, state_age_ms
        nonlocal state_age_min_ms, state_age_max_ms, exit_timing_source, trigger_roi_pct
        if outcome is not None or at_ms < modeled_first_sellable_ms:
            return
        config_fees = fee_config.fees_for(state, mint_supply)
        fees = Fees(
            protocol_bps=config_fees.protocol_bps,
            creator_bps=effective_creator_bps,
            lp_bps=config_fees.lp_bps,
        )
        quote = quote_exact_token_sell(
            state,
            inventory,
            0,
            fees,
            cashback_bps=effective_cashback_bps,
            buyback_share_bps=effective_buyback_share_bps,
        )
        roi = (quote.immediate_quote_out / hypothetical_buy.gross_quote_in - 1) * 100
        if roi >= 20:
            outcome = "TP"
        elif roi <= -20:
            outcome = "SL"
        if outcome is not None:
            exit_quote = quote
            exit_ms = at_ms
            exit_slot = slot
            state_age_min_ms = max(0, at_ms - state_observed_interval[1])
            state_age_max_ms = max(0, at_ms - state_observed_interval[0])
            state_age_ms = state_age_min_ms if state_age_min_ms == state_age_max_ms else None
            exit_timing_source = state_timing_source
            trigger_roi_pct = roi

    inspect_exit(modeled_first_sellable_ms, state_slot)
    for row in future:
        source: SourceObservation = row["source"]
        if outcome is not None:
            break
        deadline_side = temporal_side(source, deadline_ms)
        if deadline_side == "ambiguous":
            raise ExactCurveError(
                f"event timing overlaps the exact T+120 boundary: {source.signature}"
            )
        if deadline_side == "after":
            continue
        sellable_side = temporal_side(source, modeled_first_sellable_ms)
        if sellable_side == "ambiguous":
            raise ExactCurveError(
                f"event timing overlaps first-sellable boundary: {source.signature}"
            )
        if source.signature in {trade.buy_signature, trade.sell_signature}:
            tape.append(
                {
                    "kind": "own_chain_trade_excluded",
                    "signature": source.signature,
                    "side": source.side,
                    "instruction_kind": source.instruction.kind,
                    "instruction_arg0": source.instruction.arg0,
                    "instruction_arg1": source.instruction.arg1,
                    "slot": source.slot,
                    "transaction_index": source.transaction_index,
                    "instruction_index": source.instruction_index,
                    "reserve_chain_index": reserve_chain_index.get(
                        (source.signature, source.instruction_index)
                    ),
                    "observed_ms": source.observed_ms,
                    "observed_interval_ms": observation_interval_ms(source),
                    "observed_utc": utc_iso(source.observed_ms),
                    "timing_source": source.timing_source,
                    "chain_block_time_ms": row.get("block_time_ms"),
                    "actual_event": event_to_dict(row["event"]),
                    "status": "successful_zero_counterfactual_mutation",
                }
            )
            state_snapshots.append(
                {
                    "interval": observation_interval_ms(source),
                    "state": state,
                    "creator_bps": effective_creator_bps,
                    "cashback_bps": effective_cashback_bps,
                    "buyback_share_bps": effective_buyback_share_bps,
                    "slot": source.slot,
                    "source": source.signature,
                }
            )
            continue
        config_fees = fee_config.fees_for(state, mint_supply)
        event: TradeEvent = row["event"]
        event_creator_bps = event.creator_fee_basis_points + event.cashback_fee_basis_points
        if event_creator_bps not in (0, config_fees.creator_bps):
            raise ExactCurveError(
                f"counterfactual creator-fee regime is unsupported: {source.signature}"
            )
        fees = Fees(config_fees.protocol_bps, event_creator_bps, config_fees.lp_bps)
        record: dict[str, Any] = {
            "kind": "external_trade",
            "signature": source.signature,
            "side": source.side,
            "instruction_kind": source.instruction.kind,
            "instruction_arg0": source.instruction.arg0,
            "instruction_arg1": source.instruction.arg1,
            "slot": source.slot,
            "transaction_index": source.transaction_index,
            "instruction_index": source.instruction_index,
            "reserve_chain_index": reserve_chain_index.get(
                (source.signature, source.instruction_index)
            ),
            "observed_ms": source.observed_ms,
            "observed_interval_ms": observation_interval_ms(source),
            "observed_utc": utc_iso(source.observed_ms),
            "timing_source": source.timing_source,
            "chain_block_time_ms": row.get("block_time_ms"),
            "chain_success": row["success"],
            "pre_state": state_to_dict(state),
            "fees": dataclasses.asdict(fees),
        }
        if row["event"] is None:
            raise ExactCurveError(f"successful source trade missing TradeEvent: {source.signature}")
        record["actual_event"] = event_to_dict(event)
        try:
            applied = apply_instruction(
                state,
                source.instruction,
                fees,
                cashback_bps=event.cashback_fee_basis_points,
                buyback_share_bps=event.buyback_fee_basis_points,
            )
        except ExactCurveError as exc:
            record["counterfactual_mutation"] = "zero_constraint_failure"
            record["counterfactual_error"] = str(exc)
            tape.append(record)
            continue
        state = applied.post_state
        effective_creator_bps = (
            event.creator_fee_basis_points + event.cashback_fee_basis_points
        )
        effective_cashback_bps = event.cashback_fee_basis_points
        effective_buyback_share_bps = event.buyback_fee_basis_points
        state_observed_ms = source.observed_ms
        state_observed_interval = observation_interval_ms(source)
        state_timing_source = source.timing_source
        state_slot = source.slot
        record["counterfactual_mutation"] = "applied"
        record["counterfactual_quote"] = quote_to_dict(applied)
        record["post_state"] = state_to_dict(state)
        tape.append(record)
        state_snapshots.append(
            {
                "interval": observation_interval_ms(source),
                "state": state,
                "creator_bps": effective_creator_bps,
                "cashback_bps": effective_cashback_bps,
                "buyback_share_bps": effective_buyback_share_bps,
                "slot": source.slot,
                "source": source.signature,
            }
        )
        if sellable_side == "after":
            inspect_exit(source.observed_ms, source.slot)

    if outcome is None:
        terminal_source = next(
            row["source"]
            for row in successful_rows
            if row["source"].signature == trade.sell_signature
        )
        terminal_side = temporal_side(terminal_source, deadline_ms)
        if terminal_side == "ambiguous":
            raise ExactCurveError("actual sell timing overlaps the exact T+120 boundary")
        if terminal_side != "after":
            raise ExactCurveError(
                "actual sell ended the available causal tape before T+120 and no exact TP/SL fired"
            )
        config_fees = fee_config.fees_for(state, mint_supply)
        fees = Fees(
            protocol_bps=config_fees.protocol_bps,
            creator_bps=effective_creator_bps,
            lp_bps=config_fees.lp_bps,
        )
        exit_quote = quote_exact_token_sell(
            state,
            inventory,
            0,
            fees,
            cashback_bps=effective_cashback_bps,
            buyback_share_bps=effective_buyback_share_bps,
        )
        exit_ms = deadline_ms
        exit_slot = state_slot
        state_age_min_ms = max(0, deadline_ms - state_observed_interval[1])
        state_age_max_ms = max(0, deadline_ms - state_observed_interval[0])
        state_age_ms = state_age_min_ms if state_age_min_ms == state_age_max_ms else None
        exit_timing_source = state_timing_source
        trigger_roi_pct = (exit_quote.immediate_quote_out / hypothetical_buy.gross_quote_in - 1) * 100
        outcome = "TRUE_120S_MAX_HOLD"

    modeled_exit_ms = int(exit_ms or deadline_ms)
    for row in failed_rows:
        source = row["source"]
        start_ms, end_ms = observation_interval_ms(source)
        if start_ms > modeled_exit_ms:
            continue
        prior = [snapshot for snapshot in state_snapshots if snapshot["interval"][1] <= start_ms]
        record = {
            "kind": "failed_chain_transaction",
            "signature": source.signature,
            "ownership": "own" if wallet in source.source_accounts else "external",
            "side": source.side,
            "slot": source.slot,
            "transaction_index": source.transaction_index,
            "instruction_index": source.instruction_index,
            "reserve_chain_index": None,
            "instruction_kind": source.instruction.kind,
            "instruction_arg0": source.instruction.arg0,
            "instruction_arg1": source.instruction.arg1,
            "observed_ms": source.observed_ms,
            "observed_interval_ms": (start_ms, end_ms),
            "observed_utc": utc_iso(source.observed_ms),
            "timing_source": source.timing_source,
            "chain_block_time_ms": row.get("block_time_ms"),
            "rpc_error": row.get("rpc_error"),
            "counterfactual_mutation": "zero_failed_chain_transaction",
        }
        if not prior:
            record["constraint_replay"] = "AMBIGUOUS_NO_PRIOR_COUNTERFACTUAL_STATE"
        else:
            snapshot = max(prior, key=lambda item: item["interval"][1])
            overlapping = [
                item
                for item in state_snapshots
                if item["interval"][0] <= end_ms and item["interval"][1] > start_ms
            ]
            if overlapping:
                record["constraint_replay"] = "AMBIGUOUS_STATE_EVENT_IN_SAME_TIME_INTERVAL"
            else:
                failed_state = snapshot["state"]
                config_fees = fee_config.fees_for(failed_state, mint_supply)
                failed_fees = Fees(
                    config_fees.protocol_bps,
                    int(snapshot["creator_bps"]),
                    config_fees.lp_bps,
                )
                record["counterfactual_pre_state"] = state_to_dict(failed_state)
                record["counterfactual_state_source"] = snapshot["source"]
                record["fees"] = dataclasses.asdict(failed_fees)
                try:
                    counterfactual = apply_instruction(
                        failed_state, source.instruction, failed_fees
                    )
                except ExactCurveError as exc:
                    record["constraint_replay"] = "FAIL"
                    record["constraint_failure"] = str(exc)
                else:
                    record["constraint_replay"] = "PASS_BUT_CHAIN_FAILED_OTHERWISE"
                    record["counterfactual_quote"] = quote_to_dict(counterfactual)
        tape.append(record)

    actual_buy_cost = buy_event.immediate_quote_amount
    actual_sell_proceeds = sell_event.immediate_quote_amount
    actual_curve_roi = (actual_sell_proceeds / actual_buy_cost - 1) * 100
    buy_network_fee = int((buy_tx.get("meta") or {}).get("fee") or 0)
    sell_network_fee = int((sell_tx.get("meta") or {}).get("fee") or 0)
    actual_all_in_roi = (
        (actual_sell_proceeds - sell_network_fee) / (actual_buy_cost + buy_network_fee) - 1
    ) * 100
    exact_return = (exit_quote.immediate_quote_out / hypothetical_buy.gross_quote_in - 1) * 100
    old = (canonical or {}).get("p75_primary") or {}
    old_return = old.get("pnl")

    entry_interval = observation_interval_ms(entry_state_source["source"])
    entry_age_min_ms = max(0, intended_entry_ms - entry_interval[1])
    entry_age_max_ms = max(0, intended_entry_ms - entry_interval[0])
    tape.sort(
        key=lambda row: (
            int(row.get("at_ms") or row.get("observed_ms") or 0),
            int(row.get("reserve_chain_index"))
            if row.get("reserve_chain_index") is not None
            else 1 << 30,
            int(row.get("instruction_index") or -1),
        )
    )
    for sequence_index, record in enumerate(tape):
        record["causal_sequence_index"] = sequence_index
    failed_constraint_counts = {
        status: sum(
            1
            for record in tape
            if record.get("kind") == "failed_chain_transaction"
            and record.get("constraint_replay") == status
        )
        for status in {
            record.get("constraint_replay")
            for record in tape
            if record.get("kind") == "failed_chain_transaction"
        }
        if status is not None
    }

    result = {
        "evaluator": EVALUATOR_NAME,
        "session": trade.session,
        "mint": trade.mint,
        "wallet": wallet,
        "buy_signature": trade.buy_signature,
        "sell_signature": trade.sell_signature,
        "selected_ms": trade.selected_ms,
        "selected_utc": utc_iso(trade.selected_ms),
        "intended_entry_ms": intended_entry_ms,
        "intended_entry_utc": utc_iso(intended_entry_ms),
        "first_sellable_scenario": first_sellable_label,
        "first_sellable_delay_ms": first_sellable_delay_ms,
        "first_sellable_ms": modeled_first_sellable_ms,
        "first_sellable_utc": utc_iso(modeled_first_sellable_ms),
        "observed_live_first_sellable_ms": trade.first_sellable_ms,
        "observed_live_first_sellable_utc": utc_iso(trade.first_sellable_ms),
        "entry_state_slot": entry_state_source["source"].slot,
        "entry_state_observed_ms": entry_state_source["source"].observed_ms,
        "entry_state_observed_interval_ms": entry_interval,
        "entry_state_timing_source": entry_state_source["source"].timing_source,
        "entry_state_age_ms": entry_age_min_ms
        if entry_age_min_ms == entry_age_max_ms
        else None,
        "entry_state_age_min_ms": entry_age_min_ms,
        "entry_state_age_max_ms": entry_age_max_ms,
        "entry_state": state_to_dict(entry_state),
        "mint_supply": mint_supply,
        "fee_config": fee_config.address,
        "hypothetical_buy": quote_to_dict(hypothetical_buy),
        "entry_fee_regime": {
            "protocol_bps": entry_fees.protocol_bps,
            "creator_charge_bps": entry_fees.creator_bps,
            "creator_fee_bps": buy_event.creator_fee_basis_points,
            "cashback_fee_bps": buy_event.cashback_fee_basis_points,
            "buyback_share_bps": buy_event.buyback_fee_basis_points,
        },
        "hypothetical_entry_quote_lamports_per_raw_token": (
            hypothetical_buy.gross_quote_in / hypothetical_buy.token_out
        ),
        "own_buy_curve_impact": {
            "virtual_sol_delta": hypothetical_buy.post_state.virtual_sol
            - entry_state.virtual_sol,
            "virtual_token_delta": hypothetical_buy.post_state.virtual_token
            - entry_state.virtual_token,
            "real_sol_delta": hypothetical_buy.post_state.real_sol - entry_state.real_sol,
            "real_token_delta": hypothetical_buy.post_state.real_token - entry_state.real_token,
        },
        "actual_buy_event": event_to_dict(buy_event),
        "actual_buy_cost_lamports": actual_buy_cost,
        "actual_buy_network_fee_lamports": buy_network_fee,
        "inventory_raw": inventory,
        "actual_inventory_raw": buy_event.token_amount,
        "inventory_delta_raw": inventory - buy_event.token_amount,
        "outcome": outcome,
        "exit_ms": exit_ms,
        "exit_utc": utc_iso(exit_ms),
        "exit_state_slot": exit_slot,
        "exit_state_age_ms": state_age_ms,
        "exit_state_age_min_ms": state_age_min_ms,
        "exit_state_age_max_ms": state_age_max_ms,
        "exit_state_timing_source": exit_timing_source,
        "exit_curve_state_before_hypothetical_sell": state_to_dict(state),
        "exit_quote": quote_to_dict(exit_quote),
        "exit_fee_regime": {
            "protocol_fee_lamports": exit_quote.protocol_fee,
            "creator_or_cashback_charge_lamports": exit_quote.creator_fee,
            "lp_fee_lamports": exit_quote.lp_fee,
            "cashback_accrual_lamports": exit_quote.cashback_accrual,
            "buyback_fee_allocation_lamports": exit_quote.buyback_fee_allocation,
        },
        "hypothetical_exit_quote_lamports_per_raw_token": (
            exit_quote.immediate_quote_out / inventory
        ),
        "exact_return_pct": exact_return,
        "trigger_return_pct": trigger_roi_pct,
        "actual_sell_event": event_to_dict(sell_event),
        "actual_sell_proceeds_lamports": actual_sell_proceeds,
        "actual_sell_network_fee_lamports": sell_network_fee,
        "actual_curve_roi_pct": actual_curve_roi,
        "actual_all_in_roi_pct": actual_all_in_roi,
        "old_canonical_outcome": old.get("outcome"),
        "old_canonical_return_pct": old_return,
        "old_minus_exact_return_pct": (float(old_return) - exact_return)
        if old_return is not None
        else None,
        "exact_minus_actual_curve_return_pct": exact_return - actual_curve_roi,
        "event_tape_rows": len(tape),
        "successful_pre_entry_chain_trades": sum(
            1 for record in tape if record.get("kind") == "actual_pre_entry_trade"
        ),
        "successful_external_trades_replayed": sum(
            1 for record in tape if record.get("kind") == "external_trade"
        ),
        "successful_own_trades_zeroed": sum(
            1 for record in tape if record.get("kind") == "own_chain_trade_excluded"
        ),
        "failed_transactions_zeroed": sum(
            1 for record in tape if record.get("kind") == "failed_chain_transaction"
        ),
        "failed_constraint_replay_counts": failed_constraint_counts,
        "curve_history_transactions": trade.history_transaction_count,
        "authoritative_trade_transitions_reproduced": len(successful_rows),
        "fee_tier_validation": "PASS",
        "reserve_continuity_validation": "PASS",
        "causal_entry_state_validation": "PASS",
        "finalized_status_coverage": "PASS",
        "no_market_cap_fallback": True,
    }
    return result, tape


def markdown_report(
    aggregate: dict[str, Any],
    results: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {EVALUATOR_NAME}",
        "",
        f"Generated: {aggregate['generated_utc']}",
        "",
        "## Validation Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Discovered live buys | {aggregate['discovered_live_buys']} |",
        f"| Exactly evaluable completed trades | {aggregate['usable_completed_trades']} |",
        f"| Explicit blockers/exclusions | {aggregate['excluded_trades']} |",
        f"| Exact PF | {aggregate['exact_economics']['profit_factor']} |",
        f"| Exact expectancy | {aggregate['exact_economics']['expectancy_pct']}% |",
        f"| Exact PnL @1 SOL linear | {aggregate['exact_economics']['pnl_sol_at_1_sol_linear']} SOL |",
        f"| Old canonical PF (matched) | {aggregate['old_canonical']['profit_factor']} |",
        f"| Actual curve PF | {aggregate['actual_curve']['profit_factor']} |",
        f"| Actual network-adjusted PF | {aggregate['actual_live_network_adjusted']['profit_factor']} |",
        f"| Exact P75 vs actual curve median abs delta | "
        f"{aggregate['comparison']['exact_p75_minus_actual_curve']['median_absolute_pct_points']} pp |",
        f"| P50/P75/P90 first-sellable | 964 / 1476 / 1989 ms |",
        "",
        "## Trade Comparison",
        "",
        "| Session | Mint | Old P75 | Exact P50 | Exact P75 | Exact P90 | Actual curve | P75 outcome |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in results:
        old = row.get("old_canonical_return_pct")
        old_text = f"{old:.4f}%" if isinstance(old, (int, float)) else "n/a"
        scenarios = row["first_sellable_scenarios"]
        lines.append(
            f"| {row['session']} | `{row['mint']}` | {old_text} | "
            f"{scenarios['P50']['return_pct']:.4f}% | {scenarios['P75']['return_pct']:.4f}% | "
            f"{scenarios['P90']['return_pct']:.4f}% | {row['actual_curve_roi_pct']:.4f}% | "
            f"{row['outcome']} |"
        )
    if exclusions:
        lines.extend(["", "## Explicit Blockers", ""])
        for row in exclusions:
            lines.append(
                f"- `{row.get('session', 'unknown')}` `{row.get('mint', 'n/a')}`: {row.get('reason')}"
            )
    lines.extend(
        [
            "",
            "## Semantics",
            "",
            "Successful transactions mutate the replay only after finalized RPC success. Failed "
            "transactions mutate zero. Pump TradeEvent states and integer curve math are mandatory. "
            "No market-cap ratio, future mark, or theoretical token-output fallback is permitted.",
            "",
        ]
    )
    return "\n".join(lines)


def run_live_validation(args: argparse.Namespace) -> int:
    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_dirs = [pathlib.Path(path) for path in args.audit_dir]
    trades, discovery_exclusions = discover_live_trades(audit_dirs)
    mints = {trade.mint for trade in trades}
    if not trades:
        raise ExactCurveError("no live Ranker buy results discovered")
    observations, scan_stats = scan_observations(
        pathlib.Path(args.observations), mints, output_dir
    )
    rpc_url = args.rpc_url or rpc_url_from_env_file(args.env_file, args.rpc_env)
    rpc = RpcClient(rpc_url, timeout=args.rpc_timeout)
    histories: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        if not trade.bonding_curve or not trade.sell_signature:
            continue
        history, complete = fetch_curve_history_between(
            rpc,
            trade.bonding_curve,
            trade.buy_signature,
            trade.sell_signature,
        )
        histories[trade.buy_signature] = history
        trade.history_boundary_complete = complete
        trade.history_transaction_count = len(history) + 2
    signatures = sorted(
        {row.signature for values in observations.values() for row in values}
        | {trade.buy_signature for trade in trades}
        | {trade.sell_signature for trade in trades if trade.sell_signature}
        | {
            item["signature"]
            for history in histories.values()
            for item in history
            if item.get("signature")
        }
    )
    tx_results = fetch_transactions(rpc, signatures)
    scan_stats.update(augment_with_curve_history(trades, observations, histories, tx_results))
    canonical = load_canonical_outcomes(pathlib.Path(args.canonical_outcomes), mints)

    fee_configs: dict[str, FeeConfig] = {}
    supplies: dict[str, int] = {}
    results: list[dict[str, Any]] = []
    exclusions = list(discovery_exclusions)
    tapes_dir = output_dir / "event-tapes"
    tapes_dir.mkdir(exist_ok=True)
    for trade in trades:
        try:
            mint_observations = observations.get(trade.mint, [])
            if not mint_observations:
                raise ExactCurveError("no normalized source observations for mint")
            fee_address = infer_fee_config_address(mint_observations, trade.buy_signature)
            if not fee_address:
                raise ExactCurveError("fee config account cannot be derived from source accounts")
            if fee_address not in fee_configs:
                fee_configs[fee_address] = fetch_fee_config(rpc, fee_address)
            if trade.mint not in supplies:
                supplies[trade.mint] = fetch_mint_supply(rpc, trade.mint)
            scenario_results: dict[str, dict[str, Any]] = {}
            for label, delay_ms in FIRST_SELLABLE_SCENARIOS_MS.items():
                scenario_result, tape = evaluate_trade(
                    trade,
                    mint_observations,
                    tx_results,
                    fee_configs[fee_address],
                    supplies[trade.mint],
                    canonical.get(trade.mint),
                    args.wallet,
                    label,
                    delay_ms,
                )
                scenario_results[label] = scenario_result
                tape_path = tapes_dir / f"{trade.session}__{trade.mint}__{label}.jsonl"
                with tape_path.open("w", encoding="utf-8") as handle:
                    for row in tape:
                        handle.write(
                            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                        )
            result = scenario_results["P75"]
            result["first_sellable_scenarios"] = {
                label: {
                    "delay_ms": row["first_sellable_delay_ms"],
                    "outcome": row["outcome"],
                    "return_pct": row["exact_return_pct"],
                    "exit_ms": row["exit_ms"],
                    "exit_utc": row["exit_utc"],
                    "exit_state_slot": row["exit_state_slot"],
                    "trigger_return_pct": row["trigger_return_pct"],
                }
                for label, row in scenario_results.items()
            }
            results.append(result)
        except Exception as exc:
            exclusions.append(
                {
                    "session": trade.session,
                    "mint": trade.mint,
                    "buy_signature": trade.buy_signature,
                    "reason": str(exc),
                }
            )

    exact_returns_by_scenario = {
        label: [
            float(row["first_sellable_scenarios"][label]["return_pct"])
            for row in results
        ]
        for label in FIRST_SELLABLE_SCENARIOS_MS
    }
    exact_returns = exact_returns_by_scenario["P75"]
    actual_returns = [float(row["actual_curve_roi_pct"]) for row in results]
    actual_all_in_returns = [float(row["actual_all_in_roi_pct"]) for row in results]
    old_returns = [
        float(row["old_canonical_return_pct"])
        for row in results
        if row.get("old_canonical_return_pct") is not None
    ]
    aggregate = {
        "evaluator": EVALUATOR_NAME,
        "version": EVALUATOR_VERSION,
        "generated_utc": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "discovered_live_buys": len(trades),
        "usable_completed_trades": len(results),
        "excluded_trades": len(exclusions),
        "exact_economics": summarize_returns(exact_returns),
        "exact_economics_by_first_sellable": {
            label: summarize_returns(values)
            for label, values in exact_returns_by_scenario.items()
        },
        "actual_curve": summarize_returns(actual_returns),
        "actual_live_network_adjusted": summarize_returns(actual_all_in_returns),
        "old_canonical": summarize_returns(old_returns),
        "comparison": {
            "exact_p75_minus_actual_curve": summarize_deltas(
                [
                    float(row["exact_return_pct"]) - float(row["actual_curve_roi_pct"])
                    for row in results
                ]
            ),
            "old_canonical_minus_exact_p75": summarize_deltas(
                [
                    float(row["old_canonical_return_pct"]) - float(row["exact_return_pct"])
                    for row in results
                    if row.get("old_canonical_return_pct") is not None
                ]
            ),
        },
        "timing_provenance": {
            "entry_state_sources": dict(
                sorted(
                    {
                        source: sum(
                            1 for row in results if row["entry_state_timing_source"] == source
                        )
                        for source in {row["entry_state_timing_source"] for row in results}
                    }.items()
                )
            ),
            "p75_exit_state_sources": dict(
                sorted(
                    {
                        source: sum(
                            1 for row in results if row["exit_state_timing_source"] == source
                        )
                        for source in {row["exit_state_timing_source"] for row in results}
                    }.items()
                )
            ),
        },
        "failed_transaction_replay": {
            "finalized_failures_zeroed": sum(
                int(row["failed_transactions_zeroed"]) for row in results
            ),
            "constraint_status_counts": {
                status: sum(
                    int(row["failed_constraint_replay_counts"].get(status, 0))
                    for row in results
                )
                for status in {
                    status
                    for row in results
                    for status in row["failed_constraint_replay_counts"]
                }
            },
        },
        "scan": scan_stats,
        "rpc_transactions_requested": len(signatures),
        "rpc_transactions_available": sum(value is not None for value in tx_results.values()),
        "rpc_transactions_missing": sum(value is None for value in tx_results.values()),
        "fee_configs": {address: dataclasses.asdict(config) for address, config in fee_configs.items()},
        "semantic_guards": {
            "finalized_rpc_status_authoritative": True,
            "failed_transaction_zero_mutation": True,
            "atomic_success_or_zero_failure": True,
            "trade_event_reserves_required": True,
            "dynamic_fee_config_required": True,
            "market_cap_ratio_fallback": False,
            "tp_pct": 20,
            "sl_pct": -20,
            "max_hold_ms": 120_000,
            "first_sellable_scenarios_ms": FIRST_SELLABLE_SCENARIOS_MS,
        },
        "validation_evidence": {
            "authoritative_trade_transitions_reproduced": sum(
                int(row["authoritative_trade_transitions_reproduced"])
                for row in results
            ),
            "inventory_mismatches_admitted": 0,
            "reserve_continuity_mismatches_admitted": 0,
            "fee_or_instruction_mismatches_admitted": 0,
            "market_cap_fallback_rows": 0,
        },
    }
    with (output_dir / "trades.jsonl").open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    with (output_dir / "excluded.jsonl").open("w", encoding="utf-8") as handle:
        for row in exclusions:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    (output_dir / "aggregate.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        markdown_report(aggregate, results, exclusions), encoding="utf-8"
    )
    evaluator_source = pathlib.Path(__file__).read_bytes()
    (output_dir / "exactCurveEconomicsV1.py").write_bytes(evaluator_source)
    manifest = {
        "evaluator": EVALUATOR_NAME,
        "version": EVALUATOR_VERSION,
        "math_spec_sha256": hashlib.sha256(
            (
                "exact_quote_in:floor+separate_ceil+net_minus_one;"
                "exact_token_buy:ceil+one;"
                "sell:floor_constant_product+separate_ceil;"
                "failed_tx:zero;event_reserves:mandatory;mc_fallback:none"
            ).encode()
        ).hexdigest(),
        "evaluator_source_sha256": hashlib.sha256(evaluator_source).hexdigest(),
        "output_files": [
            "aggregate.json",
            "trades.jsonl",
            "excluded.jsonl",
            "report.md",
            "event-tapes/",
            "exactCurveEconomicsV1.py",
        ],
        "audit_dirs": [str(path) for path in audit_dirs],
        "observations": str(args.observations),
        "canonical_outcomes": str(args.canonical_outcomes),
        "wallet": args.wallet,
        "rpc_secret_persisted": False,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(aggregate, indent=2, sort_keys=True))
    return 0 if results else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    live = subparsers.add_parser("validate-live", help="validate completed live Ranker trades")
    live.add_argument("--audit-dir", action="append", required=True)
    live.add_argument("--observations", required=True)
    live.add_argument("--canonical-outcomes", required=True)
    live.add_argument("--wallet", required=True)
    live.add_argument("--output-dir", required=True)
    live.add_argument("--rpc-url")
    live.add_argument("--env-file", default="/root/bot/FinalBot-cya/.env")
    live.add_argument("--rpc-env", default="SECURE_RPC_URL")
    live.add_argument("--rpc-timeout", type=float, default=30.0)
    live.set_defaults(handler=run_live_validation)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.handler(args))
    except ExactCurveError as exc:
        print(f"{EVALUATOR_NAME}_BLOCKER: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
