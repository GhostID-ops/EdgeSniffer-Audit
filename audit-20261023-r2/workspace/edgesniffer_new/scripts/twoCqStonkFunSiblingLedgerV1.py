#!/usr/bin/env python3
"""Persist the existing StonkFun-marked symmetric choices as a sibling ledger.

This is outcome-blind and deliberately cannot modify the official SOL-Pump
cohort. The LaunchLab choice engine remains the authority for eligibility.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
from typing import Any


SCHEMA = "2CQ_STONKFUN_SIBLING_LEDGER_V1"
LANE = "STONKFUN_LAUNCHLAB_ACTIVE_5000MS_SAME_SPEND_ASSET"


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    overlay_signatures: set[str] = set()
    with args.overlay_rows.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                overlay_signatures.add(str(json.loads(line)["signature"]))

    route = sqlite3.connect(f"file:{args.route_state}?mode=ro", uri=True)
    route.row_factory = sqlite3.Row
    rows = list(route.execute(
        "SELECT signature,mint,spent_mint,boundary_ns,slot,tx_index,choice_json,source_generation "
        "FROM decisions WHERE lane=? AND status='ELIGIBLE' ORDER BY slot,tx_index,signature",
        ("LAUNCHLAB_ACTIVE_5000MS_SAME_SPEND_ASSET",),
    ))
    route.close()

    selected = [row for row in rows if str(row["signature"]) in overlay_signatures]
    emitted: list[dict[str, Any]] = []
    for row in selected:
        choice = json.loads(str(row["choice_json"]))
        emitted.append({
            "schema": SCHEMA + "_ROW",
            "lane": LANE,
            "signature": str(row["signature"]),
            "mint": str(row["mint"]),
            "spentMint": str(row["spent_mint"]),
            "boundaryUnixNs": int(row["boundary_ns"]),
            "slot": int(row["slot"]),
            "transactionIndex": int(row["tx_index"]),
            "choice": choice,
            "sourceGeneration": str(row["source_generation"]),
            "outcomesRead": 0,
            "validationRowsRead": 0,
            "submissions": 0,
        })

    output_rows = args.output / "sealed-candidates.jsonl"
    output_rows.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_rows.with_suffix(output_rows.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for item in emitted:
            stream.write(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, output_rows)

    report = {
        "schema": SCHEMA + "_STATUS",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "sourceLaunchLabEligibleN": len(rows),
        "stonkfunMarkedSelectedN": len(selected),
        "stonkfunUniqueMintN": len({str(row["mint"]) for row in selected}),
        "outputRowsN": len(emitted),
        "lane": LANE,
        "officialSolPumpCohortModified": False,
        "outcomeBlind": True,
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "submissions": 0,
        "sourceRouteStateSha256": sha256_file(args.route_state),
        "sourceOverlayRowsSha256": sha256_file(args.overlay_rows),
        "outputRowsSha256": sha256_file(output_rows),
    }
    atomic_json(args.output / "status.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route-state", type=pathlib.Path, required=True)
    parser.add_argument("--overlay-rows", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
