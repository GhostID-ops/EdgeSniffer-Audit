#!/usr/bin/env python3
"""Fail-closed N350/N500 handoff for the V2 symmetric 2CQ cohort.

This program is deliberately not installed as a timer. ``status`` is read-only.
``advance`` may fit the single predeclared teacher-imitation policy at N350 and
consume the sealed N150 exactly once at N500. Neither command reads economics.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import pathlib
import sqlite3
from argparse import Namespace
from typing import Any

import twoCqSymmetricCrossSectionalPolicyV1 as policy_v1


SCHEMA = "2CQ_SYMMETRIC_RESEARCH_GATE_V2"
CONTRACT_SCHEMA = "2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_CENSUS_V2_CONTRACT"
DEVELOPMENT_N = 350
VALIDATION_N = 150


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def atomic_json(path: pathlib.Path, value: Any, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def load_contract(path: pathlib.Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise RuntimeError("V2_CONTRACT_INVALID")
    integrity = contract.get("integrity") or {}
    expected = {
        "outcomesPermitted": False,
        "profitabilityPermitted": False,
        "submissionsPermitted": False,
        "validationRowsPermittedBeforeFreeze": False,
        "modelFittingBeforeDevelopmentN350": False,
        "validationSealed": True,
    }
    for key, value in expected.items():
        if integrity.get(key) is not value:
            raise RuntimeError(f"V2_CONTRACT_INTEGRITY_INVALID:{key}")
    return contract


def eligible_rows(state: pathlib.Path, limit: int = 500) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{state}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """SELECT signature,mint,block_time_ms,slot,transaction_index,choice_json
               FROM current_decisions
               WHERE status='ONLINE_ELIGIBLE' AND choice_json IS NOT NULL
               ORDER BY block_time_ms,slot,transaction_index,signature LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        connection.close()
    result = []
    for signature, mint, block_ms, slot, tx_index, payload in rows:
        choice = json.loads(payload)
        policy_v1.assert_outcome_blind(choice)
        states = choice.get("states") or []
        if len(states) < 2 or sum(bool(state.get("label")) for state in states) != 1:
            raise RuntimeError(f"CHOICE_SET_INVALID:{signature}")
        result.append({
            "signature": signature,
            "mint": mint,
            "blockTimeMs": int(block_ms),
            "slot": int(slot),
            "transactionIndex": int(tx_index),
            "choice": choice,
        })
    return result


def eligible_count(state: pathlib.Path) -> int:
    connection = sqlite3.connect(f"file:{state}?mode=ro", uri=True)
    try:
        return int(connection.execute(
            "SELECT COUNT(*) FROM current_decisions "
            "WHERE status='ONLINE_ELIGIBLE' AND choice_json IS NOT NULL"
        ).fetchone()[0])
    finally:
        connection.close()


def status(args: argparse.Namespace) -> dict[str, Any]:
    contract = load_contract(args.contract)
    online_n = eligible_count(args.state)
    development_freeze = args.cohort_root / "development" / "freeze.json"
    development_choices = args.cohort_root / "development" / "choice-sets.jsonl.gz"
    policy_path = args.policy_root / "primary-policy.json"
    validation_freeze = args.cohort_root / "validation-sealed" / "freeze.json"
    validation_report = args.policy_root / "sealed-validation" / "sealed-validation-report.json"
    joint_policy = args.policy_root / "sealed-validation" / "joint-imitation-policy.json"
    exact_engine_ready = bool(args.exact_engine and args.exact_engine.exists())
    return {
        "schema": SCHEMA + "_STATUS",
        "cohortContractSha256": sha256_file(args.contract),
        "cohortStartUtc": contract["cohortStartUtc"],
        "onlineEligibleN": online_n,
        "development": {
            "targetN": DEVELOPMENT_N,
            "remaining": max(0, DEVELOPMENT_N - online_n),
            "artifactPresent": development_choices.exists(),
            "freezePresent": development_freeze.exists(),
        },
        "policy": {
            "frozen": policy_path.exists(),
            "path": str(policy_path),
        },
        "sealedValidation": {
            "availableN": max(0, min(VALIDATION_N, online_n - DEVELOPMENT_N)),
            "targetN": VALIDATION_N,
            "exported": validation_freeze.exists(),
            "consumed": validation_report.exists(),
        },
        "jointPolicy": {
            "ready": joint_policy.exists(),
            "path": str(joint_policy),
        },
        "exactReplayHandoff": {
            "enginePresent": exact_engine_ready,
            "enginePath": str(args.exact_engine) if args.exact_engine else None,
            "engineSha256": sha256_file(args.exact_engine) if exact_engine_ready else None,
            "permittedBeforeJointPolicy": False,
            "economicAssumptionsFrozen": False,
        },
        "integrity": {
            "outcomesRead": 0,
            "validationRowsRead": 0,
            "submissions": 0,
            "activeCollectorModified": False,
        },
    }


def verify_development(args: argparse.Namespace, rows: list[dict[str, Any]]) -> tuple[pathlib.Path, dict[str, Any]]:
    freeze_path = args.cohort_root / "development" / "freeze.json"
    choices_path = args.cohort_root / "development" / "choice-sets.jsonl.gz"
    if len(rows) < DEVELOPMENT_N:
        raise RuntimeError("N350_NOT_READY")
    if not freeze_path.exists() or not choices_path.exists():
        raise RuntimeError("DEVELOPMENT_PUBLISH_PENDING_MATERIALIZER")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    signatures = [row["signature"] for row in rows[:DEVELOPMENT_N]]
    if freeze.get("signatures") != signatures:
        raise RuntimeError("DEVELOPMENT_MEMBERSHIP_MISMATCH")
    if freeze.get("choiceSetsSha256") != sha256_file(choices_path):
        raise RuntimeError("DEVELOPMENT_ARTIFACT_HASH_MISMATCH")
    if int(freeze.get("outcomesRead") or 0) != 0 or int(freeze.get("validationRowsRead") or 0) != 0:
        raise RuntimeError("DEVELOPMENT_FREEZE_TAINTED")
    return choices_path, freeze


def freeze_policy(args: argparse.Namespace, development: pathlib.Path) -> pathlib.Path:
    policy_path = args.policy_root / "primary-policy.json"
    if policy_path.exists():
        frozen = json.loads(policy_path.read_text(encoding="utf-8"))
        if frozen.get("developmentChoiceSetsSha256") != sha256_file(development):
            raise RuntimeError("FROZEN_POLICY_DEVELOPMENT_HASH_MISMATCH")
        if frozen.get("cohortContractSha256") != sha256_file(args.contract):
            raise RuntimeError("FROZEN_POLICY_CONTRACT_HASH_MISMATCH")
        return policy_path
    args.policy_root.mkdir(parents=True, exist_ok=True)
    policy_v1.fit(Namespace(
        development=development,
        cohort_contract=args.contract,
        output=policy_path,
    ))
    return policy_path


def export_validation(args: argparse.Namespace, rows: list[dict[str, Any]], policy_path: pathlib.Path) -> pathlib.Path:
    if len(rows) < DEVELOPMENT_N + VALIDATION_N:
        raise RuntimeError("N500_NOT_READY")
    destination = args.cohort_root / "validation-sealed" / "choice-sets.jsonl.gz"
    freeze_path = args.cohort_root / "validation-sealed" / "freeze.json"
    validation = rows[DEVELOPMENT_N:DEVELOPMENT_N + VALIDATION_N]
    signatures = [row["signature"] for row in validation]
    if freeze_path.exists() or destination.exists():
        if not freeze_path.exists() or not destination.exists():
            raise RuntimeError("PARTIAL_VALIDATION_EXPORT")
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        if freeze.get("signatures") != signatures or freeze.get("choiceSetsSha256") != sha256_file(destination):
            raise RuntimeError("SEALED_VALIDATION_MEMBERSHIP_MISMATCH")
        return destination
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            for ordinal, row in enumerate(validation, DEVELOPMENT_N + 1):
                choice = row["choice"] | {
                    "eligibleOrdinal": ordinal,
                    "split": "VALIDATION_SEALED",
                }
                policy_v1.assert_outcome_blind(choice)
                stream.write(canonical(choice) + b"\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    atomic_json(freeze_path, {
        "schema": SCHEMA + "_VALIDATION_FREEZE",
        "validationN": VALIDATION_N,
        "ordinals": [DEVELOPMENT_N + 1, DEVELOPMENT_N + VALIDATION_N],
        "signatures": signatures,
        "choiceSetsSha256": sha256_file(destination),
        "policySha256BeforeValidationRead": sha256_file(policy_path),
        "outcomesRead": 0,
        "submissions": 0,
    }, mode=0o600)
    return destination


def validate_once(
    args: argparse.Namespace, validation: pathlib.Path, policy_path: pathlib.Path,
) -> pathlib.Path:
    output = args.policy_root / "sealed-validation"
    report_path = output / "sealed-validation-report.json"
    if report_path.exists():
        return report_path
    policy_v1.validate(Namespace(
        validation=validation,
        policy=policy_path,
        transition_policy=args.transition_policy,
        output=output,
    ))
    return report_path


def write_handoff(args: argparse.Namespace, report_path: pathlib.Path) -> None:
    joint = args.policy_root / "sealed-validation" / "joint-imitation-policy.json"
    if not joint.exists():
        raise RuntimeError("JOINT_POLICY_NOT_CREATED")
    atomic_json(args.policy_root / "post-validation-handoff.json", {
        "schema": SCHEMA + "_POST_VALIDATION_HANDOFF",
        "jointPolicyPath": str(joint),
        "jointPolicySha256": sha256_file(joint),
        "validationReportSha256": sha256_file(report_path),
        "exactCurveEnginePath": str(args.exact_engine),
        "exactCurveEngineSha256": sha256_file(args.exact_engine),
        "nextStages": [
            "FREEZE_SEPARATE_EXACT_ECONOMIC_REPLAY_CONTRACT",
            "RUN_EXACT_CURVE_POLICY_REPLAY",
            "START_ISOLATED_PROSPECTIVE_NO_SUBMIT_SHADOW",
        ],
        "economicReplayRun": False,
        "prospectiveShadowStarted": False,
        "outcomesRead": 0,
        "submissions": 0,
    })


def advance(args: argparse.Namespace) -> dict[str, Any]:
    load_contract(args.contract)
    online_n = eligible_count(args.state)
    if online_n < DEVELOPMENT_N:
        return status(args) | {"verdict": "AWAITING_N350_NO_ACTION"}
    development_rows = eligible_rows(args.state, limit=DEVELOPMENT_N)
    development, _ = verify_development(args, development_rows)
    policy_path = freeze_policy(args, development)
    if online_n < DEVELOPMENT_N + VALIDATION_N:
        return status(args) | {"verdict": "POLICY_FROZEN_AWAITING_N500"}
    rows = eligible_rows(args.state, limit=DEVELOPMENT_N + VALIDATION_N)
    validation = export_validation(args, rows, policy_path)
    report_path = validate_once(args, validation, policy_path)
    write_handoff(args, report_path)
    return status(args) | {"verdict": "SEALED_N150_VALIDATED_JOINT_POLICY_READY"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "advance"))
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--state", type=pathlib.Path, required=True)
    parser.add_argument("--cohort-root", type=pathlib.Path, required=True)
    parser.add_argument("--policy-root", type=pathlib.Path, required=True)
    parser.add_argument("--transition-policy", type=pathlib.Path, required=True)
    parser.add_argument("--exact-engine", type=pathlib.Path, required=True)
    args = parser.parse_args()
    result = status(args) if args.command == "status" else advance(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
