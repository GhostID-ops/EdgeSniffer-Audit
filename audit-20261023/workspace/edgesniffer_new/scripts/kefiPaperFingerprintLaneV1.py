#!/usr/bin/env python3
"""Build an outcome-blind paper shadow from the frozen kEFi entry envelope."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", type=Path, required=True)
    parser.add_argument("--historical", type=Path, required=True)
    parser.add_argument("--out-signals", type=Path, required=True)
    parser.add_argument("--out-summary", type=Path, required=True)
    parser.add_argument("--out-contract", type=Path, required=True)
    args = parser.parse_args()

    fresh = [
        json.loads(line)
        for line in args.fresh.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    historical = json.loads(args.historical.read_text(encoding="utf-8"))
    state = historical["entryState"]
    base = state["baseAmountOverPrePoolBase"]
    quote = state["poolQuoteDeltaOverPrePoolQuote"]
    bounds = {
        "baseFractionMin": base["min"],
        "baseFractionMax": base["max"],
        "quoteFractionMin": quote["min"],
        "quoteFractionMax": quote["max"],
    }

    rows = []
    for row in fresh:
        base_fraction = row["base_amount"] / row["pre_pool_base"]
        quote_fraction = row["pool_quote_delta"] / row["pre_pool_quote"]
        base_match = bounds["baseFractionMin"] <= base_fraction <= bounds["baseFractionMax"]
        quote_match = bounds["quoteFractionMin"] <= quote_fraction <= bounds["quoteFractionMax"]
        rows.append(
            {
                "sourcePartition": row["source_partition"],
                "sourceFrameSha256": row["source_frame_sha256"],
                "receivedAtUnixNs": row["received_at_unix_ns"],
                "slot": row["slot"],
                "signature": row["signature"],
                "eventIndex": row["event_index"],
                "selectedMint": row["selected_mint"],
                "pool": row["pool"],
                "baseFraction": base_fraction,
                "quoteFraction": quote_fraction,
                "baseEnvelopeMatch": base_match,
                "quoteEnvelopeMatch": quote_match,
                "paperFingerprintMatch": base_match and quote_match,
                "outcome": None,
                "pnl": None,
            }
        )

    args.out_signals.parent.mkdir(parents=True, exist_ok=True)
    with args.out_signals.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    counts = Counter(
        "MATCH" if row["paperFingerprintMatch"] else "OUTSIDE_HISTORICAL_ENVELOPE"
        for row in rows
    )
    summary = {
        "schema": "KEFI_PAPER_FINGERPRINT_LANE_V1",
        "createdAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "interpretation": "Outcome-blind paper shadow only; no outcomes, PnL, model fitting, or execution.",
        "inputHashes": {"fresh": sha256(args.fresh), "historical": sha256(args.historical)},
        "historicalEnvelope": bounds,
        "freshRows": len(rows),
        "candidateMatches": counts["MATCH"],
        "outsideHistoricalEnvelope": counts["OUTSIDE_HISTORICAL_ENVELOPE"],
        "distinctMintsMatched": len({row["selectedMint"] for row in rows if row["paperFingerprintMatch"]}),
        "sourcePartitions": sorted({row["sourcePartition"] for row in rows}),
        "scientificGuards": {
            "historicalEnvelopeFrozen": True,
            "thresholdsTunedOnFreshData": False,
            "outcomesAccessed": False,
            "pnlAccessed": False,
            "executionEnabled": False,
        },
    }
    args.out_summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    contract = {
        "schema": "KEFI_PAPER_OUTCOME_INPUT_CONTRACT_V1",
        "createdAtUtc": summary["createdAtUtc"],
        "candidateSignals": str(args.out_signals),
        "requiredOutcomeFields": [
            "entrySignature", "selectedMint", "entryTimestamp", "causalEvidenceHash",
            "firstSellableTimestamp", "exitTimestamp", "outcomeClass", "returnPct",
        ],
        "rules": [
            "Outcomes must be generated from complete post-entry causal evidence.",
            "Missing or incomplete evidence is fail-closed, never imputed.",
            "The paper shadow is not a trading service and cannot submit transactions.",
            "No threshold may be changed after outcome inspection.",
        ],
        "status": "WAITING_FOR_SEPARATE_OUTCOME_REPLAY",
    }
    args.out_contract.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"schema": summary["schema"], "freshRows": len(rows), "candidateMatches": counts["MATCH"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
