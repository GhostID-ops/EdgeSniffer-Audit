import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("kefi_preentry_feature_contract_v1.py")


def test_contract_classifies_every_frozen_feature_and_rejects_preentry_trigger():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        model = root / "model.json"
        model.write_text(json.dumps({"features": ["log_pre_pool_quote", "amm_fee_bps", "log_pool_quote_delta"]}))
        output = root / "contract.json"
        run = subprocess.run([sys.executable, str(SCRIPT), "--frozen-model", str(model), "--output", str(output)],
                             text=True, capture_output=True)
        assert run.returncode == 0, run.stderr
        contract = json.loads(output.read_text())
        assert contract["pre_entry_features"] == ["log_pre_pool_quote", "amm_fee_bps"]
        assert contract["same_target_transaction_features"] == ["log_pool_quote_delta"]
        assert contract["current_fingerprint_usable_as_pre_entry_trigger"] is False
