#!/usr/bin/env python3
"""Freeze the next independent kEfi pre-entry paper hypothesis before capture."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path


SCHEMA = "KEFI_BASE_PREENTRY_H2_PREREGISTRATION_V1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_immutable(path: Path, value: dict) -> str:
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SystemExit(f"FAIL_CLOSED: pre-registration already exists: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o444)
    return digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fingerprint", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--selection-rule", type=Path, required=True)
    parser.add_argument("--economic-contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    model = json.loads(args.fingerprint.read_text())
    rule = json.loads(args.selection_rule.read_text())
    contract = json.loads(args.economic_contract.read_text())
    if model.get("status") != "FROZEN_FOR_PROSPECTIVE_OUTCOME_BLIND_SHADOW" or not model.get("strict_preentry"):
        raise SystemExit("FAIL_CLOSED: invalid strict pre-entry fingerprint")
    if rule.get("status") != "FROZEN_FOR_FRESH_OUTCOME_BLIND_PAPER_TEST":
        raise SystemExit("FAIL_CLOSED: invalid historical selection-rule artifact")
    if contract.get("schema") != "KEFI_CROWDED_IMPULSE_ECONOMIC_PAPER_CONTRACT_V1":
        raise SystemExit("FAIL_CLOSED: invalid frozen economic contract")
    value = {
        "schema": SCHEMA,
        "status": "FROZEN_FOR_FRESH_OUTCOME_BLIND_PAPER_TEST",
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "hypothesis_id": "KEFI_BASE_PREENTRY_SCORE_H2",
        "hypothesis": "The frozen strict pre-entry score alone identifies a paper-tradeable kEfi entry set; the prior sparse-crowd overlay is not applied.",
        "entry_rule": {"match_field": "base_fingerprint_match", "score": ">= frozen model threshold", "state_timing": "before the candidate event state update", "bucket_seconds": 20},
        "prohibitions": ["no model refit", "no threshold tuning", "no outcome-dependent filtering", "no PnL access before one-shot gate", "no execution"],
        "fresh_gate": {"minimum_closed_source_partitions": 3, "minimum_frozen_matches": 1000, "warmup_seconds": 120, "horizon_seconds": 120, "required_clean_source": True},
        "inputs": {
            "fingerprint_path": str(args.fingerprint), "fingerprint_sha256": sha256(args.fingerprint),
            "adapter_path": str(args.adapter), "adapter_sha256": sha256(args.adapter),
            "selection_rule_path": str(args.selection_rule), "selection_rule_sha256": sha256(args.selection_rule),
            "economic_contract_path": str(args.economic_contract), "economic_contract_sha256": sha256(args.economic_contract),
        },
        "scientific_status": "Post-H1-result successor. It requires a new boundary and fresh evidence; it is not a retest of the H1 one-shot result.",
        "outcomes_accessed": False, "pnl_accessed": False, "execution_enabled": False,
    }
    print(json.dumps({"path": str(args.out), "sha256": write_immutable(args.out, value)}, sort_keys=True))


if __name__ == "__main__":
    main()
