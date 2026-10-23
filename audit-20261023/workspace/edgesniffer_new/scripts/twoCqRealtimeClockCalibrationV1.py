#!/usr/bin/env python3
"""Outcome-blind exact-signature calibration of 2CQ realtime receipt clocks."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import statistics
import subprocess
from typing import Any


SCHEMA = "2CQ_REALTIME_CLOCK_CALIBRATION_V1"


def host_clock_domain() -> dict[str, Any]:
    boot_id_path = pathlib.Path("/proc/sys/kernel/random/boot_id")
    boot_id = boot_id_path.read_text(encoding="utf-8").strip() if boot_id_path.exists() else None
    ntp_synchronized = None
    try:
        result = subprocess.run(
            ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        ntp_synchronized = result.stdout.strip().lower() == "yes"
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "hostBootId": boot_id,
        "ntpSynchronized": ntp_synchronized,
        "clock": "CLOCK_REALTIME",
    }


def read_jsonl(path: pathlib.Path):
    with path.open("rb") as stream:
        for raw in stream:
            if not raw.strip():
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def iso_from_ms(value: int) -> str:
    return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def run(args: argparse.Namespace) -> dict[str, Any]:
    allen: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(args.allenhark):
        signature = str(row.get("signature") or "")
        received_ns = int(row.get("receivedAtUnixNs") or 0)
        if signature and received_ns and received_ns // 1_000_000 >= args.start_ms:
            previous = allen.get(signature)
            if previous is None or received_ns < int(previous["receivedAtUnixNs"]):
                allen[signature] = row

    direct: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(args.yellowstone):
        signature = str(row.get("signature") or "")
        detected_ms = int(row.get("detectedAtMs") or 0)
        if signature and detected_ms >= args.start_ms:
            previous = direct.get(signature)
            if previous is None or detected_ms < int(previous["detectedAtMs"]):
                direct[signature] = row

    matched = sorted(set(allen) & set(direct))
    same_slot: list[str] = []
    slot_mismatch: list[str] = []
    deltas: list[float] = []
    rows: list[dict[str, Any]] = []
    for signature in matched:
        a = allen[signature]
        y = direct[signature]
        a_slot = int(a.get("slot") or -1)
        y_slot = int(y.get("sourceSlot") or -2)
        if a_slot != y_slot:
            slot_mismatch.append(signature)
            continue
        same_slot.append(signature)
        a_ms = int(a["receivedAtUnixNs"]) / 1_000_000
        y_ms = int(y["detectedAtMs"])
        delta = y_ms - a_ms
        deltas.append(delta)
        rows.append({
            "signature": signature,
            "slot": a_slot,
            "allenharkReceivedUnixNs": int(a["receivedAtUnixNs"]),
            "yellowstoneDetectedAtMs": y_ms,
            "yellowstoneMinusAllenharkMs": delta,
        })

    def share(predicate) -> float | None:
        return sum(1 for value in deltas if predicate(value)) / len(deltas) if deltas else None

    report = {
        "schema": SCHEMA,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "windowStartMs": args.start_ms,
        "windowStartUtc": iso_from_ms(args.start_ms),
        "semantics": {
            "join": "exact transaction signature",
            "delta": "Yellowstone detectedAtMs minus AllenHark receivedAtUnixNs",
            "allenharkResolution": "nanoseconds as persisted by observer",
            "yellowstoneResolution": "milliseconds as persisted by Rust exact collector",
            "outcomesRead": 0,
        },
        "population": {
            "allenharkSignatures": len(allen),
            "yellowstoneDirectSignatures": len(direct),
            "matchedSignatures": len(matched),
            "sameSlotMatches": len(same_slot),
            "slotMismatches": len(slot_mismatch),
            "yellowstoneOnly": len(set(direct) - set(allen)),
            "allenharkOnly": len(set(allen) - set(direct)),
        },
        "yellowstoneMinusAllenharkMs": {
            "min": min(deltas) if deltas else None,
            "p10": percentile(deltas, .10),
            "p50": percentile(deltas, .50),
            "p75": percentile(deltas, .75),
            "p90": percentile(deltas, .90),
            "p99": percentile(deltas, .99),
            "max": max(deltas) if deltas else None,
            "mean": statistics.fmean(deltas) if deltas else None,
            "allenharkFirstFraction": share(lambda value: value >= 0),
            "yellowstoneFirstFraction": share(lambda value: value < 0),
            "absoluteWithin1msFraction": share(lambda value: abs(value) <= 1),
            "absoluteWithin5msFraction": share(lambda value: abs(value) <= 5),
            "absoluteWithin10msFraction": share(lambda value: abs(value) <= 10),
            "absoluteWithin25msFraction": share(lambda value: abs(value) <= 25),
        },
        "clockDomain": host_clock_domain() | {
            "sameHostSources": True,
            "validExactSignatureAnchors": sum(abs(value) <= 250 for value in deltas),
            "firstValidAnchorMs": min(
                (row["yellowstoneDetectedAtMs"] for row in rows
                 if abs(row["yellowstoneMinusAllenharkMs"]) <= 250),
                default=None,
            ),
            "lastValidAnchorMs": max(
                (row["yellowstoneDetectedAtMs"] for row in rows
                 if abs(row["yellowstoneMinusAllenharkMs"]) <= 250),
                default=None,
            ),
        },
        "slotMismatchSignatures": slot_mismatch,
        "matchedRows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allenhark", type=pathlib.Path, required=True)
    parser.add_argument("--yellowstone", type=pathlib.Path, required=True)
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))
