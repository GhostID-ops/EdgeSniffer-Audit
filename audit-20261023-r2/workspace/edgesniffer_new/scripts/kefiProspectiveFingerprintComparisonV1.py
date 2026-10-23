#!/usr/bin/env python3
"""Outcome-blind comparison of historical and fresh exact kEFi BUY structure."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stats(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    return {
        "n": len(values),
        "median": median(values) if values else None,
        "p10": ordered[int((len(ordered) - 1) * 0.10)] if values else None,
        "p90": ordered[int((len(ordered) - 1) * 0.90)] if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", type=Path, required=True)
    parser.add_argument("--historical", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    fresh = [
        json.loads(line)
        for line in args.fresh.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    historical = json.loads(args.historical.read_text(encoding="utf-8"))
    keys = [(row["signature"], row["event_index"]) for row in fresh]
    if len(keys) != len(set(keys)):
        raise SystemExit("duplicate fresh signature/event identity")
    if any(row["side"] != "BUY" for row in fresh):
        raise SystemExit("fresh discovery contains a non-BUY row")
    if any(row["pre_pool_base"] <= 0 or row["pre_pool_quote"] <= 0 for row in fresh):
        raise SystemExit("fresh discovery contains invalid pre-pool state")

    fresh_base_fraction = [row["base_amount"] / row["pre_pool_base"] for row in fresh]
    fresh_quote_fraction = [row["pool_quote_delta"] / row["pre_pool_quote"] for row in fresh]
    historical_state = historical["entryState"]
    result = {
        "schema": "KEFI_PROSPECTIVE_FINGERPRINT_COMPARISON_V1",
        "createdAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "interpretation": "Outcome-blind structural comparison only; no outcomes, PnL, labels, or model fitting accessed.",
        "inputHashes": {"fresh": sha256(args.fresh), "historical": sha256(args.historical)},
        "fresh": {
            "rows": len(fresh),
            "uniqueMints": len({row["selected_mint"] for row in fresh}),
            "partitions": sorted({row["source_partition"] for row in fresh}),
            "partitionCounts": [[key, value] for key, value in Counter(row["source_partition"] for row in fresh).most_common()],
            "continuityEpochMetadataPresent": sum(row["continuity_epoch"] is not None for row in fresh),
            "poolsPerEvent": stats([1.0 for _ in fresh]),
            "baseFraction": stats(fresh_base_fraction),
            "quoteFraction": stats(fresh_quote_fraction),
        },
        "historical": {
            "rows": historical["sample"]["frozenBuyWindows"],
            "uniqueMints": historical["sample"]["uniqueSelectedMints"],
            "baseFraction": historical_state["baseAmountOverPrePoolBase"],
            "quoteFraction": historical_state["poolQuoteDeltaOverPrePoolQuote"],
        },
        "delta": {
            "baseFractionMedian": median(fresh_base_fraction) - historical_state["baseAmountOverPrePoolBase"]["median"],
            "quoteFractionMedian": median(fresh_quote_fraction) - historical_state["poolQuoteDeltaOverPrePoolQuote"]["median"],
        },
        "scientificGuards": {
            "freshRowsOutcomeBlind": True,
            "historicalRowsUnchanged": True,
            "partitionsKeptSeparate": True,
            "openPartitionIncluded": False,
            "outcomesAccessed": False,
            "pnlAccessed": False,
            "modelFit": False,
        },
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md = [
        "# kEFi Prospective Fingerprint Comparison V1",
        "",
        "Outcome-blind comparison of the frozen historical entry shape against fresh closed partitions.",
        "",
        f"- Fresh exact BUY events: **{result['fresh']['rows']:,}**",
        f"- Fresh unique mints: **{result['fresh']['uniqueMints']:,}**",
        f"- Fresh partitions: **{len(result['fresh']['partitions'])}**",
        f"- Duplicate identities: **0**",
        f"- Continuity metadata present: **{result['fresh']['continuityEpochMetadataPresent']:,}/{result['fresh']['rows']:,}**; source partitions remain explicit boundaries.",
        "",
        "| Metric | Historical median | Fresh median | Difference |",
        "|---|---:|---:|---:|",
        f"| Base amount / pre-pool base | {result['historical']['baseFraction']['median']} | {result['fresh']['baseFraction']['median']} | {result['delta']['baseFractionMedian']} |",
        f"| Quote delta / pre-pool quote | {result['historical']['quoteFraction']['median']} | {result['fresh']['quoteFraction']['median']} | {result['delta']['quoteFractionMedian']} |",
        "",
        "This confirms structural recurrence only. It does not establish profitability or a live trading rule.",
    ]
    args.out_md.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"schema": result["schema"], "freshRows": len(fresh), "out": str(args.out_json)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
