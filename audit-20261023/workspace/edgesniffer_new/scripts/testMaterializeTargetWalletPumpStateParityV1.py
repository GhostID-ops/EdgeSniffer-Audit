#!/usr/bin/env python3
import json
import tempfile
from pathlib import Path

from materializeTargetWalletPumpStateParityV1 import materialize


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source.jsonl"
        joins = root / "joins.jsonl"
        output = root / "out.jsonl"
        report = root / "report.json"
        source.write_text(json.dumps({"schema": "KEFI_13_ARM_FINALIZED_RPC_RAW_TX_V1", "signature": "s", "finalized": True, "tracked_wallet": "w", "transaction": {"meta": {"logMessages": []}}}) + "\n", encoding="utf-8")
        joins.write_text(json.dumps({"signature": "s", "nativeReceiveUnixNs": 1, "nativeReceiveMonotonicNs": 2, "nativeReceiveSlot": 3, "nativeReceiveSource": "fixture"}) + "\n", encoding="utf-8")
        result = materialize(source, joins, output, report)
        assert result["materialized_exact_wallet_events"] == 0
        assert result["native_state_rows"] == 0
        assert result["economics_input_allowed"] is False
    print("ok")


if __name__ == "__main__":
    main()
