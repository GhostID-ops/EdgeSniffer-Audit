#!/usr/bin/env python3
import struct

from launchLabExactEconomicsV1 import b58encode
from orcaWhirlpoolExactEconomicsV1 import TRADED_EVENT, decode_event, evaluate_fixed_exit


def payload(direction: int, input_amount: int, output_amount: int) -> bytes:
    return TRADED_EVENT + bytes(range(32)) + bytes([direction]) + (10).to_bytes(16, "little") + (11).to_bytes(16, "little") + struct.pack("<6Q", input_amount, output_amount, 2, 1, 1, 1)


def main() -> None:
    buy = decode_event(payload(0, 100, 200))
    sell = decode_event(payload(1, 200, 130))
    result = evaluate_fixed_exit(buy, sell, quote_is_token_a=False, notional_quote_raw=102)
    assert buy.whirlpool == b58encode(bytes(range(32)))
    assert result.quote_received_raw == 129
    assert result.pnl_quote_raw == 27
    bad = bytearray(payload(0, 100, 200)); bad[40] = 9
    try:
        decode_event(bytes(bad))
    except ValueError:
        pass
    else:
        raise AssertionError("invalid fee bool was accepted")
    print("PASS testOrcaWhirlpoolExactEconomicsV1")


if __name__ == "__main__":
    main()
