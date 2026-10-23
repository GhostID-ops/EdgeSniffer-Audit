from captureWalletBoundedCausalControlsV1 import exact_control_transitions


def test_non_pumpswap_control_fails_closed():
    assert exact_control_transitions({"transaction": {"message": {}}, "meta": {"logMessages": []}}) == []
