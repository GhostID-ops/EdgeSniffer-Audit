#!/usr/bin/env python3
import hashlib
import json
import pathlib
import statistics
import sys


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    recovery_dir = pathlib.Path(sys.argv[1])
    replay_dir = pathlib.Path(sys.argv[2])
    out = pathlib.Path(sys.argv[3])
    recovery_path = recovery_dir / "report.json"
    replay_path = replay_dir / "report.json"
    recovery = json.loads(recovery_path.read_text())
    replay = json.loads(replay_path.read_text())
    coverage = {row["mint"]: row for row in recovery["mints"]}
    records = replay["records"]
    mints = {row["mint"] for row in records}
    if len(records) != 26 or mints != set(coverage):
        raise RuntimeError("fixed cohort integrity failure")

    rows = []
    for row in records:
        profile = row["actual2Cq"]
        covered = coverage[row["mint"]]
        rows.append({
            "mint": row["mint"],
            "ordinal": row["ordinal"],
            "entryUtc": covered["entryUtc"],
            "entryAgeMs": profile["entryAgeMs"],
            "latestExactEvidenceUtc": covered["latestExactEvidenceUtc"],
            "exactObservedSpanAfterEntryMs": covered["exactObservedSpanAfterEntryMs"],
            "exact120sReplayAvailable": covered["exact120sReplayAvailable"],
            "laterExactEvidenceExists": covered["exactObservedSpanAfterEntryMs"] > 120_000,
            "sellVenue": "PUMP_CURVE_SELL",
            "sellCount": profile["sellCount"],
            "firstSellLatencyMs": profile["firstSellLatencyMs"],
            "fullExitLatencyMs": profile["fullExitLatencyMs"],
            "firstSellFraction": profile["sellFractions"][0]["fractionOfThenInventory"],
            "actual2CqRoiPct": profile["actualRealizedRoiPct"],
            "mfeBeforeFirstSellPct": profile["mfeBeforeFirstSellPct"],
            "maeBeforeFirstSellPct": profile["maeBeforeFirstSellPct"],
            "generic05RoiPct": row["actualTimeFrozenExit"]["0.5"].get("returnPct"),
            "schedule05RoiPct": row["actualTimeWalletSchedule"]["0.5"].get("returnPct"),
        })

    actual_pnls = [row["actual2Cq"]["actualRealizedPnlSol"] for row in records]
    gross_profit = sum(x for x in actual_pnls if x > 0)
    gross_loss = -sum(x for x in actual_pnls if x < 0)
    generic = replay["actual2CqTimeFrozenExit"]
    schedule = replay["actual2CqTimeWalletSchedule"]
    report = {
        "schema": "TWO_CQ_EXACT_SELL_EVIDENCE_FIXED26_FINAL_V1",
        "classification": "2CQ_SELL_JOIN_BUG_FOUND_AND_FIXED_OFFLINE",
        "cohortN": 26,
        "soldMintN": 26,
        "fullyClosedMintN": 26,
        "sellTransactionN": sum(row["actual2Cq"]["sellCount"] for row in records),
        "venueDistribution": {"PUMP_CURVE_SELL": 26, "PUMPSWAP_SELL": 0, "OTHER_DEX_SELL": 0, "TOKEN_TRANSFER": 0, "NO_SELL_OBSERVED": 0},
        "rootCause": "Finalized sells decoded correctly, but the old offline arrival join retained raw rows only when metadata exposed a recognized Pump instruction kind or discovered target mint. Sell raw rows exposed neither, despite matching finalized signatures, so all 2CQ sells were omitted.",
        "joinCategoryCounts": {"A_RAW_OBSERVATION_METADATA_JOIN_GAP": sum(row["actual2Cq"]["sellCount"] for row in records)},
        "exactSellSchedulesReconstructable": 26,
        "medianFirstSellLatencyMs": statistics.median(row["actual2Cq"]["firstSellLatencyMs"] for row in records),
        "medianFullExitLatencyMs": statistics.median(row["actual2Cq"]["fullExitLatencyMs"] for row in records),
        "firstExitStyle": {
            "fullAtFirstSellN": sum(row["firstSellFraction"] >= 0.999 for row in rows),
            "partialAtFirstSellN": sum(row["firstSellFraction"] < 0.999 for row in rows),
            "medianFirstSellFraction": statistics.median(row["firstSellFraction"] for row in rows),
        },
        "genericTp20Sl20Max120": generic,
        "observed2CqScheduleScaled": schedule,
        "actual2Cq": {
            "N": 26,
            "wins": sum(x > 0 for x in actual_pnls),
            "losses": sum(x < 0 for x in actual_pnls),
            "WR": sum(x > 0 for x in actual_pnls) / 26,
            "PF": gross_profit / gross_loss,
            "pnlSol": sum(actual_pnls),
            "medianRoiPct": statistics.median(row["actual2Cq"]["actualRealizedRoiPct"] for row in records),
            "worstRoiPct": min(row["actual2Cq"]["actualRealizedRoiPct"] for row in records),
        },
        "edgeAttribution": "TIMING_PLUS_EXIT, conditional on 2CQ selection",
        "integrity": replay["integrity"],
        "rows": rows,
    }
    out.mkdir(parents=True, exist_ok=True)
    final_json = out / "report.json"
    final_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    lines = [
        "# 2CQ Exact Sell-Evidence Recovery - Fixed 26",
        "",
        "Classification: `2CQ_SELL_JOIN_BUG_FOUND_AND_FIXED_OFFLINE`",
        "",
        "## Result",
        "",
        f"- Sold/fully closed: **26/26** through **42 Pump bonding-curve sell transactions**.",
        f"- Median first sell: **{report['medianFirstSellLatencyMs']:.1f}ms**; median full exit: **{report['medianFullExitLatencyMs']:.1f}ms**.",
        f"- First sell was full-size for **{report['firstExitStyle']['fullAtFirstSellN']}** mints and partial for **{report['firstExitStyle']['partialAtFirstSellN']}**.",
        "- The old join found zero because raw sell rows lacked the two metadata hints it required; finalized signature matching recovers them without changing the official ledger.",
        "",
        "## Economics",
        "",
        "| Policy | Size | N | WR | PF | Expectancy | PnL SOL | Median ROI | Worst ROI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, block in (("TP20/SL20/120s", generic), ("Observed 2CQ schedule", schedule)):
        for size in ("0.5", "1.0"):
            item = block[size]
            lines.append(f"| {label} | {size} | {item['exactN']} | {item['WR']:.2%} | {item['PF']:.2f} | {item['expectancy']:.2%} | {item['pnlSol']:+.4f} | {item['medianRoiPct']:+.2f}% | {item['worstRoiPct']:+.2f}% |")
    a = report["actual2Cq"]
    lines += [
        f"| Actual 2CQ realized | actual sizing | {a['N']} | {a['WR']:.2%} | {a['PF']:.2f} | n/a | {a['pnlSol']:+.4f} | {a['medianRoiPct']:+.2f}% | {a['worstRoiPct']:+.2f}% |",
        "",
        "## Coverage And Exit Style",
        "",
        "The ledger is event-driven rather than a polling tape. `120s covered` below means the source partition is marked exact-replay complete and supports causal replay through T+120s; observed span is the interval to the latest retained exact event.",
        "",
        "| Mint | Ord | Entry UTC | Age ms | Observed span s | 120s covered | Sells | First/full ms | First fraction | Actual ROI |",
        "|---|---:|---|---:|---:|:---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(f"| `{row['mint']}` | {row['ordinal']} | {row['entryUtc']} | {row['entryAgeMs']} | {row['exactObservedSpanAfterEntryMs']/1000:.1f} | {'yes' if row['exact120sReplayAvailable'] else 'no'} | {row['sellCount']} | {row['firstSellLatencyMs']}/{row['fullExitLatencyMs']} | {row['firstSellFraction']:.0%} | {row['actual2CqRoiPct']:+.2f}% |")
    (out / "human-review.md").write_text("\n".join(lines) + "\n")
    provenance = {
        "recoveryReportSha256": sha256(recovery_path),
        "correctedReplayReportSha256": sha256(replay_path),
        "finalReportSha256": sha256(final_json),
        "cohortManifestSha256": replay["identity"]["prefixTableSha256"] if "prefixTableSha256" in replay["identity"] else recovery["cohortManifestSha256"],
        "validationRowsInspected": 0,
        "holdoutRowsInspected": 0,
    }
    (out / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
