#!/usr/bin/env python3
"""Write the immutable finalization receipt for the completed WHAT V1 result."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat


EXPECTED_RESULT_HASH = "662e91daef82cd9d80b9492ec00c337fcdc4e13246f1a17a42b5b98886cc4072"
EXPECTED_POLICY_HASH = "bedb0ec347876948937b92c25c2f9df9a3644ce2bb73f3ac258b956dd4d9d1cc"
EXPECTED_FEATURE_HASH = "ea30c08655bcf709b1bb20db3a5681c2979d890520545fd85877b42029dbabcd"
EXPECTED_CONTRACT_HASH = "c83f291155062dcb73740787092a940cba6bd4ca68b5bb4b06279f3d7e5e4704"


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("WHAT_V1_FINAL_FREEZE_ALREADY_EXISTS")
    if sha256(args.result) != EXPECTED_RESULT_HASH:
        raise RuntimeError("WHAT_V1_RESULT_HASH_MISMATCH")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    required = {
        "frozenWhatPolicySha256": EXPECTED_POLICY_HASH,
        "frozenFeatureSetSha256": EXPECTED_FEATURE_HASH,
        "frozenEvaluationContractSha256": EXPECTED_CONTRACT_HASH,
        "validationN": 150,
        "validationRowsEvaluated": 150,
        "validationRowsSkipped": 0,
        "validationTuningPerformed": False,
        "policyModified": False,
        "n151PlusAccessedForEvaluation": 0,
        "economicOutcomesRead": 0,
        "pnlRead": 0,
        "submissions": 0,
    }
    for key, expected in required.items():
        if result.get(key) != expected:
            raise RuntimeError(f"WHAT_V1_RESULT_FINALIZATION_MISMATCH:{key}")
    value = {
        "schema": "2CQ_PROSPECTIVE_WHAT_V1_FINAL_FREEZE",
        "officialResultPath": str(args.result),
        "officialResultSha256": EXPECTED_RESULT_HASH,
        "frozenPolicySha256": EXPECTED_POLICY_HASH,
        "frozenFeatureSetSha256": EXPECTED_FEATURE_HASH,
        "frozenEvaluationContractSha256": EXPECTED_CONTRACT_HASH,
        "whatV1Final": True,
        "mutationProhibited": ["policy", "features", "preprocessing", "evaluation contract", "official result"],
        "futureCollectionAssignment": "FINGERPRINT_V2_DEVELOPMENT_N350 onwards; no V2 development/holdout access by this receipt",
        "outcomesRead": 0,
        "pnlRead": 0,
        "submissions": 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    print(sha256(args.output))


if __name__ == "__main__":
    main()
