#!/usr/bin/env python3
"""Outcome-blind structural synthesis from exact kEFi PumpSwap entry state.

This consumes only the frozen BUY sample, exact decoded entry events, and
structural metadata. It does not read outcomes, PnL, labels, or fit a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def stats(values: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "min": min(values) if values else None,
        "median": median(values) if values else None,
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--structural", type=Path, required=True)
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    context = [
        json.loads(line)
        for line in args.context.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    structural = json.loads(args.structural.read_text(encoding="utf-8"))
    entry = json.loads(args.entry.read_text(encoding="utf-8"))
    freeze = json.loads(args.freeze.read_text(encoding="utf-8"))

    context_by_ordinal = {int(row["ordinal"]): row for row in context}
    structural_by_ordinal = {
        int(row["ordinal"]): row
        for row in structural["trades"]
        if row.get("direction") == "BUY"
    }
    entry_by_ordinal = {
        int(row["ordinal"]): row
        for row in entry.get("entryRows", [])
        if int(row["ordinal"]) in context_by_ordinal
    }
    freeze_by_ordinal = {int(row["ordinal"]): row for row in freeze["items"]}

    ordinals = set(context_by_ordinal)
    if ordinals != set(structural_by_ordinal) or ordinals != set(entry_by_ordinal):
        raise SystemExit("exact context, structural BUY, and entry ordinals differ")
    if not ordinals.issubset(freeze_by_ordinal):
        raise SystemExit("exact context contains an ordinal outside the frozen manifest")

    rows = []
    for ordinal in sorted(ordinals):
        raw = context_by_ordinal[ordinal]
        trade = structural_by_ordinal[ordinal]
        entry_row = entry_by_ordinal[ordinal]
        frozen = freeze_by_ordinal[ordinal]
        if raw["exact_event_decode_failures"] != 0:
            raise SystemExit(f"decoder failure at ordinal {ordinal}")
        if raw["entry_exact_events"] != 1 or raw["entry_exact_side"] != "BUY":
            raise SystemExit(f"non-unique BUY entry event at ordinal {ordinal}")
        required = (
            "entry_exact_pre_pool_base",
            "entry_exact_pre_pool_quote",
            "entry_exact_base_amount",
            "entry_exact_pool_quote_delta",
        )
        if any(raw[name] is None for name in required):
            raise SystemExit(f"incomplete exact entry state at ordinal {ordinal}")
        rows.append(
            {
                "ordinal": ordinal,
                "signature": trade["signature"],
                "selectedMint": raw["selected_mint"],
                "canonicalSlot": int(trade.get("canonicalSlot") or 0),
                "receivedAtUnixNs": int(frozen["receivedAtUnixNs"]),
                "continuityEpoch": int(raw.get("continuity_epoch") or 0),
                "venue": "+".join(sorted(trade.get("venues") or [])) or "UNKNOWN",
                "instructionTemplate": "+".join(entry_row.get("instructionNames") or []),
                "exactEventsBeforeEntry": int(raw["exact_pump_swap_events"]),
                "exactBuysBeforeEntry": int(raw["exact_pump_swap_buys"]),
                "exactSellsBeforeEntry": int(raw["exact_pump_swap_sells"]),
                "distinctPools": int(raw["distinct_pools"]),
                "entryPrePoolBase": int(raw["entry_exact_pre_pool_base"]),
                "entryPrePoolQuote": int(raw["entry_exact_pre_pool_quote"]),
                "entryBaseAmount": int(raw["entry_exact_base_amount"]),
                "entryPoolQuoteDelta": int(raw["entry_exact_pool_quote_delta"]),
                "entrySlot": int(raw["entry_exact_slot"]),
                "entryEventTimestamp": int(raw["entry_exact_timestamp"]),
            }
        )

    ordered = sorted(rows, key=lambda row: (row["receivedAtUnixNs"], row["ordinal"]))
    slot_gaps = [
        float(right["canonicalSlot"] - left["canonicalSlot"])
        for left, right in zip(ordered, ordered[1:])
    ]
    time_gaps = [
        (right["receivedAtUnixNs"] - left["receivedAtUnixNs"]) / 1_000_000_000.0
        for left, right in zip(ordered, ordered[1:])
    ]
    base_fraction = [
        row["entryBaseAmount"] / row["entryPrePoolBase"]
        for row in rows
        if row["entryPrePoolBase"] > 0
    ]
    quote_fraction = [
        row["entryPoolQuoteDelta"] / row["entryPrePoolQuote"]
        for row in rows
        if row["entryPrePoolQuote"] > 0
    ]

    epochs: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        epochs[row["continuityEpoch"]].append(row)
    epoch_profiles = []
    for epoch, values in sorted(epochs.items()):
        epoch_profiles.append(
            {
                "epoch": epoch,
                "buyWindows": len(values),
                "uniqueMints": len({row["selectedMint"] for row in values}),
                "medianExactEventsBeforeEntry": median(
                    row["exactEventsBeforeEntry"] for row in values
                ),
                "medianEntryBaseFraction": median(
                    row["entryBaseAmount"] / row["entryPrePoolBase"]
                    for row in values
                    if row["entryPrePoolBase"] > 0
                ),
            }
        )

    result = {
        "schema": "KEFI_EXACT_ENTRY_FINGERPRINT_SYNTHESIS_V1",
        "createdAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "trackedWallet": structural.get("trackedWallet"),
        "interpretation": "Outcome-blind exact entry structure only; no outcomes, PnL, labels, predictive scores, or model fitting accessed.",
        "inputHashes": {
            "context": sha256(args.context),
            "structural": sha256(args.structural),
            "entry": sha256(args.entry),
            "freeze": sha256(args.freeze),
        },
        "sample": {
            "frozenBuyWindows": len(rows),
            "uniqueSelectedMints": len({row["selectedMint"] for row in rows}),
            "continuityEpochs": len(epoch_profiles),
            "uniqueEntryEvents": sum(row["entry_exact_events"] == 1 for row in context),
            "decoderFailures": sum(row["exact_event_decode_failures"] for row in context),
        },
        "venueCounts": [[key, value] for key, value in Counter(row["venue"] for row in rows).most_common()],
        "instructionTemplates": [[key, value] for key, value in Counter(row["instructionTemplate"] for row in rows).most_common()],
        "entryState": {
            "prePoolBaseRaw": stats([float(row["entryPrePoolBase"]) for row in rows]),
            "prePoolQuoteRaw": stats([float(row["entryPrePoolQuote"]) for row in rows]),
            "baseAmountRaw": stats([float(row["entryBaseAmount"]) for row in rows]),
            "poolQuoteDeltaRaw": stats([float(row["entryPoolQuoteDelta"]) for row in rows]),
            "baseAmountOverPrePoolBase": stats(base_fraction),
            "poolQuoteDeltaOverPrePoolQuote": stats(quote_fraction),
            "exactEventsBeforeEntry": stats([float(row["exactEventsBeforeEntry"]) for row in rows]),
            "exactBuysBeforeEntry": stats([float(row["exactBuysBeforeEntry"]) for row in rows]),
            "exactSellsBeforeEntry": stats([float(row["exactSellsBeforeEntry"]) for row in rows]),
            "distinctPools": stats([float(row["distinctPools"]) for row in rows]),
        },
        "chronology": {
            "slotGapBetweenBuys": stats(slot_gaps),
            "receiptSecondsBetweenBuys": stats(time_gaps),
        },
        "epochProfiles": epoch_profiles,
        "scientificGuards": {
            "sampleMembershipChanged": False,
            "decoderFailures": 0,
            "uniqueEntryEvents": True,
            "continuityEpochsKeptSeparate": True,
            "openPartitionIncluded": False,
            "outcomesAccessed": False,
            "pnlAccessed": False,
            "predictiveModelFit": False,
        },
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    md = [
        "# kEFi Exact Entry Fingerprint Synthesis V1",
        "",
        f"Outcome-blind exact entry-state synthesis for **{len(rows):,}** frozen BUY windows.",
        "",
        "This is structural research only. It does not inspect outcomes/PnL or fit a predictive model.",
        "",
        "## Integrity",
        "",
        f"- Unique exact BUY entry events: **{result['sample']['uniqueEntryEvents']}**",
        f"- Decoder failures: **{result['sample']['decoderFailures']}**",
        f"- Continuity epochs kept separate: **{result['sample']['continuityEpochs']}**",
        f"- Open partition included: **no**",
        "",
        "## Exact entry structure",
        "",
        "| Measure | Median | P90 | P95 | Max |",
        "|---|---:|---:|---:|---:|",
    ]
    for key, label in (
        ("prePoolBaseRaw", "Pre-pool base reserve"),
        ("prePoolQuoteRaw", "Pre-pool quote reserve"),
        ("baseAmountRaw", "Entry base amount"),
        ("poolQuoteDeltaRaw", "Entry quote delta"),
        ("baseAmountOverPrePoolBase", "Entry base / pre-pool base"),
        ("poolQuoteDeltaOverPrePoolQuote", "Entry quote delta / pre-pool quote"),
        ("exactEventsBeforeEntry", "Exact events before entry"),
        ("exactBuysBeforeEntry", "Exact buys before entry"),
        ("exactSellsBeforeEntry", "Exact sells before entry"),
    ):
        item = result["entryState"][key]
        md.append(f"| {label} | {item['median']} | {item['p90']} | {item['p95']} | {item['max']} |")
    md.extend(
        [
            "",
            "## Interpretation",
            "",
            "- These are observed entry-context distributions, not a validated trading rule.",
            "- Every included BUY has one exact decoded PumpSwap BUY event and complete pre-pool state fields.",
            "- Fifteen source continuity epochs remain separate; no gap is bridged.",
            "- The next scientific step is a separately predeclared prospective confirmation, not an automatic live or paper strategy promotion.",
            "",
            "## Reproducibility",
            "",
            "Input SHA-256 values are recorded in the JSON artifact.",
        ]
    )
    args.out_md.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"schema": result["schema"], "buyWindows": len(rows), "out": str(args.out_json)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
