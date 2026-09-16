#!/usr/bin/env python3
"""Materialize outcome-blind exact evidence for non-SOL 2CQ route siblings."""

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

import twoCqRouteEvidenceAdaptersV1 as adapters


SCHEMA = "2CQ_EXTENDED_ROUTE_MATERIALIZER_V1"
IMPLEMENTATION_VERSION = "1.4.0"
TARGET_FAMILIES = {"NON_SOL_PUMP_V2", "PUMPSWAP", "PUMPSWAP_CPI", "ROUTER", "OTHER_DEX"}


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


def open_state(path: pathlib.Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    # Keep the final ordered report read inside the hardened service sandbox.
    # Once route evidence grew large enough, SQLite's temporary sort spill
    # failed under ProtectSystem=strict even though the durable commit passed.
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS route_evidence(
            signature TEXT PRIMARY KEY,
            mint TEXT NOT NULL,
            source_family TEXT NOT NULL,
            source_route TEXT NOT NULL,
            source_generation TEXT NOT NULL,
            classification TEXT NOT NULL,
            resolved_venue TEXT,
            route_json TEXT NOT NULL,
            exact_json TEXT,
            error TEXT,
            updated_utc TEXT NOT NULL,
            outcomes_read INTEGER NOT NULL DEFAULT 0,
            validation_rows_read INTEGER NOT NULL DEFAULT 0,
            submissions INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS route_evidence_family ON route_evidence(source_family);
    """)
    return connection


def source_generation(row: sqlite3.Row) -> str:
    dependency_hashes = {
        name: sha256_file(pathlib.Path(module.__file__))
        for name, module in {
            "routeAdapters": adapters,
            "launchLab": adapters.launchlab,
            "pumpSwap": adapters.pumpswap,
            "raydiumCpmm": adapters.cpmm,
            "raydiumClmm": adapters.clmm,
            "meteoraDbc": adapters.dbc,
        }.items()
    }
    return hashlib.sha256(canonical({
        "signature": row["signature"],
        "family": row["economic_family"],
        "route": row["route"],
        "detail": row["detail_json"],
        "implementationVersion": IMPLEMENTATION_VERSION,
        "adapterDependencies": dependency_hashes,
    }).encode()).hexdigest()


def run(args: argparse.Namespace) -> dict[str, Any]:
    source = sqlite3.connect(f"file:{args.source_state}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    decisions = list(source.execute(
        "SELECT signature,mint,route,economic_family,detail_json FROM current_decisions "
        "WHERE economic_family IN ('NON_SOL_PUMP_V2','PUMPSWAP','PUMPSWAP_CPI','ROUTER','OTHER_DEX') "
        "ORDER BY block_time_ms,slot,transaction_index,signature"
    ))
    source.close()

    args.output.mkdir(parents=True, exist_ok=True)
    state = open_state(args.output / "state.sqlite3")
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    processed = 0
    for row in decisions:
        signature = str(row["signature"])
        generation = source_generation(row)
        prior = state.execute(
            "SELECT source_generation FROM route_evidence WHERE signature=?", (signature,)
        ).fetchone()
        if prior and str(prior[0]) == generation:
            continue
        tx_path = args.wallet_rpc_cache / f"tx-{signature}.json"
        if not tx_path.exists():
            classification = "DEFERRED_TRANSACTION_NOT_CACHED"
            route = {"resolved": False, "underlyingVenues": [], "unknownPrograms": []}
            exact = None
            error = "wallet transaction cache missing"
        else:
            route = {"resolved": False, "underlyingVenues": [], "unknownPrograms": []}
            try:
                tx = json.loads(tx_path.read_text(encoding="utf-8"))
                route = adapters.resolve_route(tx)
                family = str(row["economic_family"])
                exact_family = family
                venue = (route.get("resolution") or {}).get("venue")
                if family == "ROUTER":
                    if venue == "PUMPSWAP":
                        exact_family = "PUMPSWAP_CPI"
                    elif venue in {
                        "RAYDIUM_LAUNCHLAB", "RAYDIUM_CPMM", "RAYDIUM_CLMM", "METEORA_DBC",
                    }:
                        exact_family = "OTHER_DEX"
                try:
                    exact = adapters.selected_exact_evidence(
                        exact_family, tx, signature, str(row["mint"]), venue
                    )
                    classification = "EXACT_SELECTED_TRANSITION"
                    error = None
                except Exception as protocol_error:
                    exact = adapters.exact_wallet_settlement(
                        tx, str(row["mint"])
                    ) | {"routeAttribution": route}
                    classification = "EXACT_WALLET_RECEIPT_ADAPTER_PENDING"
                    error = f"{type(protocol_error).__name__}:{protocol_error}"[:1000]
            except Exception as exc:
                exact = None
                error = f"{type(exc).__name__}:{exc}"[:1000]
                classification = (
                    "ROUTE_RESOLVED_ADAPTER_REQUIRED"
                    if route.get("resolved") else "ROUTE_AMBIGUOUS_FAIL_CLOSED"
                )
        venue = (route.get("resolution") or {}).get("venue")
        state.execute(
            "INSERT OR REPLACE INTO route_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                signature, str(row["mint"]), str(row["economic_family"]), str(row["route"]),
                generation, classification, venue, canonical(route),
                canonical(exact) if exact is not None else None, error, now, 0, 0, 0,
            ),
        )
        processed += 1
    state.commit()
    state.row_factory = sqlite3.Row
    rows = [dict(row) for row in state.execute(
        "SELECT * FROM route_evidence ORDER BY updated_utc,signature"
    )]
    counts = collections.Counter(str(row["classification"]) for row in rows)
    family_counts = collections.Counter(str(row["source_family"]) for row in rows)
    exact_by_family = collections.Counter(
        str(row["source_family"]) for row in rows
        if row["classification"] == "EXACT_SELECTED_TRANSITION"
    )
    receipt_by_family = collections.Counter(
        str(row["source_family"]) for row in rows
        if row["classification"] == "EXACT_WALLET_RECEIPT_ADAPTER_PENDING"
    )
    venue_counts = collections.Counter(
        str(row["resolved_venue"]) for row in rows if row["resolved_venue"]
    )
    adapter_backlog = collections.Counter(
        str(row["resolved_venue"] or "UNRESOLVED") for row in rows
        if row["classification"] != "EXACT_SELECTED_TRANSITION"
    )
    exact_n = int(counts.get("EXACT_SELECTED_TRANSITION", 0))
    state.close()

    output_rows = args.output / "route-evidence.jsonl"
    temporary = output_rows.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            emitted = dict(row)
            emitted["route"] = json.loads(emitted.pop("route_json"))
            exact_json = emitted.pop("exact_json")
            emitted["exactEvidence"] = json.loads(exact_json) if exact_json else None
            stream.write(canonical(emitted) + "\n")
    os.replace(temporary, output_rows)
    report = {
        "schema": SCHEMA + "_STATUS",
        "implementationVersion": IMPLEMENTATION_VERSION,
        "generatedAtUtc": now,
        "sourceDecisionN": len(decisions),
        "materializedN": len(rows),
        "exactSelectedTransitionN": exact_n,
        "exactSelectedTransitionRate": exact_n / len(rows) if rows else 0.0,
        "processedThisRun": processed,
        "classificationCounts": dict(sorted(counts.items())),
        "familyCounts": dict(sorted(family_counts.items())),
        "exactSelectedTransitionsByFamily": dict(sorted(exact_by_family.items())),
        "exactWalletReceiptsPendingAdapterByFamily": dict(sorted(receipt_by_family.items())),
        "resolvedVenues": dict(sorted(venue_counts.items())),
        "adapterBacklogByVenue": dict(sorted(adapter_backlog.items())),
        "controlUniverseStatus": {
            "NON_SOL_PUMP_V2": "QUOTE_MINT_SCOPED_STATE_PLANE_COLLECTING",
            "PUMPSWAP": "CANONICAL_POOL_STATE_LEDGER_COLLECTING",
            "PUMPSWAP_CPI": "CANONICAL_POOL_STATE_LEDGER_COLLECTING",
            "RAYDIUM_LAUNCHLAB": "EXACT_LEDGER_ACTIVE",
            "RAYDIUM_CLMM": "BOUNDED_CANONICAL_CHOICE_WINDOWS_ACTIVE",
            "METEORA_DBC": "BOUNDED_CANONICAL_CHOICE_WINDOWS_ACTIVE",
            "ROUTER": "DELEGATES_TO_RESOLVED_UNDERLYING_VENUE",
            "OTHER_DEX": "FAIL_CLOSED_UNTIL_PROTOCOL_ADAPTER_EXISTS",
        },
        "solPumpQualificationChanged": False,
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "submissions": 0,
    }
    atomic_json(args.output / "status.json", report)
    atomic_json(args.output / "provenance.json", {
        "schema": SCHEMA + "_PROVENANCE",
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "adapterSha256": sha256_file(pathlib.Path(adapters.__file__)),
        "adapterDependencySha256": {
            name: sha256_file(pathlib.Path(module.__file__))
            for name, module in {
                "launchLab": adapters.launchlab,
                "pumpSwap": adapters.pumpswap,
                "raydiumCpmm": adapters.cpmm,
                "raydiumClmm": adapters.clmm,
                "meteoraDbc": adapters.dbc,
            }.items()
        },
        "sourceStateSha256": sha256_file(args.source_state),
        "rowsSha256": sha256_file(output_rows),
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "submissions": 0,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-state", type=pathlib.Path, required=True)
    parser.add_argument("--wallet-rpc-cache", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
