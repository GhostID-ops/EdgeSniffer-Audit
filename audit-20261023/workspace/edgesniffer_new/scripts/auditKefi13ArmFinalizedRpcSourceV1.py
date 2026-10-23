#!/usr/bin/env python3
"""Audit the dedicated finalized-RPC KEFI source before economics."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


RAW_SCHEMA = "KEFI_13_ARM_FINALIZED_RPC_RAW_TX_V1"
OUTCOME_PARTS = ("pnl", "roi", "profit_factor", "win_rate", "mfe", "mae", "drawdown", "exit", "outcome")


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def audit(raw: list[dict[str, Any]], state: dict[str, Any], manifest: dict[str, Any], registry: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    boundary = parse_utc(str(manifest["boundary_utc"]))
    seen: set[str] = set()
    errors: list[dict[str, Any]] = []
    families: Counter[str] = Counter()
    arms: Counter[str] = Counter()
    latest: dt.datetime | None = None
    pre_boundary_rows = 0
    for index, row in enumerate(raw, 1):
        if row.get("schema") != RAW_SCHEMA:
            errors.append({"line": index, "reason": "RAW_SCHEMA_MISMATCH"})
            continue
        captured = parse_utc(str(row.get("captured_at_utc")))
        if captured < boundary:
            pre_boundary_rows += 1
            continue
        signature = str(row.get("signature", ""))
        if not signature:
            errors.append({"line": index, "reason": "MISSING_SIGNATURE"})
        elif signature in seen:
            errors.append({"line": index, "reason": "DUPLICATE_SIGNATURE"})
        seen.add(signature)
        if row.get("finalized") is not True:
            errors.append({"line": index, "reason": "NOT_FINALIZED"})
        if str(row.get("tracked_wallet")) != str(manifest.get("tracked_wallet")):
            errors.append({"line": index, "reason": "TRACKED_WALLET_MISMATCH"})
        transaction = row.get("transaction")
        if not isinstance(transaction, dict) or sha256(transaction) != row.get("transaction_sha256"):
            errors.append({"line": index, "reason": "TRANSACTION_HASH_MISMATCH"})
        forbidden = sorted({key for key in row if any(part in str(key).casefold() for part in OUTCOME_PARTS)})
        if forbidden:
            errors.append({"line": index, "reason": "OUTCOME_FIELD_PRESENT", "fields": forbidden})
        families.update(str(value) for value in row.get("observed_program_families", []))
        arms.update(str(value) for value in row.get("candidate_arm_ids", []))
        latest = captured if latest is None or captured > latest else latest
    state_errors = int(state.get("errors", 0))
    source_fresh = latest is not None and (now - latest).total_seconds() <= 120
    registry_ids = {row["id"] for row in registry.get("arms", [])}
    result = {
        "schema": "KEFI_13_ARM_FINALIZED_RPC_SOURCE_AUDIT_V1",
        "generated_at_utc": now.isoformat().replace("+00:00", "Z"),
        "cohort_id": manifest.get("cohort_id"),
        "outcome_blind": True,
        "registry_arm_count": len(registry_ids),
        "source": {
            "transport_clean": state_errors == 0,
            "source_fresh": source_fresh,
            "raw_rows": len(raw),
            "window_rows": len(raw) - pre_boundary_rows,
            "pre_boundary_rows_ignored": pre_boundary_rows,
            "unique_signatures": len(seen),
            "state_errors": state_errors,
            "latest_raw_capture_utc": latest.isoformat().replace("+00:00", "Z") if latest else None,
            "observed_program_families": dict(sorted(families.items())),
            "candidate_arm_counts": dict(sorted(arms.items())),
        },
        "errors": errors,
        "coverage": {
            "observed_arm_ids": sorted(arms),
            "unobserved_arm_ids": sorted(registry_ids - set(arms)),
            "all_13_arms_observed": registry_ids.issubset(set(arms)),
        },
        "status": "PASS_RAW_SOURCE_AUDIT" if not errors and state_errors == 0 and source_fresh else "FAIL_RAW_SOURCE_AUDIT",
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--canonical", type=Path)
    parser.add_argument("--tracked-wallet", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest["boundary_utc"] = manifest.get("cohort_started_at_utc")
    manifest["tracked_wallet"] = args.tracked_wallet
    raw = [json.loads(line) for line in args.raw.read_text(encoding="utf-8").splitlines() if line.strip()]
    state = json.loads(args.state.read_text(encoding="utf-8"))
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    now = parse_utc(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    result = audit(raw, state, manifest, registry, now)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "raw_rows": result["source"]["raw_rows"], "all_13_arms_observed": result["coverage"]["all_13_arms_observed"]}, sort_keys=True))
    if result["status"] != "PASS_RAW_SOURCE_AUDIT":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
