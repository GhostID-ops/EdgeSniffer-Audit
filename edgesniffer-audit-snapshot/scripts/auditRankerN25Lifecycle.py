#!/usr/bin/env python3
"""Stream Ranker N25 JSONL and report lifecycle/ownership anomalies."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


KEYWORDS = (
    "buynoposition",
    "buy_not_confirmed",
    "canonical_position_missing",
    "unmanaged",
    "balanceupdate",
    "balanceproof",
    "exactbalance",
    "positionreleased",
    "releaseblocked",
    "terminalfailed",
    "exitwatch",
    "exitsellpresubmit",
    "exitmark",
    "sellresult",
    "buyresult",
    "buyfinality",
)

ATTEMPT_KINDS = {
    "RUST_SELECTED_CANDIDATE_PERSISTENT_ONE_SHOT_STATE",
    "rustSelectedCandidateBuyResult",
    "rustSelectedCandidateBuyFinality",
    "rustSelectedCandidateLandedBuyCounted",
    "rustExitSellResult",
    "rustExitSellAck",
    "rustSelectedCandidateWalletReconcile",
    "rustSelectedCandidateWalletUnmanagedPosition",
}


def scalar_summary(row: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "kind",
        "atMs",
        "mint",
        "key",
        "signature",
        "sourceSignature",
        "sequence",
        "generation",
        "phase",
        "reason",
        "status",
        "confirmationStatus",
        "ok",
        "error",
        "amountRaw",
        "tokensHeldRaw",
        "actualTokensOutRaw",
        "actualSolOutLamports",
        "expectedSolOutLamports",
        "minSolOutLamports",
        "pnlPct",
        "triggerMarkPnlPct",
        "triggerMarkSource",
        "triggerMarkSlot",
        "triggerMarkObservedAtMs",
        "balanceSource",
        "proofSource",
        "actionPending",
        "liveEntriesUsed",
        "maxLiveEntries",
        "selectedToFirstSendMs",
        "selectedToBuyResultMs",
        "buyResultToFinalityMs",
        "firstSendMs",
        "broadcastFirstAckMs",
        "slot",
    )
    return {key: row[key] for key in keep if key in row}


def inspect(path: Path, incident_mint: str | None) -> dict[str, Any]:
    kind_counts: Counter[str] = Counter()
    malformed = 0
    rows = 0
    affected_by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    relevant_without_mint: list[dict[str, Any]] = []
    attempts_by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    incident_rows: list[dict[str, Any]] = []
    absurd_marks: list[dict[str, Any]] = []
    first_at_ms = None
    last_at_ms = None

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, raw in enumerate(handle, 1):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(row, dict):
                continue
            rows += 1
            kind = str(row.get("kind") or "NO_KIND")
            kind_counts[kind] += 1
            at_ms = row.get("atMs")
            if isinstance(at_ms, int):
                first_at_ms = at_ms if first_at_ms is None else min(first_at_ms, at_ms)
                last_at_ms = at_ms if last_at_ms is None else max(last_at_ms, at_ms)
            mint = row.get("mint")
            text = json.dumps(row, separators=(",", ":"), sort_keys=True).lower()
            relevant = any(keyword in kind.lower() or keyword in text for keyword in KEYWORDS)
            if relevant:
                summary = scalar_summary(row)
                summary["line"] = line_number
                if isinstance(mint, str) and mint:
                    affected_by_mint[mint].append(summary)
                elif len(relevant_without_mint) < 200:
                    relevant_without_mint.append(summary)
            if kind in ATTEMPT_KINDS and isinstance(mint, str) and mint:
                summary = scalar_summary(row)
                summary["line"] = line_number
                attempts_by_mint[mint].append(summary)
            pnl = row.get("pnlPct", row.get("triggerMarkPnlPct"))
            if isinstance(pnl, (int, float)) and abs(float(pnl)) >= 100.0:
                summary = scalar_summary(row)
                summary["line"] = line_number
                absurd_marks.append(summary)
            if incident_mint and mint == incident_mint:
                summary = scalar_summary(row)
                summary["line"] = line_number
                incident_rows.append(summary)

    lifecycle_mints = {
        mint: events
        for mint, events in affected_by_mint.items()
        if any(
            any(
                needle in json.dumps(event, separators=(",", ":")).lower()
                for needle in (
                    "buynoposition",
                    "buy_not_confirmed",
                    "canonical_position_missing",
                    "unmanaged",
                    "cannot update while a rust exit sell is pending",
                )
            )
            for event in events
        )
    }

    return {
        "path": str(path),
        "rows": rows,
        "malformedRows": malformed,
        "firstAtMs": first_at_ms,
        "lastAtMs": last_at_ms,
        "kindCounts": dict(kind_counts.most_common()),
        "attemptMintCount": len(attempts_by_mint),
        "attemptsByMint": dict(sorted(attempts_by_mint.items())),
        "lifecycleAnomalyMints": dict(sorted(lifecycle_mints.items())),
        "absurdMarks": absurd_marks,
        "incidentRows": incident_rows,
        "relevantWithoutMint": relevant_without_mint,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--incident-mint")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {
        "schema": "ranker-n25-lifecycle-audit-v1",
        "inputs": [inspect(path, args.incident_mint) for path in args.paths],
    }
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
