#!/usr/bin/env python3
"""Bounded outcome-blind parity check for the indexed 2CQ causal state plane."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import pathlib
import sqlite3
from typing import Any

import twoCqIndexedCausalStatePlaneV1 as indexed
import twoCqProspectiveSymmetricChoiceMaterializerV1 as legacy
import twoCqProspectiveSymmetricChoiceMaterializerV2 as v2


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def fingerprint_universe(events: dict, creates: dict, incomplete: dict) -> str:
    payload = {
        "events": {
            mint: [dataclasses.asdict(event) for event in rows]
            for mint, rows in sorted(events.items())
        },
        "creates": creates,
        "incomplete": {
            mint: sorted((list(order), reason) for order, reason in rows)
            for mint, rows in sorted(incomplete.items())
        },
    }
    return hashlib.sha256(canonical(payload)).hexdigest()


def component_hashes(events: dict, creates: dict, incomplete: dict) -> dict[str, Any]:
    event_rows = {
        mint: [dataclasses.asdict(event) for event in rows]
        for mint, rows in sorted(events.items())
    }
    incomplete_rows = {
        mint: sorted((list(order), reason) for order, reason in rows)
        for mint, rows in sorted(incomplete.items())
    }
    return {
        "eventsSha256": hashlib.sha256(canonical(event_rows)).hexdigest(),
        "createsSha256": hashlib.sha256(canonical(creates)).hexdigest(),
        "incompleteSha256": hashlib.sha256(canonical(incomplete_rows)).hexdigest(),
        "eventMintN": len(event_rows),
        "eventN": sum(len(rows) for rows in event_rows.values()),
        "createN": len(creates),
        "incompleteMintN": len(incomplete_rows),
        "incompleteN": sum(len(rows) for rows in incomplete_rows.values()),
        "eventMintHashes": {
            mint: hashlib.sha256(canonical(rows)).hexdigest() for mint, rows in event_rows.items()
        },
        "createHashes": {
            mint: hashlib.sha256(canonical(row)).hexdigest() for mint, row in creates.items()
        },
        "incompleteMintHashes": {
            mint: hashlib.sha256(canonical(rows)).hexdigest()
            for mint, rows in incomplete_rows.items()
        },
    }


def differing_keys(left: dict[str, str], right: dict[str, str]) -> list[str]:
    return sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-db", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--state-plane", type=pathlib.Path, required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    args = parser.parse_args()

    connection = sqlite3.connect(args.decision_db)
    row = connection.execute(
        """SELECT signature,mint,route,economic_family,choice_json,detail_json
           FROM current_decisions WHERE status='ONLINE_ELIGIBLE'
           ORDER BY block_time_ms DESC LIMIT 1"""
    ).fetchone()
    connection.close()
    if row is None:
        raise RuntimeError("NO_ONLINE_ELIGIBLE_PARITY_ROW")
    signature, mint, route, family, choice_json, detail_json = row
    prior_choice = json.loads(choice_json)
    detail = json.loads(detail_json)
    boundary = prior_choice["decisionBoundary"]
    boundary_ns = int(boundary["arrivalUnixNs"])
    paths: dict[str, str] = {}
    for status in detail["reconciliation"]:
        for item in status["inputEvidence"]:
            path = (args.exact_root / item["path"]).resolve()
            paths[str(path)] = str(item["sha256"])
    evidence_paths = [pathlib.Path(path) for path in sorted(paths)]
    selected = {
        "signature": signature,
        "mint": mint,
        "route": route,
        "economicFamily": family,
        "acquisitionClass": prior_choice["acquisitionClass"],
        "entryReceivedAtUnixNs": boundary_ns,
        "entrySlot": int(boundary["slot"]),
        "entryTransactionIndex": int(boundary["transactionIndex"]),
        "teacherActionProof": prior_choice["teacherActionProof"],
    }

    old_events, old_creates, old_incomplete, old_evidence = legacy.scan_universe(
        evidence_paths, args.compact_arrivals, [boundary_ns]
    )
    old_fingerprint = fingerprint_universe(old_events, old_creates, old_incomplete)
    old_components = component_hashes(old_events, old_creates, old_incomplete)
    old_choice, old_reasons, old_detail = v2.build_online_choice(
        selected, old_events, old_creates, old_incomplete
    )
    new_events, new_creates, new_incomplete, new_evidence = indexed.ensure_and_materialize(
        args.state_plane, evidence_paths, paths, args.compact_arrivals, [boundary_ns]
    )
    new_fingerprint = fingerprint_universe(new_events, new_creates, new_incomplete)
    new_components = component_hashes(new_events, new_creates, new_incomplete)
    incomplete_differences = differing_keys(
        old_components["incompleteMintHashes"], new_components["incompleteMintHashes"]
    )
    new_choice, new_reasons, new_detail = v2.build_online_choice(
        selected, new_events, new_creates, new_incomplete
    )
    result = {
        "schema": "2CQ_INDEXED_CAUSAL_STATE_PLANE_PARITY_V1",
        "signature": signature,
        "mint": mint,
        "boundaryUnixNs": boundary_ns,
        "evidencePaths": [str(path) for path in evidence_paths],
        "oldUniverseSha256": old_fingerprint,
        "newUniverseSha256": new_fingerprint,
        "oldComponents": {key: value for key, value in old_components.items() if not key.endswith("Hashes")},
        "newComponents": {key: value for key, value in new_components.items() if not key.endswith("Hashes")},
        "differingEventMints": differing_keys(
            old_components["eventMintHashes"], new_components["eventMintHashes"]
        )[:100],
        "differingCreateMints": differing_keys(
            old_components["createHashes"], new_components["createHashes"]
        )[:100],
        "differingIncompleteMints": incomplete_differences[:100],
        "incompleteDifferenceRows": {
            mint: {
                "old": sorted((list(order), reason) for order, reason in old_incomplete.get(mint, [])),
                "new": sorted((list(order), reason) for order, reason in new_incomplete.get(mint, [])),
            }
            for mint in incomplete_differences[:20]
        },
        "universeExactMatch": old_fingerprint == new_fingerprint,
        "choiceExactMatch": canonical(old_choice) == canonical(new_choice),
        "reasonsExactMatch": old_reasons == new_reasons,
        "detailExactMatch": canonical(old_detail) == canonical(new_detail),
        "oldEvidence": old_evidence,
        "newEvidence": new_evidence,
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "submissions": 0,
    }
    result["verdict"] = (
        "INDEXED_CAUSAL_STATE_PLANE_PARITY_PASS"
        if all(result[key] for key in (
            "universeExactMatch", "choiceExactMatch", "reasonsExactMatch", "detailExactMatch"
        ))
        else "INDEXED_CAUSAL_STATE_PLANE_PARITY_BLOCKER"
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["verdict"].endswith("BLOCKER"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
