#!/usr/bin/env python3
"""Refresh venue-separated read-only census, controls, and economics artifacts."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def refresh(args: argparse.Namespace) -> None:
    root = Path(args.root)
    py = sys.executable
    run([py, str(root / "scripts/materializeWalletRouteCensusV1.py"), "--raw", str(args.raw), "--out", str(args.census), "--events-out", str(args.events)])
    run([py, str(root / "scripts/evaluateWalletPumpCurveCausalEconomicsV1.py"), "--windows", str(args.pump_curve_windows), "--output", str(args.pump_curve_surface)])
    run([py, str(root / "scripts/evaluateWalletCausalEconomicsV1.py"), "--anchors", str(args.raw), "--windows", str(args.pumpswap_windows), "--wallet", args.wallet, "--output", str(args.pumpswap_surface)])
    run([py, str(root / "scripts/evaluateWalletRaydiumClmmCausalEconomicsV1.py"), "--windows", str(args.raydium_windows), "--output", str(args.raydium_surface)])
    run([py, str(root / "scripts/materializeWalletRaydiumControlsV1.py"), "--windows", str(args.raydium_windows), "--control-seconds", "5", "--out", str(args.raydium_controls)])
    run([py, str(root / "scripts/evaluateWalletMeteoraDlmmCausalEconomicsV1.py"), "--source", str(args.raw), "--output", str(args.meteora_surface)])
    run([py, str(root / "scripts/materializePumpCurveControlsV1.py"), "--windows", str(args.pump_curve_windows), "--control-seconds", "5", "--out", str(args.pump_curve_controls)])
    run([py, str(root / "scripts/reportWalletVenueEconomicsReadinessV1.py"), "--census", str(args.census), "--pump-curve-surface", str(args.pump_curve_surface), "--pump-curve-controls", str(args.pump_curve_controls), "--pumpswap-surface", str(args.pumpswap_surface), "--pumpswap-controls", str(args.pumpswap_controls), "--pumpswap-repair", str(args.pumpswap_repair), "--raydium-surface", str(args.raydium_surface), "--raydium-controls", str(args.raydium_controls), "--meteora-surface", str(args.meteora_surface), "--out", str(args.report)])
    run([
        py, str(root / "scripts/publishWalletLiveNamespaceV1.py"),
        "--out", str(args.live_manifest), "--wallet", args.wallet,
        "--raw", str(args.raw), "--events", str(args.events), "--census", str(args.census),
        "--venue-report", str(args.report),
        "--pump-curve-windows", str(args.pump_curve_windows), "--pump-curve-surface", str(args.pump_curve_surface),
        "--pump-curve-controls", str(args.pump_curve_controls), "--pumpswap-windows", str(args.pumpswap_windows),
        "--pumpswap-surface", str(args.pumpswap_surface), "--pumpswap-controls", str(args.pumpswap_controls),
        "--raydium-windows", str(args.raydium_windows), "--raydium-surface", str(args.raydium_surface),
        "--raydium-controls", str(args.raydium_controls), "--meteora-surface", str(args.meteora_surface),
        "--prospective-windows", str(args.prospective_windows), "--prospective-controls", str(args.prospective_controls),
        "--prospective-evaluation", str(args.prospective_evaluation), "--prospective-policy", str(args.prospective_policy),
        "--prospective-protocol", str(args.prospective_protocol), "--pool-state", str(args.pool_state),
        "--controls-state", str(args.controls_state), "--pump-curve-state", str(args.pump_curve_state),
        "--prospective-state", str(args.prospective_state), "--raydium-state", str(args.raydium_state),
    ])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--pump-curve-windows", type=Path, required=True)
    parser.add_argument("--pump-curve-surface", type=Path, required=True)
    parser.add_argument("--pump-curve-controls", type=Path, required=True)
    parser.add_argument("--pumpswap-windows", type=Path, required=True)
    parser.add_argument("--pumpswap-surface", type=Path, required=True)
    parser.add_argument("--pumpswap-controls", type=Path, required=True)
    parser.add_argument("--pumpswap-repair", type=Path, required=True)
    parser.add_argument("--raydium-windows", type=Path, required=True)
    parser.add_argument("--raydium-surface", type=Path, required=True)
    parser.add_argument("--raydium-controls", type=Path, required=True)
    parser.add_argument("--meteora-surface", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--live-manifest", type=Path, required=True)
    parser.add_argument("--prospective-windows", type=Path, required=True)
    parser.add_argument("--prospective-controls", type=Path, required=True)
    parser.add_argument("--prospective-evaluation", type=Path, required=True)
    parser.add_argument("--prospective-policy", type=Path, required=True)
    parser.add_argument("--prospective-protocol", type=Path, required=True)
    parser.add_argument("--pool-state", type=Path, required=True)
    parser.add_argument("--controls-state", type=Path, required=True)
    parser.add_argument("--pump-curve-state", type=Path, required=True)
    parser.add_argument("--prospective-state", type=Path, required=True)
    parser.add_argument("--raydium-state", type=Path, required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--interval-seconds", type=int, default=300)
    args = parser.parse_args()
    while True:
        started = time.monotonic()
        try:
            refresh(args)
            print("VENUE_ECONOMICS_REFRESH_OK", flush=True)
        except Exception as error:
            print(f"VENUE_ECONOMICS_REFRESH_ERROR:{type(error).__name__}:{error}", flush=True)
        if args.interval_seconds <= 0:
            return
        time.sleep(max(1, args.interval_seconds - int(time.monotonic() - started)))


if __name__ == "__main__":
    main()
