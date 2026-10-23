#!/usr/bin/env python3
"""Build an outcome-blind route census from KEFI wallet transactions only.

This intentionally ignores legacy candidate_arm_ids.  Those labels came from
the mixed 13-arm registry and include 2CQ-derived populations.  Route identity
is reconstructed from the finalized transaction's invoked programs, exact
underlying-venue resolution, and exact wallet-side transitions where an
adapter can prove them.

The result is a census and evidence inventory, not a policy selector.  It does
not compute PnL, freeze a policy, start a cohort, or touch any collector.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pumpSwapExactEconomicsV1 as pumpswap
import exactCurveEconomicsV1 as pumpcurve
import raydiumClmmExactEconomicsV1 as raydium_clmm
import twoCqRouteEvidenceAdaptersV1 as route_adapters


SCHEMA = "KEFI_ONLY_ROUTE_CENSUS_V1"
WALLET = "kEFiAX3jo5NmemysQov342TZ9mGh6yp92GDRjhA8XDf"
PUMPSWAP_FAMILY = "PUMPSWAP_PROGRAM"
PUMP_FAMILY = "PUMP_PROGRAM"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def route_label(tx: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Return route, certainty, and resolver evidence without legacy labels."""
    try:
        resolved = route_adapters.resolve_route(tx)
    except Exception as error:
        return "UNRESOLVED_ROUTE", "UNRESOLVED", {"error": f"{type(error).__name__}:{error}"}
    venues = resolved.get("underlyingVenues") or []
    routers = list(resolved.get("routers") or [])
    if len(venues) != 1:
        return "UNRESOLVED_ROUTE", "UNRESOLVED", {
            "underlying_venues": venues,
            "routers": routers,
            "unknown_programs": resolved.get("unknownPrograms") or [],
        }
    venue = venues[0]
    name = str(venue.get("venue") or "UNKNOWN_VENUE")
    invocation = str(venue.get("invocation") or "UNKNOWN_INVOCATION")
    router_prefix = "+".join(routers)
    if name == "PUMPSWAP":
        if routers:
            label = f"ROUTER_{router_prefix}_PUMPSWAP_{invocation}"
        else:
            label = f"PUMPSWAP_{'DIRECT' if invocation == 'TOP_LEVEL' else 'CPI'}"
    elif name == "PUMP_BONDING_CURVE":
        label = "PUMP_BONDING_CURVE"
    elif routers:
        label = f"ROUTER_{router_prefix}_{name}_{invocation}"
    else:
        label = f"{name}_{invocation}"
    return label, "EXACT_ROUTE", {
        "underlying_venues": venues,
        "routers": routers,
        "invocation": invocation,
    }


def pump_swap_events(raw: dict[str, Any], route: str) -> tuple[list[dict[str, Any]], list[str]]:
    tx = raw.get("transaction") or {}
    try:
        transitions = pumpswap.validate_transaction(tx)
    except Exception as error:
        return [], [f"PUMPSWAP_DECODER:{type(error).__name__}:{error}"]
    events: list[dict[str, Any]] = []
    ignored: list[str] = []
    for index, transition in enumerate(transitions):
        if transition.get("user") != raw.get("tracked_wallet"):
            continue
        if not transition.get("exactObservedTransition"):
            ignored.append("TRANSITION_NOT_EXACT")
            continue
        events.append({
            "signature": raw.get("signature"),
            "slot": int(raw.get("slot") or 0),
            "block_time": int(raw.get("block_time") or 0),
            "event_index": index,
            "route": route,
            "route_certainty": "EXACT_ROUTE_AND_TRANSITION",
            "side": transition.get("side"),
            "mint": transition.get("baseMint"),
            "pool": transition.get("pool"),
            "quote_mint": transition.get("quoteMint"),
            "amount_raw": int(transition.get("amountRaw") or 0),
            "user_quote_amount_raw": int(transition.get("userQuoteAmountRaw") or 0),
            "curve_quote_amount_raw": int(transition.get("curveQuoteAmountRaw") or 0),
            "pool_quote_delta_raw": int(transition.get("poolQuoteDeltaRaw") or 0),
            "pre_state": transition.get("preState"),
            "post_state": transition.get("postState"),
            "fees": {
                "lp_bps": int(transition.get("lpFeeBps") or 0),
                "lp_raw": int(transition.get("lpFeeRaw") or 0),
                "protocol_bps": int(transition.get("protocolFeeBps") or 0),
                "protocol_raw": int(transition.get("protocolFeeRaw") or 0),
                "creator_bps": int(transition.get("coinCreatorBps") or 0),
                "creator_raw": int(transition.get("creatorFeeRaw") or 0),
            },
            "source_frame_sha256": raw.get("transaction_sha256"),
        })
    return events, ignored


def non_pumpswap_exact_events(raw: dict[str, Any], route: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Attempt exact CLMM wallet settlement; retain failures as evidence gaps."""
    tx = raw.get("transaction") or {}
    if "RAYDIUM_CLMM" not in route:
        return [], ["NO_SELECTED_EXACT_ADAPTER"]
    try:
        transitions = raydium_clmm.validate_transaction(tx)
    except Exception as error:
        return [], [f"RAYDIUM_CLMM_DECODER:{type(error).__name__}:{error}"]
    wallet = str(raw.get("tracked_wallet") or WALLET)
    deltas = raydium_clmm.wallet_mint_deltas(tx, wallet)
    events: list[dict[str, Any]] = []
    for index, transition in enumerate(transitions):
        if transition.get("payer") != wallet and transition.get("outputOwner") != wallet:
            continue
        input_mint = str(transition.get("inputMint") or "")
        output_mint = str(transition.get("outputMint") or "")
        input_delta = int(deltas.get(input_mint, 0))
        output_delta = int(deltas.get(output_mint, 0))
        if output_delta > 0 and input_delta < 0:
            side = "BUY"
            mint = output_mint
        elif output_delta < 0 and input_delta > 0:
            side = "SELL"
            mint = output_mint
        else:
            continue
        events.append({
            "signature": raw.get("signature"),
            "slot": int(raw.get("slot") or 0),
            "block_time": int(raw.get("block_time") or 0),
            "event_index": index,
            "route": route,
            "route_certainty": "EXACT_ROUTE_AND_SETTLEMENT",
            "side": side,
            "mint": mint,
            "pool": transition.get("pool"),
            "quote_mint": input_mint if side == "BUY" else output_mint,
            "amount_raw": abs(output_delta if side == "BUY" else input_delta),
            "source_frame_sha256": raw.get("transaction_sha256"),
        })
    if not events:
        return [], ["EXACT_VENUE_TRANSITION_WITHOUT_PROVEN_WALLET_SETTLEMENT"]
    return events, []


def classify_row(raw: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]], list[str], dict[str, Any]]:
    tx = raw.get("transaction") or {}
    route, certainty, evidence = route_label(tx)
    if raw.get("finalized") is not True:
        return route, "NOT_FINALIZED", [], ["NOT_FINALIZED"], evidence
    if (tx.get("meta") or {}).get("err") is not None:
        return route, "FAILED_TRANSACTION", [], ["FAILED_TRANSACTION"], evidence
    if raw.get("program_family") == PUMPSWAP_FAMILY or "PUMPSWAP" in route:
        events, failures = pump_swap_events(raw, route)
        return route, certainty, events, failures, evidence
    if raw.get("program_family") == PUMP_FAMILY:
        activity = pumpcurve.decode_trade_events((tx.get("meta") or {}).get("logMessages") or [])
        if not activity:
            return route, certainty, [], ["NO_EXACT_PUMP_TRADE_EVENT"], {**evidence, "pump_trade_event_count": 0}
        return route, certainty, [], ["PUMP_TRADE_EVENT_NEEDS_WALLET_JOIN"], {**evidence, "pump_trade_event_count": len(activity)}
    events, failures = non_pumpswap_exact_events(raw, route)
    return route, certainty, events, failures, evidence


def control_counts(control_tape: list[dict[str, Any]] | None) -> dict[str, Any]:
    if not control_tape:
        return {"venue_level_pumpswap_buy_controls": 0, "route_specific_controls_proven": False}
    eligible = [
        row for row in control_tape
        if not row.get("is_tracked_teacher")
        and row.get("side") == "BUY"
        and row.get("exact_transition_validated") is True
    ]
    return {
        "venue_level_pumpswap_buy_controls": len(eligible),
        "route_specific_controls_proven": False,
        "control_route_limitation": "The pool tape proves PumpSwap venue mechanics but does not prove CPI/direct route for non-KEFI control rows.",
    }


def build_census(raw_rows: list[dict[str, Any]], control_tape: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    route_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "raw_transactions": 0,
        "unique_signatures": set(),
        "genuine_exact_events": 0,
        "genuine_buys": 0,
        "genuine_sells": 0,
        "exact_route_rows": 0,
        "pre_entry_state_events": 0,
        "liquidity_fields_events": 0,
        "fee_fields_events": 0,
        "unresolved_or_incomplete_rows": 0,
        "failure_reasons": Counter(),
    })
    events: list[dict[str, Any]] = []
    raw_route_hints = Counter()
    for raw in raw_rows:
        route, certainty, row_events, failures, evidence = classify_row(raw)
        bucket = route_rows[route]
        bucket["raw_transactions"] += 1
        bucket["unique_signatures"].add(str(raw.get("signature")))
        if certainty == "EXACT_ROUTE":
            bucket["exact_route_rows"] += 1
        if failures:
            bucket["unresolved_or_incomplete_rows"] += 1
            bucket["failure_reasons"].update(failures)
        for event in row_events:
            event["tracked_wallet"] = raw.get("tracked_wallet")
            event["observed_program_family"] = raw.get("program_family")
            event["observed_program_ids"] = raw.get("observed_program_ids") or []
            event["candidate_arm_ids_legacy_ignored"] = raw.get("candidate_arm_ids") or []
            events.append(event)
            bucket["genuine_exact_events"] += 1
            bucket["genuine_buys"] += int(event.get("side") == "BUY")
            bucket["genuine_sells"] += int(event.get("side") == "SELL")
            if event.get("pre_state"):
                bucket["pre_entry_state_events"] += 1
            if event.get("pool") and (event.get("pre_state") or event.get("post_state")):
                bucket["liquidity_fields_events"] += 1
            if event.get("fees"):
                bucket["fee_fields_events"] += 1
        for legacy_hint in raw.get("candidate_arm_ids") or []:
            raw_route_hints[str(legacy_hint)] += 1

    summaries: list[dict[str, Any]] = []
    for route_name, bucket in route_rows.items():
        exact_events = int(bucket["genuine_exact_events"])
        summary = {
            "route": route_name,
            "raw_transactions": int(bucket["raw_transactions"]),
            "unique_signatures": len(bucket["unique_signatures"]),
            "genuine_exact_events": exact_events,
            "genuine_buys": int(bucket["genuine_buys"]),
            "genuine_sells": int(bucket["genuine_sells"]),
            "route_certainty": "EXACT" if bucket["exact_route_rows"] == bucket["raw_transactions"] else "PARTIAL_OR_UNRESOLVED",
            "available_pre_entry_state": bool(bucket["pre_entry_state_events"]),
            "liquidity_and_fee_fields": exact_events > 0 and bucket["liquidity_fields_events"] == exact_events and bucket["fee_fields_events"] == exact_events,
            "unresolved_or_incomplete_rows": int(bucket["unresolved_or_incomplete_rows"]),
            "failure_reasons": dict(bucket["failure_reasons"]),
            "controls": control_counts(control_tape) if "PUMPSWAP" in route_name else {"venue_level_controls": 0, "route_specific_controls_proven": False},
        }
        summary["eligible_for_strategy_search"] = bool(
            summary["genuine_buys"] > 0
            and summary["route_certainty"] == "EXACT"
            and summary["available_pre_entry_state"]
            and summary["liquidity_and_fee_fields"]
        )
        summaries.append(summary)
    summaries.sort(key=lambda row: (
        -row["genuine_buys"],
        -row["genuine_exact_events"],
        row["route"],
    ))
    for index, row in enumerate(summaries, 1):
        row["route_rank_by_evidence"] = index
    census = {
        "schema": SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "outcome_blind": True,
        "strategy_selection_performed": False,
        "tracked_wallet": WALLET,
        "raw_transactions": len(raw_rows),
        "unique_raw_signatures": len({str(row.get("signature")) for row in raw_rows}),
        "legacy_candidate_labels_ignored": True,
        "legacy_candidate_label_counts": dict(raw_route_hints),
        "control_input_rows": len(control_tape or []),
        "routes": summaries,
        "interpretation": {
            "historical_registry": "legacy mixed-source infrastructure; not used as KEFI route truth",
            "genuine_event_definition": "finalized successful transaction plus exact wallet-side venue transition",
            "unresolved_definition": "raw venue evidence exists but exact wallet settlement/route transition is not proven by the available adapter",
            "promotion": "no route is promoted or frozen by this census",
        },
    }
    return census, events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--control-tape", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--events-out", type=Path, required=True)
    args = parser.parse_args()
    raw_rows = load_jsonl(args.raw)
    controls = load_jsonl(args.control_tape) if args.control_tape else None
    census, events = build_census(raw_rows, controls)
    census["inputs"] = {
        "raw_path": str(args.raw),
        "raw_sha256": sha256_file(args.raw),
        "control_tape_path": str(args.control_tape) if args.control_tape else None,
        "control_tape_sha256": sha256_file(args.control_tape) if args.control_tape else None,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.events_out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(census, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.events_out.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in events), encoding="utf-8")
    print(json.dumps({"status": "KEFI_ONLY_ROUTE_CENSUS_COMPLETE", "raw": len(raw_rows), "events": len(events), "routes": len(census["routes"])}, sort_keys=True))


if __name__ == "__main__":
    main()
