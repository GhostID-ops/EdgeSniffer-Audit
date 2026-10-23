#!/usr/bin/env python3
"""Outcome-blind replay over an already proof-gated full-meta state plane."""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3

import twoCqFullMetaCanonicalStatePlaneV1 as adapter
import twoCqProspectiveSymmetricChoiceMaterializerV1 as legacy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-db", type=pathlib.Path, required=True)
    parser.add_argument("--funnel", type=pathlib.Path, required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    args = parser.parse_args()
    funnel = json.loads(args.funnel.read_text(encoding="utf-8"))
    acquisitions = [row for row in funnel["rows"] if row.get("stage") == "CANONICAL_SUCCESSFUL_PUMP_ACQUISITION_PENDING_FULL_WINDOW"]
    connection = sqlite3.connect(args.state_db)
    try:
        events, creates, incomplete = adapter.load_state(connection)
        rows = []
        for acquisition in acquisitions:
            order = connection.execute("SELECT tx_index FROM transactions WHERE signature=?", (str(acquisition["signature"]),)).fetchone()
            for mint in acquisition.get("acquiredMints") or []:
                selected = {
                    "signature": str(acquisition["signature"]), "mint": str(mint),
                    "entryReceivedAtUnixNs": int(acquisition["receivedAtUnixNs"]),
                    "entrySlot": int(acquisition["canonicalSlot"]),
                    "entryTransactionIndex": int(order[0]) if order else -1,
                    "route": "SOL_PUMP_FULL_META_PROSPECTIVE",
                    "acquisitionClass": "CANONICAL_SUCCESSFUL_PUMP_ACQUISITION",
                }
                choice, reasons, detail = legacy.build_choice(selected, events, creates, incomplete)
                rows.append({
                    "signature": selected["signature"], "mint": selected["mint"],
                    "selectedCreatePass": selected["mint"] in creates,
                    "full120sWindowPass": True,
                    "selectedExactStateComplete": not any(reason.startswith("SELECTED_") for reason in reasons),
                    "legalAlternativeWithCompleteState": "NO_CONTEMPORANEOUS_ALTERNATIVE" not in reasons,
                    "symmetricEligible": choice is not None,
                    "failureReasons": reasons, "detail": detail,
                })
        report = {
            "schema": "2CQ_FULL_META_PROSPECTIVE_STATE_REPLAY_V1", "outcomesRead": 0, "submissions": 0,
            "postBoundaryAcquisitionsTested": len(acquisitions), "replayRows": rows,
            "selectedCreatePass": sum(bool(row["selectedCreatePass"]) for row in rows),
            "selectedExactStateComplete": sum(bool(row["selectedExactStateComplete"]) for row in rows),
            "legalAlternativeWithCompleteState": sum(bool(row["legalAlternativeWithCompleteState"]) for row in rows),
            "symmetricEligible": sum(bool(row["symmetricEligible"]) for row in rows),
            "statePlaneTransactions": int(connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]),
            "causalEventsMaterialized": int(connection.execute("SELECT COUNT(*) FROM causal_events").fetchone()[0]),
        }
    finally:
        connection.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "replayRows"}, sort_keys=True))


if __name__ == "__main__":
    main()
