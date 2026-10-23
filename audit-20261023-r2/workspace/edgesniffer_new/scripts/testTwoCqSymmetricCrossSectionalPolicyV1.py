#!/usr/bin/env python3
"""Deterministic tests for the symmetric cross-sectional freeze boundary."""

from __future__ import annotations

import gzip
import json
import pathlib
import tempfile
from argparse import Namespace

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqSymmetricCrossSectionalPolicyV1 as subject


def rows(first: int, last: int) -> list[dict]:
    result = []
    for ordinal in range(first, last + 1):
        positive = {name: 0.0 for name in timing.FULL_FEATURES}
        negative = {name: 0.0 for name in timing.FULL_FEATURES}
        positive["gross_buy_sol_250ms"] = 2.0 + ordinal / 1000
        negative["gross_buy_sol_250ms"] = 0.1
        result.append({
            "eligibleOrdinal": ordinal,
            "episodeId": f"episode-{ordinal}",
            "mint": f"mint-{ordinal}",
            "controlN": 1,
            "outcomesRead": 0,
            "states": [
                {"label": False, "mint": f"control-{ordinal}", "features": negative},
                {"label": True, "mint": f"mint-{ordinal}", "features": positive},
            ],
        })
    return result


def write_gz(path: pathlib.Path, values: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value) + "\n")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        development = root / "development.jsonl.gz"
        validation = root / "validation.jsonl.gz"
        contract = root / "contract.json"
        policy = root / "primary-policy.json"
        transition = root / "transition.json"
        output = root / "validation"
        write_gz(development, rows(1, 350))
        write_gz(validation, rows(351, 500))
        contract.write_text(json.dumps({
            "schema": "2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_CENSUS_V1_CONTRACT",
            "integrity": {"modelFittingBeforeDevelopmentN350": False},
        }), encoding="utf-8")
        transition.write_text(json.dumps({
            "timingFamily": "BASE_PLUS_TRANSITION",
            "timingModel": {"featureNames": ["transition"]},
            "timingThreshold": 0.5,
        }), encoding="utf-8")

        frozen = subject.fit(Namespace(
            development=development, cohort_contract=contract, output=policy,
        ))
        assert frozen["policyCountFit"] == 1
        assert frozen["outcomesRead"] == 0
        report = subject.validate(Namespace(
            validation=validation, policy=policy,
            transition_policy=transition, output=output,
        ))
        assert report["validationN"] == 150
        assert report["modelRefitOnValidation"] is False
        assert (output / "joint-imitation-policy.json").exists()

        event_hazard = root / "event-hazard.json"
        event_hazard.write_text(json.dumps({
            "schema": "2CQ_EVENT_LEVEL_TIMING_HAZARD_V1_TIMING_POLICY_CANDIDATE",
            "teacherImitationOnly": True,
            "model": {"featureNames": ["transition"]},
            "threshold": 0.6,
            "triggerClock": "after every canonically ordered successful external Pump transaction",
            "dedupeRule": "first threshold crossing per mint lifecycle",
            "sealedValidationRequired": True,
            "profitabilityClaim": False,
        }), encoding="utf-8")
        normalized = subject.normalized_timing_policy(event_hazard)
        assert normalized["timingFamily"] == "PROXIMAL_EVENT_LEVEL_HAZARD_V1"
        assert normalized["canonicalStateOrderRequired"] is True

        event_output = root / "validation-event-hazard"
        subject.validate(Namespace(
            validation=validation, policy=policy,
            transition_policy=event_hazard, output=event_output,
        ))
        joint = json.loads(
            (event_output / "joint-imitation-policy.json").read_text(encoding="utf-8")
        )
        assert joint["timingFamily"] == "PROXIMAL_EVENT_LEVEL_HAZARD_V1"
        assert joint["timingScoreDomain"] == "LOGISTIC_PROBABILITY"
        assert joint["crossMintScoreDomain"] == "STANDARDIZED_LOGISTIC_LOGIT_FOR_RANKING"
        assert joint["canonicalStateOrderRequired"] is True
        assert joint["componentValidation"]["crossMintWhat"] == "ONE_TIME_SEALED_N150_COMPLETE"
        assert joint["componentValidation"]["eventTimingWhen"] == "DEVELOPMENT_ONLY_AWAITING_PROSPECTIVE_NO_SUBMIT"
        assert joint["outcomesRead"] == 0
        assert joint["submissions"] == 0

        try:
            subject.validate(Namespace(
                validation=validation, policy=policy,
                transition_policy=transition, output=output,
            ))
        except RuntimeError as error:
            assert str(error) == "SEALED_VALIDATION_ALREADY_CONSUMED"
        else:
            raise AssertionError("second validation must fail closed")

        contaminated = rows(1, 350)
        contaminated[0]["states"][0]["pnlSol"] = 1.0
        try:
            subject.assert_outcome_blind(contaminated)
        except RuntimeError as error:
            assert str(error).startswith("OUTCOME_FIELD_FORBIDDEN")
        else:
            raise AssertionError("outcome-bearing input must fail closed")

    print("PASS: symmetric cross-sectional policy fits once and validates once")


if __name__ == "__main__":
    main()
