#!/usr/bin/env python3
"""Read-only ARM08 admission certificate audit.

ARM08 is admitted only when a finalized transaction proves all of:
  * exactly one underlying PumpSwap venue invoked through CPI (not top-level);
  * exactly one canonical PumpSwap BUY transition by the tracked wallet;
  * positive finalized wallet receipt of the purchased mint;
  * distinct signature/event identity from the ARM07 partition; and
  * no decoder, settlement, or ambiguity failure.

This script deliberately produces zero ARM08 observations for direct PumpSwap
transactions. It does not create a cohort or alter the frozen fail-closed
ARM08 policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pumpSwapExactEconomicsV1 as pumpswap
import twoCqRouteEvidenceAdaptersV1 as route


ARM08 = "ARM08_PUMPSWAP_CPI"
ARM07 = "ARM07_PUMPSWAP_DIRECT"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def observation_signature(row: dict[str, Any]) -> str:
    return str(row.get("state", {}).get("raw_evidence", {}).get("signature", ""))


def observation_identity(row: dict[str, Any]) -> tuple[str, str, int, int, int]:
    event = row["state"]["decoded_event"]
    return (
        str(event.get("pool", "")),
        str(event.get("mint", "")),
        int(event.get("slot", row.get("finalized_slot", 0))),
        int(event.get("event_index", 0)),
        int(event.get("base_amount", 0)),
    )


def classify_candidate(
    raw: dict[str, Any],
    arm07_signatures: set[str],
    arm07_identities: set[tuple[str, str, int, int, int]],
) -> tuple[str, dict[str, Any]]:
    signature = str(raw.get("signature", ""))
    tx = raw.get("transaction") or {}
    evidence: dict[str, Any] = {
        "signature": signature,
        "slot": int(raw.get("slot", 0)),
        "candidate_arm_ids": list(raw.get("candidate_arm_ids") or []),
    }
    if raw.get("finalized") is not True or (tx.get("meta") or {}).get("err") is not None:
        return "REJECT_NOT_FINALIZED_OR_FAILED", evidence
    try:
        resolved = route.resolve_route(tx)
    except Exception as error:
        evidence["error"] = f"{type(error).__name__}:{error}"
        return "REJECT_ROUTE_RESOLUTION_ERROR", evidence
    evidence["underlying_venues"] = resolved["underlyingVenues"]
    if len(resolved["underlyingVenues"]) != 1:
        return "REJECT_AMBIGUOUS_UNDERLYING_VENUE", evidence
    venue = resolved["underlyingVenues"][0]
    if venue.get("venue") != "PUMPSWAP" or venue.get("invocation") != "CPI":
        return "REJECT_ARM07_DIRECT_OR_NON_CPI_ROUTE", evidence
    if signature in arm07_signatures:
        return "REJECT_ARM07_SIGNATURE_DUPLICATE", evidence
    try:
        transitions = pumpswap.validate_transaction(tx)
    except Exception as error:
        evidence["error"] = f"{type(error).__name__}:{error}"
        return "REJECT_PUMPSWAP_DECODER_ERROR", evidence
    wallet = str(raw.get("tracked_wallet", ""))
    wallet_buys = [
        row for row in transitions
        if row.get("user") == wallet and row.get("side") == "BUY"
    ]
    evidence["wallet_buy_count"] = len(wallet_buys)
    if len(wallet_buys) != 1:
        return "REJECT_NOT_EXACTLY_ONE_WALLET_BUY", evidence
    transition = wallet_buys[0]
    try:
        settlement = route.exact_wallet_settlement(tx, str(transition["baseMint"]), wallet)
    except Exception as error:
        evidence["error"] = f"{type(error).__name__}:{error}"
        return "REJECT_NO_EXACT_POSITIVE_WALLET_SETTLEMENT", evidence
    identity = (
        str(transition["pool"]),
        str(transition["baseMint"]),
        int(raw.get("slot", 0)),
        0,
        int(transition["amountRaw"]),
    )
    evidence["transition"] = transition
    evidence["settlement"] = settlement
    evidence["identity"] = list(identity)
    if identity in arm07_identities:
        return "REJECT_ARM07_EVENT_IDENTITY_DUPLICATE", evidence
    return "ADMIT_ARM08_GENUINE_CPI_WALLET_BUY", evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--arm07-observations", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    arm07_rows = [row for path in args.arm07_observations for row in load_jsonl(path)]
    arm07_signatures = {observation_signature(row) for row in arm07_rows if observation_signature(row)}
    arm07_identities = {observation_identity(row) for row in arm07_rows}
    raw_rows = load_jsonl(args.raw)
    candidates = [row for row in raw_rows if ARM08 in (row.get("candidate_arm_ids") or [])]
    counts: Counter[str] = Counter()
    admissions: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for raw in candidates:
        reason, evidence = classify_candidate(raw, arm07_signatures, arm07_identities)
        counts[reason] += 1
        if reason.startswith("ADMIT_"):
            admissions.append(evidence)
        decisions.append({"signature": evidence["signature"], "slot": evidence["slot"], "decision": reason})
    output = {
        "schema": "KEFI_ARM08_GENUINE_CPI_WALLET_PURCHASE_ADMISSION_AUDIT_V1",
        "read_only": True,
        "research_only": True,
        "cohort_created": False,
        "policy_changed": False,
        "collector_changed": False,
        "arm_id": ARM08,
        "arm07_partition_excluded": True,
        "admission_contract": [
            "finalized successful transaction",
            "exactly one underlying PumpSwap venue invoked through CPI",
            "not top-level/direct PumpSwap (ARM07 route)",
            "exactly one canonical PumpSwap BUY transition by the tracked wallet",
            "positive finalized wallet token settlement for the purchased mint",
            "signature and event identity distinct from ARM07 observations",
            "no decoder, ambiguity, or non-trading failure",
        ],
        "inputs": {
            "raw_path": str(args.raw),
            "raw_sha256": sha256_file(args.raw),
            "raw_rows_total": len(raw_rows),
            "arm08_candidate_rows": len(candidates),
            "arm07_observation_rows": len(arm07_rows),
            "arm07_signature_count": len(arm07_signatures),
        },
        "counts": dict(counts),
        "admitted_arm08_count": len(admissions),
        "admitted_arm08_observations": admissions,
        "decisions": decisions,
        "interpretation": {
            "zero_admitted": "The latest source contains no proven CPI-linked PumpSwap wallet purchases. Its ARM08-labelled candidates resolve to direct PumpSwap or fail closed.",
            "arm07_reuse": "Direct PumpSwap candidates are rejected before economics and cannot be relabeled as ARM08.",
            "promotion": "No ARM08 policy can be evaluated or promoted until at least one genuine CPI admission exists and its independent frozen policy is sealed.",
        },
    }
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "ARM08_ADMISSION_AUDIT_COMPLETE_READ_ONLY",
        "arm08_candidates": len(candidates),
        "admitted_arm08": len(admissions),
        "rejected_direct_or_non_cpi": counts["REJECT_ARM07_DIRECT_OR_NON_CPI_ROUTE"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
