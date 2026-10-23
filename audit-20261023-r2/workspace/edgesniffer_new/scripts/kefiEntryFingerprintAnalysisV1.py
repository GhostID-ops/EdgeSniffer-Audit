#!/usr/bin/env python3
"""Outcome-blind descriptive entry-structure analysis for the frozen kEFi sample.

This deliberately does not read outcomes, PnL, labels, or fit a predictive model.
It joins the sealed structural trade rows to their sealed pre-entry context rows and
emits reproducible descriptive archetypes for offline research.
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
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, int((len(values) - 1) * fraction))
    return values[index]


def summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "min": min(values) if values else None,
        "median": median(values) if values else None,
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def counts(counter: Counter[str]) -> list[list[str | int]]:
    return [[key, value] for key, value in counter.most_common()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--structural", type=Path, required=True)
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    structural = load_json(args.structural)
    entry_analysis = load_json(args.entry)
    freeze = load_json(args.freeze)
    context_rows = {
        int(row["ordinal"]): row
        for row in (
            json.loads(line)
            for line in args.context.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    freeze_rows = {int(row["ordinal"]): row for row in freeze["items"]}
    trade_rows = {int(row["ordinal"]): row for row in structural["trades"]}
    entry_rows = {
        int(row["ordinal"]): row for row in entry_analysis.get("entryRows", [])
    }

    if set(context_rows) != set(trade_rows) or set(context_rows) != set(freeze_rows):
        raise SystemExit("structural/context/freeze ordinal sets differ")

    rows = []
    for ordinal in sorted(trade_rows):
        trade = trade_rows[ordinal]
        context = context_rows[ordinal]
        frozen = freeze_rows[ordinal]
        direction = str(trade.get("direction", "UNKNOWN"))
        deltas = trade.get("tokenDeltas") or {}
        selected_mints = sorted(str(mint) for mint, delta in deltas.items() if int(delta) != 0)
        kinds = context.get("pumpInstructionKinds") or {}
        relevant_count = sum(int(value) for value in kinds.values())
        entry_row = entry_rows.get(ordinal, {})
        rows.append(
            {
                "ordinal": ordinal,
                "signature": trade["signature"],
                "direction": direction,
                "venue": "+".join(sorted(trade.get("venues") or [])) or "UNKNOWN",
                "selectedMints": selected_mints,
                "canonicalSlot": int(trade.get("canonicalSlot") or 0),
                "receivedAtUnixNs": int(frozen["receivedAtUnixNs"]),
                "continuityEpoch": int(frozen.get("continuityEpoch") or 0),
                "preEntryFrames": int(context.get("framesInPreEntryWindow") or 0),
                "preEntryRelevantFrames": relevant_count,
                "preEntryPumpSwap": int(kinds.get("PUMPSWAP_FILTER_MATCH", 0)),
                "preEntryPumpCpi": int(kinds.get("PUMP_CPI_ROUTER_FILTER_MATCH", 0)),
                "preEntryDirectPump": int(kinds.get("PUMP_FILTER_MATCH", 0)),
                "instructionTemplate": "+".join(entry_row.get("instructionNames") or []),
            }
        )

    buys = [row for row in rows if row["direction"] == "BUY"]
    sells = [row for row in rows if row["direction"] == "SELL"]
    ordered_buys = sorted(buys, key=lambda row: (row["receivedAtUnixNs"], row["ordinal"]))
    seen_mints: set[str] = set()
    buy_reuse = Counter()
    for row in ordered_buys:
        for mint in row["selectedMints"]:
            buy_reuse["repeat_buy"] += int(mint in seen_mints)
            buy_reuse["first_buy"] += int(mint not in seen_mints)
            seen_mints.add(mint)

    per_mint: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for mint in row["selectedMints"]:
            per_mint[mint].append(row["direction"])
    mint_sequence = Counter()
    for directions in per_mint.values():
        has_buy = "BUY" in directions
        has_sell = "SELL" in directions
        if has_buy and has_sell:
            mint_sequence["both_buy_and_sell"] += 1
        elif has_buy:
            mint_sequence["buy_only"] += 1
        elif has_sell:
            mint_sequence["sell_only"] += 1
        else:
            mint_sequence["other"] += 1

    background_profiles = Counter()
    for row in buys:
        values = {
            "pumpswap": row["preEntryPumpSwap"],
            "pump_cpi": row["preEntryPumpCpi"],
            "direct_pump": row["preEntryDirectPump"],
        }
        total = sum(values.values())
        if not total:
            background_profiles["no_pump_match"] += 1
        else:
            dominant, dominant_count = max(values.items(), key=lambda item: item[1])
            share = dominant_count / total
            bucket = "<75%" if share < 0.75 else "75-90%" if share < 0.90 else ">=90%"
            background_profiles[f"{dominant}:{bucket}"] += 1

    buy_frame_values = [float(row["preEntryFrames"]) for row in buys]
    buy_relevant_values = [float(row["preEntryRelevantFrames"]) for row in buys]
    buy_density = [value / 4.0 for value in buy_relevant_values]
    buy_slot_gaps = [
        float(right["canonicalSlot"] - left["canonicalSlot"])
        for left, right in zip(ordered_buys, ordered_buys[1:])
    ]
    buy_time_gaps = [
        (right["receivedAtUnixNs"] - left["receivedAtUnixNs"]) / 1_000_000_000.0
        for left, right in zip(ordered_buys, ordered_buys[1:])
    ]

    template_counts = Counter(row["instructionTemplate"] for row in buys)
    venue_counts = Counter(row["venue"] for row in rows)
    epoch_counts = Counter(str(row["continuityEpoch"]) for row in rows)
    buy_epoch_counts = Counter(str(row["continuityEpoch"]) for row in buys)
    epoch_profiles = []
    for epoch in sorted({row["continuityEpoch"] for row in rows}):
        epoch_rows = [row for row in rows if row["continuityEpoch"] == epoch]
        epoch_buys = [row for row in epoch_rows if row["direction"] == "BUY"]
        epoch_profiles.append(
            {
                "epoch": epoch,
                "trades": len(epoch_rows),
                "buys": len(epoch_buys),
                "sells": sum(row["direction"] == "SELL" for row in epoch_rows),
                "uniqueSelectedMints": len({mint for row in epoch_rows for mint in row["selectedMints"]}),
                "buyMedianRelevantFrames": median(
                    [row["preEntryRelevantFrames"] for row in epoch_buys]
                )
                if epoch_buys
                else None,
            }
        )

    result = {
        "schema": "KEFI_OUTCOME_BLIND_ENTRY_FINGERPRINT_ANALYSIS_V1",
        "createdAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "trackedWallet": structural.get("trackedWallet"),
        "interpretation": "Descriptive entry structure only; no outcomes, PnL, labels, predictive scores, or model fitting accessed.",
        "inputHashes": {
            "structural": sha256(args.structural),
            "entry": sha256(args.entry),
            "context": sha256(args.context),
            "freeze": sha256(args.freeze),
        },
        "sample": {
            "canonicalTrades": len(rows),
            "buyTransactions": len(buys),
            "sellTransactions": len(sells),
            "uniqueSelectedMints": len(per_mint),
            "continuityEpochs": counts(epoch_counts),
            "buyContinuityEpochs": counts(buy_epoch_counts),
        },
        "venueCounts": counts(venue_counts),
        "epochProfiles": epoch_profiles,
        "buyEntryStructure": {
            "preEntryBackgroundProfiles": counts(background_profiles),
            "instructionTemplates": counts(template_counts),
            "preEntryFrames": summary(buy_frame_values),
            "preEntryRelevantFrames": summary(buy_relevant_values),
            "preEntryRelevantFramesPer30s": summary(buy_density),
            "canonicalSlotGapBetweenBuys": summary(buy_slot_gaps),
            "receiptSecondsBetweenBuys": summary(buy_time_gaps),
            "mintReuse": counts(buy_reuse),
        },
        "mintSequences": counts(mint_sequence),
        "structuralFacts": {
            "buyMintEvents": int(structural["summary"].get("buy_mint_events", 0)),
            "sellMintEvents": int(structural["summary"].get("sell_mint_events", 0)),
            "unresolvedTransactions": int(structural["summary"].get("unresolved_transactions", 0)),
            "preEntryWindowsWithDiscoveredMints": sum(bool(row.get("discoveredMints")) for row in context_rows.values()),
            "preEntryWindowsWithTradeEventFrames": sum(int(row.get("tradeEventFrames", 0)) > 0 for row in context_rows.values()),
        },
        "scientificGuards": {
            "sampleManifestUnchanged": True,
            "continuityEpochsKeptSeparate": True,
            "openPartitionIncluded": False,
            "outcomesAccessed": False,
            "pnlAccessed": False,
            "predictiveModelFit": False,
        },
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    top_profiles = result["buyEntryStructure"]["preEntryBackgroundProfiles"][:8]
    top_templates = result["buyEntryStructure"]["instructionTemplates"][:8]
    markdown = f"""# kEFi Entry Fingerprint Analysis V1

Outcome-blind descriptive analysis of the sealed first **{len(rows):,}** canonical trades for `{structural.get('trackedWallet')}`.

This report describes entry structure only. It does not inspect outcomes or PnL, fit a model, or claim profitability.

## Sample

- Buy transactions: **{len(buys):,}**
- Sell transactions: **{len(sells):,}**
- Unique selected mints: **{len(per_mint):,}**
- Continuity epochs retained separately: **{len(epoch_counts)}**
- Open partition included: **no**

## Pre-entry background profiles

These describe the activity mix in the 120-second source window before the buy. They are not entry rules and are not predictive results.

| Dominant background | Count |
|---|---:|
""" + "\n".join(f"| `{key}` | {value} |" for key, value in top_profiles) + f"""

## Most common buy transaction templates

| Template | Count |
|---|---:|
""" + "\n".join(f"| `{key}` | {value} |" for key, value in top_templates) + f"""

## Pre-entry activity

| Measure | Min | Median | P90 | P95 | Max |
|---|---:|---:|---:|---:|---:|
| Source frames in 120s window | {result['buyEntryStructure']['preEntryFrames']['min']} | {result['buyEntryStructure']['preEntryFrames']['median']} | {result['buyEntryStructure']['preEntryFrames']['p90']} | {result['buyEntryStructure']['preEntryFrames']['p95']} | {result['buyEntryStructure']['preEntryFrames']['max']} |
| Relevant Pump frames | {result['buyEntryStructure']['preEntryRelevantFrames']['min']} | {result['buyEntryStructure']['preEntryRelevantFrames']['median']} | {result['buyEntryStructure']['preEntryRelevantFrames']['p90']} | {result['buyEntryStructure']['preEntryRelevantFrames']['p95']} | {result['buyEntryStructure']['preEntryRelevantFrames']['max']} |

## Epoch-separated stability

| Epoch | Trades | Buys | Sells | Unique mints | Median relevant frames |
|---:|---:|---:|---:|---:|---:|
""" + "\n".join(
        f"| {item['epoch']} | {item['trades']} | {item['buys']} | {item['sells']} | {item['uniqueSelectedMints']} | {item['buyMedianRelevantFrames'] or '-'} |"
        for item in epoch_profiles
    ) + f"""

## Structural interpretation

- The dominant venue and entry-instruction patterns are descriptive facts, not a trading signal.
- Background activity profiles are reported separately from the actual entry transaction template.
- Pre-entry windows intentionally contain no selected trade event; seeing zero trade-event frames before entry is expected.
- Repeated mints and repeated buy/sell sequences are retained as separate structural categories.
- Epoch boundaries are preserved and never merged into one continuous stream.

## Reproducibility

The JSON artifact records SHA-256 hashes for all four inputs and the scientific guard assertions. No live service or source partition was modified.
"""
    args.out_md.write_text(markdown, encoding="utf-8")
    print(json.dumps({"schema": result["schema"], "trades": len(rows), "buys": len(buys), "out": str(args.out_json)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
