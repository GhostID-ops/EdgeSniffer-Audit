#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import tempfile
from pathlib import Path


PATH = Path(__file__).with_name("kefi_choiceset_snapshot_audit_v1.py")
SPEC = importlib.util.spec_from_file_location("choice_snapshot", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "ledger.jsonl"
        source.write_bytes(b'{"id":1}\n{"id":2}\n')
        expected = hashlib.sha256(b'{"id":1}\n').hexdigest()
        target = root / "frozen.jsonl"
        assert MODULE.freeze_ledger_prefix(source, expected, target) == 1
        assert target.read_bytes() == b'{"id":1}\n'
        try:
            MODULE.freeze_ledger_prefix(source, "0" * 64, root / "bad.jsonl")
        except RuntimeError as error:
            assert "FAIL_CLOSED" in str(error)
        else:
            raise AssertionError("must reject unknown append-only hash")
    print("PASS")


if __name__ == "__main__":
    main()
