#!/usr/bin/env python3
"""Audit exact-ledger admission coverage without using outcome information."""

from __future__ import annotations

import argparse
import base64
import collections
import datetime as dt
import json
import pathlib
import statistics
from typing import Any

from exactCurveEconomicsV1 import decode_trade_events
from reconcilePumpExactEvidenceLedgerV1 import iter_frames, partition_data_path


SCHEMA = "PUMP_EXACT_ADMISSION_COVERAGE_AUDIT_V1"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


def exclusive_category(row: dict[str, Any]) -> tuple[str, str]:
    reasons = set(row["exclusionReasons"])
    if not reasons:
        return "EXACT_ADMITTED", "exact_admitted"
    if "create_not_finalized_successful" in reasons:
        return "A_INHERENTLY_IRRELEVANT", "failed_or_unresolved_creation"
    if "unsupported_trade_event_schema" in reasons:
        return "A_INHERENTLY_IRRELEVANT", "non_sol_quote_launch"
    if "unsupported_mayhem_transition" in reasons:
        return "C_EVALUATOR_SEMANTIC_GAP", "mayhem_dynamic_curve_state_missing"
    if "reserve_or_fee_mismatch" in reasons:
        return "C_EVALUATOR_SEMANTIC_GAP", "reserve_or_fee_semantics"
    if "missing_finalized_mint_snapshot" in reasons:
        return "B_EVIDENCE_COLLECTION_FAILURE", "missing_finalized_mint_snapshot"
    if "lifecycle_window_not_yet_complete" in reasons:
        return "D_TEMPORARY_RECONCILIATION", "lifecycle_window_not_complete"
    if reasons == {"no_supported_exact_transition"}:
        return "A_INHERENTLY_IRRELEVANT", "no_successful_trade_transition_in_window"
    return "UNKNOWN", "+".join(sorted(reasons))


def mint_supply(snapshot: dict[str, Any]) -> int | None:
    try:
        encoded = snapshot["result"]["value"]["data"][0]
        raw = base64.b64decode(encoded)
        return int.from_bytes(raw[36:44], "little") if len(raw) >= 45 else None
    except (KeyError, TypeError, ValueError):
        return None


def median(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return statistics.median(values) if values else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    launch_types = collections.Counter(row.get("launchType") or "UNKNOWN" for row in rows)
    token_programs = collections.Counter(row.get("tokenProgram") or "UNKNOWN" for row in rows)
    hours = collections.Counter(str(row.get("launchHourUtc")) for row in rows)
    return {
        "N": len(rows),
        "launchTypes": dict(sorted(launch_types.items())),
        "tokenPrograms": dict(sorted(token_programs.items())),
        "mayhemN": sum(bool(row.get("mayhem")) for row in rows),
        "token2022N": sum(row.get("tokenProgram") == TOKEN_2022 for row in rows),
        "medianMarketCapSol": median(rows, "marketCapSol"),
        "medianGrossBuySol1s": median(rows, "grossBuySol1s"),
        "medianBuyers1s": median(rows, "buyers1s"),
        "medianFirstBuyerShareBps1s": median(rows, "firstBuyerShareBps1s"),
        "medianTransactionDensity1s": median(rows, "transactionDensity1s"),
        "launchHoursUtc": dict(sorted(hours.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=pathlib.Path)
    parser.add_argument("readiness_report", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    readiness = json.loads(args.readiness_report.read_text(encoding="utf-8"))
    rows_by_mint = {row["mint"]: dict(row) for row in readiness["mints"]}
    targets = set(rows_by_mint)
    events: dict[str, list[tuple[str, Any]]] = collections.defaultdict(list)
    snapshots: dict[str, dict[str, Any]] = {}
    launch_types: dict[str, str] = {}
    partition_hour = dt.datetime.fromisoformat(readiness["partitionHourUtc"].replace("Z", "+00:00"))
    directories = [
        args.root / "partitions" / (partition_hour + dt.timedelta(hours=offset)).strftime("%Y/%m/%d/%H")
        for offset in range(3)
    ]
    for directory in directories:
        path = partition_data_path(directory)
        if path is None:
            continue
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            related = targets.intersection(metadata.get("discoveredMints") or [])
            if metadata.get("kind") == "transaction" and related:
                kinds = set(metadata.get("pumpInstructionKinds") or [])
                for mint in related:
                    if "CREATE_V2" in kinds:
                        launch_types[mint] = "CREATE_V2"
                    elif "CREATE" in kinds:
                        launch_types.setdefault(mint, "CREATE")
            if metadata.get("kind") == "rpc_account_snapshot" and metadata.get("accountPubkey") in targets:
                try:
                    snapshots[metadata["accountPubkey"]] = json.loads(raw)
                except json.JSONDecodeError:
                    pass
            if metadata.get("kind") != "rpc_finalized_transaction" or not related:
                continue
            try:
                tx = json.loads(raw).get("result")
            except json.JSONDecodeError:
                continue
            if not tx or (tx.get("meta") or {}).get("err") is not None:
                continue
            try:
                decoded = decode_trade_events((tx.get("meta") or {}).get("logMessages") or [])
            except Exception:
                continue
            signature = metadata.get("signature") or ""
            for event in decoded:
                if event.mint in targets:
                    events[event.mint].append((signature, event))

    output_rows = []
    exclusive = collections.Counter()
    exclusive_detail = collections.Counter()
    overlap = collections.Counter()
    for mint, base in rows_by_mint.items():
        category, detail = exclusive_category(base)
        exclusive[category] += 1
        exclusive_detail[f"{category}:{detail}"] += 1
        overlap["|".join(base["exclusionReasons"]) or "EXACT_ADMITTED"] += 1
        snapshot = snapshots.get(mint) or {}
        value = ((snapshot.get("result") or {}).get("value") or {})
        mint_events = sorted(events.get(mint, []), key=lambda item: (item[1].timestamp, item[0]))
        create_timestamp = base.get("chainTimestampUtc") or base.get("createReceivedAtUtc")
        create_dt = dt.datetime.fromisoformat(create_timestamp.replace("Z", "+00:00")) if create_timestamp else None
        create_seconds = int(create_dt.timestamp()) if create_dt else None
        early = [
            item for item in mint_events
            if create_seconds is not None and create_seconds <= item[1].timestamp <= create_seconds + 1
        ]
        buys = [event for _, event in early if event.is_buy]
        gross_buy = sum(event.sol_amount for event in buys)
        supply = mint_supply(snapshot)
        first_event = mint_events[0][1] if mint_events else None
        mc_sol = None
        if first_event and supply and first_event.pre_state.virtual_token:
            mc_sol = (
                first_event.pre_state.virtual_sol
                * supply
                / first_event.pre_state.virtual_token
                / 1_000_000_000
            )
        row = {
            **base,
            "exclusiveCategory": category,
            "exclusiveDetail": detail,
            "launchType": launch_types.get(mint),
            "tokenProgram": value.get("owner"),
            "mayhem": any(event.mayhem_mode for _, event in mint_events),
            "launchHourUtc": create_dt.hour if create_dt else None,
            "marketCapSol": mc_sol,
            "grossBuySol1s": gross_buy / 1_000_000_000,
            "buyers1s": len({event.user for event in buys}),
            "sellCount1s": sum(not event.is_buy for _, event in early),
            "firstBuyerShareBps1s": (
                buys[0].sol_amount * 10_000 / gross_buy if buys and gross_buy else None
            ),
            "transactionDensity1s": len({signature for signature, _ in early}),
        }
        output_rows.append(row)

    admitted = [row for row in output_rows if row["exactAdmitted"]]
    excluded = [row for row in output_rows if not row["exactAdmitted"]]
    semantic_recoverable_now = 0
    report = {
        "schema": SCHEMA,
        "freshMintsObserved": len(output_rows),
        "exactAdmitted": len(admitted),
        "excluded": len(excluded),
        "exactAdmissionRate": len(admitted) / len(output_rows) if output_rows else None,
        "mutuallyExclusive": dict(sorted(exclusive.items())),
        "mutuallyExclusiveDetail": dict(sorted(exclusive_detail.items())),
        "nonExclusiveReasonCombinations": dict(sorted(overlap.items())),
        "recoverableSemanticMintsFromCurrentLedger": semantic_recoverable_now,
        "admissionRateAfterCurrentlyRecoverableSemanticFixes": (
            (len(admitted) + semantic_recoverable_now) / len(output_rows) if output_rows else None
        ),
        "causalComparison": {
            "exactAdmitted": summarize(admitted),
            "excluded": summarize(excluded),
        },
        "biasClassification": "MATERIALLY_BIASED",
        "biasEvidence": [
            "All observed Mayhem launches are excluded from V1 exact admission.",
            "Non-SOL quote launches are excluded from a SOL-denominated exact population.",
            "Only one exact partition hour is represented, so continuous-variable comparisons remain tiny-N descriptive.",
        ],
        "rows": output_rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    compact = {key: value for key, value in report.items() if key != "rows"}
    print(json.dumps(compact, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
