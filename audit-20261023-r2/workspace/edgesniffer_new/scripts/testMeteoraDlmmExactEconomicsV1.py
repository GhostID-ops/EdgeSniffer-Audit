import struct

from meteoraDlmmExactEconomicsV1 import SWAP2_EVENT, decode_swap2_event, evaluate_fixed_exit


def payload(direction: int, amount_in: int, amount_left: int, amount_out: int) -> bytes:
    return SWAP2_EVENT + bytes([1]) * 32 + bytes([2]) * 32 + struct.pack("<2i", 1, 2) + bytes([direction]) + (100).to_bytes(16, "little") + struct.pack("<7Q", amount_in, amount_left, amount_out, 1, 1, 1, 1) + bytes([1, 1])


def main() -> None:
    buy = decode_swap2_event(payload(0, 100, 0, 200))
    sell = decode_swap2_event(payload(1, 200, 0, 130))
    result = evaluate_fixed_exit(buy, sell, quote_is_token_x=False, notional_quote_raw=100)
    assert result.quote_received_raw == 130
    assert result.pnl_quote_raw == 30
    bad = bytearray(payload(0, 100, 0, 200)); bad[80] = 2
    try:
        decode_swap2_event(bytes(bad))
    except ValueError:
        pass
    else:
        raise AssertionError("invalid swap direction was accepted")
    print("PASS testMeteoraDlmmExactEconomicsV1")


if __name__ == "__main__":
    main()
