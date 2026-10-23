#!/usr/bin/env python3
"""Outcome-blind, chronological feasibility check using only pre-entry fields.

This is deliberately a discovery diagnostic, not a prospective validation.
Its control input is marked selected-centered and the result refuses to claim
profitability, execution readiness, or pre-entry validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


FEATURES = ("log_pre_pool_quote", "amm_fee_bps")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-min(value, 60)))
    z = math.exp(max(value, -60))
    return z / (1.0 + z)


def parse(path: Path, label: int) -> list[dict]:
    rows = []
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if label and row.get("side") != "BUY":
                continue
            pre_quote = float(row.get("pre_pool_quote", 0))
            timestamp = int(row.get("received_at_unix_ns", 0))
            if pre_quote <= 0 or timestamp <= 0:
                continue
            fee = float(row.get("lp_fee_bps", 0) + row.get("protocol_fee_bps", 0) + row.get("coin_creator_bps", 0))
            identity = str(row.get("signature") or row.get("candidate_signature") or "")
            if not identity:
                continue
            rows.append({"label": label, "time": timestamp, "identity": identity,
                         "x": [math.log1p(pre_quote), fee]})
    return rows


def deterministic_cap(rows: list[dict], target: int) -> list[dict]:
    return sorted(rows, key=lambda row: hashlib.sha256(row["identity"].encode()).hexdigest())[:target]


def standardize(train: list[dict], all_rows: list[dict]):
    means, scales = [], []
    for index in range(len(FEATURES)):
        values = [row["x"][index] for row in train]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        means.append(mean); scales.append(math.sqrt(variance) or 1.0)
    for row in all_rows:
        row["z"] = [(row["x"][i] - means[i]) / scales[i] for i in range(len(FEATURES))]
    return means, scales


def fit(rows: list[dict], epochs: int = 1200):
    positives = sum(row["label"] for row in rows); negatives = len(rows) - positives
    if not positives or not negatives:
        raise ValueError("both classes are required")
    weights = [len(rows) / (2 * positives), len(rows) / (2 * negatives)]
    beta, intercept, rate = [0.0] * len(FEATURES), 0.0, 0.04
    for epoch in range(epochs):
        grad, grad_i = [0.0] * len(FEATURES), 0.0
        for row in rows:
            prediction = sigmoid(intercept + sum(weight * value for weight, value in zip(beta, row["z"])))
            error = (prediction - row["label"]) * weights[row["label"]]
            grad_i += error
            for index, value in enumerate(row["z"]):
                grad[index] += error * value
        step = 1.0 / len(rows)
        intercept -= rate * grad_i * step
        for index in range(len(beta)):
            beta[index] -= rate * (grad[index] * step + 0.001 * beta[index])
        if epoch == 600:
            rate = 0.015
    return beta, intercept


def auc(rows: list[dict], scores: list[float]) -> float:
    pos = sorted(score for row, score in zip(rows, scores) if row["label"])
    neg = sorted(score for row, score in zip(rows, scores) if not row["label"])
    wins = sum(sum(value > other for other in neg) + .5 * sum(value == other for other in neg) for value in pos)
    return wins / (len(pos) * len(neg))


def metrics(rows: list[dict], beta: list[float], intercept: float):
    scores = [sigmoid(intercept + sum(weight * value for weight, value in zip(beta, row["z"]))) for row in rows]
    tp = sum(row["label"] and score >= .5 for row, score in zip(rows, scores))
    fn = sum(row["label"] and score < .5 for row, score in zip(rows, scores))
    tn = sum(not row["label"] and score < .5 for row, score in zip(rows, scores))
    fp = sum(not row["label"] and score >= .5 for row, score in zip(rows, scores))
    return {"rows": len(rows), "positives": tp + fn, "negatives": tn + fp, "auc": auc(rows, scores),
            "balanced_accuracy_at_0_5": .5 * (tp / (tp + fn) + tn / (tn + fp)),
            "confusion": {"tp": tp, "fn": fn, "tn": tn, "fp": fp}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected", required=True, type=Path)
    parser.add_argument("--controls", required=True, type=Path)
    parser.add_argument("--preentry-contract", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    contract = json.loads(args.preentry_contract.read_text())
    if set(FEATURES) - set(contract.get("pre_entry_features", [])):
        raise SystemExit("FAIL_CLOSED: a requested feature is not pre-entry approved")
    selected, controls = parse(args.selected, 1), parse(args.controls, 0)
    split = sorted(row["time"] for row in selected)[int(.70 * (len(selected) - 1))]
    train_pos = [row for row in selected if row["time"] <= split]
    test_pos = [row for row in selected if row["time"] > split]
    train_neg = deterministic_cap([row for row in controls if row["time"] <= split], len(train_pos) * 3)
    test_neg = deterministic_cap([row for row in controls if row["time"] > split], len(test_pos) * 3)
    train, test = train_pos + train_neg, test_pos + test_neg
    if min(len(train_pos), len(test_pos), len(train_neg), len(test_neg)) == 0:
        raise SystemExit("FAIL_CLOSED: insufficient chronological class coverage")
    means, scales = standardize(train, train + test)
    beta, intercept = fit(train)
    result = {
        "schema": "KEFI_PREENTRY_FEASIBILITY_V1", "status": "DESCRIPTIVE_NOT_VALIDATED",
        "features": list(FEATURES), "pre_entry_only": True,
        "control_caveat": "selected-centered historical controls; this is not a prospective validation cohort",
        "split_unix_ns": split, "coefficients": dict(zip(FEATURES, beta)), "intercept": intercept,
        "standardization_means": means, "standardization_scales": scales,
        "train": metrics(train, beta, intercept), "chronological_holdout": metrics(test, beta, intercept),
        "inputs": {"selected_sha256": sha(args.selected), "controls_sha256": sha(args.controls),
                   "preentry_contract_sha256": sha(args.preentry_contract)},
        "outcomes_accessed": False, "pnl_accessed": False,
        "interpretation": "Feasibility only. A fresh prospective pre-entry cohort is required before any prediction or execution claim.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "holdout": result["chronological_holdout"], "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
