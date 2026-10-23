#!/usr/bin/env python3
import json
import tempfile
from pathlib import Path

from auditIndependentEconomicsAdmissionGateV1 import audit


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        report_path = root / "report.json"
        parity_path = root / "parity.json"
        report = {
            "schema": "EDGESNIFFER_INDEPENDENT_STRATEGY_LAB_V1",
            "read_only": True,
            "trading_enabled": False,
            "policy_frozen": False,
            "selection_performed": False,
            "promotion_blocked": True,
            "entry_delay_ms": 1000,
            "notionals_sol": [0.1, 0.5, 1.0],
            "admitted_windows": 20,
            "control_windows_loaded": 20,
            "outcomes": [],
            "surface": [
                {"strategy_id": f"ALL|FIXED_5S|{notional}|stress0", "metrics": {"pnl_sol": 1.0, "profit_factor": 2.0}}
                for notional in ("0.1", "0.5", "1")
            ],
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")
        parity_path.write_text(json.dumps({"gate_status": "PASS_NATIVE_RECEIPT_AND_STATE_PARITY", "economics_input_allowed": True}), encoding="utf-8")
        result = audit(report_path, parity_path)
        assert result["discovery_surface_status"] == "PASS_EXPLORATORY_SURFACE"
        assert result["validation_allowed"] is True

        report["control_windows_loaded"] = 0
        report_path.write_text(json.dumps(report), encoding="utf-8")
        result = audit(report_path, parity_path)
        assert result["validation_allowed"] is False
        assert result["validation_status"] == "BLOCKED_CONTROLS_OR_NATIVE_PARITY"
    print("ok")


if __name__ == "__main__":
    main()
