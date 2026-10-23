import json
from pathlib import Path

import kefi13ArmFinalizedRpcSourceV1 as subject


class FakeRpc:
    def __init__(self):
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "getSignaturesForAddress":
            program_id = params[0]
            return [{"signature": f"sig-{program_id}", "slot": 100, "blockTime": 1792378000, "err": None}]
        signature = params[0]
        program_id = signature.removeprefix("sig-")
        return {
            "slot": 100,
            "meta": {"err": None, "logMessages": []},
            "transaction": {"message": {"accountKeys": ["teacher-wallet"], "instructions": [{"programId": program_id}]}, "signatures": [signature]},
        }


def test_source_manifest_has_separate_canonical_program_watches():
    ids = {row["program_id"] for row in subject.WATCHES}
    assert len(ids) == len(subject.WATCHES)
    assert any(row["family"] == "PUMPSWAP_PROGRAM" for row in subject.WATCHES)
    assert any(row["family"] == "ORCA_WHIRLPOOL" for row in subject.WATCHES)
    assert all("ARM13_AMBIGUOUS_PUMP_FAIL_CLOSED" not in row["arm_ids"] for row in subject.WATCHES)


def test_run_once_is_finalized_append_only_and_idempotent(tmp_path: Path):
    state = subject.load_state(tmp_path / "state.json", "cohort", "2026-10-19T02:00:00Z")
    rpc = FakeRpc()
    output = tmp_path / "raw.jsonl"
    result = subject.run_once(rpc, state, output)
    assert result["written"] == 11
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 11
    assert all(row["schema"] == subject.SCHEMA for row in rows)
    assert all(row["finalized"] is True for row in rows)
    assert all("pnl" not in row and "exit" not in row for row in rows)
    second = subject.run_once(rpc, state, output)
    assert second["written"] == 0
    assert len(output.read_text().splitlines()) == 11


def test_tracked_wallet_query_classifies_programs_from_transaction(tmp_path: Path):
    class WalletRpc(FakeRpc):
        def call(self, method, params):
            self.calls.append((method, params))
            if method == "getSignaturesForAddress":
                assert params[0] == "teacher-wallet"
                return [{"signature": "sig-wallet", "slot": 100, "blockTime": 1792378000, "err": None}]
            return {
                "slot": 100,
                "meta": {"err": None, "logMessages": []},
                "transaction": {
                    "message": {
                        "accountKeys": ["teacher-wallet"],
                        "instructions": [{"programId": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"}],
                    },
                    "signatures": ["sig-wallet"],
                },
            }

    state = subject.load_state(tmp_path / "state.json", "cohort", "2026-10-19T02:00:00Z")
    output = tmp_path / "raw.jsonl"
    result = subject.run_once(WalletRpc(), state, output, tracked_wallet="teacher-wallet")
    assert result["written"] == 1
    row = json.loads(output.read_text().splitlines()[0])
    assert row["tracked_wallet"] == "teacher-wallet"
    assert row["observed_program_ids"] == ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"]
    assert row["candidate_arm_ids"] == ["ARM07_PUMPSWAP_DIRECT", "ARM08_PUMPSWAP_CPI"]
