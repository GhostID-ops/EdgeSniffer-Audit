from __future__ import annotations

import json
from pathlib import Path

from build2CQFingerprintFromEvidenceV1 import main as build_main
from materialize2CQFingerprintMarketWindowsV1 import materialize


def state(vs: int) -> dict:
    return {"virtual_sol": vs, "virtual_token": 1_000_000_000_000,
            "real_sol": vs, "real_token": 900_000_000_000}


def event(mint: str, ts: int, side: str, vs: int, quote_mint: str = "11111111111111111111111111111111") -> dict:
    return {"mint": mint, "side": side.upper(), "route": "PUMP_BONDING_CURVE_TOP_LEVEL",
            "quote_mint": quote_mint, "block_time": ts // 1000, "slot": ts,
            "pre_state": state(vs), "post_state": state(vs + 1_000_000_000),
            "sol_amount_raw": 100_000_000, "quote_amount_raw": 100_000_000}


def native(mint: str, ts: int, side: str, vs: int) -> dict:
    return {"mint": mint, "side": side, "local_receive_ts_ms": ts,
            "raw_record": {"side": side, "mint": mint, "nativeKind": "flow",
                            "wallet": "market-wallet", "teacherWalletMatched": False},
            "canonical_state": {"route": "PUMP_BONDING_CURVE_TOP_LEVEL",
                                 "pre_state": state(vs), "post_state": state(vs + 1_000_000_000)},
            "fee_basis_points": 125, "protocol_fee_basis_points": 95,
            "creator_fee_basis_points": 30, "native_economics_state_complete": True,
            "observation_id": f"obs-{mint}-{ts}"}


def test_outcome_blind_builder_and_causal_materializer(tmp_path: Path, monkeypatch) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join(json.dumps(event("mint-a", 1_000 + i * 1_000, "BUY", 30_000_000_000)) for i in range(2)) + "\n", encoding="utf-8")
    fingerprint = tmp_path / "fingerprint.json"
    monkeypatch.setattr("sys.argv", ["build", "--events", str(events), "--output", str(fingerprint), "--wallet", "2cq"])
    build_main()
    doc = json.loads(fingerprint.read_text(encoding="utf-8"))
    assert doc["outcome_blind"] is True
    assert doc["policy_frozen"] is False
    assert doc["evidence"]["economic_buy_rows"] == 2

    source = tmp_path / "native.jsonl"
    rows = [native("market-a", 10_000, "buy", 30_000_000_000),
            native("market-a", 10_500, "buy", 30_100_000_000),
            native("market-a", 11_100, "sell", 30_200_000_000),
            native("market-a", 11_500, "buy", 30_300_000_000)]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    output = tmp_path / "windows.jsonl"
    report = materialize(source, fingerprint, output)
    assert report["matched_signals"] == 1
    assert report["windows_written"] == 1
    window = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert window["lineage"]["entry_delay_ms"] == 1_000
    assert window["lineage"]["teacher_label_used_for_admission"] is False
    assert window["native_economics_state_complete"] is True
    assert window["fee_basis_points"] == 125
    assert window["protocol_fee_basis_points"] == 95
    assert window["creator_fee_basis_points"] == 30
    assert window["observation_as_of_ts_ms"] == 11_500
