import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("kefi_preentry_feasibility_v1.py")


def test_preentry_feasibility_refuses_nonpreentry_and_emits_chronological_holdout():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        selected, controls = root / "selected.jsonl", root / "controls.jsonl"
        selected.write_text("\n".join(json.dumps({"side":"BUY", "received_at_unix_ns": i,
            "signature":f"s{i}", "pre_pool_quote":1000 + i * 10, "lp_fee_bps":2,
            "protocol_fee_bps":100, "coin_creator_bps":30}) for i in range(1, 11)) + "\n")
        controls.write_text("\n".join(json.dumps({"received_at_unix_ns": i, "candidate_signature":f"c{i}",
            "pre_pool_quote":100 + i, "lp_fee_bps":2, "protocol_fee_bps":90, "coin_creator_bps":20})
            for i in range(1, 81)) + "\n")
        contract = root / "contract.json"
        contract.write_text(json.dumps({"pre_entry_features":["log_pre_pool_quote", "amm_fee_bps"]}))
        output = root / "out.json"
        run = subprocess.run([sys.executable, str(SCRIPT), "--selected", str(selected), "--controls", str(controls),
                              "--preentry-contract", str(contract), "--output", str(output)], text=True, capture_output=True)
        assert run.returncode == 0, run.stderr
        assert json.loads(output.read_text())["chronological_holdout"]["rows"] > 0
