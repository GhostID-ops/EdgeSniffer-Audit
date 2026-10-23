#!/usr/bin/env python3
"""Isolated, outcome-blind V3 source-to-lane rollover integration test."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def load_lane(path: Path):
    spec = importlib.util.spec_from_file_location("v3_lane", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(".partial")
    temp.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def pack_frame(lane, metadata: dict, raw: bytes) -> bytes:
    header = json.dumps(metadata, separators=(",", ":")).encode()
    body = header + raw
    return lane.MAGIC + struct.pack("<II", len(header), len(raw)) + body + hashlib.sha256(body).digest()


def capture_samples(lane, source: Path, count: int) -> list[tuple[dict, bytes]]:
    handle, process, seekable = lane.open_source(source, 0)
    values = []
    try:
        for meta, raw, _ in lane.frames(handle, 0, seekable):
            if meta.get("kind") == "transaction" and "V3_PROTOCOL_V2" in meta.get("filters", []):
                values.append((meta, raw))
                if len(values) == count:
                    break
    finally:
        handle.close()
        if process:
            process.wait(timeout=20)
    if len(values) < count:
        raise RuntimeError(f"insufficient retained full-meta samples: {len(values)}")
    return values


def wait_for(path: Path, predicate, seconds: float, message: str) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            if predicate(value):
                return value
        time.sleep(0.05)
    raise RuntimeError(message)


def terminate(process: subprocess.Popen | None) -> None:
    if not process or process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-script", type=Path, required=True)
    parser.add_argument("--normalizer", type=Path, required=True)
    parser.add_argument("--fingerprint", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--finalizer", type=Path, required=True)
    parser.add_argument("--monitor", type=Path, required=True)
    parser.add_argument("--sample-source", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()

    lane = load_lane(args.lane_script)
    samples = capture_samples(lane, args.sample_source, 105)
    work = Path(tempfile.mkdtemp(prefix="v3-fullstack-rollover-"))
    root = work / "source"
    lane_root = root / "lane"
    root.mkdir(parents=True)
    boundary = lane_root / "boundary.json"
    boundary.parent.mkdir()
    boundary_time = time.time() - 121
    boundary.write_text(json.dumps({
        "boundaryUtc": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(boundary_time)) + ".000000000Z",
        "warmupSeconds": 120,
        "sourceContinuityBaseline": {"sourceEpoch": 1, "continuityGaps": 0, "reconnects": 0, "closedThroughUnixNs": 0, "durableOutputSequence": 0},
    }), encoding="utf-8")
    health_path = root / "health.json"
    state = {"sequence": 0, "closed": time.time_ns() + 3_000_000_000, "running": True, "latest_slot": max(int(meta.get("slot") or 0) for meta, _ in samples)}

    def publish_health() -> None:
        atomic_json(health_path, {
            "globalState": "DUAL_LIVE",
            "currentEpoch": 1,
            "continuityGaps": 0,
            "latestSlot": state["latest_slot"],
            "closedThroughUnixNs": state["closed"],
            "durableOutputSequence": state["sequence"],
            "global": {"mode": "DUAL_LIVE", "continuityGaps": 0, "durableOutputSequence": state["sequence"], "closedThroughUnixNs": state["closed"], "sourceFrontier": state["latest_slot"], "lateEarlierViolations": 0, "replayConflicts": 0, "timingProvenanceViolations": 0},
        })

    def frontier_clock() -> None:
        while state["running"]:
            state["closed"] = time.time_ns() + 3_000_000_000
            publish_health()
            time.sleep(0.05)

    def hour_path(index: int) -> Path:
        return root / "partitions/2026/09/30" / f"{index:02d}" / "evidence.frames.partial"

    def write_batch(index: int) -> int:
        target = hour_path(index)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab") as handle:
            for sample_meta, raw in samples[index * 5:(index + 1) * 5]:
                state["sequence"] += 1
                metadata = dict(sample_meta)
                receipt_ns = time.time_ns()
                metadata["writerSequence"] = state["sequence"]
                metadata["receivedAtUnixNs"] = receipt_ns
                metadata["receivedAtUtc"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(receipt_ns / 1e9)) + ".000000000Z"
                handle.write(pack_frame(lane, metadata, raw))
        publish_health()
        return state["sequence"]

    write_batch(0)
    clock = threading.Thread(target=frontier_clock, daemon=True)
    clock.start()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(args.lane_script.parent) + ":/root/bot/EdgeSniffer/scripts"
    lane_process = finalizer_process = monitor_process = None
    try:
        lane_process = subprocess.Popen([
            sys.executable, str(args.lane_script), "--root", str(lane_root), "--normalizer", str(args.normalizer),
            "--fingerprint", str(args.fingerprint), "--env", str(args.env), "--spec", str(args.spec), "--boundary", str(boundary),
        ], env=env)
        first_health = wait_for(lane_root / "health.json", lambda x: x.get("ticks", 0) >= 2, 45, "lane did not begin scheduled ticks")
        finalizer_process = subprocess.Popen([sys.executable, str(args.finalizer), "--root", str(lane_root), "--env", str(args.env), "--boundary", str(boundary)], env=env)
        monitor_process = subprocess.Popen([sys.executable, str(args.monitor), "--root", str(lane_root), "--boundary", str(boundary)], env=env)

        # Twenty short synthetic hours. The active hour is sealed before the
        # next exists for one case, and the next is pre-populated before the
        # reader switches for every other case. Compression remains detached.
        switch_lag_ns = []
        for index in range(20):
            old = hour_path(index)
            os.replace(old, old.with_name("evidence.frames.sealed"))
            if index == 7:
                time.sleep(0.35)  # caught up at sealed EOF before next arrival
            switch_started = time.perf_counter_ns()
            target_sequence = write_batch(index + 1)
            target_partition = str(hour_path(index + 1).parent)
            wait_for(
                lane_root / "health.json",
                lambda x: x.get("sourcePartition") == target_partition and int(x.get("consumedDurableOutputSequence") or 0) >= target_sequence,
                10,
                f"reader did not switch to partition {index + 1}",
            )
            switch_lag_ns.append(time.perf_counter_ns() - switch_started)
            if index == 11:
                time.sleep(2.5)  # detached sealed-hour compression analogue
            time.sleep(0.35)

        time.sleep(8)
        lane_health = json.loads((lane_root / "health.json").read_text(encoding="utf-8"))
        monitor_health = json.loads((lane_root / "operational-monitor-v1/health.json").read_text(encoding="utf-8"))
        db = sqlite3.connect(lane_root / "autonomous-when-v3.sqlite3")
        tick_rows = [row[0] for row in db.execute("SELECT tick_ns FROM cadence_ticks ORDER BY tick_ns")]
        db.close()
        expected_tick_count = ((tick_rows[-1] - tick_rows[0]) // 1_000_000_000 + 1) if tick_rows else 0
        actual_tick_count = len(tick_rows)
        missing_tick_count = expected_tick_count - actual_tick_count
        if lane_process.poll() is not None:
            raise RuntimeError(f"lane exited: {lane_process.returncode}")
        if missing_tick_count != 0 or len(tick_rows) != len(set(tick_rows)):
            raise RuntimeError("cadence regression during forced rollover")
        if lane_health.get("decoderRejections") != 0:
            raise RuntimeError("decoder rejection during forced rollover")
        output = {
            "schema": "V3_FULLSTACK_FORCED_ROLLOVER_TEST_V1",
            "forcedTransitions": 20,
            "laneTicks": lane_health.get("ticks"),
            "expectedTicks": expected_tick_count,
            "actualTicks": actual_tick_count,
            "missingTicks": missing_tick_count,
            "duplicateTicks": actual_tick_count - len(set(tick_rows)),
            "duplicateRankRows": monitor_health.get("duplicateRankRows"),
            "decoderRejections": lane_health.get("decoderRejections"),
            "cardinalityMismatches": monitor_health.get("cardinalityMismatches"),
            "continuityFailures": lane_health.get("continuityGaps"),
            "sourceDurableSequence": state["sequence"],
            "consumedDurableSequence": lane_health.get("consumedDurableOutputSequence"),
            "maxTimerDriftNs": lane_health.get("maxTimerDriftNs"),
            "maxPartitionSwitchLatencyNs": max(switch_lag_ns),
            "p50PartitionSwitchLatencyNs": sorted(switch_lag_ns)[len(switch_lag_ns) // 2],
            "finalizerActive": finalizer_process.poll() is None,
            "monitorActive": monitor_process.poll() is None,
            "status": "PASS",
        }
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(output, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(output, sort_keys=True))
    finally:
        state["running"] = False
        terminate(monitor_process)
        terminate(finalizer_process)
        terminate(lane_process)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
