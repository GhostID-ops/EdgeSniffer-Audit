#!/usr/bin/env python3
"""Outcome-blind symmetric choice cohorts for non-SOL Pump, PumpSwap and LaunchLab."""

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


SCHEMA = "2CQ_ROUTE_SPECIFIC_SYMMETRIC_CHOICE_V1"
IMPLEMENTATION_VERSION = "1.3.0"
WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"
LANES = {
    "NON_SOL_PUMP_V2": {
        "name": "NON_SOL_PUMP_V2_LAUNCHED_120S_SAME_QUOTE",
        "windowNs": 120_000_000_000,
        "universe": "ALL_EXACT_CREATEV2_MARKETS_WITH_IDENTICAL_QUOTE_MINT",
    },
    "PUMPSWAP": {
        "name": "PUMPSWAP_ACTIVE_5000MS_SAME_SPEND_ASSET",
        "windowNs": 5_000_000_000,
        "universe": "FULL_PROSPECTIVE_PUMPSWAP_PROGRAM_POPULATION",
    },
    "RAYDIUM_LAUNCHLAB": {
        "name": "LAUNCHLAB_ACTIVE_5000MS_SAME_SPEND_ASSET",
        "windowNs": 5_000_000_000,
        "universe": "FULL_PROSPECTIVE_LAUNCHLAB_PROGRAM_POPULATION",
    },
}


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


def snapshot_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decision_generation(
    selected_source_generation: str, contract_sha256: str, route_state_sha256: str
) -> str:
    return snapshot_sha256({
        "source": selected_source_generation,
        "implementation": IMPLEMENTATION_VERSION,
        "contract": contract_sha256,
        "routeState": route_state_sha256,
    })


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
        CREATE INDEX IF NOT EXISTS decisions_lane_status
          ON decisions(lane,status,boundary_ns,slot,tx_index);
    """)
    return connection


def route_spec() -> dict[str, Any]:
    return {
        "schema": SCHEMA + "_FROZEN_CONTRACT",
        "implementationVersion": "1.0.0",
        "decisionBoundary": "IMMEDIATELY_BEFORE_TEACHER_ENTRY_BY_REALTIME_RECEIPT_AND_CHAIN_ORDER",
        "lanes": LANES,
        "requirements": [
            "selected transition exact and acquired-asset normalized",
            "candidate transition exact and causally received before boundary",
            "same route-native spend asset",
            "at least one earlier external transition",
            "teacher not already exposed to candidate asset",
            "at least one contemporaneous alternative",
            "unresolved source history defers fail-closed",
        ],
        "solPumpQualificationChanged": False,
        "outcomesPermitted": False,
        "submissionsPermitted": False,
    }


def freeze_contract(output: pathlib.Path) -> pathlib.Path:
    path = output / "contract.json"
    payload = (json.dumps(route_spec(), indent=2, sort_keys=True) + "\n").encode()
    if path.exists() and path.read_bytes() != payload:
        raise RuntimeError("ROUTE_SYMMETRIC_CONTRACT_CHANGED")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        os.chmod(path, 0o444)
    return path


def source_rows(source_state: pathlib.Path, route_state: pathlib.Path) -> list[dict[str, Any]]:
    source = connect_ro(source_state)
    evidence = connect_ro(route_state)
    routes = {str(row["signature"]): row for row in evidence.execute(
        "SELECT signature,mint,classification,resolved_venue,exact_json,source_generation "
        "FROM route_evidence WHERE classification='EXACT_SELECTED_TRANSITION'"
    )}
    rows: list[dict[str, Any]] = []
    for row in source.execute(
        "SELECT signature,mint,economic_family,block_time_ms,slot,transaction_index "
        "FROM current_decisions ORDER BY block_time_ms,slot,transaction_index,signature"
    ):
        exact = routes.get(str(row["signature"]))
        if exact is None:
            continue
        venue = str(exact["resolved_venue"] or "")
        family = str(row["economic_family"])
        lane = (
            "NON_SOL_PUMP_V2" if family == "NON_SOL_PUMP_V2" else
            "PUMPSWAP" if venue == "PUMPSWAP" else
            "RAYDIUM_LAUNCHLAB" if venue == "RAYDIUM_LAUNCHLAB" else None
        )
        if lane is None:
            continue
        payload = json.loads(str(exact["exact_json"]))
        acquired = str(payload.get("acquiredMint") or payload.get("mint") or payload.get("baseMint") or "")
        spent = str(payload.get("spentMint") or payload.get("quoteMint") or "")
        if acquired != str(row["mint"]) or not spent:
            continue
        rows.append(dict(row) | {
            "lane": lane, "spentMint": spent, "exact": payload,
            "sourceGeneration": str(exact["source_generation"]),
        })
    source.close()
    evidence.close()
    return rows


def event_features(rows: list[sqlite3.Row], boundary_ns: int) -> dict[str, Any]:
    keys = set(rows[0].keys()) if rows else set()
    buys = sum(
        1 for row in rows
        if str(row["side"]).upper() == "BUY"
        or ("is_buy" in keys and int(row["is_buy"]))
    )
    users = {str(row["actor"]) for row in rows if row["actor"]}
    return {
        "eventCount": len(rows),
        "buyCount": buys,
        "sellCount": len(rows) - buys,
        "uniqueActorCount": len(users),
        "lastEventAgeMs": (boundary_ns - int(rows[-1]["received_ns"])) / 1_000_000,
    }


def non_sol_choice(
    selected: dict[str, Any],
    events_by_mint: dict[str, list[sqlite3.Row]],
    creates_by_quote: dict[str, list[sqlite3.Row]],
    source_bounds: tuple[int, int],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    event = [
        row for row in events_by_mint.get(selected["mint"], [])
        if str(row["signature"]) == selected["signature"]
        and str(row["user"]) == WALLET and int(row["is_buy"]) == 1
    ]
    if len(event) != 1:
        reason = (
            "SOURCE_RECONCILIATION_PENDING" if int(selected["slot"]) > source_bounds[1]
            else "SELECTED_PRE_STATE_UNIVERSE_START" if int(selected["slot"]) < source_bounds[0]
            else "EXACT_SELECTED_EVENT_NOT_IN_STATE_PLANE"
        )
        return None, [reason], {"selectedMatches": len(event), "sourceSlotBounds": source_bounds}
    if int(event[0]["received_ns"]) <= 0:
        return None, ["SELECTED_REALTIME_ARRIVAL_MISSING"], {"selectedMatches": 1}
    boundary = int(event[0]["received_ns"])
    boundary_order = (int(selected["slot"]), int(selected["transaction_index"]), 1 << 30)
    creates = [
        row for row in creates_by_quote.get(selected["spentMint"], [])
        if boundary - LANES["NON_SOL_PUMP_V2"]["windowNs"]
        <= int(row["received_at_unix_ns"]) < boundary
    ]
    choices: list[dict[str, Any]] = []
    exclusions: collections.Counter[str] = collections.Counter()
    for create in creates:
        mint = str(create["mint"])
        raw = [
            row for row in events_by_mint.get(mint, [])
            if str(row["quote_mint"]) == selected["spentMint"]
            and (int(row["slot"]), int(row["canonical_tx_index"]), int(row["instruction_index"]))
            < boundary_order
        ]
        if not raw or any(int(row["received_ns"]) <= 0 or int(row["received_ns"]) >= boundary for row in raw):
            exclusions["INCOMPLETE_CAUSAL_EVENT_HISTORY"] += 1
            continue
        external = [row for row in raw if str(row["actor"]) != WALLET]
        if not external:
            exclusions["NO_PRIOR_EXTERNAL_STATE"] += 1
            continue
        exposure = sum(
            int(row["token_amount_raw"]) * (1 if int(row["is_buy"]) else -1)
            for row in raw if str(row["actor"]) == WALLET
        )
        if exposure > 1:
            exclusions["TEACHER_ALREADY_EXPOSED"] += 1
            continue
        last = raw[-1]
        post = json.loads(str(last["post_state_json"]))
        if int(post.get("real_token", post.get("real_token_reserves", 0))) <= 0:
            exclusions["CURVE_NOT_EXECUTABLE"] += 1
            continue
        choices.append({
            "label": mint == selected["mint"], "mint": mint,
            "spentMint": selected["spentMint"], "createReceivedAtUnixNs": int(create["received_at_unix_ns"]),
            "stateSignature": str(last["signature"]), "stateSlot": int(last["slot"]),
            "stateTransactionIndex": int(last["canonical_tx_index"]),
            "stateReceivedAtUnixNs": int(last["received_ns"]), "state": post,
            "features": event_features(raw, boundary),
        })
    return finish_choice(selected, boundary, choices, exclusions)


def amm_event_rows(connection: sqlite3.Connection, venue: str) -> list[sqlite3.Row]:
    table_pool = "pool" if venue == "PUMPSWAP" else "pool_state"
    return connection.execute(
        f"SELECT signature,event_index,slot,canonical_tx_index,received_at_unix_ns received_ns,"
        f"{table_pool} pool,base_mint,quote_mint,side,"
        f"{'user' if venue == 'PUMPSWAP' else 'payer'} actor,pre_state_json,post_state_json "
        "FROM events ORDER BY slot,canonical_tx_index,event_index"
    ).fetchall()


def quote_indexes(
    connection: sqlite3.Connection,
) -> tuple[dict[str, list[sqlite3.Row]], dict[str, list[sqlite3.Row]]]:
    events: dict[str, list[sqlite3.Row]] = collections.defaultdict(list)
    for row in connection.execute(
        "SELECT *,CASE WHEN is_buy=1 THEN 'BUY' ELSE 'SELL' END side,user actor,"
        "received_at_unix_ns received_ns FROM events "
        "ORDER BY slot,canonical_tx_index,instruction_index"
    ):
        events[str(row["mint"])].append(row)
    creates: dict[str, list[sqlite3.Row]] = collections.defaultdict(list)
    for row in connection.execute(
        "SELECT * FROM create_events ORDER BY received_at_unix_ns,mint"
    ):
        creates[str(row["quote_mint"])].append(row)
    return dict(events), dict(creates)


def amm_choice(
    selected: dict[str, Any], all_rows: list[sqlite3.Row], venue: str,
    source_bounds: tuple[int, int], gaps: list[sqlite3.Row] | None = None,
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    selected_rows = [
        row for row in all_rows
        if str(row["signature"]) == selected["signature"] and str(row["actor"]) == WALLET
        and (
            (str(row["side"]).upper() == "BUY" and str(row["base_mint"]) == selected["mint"])
            or (str(row["side"]).upper() == "SELL" and str(row["quote_mint"]) == selected["mint"])
        )
    ]
    if len(selected_rows) != 1:
        reason = (
            "SOURCE_RECONCILIATION_PENDING" if int(selected["slot"]) > source_bounds[1]
            else "SELECTED_PRE_STATE_UNIVERSE_START" if int(selected["slot"]) < source_bounds[0]
            else "EXACT_SELECTED_EVENT_NOT_IN_STATE_PLANE"
        )
        return None, [reason], {"selectedMatches": len(selected_rows), "sourceSlotBounds": source_bounds}
    if int(selected_rows[0]["received_ns"]) <= 0:
        return None, ["SELECTED_REALTIME_ARRIVAL_MISSING"], {"selectedMatches": 1}
    boundary = int(selected_rows[0]["received_ns"])
    boundary_order = (int(selected["slot"]), int(selected["transaction_index"]), 1 << 30)
    window_start = boundary - int(LANES[venue]["windowNs"])
    relevant_gaps = [
        row for row in (gaps or [])
        if (int(row["received_at_unix_ns"]) >= window_start
            or (int(row["received_at_unix_ns"]) <= 0
                and int(row["slot"]) >= int(selected["slot"]) - 20))
        and (int(row["slot"]), int(row["canonical_tx_index"]), 1 << 30) < boundary_order
    ]
    if any(row["pool"] is None for row in relevant_gaps):
        return None, ["UNSCOPED_INEXACT_EVENT_GAP"], {
            "inexactGapN": len(relevant_gaps), "failClosed": True,
        }
    tainted_pools = {str(row["pool"]) for row in relevant_gaps}
    relevant = [
        row for row in all_rows
        if window_start <= int(row["received_ns"]) < boundary
        and (int(row["slot"]), int(row["canonical_tx_index"]), int(row["event_index"])) < boundary_order
        and selected["spentMint"] in {str(row["base_mint"]), str(row["quote_mint"])}
    ]
    by_pool: dict[str, list[sqlite3.Row]] = collections.defaultdict(list)
    for row in relevant:
        by_pool[str(row["pool"])].append(row)
    choices: list[dict[str, Any]] = []
    exclusions: collections.Counter[str] = collections.Counter()
    for pool, rows in sorted(by_pool.items()):
        if pool in tainted_pools:
            exclusions["POOL_INEXACT_EVENT_GAP"] += 1
            continue
        rows.sort(key=lambda row: (int(row["slot"]), int(row["canonical_tx_index"]), int(row["event_index"])))
        last = rows[-1]
        base, quote = str(last["base_mint"]), str(last["quote_mint"])
        acquired = quote if selected["spentMint"] == base else base
        if acquired == selected["spentMint"]:
            exclusions["INVALID_IDENTICAL_POOL_ASSETS"] += 1
            continue
        external = [row for row in rows if str(row["actor"]) != WALLET]
        if not external:
            exclusions["NO_PRIOR_EXTERNAL_STATE"] += 1
            continue
        # This active-window cohort is intentionally conservative: any teacher
        # acquisition of the same candidate inside the window removes it.
        already_acquired = any(
            str(row["actor"]) == WALLET and (
                (str(row["side"]).upper() == "BUY" and str(row["base_mint"]) == acquired)
                or (str(row["side"]).upper() == "SELL" and str(row["quote_mint"]) == acquired)
            ) for row in rows
        )
        if already_acquired:
            exclusions["TEACHER_ALREADY_EXPOSED"] += 1
            continue
        choices.append({
            "label": acquired == selected["mint"], "mint": acquired,
            "spentMint": selected["spentMint"], "pool": pool,
            "stateSignature": str(last["signature"]), "stateSlot": int(last["slot"]),
            "stateTransactionIndex": int(last["canonical_tx_index"]),
            "stateReceivedAtUnixNs": int(last["received_ns"]),
            "state": json.loads(str(last["post_state_json"])),
            "features": event_features(rows, boundary),
        })
    # The same acquired mint may have several pools. Keep the causally freshest.
    deduped: dict[str, dict[str, Any]] = {}
    for row in choices:
        prior = deduped.get(row["mint"])
        if prior is None or int(row["stateReceivedAtUnixNs"]) > int(prior["stateReceivedAtUnixNs"]):
            deduped[row["mint"]] = row
    return finish_choice(selected, boundary, list(deduped.values()), exclusions)


def finish_choice(
    selected: dict[str, Any], boundary: int, choices: list[dict[str, Any]],
    exclusions: collections.Counter[str],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
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
        return None, reasons, detail
    payload = {
        "schema": SCHEMA + "_DECISION", "lane": LANES[selected["lane"]]["name"],
        "signature": selected["signature"], "selectedMint": selected["mint"],
        "spentMint": selected["spentMint"], "boundaryUnixNs": boundary,
        "boundarySlot": selected["slot"], "boundaryTransactionIndex": selected["transaction_index"],
        "states": sorted(choices, key=lambda row: row["mint"]), "evidenceSummary": detail,
        "outcomesRead": 0, "validationRowsRead": 0, "submissions": 0,
    }
    return payload, [], detail


def minimum_unresolved_slot(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        "SELECT MIN(slot) FROM arrivals WHERE finalized=0"
    ).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def source_ready(minimum_unresolved: int | None, selected: dict[str, Any]) -> bool:
    return minimum_unresolved is None or minimum_unresolved > int(selected["slot"])


def metadata_value(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return None if row is None else str(row[0])


def slot_bounds(connection: sqlite3.Connection, table: str) -> tuple[int, int]:
    row = connection.execute(
        f"SELECT COALESCE(MIN(slot),0),COALESCE(MAX(slot),0) FROM {table}"
    ).fetchone()
    return int(row[0]), int(row[1])


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    contract = freeze_contract(args.output)
    selected_rows = source_rows(args.source_state, args.route_state)
    quote = connect_ro(args.quote_state)
    pump = connect_ro(args.pumpswap_state)
    launch = connect_ro(args.launchlab_state)
    output = open_output(args.output / "state.sqlite3")
    quote_events, quote_creates = quote_indexes(quote)
    pump_events = amm_event_rows(pump, "PUMPSWAP")
    pump_gaps = list(pump.execute(
        "SELECT * FROM exact_gaps ORDER BY slot,canonical_tx_index,signature"
    ))
    completed_pump_windows = {
        str(row[0]) for row in pump.execute(
            "SELECT selected_signature FROM choice_windows WHERE status='COMPLETE'"
        )
    }
    launch_events = amm_event_rows(launch, "RAYDIUM_LAUNCHLAB")
    pump_minimum_unresolved = minimum_unresolved_slot(pump)
    launch_minimum_unresolved = minimum_unresolved_slot(launch)
    quote_bounds = slot_bounds(quote, "events")
    pump_bounds = slot_bounds(pump, "arrivals")
    launch_bounds = slot_bounds(launch, "arrivals")
    quote_state_sha = snapshot_sha256({
        "metadata": [tuple(row) for row in quote.execute(
            "SELECT key,value FROM metadata ORDER BY key"
        )],
        "evidence": [tuple(row) for row in quote.execute(
            "SELECT path,sha256,transaction_n,exact_event_n,error_n "
            "FROM evidence_files ORDER BY path"
        )],
        "eventN": len(quote_events),
        "createN": sum(len(rows) for rows in quote_creates.values()),
        "slotBounds": quote_bounds,
    })
    pump_state_sha = snapshot_sha256({
        "eventN": len(pump_events),
        "slotBounds": pump_bounds,
        "minimumUnresolvedSlot": pump_minimum_unresolved,
        "completeWindows": sorted(completed_pump_windows),
        "gapN": len(pump_gaps),
    })
    launch_state_sha = snapshot_sha256({
        "eventN": len(launch_events),
        "slotBounds": launch_bounds,
        "minimumUnresolvedSlot": launch_minimum_unresolved,
    })
    route_state_generations = {
        "NON_SOL_PUMP_V2": quote_state_sha,
        "PUMPSWAP": pump_state_sha,
        "RAYDIUM_LAUNCHLAB": launch_state_sha,
    }
    contract_sha = sha256_file(contract)
    processed = 0
    for selected in selected_rows:
        generation = decision_generation(
            selected["sourceGeneration"], contract_sha,
            route_state_generations[selected["lane"]],
        )
        prior = output.execute(
            "SELECT source_generation,status FROM decisions WHERE signature=?", (selected["signature"],)
        ).fetchone()
        if prior and str(prior[0]) == generation and str(prior[1]) != "DEFERRED":
            continue
        lane = selected["lane"]
        try:
            if lane == "NON_SOL_PUMP_V2":
                choice, reasons, detail = non_sol_choice(
                    selected, quote_events, quote_creates, quote_bounds
                )
            elif lane == "PUMPSWAP":
                if selected["signature"] not in completed_pump_windows:
                    choice, reasons, detail = None, ["SOURCE_RECONCILIATION_PENDING"], {}
                elif not source_ready(pump_minimum_unresolved, selected):
                    choice, reasons, detail = None, ["SOURCE_RECONCILIATION_PENDING"], {}
                else:
                    choice, reasons, detail = amm_choice(
                        selected, pump_events, lane, pump_bounds, pump_gaps
                    )
            else:
                if not source_ready(launch_minimum_unresolved, selected):
                    choice, reasons, detail = None, ["SOURCE_RECONCILIATION_PENDING"], {}
                else:
                    choice, reasons, detail = amm_choice(
                        selected, launch_events, lane, launch_bounds
                    )
            status = "ELIGIBLE" if choice else (
                "DEFERRED" if any("PENDING" in reason for reason in reasons) else "EXCLUDED"
            )
            boundary = int((choice or {}).get("boundaryUnixNs") or 0)
        except Exception as error:
            choice, reasons, detail = None, ["MATERIALIZATION_ERROR"], {
                "errorType": type(error).__name__, "error": str(error)[:1000],
            }
            status, boundary = "DEFERRED", 0
        output.execute(
            "INSERT OR REPLACE INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                selected["signature"], LANES[lane]["name"], selected["mint"],
                selected["spentMint"], boundary, selected["slot"], selected["transaction_index"],
                status, canonical(reasons), canonical(choice) if choice else None,
                generation, now_utc(), 0, 0, 0,
            ),
        )
        processed += 1
    output.commit()
    rows = list(output.execute("SELECT * FROM decisions ORDER BY slot,tx_index,signature"))
    status_counts = collections.Counter(str(row["status"]) for row in rows)
    lane_counts: dict[str, dict[str, int]] = {}
    for lane in (spec["name"] for spec in LANES.values()):
        subset = [row for row in rows if row["lane"] == lane]
        lane_counts[lane] = dict(collections.Counter(str(row["status"]) for row in subset)) | {
            "TOTAL": len(subset)
        }
    reason_counts = collections.Counter(
        reason for row in rows for reason in json.loads(str(row["reasons_json"]))
    )
    max_controls = {
        lane: max((
            int(json.loads(str(row["choice_json"]))["evidenceSummary"]["controlN"])
            for row in rows if row["lane"] == lane and row["choice_json"]
        ), default=0) for lane in lane_counts
    }
    output.close(); quote.close(); pump.close(); launch.close()
    report = {
        "schema": SCHEMA + "_STATUS", "implementationVersion": IMPLEMENTATION_VERSION,
        "generatedAtUtc": now_utc(), "selectedTransitionsN": len(selected_rows),
        "processedThisRun": processed, "statusCounts": dict(sorted(status_counts.items())),
        "laneCounts": lane_counts, "reasonCounts": dict(sorted(reason_counts.items())),
        "maxControlsByLane": max_controls,
        "pumpSwapUniverse": "TEACHER_BOUNDARY_CONDITIONED_CANONICAL_FINALIZED_WINDOWS",
        "solPumpQualificationChanged": False,
        "integrity": {"outcomesRead": 0, "validationRowsRead": 0, "submissions": 0},
    }
    atomic_json(args.output / "status.json", report)
    atomic_json(args.output / "provenance.json", {
        "schema": SCHEMA + "_PROVENANCE", "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "contractSha256": sha256_file(contract), "sourceStateSha256": sha256_file(args.source_state),
        "routeStateSha256": sha256_file(args.route_state), "outcomesRead": 0,
        "validationRowsRead": 0, "submissions": 0,
    })
    atomic_json(args.output / "teacher-isolation-policy.json", {
        "schema": SCHEMA + "_TEACHER_ISOLATION_POLICY",
        "teacherWallet": WALLET,
        "crossTeacherBlendingPermitted": False,
        "futureTeacherWalletsRequireSeparateSourceAndOutputCohorts": True,
        "solPumpQualificationChanged": False,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-state", type=pathlib.Path, required=True)
    parser.add_argument("--route-state", type=pathlib.Path, required=True)
    parser.add_argument("--quote-state", type=pathlib.Path, required=True)
    parser.add_argument("--pumpswap-state", type=pathlib.Path, required=True)
    parser.add_argument("--launchlab-state", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
