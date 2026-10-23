#!/usr/bin/env python3
"""Seal a complete Pump-curve discovery snapshot without changing live files."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--surface", type=Path, required=True)
    parser.add_argument("--controls", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    census = json.loads(args.census.read_text(encoding="utf-8"))
    state = json.loads(args.state.read_text(encoding="utf-8"))
    surface = json.loads(args.surface.read_text(encoding="utf-8"))
    report = json.loads(args.report.read_text(encoding="utf-8"))
    window_rows = read_jsonl(args.windows)
    anchor_ids = sorted({str(row.get("anchor_id")) for row in window_rows if row.get("record_type") == "ANCHOR_WINDOW"})
    market_count = sum(1 for row in window_rows if row.get("record_type") == "MARKET_TRANSACTION")
    controls = json.loads(args.controls.read_text(encoding="utf-8"))
    buy_count = sum(int(row.get("genuine_buys") or 0) for row in census.get("routes") or [] if row.get("route") in {"PUMP_BONDING_CURVE_TOP_LEVEL", "PUMP_BONDING_CURVE_CPI"})
    if int(state.get("errors") or 0) != 0:
        raise SystemExit("FAIL_CLOSED: collector errors present")
    if len(anchor_ids) != buy_count:
        raise SystemExit(f"FAIL_CLOSED: windows/buy mismatch {len(anchor_ids)}/{buy_count}")
    if int(surface.get("windows_loaded") or 0) != buy_count:
        raise SystemExit(f"FAIL_CLOSED: surface/windows mismatch {surface.get('windows_loaded')}/{buy_count}")
    if report.get("venues", {}).get("PUMP_BONDING_CURVE", {}).get("status") != "CAUSAL_SURFACE_DISCOVERY_COMPLETE_NO_POLICY_FROZEN":
        raise SystemExit("FAIL_CLOSED: readiness report is not complete")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = args.out / f"pump-curve-discovery-{stamp}"
    destination.mkdir(parents=True, exist_ok=False)
    inputs = {"census": args.census, "windows": args.windows, "state": args.state, "surface": args.surface, "controls": args.controls, "report": args.report}
    manifest_inputs = {}
    for name, source in inputs.items():
        target = destination / source.name
        shutil.copy2(source, target)
        manifest_inputs[name] = {"file": target.name, "sha256": sha256(target), "bytes": target.stat().st_size}
    manifest = {
        "schema": "WALLET_PUMP_CURVE_DISCOVERY_SEALED_V1",
        "sealed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "read_only": True, "policy_frozen": False, "trading_enabled": False,
        "buy_count": buy_count, "window_count": len(anchor_ids), "market_transaction_count": market_count,
        "control_count": int(controls.get("controls") or 0), "surface_windows_loaded": int(surface.get("windows_loaded") or 0),
        "cohort_id": state.get("cohort_id"), "boundary_utc": state.get("boundary_utc"),
        "inputs": manifest_inputs,
    }
    manifest["manifest_sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (destination / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PUMP_CURVE_DISCOVERY_SEALED", "destination": str(destination), "buy_count": buy_count, "window_count": len(anchor_ids), "manifest_sha256": manifest["manifest_sha256"]}, sort_keys=True))


if __name__ == "__main__":
    main()
