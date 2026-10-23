#!/usr/bin/env python3
"""Small outcome-blind temporal fingerprint discovery model.

This is a wallet-selection classifier, not a profit model. It uses only entry
context and keeps the final partitions out of fitting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


FEATURES = (
    "log_base_amount",
    "log_pre_pool_quote",
    "log_pool_quote_delta",
    "relative_pool_impulse",
    "relative_user_quote",
    "amm_fee_bps",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_rows(path: Path, label: int) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            pre_quote = float(row.get("pre_pool_quote", 0))
            if pre_quote <= 0:
                continue
            rows.append(
                {
                    "label": label,
                    "partition": row["source_partition"],
                    "signature": row["signature"],
                    "x": [
                        math.log1p(float(row["base_amount"])),
                        math.log1p(pre_quote),
                        math.log1p(float(row["pool_quote_delta"])),
                        float(row["pool_quote_delta"]) / pre_quote,
                        float(row["user_quote_amount"]) / pre_quote,
                        float(row["lp_fee_bps"] + row["protocol_fee_bps"] + row["coin_creator_bps"]),
                    ],
                }
            )
    return rows


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-min(value, 60.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(value, -60.0))
    return z / (1.0 + z)


def standardize(train: list[dict], rows: list[dict]):
    means = []
    scales = []
    for index in range(len(FEATURES)):
        values = [row["x"][index] for row in train]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        means.append(mean)
        scales.append(math.sqrt(variance) or 1.0)
    for row in rows:
        row["z"] = [(value - means[i]) / scales[i] for i, value in enumerate(row["x"])]
    return means, scales


def fit(train: list[dict]) -> tuple[list[float], float, int]:
    positive = sum(row["label"] for row in train)
    negative = len(train) - positive
    weights = [len(train) / (2.0 * positive), len(train) / (2.0 * negative)]
    beta = [0.0] * len(FEATURES)
    intercept = 0.0
    rate = 0.05
    for epoch in range(3500):
        grad = [0.0] * len(FEATURES)
        grad_intercept = 0.0
        for row in train:
            probability = sigmoid(intercept + sum(beta[i] * row["z"][i] for i in range(len(FEATURES))))
            error = (probability - row["label"]) * weights[row["label"]]
            grad_intercept += error
            for i in range(len(FEATURES)):
                grad[i] += error * row["z"][i]
        scale = 1.0 / len(train)
        intercept -= rate * grad_intercept * scale
        for i in range(len(FEATURES)):
            beta[i] -= rate * (grad[i] * scale + 0.001 * beta[i])
        if epoch == 1200:
            rate = 0.02
    return beta, intercept, epoch + 1


def predict(row: dict, beta: list[float], intercept: float) -> float:
    return sigmoid(intercept + sum(beta[i] * row["z"][i] for i in range(len(FEATURES))))


def auc(rows: list[dict], scores: list[float]) -> float | None:
    positives = sorted(score for row, score in zip(rows, scores) if row["label"] == 1)
    negatives = sorted(score for row, score in zip(rows, scores) if row["label"] == 0)
    if not positives or not negatives:
        return None
    wins = sum(sum(1 for negative in negatives if positive > negative) + 0.5 * sum(1 for negative in negatives if positive == negative) for positive in positives)
    return wins / (len(positives) * len(negatives))


def metrics(rows: list[dict], scores: list[float]) -> dict:
    tp = fp = tn = fn = 0
    for row, score in zip(rows, scores):
        predicted = score >= 0.5
        if row["label"] and predicted: tp += 1
        elif row["label"] and not predicted: fn += 1
        elif not row["label"] and predicted: fp += 1
        else: tn += 1
    total = len(rows)
    return {
        "rows": total,
        "positives": sum(row["label"] for row in rows),
        "negatives": total - sum(row["label"] for row in rows),
        "accuracy_at_0_5": (tp + tn) / total if total else None,
        "balanced_accuracy_at_0_5": 0.5 * ((tp / (tp + fn)) if tp + fn else 0) + 0.5 * ((tn / (tn + fp)) if tn + fp else 0),
        "auc": auc(rows, scores),
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selected", required=True, type=Path)
    ap.add_argument("--controls", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ns = ap.parse_args()
    all_rows = read_rows(ns.selected, 1) + read_rows(ns.controls, 0)
    train = [row for row in all_rows if row["partition"].split("/")[-1] in {str(i).zfill(2) for i in range(3, 10)}]
    test = [row for row in all_rows if row["partition"].split("/")[-1] in {"10", "11", "12"}]
    means, scales = standardize(train, all_rows)
    beta, intercept, epochs = fit(train)
    train_scores = [predict(row, beta, intercept) for row in train]
    test_scores = [predict(row, beta, intercept) for row in test]
    result = {
        "schema": "KEFI_ENTRY_FINGERPRINT_DISCOVERY_V1",
        "status": "EXPLORATORY_TEMPORAL_TEST",
        "outcome_blind": True,
        "economic_outcomes_read": False,
        "target": "imitate kEFi wallet selection from entry context only",
        "fit_partitions": ["2026/10/05/03", "2026/10/05/04", "2026/10/05/05", "2026/10/05/06", "2026/10/05/07", "2026/10/05/08", "2026/10/05/09"],
        "unseen_test_partitions": ["2026/10/05/10", "2026/10/05/11", "2026/10/05/12"],
        "features": list(FEATURES),
        "features_not_available_yet": ["holder_count", "rolling_volume", "unique_buyer_count", "social_or_metadata_features"],
        "standardization_means": means,
        "standardization_scales": scales,
        "coefficients": dict(zip(FEATURES, beta)),
        "intercept": intercept,
        "training_epochs": epochs,
        "fit_metrics": metrics(train, train_scores),
        "unseen_temporal_metrics": metrics(test, test_scores),
        "input_sha256": {"selected": sha256(ns.selected), "controls": sha256(ns.controls)},
        "interpretation": "A temporal selection-imitation result only; not a profitability, WHEN, or execution claim.",
    }
    ns.output.parent.mkdir(parents=True, exist_ok=True)
    ns.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"fit": result["fit_metrics"], "unseen": result["unseen_temporal_metrics"]}, sort_keys=True))


if __name__ == "__main__":
    main()
