#!/usr/bin/env python3
"""Chronological, outcome-blind feasibility test for rich pre-entry kEfi state.

This is intentionally an exploratory diagnostic.  It fits only on the early
portion of a reconstructed corpus and evaluates once on the later portion.
Its threshold is selected from training rows only.  It does not read exits,
outcomes, or PnL and cannot authorize execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


RAW_FEATURES = (
    "log_pre_pool_quote", "log_pre_pool_base", "amm_fee_bps", "observed_pool_age_seconds",
    "seconds_since_previous_observed_buy", "prior_pre_pool_quote_log_change",
    "prior_buy_count_5s", "prior_buy_count_20s", "prior_buy_count_60s",
    "prior_buy_quote_5s", "prior_buy_quote_20s", "prior_buy_quote_60s",
)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-min(value, 60)))
    e = math.exp(max(value, -60))
    return e / (1 + e)


def vector(pre: dict) -> list[float]:
    prior_gap = pre.get("seconds_since_previous_observed_buy")
    return [
        float(pre["log_pre_pool_quote"]), float(pre["log_pre_pool_base"]), float(pre["amm_fee_bps"]),
        math.log1p(min(float(pre["observed_pool_age_seconds"]), 86_400)),
        math.log1p(min(float(prior_gap) if prior_gap is not None else 86_400, 86_400)),
        float(pre["prior_pre_pool_quote_log_change"]),
        *[math.log1p(float(pre[f"prior_buy_count_{seconds}s"])) for seconds in (5, 20, 60)],
        *[math.log1p(float(pre[f"prior_buy_quote_{seconds}s"])) for seconds in (5, 20, 60)],
        1.0 if prior_gap is None else 0.0,
    ]


FEATURES = RAW_FEATURES + ("no_prior_observed_buy",)


def load(path: Path) -> list[dict]:
    rows = []
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("schema") != "KEFI_RICH_PREENTRY_RECONSTRUCTION_V1":
                raise ValueError("FAIL_CLOSED: unexpected reconstruction schema")
            rows.append({"label": int(item["label"]), "time": int(item["received_at_unix_ns"]), "identity": item["identity"], "x": vector(item["preentry"])})
    if not rows or not any(row["label"] for row in rows) or all(row["label"] for row in rows):
        raise ValueError("FAIL_CLOSED: both classes required")
    return rows


def standardize(train: list[dict], rows: list[dict]):
    means, scales = [], []
    for idx in range(len(FEATURES)):
        values = [row["x"][idx] for row in train]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        means.append(mean); scales.append(math.sqrt(variance) or 1.0)
    for row in rows:
        row["z"] = [(value - means[idx]) / scales[idx] for idx, value in enumerate(row["x"])]
    return means, scales


def fit(rows: list[dict], epochs: int = 450):
    positives = sum(row["label"] for row in rows); negatives = len(rows) - positives
    class_weight = (len(rows) / (2 * positives), len(rows) / (2 * negatives))
    beta, intercept, rate = [0.0] * len(FEATURES), 0.0, 0.035
    for epoch in range(epochs):
        grad, intercept_grad = [0.0] * len(FEATURES), 0.0
        for row in rows:
            predicted = sigmoid(intercept + sum(weight * value for weight, value in zip(beta, row["z"])))
            error = (predicted - row["label"]) * class_weight[row["label"]]
            intercept_grad += error
            for idx, value in enumerate(row["z"]): grad[idx] += error * value
        inv = 1 / len(rows)
        intercept -= rate * intercept_grad * inv
        for idx in range(len(beta)): beta[idx] -= rate * (grad[idx] * inv + 0.002 * beta[idx])
        if epoch == 225: rate = 0.012
    return beta, intercept


def scores(rows: list[dict], beta: list[float], intercept: float):
    return [sigmoid(intercept + sum(weight * value for weight, value in zip(beta, row["z"]))) for row in rows]


def auc(rows, values):
    positive = [score for row, score in zip(rows, values) if row["label"]]
    negative = [score for row, score in zip(rows, values) if not row["label"]]
    wins = sum(sum(score > other for other in negative) + .5 * sum(score == other for other in negative) for score in positive)
    return wins / (len(positive) * len(negative))


def metrics(rows, values, threshold):
    tp = sum(row["label"] and score >= threshold for row, score in zip(rows, values)); fn = sum(row["label"] and score < threshold for row, score in zip(rows, values))
    tn = sum(not row["label"] and score < threshold for row, score in zip(rows, values)); fp = sum(not row["label"] and score >= threshold for row, score in zip(rows, values))
    sensitivity = tp / (tp + fn) if tp + fn else 0; specificity = tn / (tn + fp) if tn + fp else 0
    return {"rows": len(rows), "positives": tp + fn, "negatives": tn + fp, "auc": auc(rows, values), "threshold": threshold,
            "balanced_accuracy": .5 * (sensitivity + specificity), "sensitivity": sensitivity, "specificity": specificity,
            "confusion": {"tp": tp, "fn": fn, "tn": tn, "fp": fp}}


def choose_threshold(rows, values):
    # A full unique-score scan is quadratic because each candidate requires a
    # confusion-matrix pass.  Use a deterministic, training-only quantile grid
    # instead; this is a threshold-selection convention, not a data shortcut.
    ordered = sorted(values)
    candidates = sorted({ordered[round(index * (len(ordered) - 1) / 200)] for index in range(201)})
    best = max((metrics(rows, values, value) for value in candidates), key=lambda value: (value["balanced_accuracy"], value["sensitivity"], -value["threshold"]))
    return best["threshold"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--reconstruction-manifest", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    reconstruction = json.loads(args.reconstruction_manifest.read_text())
    if reconstruction.get("status") != "PASS_OUTCOME_BLIND_DESCRIPTIVE" or not reconstruction.get("strict_preentry"):
        raise SystemExit("FAIL_CLOSED: reconstruction is not a passing strict pre-entry corpus")
    rows = load(args.dataset)
    positives = sorted(row["time"] for row in rows if row["label"])
    split = positives[int(.70 * (len(positives) - 1))]
    train, test = [row for row in rows if row["time"] <= split], [row for row in rows if row["time"] > split]
    if not all(any(row["label"] == label for row in group) for group in (train, test) for label in (0, 1)):
        raise SystemExit("FAIL_CLOSED: chronological split lacks class coverage")
    means, scales = standardize(train, rows)
    beta, intercept = fit(train)
    train_scores, test_scores = scores(train, beta, intercept), scores(test, beta, intercept)
    threshold = choose_threshold(train, train_scores)
    result = {"schema": "KEFI_RICH_PREENTRY_FEASIBILITY_V2", "status": "EXPLORATORY_DESCRIPTIVE_NOT_VALIDATED",
              "strict_preentry": True, "features": list(FEATURES), "split_unix_ns": split,
              "standardization_means": means, "standardization_scales": scales, "coefficients": dict(zip(FEATURES, beta)), "intercept": intercept,
              "training_threshold": threshold, "train": metrics(train, train_scores, threshold), "chronological_holdout": metrics(test, test_scores, threshold),
              "inputs": {"dataset_sha256": sha(args.dataset), "reconstruction_manifest_sha256": sha(args.reconstruction_manifest)},
              "outcomes_accessed": False, "pnl_accessed": False,
              "interpretation": "Exploratory pre-entry feasibility only. Historical controls and this result cannot prove prospective prediction, profitability, or execution readiness."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "holdout": result["chronological_holdout"], "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
