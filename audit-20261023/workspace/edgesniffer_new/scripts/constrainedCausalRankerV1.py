#!/usr/bin/env python3

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import pathlib
import random
import statistics
from typing import Any


SCHEMA = "CONSTRAINED_CAUSAL_RANKER_V1"


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        rank = (cursor + end - 1) / 2 + 1
        for offset in range(cursor, end):
            ranks[order[offset]] = rank
        cursor = end
    return ranks


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    left_mean = statistics.mean(left)
    right_mean = statistics.mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_sum = sum((value - left_mean) ** 2 for value in left)
    right_sum = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_sum * right_sum)
    return numerator / denominator if denominator else None


def spearman(rows: list[dict[str, Any]], left: str, right: str) -> dict[str, Any]:
    pairs = [
        (row["features"].get(left), row["features"].get(right))
        for row in rows
        if row["features"].get(left) is not None and row["features"].get(right) is not None
    ]
    return {
        "left": left,
        "right": right,
        "completeCaseN": len(pairs),
        "spearmanRho": pearson(
            average_ranks([float(pair[0]) for pair in pairs]),
            average_ranks([float(pair[1]) for pair in pairs]),
        ),
    }


def safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def economics(returns: list[float]) -> dict[str, Any]:
    gains = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    gross_profit = sum(gains) / 100
    gross_loss = abs(sum(losses)) / 100
    sorted_losses = sorted((abs(value) for value in losses), reverse=True)
    sorted_gains = sorted(gains, reverse=True)
    return {
        "N": len(returns),
        "wins": len(gains),
        "losses": len(losses),
        "winRate": safe_ratio(len(gains), len(returns)),
        "profitFactor": safe_ratio(gross_profit, gross_loss),
        "expectancyPct": statistics.mean(returns) if returns else None,
        "normalizedPnlSolAt1Sol": sum(returns) / 100,
        "medianRoiPct": statistics.median(returns) if returns else None,
        "worstRoiPct": min(returns) if returns else None,
        "bestRoiPct": max(returns) if returns else None,
        "lossConcentration": {
            "largestLossShare": safe_ratio(sum(sorted_losses[:1]), sum(sorted_losses)),
            "top2LossShare": safe_ratio(sum(sorted_losses[:2]), sum(sorted_losses)),
            "top5LossShare": safe_ratio(sum(sorted_losses[:5]), sum(sorted_losses)),
        },
        "winnerConcentration": {
            "largestWinnerShare": safe_ratio(sum(sorted_gains[:1]), sum(sorted_gains)),
            "top2WinnerShare": safe_ratio(sum(sorted_gains[:2]), sum(sorted_gains)),
            "top5WinnerShare": safe_ratio(sum(sorted_gains[:5]), sum(sorted_gains)),
        },
    }


def top_count(size: int, percentage: float) -> int:
    return max(1, int(math.ceil(size * percentage / 100))) if size else 0


def sorted_predictions(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(predictions, key=lambda row: (-row["score"], row["ordinal"], row["mint"]))


def ranking_summary(predictions: list[dict[str, Any]], target: str) -> dict[str, Any]:
    usable = [row for row in predictions if row["labels"].get(target) is not None]
    ordered = sorted_predictions(usable)
    positive_n = sum(row["labels"][target] is True for row in usable)
    base_rate = safe_ratio(positive_n, len(usable))
    output = {
        "N": len(usable),
        "positiveN": positive_n,
        "basePositiveRate": base_rate,
        "buckets": {},
    }
    for percentage in (5, 10, 20):
        count = top_count(len(ordered), percentage)
        selected = ordered[:count]
        rate = safe_ratio(sum(row["labels"][target] is True for row in selected), count)
        output["buckets"][f"TOP_{percentage}"] = {
            "N": count,
            "positiveRate": rate,
            "enrichment": safe_ratio(rate, base_rate) if rate is not None and base_rate else None,
        }
        if target == "POLICY_GE_10":
            returns = [float(row["labels"]["policyReturnPct"]) for row in selected]
            output["buckets"][f"TOP_{percentage}"]["economics"] = economics(returns)
    remainder = ordered[top_count(len(ordered), 20):]
    remainder_rate = safe_ratio(sum(row["labels"][target] is True for row in remainder), len(remainder))
    output["buckets"]["REMAINING_80"] = {
        "N": len(remainder),
        "positiveRate": remainder_rate,
        "enrichment": safe_ratio(remainder_rate, base_rate) if remainder_rate is not None and base_rate else None,
    }
    if target == "POLICY_GE_10":
        output["buckets"]["REMAINING_80"]["economics"] = economics(
            [float(row["labels"]["policyReturnPct"]) for row in remainder]
        )
        all_returns = [float(row["labels"]["policyReturnPct"]) for row in ordered]
        output["allEconomics"] = economics(all_returns)
        top10 = ordered[:top_count(len(ordered), 10)]
        trimmed = sorted((float(row["labels"]["policyReturnPct"]) for row in top10), reverse=True)[2:]
        output["top10EconomicsAfterRemovingBestTwo"] = economics(trimmed)
    return output


def calibration_bins(labels: list[int], scores: list[float], count: int) -> list[dict[str, Any]]:
    bins = []
    for index in range(count):
        low = index / count
        high = (index + 1) / count
        members = [
            (label, score)
            for label, score in zip(labels, scores)
            if low <= score < high or (index == count - 1 and score == 1.0)
        ]
        bins.append({
            "bin": index + 1,
            "lowInclusive": low,
            "highExclusive": high if index < count - 1 else None,
            "N": len(members),
            "meanPrediction": statistics.mean(score for _, score in members) if members else None,
            "actualPositiveRate": statistics.mean(label for label, _ in members) if members else None,
        })
    return bins


def expected_calibration_error(bins: list[dict[str, Any]]) -> float | None:
    total = sum(row["N"] for row in bins)
    if not total:
        return None
    return sum(
        row["N"] * abs(row["meanPrediction"] - row["actualPositiveRate"])
        for row in bins if row["N"]
    ) / total


def metric_view(predictions: list[dict[str, Any]], target: str, roc_auc_score, average_precision_score, brier_score_loss, calibration_count: int, calibrate: bool = True) -> dict[str, Any]:
    usable = [row for row in predictions if row["labels"].get(target) is not None]
    labels = [int(row["labels"][target]) for row in usable]
    scores = [float(row["score"]) for row in usable]
    bins = calibration_bins(labels, scores, calibration_count) if calibrate else []
    ranked = ranking_summary(usable, target)
    ranked.update({
        "auc": float(roc_auc_score(labels, scores)) if len(set(labels)) == 2 else None,
        "prAuc": float(average_precision_score(labels, scores)) if len(set(labels)) == 2 else None,
        "brierScore": float(brier_score_loss(labels, scores)) if labels and calibrate else None,
        "expectedCalibrationError": expected_calibration_error(bins) if calibrate else None,
        "calibration": bins,
    })
    return ranked


def lift_curve(predictions: list[dict[str, Any]], percentages: list[float]) -> list[dict[str, Any]]:
    usable = [row for row in predictions if row["labels"].get("POLICY_GE_10") is not None]
    ordered = sorted_predictions(usable)
    base = safe_ratio(sum(row["labels"]["POLICY_GE_10"] is True for row in ordered), len(ordered))
    rows = []
    for percentage in percentages:
        count = top_count(len(ordered), percentage)
        selected = ordered[:count]
        rate = safe_ratio(sum(row["labels"]["POLICY_GE_10"] is True for row in selected), count)
        rows.append({"topPct": percentage, "N": count, "positiveRate": rate, "enrichment": safe_ratio(rate, base) if base else None})
    return rows


def paired_auc_bootstrap(predictions: list[dict[str, Any]], repeats: int, seed: int, roc_auc_score) -> dict[str, Any]:
    usable = [row for row in predictions if row["labels"].get("POLICY_GE_10") is not None]
    labels = [int(row["labels"]["POLICY_GE_10"]) for row in usable]
    model_scores = [float(row["score"]) for row in usable]
    baseline_scores = [float(row["features"]["net_external_flow_sol"]) for row in usable]
    rng = random.Random(seed)
    model_values = []
    baseline_values = []
    deltas = []
    for _ in range(repeats):
        indices = [rng.randrange(len(usable)) for _ in usable]
        sample_labels = [labels[index] for index in indices]
        if len(set(sample_labels)) < 2:
            continue
        model_auc = float(roc_auc_score(sample_labels, [model_scores[index] for index in indices]))
        baseline_auc = float(roc_auc_score(sample_labels, [baseline_scores[index] for index in indices]))
        model_values.append(model_auc)
        baseline_values.append(baseline_auc)
        deltas.append(model_auc - baseline_auc)
    def interval(values: list[float]) -> list[float | None]:
        return [percentile(values, 0.025), percentile(values, 0.975)]
    return {
        "replicatesRequested": repeats,
        "replicatesUsable": len(deltas),
        "modelAuc95CI": interval(model_values),
        "baselineAuc95CI": interval(baseline_values),
        "modelMinusBaselineAuc95CI": interval(deltas),
    }


def load_rows(path: pathlib.Path, contract: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        header = json.loads(next(handle))["header"]
        rows = [json.loads(line) for line in handle if line.strip()]
    expected = contract["parents"]
    if sha256_file(path) != expected["sameAnchorAnalysisRowsSha256"]:
        raise RuntimeError("ANALYSIS_ROWS_HASH_MISMATCH")
    if header.get("contractSha256") != expected["sameAnchorContractSha256"]:
        raise RuntimeError("PARENT_CONTRACT_HASH_MISMATCH")
    filtered = [
        row for row in rows
        if row["anchor"] == contract["population"]["anchor"]
        and contract["population"]["discoveryOrdinalStart"] <= int(row["ordinal"]) <= contract["population"]["discoveryOrdinalEnd"]
    ]
    if len(filtered) != len({row["mint"] for row in filtered}):
        raise RuntimeError("DUPLICATE_PRIMARY_MINT")
    if any(not row["availability"].get("causalAvailabilityPass") for row in filtered):
        raise RuntimeError("CAUSAL_AVAILABILITY_FAILURE")
    if any((row["availability"].get("sameTimestampPostTriggerRowsExcluded") or 0) < 0 for row in filtered):
        raise RuntimeError("INVALID_TRIGGER_BOUNDARY_AUDIT")
    if any(
        abs(
            float(row["features"]["actual_cumulative_external_gross_buy_sol"])
            - float(row["features"]["overshoot_sol"])
            - 2.0
        ) > 1e-9
        for row in filtered
    ):
        raise RuntimeError("CORRECTED_TRIGGER_BOUNDARY_IDENTITY_FAILURE")
    return header, sorted(filtered, key=lambda row: (int(row["ordinal"]), row["mint"]))


def matrix(rows: list[dict[str, Any]], features: list[str], np):
    return np.asarray([
        [float(row["features"][feature]) if row["features"].get(feature) is not None else np.nan for feature in features]
        for row in rows
    ], dtype=float)


def train_model(configuration: dict[str, Any], HistGradientBoostingClassifier):
    return HistGradientBoostingClassifier(**configuration)


def fold_partial_dependence(model, test_x, train_x, features: list[str], quantiles: list[float], np) -> dict[str, Any]:
    output = {}
    for feature_index, feature in enumerate(features):
        values = train_x[:, feature_index]
        values = values[~np.isnan(values)]
        grid = [float(np.quantile(values, probability)) for probability in quantiles]
        means = []
        for value in grid:
            modified = test_x.copy()
            modified[:, feature_index] = value
            means.append(float(np.mean(model.predict_proba(modified)[:, 1])))
        output[feature] = {"quantiles": quantiles, "featureValues": grid, "meanPredictions": means}
    return output


def fold_interaction(model, test_x, train_x, features: list[str], quantiles: list[float], np) -> dict[str, Any] | None:
    left_name = "net_external_flow_sol"
    right_name = "buy_size_progression_ratio"
    if left_name not in features or right_name not in features:
        return None
    left_index = features.index(left_name)
    right_index = features.index(right_name)
    left_values = train_x[:, left_index]
    right_values = train_x[:, right_index]
    left_values = left_values[~np.isnan(left_values)]
    right_values = right_values[~np.isnan(right_values)]
    left_grid = [float(np.quantile(left_values, q)) for q in quantiles]
    right_grid = [float(np.quantile(right_values, q)) for q in quantiles]
    grid = []
    for left in left_grid:
        row = []
        for right in right_grid:
            modified = test_x.copy()
            modified[:, left_index] = left
            modified[:, right_index] = right
            row.append(float(np.mean(model.predict_proba(modified)[:, 1])))
        grid.append(row)
    array = np.asarray(grid)
    grand = float(np.mean(array))
    additive = np.mean(array, axis=1)[:, None] + np.mean(array, axis=0)[None, :] - grand
    numerator = float(np.sum((array - additive) ** 2))
    denominator = float(np.sum((array - grand) ** 2))
    return {
        "left": left_name,
        "right": right_name,
        "quantiles": quantiles,
        "leftValues": left_grid,
        "rightValues": right_grid,
        "meanPredictionGrid": grid,
        "friedmanHApprox": math.sqrt(numerator / denominator) if denominator else 0.0,
    }


def aggregate_partial_dependence(folds: list[dict[str, Any]], features: list[str]) -> dict[str, Any]:
    output = {}
    for feature in features:
        relevant = [fold for fold in folds if feature in fold["partialDependence"]]
        total = sum(fold["scoredTestN"] for fold in relevant)
        if not total:
            continue
        count = len(relevant[0]["partialDependence"][feature]["quantiles"])
        output[feature] = {
            "quantiles": relevant[0]["partialDependence"][feature]["quantiles"],
            "weightedMeanFeatureValues": [
                sum(fold["scoredTestN"] * fold["partialDependence"][feature]["featureValues"][index] for fold in relevant) / total
                for index in range(count)
            ],
            "weightedMeanPredictions": [
                sum(fold["scoredTestN"] * fold["partialDependence"][feature]["meanPredictions"][index] for fold in relevant) / total
                for index in range(count)
            ],
        }
        predictions = output[feature]["weightedMeanPredictions"]
        output[feature]["p90MinusP10Prediction"] = predictions[-1] - predictions[0]
    return output


def write_gzip_rows(path: pathlib.Path, header: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"header": header}, sort_keys=True) + "\n")
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def markdown_report(report: dict[str, Any]) -> str:
    pooled = report["evaluation"]["primary"]
    baseline = report["evaluation"]["univariateReference"]
    top10 = pooled["buckets"]["TOP_10"]
    lines = [
        "# CONSTRAINED_CAUSAL_RANKER_V1",
        "",
        f"Final verdict: `{report['verdict']}`",
        "",
        "This is discovery-only forward-style evaluation, not a trading threshold or deployable strategy.",
        "",
        "## Inputs",
        "",
        f"Included: {', '.join(report['featureSelection']['included'])}",
        f"Excluded by frozen redundancy rule: {', '.join(report['featureSelection']['excluded']) or 'none'}",
        "",
        "## Pooled forward evaluation",
        "",
        "| Metric | Model | Net-flow reference |",
        "|---|---:|---:|",
        f"| AUC | {pooled['auc']:.3f} | {baseline['auc']:.3f} |",
        f"| PR-AUC | {pooled['prAuc']:.3f} | {baseline['prAuc']:.3f} |",
        f"| Top 10% positive rate | {top10['positiveRate']:.2%} | {baseline['buckets']['TOP_10']['positiveRate']:.2%} |",
        f"| Top 10% enrichment | {top10['enrichment']:.2f}x | {baseline['buckets']['TOP_10']['enrichment']:.2f}x |",
        "",
        "## Chronological blocks",
        "",
        "| Test block | Train ordinals | Test ordinals | N | AUC | Top-10 enrichment | Top-10 PF |",
        "|---:|---|---|---:|---:|---:|---:|",
    ]
    for fold in report["folds"]:
        metrics = fold["primaryMetrics"]
        economics_row = metrics["buckets"]["TOP_10"]["economics"]
        lines.append(
            f"| {fold['testBlock']} | {fold['trainOrdinalRange'][0]}-{fold['trainOrdinalRange'][1]} | "
            f"{fold['testOrdinalRange'][0]}-{fold['testOrdinalRange'][1]} | {metrics['N']} | "
            f"{metrics['auc']:.3f} | {metrics['buckets']['TOP_10']['enrichment']:.2f}x | "
            f"{economics_row['profitFactor'] if economics_row['profitFactor'] is not None else 'NA'} |"
        )
    lines += [
        "",
        "No validation or holdout rows were inspected. No threshold was selected and no shadow/live service was changed.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("analysis_rows", type=pathlib.Path)
    parser.add_argument("contract", type=pathlib.Path)
    parser.add_argument("feature_registry", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()

    import joblib
    import numpy as np
    import sklearn
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    registry = json.loads(args.feature_registry.read_text(encoding="utf-8"))
    contract_sha = sha256_file(args.contract)
    registry_sha = sha256_file(args.feature_registry)
    script_sha = sha256_file(pathlib.Path(__file__))
    header, rows = load_rows(args.analysis_rows, contract)

    candidate_features = list(contract["candidateFeatures"])
    correlations = [
        spearman(rows, candidate_features[left], candidate_features[right])
        for left in range(len(candidate_features))
        for right in range(left + 1, len(candidate_features))
    ]
    efficiency = "gross_buy_sol_per_successful_tx"
    strength = contract["redundancy"]["transactionEfficiencyComparedTo"]
    efficiency_pair = next(row for row in correlations if {row["left"], row["right"]} == {efficiency, strength})
    excluded = []
    if abs(float(efficiency_pair["spearmanRho"])) >= float(contract["redundancy"]["excludeTransactionEfficiencyAtAbsRhoGte"]):
        excluded.append(efficiency)
    features = [feature for feature in candidate_features if feature not in excluded]
    if len(features) > 4:
        raise RuntimeError("MAX_FEATURE_COUNT_EXCEEDED")

    configuration = dict(contract["model"]["configuration"])
    blocks = contract["evaluation"]["blocks"]
    predictions = []
    folds = []
    permutation_accumulator = {feature: [] for feature in features}
    for block_index in range(1, len(blocks)):
        train_end = blocks[block_index - 1][1]
        test_start, test_end = blocks[block_index]
        train_rows = [row for row in rows if int(row["ordinal"]) <= train_end and row["labels"].get("POLICY_GE_10") is not None]
        test_rows_all = [row for row in rows if test_start <= int(row["ordinal"]) <= test_end]
        test_rows = [row for row in test_rows_all if row["labels"].get("POLICY_GE_10") is not None]
        if len({bool(row["labels"]["POLICY_GE_10"]) for row in train_rows}) < 2:
            raise RuntimeError(f"TRAIN_CLASS_MISSING_BLOCK_{block_index + 1}")
        train_x = matrix(train_rows, features, np)
        test_x_all = matrix(test_rows_all, features, np)
        test_x = matrix(test_rows, features, np)
        train_y = np.asarray([int(row["labels"]["POLICY_GE_10"]) for row in train_rows], dtype=int)
        test_y = np.asarray([int(row["labels"]["POLICY_GE_10"]) for row in test_rows], dtype=int)
        model = train_model(configuration, HistGradientBoostingClassifier)
        model.fit(train_x, train_y)
        scores = model.predict_proba(test_x_all)[:, 1]
        fold_predictions = []
        for row, score in zip(test_rows_all, scores):
            prediction = {
                "schema": SCHEMA + "_OOF_PREDICTION",
                "mint": row["mint"],
                "ordinal": int(row["ordinal"]),
                "decisionUnixMs": int(row["decisionUnixMs"]),
                "testBlock": block_index + 1,
                "score": float(score),
                "features": {feature: row["features"].get(feature) for feature in features},
                "labels": row["labels"],
            }
            predictions.append(prediction)
            fold_predictions.append(prediction)
        primary_metrics = metric_view(fold_predictions, "POLICY_GE_10", roc_auc_score, average_precision_score, brier_score_loss, int(contract["evaluation"]["calibrationBins"]))
        permutation = permutation_importance(
            model,
            test_x,
            test_y,
            scoring="roc_auc",
            n_repeats=int(contract["evaluation"]["permutationRepeatsPerFold"]),
            random_state=int(contract["evaluation"]["bootstrapSeed"]) + block_index,
        )
        for feature, mean, deviation in zip(features, permutation.importances_mean, permutation.importances_std):
            permutation_accumulator[feature].append({"testBlock": block_index + 1, "aucDropMean": float(mean), "aucDropStd": float(deviation), "testN": len(test_rows)})
        folds.append({
            "testBlock": block_index + 1,
            "trainOrdinalRange": [blocks[0][0], train_end],
            "testOrdinalRange": [test_start, test_end],
            "trainN": len(train_rows),
            "trainPositiveN": int(sum(train_y)),
            "scoredTestN": len(test_rows_all),
            "testN": len(test_rows),
            "testPositiveN": int(sum(test_y)),
            "primaryMetrics": primary_metrics,
            "partialDependence": fold_partial_dependence(model, test_x_all, train_x, features, contract["evaluation"]["partialDependenceQuantiles"], np),
            "crossingProgressionInteraction": fold_interaction(model, test_x_all, train_x, features, contract["evaluation"]["partialDependenceQuantiles"], np),
        })

    primary = metric_view(predictions, "POLICY_GE_10", roc_auc_score, average_precision_score, brier_score_loss, int(contract["evaluation"]["calibrationBins"]))
    policy_positive = metric_view(predictions, "POLICY_POSITIVE", roc_auc_score, average_precision_score, brier_score_loss, int(contract["evaluation"]["calibrationBins"]))
    opportunity = metric_view(predictions, "OPPORTUNITY_GE_10", roc_auc_score, average_precision_score, brier_score_loss, int(contract["evaluation"]["calibrationBins"]))
    non_divergent_predictions = [row for row in predictions if row["labels"].get("counterfactualDiverged") is False]
    non_divergent = metric_view(non_divergent_predictions, "POLICY_GE_10", roc_auc_score, average_precision_score, brier_score_loss, int(contract["evaluation"]["calibrationBins"]))

    baseline_predictions = [dict(row, score=float(row["features"]["net_external_flow_sol"])) for row in predictions]
    baseline = metric_view(baseline_predictions, "POLICY_GE_10", roc_auc_score, average_precision_score, brier_score_loss, int(contract["evaluation"]["calibrationBins"]), calibrate=False)
    bootstrap = paired_auc_bootstrap(predictions, int(contract["evaluation"]["bootstrapReplicates"]), int(contract["evaluation"]["bootstrapSeed"]), roc_auc_score)
    partial = aggregate_partial_dependence(folds, features)
    permutation_summary = {}
    for feature, values in permutation_accumulator.items():
        total = sum(row["testN"] for row in values)
        permutation_summary[feature] = {
            "weightedMeanAucDrop": sum(row["testN"] * row["aucDropMean"] for row in values) / total,
            "folds": values,
        }
    interactions = [fold["crossingProgressionInteraction"] for fold in folds if fold["crossingProgressionInteraction"]]
    interaction_summary = {
        "weightedMeanFriedmanHApprox": sum(fold["scoredTestN"] * interaction["friedmanHApprox"] for fold, interaction in zip(folds, interactions)) / sum(fold["scoredTestN"] for fold in folds),
        "folds": [{"testBlock": fold["testBlock"], **interaction} for fold, interaction in zip(folds, interactions)],
    } if interactions else None

    gate = contract["freezeGate"]
    stable_blocks = sum(
        fold["primaryMetrics"]["auc"] > float(gate["stableBlockAucGt"])
        and fold["primaryMetrics"]["buckets"]["TOP_10"]["enrichment"] > float(gate["stableBlockTop10EnrichmentGt"])
        for fold in folds
    )
    top10 = primary["buckets"]["TOP_10"]
    trimmed_pf = primary["top10EconomicsAfterRemovingBestTwo"]["profitFactor"] or 0.0
    coherence = {
        feature: partial.get(feature, {}).get("p90MinusP10Prediction", 0.0) > 0
        for feature in gate["requiredPartialDependenceDirections"]
    }
    checks = {
        "pooledForwardAuc": primary["auc"] >= float(gate["pooledForwardAucGte"]),
        "pairedBootstrapAucDelta": bootstrap["modelMinusBaselineAuc95CI"][0] is not None and bootstrap["modelMinusBaselineAuc95CI"][0] > float(gate["pairedBootstrapAucDelta95CiLowerGt"]),
        "top10Enrichment": top10["enrichment"] >= float(gate["top10EnrichmentGte"]),
        "chronologicalStability": stable_blocks >= int(gate["stableEvaluationBlocksRequired"]),
        "nonDivergentAuc": non_divergent["auc"] >= float(gate["nonDivergentAucGte"]),
        "nonDivergentTop10Enrichment": non_divergent["buckets"]["TOP_10"]["enrichment"] >= float(gate["nonDivergentTop10EnrichmentGte"]),
        "top10NotBestTwoDriven": trimmed_pf > float(gate["top10PfAfterRemovingBestTwoGt"]),
        "coherentPartialDependence": all(coherence.values()),
    }
    if all(checks.values()):
        verdict = "MULTIVARIATE_SELECTOR_FREEZE_JUSTIFIED"
    elif primary["auc"] > float(contract["reference"]["correctedDiscoveryAuc"]) or top10["enrichment"] >= 1.5:
        verdict = "MULTIVARIATE_SIGNAL_PROMISING_BUT_NOT_FREEZE_READY"
    else:
        verdict = "MULTIVARIATE_SIGNAL_NOT_USEFUL"

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "contract.json").write_bytes(args.contract.read_bytes())
    (args.output / "feature-registry.json").write_bytes(args.feature_registry.read_bytes())
    oof_path = args.output / "oof-predictions.jsonl.gz"
    write_gzip_rows(oof_path, {
        "schema": SCHEMA + "_OOF_PREDICTIONS",
        "contractSha256": contract_sha,
        "featureRegistrySha256": registry_sha,
        "scriptSha256": script_sha,
        "rowCount": len(predictions),
    }, sorted(predictions, key=lambda row: (row["ordinal"], row["mint"])))

    model_artifact = None
    if verdict == "MULTIVARIATE_SELECTOR_FREEZE_JUSTIFIED":
        final_rows = [row for row in rows if row["labels"].get("POLICY_GE_10") is not None]
        final_model = train_model(configuration, HistGradientBoostingClassifier)
        final_model.fit(matrix(final_rows, features, np), np.asarray([int(row["labels"]["POLICY_GE_10"]) for row in final_rows], dtype=int))
        model_path = args.output / "model.joblib"
        joblib.dump({"model": final_model, "features": features, "configuration": configuration}, model_path, compress=3)
        model_artifact = {"path": str(model_path), "sha256": sha256_file(model_path), "trainedN": len(final_rows)}

    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": iso_now(),
        "verdict": verdict,
        "contractSha256": contract_sha,
        "featureRegistrySha256": registry_sha,
        "scriptSha256": script_sha,
        "inputAnalysisRowsSha256": sha256_file(args.analysis_rows),
        "parentHeader": header,
        "population": {
            "anchorRows": len(rows),
            "uniqueMints": len({row["mint"] for row in rows}),
            "primaryLabelN": sum(row["labels"].get("POLICY_GE_10") is not None for row in rows),
            "block1EvaluationN": 0,
            "forwardEvaluationN": len(predictions),
        },
        "featureSelection": {"candidates": candidate_features, "included": features, "excluded": excluded, "pairwiseCorrelations": correlations},
        "model": {"libraryVersion": sklearn.__version__, "class": contract["model"]["class"], "configuration": configuration, "artifact": model_artifact},
        "folds": folds,
        "evaluation": {
            "primary": primary,
            "policyPositiveSecondary": policy_positive,
            "opportunityGe10Secondary": opportunity,
            "nonDivergentOnly": non_divergent,
            "univariateReference": baseline,
            "pairedMintBootstrap": bootstrap,
            "liftCurve": lift_curve(predictions, contract["evaluation"]["liftCurvePct"]),
        },
        "featureContribution": {"permutationImportance": permutation_summary, "partialDependence": partial, "crossingProgressionInteraction": interaction_summary},
        "freezeGate": {"criteria": gate, "checks": checks, "stableEvaluationBlocks": stable_blocks, "coherence": coherence},
        "integrity": {
            "validationRowsInspected": 0,
            "holdoutRowsInspected": 0,
            "thresholdsSearched": 0,
            "featureCombinationsTested": 1,
            "modelFamiliesTested": 1,
            "futureFoldTrainingViolations": 0,
            "shadowOrLiveChanges": 0,
            "submissions": 0,
            "duplicateOofMints": len(predictions) - len({row["mint"] for row in predictions}),
        },
    }
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path = args.output / "report.md"
    markdown_path.write_text(markdown_report(report), encoding="utf-8")
    provenance = {
        "schema": SCHEMA + "_PROVENANCE",
        "contractSha256": contract_sha,
        "featureRegistrySha256": registry_sha,
        "scriptSha256": script_sha,
        "inputAnalysisRowsSha256": sha256_file(args.analysis_rows),
        "oofPredictionsSha256": sha256_file(oof_path),
        "reportSha256": sha256_file(report_path),
        "modelArtifact": model_artifact,
        "validationRowsInspected": 0,
        "holdoutRowsInspected": 0,
    }
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"complete": True, "verdict": verdict, "forwardN": len(predictions), "report": str(report_path)}), flush=True)


if __name__ == "__main__":
    main()
