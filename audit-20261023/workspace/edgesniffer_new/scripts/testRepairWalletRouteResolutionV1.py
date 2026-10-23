from repairWalletRouteResolutionV1 import repair_route


def test_exact_route_is_repaired():
    route, category = repair_route({"underlying_venues": [{"venue": "PUMPSWAP", "invocation": "CPI"}]})
    assert category == "EXACT_SINGLE_VENUE"
    assert route["repaired_route"] == "PUMPSWAP:CPI"


def test_unknown_route_is_not_falsely_promoted():
    route, category = repair_route({"underlying_venues": [], "unknown_programs": ["X"]})
    assert category == "UNRESOLVED_PROGRAM"
    assert route["repaired"] is False
