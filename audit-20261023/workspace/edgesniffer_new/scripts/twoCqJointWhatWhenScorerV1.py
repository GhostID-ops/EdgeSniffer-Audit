#!/usr/bin/env python3
"""No-submit scorer for the frozen 2CQ WHAT + WHEN imitation policy."""

from __future__ import annotations

import argparse
import json
import math
import pathlib
from typing import Any


SCHEMA = "2CQ_JOINT_WHAT_WHEN_SCORER_V1"
JOINT_SCHEMA = "2CQ_SYMMETRIC_JOINT_IMITATION_POLICY_V1"


def model_logit(features: dict[str, Any], model: dict[str, Any]) -> float:
    score = float(model["intercept"])
    for name in model["featureNames"]:
        if name not in features or isinstance(features[name], bool):
            raise RuntimeError(f"FEATURE_MISSING_OR_INVALID:{name}")
        value = float(features[name])
        mean = float(model["means"][name])
        scale = float(model["scales"][name])
        coefficient = float(model["coefficients"][name])
        if not all(math.isfinite(item) for item in (value, mean, scale, coefficient)) or scale <= 0:
            raise RuntimeError(f"FEATURE_NONFINITE_OR_SCALE_INVALID:{name}")
        score += coefficient * ((value - mean) / scale)
    if not math.isfinite(score):
        raise RuntimeError("MODEL_SCORE_NONFINITE")
    return score


def model_probability(features: dict[str, Any], model: dict[str, Any]) -> float:
    logit = model_logit(features, model)
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    exp_logit = math.exp(logit)
    return exp_logit / (1.0 + exp_logit)


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema") != JOINT_SCHEMA:
        raise RuntimeError("JOINT_POLICY_SCHEMA_INVALID")
    if int(policy.get("outcomesRead") or 0) != 0 or int(policy.get("submissions") or 0) != 0:
        raise RuntimeError("JOINT_POLICY_TAINTED")
    if policy.get("prospectiveImitationRequiredNext") is not True:
        raise RuntimeError("JOINT_POLICY_PROSPECTIVE_GATE_INVALID")
    if policy.get("exactProfitabilityPermittedNow") is not False:
        raise RuntimeError("JOINT_POLICY_ECONOMIC_BOUNDARY_INVALID")
    for key in ("timingModel", "timingThreshold", "crossMintModel"):
        if key not in policy:
            raise RuntimeError(f"JOINT_POLICY_FIELD_MISSING:{key}")
    if policy.get("timingScoreDomain") != "LOGISTIC_PROBABILITY":
        raise RuntimeError("TIMING_SCORE_DOMAIN_INVALID")
    if policy.get("crossMintScoreDomain") != "STANDARDIZED_LOGISTIC_LOGIT_FOR_RANKING":
        raise RuntimeError("CROSS_MINT_SCORE_DOMAIN_INVALID")


def evaluate_event(policy: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one complete causal risk-set snapshot and fail closed on ambiguity."""
    validate_policy(policy)
    event_id = str(event.get("eventId") or "")
    changed_mint = str(event.get("changedMint") or "")
    prior = event.get("priorTimingFeatures")
    current = event.get("currentTimingFeatures")
    candidates = event.get("riskSet") or []
    base = {
        "schema": SCHEMA + "_DECISION",
        "eventId": event_id,
        "changedMint": changed_mint,
        "submit": False,
    }
    if not event_id or not changed_mint:
        return base | {"qualified": False, "reason": "EVENT_ID_OR_MINT_MISSING"}
    if event.get("canonicalOrderProven") is not True:
        return base | {"qualified": False, "reason": "CANONICAL_ORDER_UNPROVEN"}
    if not isinstance(prior, dict):
        return base | {"qualified": False, "reason": "PRIOR_TIMING_STATE_MISSING"}
    if not isinstance(current, dict):
        return base | {"qualified": False, "reason": "CURRENT_TIMING_STATE_MISSING"}
    if not candidates:
        return base | {"qualified": False, "reason": "SYMMETRIC_RISK_SET_MISSING"}
    try:
        prior_score = model_probability(prior, policy["timingModel"])
        current_score = model_probability(current, policy["timingModel"])
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        return base | {"qualified": False, "reason": f"TIMING_{error}"}
    threshold = float(policy["timingThreshold"])
    crossing = prior_score < threshold <= current_score
    if not crossing:
        return base | {
            "qualified": False,
            "reason": "NO_FRESH_TIMING_CROSSING",
            "priorTimingScore": prior_score,
            "currentTimingScore": current_score,
            "timingThreshold": threshold,
        }
    scored = []
    seen_mints = set()
    try:
        for candidate in candidates:
            mint = str(candidate.get("mint") or "")
            if not mint or mint in seen_mints:
                raise RuntimeError("RISK_SET_MINT_MISSING_OR_DUPLICATE")
            seen_mints.add(mint)
            scored.append((model_logit(candidate.get("features") or {}, policy["crossMintModel"]), mint))
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        return base | {"qualified": False, "reason": f"RISK_SET_{error}"}
    if changed_mint not in seen_mints:
        return base | {"qualified": False, "reason": "CHANGED_MINT_ABSENT_FROM_RISK_SET"}
    scored.sort(key=lambda item: (-item[0], item[1]))
    top_score = scored[0][0]
    top_mints = [mint for score, mint in scored if score == top_score]
    if len(top_mints) != 1:
        return base | {"qualified": False, "reason": "CROSS_SECTIONAL_TOP_SCORE_TIE"}
    rank = 1 + sum(score > next(value for value, mint in scored if mint == changed_mint) for score, _ in scored)
    if top_mints[0] != changed_mint:
        return base | {
            "qualified": False,
            "reason": "CHANGED_MINT_NOT_TOP_RANKED",
            "changedMintRank": rank,
            "topMint": top_mints[0],
            "riskSetN": len(scored),
        }
    return base | {
        "qualified": True,
        "reason": "WHAT_AND_WHEN_PASS",
        "priorTimingScore": prior_score,
        "currentTimingScore": current_score,
        "timingThreshold": threshold,
        "changedMintRank": 1,
        "riskSetN": len(scored),
        "topMint": changed_mint,
    }


def run(policy_path: pathlib.Path, events_path: pathlib.Path, output_path: pathlib.Path) -> dict[str, Any]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    validate_policy(policy)
    decisions = []
    with events_path.open("rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                decisions.append(evaluate_event(policy, json.loads(line)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary.open("wt", encoding="utf-8") as stream:
        for decision in decisions:
            stream.write(json.dumps(decision, sort_keys=True, separators=(",", ":")) + "\n")
    temporary.replace(output_path)
    return {
        "schema": SCHEMA + "_RUN",
        "events": len(decisions),
        "qualified": sum(bool(row["qualified"]) for row in decisions),
        "submissions": 0,
        "output": str(output_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=pathlib.Path, required=True)
    parser.add_argument("--events", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.policy, args.events, args.output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
