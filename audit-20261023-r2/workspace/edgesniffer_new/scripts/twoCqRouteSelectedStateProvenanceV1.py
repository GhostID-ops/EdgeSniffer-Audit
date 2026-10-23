#!/usr/bin/env python3
"""Publish outcome-blind, route-specific selected-entry provenance for 2CQ.

This is deliberately not a generic OTHER_DEX model and not a choice-model
publisher.  Each row keeps the transaction's deterministically resolved venue
separate, exposes only pre-entry state, and marks every missing causal field
explicitly.  It never opens the official validation artifact or any outcome
source.
"""

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


SCHEMA = "2CQ_ROUTE_SELECTED_STATE_PROVENANCE_V1"
IMPLEMENTATION_VERSION = "1.0.0"
WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"
FLOW_WINDOW_NS = 5_000_000_000


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def open_ro(path: pathlib.Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def output_connection(path: pathlib.Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS selected_states(
            signature TEXT PRIMARY KEY,
            mint TEXT NOT NULL,
            source_family TEXT NOT NULL,
            source_route TEXT NOT NULL,
            venue TEXT NOT NULL,
            venue_provenance TEXT NOT NULL,
            stonkfun_overlay TEXT,
            slot INTEGER NOT NULL,
            transaction_index INTEGER NOT NULL,
            block_time_ms INTEGER NOT NULL,
            selected_entry_json TEXT NOT NULL,
            provenance_json TEXT NOT NULL,
            exact_pre_state_json TEXT,
            direct_realtime_json TEXT NOT NULL,
            contextual_state_json TEXT NOT NULL,
            lifecycle_json TEXT NOT NULL,
            completeness_json TEXT NOT NULL,
            source_generation TEXT NOT NULL,
            updated_utc TEXT NOT NULL,
            outcomes_read INTEGER NOT NULL DEFAULT 0,
            validation_rows_read INTEGER NOT NULL DEFAULT 0,
            submissions INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS selected_states_venue ON selected_states(venue);
        CREATE INDEX IF NOT EXISTS selected_states_stonkfun ON selected_states(stonkfun_overlay);
        """
    )
    return connection


def load_overlay(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                rows[str(row["signature"])] = row
    return rows


def exact_venue(row: sqlite3.Row) -> tuple[str, str]:
    """Resolve the economic venue without guessing from the parent route."""
    if row["resolved_venue"]:
        return str(row["resolved_venue"]), "FINALIZED_PROGRAM_RESOLUTION"
    exact = json.loads(str(row["exact_json"])) if row["exact_json"] else {}
    underlying = exact.get("underlyingVenue")
    if underlying:
        return str(underlying), "EXACT_TRANSITION_UNDERLYING_VENUE"
    route = json.loads(str(row["route_json"]))
    venues = route.get("underlyingVenues") or []
    if len(venues) > 1:
        return "UNRESOLVED_MULTI_VENUE", "MULTIPLE_FINALIZED_VENUES"
    return "UNRESOLVED_OTHER_DEX", "NO_DETERMINISTIC_VENUE"


def pre_state(exact: dict[str, Any]) -> dict[str, Any] | None:
    direct = exact.get("preState")
    if isinstance(direct, dict):
        return direct
    legs = exact.get("routeLegs")
    if isinstance(legs, list) and len(legs) == 1 and isinstance(legs[0], dict):
        nested = legs[0].get("preState")
        if isinstance(nested, dict):
            return nested
    return None


def stripped_entry(exact: dict[str, Any]) -> dict[str, Any]:
    """Teacher action metadata, explicitly not a feature vector for imitation."""
    keys = (
        "schema", "classification", "underlyingVenue", "pool", "poolState",
        "baseMint", "quoteMint", "spentMint", "acquiredMint", "side",
        "instructionCoordinate", "instructionVersion", "amountMode",
        "exactObservedTransition", "selectedAssetPerspective",
    )
    return {key: exact[key] for key in keys if key in exact}


def events_for_signature(connection: sqlite3.Connection, signature: str, mint: str) -> list[sqlite3.Row]:
    # The two route state planes use the same event identifiers but differ in
    # actor column naming.  This query is intentionally direct and finalized.
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(events)")
    }
    actor_column = "payer" if "payer" in columns else "user"
    pool_column = "pool_state" if "pool_state" in columns else "pool"
    return connection.execute(
        f"SELECT signature,event_index,slot,canonical_tx_index,received_at_unix_ns,"
        f"{pool_column} AS pool,base_mint,quote_mint,side,{actor_column} actor,"
        "pre_state_json,post_state_json FROM events "
        "WHERE signature=? AND base_mint=? AND " + actor_column + "=? "
        "ORDER BY event_index",
        (signature, mint, WALLET),
    ).fetchall()


def direct_realtime_state(
    connection: sqlite3.Connection | None, signature: str, mint: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if connection is None:
        return {"status": "STATE_PLANE_NOT_CONFIGURED"}, None
    try:
        rows = events_for_signature(connection, signature, mint)
    except sqlite3.Error as error:
        return {"status": "STATE_PLANE_QUERY_FAILED", "error": type(error).__name__}, None
    if len(rows) != 1:
        return {"status": "SELECTED_EVENT_NOT_UNAMBIGUOUS", "matchingEventN": len(rows)}, None
    row = rows[0]
    received = int(row["received_at_unix_ns"])
    if received <= 0:
        return {"status": "REALTIME_ARRIVAL_MISSING", "matchingEventN": 1}, None
    return {
        "status": "PRESENT",
        "source": "ROUTE_STATE_PLANE_REALTIME_ARRIVAL",
        "receivedAtUnixNs": received,
        "slot": int(row["slot"]),
        "transactionIndex": int(row["canonical_tx_index"]),
        "eventIndex": int(row["event_index"]),
        "pool": str(row["pool"]),
    }, dict(row)


def contextual_selected_state(
    choices: dict[str, sqlite3.Row], signature: str, mint: str,
) -> dict[str, Any]:
    row = choices.get(signature)
    if row is None:
        return {"status": "NO_FULL_WINDOW_CONTEXT"}
    if str(row["status"]) != "ELIGIBLE" or not row["choice_json"]:
        return {"status": "FULL_WINDOW_NOT_ELIGIBLE", "reasons": json.loads(str(row["reasons_json"]))}
    payload = json.loads(str(row["choice_json"]))
    chosen = [item for item in payload.get("states") or [] if item.get("label") and item.get("mint") == mint]
    if len(chosen) != 1:
        return {"status": "CHOICE_SELECTED_STATE_NOT_UNAMBIGUOUS", "matchingStateN": len(chosen)}
    state = chosen[0]
    return {
        "status": "FULL_WINDOW_CAUSAL_CONTEXT",
        "windowMs": 5_000,
        "boundaryUnixNs": payload.get("boundaryUnixNs"),
        "stateSignature": state.get("stateSignature"),
        "stateSlot": state.get("stateSlot"),
        "stateTransactionIndex": state.get("stateTransactionIndex"),
        "stateReceivedAtUnixNs": state.get("stateReceivedAtUnixNs"),
        "pool": state.get("pool"),
        "preEntryState": state.get("state"),
        "recentFlow": state.get("features"),
        "controlN": (payload.get("evidenceSummary") or {}).get("controlN"),
    }


def lifecycle_provenance(venue: str) -> dict[str, Any]:
    if venue == "RAYDIUM_LAUNCHLAB":
        return {
            "status": "UNAVAILABLE_EXISTING_EVIDENCE",
            "missing": ["canonical_create_or_pool_init_to_entry_join"],
            "prospectiveRequirement": "persist finalized LaunchLab create/pool-init signature and realtime arrival keyed by pool/mint",
        }
    if venue == "PUMPSWAP":
        return {
            "status": "UNAVAILABLE_EXISTING_EVIDENCE",
            "missing": ["canonical_pool_initialization_to_entry_join"],
            "prospectiveRequirement": "persist PumpSwap pool-init signature and realtime arrival keyed by pool/mint",
        }
    return {
        "status": "UNAVAILABLE_OR_ROUTE_UNSUPPORTED",
        "missing": ["route_specific_lifecycle_registry"],
        "prospectiveRequirement": "venue-specific create/pool-init registry with finalized and realtime provenance",
    }


def row_generation(route: sqlite3.Row, decision: sqlite3.Row | None, choice: sqlite3.Row | None, overlay: dict[str, Any] | None) -> str:
    payload = {
        "implementationVersion": IMPLEMENTATION_VERSION,
        "routeSourceGeneration": route["source_generation"],
        "decisionGeneration": None if decision is None else decision["generation_sha"],
        "choiceGeneration": None if choice is None else choice["source_generation"],
        "overlay": overlay,
    }
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def run(args: argparse.Namespace) -> dict[str, Any]:
    route = open_ro(args.route_state)
    source = open_ro(args.source_state)
    choices_db = open_ro(args.choice_state)
    launch = open_ro(args.launchlab_state) if args.launchlab_state.exists() else None
    pumpswap = open_ro(args.pumpswap_state) if args.pumpswap_state.exists() else None
    overlay = load_overlay(args.stonkfun_overlay)

    source_rows = {
        str(row["signature"]): row for row in source.execute(
            "SELECT signature,mint,route,economic_family,block_time_ms,slot,transaction_index,generation_sha "
            "FROM current_decisions"
        )
    }
    choice_rows = {
        str(row["signature"]): row for row in choices_db.execute(
            "SELECT signature,lane,status,reasons_json,choice_json,source_generation FROM decisions"
        )
    }
    exact_rows = list(route.execute(
        "SELECT * FROM route_evidence WHERE classification='EXACT_SELECTED_TRANSITION' "
        "ORDER BY signature"
    ))

    args.output.mkdir(parents=True, exist_ok=True)
    output = output_connection(args.output / "state.sqlite3")
    processed = 0
    for item in exact_rows:
        signature = str(item["signature"])
        decision = source_rows.get(signature)
        if decision is None:
            # Source provenance cannot be reconstructed safely without the
            # immutable decision order, so keep the source out of the adapter.
            continue
        venue, venue_provenance = exact_venue(item)
        exact = json.loads(str(item["exact_json"]))
        overlay_row = overlay.get(signature)
        route_plane = launch if venue == "RAYDIUM_LAUNCHLAB" else pumpswap if venue == "PUMPSWAP" else None
        realtime, route_event = direct_realtime_state(route_plane, signature, str(item["mint"]))
        exact_state = pre_state(exact)
        choice = choice_rows.get(signature)
        context = contextual_selected_state(choice_rows, signature, str(item["mint"]))
        complete = {
            "exactSelectedTransition": True,
            "exactPreEntryState": exact_state is not None,
            "directRealtimeArrival": realtime.get("status") == "PRESENT",
            "fullFiveSecondCausalContext": context.get("status") == "FULL_WINDOW_CAUSAL_CONTEXT",
            "symmetricChoiceProven": context.get("status") == "FULL_WINDOW_CAUSAL_CONTEXT",
            "outcomeBlind": True,
        }
        provenance = {
            "canonicalFinalizedTeacherAction": {
                "signature": signature,
                "slot": int(decision["slot"]),
                "transactionIndex": int(decision["transaction_index"]),
                "blockTimeMs": int(decision["block_time_ms"]),
            },
            "venue": venue,
            "venueProvenance": venue_provenance,
            "routeFamily": str(item["source_family"]),
            "sourceRoute": str(item["source_route"]),
            "stonkfunOverlay": None if overlay_row is None else {
                "classification": overlay_row.get("overlay"),
                "evidence": overlay_row.get("evidence"),
            },
        }
        generation = row_generation(item, decision, choice, overlay_row)
        output.execute(
            "INSERT OR REPLACE INTO selected_states VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                signature, str(item["mint"]), str(item["source_family"]), str(item["source_route"]),
                venue, venue_provenance,
                None if overlay_row is None else str(overlay_row.get("overlay")),
                int(decision["slot"]), int(decision["transaction_index"]), int(decision["block_time_ms"]),
                canonical(stripped_entry(exact)), canonical(provenance),
                None if exact_state is None else canonical(exact_state), canonical(realtime),
                canonical(context), canonical(lifecycle_provenance(venue)), canonical(complete),
                generation, utc_now(), 0, 0, 0,
            ),
        )
        processed += 1
    output.commit()
    output.row_factory = sqlite3.Row
    rows = list(output.execute("SELECT * FROM selected_states ORDER BY slot,transaction_index,signature"))
    output.close()
    route.close(); source.close(); choices_db.close()
    if launch is not None:
        launch.close()
    if pumpswap is not None:
        pumpswap.close()

    grouped: dict[str, list[sqlite3.Row]] = collections.defaultdict(list)
    for row in rows:
        grouped[str(row["venue"])].append(row)
    by_venue: dict[str, dict[str, int]] = {}
    for venue, entries in grouped.items():
        def truth(column: str) -> int:
            return sum(bool(json.loads(str(item["completeness_json"])).get(column)) for item in entries)
        by_venue[venue] = {
            "exactSelectedTransitionN": len(entries),
            "exactPreEntryStateN": truth("exactPreEntryState"),
            "directRealtimeArrivalN": truth("directRealtimeArrival"),
            "fullFiveSecondCausalContextN": truth("fullFiveSecondCausalContext"),
            "symmetricChoiceProvenN": truth("symmetricChoiceProven"),
            "stonkfunMarkedN": sum(item["stonkfun_overlay"] is not None for item in entries),
        }
    output_rows = args.output / "selected-states.jsonl"
    temporary = output_rows.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for item in rows:
            result = dict(item)
            for key in (
                "selected_entry_json", "provenance_json", "exact_pre_state_json",
                "direct_realtime_json", "contextual_state_json", "lifecycle_json", "completeness_json",
            ):
                value = result.pop(key)
                result[{
                    "selected_entry_json": "selectedEntry",
                    "provenance_json": "provenance",
                    "exact_pre_state_json": "exactPreEntryState",
                    "direct_realtime_json": "directRealtime",
                    "contextual_state_json": "contextualState",
                    "lifecycle_json": "lifecycle",
                    "completeness_json": "completeness",
                }[key]] = None if value is None else json.loads(str(value))
            stream.write(canonical(result) + "\n")
    os.replace(temporary, output_rows)

    report = {
        "schema": SCHEMA + "_STATUS",
        "implementationVersion": IMPLEMENTATION_VERSION,
        "generatedAtUtc": utc_now(),
        "selectedStateRowsN": len(rows),
        "processedThisRun": processed,
        "byVenue": dict(sorted(by_venue.items())),
        "routeSemantics": {
            "RAYDIUM_LAUNCHLAB": "exact selected pre-state; 5s causal context only where state plane proves it",
            "PUMPSWAP": "exact selected pre-state; 5s causal context only where pool window proves it",
            "METEORA_DBC": "exact selected transition; no pre-entry reserve snapshot in current adapter",
            "RAYDIUM_CLMM": "exact selected transition; no pre-entry pool snapshot in current adapter",
            "RAYDIUM_CPMM": "exact selected pre-state; no separately proven recent-flow window",
            "UNRESOLVED_MULTI_VENUE": "preserved separately and never merged into a generic causal model",
            "UNRESOLVED_OTHER_DEX": "preserved separately and never guessed",
        },
        "fieldPolicy": {
            "postEntryStateExcluded": True,
            "outcomeBlind": True,
            "validationRowsRead": 0,
            "outcomesRead": 0,
            "submissions": 0,
        },
        "officialSolPumpCohortModified": False,
        "outputRowsSha256": sha256_file(output_rows),
    }
    atomic_json(args.output / "status.json", report)
    atomic_json(args.output / "provenance.json", {
        "schema": SCHEMA + "_PROVENANCE",
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "sourceStateSha256": sha256_file(args.source_state),
        "routeStateSha256": sha256_file(args.route_state),
        "choiceStateSha256": sha256_file(args.choice_state),
        "stonkfunOverlaySha256": sha256_file(args.stonkfun_overlay) if args.stonkfun_overlay.exists() else None,
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "submissions": 0,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-state", type=pathlib.Path, required=True)
    parser.add_argument("--route-state", type=pathlib.Path, required=True)
    parser.add_argument("--choice-state", type=pathlib.Path, required=True)
    parser.add_argument("--launchlab-state", type=pathlib.Path, required=True)
    parser.add_argument("--pumpswap-state", type=pathlib.Path, required=True)
    parser.add_argument("--stonkfun-overlay", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
