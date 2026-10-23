#!/usr/bin/env python3
import json
import tempfile
from pathlib import Path

from auditCanonicalNativeParityGateV2 import audit


def row(signature: str, exact: bool = True) -> dict:
    pre = {"virtual_sol": 10, "virtual_token": 20}
    post = {"virtual_sol": 11, "virtual_token": 19}
    if not exact:
        post = {"virtual_sol": 21, "virtual_token": 19}
    return {
        "signature": signature,
        "slot": 1,
        "canonical_state": {"pre_state": pre, "post_state": post},
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        native = root / "native.jsonl"
        finalized = root / "finalized.jsonl"
        joins = root / "joins.jsonl"
        native.write_text("".join(json.dumps(row(f"s{i}")) + "\n" for i in range(20)), encoding="utf-8")
        finalized.write_text("".join(json.dumps(row(f"s{i}")) + "\n" for i in range(20)), encoding="utf-8")
        joins.write_text("".join(json.dumps({
            "signature": f"s{i}",
            "slot": 1,
            "nativeReceiveSlot": 1,
            "nativeReceiveUnixNs": i + 1,
            "nativeReceiveMonotonicNs": i + 2,
            "nativeReceiveSource": "fixture",
        }) + "\n" for i in range(20)), encoding="utf-8")
        result = audit(native, finalized, joins, finalized)
        assert result["gate_status"] == "PASS_NATIVE_RECEIPT_AND_STATE_PARITY"
        assert result["economics_input_allowed"] is True

        finalized.write_text("".join(json.dumps(row(f"s{i}", exact=i != 0)) + "\n" for i in range(20)), encoding="utf-8")
        result = audit(native, finalized, joins, finalized)
        assert result["gate_status"] == "BLOCKED_NATIVE_STATE_OR_RECEIPT_PARITY"
        assert result["native_state_gate"]["mismatch_rows"] == 1
    print("ok")


if __name__ == "__main__":
    main()
