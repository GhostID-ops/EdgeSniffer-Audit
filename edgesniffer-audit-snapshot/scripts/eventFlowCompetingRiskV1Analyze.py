#!/usr/bin/env python3
"""Analyze the preregistered EVENT_FLOW_COMPETING_RISK_V1 rows."""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import math
import pathlib
import random
import statistics
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


SCHEMA = "EVENT_FLOW_COMPETING_RISK_V1"
CLASS_NAMES = ("ADVERSE_FLOW", "FLOW_DECAY_TIMEOUT", "PROFITABLE_CONTINUATION")
CLASS_TO_INT = {name: index for index, name in enumerate(CLASS_NAMES)}


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def read_rows(root: pathlib.Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((root / "partitions").glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            next(handle)
            for line in handle:
                mint_row = json.loads(line)
                for state in mint_row.get("decisionStates", []):
                    target = state.get("target") or {}
                    if state.get("status") != "EXACT" or target.get("status") != "EXACT":
                        continue
                    rows.append({
                        "mint": mint_row["mint"], "ordinal": int(mint_row["ordinal"]),
                        "stateIndex": int(state["stateIndex"]), "decisionUnixMs": int(state["decisionUnixMs"]),
                        "mintAgeMs": int(state["mintAgeMs"]), "features": state["features"],
                        "outcome": target["outcome"], "returnPct": float(target["returnPct"]),
                        "pnlSol": float(target["pnlSol"]), "timeToOutcomeMs": int(target["timeToOutcomeMs"]),
                        "entryInventoryDeteriorationPct": target.get("entryInventoryDeteriorationPct"),
                        "counterfactualFailedTransactions": int(target.get("counterfactualFailedTransactions") or 0),
                    })
    return sorted(rows, key=lambda row: (row["ordinal"], row["decisionUnixMs"], row["stateIndex"]))


def matrix(rows: list[dict[str, Any]], features: list[str]) -> np.ndarray:
    return np.asarray([
        [np.nan if row["features"].get(feature) is None else float(row["features"][feature]) for feature in features]
        for row in rows
    ], dtype=float)


def labels(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([CLASS_TO_INT[row["outcome"]] for row in rows], dtype=int)


def mint_weights(rows: list[dict[str, Any]]) -> np.ndarray:
    counts = collections.Counter(row["mint"] for row in rows)
    return np.asarray([1.0 / counts[row["mint"]] for row in rows], dtype=float)


def align_probabilities(model: Any, raw: np.ndarray) -> np.ndarray:
    output = np.zeros((raw.shape[0], len(CLASS_NAMES)), dtype=float)
    for source, class_id in enumerate(model.classes_):
        output[:, int(class_id)] = raw[:, source]
    return output


def metric_set(y: np.ndarray, probabilities: np.ndarray, weights: np.ndarray | None = None) -> dict[str, Any]:
    predicted = probabilities.argmax(axis=1)
    try:
        macro_auc = float(roc_auc_score(y, probabilities, labels=list(range(len(CLASS_NAMES))), multi_class="ovr", average="macro", sample_weight=weights))
    except ValueError:
        macro_auc = None
    try:
        profit_auc = float(roc_auc_score((y == CLASS_TO_INT["PROFITABLE_CONTINUATION"]).astype(int), probabilities[:, CLASS_TO_INT["PROFITABLE_CONTINUATION"]], sample_weight=weights))
    except ValueError:
        profit_auc = None
    return {
        "N": int(len(y)), "macroOvrAuc": macro_auc, "profitableContinuationAuc": profit_auc,
        "multiclassLogLoss": float(log_loss(y, probabilities, labels=list(range(len(CLASS_NAMES))), sample_weight=weights)),
        "accuracy": float(accuracy_score(y, predicted, sample_weight=weights)),
    }


def hgb(contract: dict[str, Any]) -> HistGradientBoostingClassifier:
    cfg = contract["models"]["primary"]
    return HistGradientBoostingClassifier(
        loss="log_loss", learning_rate=float(cfg["learningRate"]), max_iter=int(cfg["maxIter"]),
        max_leaf_nodes=int(cfg["maxLeafNodes"]), max_depth=int(cfg["maxDepth"]),
        min_samples_leaf=int(cfg["minSamplesLeaf"]), l2_regularization=float(cfg["l2Regularization"]),
        max_bins=int(cfg["maxBins"]), early_stopping=False, random_state=int(cfg["randomState"]),
    )


def logistic(contract: dict[str, Any]) -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
        ("scale", StandardScaler()),
        ("model", LogisticRegression(C=1.0, max_iter=1000, solver="lbfgs", class_weight="balanced", random_state=int(contract["models"]["primary"]["randomState"]))),
    ])


def economics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(row["pnlSol"]) for row in rows]
    returns = [float(row["returnPct"]) for row in rows]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    ordered = sorted(rows, key=lambda row: row["pnlSol"], reverse=True)
    without_two = ordered[2:] if len(ordered) > 2 else []
    reduced_wins = [float(row["pnlSol"]) for row in without_two if row["pnlSol"] > 0]
    reduced_losses = [float(row["pnlSol"]) for row in without_two if row["pnlSol"] < 0]
    return {
        "N": len(rows), "uniqueMintN": len({row["mint"] for row in rows}),
        "wins": len(wins), "losses": len(losses), "WR": len(wins) / len(rows) if rows else None,
        "PF": sum(wins) / abs(sum(losses)) if losses else None,
        "expectancySol": statistics.mean(pnls) if pnls else None, "totalPnlSol": sum(pnls),
        "medianReturnPct": statistics.median(returns) if returns else None,
        "bestReturnPct": max(returns) if returns else None, "worstReturnPct": min(returns) if returns else None,
        "PFAfterRemovingBestTwo": sum(reduced_wins) / abs(sum(reduced_losses)) if reduced_losses else None,
        "outcomes": dict(collections.Counter(row["outcome"] for row in rows)),
        "medianMintAgeMs": statistics.median(row["mintAgeMs"] for row in rows) if rows else None,
        "P75MintAgeMs": quantile([float(row["mintAgeMs"]) for row in rows], 0.75),
    }


def bootstrap_delta(predictions: list[dict[str, Any]], event_key: str, static_key: str, replicates: int, seed: int) -> list[float | None]:
    by_mint: dict[str, list[int]] = collections.defaultdict(list)
    for index, row in enumerate(predictions):
        by_mint[row["mint"]].append(index)
    mints = sorted(by_mint)
    y = np.asarray([row["y"] for row in predictions])
    event = np.asarray([row["probabilities"][event_key] for row in predictions])
    static = np.asarray([row["probabilities"][static_key] for row in predictions])
    rng = random.Random(seed)
    deltas = []
    for _ in range(replicates):
        multiplicity = collections.Counter(rng.choice(mints) for _ in mints)
        weights = np.zeros(len(predictions), dtype=float)
        for mint, count in multiplicity.items():
            for index in by_mint[mint]:
                weights[index] = count / len(by_mint[mint])
        left = metric_set(y, event, weights)["macroOvrAuc"]
        right = metric_set(y, static, weights)["macroOvrAuc"]
        if left is not None and right is not None:
            deltas.append(left - right)
    return [quantile(deltas, 0.025), quantile(deltas, 0.975)]


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# EVENT_FLOW_COMPETING_RISK_V1", "", f"## {report['verdict']}", "",
        f"Exact states: `{report['population']['exactStateN']}` across `{report['population']['exactMintN']}` mints.", "",
        "| Model | Event macro AUC | Mint-weighted macro AUC | Profit AUC | Mint-weighted log loss |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, row in report["models"].items():
        lines.append(f"| {name} | {(row['event']['macroOvrAuc'] or 0):.4f} | {(row['mintClustered']['macroOvrAuc'] or 0):.4f} | {(row['mintClustered']['profitableContinuationAuc'] or 0):.4f} | {row['mintClustered']['multiclassLogLoss']:.4f} |")
    lines += ["", f"Strongest static baseline: `{report['incrementalSignal']['strongestStaticBaseline']}`.", "", "No validation, holdout, fresh data, shadow trading, or live trading was used.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=pathlib.Path)
    parser.add_argument("contract", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema") != SCHEMA + "_CONTRACT":
        raise RuntimeError("CONTRACT_SCHEMA_MISMATCH")
    rows = read_rows(args.input)
    all_features = list(contract["features"])
    static = contract["staticBaselines"]
    model_features = {
        "CUMULATIVE_NET_FLOW_HGB": static["cumulativeNetFlowOnly"],
        "CURVE_STATE_HGB": static["curveStateOnly"],
        "TOKEN_AGE_HGB": static["tokenAgeOnly"],
        "COMBINED_STATIC_HGB": static["combinedStatic"],
        "EVENT_FULL_HGB": all_features,
        "EVENT_FULL_LOGISTIC": all_features,
    }
    blocks = contract["population"]["chronologicalBlocks"]
    predictions: dict[tuple[str, int, int], dict[str, Any]] = {}
    fold_models: dict[int, Any] = {}
    cutoffs: dict[int, dict[str, float]] = {}
    permutation_rows = []
    rng = np.random.default_rng(int(contract["models"]["primary"]["randomState"]))
    for fold_index, bounds in enumerate(blocks[1:], 2):
        train = [row for row in rows if row["ordinal"] < bounds[0]]
        test = [row for row in rows if bounds[0] <= row["ordinal"] <= bounds[1]]
        y_train, y_test = labels(train), labels(test)
        train_weights = mint_weights(train)
        fitted = {}
        for name, features in model_features.items():
            model = logistic(contract) if name.endswith("LOGISTIC") else hgb(contract)
            fit_kwargs = {"model__sample_weight": train_weights} if name.endswith("LOGISTIC") else {"sample_weight": train_weights}
            model.fit(matrix(train, features), y_train, **fit_kwargs)
            fitted[name] = model
            probabilities = align_probabilities(model[-1] if name.endswith("LOGISTIC") else model, model.predict_proba(matrix(test, features)))
            for row, y_value, probs in zip(test, y_test, probabilities):
                key = (row["mint"], row["stateIndex"], fold_index)
                item = predictions.setdefault(key, {**row, "fold": fold_index, "y": int(y_value), "probabilities": {}})
                item["probabilities"][name] = probs.tolist()
        full_model = fitted["EVENT_FULL_HGB"]
        train_prob = align_probabilities(full_model, full_model.predict_proba(matrix(train, all_features)))[:, CLASS_TO_INT["PROFITABLE_CONTINUATION"]]
        cutoffs[fold_index] = {str(int(fraction * 100)): float(np.quantile(train_prob, 1 - fraction)) for fraction in contract["economicDiagnosticIfMaterial"]["buckets"]}
        base_probs = align_probabilities(full_model, full_model.predict_proba(matrix(test, all_features)))
        base_auc = metric_set(y_test, base_probs, mint_weights(test))["macroOvrAuc"]
        x_test = matrix(test, all_features)
        for group_name, group_features in contract["incrementalGroups"].items():
            indexes = [all_features.index(feature) for feature in group_features]
            aucs = []
            for _ in range(int(contract["evaluation"]["permutationRepeats"])):
                changed = x_test.copy()
                order = rng.permutation(len(test))
                changed[:, indexes] = changed[order][:, indexes]
                perm_probs = align_probabilities(full_model, full_model.predict_proba(changed))
                value = metric_set(y_test, perm_probs, mint_weights(test))["macroOvrAuc"]
                if value is not None:
                    aucs.append(value)
            permutation_rows.append({"fold": fold_index, "group": group_name, "baseAuc": base_auc, "permutedAucMean": statistics.mean(aucs), "meanAucDrop": base_auc - statistics.mean(aucs)})
        fold_models[fold_index] = full_model

    prediction_rows = list(predictions.values())
    y_all = np.asarray([row["y"] for row in prediction_rows])
    weights_all = mint_weights(prediction_rows)
    model_report = {}
    for name in model_features:
        probs = np.asarray([row["probabilities"][name] for row in prediction_rows])
        model_report[name] = {"event": metric_set(y_all, probs), "mintClustered": metric_set(y_all, probs, weights_all)}
    static_names = ("CUMULATIVE_NET_FLOW_HGB", "CURVE_STATE_HGB", "TOKEN_AGE_HGB", "COMBINED_STATIC_HGB")
    strongest = max(static_names, key=lambda name: model_report[name]["mintClustered"]["macroOvrAuc"] or -1)
    event_metrics = model_report["EVENT_FULL_HGB"]["mintClustered"]
    static_metrics = model_report[strongest]["mintClustered"]
    auc_delta = event_metrics["macroOvrAuc"] - static_metrics["macroOvrAuc"]
    loss_delta = static_metrics["multiclassLogLoss"] - event_metrics["multiclassLogLoss"]
    fold_deltas = []
    for fold_index in range(2, 6):
        subset = [row for row in prediction_rows if row["fold"] == fold_index]
        y = np.asarray([row["y"] for row in subset])
        weights = mint_weights(subset)
        event_auc = metric_set(y, np.asarray([row["probabilities"]["EVENT_FULL_HGB"] for row in subset]), weights)["macroOvrAuc"]
        static_auc = metric_set(y, np.asarray([row["probabilities"][strongest] for row in subset]), weights)["macroOvrAuc"]
        fold_deltas.append({"fold": fold_index, "ordinalRange": blocks[fold_index - 1], "eventAuc": event_auc, "staticAuc": static_auc, "delta": event_auc - static_auc})
    bootstrap_ci = bootstrap_delta(prediction_rows, "EVENT_FULL_HGB", strongest, int(contract["evaluation"]["clusterBootstrapReplicates"]), int(contract["evaluation"]["bootstrapSeed"]))
    state_counts = collections.Counter(row["mint"] for row in prediction_rows)
    top_n = max(1, math.ceil(len(state_counts) * 0.01))
    dominant = {mint for mint, _ in state_counts.most_common(top_n)}
    reduced = [row for row in prediction_rows if row["mint"] not in dominant]
    reduced_y, reduced_weights = np.asarray([row["y"] for row in reduced]), mint_weights(reduced)
    reduced_event = metric_set(reduced_y, np.asarray([row["probabilities"]["EVENT_FULL_HGB"] for row in reduced]), reduced_weights)
    reduced_static = metric_set(reduced_y, np.asarray([row["probabilities"][strongest] for row in reduced]), reduced_weights)
    reduced_delta = reduced_event["macroOvrAuc"] - reduced_static["macroOvrAuc"]
    rules = contract["evaluation"]["materialIncrementalSignal"]
    positive_blocks = sum(row["delta"] > 0 for row in fold_deltas)
    material = (
        auc_delta >= float(rules["primaryModelMintWeightedMacroAucDeltaVsStrongestStaticGte"])
        and bootstrap_ci[0] is not None and bootstrap_ci[0] > 0
        and loss_delta > 0
        and positive_blocks >= int(rules["positiveAucDeltaBlocksRequired"])
        and reduced_delta >= 0.01
    )

    grouped_permutation = {}
    for name in contract["incrementalGroups"]:
        values = [row["meanAucDrop"] for row in permutation_rows if row["group"] == name]
        grouped_permutation[name] = {"folds": len(values), "meanMacroAucDrop": statistics.mean(values), "positiveDropFolds": sum(value > 0 for value in values)}

    entropy_rows = []
    for row in prediction_rows:
        probs = row["probabilities"]["EVENT_FULL_HGB"]
        entropy = -sum(value * math.log(value) for value in probs if value > 0)
        entropy_rows.append({**row, "entropy": entropy})
    entropy_rows.sort(key=lambda row: row["entropy"])
    tail_n = max(1, math.ceil(len(entropy_rows) * 0.10))
    uncertainty = {}
    for name, subset in (("lowest10pct", entropy_rows[:tail_n]), ("highest10pct", entropy_rows[-tail_n:])):
        uncertainty[name] = {
            "N": len(subset), "uniqueMintN": len({row["mint"] for row in subset}),
            "accuracy": sum(int(np.argmax(row["probabilities"]["EVENT_FULL_HGB"]) == row["y"]) for row in subset) / len(subset),
            "outcomes": dict(collections.Counter(row["outcome"] for row in subset)),
            "medianMintAgeMs": statistics.median(row["mintAgeMs"] for row in subset),
            "medianEntropy": statistics.median(row["entropy"] for row in subset),
        }

    economic_report = None
    actionable = False
    if material:
        buckets = {}
        for fraction in contract["economicDiagnosticIfMaterial"]["buckets"]:
            label = str(int(fraction * 100))
            qualified = [
                row for row in prediction_rows
                if row["probabilities"]["EVENT_FULL_HGB"][CLASS_TO_INT["PROFITABLE_CONTINUATION"]] >= cutoffs[row["fold"]][label]
            ]
            first_by_mint = {}
            for row in sorted(qualified, key=lambda item: (item["decisionUnixMs"], item["mint"], item["stateIndex"])):
                first_by_mint.setdefault(row["mint"], row)
            first = list(first_by_mint.values())
            blocks_econ = []
            for fold_index in range(2, 6):
                subset = [row for row in first if row["fold"] == fold_index]
                blocks_econ.append({"fold": fold_index, **economics(subset)})
            buckets[f"top{label}pct"] = {"allQualifyingStates": economics(qualified), "firstQualifyingStatePerMint": economics(first), "chronological": blocks_econ, "foldScoreCutoffs": {str(fold): cutoffs[fold][label] for fold in cutoffs}}
        economic_report = buckets
        top10 = buckets["top10pct"]["firstQualifyingStatePerMint"]
        gate = contract["economicDiagnosticIfMaterial"]["actionableGate"]
        top10_blocks = buckets["top10pct"]["chronological"]
        actionable = (
            top10["N"] >= int(gate["top10FirstPerMintMinimumN"])
            and top10["PF"] is not None and top10["PF"] > float(gate["PFAbove"])
            and top10["expectancySol"] > float(gate["expectancySolAbove"])
            and top10["PFAfterRemovingBestTwo"] is not None and top10["PFAfterRemovingBestTwo"] > float(gate["PFAfterRemovingBestTwoAbove"])
            and sum((row["expectancySol"] or 0) > 0 for row in top10_blocks) >= int(gate["positiveChronologicalEvaluationBlocksRequired"])
        )

    if not material:
        verdict = "EVENT_FLOW_HAS_NO_USEFUL_INCREMENTAL_SIGNAL"
        next_block = None
    elif not actionable:
        verdict = "EVENT_FLOW_SIGNAL_EXISTS_BUT_NOT_ECONOMICALLY_ACTIONABLE"
        next_block = None
    else:
        verdict = "EVENT_FLOW_SIGNAL_JUSTIFIES_DEEPER_RESEARCH"
        best_group = max(grouped_permutation, key=lambda name: grouped_permutation[name]["meanMacroAucDrop"])
        next_block = "inventory/sell-overhang hazard" if best_group == "recentSelling" else "flow-intensity/survival refinement" if best_group in {"timing", "acceleration"} else "cross-sectional ranking"

    report = {
        "schema": SCHEMA + "_REPORT", "verdict": verdict, "nextBlockIfJustified": next_block,
        "contractSha256": sha256_file(args.contract), "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "population": {
            "exactStateN": len(rows), "exactMintN": len({row["mint"] for row in rows}),
            "forwardScoredStateN": len(prediction_rows), "forwardScoredMintN": len({row["mint"] for row in prediction_rows}),
            "outcomes": dict(collections.Counter(row["outcome"] for row in rows)),
            "counterfactualDivergentStateN": sum(row["counterfactualFailedTransactions"] > 0 for row in rows),
        },
        "models": model_report,
        "incrementalSignal": {
            "strongestStaticBaseline": strongest, "mintWeightedMacroAucDelta": auc_delta,
            "mintWeightedLogLossImprovement": loss_delta, "mintClusterBootstrapDelta95CI": bootstrap_ci,
            "positiveDeltaBlocks": positive_blocks, "requiredPositiveBlocks": rules["positiveAucDeltaBlocksRequired"],
            "chronological": fold_deltas, "afterRemovingTop1PctStateCountMintsDelta": reduced_delta,
            "materialGatePassed": material,
        },
        "eventInformation": grouped_permutation,
        "uncertainty": uncertainty,
        "economicDiagnosticRun": material,
        "economicDiagnostic": economic_report,
        "economicallyActionableGatePassed": actionable,
        "validationRowsInspected": 0, "holdoutRowsInspected": 0, "freshOrdinalsInspected": 0,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output / "report.md").write_text(markdown(report), encoding="utf-8")
    with gzip.open(args.output / "forward-predictions.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in prediction_rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    provenance = {
        "schema": SCHEMA + "_PROVENANCE", "contractSha256": report["contractSha256"],
        "buildProvenanceSha256": sha256_file(args.input / "build-provenance.json"),
        "analysisScriptSha256": report["scriptSha256"], "reportSha256": sha256_file(report_path),
        "validationRowsInspected": 0, "holdoutRowsInspected": 0,
    }
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": verdict, "population": report["population"], "incrementalSignal": report["incrementalSignal"], "eventInformation": grouped_permutation, "economicDiagnosticRun": material}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
