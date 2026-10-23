#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("materializeNativeYellowstoneTransactionBridgeV1.py")
SPEC = importlib.util.spec_from_file_location("bridge_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    events = root / "events.jsonl"
    target = root / "target.jsonl"
    receipts = root / "receipts.jsonl"
    output = root / "output.jsonl"
    state = root / "state.json"
    report = root / "report.json"
    events.write_text("", encoding="utf-8")
    target_row = {
        "signature": "sig-1",
        "wallet": "wallet-1",
        "slot": 10,
        "account_keys": ["curve-1", "other"],
        "instructions": [],
    }
    write_jsonl(target, [target_row])
    first = MODULE.run_once(type("Args", (), {
        "events": events, "native_transactions": target, "receipt_output": receipts,
        "output": output, "state": state, "report": report,
        "required_wallet": "wallet-1",
    })())
    assert first["bridge"]["bridged_rows"] == 0
    with events.open("a", encoding="utf-8") as handle:
        receipt = {
            "kind": "rustNativeTargetTransactionReceipt",
            "schema": MODULE.RECEIPT_SCHEMA,
            "signature": "sig-1",
            "bondingCurve": "curve-1",
            "slot": 10,
            "transactionIndex": 2,
            "nativeReceiveUnixNs": 123,
            "nativeReceiveMonotonicNs": 456,
            "nativeReceiveSource": "yellowstone-transaction-update-receive",
            "causalStreamId": "yellowstone-grpc-subscription",
        }
        handle.write(json.dumps(receipt) + "\n")
        handle.write(json.dumps(receipt) + "\n")
    second = MODULE.run_once(type("Args", (), {
        "events": events, "native_transactions": target, "receipt_output": receipts,
        "output": output, "state": state, "report": report,
        "required_wallet": "wallet-1",
    })())
    assert second["bridge"]["bridged_rows"] == 1
    bridged = json.loads(output.read_text(encoding="utf-8").strip())
    assert bridged["same_source_receive_unix_ns"] == 123
    assert bridged["same_source_slot"] == 10
    third = MODULE.run_once(type("Args", (), {
        "events": events, "native_transactions": target, "receipt_output": receipts,
        "output": output, "state": state, "report": report,
        "required_wallet": "wallet-1",
    })())
    assert third["receipt_stream"]["receipt_rows_appended"] == 0
    assert len(receipts.read_text(encoding="utf-8").splitlines()) == 2

print("materializeNativeYellowstoneTransactionBridgeV1 tests passed")
