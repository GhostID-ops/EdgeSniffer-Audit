#!/usr/bin/env python3
"""Publish an atomic pointer manifest for the active 2CQ research namespace.

The manifest is deliberately a pointer/index, not a replacement for any raw or
sealed artifact.  It prevents dashboards and reports from accidentally using
the older v1 snapshot files when the active collectors are writing v3-v8.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def identity(path: Path, *, hash_limit_bytes: int = 20 * 1024 * 1024) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    result: dict[str, Any] = {
        "path": str(path),
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime_utc": utc_mtime(path),
    }
    if stat.st_size <= hash_limit_bytes:
        result["sha256"] = sha256_file(path)
    else:
        result["sha256"] = None
        result["hash_deferred"] = True
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def publish(args: argparse.Namespace) -> dict[str, Any]:
    files = {
        "raw_source": args.raw,
        "canonical_events": args.events,
        "canonical_census": args.census,
        "venue_readiness": args.venue_report,
        "pump_curve_windows": args.pump_curve_windows,
        "pump_curve_surface": args.pump_curve_surface,
        "pump_curve_controls": args.pump_curve_controls,
        "pumpswap_windows": args.pumpswap_windows,
        "pumpswap_surface": args.pumpswap_surface,
        "pumpswap_controls": args.pumpswap_controls,
        "raydium_windows": args.raydium_windows,
        "raydium_surface": args.raydium_surface,
        "raydium_controls": args.raydium_controls,
        "meteora_surface": args.meteora_surface,
        "prospective_windows": args.prospective_windows,
        "prospective_controls": args.prospective_controls,
        "prospective_evaluation": args.prospective_evaluation,
        "prospective_policy": args.prospective_policy,
        "prospective_protocol": args.prospective_protocol,
    }
    census = load_json(args.census)
    venue_report = load_json(args.venue_report)
    prospective = load_json(args.prospective_evaluation)
    state_paths = {
        "pool_windows_state": args.pool_state,
        "bounded_controls_state": args.controls_state,
        "pump_curve_windows_state": args.pump_curve_state,
        "prospective_windows_state": args.prospective_state,
        "raydium_windows_state": args.raydium_state,
    }
    state = {key: load_json(path) for key, path in state_paths.items()}

    manifest = {
        "schema": "WALLET_LIVE_RESEARCH_NAMESPACE_V1",
        "namespace_id": "2cq-new-wallet-route-census-live-v1",
        "published_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "wallet": args.wallet,
        "read_only": True,
        "trading_enabled": False,
        "strategy_selection_performed": False,
        "authority": {
            "active_artifacts_are_versioned": True,
            "legacy_snapshot_files_are_not_authoritative": [
                "canonical-route-census.json",
                "canonical-route-events.jsonl",
                "route-census.json",
                "route-events.jsonl",
            ],
            "raw_evidence_preserved": True,
        },
        "files": {key: identity(path) for key, path in files.items()},
        "watermarks": {
            key: {
                "cohort_id": value.get("cohort_id"),
                "boundary_utc": value.get("boundary_utc"),
                "updated_at_utc": value.get("updated_at_utc"),
                "anchors": value.get("anchors"),
                "market_transactions": value.get("market_transactions"),
                "controls": value.get("controls"),
                "errors": value.get("errors"),
                "source_byte_offset": value.get("source_byte_offset"),
            }
            for key, value in state.items()
        },
        "route_summary": {
            "raw_transactions": census.get("raw_transactions"),
            "generated_at_utc": census.get("generated_at_utc"),
            "routes": census.get("routes", []),
            "decoder_failures": census.get("decoder_failures", {}),
        },
        "prospective_gate": {
            "policy_id": prospective.get("policy_id"),
            "policy_sha256": prospective.get("policy_sha256"),
            "windows_loaded": prospective.get("windows_loaded"),
            "wallet": prospective.get("wallet"),
            "controls": prospective.get("controls"),
            "promotion_gate": prospective.get("promotion_gate"),
            "promotion_allowed": prospective.get("promotion_allowed", False),
        },
        "venue_status": venue_report.get("venues", {}),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=args.out.name + ".", dir=args.out.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, args.out)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wallet", required=True)
    for name in (
        "raw", "events", "census", "venue-report", "pump-curve-windows", "pump-curve-surface",
        "pump-curve-controls", "pumpswap-windows", "pumpswap-surface", "pumpswap-controls",
        "raydium-windows", "raydium-surface", "raydium-controls", "meteora-surface",
        "prospective-windows", "prospective-controls", "prospective-evaluation", "prospective-policy",
        "prospective-protocol", "pool-state", "controls-state", "pump-curve-state", "prospective-state",
        "raydium-state",
    ):
        parser.add_argument("--" + name, dest=name.replace("-", "_"), type=Path, required=True)
    args = parser.parse_args()
    result = publish(args)
    print(json.dumps({
        "schema": result["schema"],
        "namespace_id": result["namespace_id"],
        "published_at_utc": result["published_at_utc"],
        "raw_transactions": result["route_summary"]["raw_transactions"],
        "prospective_gate": result["prospective_gate"]["promotion_gate"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
