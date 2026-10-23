#!/usr/bin/env python3
"""Validate the pinned Pump instruction matrix against the native decoder source."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def load_matrix(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_constants(path: Path) -> dict[str, list[int]]:
    text = path.read_text(encoding="utf-8")
    result: dict[str, list[int]] = {}
    pattern = re.compile(r"const\s+([A-Z0-9_]+):\s*\[u8;\s*8\]\s*=\s*\[([^\]]+)\]")
    for name, body in pattern.findall(text):
        result[name] = [int(value.strip()) for value in body.split(",") if value.strip()]
    return result


def validate(matrix: dict[str, Any], source_path: Path) -> dict[str, Any]:
    constants = source_constants(source_path)
    missing: list[str] = []
    mismatched: list[str] = []
    for variant in matrix["variants"]:
        name = str(variant["constant"])
        expected = list(variant["discriminator"])
        actual = constants.get(name)
        if actual is None:
            missing.append(name)
        elif actual != expected:
            mismatched.append(name)
    return {
        "schema": "EDGESNIFFER_NATIVE_PUMP_DECODER_MATRIX_VALIDATION_V1",
        "matrix_schema": matrix.get("schema"),
        "source": str(source_path),
        "variant_count": len(matrix["variants"]),
        "missing_constants": missing,
        "mismatched_constants": mismatched,
        "valid": not missing and not mismatched,
        "execution_boundary": matrix["execution_boundary"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate(load_matrix(args.matrix), args.source)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
