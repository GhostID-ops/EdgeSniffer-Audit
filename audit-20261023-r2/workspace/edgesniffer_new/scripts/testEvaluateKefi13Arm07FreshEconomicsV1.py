import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("arm07_evaluator", ROOT / "scripts/evaluateKefi13Arm07FreshEconomicsV1.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def test_exact_fixed_horizon_report_requires_controls():
    base = {
        "mint": "m", "pool": "p", "quote_mint": module.WSOL, "base_amount": 100,
        "pool_base_raw": 1_000_000, "pool_quote_raw": 2_000_000, "pool_quote_delta": 1_000,
        "virtual_quote_reserves": 0, "lp_fee_bps": 20, "protocol_fee_bps": 5, "coin_creator_bps": 0,
        "cashback_bps": 0, "received_at_unix_ns": 1_000_000_000, "source_frame_sha256": "a",
        "event_index": 0, "exact_transition_validated": True, "side": "BUY",
    }
    entry = dict(base, is_tracked_teacher=True, candidate_signature="s")
    mark = dict(base, is_tracked_teacher=False, candidate_signature="m2", received_at_unix_ns=32_000_000_000, source_frame_sha256="b", event_index=0, pool_base_raw=999_000, pool_quote_raw=2_001_000)
    observation = {
        "observation_id": "o", "arm_id": "ARM07_PUMPSWAP_DIRECT", "state": {"decoded_event": entry}
    }
    report = module.evaluate([observation], [entry, mark], 100_000, module.HORIZON_NS)
    assert report["metrics"]["actionable_n"] == 1
    assert report["metrics"]["controls_outperformed"] is None
    assert report["promotion_eligible"] is False


def test_matched_same_pool_control_is_evaluated():
    base = {
        "mint": "m", "pool": "p", "quote_mint": module.WSOL, "base_amount": 100,
        "pool_base_raw": 1_000_000, "pool_quote_raw": 2_000_000, "pool_quote_delta": 1_000,
        "virtual_quote_reserves": 0, "lp_fee_bps": 20, "protocol_fee_bps": 5, "coin_creator_bps": 0,
        "cashback_bps": 0, "exact_transition_validated": True,
    }
    control = dict(base, is_tracked_teacher=False, candidate_signature="c", side="BUY", received_at_unix_ns=900_000_000, source_frame_sha256="c", event_index=0)
    entry = dict(base, is_tracked_teacher=True, candidate_signature="s", side="BUY", received_at_unix_ns=1_000_000_000, source_frame_sha256="a", event_index=0)
    mark = dict(base, is_tracked_teacher=False, candidate_signature="m", side="BUY", received_at_unix_ns=32_000_000_000, source_frame_sha256="b", event_index=0, pool_base_raw=999_000, pool_quote_raw=2_001_000)
    observation = {"observation_id": "o", "arm_id": "ARM07_PUMPSWAP_DIRECT", "state": {"decoded_event": entry}}
    report = module.evaluate([observation], [control, entry, mark], 100_000, module.HORIZON_NS)
    assert report["metrics"]["controls_n"] == 1
    assert report["metrics"]["controls_outperformed"] is not None
