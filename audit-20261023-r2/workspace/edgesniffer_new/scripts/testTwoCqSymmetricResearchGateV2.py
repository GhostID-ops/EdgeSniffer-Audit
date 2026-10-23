#!/usr/bin/env python3
"""Deterministic N350/N500 gate tests for the V2 symmetric cohort."""

from __future__ import annotations

import gzip
import hashlib
import json
import pathlib
import sqlite3
import tempfile
from argparse import Namespace

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqSymmetricResearchGateV2 as subject


def choice(ordinal: int) -> dict:
    selected = {name: 0.0 for name in timing.FULL_FEATURES}
    control = {name: 0.0 for name in timing.FULL_FEATURES}
    selected["gross_buy_sol_250ms"] = 2.0 + ordinal / 1000
    control["gross_buy_sol_250ms"] = 0.1
    return {
        "episodeId": f"sig-{ordinal}",
        "mint": f"mint-{ordinal}",
        "controlN": 1,
        "outcomesRead": 0,
        "submissions": 0,
        "states": [
            {"label": False, "mint": f"control-{ordinal}", "features": control},
            {"label": True, "mint": f"mint-{ordinal}", "features": selected},
        ],
    }


def database(path: pathlib.Path, n: int) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE current_decisions (
        signature TEXT PRIMARY KEY,mint TEXT,block_time_ms INTEGER,slot INTEGER,
        transaction_index INTEGER,status TEXT,choice_json TEXT)"""
    )
    for ordinal in range(1, n + 1):
        connection.execute(
            "INSERT INTO current_decisions VALUES (?,?,?,?,?,?,?)",
            (f"sig-{ordinal}", f"mint-{ordinal}", ordinal, ordinal, 0,
             "ONLINE_ELIGIBLE", json.dumps(choice(ordinal), sort_keys=True)),
        )
    connection.commit()
    connection.close()


def write_development(root: pathlib.Path) -> None:
    directory = root / "development"
    directory.mkdir(parents=True)
    path = directory / "choice-sets.jsonl.gz"
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            for ordinal in range(1, 351):
                row = choice(ordinal) | {"eligibleOrdinal": ordinal, "split": "DEVELOPMENT"}
                stream.write(subject.canonical(row) + b"\n")
    directory.joinpath("freeze.json").write_text(json.dumps({
        "signatures": [f"sig-{ordinal}" for ordinal in range(1, 351)],
        "choiceSetsSha256": subject.sha256_file(path),
        "outcomesRead": 0,
        "validationRowsRead": 0,
    }), encoding="utf-8")


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        contract = root / "contract.json"
        contract.write_text(json.dumps({
            "schema": subject.CONTRACT_SCHEMA,
            "cohortStartUtc": "2026-01-01T00:00:00Z",
            "integrity": {
                "outcomesPermitted": False,
                "profitabilityPermitted": False,
                "submissionsPermitted": False,
                "validationRowsPermittedBeforeFreeze": False,
                "modelFittingBeforeDevelopmentN350": False,
                "validationSealed": True,
            },
        }), encoding="utf-8")
        transition = root / "transition.json"
        transition.write_text(json.dumps({
            "timingFamily": "BASE_PLUS_TRANSITION",
            "timingModel": {"featureNames": ["test"]},
            "timingThreshold": 0.5,
        }), encoding="utf-8")
        engine = root / "exact.py"
        engine.write_text("# frozen test engine\n", encoding="utf-8")

        pending_state = root / "pending.sqlite3"
        database(pending_state, 349)
        pending_args = Namespace(
            contract=contract, state=pending_state, cohort_root=root / "pending-cohort",
            policy_root=root / "pending-policy", transition_policy=transition,
            exact_engine=engine,
        )
        pending = subject.advance(pending_args)
        assert pending["verdict"] == "AWAITING_N350_NO_ACTION"
        assert pending["development"]["remaining"] == 1
        assert not pending_args.policy_root.exists()

        # The N350 freeze must not read already-arrived sealed validation rows.
        guarded_state = root / "guarded.sqlite3"
        database(guarded_state, 353)
        guarded_cohort = root / "guarded-cohort"
        guarded_cohort.mkdir()
        write_development(guarded_cohort)
        (guarded_cohort / "validation-sealed").mkdir()
        guarded_args = Namespace(
            contract=contract, state=guarded_state, cohort_root=guarded_cohort,
            policy_root=root / "guarded-policy", transition_policy=transition,
            exact_engine=engine,
        )
        original_eligible_rows = subject.eligible_rows
        row_limits = []

        def traced_eligible_rows(path: pathlib.Path, limit: int = 500) -> list[dict]:
            row_limits.append(limit)
            return original_eligible_rows(path, limit)

        subject.eligible_rows = traced_eligible_rows
        try:
            guarded = subject.advance(guarded_args)
        finally:
            subject.eligible_rows = original_eligible_rows
        assert guarded["verdict"] == "POLICY_FROZEN_AWAITING_N500"
        assert row_limits == [350]
        assert not (guarded_cohort / "validation-sealed" / "choice-sets.jsonl.gz").exists()
        assert not (guarded_args.policy_root / "sealed-validation").exists()

        state = root / "ready.sqlite3"
        database(state, 500)
        cohort_root = root / "cohort"
        cohort_root.mkdir()
        write_development(cohort_root)
        (cohort_root / "validation-sealed").mkdir()
        args = Namespace(
            contract=contract, state=state, cohort_root=cohort_root,
            policy_root=root / "policy", transition_policy=transition,
            exact_engine=engine,
        )
        result = subject.advance(args)
        assert result["verdict"] == "SEALED_N150_VALIDATED_JOINT_POLICY_READY"
        assert result["policy"]["frozen"] is True
        assert result["sealedValidation"]["consumed"] is True
        assert result["jointPolicy"]["ready"] is True
        assert result["integrity"]["outcomesRead"] == 0
        frozen = json.loads((args.policy_root / "primary-policy.json").read_text())
        assert frozen["cohortContractSchema"] == subject.CONTRACT_SCHEMA
        assert frozen["candidateUniverse"].endswith("_V2")

        second = subject.advance(args)
        assert second["verdict"] == "SEALED_N150_VALIDATED_JOINT_POLICY_READY"
        assert second["integrity"]["outcomesRead"] == 0

    print("PASS: V2 N350/N500 handoff is fail-closed, one-policy, and outcome-blind")


if __name__ == "__main__":
    main()
