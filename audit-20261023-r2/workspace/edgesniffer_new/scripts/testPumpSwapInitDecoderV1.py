#!/usr/bin/env python3
"""Deterministic contract tests for the finalized PumpSwap init decoder."""

from __future__ import annotations

import base64
import struct

import launchLabExactEconomicsV1 as b58
import pumpSwapInitDecoderV1 as decoder


def key(seed: int) -> str:
    return b58.b58encode(bytes([seed]) * 32)


def event(*, pool: str, creator: str, base: str, quote: str, index: int, base_in: int, quote_in: int,
          coin_creator: str, mayhem: bool) -> bytes:
    raw = bytearray(decoder.CREATE_POOL_EVENT)
    raw += struct.pack("<qH", 1_726_000_000, index)
    raw += b58.b58decode(creator) + b58.b58decode(base) + b58.b58decode(quote)
    raw += bytes([6, 9])
    raw += struct.pack("<7Q", base_in, quote_in, base_in, quote_in, 1, 2, 3)
    raw += bytes([7])
    raw += b58.b58decode(pool) + bytes([8]) * 32 + bytes([9]) * 32 + bytes([10]) * 32
    raw += b58.b58decode(coin_creator) + bytes([int(mayhem)]) + struct.pack("<Q", 0) + bytes([1, 0])
    assert len(raw) == 344
    return bytes(raw)


def tx(*, event_payload: bytes | None, quote_override: str | None = None, account_count: int = 18) -> dict:
    pool, creator, base, quote, coin_creator = key(1), key(2), key(3), key(4), key(5)
    accounts = [pool, key(6), creator, base, quote_override or quote] + [key(index) for index in range(7, 20)]
    raw = decoder.CREATE_POOL + struct.pack("<HQQ", 0, 100, 200) + b58.b58decode(coin_creator) + bytes([0])
    keys = [decoder.PUMP_PROGRAM_ID, decoder.PUMPSWAP_PROGRAM_ID, *accounts]
    logs = ["Program log: Instruction: Migrate"]
    if event_payload is not None:
        logs.append("Program data: " + base64.b64encode(event_payload).decode())
    return {
        "meta": {"err": None, "logMessages": logs, "loadedAddresses": {}},
        "transaction": {"message": {"accountKeys": keys, "instructions": [{"programIdIndex": 0, "accounts": [], "data": ""}]}},
        "innerInstructions": [],
        # Inner create_pool ensures this is a Pump migration sequence.
        "_inner": {"programIdIndex": 1, "accounts": list(range(2, 2 + account_count)), "data": b58.b58encode(raw)},
    }


def normalized_tx(**kwargs) -> dict:
    value = tx(**kwargs)
    value["meta"]["innerInstructions"] = [{"index": 0, "instructions": [value.pop("_inner")]}]
    return value


def main() -> None:
    pool, creator, base, quote, coin_creator = key(1), key(2), key(3), key(4), key(5)
    valid_event = event(pool=pool, creator=creator, base=base, quote=quote, index=0,
                        base_in=100, quote_in=200, coin_creator=coin_creator, mayhem=False)
    decoded = decoder.validate_finalized_init(normalized_tx(event_payload=valid_event))
    assert decoded is not None and decoded.pool == pool and decoded.canonical_pump_migration

    wrong_quote = event(pool=pool, creator=creator, base=base, quote=key(42), index=0,
                        base_in=100, quote_in=200, coin_creator=coin_creator, mayhem=False)
    try:
        decoder.validate_finalized_init(normalized_tx(event_payload=wrong_quote))
        raise AssertionError("mismatched event must fail closed")
    except decoder.PumpSwapInitDecodeError:
        pass

    try:
        decoder.validate_finalized_init(normalized_tx(event_payload=None))
        raise AssertionError("create_pool without canonical event must fail closed")
    except decoder.PumpSwapInitDecodeError:
        pass
    try:
        decoder.validate_finalized_init(normalized_tx(event_payload=valid_event, account_count=17))
        raise AssertionError("short account layout must fail closed")
    except decoder.PumpSwapInitDecodeError:
        pass
    print("PUMPSWAP_INIT_DECODER_V1_TESTS_PASS")


if __name__ == "__main__":
    main()
