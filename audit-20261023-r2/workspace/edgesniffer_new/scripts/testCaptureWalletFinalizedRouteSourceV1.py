from captureWalletFinalizedRouteSourceV1 import classify_programs, load_seen, PUMP, PUMPSWAP

def test_classifies_pumpswap():
    tx = {"transaction": {"message": {"accountKeys": [PUMPSWAP], "instructions": [{"programIdIndex": 0}]}}, "meta": {}}
    assert classify_programs(tx)[0] == "PUMPSWAP_PROGRAM"

def test_classifies_pump():
    tx = {"transaction": {"message": {"accountKeys": [PUMP], "instructions": [{"programIdIndex": 0}]}}, "meta": {}}
    assert classify_programs(tx)[0] == "PUMP_PROGRAM"

def test_load_seen_skips_malformed_jsonl_record(tmp_path):
    path = tmp_path / "source.jsonl"
    path.write_text('{"signature":"good"}\n{"signature":"incomplete"', encoding="utf-8")
    assert load_seen(path) == {"good"}
