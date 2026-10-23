#!/usr/bin/env python3
"""Materialize exact Pump launch lineage for the preserved native scorer.

The native trade stream contains the causal trade state but not necessarily the
CREATE transaction.  This bridge joins an exact CREATE registry to an optional
CREATE decoder output.  It never treats first observation as launch, and it
never invents creator or launch reserves.  A mint is emitted only when all
required fields are independently present and the create clock precedes the
first native observation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from runIndependentStrategyLabV1 import state as parse_state
from materialize2CQEventDrivenIndependentWindowsV1 import timestamp


def sha256_file(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def load_registry(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT mint, signature, slot, realtime_receipt_ns, canonical_order_status, "
            "realtime_status, decoder_status, canonical_evidence_hash FROM create_registry"
        ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            result[str(row["mint"])] = dict(row)
        return result
    finally:
        connection.close()


def state_dict(value: Any) -> dict[str, int] | None:
    parsed = parse_state(value)
    if parsed is None:
        return None
    return {
        "virtual_sol": int(parsed.virtual_sol), "virtual_token": int(parsed.virtual_token),
        "real_sol": int(parsed.real_sol), "real_token": int(parsed.real_token),
    }


def candidate_metadata(mint: str, registry: dict[str, dict[str, Any]], evidence: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    base = dict(registry.get(mint) or {})
    supplied = dict(evidence.get(mint) or {})
    reasons: list[str] = []
    create_ms = supplied.get("create_receive_ts_ms")
    if create_ms is None and supplied.get("create_receive_ts_ns") is not None:
        create_ms = int(supplied["create_receive_ts_ns"]) // 1_000_000
    if create_ms is None and base.get("realtime_receipt_ns") is not None:
        create_ms = int(base["realtime_receipt_ns"]) // 1_000_000
    if create_ms is None:
        reasons.append("missing_exact_create_receive_clock")
    else:
        supplied["create_receive_ts_ms"] = int(create_ms)
    creator = supplied.get("creator")
    if not isinstance(creator, str) or not creator:
        reasons.append("missing_creator_identity")
    initial = state_dict(supplied.get("initial_state"))
    if initial is None:
        reasons.append("missing_exact_launch_reserve_state")
    else:
        supplied["initial_state"] = initial
    if base and base.get("canonical_order_status") not in {None, "VALID"}:
        reasons.append("create_registry_canonical_order_not_valid")
    if supplied.get("provenance_status") not in {None, "EXACT_CREATE_DECODER"}:
        reasons.append("create_evidence_not_exact_decoder")
    supplied["registry"] = base
    supplied["creator"] = creator
    return supplied, reasons


def materialize(input_path: Path, registry_path: Path | None, evidence_path: Path | None,
                output_path: Path, report_path: Path) -> dict[str, Any]:
    source_rows = read_jsonl(input_path)
    registry = load_registry(registry_path)
    evidence_rows = read_jsonl(evidence_path)
    evidence = {str(row.get("mint")): row for row in evidence_rows if row.get("mint")}
    first_seen: dict[str, int] = {}
    for row in source_rows:
        mint = str(row.get("mint") or "")
        observed = timestamp(row)
        if mint and observed is not None:
            first_seen[mint] = min(first_seen.get(mint, observed), observed)
    output: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    complete = 0
    for mint in sorted(first_seen):
        metadata, missing = candidate_metadata(mint, registry, evidence)
        if missing:
            reasons.update(missing)
            continue
        if metadata["create_receive_ts_ms"] > first_seen[mint]:
            reasons["create_after_first_native_observation"] += 1
            continue
        output.append({
            "schema": "EDGESNIFFER_2CQ_NATIVE_LAUNCH_LINEAGE_V1",
            "mint": mint,
            "create_receive_ts_ms": metadata["create_receive_ts_ms"],
            "creator": metadata["creator"],
            "initial_state": metadata["initial_state"],
            "first_native_observation_ts_ms": first_seen[mint],
            "create_signature": (metadata.get("registry") or {}).get("signature") or metadata.get("create_signature"),
            "create_slot": (metadata.get("registry") or {}).get("slot") or metadata.get("create_slot"),
            "lineage": {
                "create_clock_source": "exact_create_registry_or_decoder",
                "creator_source": "exact_create_decoder",
                "initial_state_source": "exact_create_decoder",
                "first_observation_is_not_launch": True,
                "provenance_status": "EXACT_CREATE_DECODER",
            },
        })
        complete += 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in output:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    report = {
        "schema": "EDGESNIFFER_2CQ_NATIVE_LAUNCH_LINEAGE_MATERIALIZER_V1",
        "source_rows": len(source_rows), "source_mints": len(first_seen),
        "registry_mints": len(registry), "create_evidence_rows": len(evidence_rows),
        "complete_lineage_mints": complete, "missing_reason_counts": dict(reasons),
        "source_sha256": sha256_file(input_path), "registry_sha256": sha256_file(registry_path),
        "create_evidence_sha256": sha256_file(evidence_path),
        "output": str(output_path), "read_only": True, "trading_enabled": False,
        "proxy_substitution": False, "promotion_blocked": True,
        "notes": [
            "The registry supplies only the exact create receive clock; creator and launch reserves require exact decoder evidence.",
            "No first native observation is used as a launch substitute.",
            "This artifact is an enrichment input to the frozen scorer adapter, not a strategy result.",
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--create-registry", type=Path)
    parser.add_argument("--create-evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(args.input, args.create_registry, args.create_evidence, args.output, args.report), sort_keys=True))


if __name__ == "__main__":
    main()
