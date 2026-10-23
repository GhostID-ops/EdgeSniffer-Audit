#!/usr/bin/env python3
"""Outcome-blind, route-native symmetric choices for 2CQ Raydium CLMM entries."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
from typing import Any


SCHEMA = "2CQ_RAYDIUM_CLMM_SYMMETRIC_CHOICE_V1"
IMPLEMENTATION_VERSION = "1.0.0"
WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"
LANE = "RAYDIUM_CLMM_ACTIVE_5000MS_SAME_SPEND_ASSET"
WINDOW_NS = 5_000_000_000


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def connect_ro(path: pathlib.Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    connection.row_factory = sqlite3.Row
    return connection


def open_output(path: pathlib.Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS decisions(
            signature TEXT PRIMARY KEY,
            lane TEXT NOT NULL,
            mint TEXT NOT NULL,
            spent_mint TEXT NOT NULL,
            boundary_ns INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            tx_index INTEGER NOT NULL,
            status TEXT NOT NULL,
            reasons_json TEXT NOT NULL,
            choice_json TEXT,
            source_generation TEXT NOT NULL,
            updated_utc TEXT NOT NULL,
            outcomes_read INTEGER NOT NULL DEFAULT 0,
            validation_rows_read INTEGER NOT NULL DEFAULT 0,
            submissions INTEGER NOT NULL DEFAULT 0
        );
    """)
    return connection


def contract() -> dict[str, Any]:
    return {
        "schema": SCHEMA + "_FROZEN_CONTRACT",
        "implementationVersion": IMPLEMENTATION_VERSION,
        "teacherWallet": WALLET,
        "lane": LANE,
        "decisionBoundary": "IMMEDIATELY_BEFORE_TEACHER_ENTRY_BY_REALTIME_RECEIPT_AND_CHAIN_ORDER",
        "universe": "ALL_EXACT_CLMM_POOLS_ACTIVE_IN_PRIOR_5000MS_WITH_SAME_ROUTE_SPEND_ASSET",
        "requirements": [
            "selected CLMM acquisition exact and wallet-reconciled",
            "candidate CLMM transition exact and causally received before boundary",
            "same route-native spend asset",
            "at least one earlier external transition",
            "teacher not already exposed to candidate inside active window",
            "at least one contemporaneous alternative",
            "any relevant exact gap excludes fail-closed",
        ],
        "outcomesPermitted": False,
        "submissionsPermitted": False,
        "crossTeacherBlendingPermitted": False,
        "solPumpQualificationChanged": False,
    }


def freeze_contract(output: pathlib.Path) -> pathlib.Path:
    path = output / "contract.json"
    payload = (json.dumps(contract(), indent=2, sort_keys=True) + "\n").encode()
    if path.exists() and path.read_bytes() != payload:
        raise RuntimeError("CLMM_SYMMETRIC_CONTRACT_CHANGED")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        os.chmod(path, 0o444)
    return path


def selected_rows(
    source_state: pathlib.Path, route_state: pathlib.Path,
) -> list[dict[str, Any]]:
    source = connect_ro(source_state)
    routes = connect_ro(route_state)
    exact = {
        str(row["signature"]): row for row in routes.execute(
            "SELECT signature,mint,exact_json,source_generation FROM route_evidence "
            "WHERE classification='EXACT_SELECTED_TRANSITION' "
            "AND resolved_venue='RAYDIUM_CLMM'"
        )
    }
    rows: list[dict[str, Any]] = []
    for row in source.execute(
        "SELECT signature,mint,slot,transaction_index FROM current_decisions "
        "ORDER BY slot,transaction_index,signature"
    ):
        evidence = exact.get(str(row["signature"]))
        if evidence is None:
            continue
        payload = json.loads(str(evidence["exact_json"]))
        if str(payload.get("acquiredMint") or "") != str(row["mint"]):
            continue
        spent = str(payload.get("spentMint") or "")
        if not spent:
            continue
        rows.append(dict(row) | {
            "spentMint": spent,
            "sourceGeneration": str(evidence["source_generation"]),
        })
    source.close()
    routes.close()
    return rows


def event_features(rows: list[sqlite3.Row], boundary: int) -> dict[str, Any]:
    buys = sum(1 for row in rows if str(row["output_mint"]) not in {""})
    return {
        "eventCount": len(rows),
        "swapCount": buys,
        "uniqueActorCount": len({str(row["actor"]) for row in rows if row["actor"]}),
        "lastEventAgeMs": (boundary - int(rows[-1]["received_at_unix_ns"])) / 1_000_000,
    }


def materialize_choice(
    selected: dict[str, Any], events: list[sqlite3.Row], gaps: list[sqlite3.Row],
    completed_windows: set[str], slot_bounds: tuple[int, int],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any], int]:
    if selected["signature"] not in completed_windows:
        return None, ["SOURCE_RECONCILIATION_PENDING"], {}, 0
    selected_events = [
        row for row in events
        if str(row["signature"]) == selected["signature"]
        and str(row["output_mint"]) == selected["mint"]
        and str(row["actor"]) == WALLET
    ]
    if not selected_events:
        reason = (
            "SELECTED_PRE_STATE_UNIVERSE_START" if int(selected["slot"]) < slot_bounds[0]
            else "EXACT_SELECTED_EVENT_NOT_IN_STATE_PLANE"
        )
        return None, [reason], {"selectedMatches": 0, "sourceSlotBounds": slot_bounds}, 0
    receipts = {int(row["received_at_unix_ns"]) for row in selected_events}
    if len(receipts) != 1 or next(iter(receipts)) <= 0:
        return None, ["SELECTED_REALTIME_ARRIVAL_MISSING"], {
            "selectedMatches": len(selected_events), "receiptCardinality": len(receipts),
        }, 0
    boundary = next(iter(receipts))
    window_start = boundary - WINDOW_NS
    boundary_order = (int(selected["slot"]), int(selected["transaction_index"]), 1 << 30)
    relevant_gaps = [
        row for row in gaps
        if (int(row["received_at_unix_ns"]) >= window_start
            or (int(row["received_at_unix_ns"]) <= 0
                and int(row["slot"]) >= int(selected["slot"]) - 20))
        and (int(row["slot"]), int(row["canonical_tx_index"]), 1 << 30) < boundary_order
    ]
    if any(row["pool"] is None for row in relevant_gaps):
        return None, ["UNSCOPED_INEXACT_EVENT_GAP"], {
            "inexactGapN": len(relevant_gaps), "failClosed": True,
        }, boundary
    tainted = {str(row["pool"]) for row in relevant_gaps}
    relevant = [
        row for row in events
        if window_start <= int(row["received_at_unix_ns"]) < boundary
        and (int(row["slot"]), int(row["canonical_tx_index"]), int(row["event_index"]))
        < boundary_order
        and selected["spentMint"] in {str(row["token_0_mint"]), str(row["token_1_mint"])}
    ]
    by_pool: dict[str, list[sqlite3.Row]] = collections.defaultdict(list)
    for row in relevant:
        by_pool[str(row["pool"])].append(row)
    choices: list[dict[str, Any]] = []
    exclusions: collections.Counter[str] = collections.Counter()
    for pool, rows in sorted(by_pool.items()):
        if pool in tainted:
            exclusions["POOL_INEXACT_EVENT_GAP"] += 1
            continue
        rows.sort(key=lambda row: (
            int(row["slot"]), int(row["canonical_tx_index"]), int(row["event_index"])
        ))
        last = rows[-1]
        token_0, token_1 = str(last["token_0_mint"]), str(last["token_1_mint"])
        candidate = token_1 if selected["spentMint"] == token_0 else token_0
        if candidate == selected["spentMint"]:
            exclusions["INVALID_IDENTICAL_POOL_ASSETS"] += 1
            continue
        external = [row for row in rows if str(row["actor"]) != WALLET]
        if not external:
            exclusions["NO_PRIOR_EXTERNAL_STATE"] += 1
            continue
        if any(
            str(row["actor"]) == WALLET and str(row["output_mint"]) == candidate
            for row in rows
        ):
            exclusions["TEACHER_ALREADY_EXPOSED"] += 1
            continue
        choices.append({
            "label": candidate == selected["mint"], "mint": candidate,
            "spentMint": selected["spentMint"], "pool": pool,
            "stateSignature": str(last["signature"]), "stateSlot": int(last["slot"]),
            "stateTransactionIndex": int(last["canonical_tx_index"]),
            "stateReceivedAtUnixNs": int(last["received_at_unix_ns"]),
            "state": json.loads(str(last["post_state_json"])),
            "features": event_features(rows, boundary),
        })
    deduped: dict[str, dict[str, Any]] = {}
    for row in choices:
        prior = deduped.get(row["mint"])
        if prior is None or int(row["stateReceivedAtUnixNs"]) > int(prior["stateReceivedAtUnixNs"]):
            deduped[row["mint"]] = row
    choices = list(deduped.values())
    chosen = [row for row in choices if row["label"]]
    reasons: list[str] = []
    if len(chosen) != 1:
        reasons.append("SELECTED_MINT_ABSENT_FROM_SYMMETRIC_UNIVERSE")
    if len(choices) <= 1:
        reasons.append("NO_CONTEMPORANEOUS_ALTERNATIVE")
    detail = {
        "eligibleCandidateN": len(choices), "controlN": max(0, len(choices) - len(chosen)),
        "candidateExclusions": dict(sorted(exclusions.items())),
    }
    if reasons:
        return None, reasons, detail, boundary
    return {
        "schema": SCHEMA + "_DECISION", "lane": LANE,
        "signature": selected["signature"], "selectedMint": selected["mint"],
        "spentMint": selected["spentMint"], "boundaryUnixNs": boundary,
        "boundarySlot": selected["slot"],
        "boundaryTransactionIndex": selected["transaction_index"],
        "states": sorted(choices, key=lambda row: row["mint"]),
        "evidenceSummary": detail, "outcomesRead": 0,
        "validationRowsRead": 0, "submissions": 0,
    }, [], detail, boundary


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    frozen = freeze_contract(args.output)
    selected = selected_rows(args.source_state, args.route_state)
    source = connect_ro(args.clmm_state)
    events = list(source.execute(
        "SELECT * FROM clmm_events ORDER BY slot,canonical_tx_index,event_index"
    ))
    gaps = list(source.execute(
        "SELECT * FROM exact_gaps ORDER BY slot,canonical_tx_index,signature"
    ))
    completed = {
        str(row[0]) for row in source.execute(
            "SELECT selected_signature FROM choice_windows WHERE status='COMPLETE'"
        )
    }
    bounds_row = source.execute(
        "SELECT COALESCE(MIN(slot),0),COALESCE(MAX(slot),0) FROM arrivals"
    ).fetchone()
    bounds = (int(bounds_row[0]), int(bounds_row[1]))
    output = open_output(args.output / "state.sqlite3")
    processed = 0
    for row in selected:
        generation = hashlib.sha256(canonical({
            "source": row["sourceGeneration"], "implementation": IMPLEMENTATION_VERSION,
            "contract": sha256_file(frozen),
        }).encode()).hexdigest()
        prior = output.execute(
            "SELECT source_generation,status FROM decisions WHERE signature=?", (row["signature"],)
        ).fetchone()
        if prior and str(prior[0]) == generation and str(prior[1]) != "DEFERRED":
            continue
        try:
            choice, reasons, detail, boundary = materialize_choice(
                row, events, gaps, completed, bounds
            )
            status = "ELIGIBLE" if choice else (
                "DEFERRED" if any("PENDING" in reason for reason in reasons) else "EXCLUDED"
            )
        except Exception as error:
            choice, reasons, detail, boundary, status = (
                None, ["MATERIALIZATION_ERROR"],
                {"errorType": type(error).__name__, "error": str(error)[:1000]}, 0, "DEFERRED",
            )
        output.execute(
            "INSERT OR REPLACE INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["signature"], LANE, row["mint"], row["spentMint"], boundary,
                row["slot"], row["transaction_index"], status, canonical(reasons),
                canonical(choice) if choice else None, generation, now_utc(), 0, 0, 0,
            ),
        )
        processed += 1
    output.commit()
    rows = list(output.execute("SELECT * FROM decisions ORDER BY slot,tx_index,signature"))
    statuses = collections.Counter(str(row["status"]) for row in rows)
    reasons = collections.Counter(
        reason for row in rows for reason in json.loads(str(row["reasons_json"]))
    )
    max_controls = max((
        int(json.loads(str(row["choice_json"]))["evidenceSummary"]["controlN"])
        for row in rows if row["choice_json"]
    ), default=0)
    output.close()
    source.close()
    report = {
        "schema": SCHEMA + "_STATUS", "implementationVersion": IMPLEMENTATION_VERSION,
        "generatedAtUtc": now_utc(), "selectedTransitionsN": len(selected),
        "processedThisRun": processed, "statusCounts": dict(sorted(statuses.items())),
        "reasonCounts": dict(sorted(reasons.items())), "maxControls": max_controls,
        "universeAuthority": "TEACHER_BOUNDARY_CONDITIONED_CANONICAL_FINALIZED_CLMM_WINDOWS",
        "solPumpQualificationChanged": False,
        "integrity": {"outcomesRead": 0, "validationRowsRead": 0, "submissions": 0},
    }
    atomic_json(args.output / "status.json", report)
    atomic_json(args.output / "provenance.json", {
        "schema": SCHEMA + "_PROVENANCE", "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "contractSha256": sha256_file(frozen), "sourceStateSha256": sha256_file(args.source_state),
        "routeStateSha256": sha256_file(args.route_state), "clmmStateSha256": sha256_file(args.clmm_state),
        "outcomesRead": 0, "validationRowsRead": 0, "submissions": 0,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-state", type=pathlib.Path, required=True)
    parser.add_argument("--route-state", type=pathlib.Path, required=True)
    parser.add_argument("--clmm-state", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
