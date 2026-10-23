#!/usr/bin/env python3
"""Fail-closed timing contract for the kEfi selection fingerprint.

This audits feature availability from the frozen feature names and produces a
machine-readable boundary between selection imitation and a future pre-entry
paper lane.  It neither reads outcomes nor fits/scores a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


# Every declared feature must be classified.  UNKNOWN is intentionally treated
# as unavailable for a future pre-entry lane.
AVAILABILITY = {
    "log_base_amount": {
        "availability": "SAME_TARGET_TRANSACTION",
        "reason": "The target buyer's base amount is decoded from its own swap instruction.",
    },
    "log_pre_pool_quote": {
        "availability": "PRE_ENTRY_STATE",
        "reason": "Pool quote reserves immediately before the target transaction can be reconstructed from prior durable state.",
    },
    "log_pool_quote_delta": {
        "availability": "SAME_TARGET_TRANSACTION",
        "reason": "The delta is caused by the target transaction and is not known before it lands.",
    },
    "relative_pool_impulse": {
        "availability": "SAME_TARGET_TRANSACTION",
        "reason": "It includes the target transaction's pool quote delta.",
    },
    "relative_user_quote": {
        "availability": "SAME_TARGET_TRANSACTION",
        "reason": "It includes the target buyer's own quote amount.",
    },
    "amm_fee_bps": {
        "availability": "PRE_ENTRY_STATE",
        "reason": "The AMM fee configuration is pool state available before a swap, subject to ordinary source freshness.",
    },
}


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    model = json.loads(args.frozen_model.read_text())
    features = model.get("features")
    if not isinstance(features, list) or not features:
        raise SystemExit("FAIL_CLOSED: frozen model has no declared feature list")
    unknown = sorted(set(features) - set(AVAILABILITY))
    if unknown:
        raise SystemExit("FAIL_CLOSED: unclassified feature(s): " + ", ".join(unknown))
    declared = {name: AVAILABILITY[name] for name in features}
    pre_entry = [name for name in features if declared[name]["availability"] == "PRE_ENTRY_STATE"]
    same_transaction = [name for name in features if declared[name]["availability"] != "PRE_ENTRY_STATE"]
    result = {
        "schema": "KEFI_PREENTRY_FEATURE_CONTRACT_V1",
        "created_utc": utcnow(),
        "frozen_model_path": str(args.frozen_model.absolute()),
        "frozen_model_sha256": sha256(args.frozen_model),
        "features": declared,
        "pre_entry_features": pre_entry,
        "same_target_transaction_features": same_transaction,
        "current_fingerprint_usable_as_pre_entry_trigger": len(same_transaction) == 0,
        "selection_imitation_valid": True,
        "future_pre_entry_lane_rule": "Permit only PRE_ENTRY_STATE features; fail closed on unknown or same-target-transaction fields.",
        "outcomes_read": False,
        "pnl_read": False,
    }
    raw = canonical(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(raw)
    print(json.dumps({"status": "CONTRACT_WRITTEN", "output": str(args.output),
                      "sha256": hashlib.sha256(raw).hexdigest(),
                      "pre_entry_features": pre_entry,
                      "same_target_transaction_features": same_transaction}, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
