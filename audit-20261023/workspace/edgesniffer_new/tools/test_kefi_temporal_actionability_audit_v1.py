import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "kefi_temporal_actionability_audit_v1.py"


def module():
    spec = importlib.util.spec_from_file_location("audit", SCRIPT); assert spec and spec.loader
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


def row(**overrides):
    result = {"source_frame_sha256":"frame", "event_index":0, "received_at_unix_ns":1, "slot":1,
              "event_timestamp":1, "side":"buy", "pool":"pool", "mint":"mint",
              "is_tracked_teacher":False, "lp_fee_bps":1, "protocol_fee_bps":1, "buyback_bps":0,
              "cashback_bps":0, "coin_creator_bps":0}
    result.update(overrides); return result


def test_actionability_audit_accepts_complete_rows(tmp_path):
    path = tmp_path / "rows.jsonl"; path.write_text(__import__("json").dumps(row()) + "\n")
    report = module().inspect([path])
    assert report["actionability_ready"] and report["unique_source_events"] == 1


def test_actionability_audit_fails_closed_on_conflict_or_missing_fee(tmp_path):
    import json
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(row()) + "\n" + json.dumps(row(pool="different")) + "\n" + json.dumps(row(source_frame_sha256="other", lp_fee_bps=None)) + "\n")
    report = module().inspect([path])
    assert not report["actionability_ready"]
    assert report["defects"]["conflicting_event_identity"] == 1
    assert report["defects"]["missing_or_invalid_fee_schedule"] == 1
