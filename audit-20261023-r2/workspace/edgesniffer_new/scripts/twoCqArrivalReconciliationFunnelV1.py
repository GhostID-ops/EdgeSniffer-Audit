#!/usr/bin/env python3
"""Publish an outcome-blind provenance funnel for recent direct 2CQ detections."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sqlite3
import sys
from typing import Any



SCHEMA = "2CQ_ARRIVAL_RECONCILIATION_FUNNEL_V1"


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_recent_jsonl(path: pathlib.Path, limit: int) -> list[dict[str, Any]]:
    rows: collections.deque[dict[str, Any]] = collections.deque(maxlen=limit)
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("signature"):
                rows.append(row)
    return list(rows)


def matching_rows(path: pathlib.Path, signatures: set[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    if not path.exists() or not signatures:
        return found
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            signature = str(row.get("signature") or "")
            if signature in signatures:
                found[signature] = row
    return found


def decision_rows(path: pathlib.Path, signatures: set[str]) -> dict[str, dict[str, Any]]:
    if not path.exists() or not signatures:
        return {}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" for _ in signatures)
        rows = connection.execute(
            f"SELECT signature,status,reasons_json,updated_utc FROM current_decisions "
            f"WHERE signature IN ({placeholders})", tuple(sorted(signatures))
        ).fetchall()
    finally:
        connection.close()
    return {
        str(signature): {
            "status": str(status),
            "reasons": json.loads(reasons_json) if reasons_json else [],
            "updatedUtc": updated_utc,
        }
        for signature, status, reasons_json, updated_utc in rows
    }


def atomic_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    direct = read_recent_jsonl(args.yellowstone_direct, args.limit)
    signatures = {str(row["signature"]) for row in direct}
    wallet_candidates = matching_rows(args.wallet_candidates, signatures)
    fallback_rows = matching_rows(args.yellowstone_recovery_ledger, signatures)
    decisions = decision_rows(args.decision_state, signatures)

    rows: list[dict[str, Any]] = []
    for direct_row in reversed(direct):
        signature = str(direct_row["signature"])
        candidate = wallet_candidates.get(signature)
        fallback = fallback_rows.get(signature)
        decision = decisions.get(signature)
        if decision is not None:
            reconciliation = decision["status"]
        elif fallback is not None:
            reconciliation = "YELLOWSTONE_FALLBACK_READY_FOR_MATERIALIZATION"
        else:
            reconciliation = "AWAITING_FINALIZED_RECONCILIATION"
        compact_provenance = (
            "WALLET_FILTERED_ARRIVAL_PRESENT" if candidate is not None
            else "YELLOWSTONE_FALLBACK_RECORDED" if fallback is not None
            else "PENDING_NORMAL_MATERIALIZER_COMPACT_JOIN"
        )
        rows.append({
            "signature": signature,
            "slot": direct_row.get("sourceSlot"),
            "kind": direct_row.get("kind"),
            "inputMint": direct_row.get("inputMint"),
            "outputMint": direct_row.get("outputMint"),
            "directSource": direct_row.get("source"),
            "allenharkWalletCandidatePresent": candidate is not None,
            "allenharkCompactProvenance": compact_provenance,
            "yellowstoneFallbackRecorded": fallback is not None,
            "reconciliationState": reconciliation,
            "decisionStatus": decision["status"] if decision else None,
            "decisionReasons": decision["reasons"] if decision else [],
            "decisionUpdatedUtc": decision["updatedUtc"] if decision else None,
        })

    counts = collections.Counter(row["reconciliationState"] for row in rows)
    summary = {
        "schema": SCHEMA,
        "atUtc": now_utc(),
        "window": {"latestDirectRecords": len(rows), "directLedger": str(args.yellowstone_direct)},
        "counts": {
            "allenharkWalletCandidatePresent": sum(row["allenharkWalletCandidatePresent"] for row in rows),
            "compactJoinPendingNormalMaterializer": sum(
                row["allenharkCompactProvenance"] == "PENDING_NORMAL_MATERIALIZER_COMPACT_JOIN"
                for row in rows
            ),
            "yellowstoneFallbackRecorded": sum(row["yellowstoneFallbackRecorded"] for row in rows),
            "reconciliationStates": dict(sorted(counts.items())),
        },
        "rows": rows,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(args.output, summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yellowstone-direct", type=pathlib.Path, required=True)
    parser.add_argument("--wallet-candidates", type=pathlib.Path, required=True)
    parser.add_argument("--yellowstone-recovery-ledger", type=pathlib.Path, required=True)
    parser.add_argument("--decision-state", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--limit", type=int, default=32)
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
