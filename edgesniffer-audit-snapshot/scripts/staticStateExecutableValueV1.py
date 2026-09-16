#!/usr/bin/env python3
"""Bounded economic falsification of the frozen combined-static state score."""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import math
import pathlib
import statistics
from typing import Any

import numpy as np
from scipy.stats import spearmanr

import eventFlowCompetingRiskV1Analyze as parent


SCHEMA = "STATIC_STATE_EXECUTABLE_VALUE_V1"
PROFIT_INDEX = parent.CLASS_TO_INT["PROFITABLE_CONTINUATION"]


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quantile(values: list[float], q: float) -> float | None:
    return parent.quantile(values, q)


def read_predictions(path: pathlib.Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle]
    return sorted(rows, key=lambda row: (row["ordinal"], row["decisionUnixMs"], row["stateIndex"]))


def read_mint_rows(root: pathlib.Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((root / "partitions").glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            next(handle)
            rows.extend(json.loads(line) for line in handle)
    return sorted(rows, key=lambda row: row["ordinal"])


def first_by_mint(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (item["decisionUnixMs"], item["ordinal"], item["stateIndex"])):
        selected.setdefault(row["mint"], row)
    return sorted(selected.values(), key=lambda item: (item["decisionUnixMs"], item["ordinal"], item["stateIndex"]))


def pf(pnls: list[float]) -> float | None:
    gains = sum(value for value in pnls if value > 0)
    losses = abs(sum(value for value in pnls if value < 0))
    return gains / losses if losses else None


def maximum_drawdown(pnls: list[float]) -> float:
    equity = peak = 0.0
    drawdown = 0.0
    for value in pnls:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (row["decisionUnixMs"], row["ordinal"], row["stateIndex"]))
    pnls = [float(row["pnlSol"]) for row in ordered]
    returns = [float(row["returnPct"]) for row in ordered]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    ranked = sorted(ordered, key=lambda row: float(row["pnlSol"]), reverse=True)
    gross_profit = sum(wins)

    def reduced(count: int) -> dict[str, Any]:
        kept = ranked[count:]
        values = [float(row["pnlSol"]) for row in kept]
        return {"N": len(kept), "PF": pf(values), "expectancySol": statistics.mean(values) if values else None, "totalPnlSol": sum(values)}

    top_one_n = max(1, math.ceil(len(ranked) * 0.01)) if ranked else 0
    outcome_counts = collections.Counter(row["outcome"] for row in ordered)
    total = len(ordered)
    return {
        "N": total,
        "uniqueMintN": len({row["mint"] for row in ordered}),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": len(wins) / total if total else None,
        "expectancySol": statistics.mean(pnls) if pnls else None,
        "profitFactor": pf(pnls),
        "totalPnlSol": sum(pnls),
        "medianReturnPct": statistics.median(returns) if returns else None,
        "averageWinnerSol": statistics.mean(wins) if wins else None,
        "averageLoserSol": statistics.mean(losses) if losses else None,
        "bestTradeSol": max(pnls) if pnls else None,
        "worstTradeSol": min(pnls) if pnls else None,
        "sequentialMaxDrawdownSol": maximum_drawdown(pnls),
        "medianHoldingTimeMs": statistics.median(float(row["timeToOutcomeMs"]) for row in ordered) if ordered else None,
        "returnPctP10": quantile(returns, 0.10),
        "returnPctP50": quantile(returns, 0.50),
        "returnPctP90": quantile(returns, 0.90),
        "grossProfitSol": gross_profit,
        "bestTradeGrossProfitShare": (ranked[0]["pnlSol"] / gross_profit) if gross_profit and ranked else None,
        "best5GrossProfitShare": (sum(max(0.0, float(row["pnlSol"])) for row in ranked[:5]) / gross_profit) if gross_profit else None,
        "top1PctGrossProfitShare": (sum(max(0.0, float(row["pnlSol"])) for row in ranked[:top_one_n]) / gross_profit) if gross_profit else None,
        "afterRemovingBest1": reduced(1),
        "afterRemovingBest2": reduced(2),
        "outcomeCounts": dict(outcome_counts),
        "profitableContinuationRate": outcome_counts["PROFITABLE_CONTINUATION"] / total if total else None,
        "adverseFlowRate": outcome_counts["ADVERSE_FLOW"] / total if total else None,
        "timeoutRate": outcome_counts["FLOW_DECAY_TIMEOUT"] / total if total else None,
    }


def causal_thresholds(
    rows: list[dict[str, Any]], predictions: list[dict[str, Any]], parent_contract: dict[str, Any], tails: list[float], tolerance: float
) -> tuple[dict[int, dict[str, float]], float]:
    blocks = parent_contract["population"]["chronologicalBlocks"]
    features = parent_contract["staticBaselines"]["combinedStatic"]
    thresholds: dict[int, dict[str, float]] = {}
    maximum_difference = 0.0
    for fold, bounds in enumerate(blocks[1:], 2):
        train = [row for row in rows if row["ordinal"] < bounds[0]]
        test = [row for row in rows if bounds[0] <= row["ordinal"] <= bounds[1]]
        model = parent.hgb(parent_contract)
        model.fit(parent.matrix(train, features), parent.labels(train), sample_weight=parent.mint_weights(train))
        train_scores = parent.align_probabilities(model, model.predict_proba(parent.matrix(train, features)))[:, PROFIT_INDEX]
        thresholds[fold] = {f"top{int(tail * 100)}pct": float(np.quantile(train_scores, 1 - tail)) for tail in tails}
        recalculated = parent.align_probabilities(model, model.predict_proba(parent.matrix(test, features)))[:, PROFIT_INDEX]
        recorded = [row for row in predictions if row["fold"] == fold]
        if len(recorded) != len(test):
            raise RuntimeError(f"FORWARD_ROW_COUNT_MISMATCH_FOLD_{fold}:{len(recorded)}:{len(test)}")
        test_keys = [(row["mint"], row["stateIndex"]) for row in test]
        recorded_keys = [(row["mint"], row["stateIndex"]) for row in recorded]
        if test_keys != recorded_keys:
            raise RuntimeError(f"FORWARD_ROW_ORDER_MISMATCH_FOLD_{fold}")
        differences = [abs(float(expected) - float(row["probabilities"]["COMBINED_STATIC_HGB"][PROFIT_INDEX])) for expected, row in zip(recalculated, recorded)]
        maximum_difference = max(maximum_difference, max(differences, default=0.0))
    if maximum_difference > tolerance:
        raise RuntimeError(f"FROZEN_SCORE_REPRODUCTION_MISMATCH:{maximum_difference}")
    return thresholds, maximum_difference


def coverage(mint_rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = {
        "evaluable": [row for row in mint_rows if row.get("status") == "EXACT_EVALUABLE"],
        "nonEvaluable": [row for row in mint_rows if row.get("status") != "EXACT_EVALUABLE"],
    }
    output: dict[str, Any] = {}
    for name, rows in groups.items():
        activity, lifespan, flow, virtual_sol = [], [], [], []
        state_reasons = collections.Counter()
        for row in rows:
            states = row.get("decisionStates") or []
            activity.append(len(states))
            featured = [state["features"] for state in states if state.get("features")]
            if featured:
                lifespan.append(max(float(item["token_age_ms"]) for item in featured))
                flow.append(max(float(item["cumulative_net_external_flow_sol"]) for item in featured))
                virtual_sol.append(max(float(item["current_virtual_sol"]) for item in featured))
            for state in states:
                reason = (state.get("target") or {}).get("reason") or state.get("reason")
                if reason:
                    state_reasons[reason] += 1
        output[name] = {
            "N": len(rows),
            "share": len(rows) / len(mint_rows) if mint_rows else None,
            "medianCausalStateCount": statistics.median(activity) if activity else None,
            "P90CausalStateCount": quantile([float(value) for value in activity], 0.90),
            "causalQuantityCoverageN": len(lifespan),
            "medianObservedLifespanMs": statistics.median(lifespan) if lifespan else None,
            "medianMaximumCumulativeNetFlowSol": statistics.median(flow) if flow else None,
            "medianMaximumVirtualSol": statistics.median(virtual_sol) if virtual_sol else None,
            "mintReasons": dict(collections.Counter((row.get("reason") or "NONE") for row in rows)),
            "stateFailureReasons": dict(state_reasons),
        }
    evaluable_rate = len(groups["evaluable"]) / len(mint_rows)
    coverage_gap = output["evaluable"]["causalQuantityCoverageN"] / max(1, output["evaluable"]["N"]) - output["nonEvaluable"]["causalQuantityCoverageN"] / max(1, output["nonEvaluable"]["N"])
    classification = "SEVERE_SELECTION_BIAS_OBVIOUS" if evaluable_rate < 0.60 or coverage_gap > 0.50 else "MODEST_SELECTION_BIAS" if evaluable_rate < 0.80 else "NO_OBVIOUS_SELECTION_BIAS"
    return {"comparison": output, "classification": classification, "note": "Maximum virtual SOL is the available causal curve-progression proxy; no unavailable quantity was synthesized."}


def descriptive_bins(baseline: list[dict[str, Any]], bin_specs: list[list[float]]) -> tuple[list[dict[str, Any]], float | None]:
    scores = [float(row["staticScore"]) for row in baseline]
    boundaries = sorted(set(value for pair in bin_specs for value in pair))
    cuts = {str(value): float(np.quantile(scores, value)) for value in boundaries}
    output = []
    for index, (low_q, high_q) in enumerate(bin_specs):
        low, high = cuts[str(low_q)], cuts[str(high_q)]
        subset = [row for row in baseline if float(row["staticScore"]) >= low and (float(row["staticScore"]) <= high if index == len(bin_specs) - 1 else float(row["staticScore"]) < high)]
        output.append({"quantileBand": [low_q, high_q], "scoreBounds": [low, high], **metrics(subset)})
    valid = [(sum(row["quantileBand"]) / 2, row["expectancySol"]) for row in output if row["expectancySol"] is not None]
    rho = float(spearmanr([row[0] for row in valid], [row[1] for row in valid]).statistic) if len(valid) >= 2 else None
    return output, rho


def with_scores(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**row, "staticScore": float(row["probabilities"]["COMBINED_STATIC_HGB"][PROFIT_INDEX])} for row in predictions]


def select_policies(rows: list[dict[str, Any]], thresholds: dict[int, dict[str, float]], tails: list[float]) -> dict[str, list[dict[str, Any]]]:
    policies = {"baseline": first_by_mint(rows)}
    for tail in tails:
        name = f"top{int(tail * 100)}pct"
        eligible = [row for row in rows if float(row["staticScore"]) >= thresholds[int(row["fold"])][name]]
        policies[name] = first_by_mint(eligible)
    return policies


def join_sensitivity(selected: list[dict[str, Any]], p75_rows: dict[tuple[str, int], dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    joined = []
    missing = 0
    for row in selected:
        target = p75_rows.get((row["mint"], int(row["stateIndex"])))
        if target is None:
            missing += 1
            continue
        joined.append({**row, **{key: target[key] for key in ("outcome", "returnPct", "pnlSol", "timeToOutcomeMs", "counterfactualFailedTransactions")}})
    return joined, missing


def compact_metric(row: dict[str, Any]) -> str:
    pf_value = "NA" if row["profitFactor"] is None else f"{row['profitFactor']:.3f}"
    return f"N={row['N']}, WR={row['winRate']:.2%}, E={row['expectancySol']:+.5f}, PF={pf_value}, PnL={row['totalPnlSol']:+.3f}"


def number(value: float | None, digits: int = 4, signed: bool = False) -> str:
    if value is None:
        return "NA"
    return f"{value:{'+' if signed else ''}.{digits}f}"


def percent(value: float | None, digits: int = 2) -> str:
    return "NA" if value is None else f"{value * 100:.{digits}f}%"


def markdown(report: dict[str, Any]) -> str:
    lines = [f"# {SCHEMA}", "", f"## {report['verdict']}", "", "Frozen features: " + ", ".join(f"`{name}`" for name in report["frozenFeatures"]), ""]
    lines += ["## Coverage", "", "| Population | N | Share | Median states | Feature coverage | Median lifespan ms | Median max net flow | Median max virtual SOL |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in report["coverage"]["comparison"].items():
        lines.append(f"| {name} | {row['N']} | {row['share']:.2%} | {row['medianCausalStateCount']:.1f} | {row['causalQuantityCoverageN']} | {row['medianObservedLifespanMs'] if row['medianObservedLifespanMs'] is not None else 'NA'} | {row['medianMaximumCumulativeNetFlowSol'] if row['medianMaximumCumulativeNetFlowSol'] is not None else 'NA'} | {row['medianMaximumVirtualSol'] if row['medianMaximumVirtualSol'] is not None else 'NA'} |")
    excluded = report["coverage"]["comparison"]["nonEvaluable"]
    lines += [
        "", f"Coverage classification: `{report['coverage']['classification']}`.",
        f"Non-evaluable reasons: `{excluded['mintReasons']}`. Only `{excluded['causalQuantityCoverageN']}` of `{excluded['N']}` non-evaluable mints retained comparable causal quantities; their medians must not be treated as population medians.",
        "", "## Primary policies", "", "| Policy | N | WR | Expectancy SOL | PF | PnL SOL | Median ROI | Max DD SOL |", "|---|---:|---:|---:|---:|---:|---:|---:|"
    ]
    for name, row in report["policies"].items():
        value = row["primary"]
        lines.append(f"| {name} | {value['N']} | {value['winRate']:.2%} | {value['expectancySol']:+.5f} | {value['profitFactor'] if value['profitFactor'] is not None else 'NA'} | {value['totalPnlSol']:+.3f} | {value['medianReturnPct']:+.3f}% | {value['sequentialMaxDrawdownSol']:.3f} |")
    lines += ["", "### Return distribution and concentration", "", "| Policy | P10 ROI | P50 ROI | P90 ROI | Avg win SOL | Avg loss SOL | Best SOL | Worst SOL | Best / gross profit | Best 5 / gross profit | Top 1% / gross profit |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in report["policies"].items():
        value = row["primary"]
        lines.append(f"| {name} | {number(value['returnPctP10'], 3, True)}% | {number(value['returnPctP50'], 3, True)}% | {number(value['returnPctP90'], 3, True)}% | {number(value['averageWinnerSol'], 5, True)} | {number(value['averageLoserSol'], 5, True)} | {number(value['bestTradeSol'], 5, True)} | {number(value['worstTradeSol'], 5, True)} | {percent(value['bestTradeGrossProfitShare'])} | {percent(value['best5GrossProfitShare'])} | {percent(value['top1PctGrossProfitShare'])} |")
    lines += ["", "### Best-trade removal and competing outcomes", "", "| Policy | PF after best 1 | PnL after best 1 | PF after best 2 | PnL after best 2 | Profitable continuation | Adverse flow | Timeout | Median hold ms |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in report["policies"].items():
        value = row["primary"]
        lines.append(f"| {name} | {number(value['afterRemovingBest1']['PF'], 3)} | {number(value['afterRemovingBest1']['totalPnlSol'], 3, True)} | {number(value['afterRemovingBest2']['PF'], 3)} | {number(value['afterRemovingBest2']['totalPnlSol'], 3, True)} | {percent(value['profitableContinuationRate'])} | {percent(value['adverseFlowRate'])} | {percent(value['timeoutRate'])} | {number(value['medianHoldingTimeMs'], 0)} |")
    lines += ["", "## Descriptive monotonicity", "", "These bins use the first forward-scored state per mint and full-discovery score quantiles for description only. They are not executable tail thresholds.", "", "| Score quantile | Score bounds | N | WR | Expectancy SOL | PF | PnL SOL | Median ROI |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in report["descriptiveFirstStateScoreBins"]:
        label = f"{int(row['quantileBand'][0] * 100)}-{int(row['quantileBand'][1] * 100)}%"
        bounds = f"{row['scoreBounds'][0]:.6f}-{row['scoreBounds'][1]:.6f}"
        lines.append(f"| {label} | {bounds} | {row['N']} | {percent(row['winRate'])} | {number(row['expectancySol'], 5, True)} | {number(row['profitFactor'], 3)} | {number(row['totalPnlSol'], 3, True)} | {number(row['medianReturnPct'], 3, True)}% |")
    lines += ["", f"Expectancy rank monotonicity (Spearman): `{number(report['descriptiveBinExpectancySpearman'], 3)}`.", "", "## Chronological robustness", ""]
    for policy in ("top20pct", "top10pct", "top5pct"):
        lines += [f"### {policy}", "", "| Fold | Ordinals | N | WR | Expectancy SOL | PF | PnL SOL |", "|---:|---|---:|---:|---:|---:|---:|"]
        for row in report["policies"][policy]["chronological"]:
            bounds = report["chronologicalBlockBounds"][str(row["fold"])]
            lines.append(f"| {row['fold']} | {bounds[0]}-{bounds[1]} | {row['N']} | {percent(row['winRate'])} | {number(row['expectancySol'], 5, True)} | {number(row['profitFactor'], 3)} | {number(row['totalPnlSol'], 3, True)} |")
        lines.append("")
    lines += ["## Primary robustness", "", "| Policy | Remove top 1% active PF | Expectancy | PnL | Non-divergent N | Non-divergent PF | Expectancy | PnL |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in report["policies"].items():
        active, clean = row["removeTop1PctActiveMints"], row["nonDivergentOnly"]
        lines.append(f"| {name} | {number(active['profitFactor'], 3)} | {number(active['expectancySol'], 5, True)} | {number(active['totalPnlSol'], 3, True)} | {clean['N']} | {number(clean['profitFactor'], 3)} | {number(clean['expectancySol'], 5, True)} | {number(clean['totalPnlSol'], 3, True)} |")
    lines += ["", "## P75 sensitivity", "", "| Policy | Matched N | Missing | WR | Expectancy SOL | PF | PnL SOL |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name, row in report["policies"].items():
        value = row["pessimistic"]
        lines.append(f"| {name} | {value['N']} | {row['pessimisticMissingN']} | {value['winRate']:.2%} | {value['expectancySol']:+.5f} | {value['profitFactor'] if value['profitFactor'] is not None else 'NA'} | {value['totalPnlSol']:+.3f} |")
    lines += ["", "## Walk-forward thresholds", "", "Each threshold is a quantile of frozen combined-static scores from the corresponding fold's strictly prior training population.", "", "| Fold | Top 20% | Top 10% | Top 5% |", "|---:|---:|---:|---:|"]
    for fold, values in report["causalThresholds"].items():
        lines.append(f"| {fold} | {values['top20pct']:.9f} | {values['top10pct']:.9f} | {values['top5pct']:.9f} |")
    lines += ["", "## Verdict gates", ""]
    for name, value in report["verdictChecks"].items():
        lines.append(f"- `{name}`: `{value}`")
    lines += [
        "", "## Interpretation", "",
        "The frozen static score materially enriches profitable continuations, but its causal first-entry policies remain below break-even. The top 10% and top 5% tails both have negative expectancy and PF below 1; later chronological folds deteriorate, descriptive bin expectancy is not monotonic, and P75 execution remains negative.",
        "", "The optional 1 SOL capacity sensitivity was not run because the primary 0.5 SOL result was not clearly promising.",
        "", f"Frozen-score reproduction maximum absolute difference: `{report['scoreReproductionMaximumAbsoluteDifference']}`.",
        "", "No ordinal 6849+, validation, holdout, prospective, or live data was read.", ""
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("parent_build", type=pathlib.Path)
    parser.add_argument("parent_predictions", type=pathlib.Path)
    parser.add_argument("parent_contract", type=pathlib.Path)
    parser.add_argument("p75_build", type=pathlib.Path)
    parser.add_argument("contract", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    parent_contract = json.loads(args.parent_contract.read_text(encoding="utf-8"))
    if contract.get("schema") != SCHEMA + "_CONTRACT":
        raise RuntimeError("CONTRACT_SCHEMA_MISMATCH")
    if sha256_file(args.parent_contract) != contract["parents"]["contractSha256"]:
        raise RuntimeError("PARENT_CONTRACT_HASH_MISMATCH")
    rows = parent.read_rows(args.parent_build)
    predictions = with_scores(read_predictions(args.parent_predictions))
    if any(int(row["ordinal"]) > 6848 for row in rows + predictions):
        raise RuntimeError("FORBIDDEN_ORDINAL_ACCESSED")
    tails = [float(value) for value in contract["selection"]["tails"]]
    thresholds, score_difference = causal_thresholds(rows, predictions, parent_contract, tails, float(contract["frozenModel"]["forwardScoreReproductionTolerance"]))
    policies = select_policies(predictions, thresholds, tails)
    p75_exact = {(row["mint"], int(row["stateIndex"])): row for row in parent.read_rows(args.p75_build)}
    activity = collections.Counter(row["mint"] for row in predictions)
    removal_n = max(1, math.ceil(len(activity) * float(contract["robustness"]["removeTopActivityMintFraction"])))
    removed_active = {mint for mint, _ in activity.most_common(removal_n)}

    policy_report = {}
    selected_evidence = []
    for name, selected in policies.items():
        p75_selected, p75_missing = join_sensitivity(selected, p75_exact)
        chronological = []
        for fold in contract["population"]["forwardEvaluationFolds"]:
            chronological.append({"fold": fold, **metrics([row for row in selected if int(row["fold"]) == fold])})
        policy_report[name] = {
            "primary": metrics(selected),
            "chronological": chronological,
            "removeTop1PctActiveMints": metrics([row for row in selected if row["mint"] not in removed_active]),
            "nonDivergentOnly": metrics([row for row in selected if int(row["counterfactualFailedTransactions"]) == 0]),
            "pessimistic": metrics(p75_selected),
            "pessimisticMissingN": p75_missing,
            "foldScoreThresholds": {str(fold): thresholds[fold].get(name) for fold in thresholds} if name != "baseline" else None,
        }
        selected_evidence.extend({"policy": name, **row} for row in selected)

    baseline = policies["baseline"]
    bins, bin_rho = descriptive_bins(baseline, contract["selection"]["descriptiveBins"])
    gate = contract["verdictGate"]
    primary = policy_report[gate["primaryTail"]]["primary"]
    pessimistic = policy_report[gate["primaryTail"]]["pessimistic"]
    positive_blocks = sum((row["expectancySol"] or 0) > 0 and (row["profitFactor"] or 0) > 1 for row in policy_report[gate["primaryTail"]]["chronological"])
    checks = {
        "minimumTrades": primary["N"] >= int(gate["minimumTrades"]),
        "materialPositiveExpectancy": primary["expectancySol"] is not None and primary["expectancySol"] >= float(gate["minimumExpectancySol"]),
        "profitFactor": primary["profitFactor"] is not None and primary["profitFactor"] >= float(gate["minimumProfitFactor"]),
        "survivesBestTwoRemoval": primary["afterRemovingBest2"]["PF"] is not None and primary["afterRemovingBest2"]["PF"] >= float(gate["minimumProfitFactorAfterRemovingBestTwo"]),
        "chronologicalConsistency": positive_blocks >= int(gate["minimumPositiveChronologicalBlocks"]),
        "pessimisticExecution": pessimistic["profitFactor"] is not None and pessimistic["profitFactor"] >= float(gate["minimumPessimisticProfitFactor"]) and pessimistic["expectancySol"] is not None and pessimistic["expectancySol"] > float(gate["minimumPessimisticExpectancySol"]),
        "descriptiveMonotonicity": bin_rho is not None and bin_rho >= float(gate["minimumDescriptiveBinSpearman"]),
    }
    if all(checks.values()):
        verdict = "STATIC_STATE_EXECUTABLE_EDGE_PROMISING_NEEDS_UNSEEN"
    elif primary["expectancySol"] is not None and primary["expectancySol"] > 0 and primary["profitFactor"] is not None and primary["profitFactor"] > 1:
        verdict = "STATIC_STATE_EXECUTABLE_EDGE_FRAGILE"
    else:
        verdict = "STATIC_STATE_HAS_NO_USEFUL_EXECUTABLE_EDGE"

    report = {
        "schema": SCHEMA + "_REPORT",
        "verdict": verdict,
        "frozenFeatures": contract["frozenModel"]["features"],
        "scoreSemantics": contract["frozenModel"]["score"],
        "scoreReproductionMaximumAbsoluteDifference": score_difference,
        "coverage": coverage(read_mint_rows(args.parent_build)),
        "causalThresholdMethod": contract["selection"]["thresholdSource"],
        "causalThresholds": {str(fold): values for fold, values in thresholds.items()},
        "chronologicalBlockBounds": {str(fold): parent_contract["population"]["chronologicalBlocks"][fold - 1] for fold in contract["population"]["forwardEvaluationFolds"]},
        "policies": policy_report,
        "descriptiveFirstStateScoreBins": bins,
        "descriptiveBinExpectancySpearman": bin_rho,
        "removedTopActivityMintsN": len(removed_active),
        "verdictChecks": checks,
        "validationRowsInspected": 0,
        "holdoutRowsInspected": 0,
        "ordinalsAfter6848Inspected": 0,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output / "report.md").write_text(markdown(report), encoding="utf-8")
    with gzip.open(args.output / "selected-policy-rows.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in selected_evidence:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    provenance = {
        "schema": SCHEMA + "_PROVENANCE",
        "contractSha256": sha256_file(args.contract),
        "parentContractSha256": sha256_file(args.parent_contract),
        "parentPredictionsSha256": sha256_file(args.parent_predictions),
        "parentBuildProvenanceSha256": sha256_file(args.parent_build / "build-provenance.json"),
        "p75BuildProvenanceSha256": sha256_file(args.p75_build / "build-provenance.json"),
        "analysisScriptSha256": sha256_file(pathlib.Path(__file__)),
        "reportSha256": sha256_file(args.output / "report.json"),
        "validationRowsInspected": 0,
        "holdoutRowsInspected": 0,
        "ordinalsAfter6848Inspected": 0,
    }
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": verdict, "primary": compact_metric(primary), "scoreMaxDiff": score_difference}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
