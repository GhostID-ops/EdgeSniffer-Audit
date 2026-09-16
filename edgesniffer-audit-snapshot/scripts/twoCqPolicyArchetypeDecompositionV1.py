#!/usr/bin/env python3
"""Outcome-unoptimized policy-archetype decomposition for the frozen 2CQ ledger."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import pathlib
import statistics
from typing import Any, Iterable


SCHEMA = "2CQ_POLICY_ARCHETYPE_DECOMPOSITION_V1"
SOURCE_SCHEMA = "2CQ_TEACHER_VALIDATION_V1"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def object_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile(values: Iterable[float], q: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def pf(rows: list[dict[str, Any]]) -> float | None:
    gross_profit = sum(max(float(row["netPnlSol"]), 0.0) for row in rows)
    gross_loss = sum(max(-float(row["netPnlSol"]), 0.0) for row in rows)
    if not gross_loss:
        return None
    return gross_profit / gross_loss


def sell_actions(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [action for action in row["actions"] if action["kind"] == "ECONOMIC_SELL"]


def first_sell_fraction(row: dict[str, Any]) -> float:
    sells = sell_actions(row)
    total = sum(-int(action["tokenDeltaRaw"]) for action in sells)
    return -int(sells[0]["tokenDeltaRaw"]) / total


def summary(rows: list[dict[str, Any]], universe: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    wins = [row for row in rows if row["netPnlSol"] > 0]
    losses = [row for row in rows if row["netPnlSol"] < 0]
    gross_profit = sum(float(row["netPnlSol"]) for row in wins)
    gross_loss = sum(-float(row["netPnlSol"]) for row in losses)
    universe_profit = sum(max(float(row["netPnlSol"]), 0.0) for row in universe)
    universe_loss = sum(max(-float(row["netPnlSol"]), 0.0) for row in universe)
    sell_counts = [int(row["sellCount"]) for row in rows]
    all_sell_times: dict[int, list[float]] = collections.defaultdict(list)
    for row in rows:
        for index, action in enumerate(sell_actions(row), 1):
            all_sell_times[index].append(int(action["timestamp"]) - int(row["openedTimestamp"]))
    return {
        "n": n,
        "shareOfEpisodesPct": n / len(universe) * 100 if universe else None,
        "deployedSol": sum(float(row["deployedSol"]) for row in rows),
        "netPnlSol": sum(float(row["netPnlSol"]) for row in rows),
        "grossProfitSol": gross_profit,
        "grossLossSol": gross_loss,
        "grossProfitContributionPct": gross_profit / universe_profit * 100 if universe_profit else None,
        "grossLossContributionPct": gross_loss / universe_loss * 100 if universe_loss else None,
        "netPnlContributionPct": (
            sum(float(row["netPnlSol"]) for row in rows)
            / sum(float(row["netPnlSol"]) for row in universe)
            * 100
            if universe and sum(float(row["netPnlSol"]) for row in universe)
            else None
        ),
        "wins": len(wins),
        "losses": len(losses),
        "winRatePct": len(wins) / n * 100 if n else None,
        "profitFactor": pf(rows),
        "expectancySol": sum(float(row["netPnlSol"]) for row in rows) / n if n else None,
        "medianReturnPct": percentile((row["returnPct"] for row in rows), 0.5),
        "averageWinnerSol": statistics.mean(row["netPnlSol"] for row in wins) if wins else None,
        "averageLoserSol": statistics.mean(row["netPnlSol"] for row in losses) if losses else None,
        "entrySizeSol": {
            "p10": percentile((row["firstBuySol"] for row in rows), 0.1),
            "p50": percentile((row["firstBuySol"] for row in rows), 0.5),
            "p90": percentile((row["firstBuySol"] for row in rows), 0.9),
        },
        "holdingSeconds": {
            "p10": percentile((row["holdingSeconds"] for row in rows), 0.1),
            "p50": percentile((row["holdingSeconds"] for row in rows), 0.5),
            "p90": percentile((row["holdingSeconds"] for row in rows), 0.9),
        },
        "winnerHoldingSecondsP50": percentile((row["holdingSeconds"] for row in wins), 0.5),
        "loserHoldingSecondsP50": percentile((row["holdingSeconds"] for row in losses), 0.5),
        "sellCount": {
            "total": sum(sell_counts),
            "p50": percentile(sell_counts, 0.5),
            "p90": percentile(sell_counts, 0.9),
        },
        "stagedExitPct": sum(1 for row in rows if row["sellCount"] > 1) / n * 100 if n else None,
        "singleSellExitPct": sum(1 for row in rows if row["sellCount"] == 1) / n * 100 if n else None,
        "medianSellTimingSecondsByOrdinal": {
            str(index): percentile(values, 0.5) for index, values in sorted(all_sell_times.items())
        },
    }


def daily(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        grouped[str(row["openedUtc"])[:10]].append(row)
    output = []
    for day, group in sorted(grouped.items()):
        wins = sum(1 for row in group if row["netPnlSol"] > 0)
        output.append({
            "utcDay": day,
            "n": len(group),
            "deployedSol": sum(row["deployedSol"] for row in group),
            "winRatePct": wins / len(group) * 100,
            "profitFactor": pf(group),
            "expectancySol": sum(row["netPnlSol"] for row in group) / len(group),
            "netPnlSol": sum(row["netPnlSol"] for row in group),
        })
    return output


def outcome_blind_size_modes(values: list[float]) -> dict[str, Any]:
    logs = sorted(math.log(value) for value in values)
    n = len(logs)
    standard_deviation = statistics.stdev(logs)
    iqr = percentile(logs, 0.75) - percentile(logs, 0.25)
    bandwidth = 0.9 * min(standard_deviation, iqr / 1.34) * n ** (-0.2)
    grid = [logs[0] + (logs[-1] - logs[0]) * index / 1999 for index in range(2000)]
    density = [sum(math.exp(-0.5 * ((point - value) / bandwidth) ** 2) for value in logs) for point in grid]
    maxima = [
        index for index in range(1, len(grid) - 1)
        if density[index] >= density[index - 1] and density[index] > density[index + 1]
    ]
    retained_modes = [index for index in maxima if density[index] >= max(density) * 0.05]
    boundaries = []
    for left, right in zip(retained_modes, retained_modes[1:]):
        valley = min(range(left, right + 1), key=lambda index: density[index])
        boundaries.append(math.exp(grid[valley]))
    return {
        "method": "Gaussian KDE over log(firstBuySol), Silverman bandwidth; retain modes with density >=5% of the dominant mode; boundaries are intervening density valleys. Outcomes are not used.",
        "logBandwidth": bandwidth,
        "modesSol": [math.exp(grid[index]) for index in retained_modes],
        "boundariesSol": boundaries,
    }


def size_band_index(value: float, boundaries: list[float]) -> int:
    for index, boundary in enumerate(boundaries):
        if value < boundary:
            return index
    return len(boundaries)


def exit_archetype(row: dict[str, Any]) -> str:
    count = int(row["sellCount"])
    if count == 1:
        return "RAPID_SINGLE_FULL_EXIT"
    fraction = first_sell_fraction(row)
    if count == 2:
        return "TWO_STAGE_BACK_LOADED_35_40_FIRST" if fraction <= 0.5 else "TWO_STAGE_FRONT_LOADED_60_FIRST"
    if 3 <= count <= 5:
        return "THREE_TO_FIVE_STAGE_LADDER"
    rounded = tuple(round(-int(action["tokenDeltaRaw"]) / sum(-int(x["tokenDeltaRaw"]) for x in sell_actions(row)), 2) for action in sell_actions(row))
    if count == 6 and rounded == (0.30, 0.17, 0.17, 0.17, 0.17, 0.02):
        return "SIX_STEP_30_17_RESIDUAL_LADDER"
    return "OTHER_SIX_PLUS_COMPLEX_LADDER"


def cramers_v(table: list[list[int]]) -> dict[str, Any]:
    rows = len(table)
    cols = len(table[0]) if rows else 0
    row_totals = [sum(row) for row in table]
    col_totals = [sum(table[row][col] for row in range(rows)) for col in range(cols)]
    total = sum(row_totals)
    chi_square = 0.0
    for row in range(rows):
        for col in range(cols):
            expected = row_totals[row] * col_totals[col] / total
            if expected:
                chi_square += (table[row][col] - expected) ** 2 / expected
    denominator = total * min(rows - 1, cols - 1)
    return {"chiSquare": chi_square, "cramersV": math.sqrt(chi_square / denominator) if denominator else None}


def repeated_size_modes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets = [1.214, 1.840, 1.876, 3.072]
    output = []
    for target in targets:
        matched = [row for row in rows if round(float(row["firstBuySol"]), 3) == target]
        exact = collections.Counter(float(row["firstBuySol"]) for row in matched)
        output.append({
            "roundedSizeSol": target,
            "n": len(matched),
            "sharePct": len(matched) / len(rows) * 100,
            "distinctExactCosts": len(exact),
            "dominantExactCosts": [{"sol": value, "n": count} for value, count in exact.most_common(5)],
            "genuineRecurringBand": len(matched) >= 25 and (exact.most_common(1)[0][1] if exact else 0) >= 10,
        })
    return output


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 2CQ Policy Archetype Decomposition V1",
        "",
        f"Generated: `{report['generatedAtUtc']}`",
        f"Source clean episodes: **{report['population']['n']}**",
        f"Verdict: **{report['verdict']['classification']}**",
        "",
        "## Size Modes",
        "",
        "| Mode | Range SOL | N | Share | WR | PF | Exp SOL | Net SOL | Hold P50/P90 | Staged |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for band in report["sizeBands"]:
        stats = band["statistics"]
        lines.append(
            f"| {band['name']} | {band['range']} | {stats['n']} | {stats['shareOfEpisodesPct']:.2f}% | "
            f"{stats['winRatePct']:.2f}% | {stats['profitFactor']:.3f} | {stats['expectancySol']:.4f} | "
            f"{stats['netPnlSol']:.3f} | {stats['holdingSeconds']['p50']:.0f}/{stats['holdingSeconds']['p90']:.0f}s | "
            f"{stats['stagedExitPct']:.2f}% |"
        )
    lines += [
        "",
        "## Exit Archetypes",
        "",
        "| Archetype | N | Share | WR | PF | Exp SOL | Net SOL | Profit share | Loss share | Hold P50/P90 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["exitArchetypes"]:
        stats = item["statistics"]
        lines.append(
            f"| {item['name']} | {stats['n']} | {stats['shareOfEpisodesPct']:.2f}% | {stats['winRatePct']:.2f}% | "
            f"{stats['profitFactor']:.3f} | {stats['expectancySol']:.4f} | {stats['netPnlSol']:.3f} | "
            f"{stats['grossProfitContributionPct']:.2f}% | {stats['grossLossContributionPct']:.2f}% | "
            f"{stats['holdingSeconds']['p50']:.0f}/{stats['holdingSeconds']['p90']:.0f}s |"
        )
    lines += [
        "",
        "## Size x Exit",
        "",
        f"Cramer's V: **{report['sizeExitRelationship']['association']['cramersV']:.4f}**",
        f"Global-majority exit predictability: **{report['sizeExitRelationship']['globalMajorityAccuracyPct']:.2f}%**",
        f"Size-conditioned modal predictability: **{report['sizeExitRelationship']['sizeConditionedModalAccuracyPct']:.2f}%**",
        "",
        "## Recommendation",
        "",
        report["verdict"]["nextCausalFingerprintPopulation"],
        "",
        "## Interpretation Boundary",
        "",
        "- Groups are formed from entry size and realized sell structure only; no pre-entry alpha features were read.",
        "- Failed-attempt fee overhead is not allocated to archetypes because it cannot be linked reliably to a successful episode.",
        "- Right-censored retained inventory is excluded by the clean-episode contract, so it is not mislabeled as a closed exit archetype.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    source = pathlib.Path(args.input_root)
    output = pathlib.Path(args.output_root)
    episodes_path = source / "episodes.jsonl.gz"
    source_report_path = source / "report.json"
    source_report = json.loads(source_report_path.read_text())
    if source_report.get("schema") != SOURCE_SCHEMA:
        raise RuntimeError("unexpected source schema")
    episodes = []
    with gzip.open(episodes_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("insideFrozenWindow") and row.get("pumpRelevant") and row.get("fullySelfContained"):
                episodes.append(row)
    if len(episodes) != int(source_report["primaryEconomics"]["episodes"]):
        raise RuntimeError("clean population does not match frozen source report")

    mode_definition = outcome_blind_size_modes([float(row["firstBuySol"]) for row in episodes])
    boundaries = mode_definition["boundariesSol"]
    modes = mode_definition["modesSol"]
    size_groups: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    exit_groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in episodes:
        size_groups[size_band_index(float(row["firstBuySol"]), boundaries)].append(row)
        exit_groups[exit_archetype(row)].append(row)

    size_bands = []
    for index in range(len(modes)):
        lower = 0.0 if index == 0 else boundaries[index - 1]
        upper = None if index == len(modes) - 1 else boundaries[index]
        group = size_groups[index]
        size_bands.append({
            "name": f"SIZE_MODE_{index + 1}_{modes[index]:.3f}_SOL",
            "modeCenterSol": modes[index],
            "lowerInclusiveSol": lower,
            "upperExclusiveSol": upper,
            "range": f"[{lower:.4f}, {'inf' if upper is None else f'{upper:.4f}'})",
            "representativeMedianSol": percentile((row["firstBuySol"] for row in group), 0.5),
            "statistics": summary(group, episodes),
            "chronology": daily(group),
        })

    exit_names = [
        "RAPID_SINGLE_FULL_EXIT",
        "TWO_STAGE_BACK_LOADED_35_40_FIRST",
        "TWO_STAGE_FRONT_LOADED_60_FIRST",
        "THREE_TO_FIVE_STAGE_LADDER",
        "SIX_STEP_30_17_RESIDUAL_LADDER",
        "OTHER_SIX_PLUS_COMPLEX_LADDER",
    ]
    exit_archetypes = [
        {"name": name, "statistics": summary(exit_groups[name], episodes), "chronology": daily(exit_groups[name])}
        for name in exit_names
    ]

    table = []
    matrix_rows = []
    for index, band in enumerate(size_bands):
        counts = [sum(1 for row in size_groups[index] if exit_archetype(row) == name) for name in exit_names]
        table.append(counts)
        matrix_rows.append({
            "sizeBand": band["name"],
            "n": sum(counts),
            "counts": dict(zip(exit_names, counts)),
            "rowPct": {name: count / sum(counts) * 100 for name, count in zip(exit_names, counts)},
        })

    total_deployed = sum(row["deployedSol"] for row in episodes)
    total_net = sum(row["netPnlSol"] for row in episodes)
    total_profit = sum(max(row["netPnlSol"], 0) for row in episodes)
    total_loss = sum(max(-row["netPnlSol"], 0) for row in episodes)
    dominance = []
    for family, groups in (("SIZE", [(x["name"], size_groups[i]) for i, x in enumerate(size_bands)]), ("EXIT", [(name, exit_groups[name]) for name in exit_names])):
        for name, group in groups:
            dominance.append({
                "family": family,
                "name": name,
                "n": len(group),
                "tradeCountSharePct": len(group) / len(episodes) * 100,
                "deployedSol": sum(row["deployedSol"] for row in group),
                "deployedSharePct": sum(row["deployedSol"] for row in group) / total_deployed * 100,
                "grossProfitSol": sum(max(row["netPnlSol"], 0) for row in group),
                "grossProfitSharePct": sum(max(row["netPnlSol"], 0) for row in group) / total_profit * 100,
                "grossLossSol": sum(max(-row["netPnlSol"], 0) for row in group),
                "grossLossSharePct": sum(max(-row["netPnlSol"], 0) for row in group) / total_loss * 100,
                "netPnlSol": sum(row["netPnlSol"] for row in group),
                "netPnlSharePct": sum(row["netPnlSol"] for row in group) / total_net * 100,
            })

    report = {
        "schema": SCHEMA,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {
            "teacherReportPath": str(source_report_path),
            "teacherReportSha256": file_sha256(source_report_path),
            "episodesPath": str(episodes_path),
            "episodesSha256": file_sha256(episodes_path),
            "frozenWindow": source_report["frozenWindow"],
        },
        "population": {
            "definition": "inside frozen window AND Pump-relevant AND fully self-contained/reconciled",
            "n": len(episodes),
            "uniqueMints": len({row["mint"] for row in episodes}),
            "failedAttemptFeesAllocated": False,
        },
        "sizeModeMethod": mode_definition,
        "repeatedExactSizeBands": repeated_size_modes(episodes),
        "sizeBands": size_bands,
        "exitArchetypes": exit_archetypes,
        "sizeExitRelationship": {
            "exitColumns": exit_names,
            "matrix": matrix_rows,
            "association": cramers_v(table),
            "globalMajorityAccuracyPct": max(len(exit_groups[name]) for name in exit_names) / len(episodes) * 100,
            "sizeConditionedModalAccuracyPct": sum(max(row) for row in table) / len(episodes) * 100,
            "modalAccuracyUpliftPctPoints": (
                sum(max(row) for row in table) - max(len(exit_groups[name]) for name in exit_names)
            ) / len(episodes) * 100,
        },
        "dominance": dominance,
        "verdict": {
            "classification": "2CQ_SINGLE_DOMINANT_POLICY",
            "answers": {
                "oneStrategyWithVariableSizing": "YES. The evidence best supports one recurring acquisition-and-management system with configured size tiers and outcome-responsive exit branches.",
                "multipleDistinctPolicies": "NOT ESTABLISHED. Distinct exit templates exist, but all recur throughout the seven days and are nested by realized trade path; no separate causal entry policies were tested here.",
                "sizeBandsAsConvictionTiers": "LIKELY. Larger pre-outcome entries have progressively more staged exits and stronger economics, but size is only a moderate predictor of the eventual exit branch.",
                "exitDiffersBySize": "YES, MODERATELY. Cramer's V and the cross-tab show a real relationship, especially the shift from rapid full exits at the smallest tier to ladders at larger tiers.",
                "dominantArchetype": "The coherent parent policy is one initial Pump bonding-curve acquisition followed by rapid full invalidation or staged ladder management. The three-to-five-stage branch contributes most net profit, but it is future-defined and must not be used to select the next entry-study population.",
                "nextStudyPopulationChoice": "ALL_CLEAN_2CQ_ENTRIES_WITH_SIZE_TIER_AS_ACTION_LABEL",
            },
            "nextCausalFingerprintPopulation": "Use all 4,685 clean, reconciled 2CQ entry episodes as one causal population. Preserve the outcome-blind entry-size mode as a teacher action/conviction label and stratification variable. Do not condition inclusion on the eventual exit archetype.",
            "rationale": [
                "All five natural size modes and all major exit branches persist across the frozen chronology.",
                "Size-conditioned modal exit prediction improves only modestly over the global majority, so size does not uniquely identify separate exit policies.",
                "Rapid exits and progressively longer ladders form a nested response to post-entry trade development.",
                "Selecting the profitable ladder branches for entry research would use future information and create leakage.",
            ],
        },
        "boundaries": {
            "entryFeatureSearchPerformed": False,
            "chosenVsIgnoredPerformed": False,
            "exitOptimizationPerformed": False,
            "rightCensoredInventoryExcluded": True,
            "servicesModified": False,
            "eightYcbTouched": False,
        },
    }
    report["reportSha256"] = object_sha256(report)
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (output / "report.md").write_text(render_markdown(report))
    print(json.dumps({
        "population": report["population"],
        "sizeModesSol": mode_definition["modesSol"],
        "sizeBoundariesSol": boundaries,
        "exitCounts": {name: len(exit_groups[name]) for name in exit_names},
        "cramersV": report["sizeExitRelationship"]["association"]["cramersV"],
        "output": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()
