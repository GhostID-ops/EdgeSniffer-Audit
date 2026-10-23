from evaluateWalletCausalEconomicsV1 import buy_tokens, sell_quote


def test_buy_and_sell_are_positive_with_valid_reserves():
    state = {"base": 1_000_000, "quote": 1_000_000_000, "virtual_quote": 0}
    tokens = buy_tokens(100_000_000, state, 100, 0)
    assert tokens > 0
    quote = sell_quote(tokens, state, 100, 0)
    assert quote > 0


def test_invalid_state_fails_closed():
    assert buy_tokens(100, {"base": 0, "quote": 0}, 0, 0) == 0
