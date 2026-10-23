#!/usr/bin/env python3
"""Materialize exact route events from a generic wallet raw source."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import twoCqRouteEvidenceAdaptersV1 as route_adapters
import pumpSwapExactEconomicsV1 as pumpswap
import exactCurveEconomicsV1 as pumpcurve
import raydiumClmmExactEconomicsV1 as raydium_clmm

def route_provenance(tx: dict) -> dict | None:
    resolved = route_adapters.resolve_route(tx)
    venues = resolved.get("underlyingVenues") or []
    if len(venues) != 1 or venues[0].get("venue") != "PUMPSWAP":
        return None
    venue = venues[0]
    routers = list(resolved.get("routers") or [])
    invocation = str(venue.get("invocation") or "")
    return {
        "route": "PUMPSWAP_DIRECT" if invocation == "TOP_LEVEL" and not routers else "PUMPSWAP_CPI",
        "route_certainty": "EXACT_ROUTE",
        "underlying_venue": "PUMPSWAP",
        "venue_program_id": venue.get("programId"),
        "invocation": invocation,
        "routers": routers,
    }

def resolved_route_label(tx: dict) -> str:
    resolved = route_adapters.resolve_route(tx)
    venues = resolved.get("underlyingVenues") or []
    routers = list(resolved.get("routers") or [])
    if venues:
        venue_names = "+".join(sorted(f"{row.get('venue')}_{row.get('invocation')}" for row in venues))
        if len(venues) == 1 and not routers:
            return venue_names
        router_names = "+".join(sorted(routers)) or "COMPOSITE"
        return f"ROUTER_{router_names}_{venue_names}"
    if resolved.get("unknownPrograms"):
        return "UNRESOLVED_PROGRAMS"
    return "UNRESOLVED_ROUTE"


def pump_curve_route(tx: dict) -> str:
    """Route an exact Pump trade by Pump invocation, not incidental accounts.

    A Pump trade can contain Raydium/Meteora accounts during migration or
    related CPI activity.  Those accounts do not turn the decoded Pump trade
    into a Raydium/Meteora trade.
    """
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = [
        str(row.get("pubkey")) if isinstance(row, dict) else str(row)
        for row in message.get("accountKeys") or []
    ]
    for instruction in message.get("instructions") or []:
        program = instruction.get("programId")
        index = instruction.get("programIdIndex")
        if program is None and index is not None and int(index) < len(keys):
            program = keys[int(index)]
        if program == route_adapters.PUMP:
            return "PUMP_BONDING_CURVE_TOP_LEVEL"
    return "PUMP_BONDING_CURVE_CPI"


def raydium_clmm_events(raw: dict) -> tuple[list[dict], list[str]]:
    """Admit only a native CLMM wallet settlement, never a Pump mixed route."""
    tx = raw.get("transaction") or {}
    if raw.get("program_family") == "PUMP_PROGRAM":
        return [], ["PUMP_EXACT_TRADE_TAKES_PRECEDENCE_OVER_CLMM_ACCOUNT"]
    try:
        transitions = raydium_clmm.validate_transaction(tx)
    except Exception as error:
        return [], [f"RAYDIUM_CLMM_DECODER:{type(error).__name__}:{error}"]
    wallet = str(raw.get("wallet") or "")
    deltas = raydium_clmm.wallet_mint_deltas(tx, wallet)
    top_level = any(
        (instruction.get("programId") or "") == raydium_clmm.PROGRAM_ID
        for instruction in ((tx.get("transaction") or {}).get("message") or {}).get("instructions") or []
    )
    route = "RAYDIUM_CLMM_TOP_LEVEL" if top_level else "RAYDIUM_CLMM_CPI"
    events: list[dict] = []
    for index, transition in enumerate(transitions):
        if transition.get("payer") != wallet and transition.get("outputOwner") != wallet:
            continue
        input_mint = str(transition.get("inputMint") or "")
        output_mint = str(transition.get("outputMint") or "")
        input_delta = int(deltas.get(input_mint, 0))
        output_delta = int(deltas.get(output_mint, 0))
        if output_delta > 0 and input_delta < 0:
            side, mint, quote_mint, amount = "BUY", output_mint, input_mint, output_delta
        elif output_delta < 0 and input_delta > 0:
            side, mint, quote_mint, amount = "SELL", input_mint, output_mint, input_delta
        else:
            continue
        events.append({
            "signature": raw.get("signature"),
            "slot": int(raw.get("slot") or 0),
            "block_time": int(raw.get("block_time") or 0),
            "event_index": index,
            "route": route,
            "route_certainty": "EXACT_ROUTE_AND_TRANSITION",
            "underlying_venue": "RAYDIUM_CLMM",
            "side": side,
            "mint": mint,
            "pool": transition.get("pool"),
            "quote_mint": quote_mint,
            "amount_raw": int(amount),
            "quote_amount_raw": abs(input_delta if side == "BUY" else output_delta),
            "fees": {
                "trade_raw": transition.get("tradeFeeInputRaw"),
                "input_transfer_raw": transition.get("inputTransferFeeRaw"),
                "output_transfer_raw": transition.get("outputTransferFeeRaw"),
            },
            "pre_state": transition.get("preState"),
            "post_state": transition.get("postState"),
            "source_frame_sha256": raw.get("transaction_sha256"),
        })
    return events, [] if events else ["EXACT_CLMM_TRANSITION_WITHOUT_PROVEN_WALLET_SETTLEMENT"]

def curve_events_with_extension(tx: dict) -> tuple[list, str]:
    logs = (tx.get("meta") or {}).get("logMessages") or []
    events = []
    variant = "CANONICAL"
    for line in logs:
        marker = "Program data: "
        if marker not in line:
            continue
        try:
            payload = base64.b64decode(line.split(marker, 1)[1].strip(), validate=True)
        except Exception:
            continue
        if not payload.startswith(pumpcurve.TRADE_EVENT_DISCRIMINATOR):
            continue
        try:
            events.append(pumpcurve.decode_trade_event(payload))
        except Exception:
            if len(payload) <= 32:
                raise
            events.append(pumpcurve.decode_trade_event(payload[:-32]))
            variant = "TRAILING_32_BYTES_EXTENSION"
    return events, variant

def pumpswap_tx_with_extension(tx: dict) -> tuple[dict, str]:
    clone = json.loads(json.dumps(tx))
    logs = (clone.get("meta") or {}).get("logMessages") or []
    changed = False
    for index, line in enumerate(logs):
        marker = "Program data: "
        if marker not in line:
            continue
        try:
            payload = base64.b64decode(line.split(marker, 1)[1].strip(), validate=True)
        except Exception:
            continue
        if not (payload.startswith(pumpswap.BUY_EVENT) or payload.startswith(pumpswap.SELL_EVENT)):
            continue
        try:
            pumpswap.decode_event(payload)
        except Exception:
            if len(payload) <= 32:
                raise
            pumpswap.decode_event(payload[:-32])
            logs[index] = line.split(marker, 1)[0] + marker + base64.b64encode(payload[:-32]).decode()
            changed = True
    return clone, "TRAILING_32_BYTES_EXTENSION" if changed else "CANONICAL"

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--events-out", type=Path, required=True)
    args = parser.parse_args()
    raw_rows = []
    for line in args.raw.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row["schema"] = "KEFI_13_ARM_FINALIZED_RPC_RAW_TX_V1"
        row["tracked_wallet"] = row.get("wallet")
        raw_rows.append(row)
    wallet = str(raw_rows[0].get("tracked_wallet")) if raw_rows else ""
    routes = {}
    route_inventory = {}
    events = []
    failures = {}
    for raw in raw_rows:
        tx = raw.get("transaction") or {}
        if (tx.get("meta") or {}).get("err") is not None:
            continue
        try:
            provenance = route_provenance(tx)
            inventory_route = resolved_route_label(tx)
            inventory = route_inventory.setdefault(inventory_route, {"route": inventory_route, "raw_transactions": 0, "resolved": inventory_route != "UNRESOLVED_ROUTE"})
            inventory["raw_transactions"] += 1
            if raw.get("program_family") == "PUMP_PROGRAM":
                curve_events, curve_variant = curve_events_with_extension(tx)
                exact_route = pump_curve_route(tx)
                for event_index, event in enumerate(curve_events):
                    if event.user != wallet:
                        continue
                    row = {
                        "signature": raw.get("signature"), "slot": raw.get("slot"), "block_time": raw.get("block_time"),
                        "event_index": event_index, "route": exact_route, "route_certainty": "EXACT_ROUTE_AND_TRANSITION", "decoder_variant": curve_variant,
                        "underlying_venue": "PUMP_BONDING_CURVE", "side": "BUY" if event.is_buy else "SELL",
                        "mint": event.mint, "pool": None, "quote_mint": event.quote_mint,
                        "amount_raw": int(event.token_amount), "quote_amount_raw": int(event.quote_amount),
                        "sol_amount_raw": int(event.sol_amount), "pre_state": event.pre_state.__dict__,
                        "post_state": event.post_state.__dict__,
                        "fees": {"fee_bps": int(event.fee_basis_points), "fee_raw": int(event.fee), "creator_bps": int(event.creator_fee_basis_points), "creator_raw": int(event.creator_fee)},
                    }
                    events.append(row)
                    bucket = routes.setdefault(exact_route, {"route": exact_route, "raw_transactions": 0, "genuine_exact_events": 0, "genuine_buys": 0, "genuine_sells": 0, "unique_signatures": set(), "decoder_failures": 0})
                    bucket["genuine_exact_events"] += 1
                    bucket["genuine_buys"] += int(row["side"] == "BUY")
                    bucket["genuine_sells"] += int(row["side"] == "SELL")
                    bucket["unique_signatures"].add(str(raw.get("signature")))
                continue
            native_events, native_failures = raydium_clmm_events(raw)
            if native_events or any("RAYDIUM_CLMM" in item for item in native_failures):
                for event in native_events:
                    events.append(event)
                    bucket = routes.setdefault(event["route"], {"route": event["route"], "raw_transactions": 0, "genuine_exact_events": 0, "genuine_buys": 0, "genuine_sells": 0, "unique_signatures": set(), "decoder_failures": 0})
                    bucket["genuine_exact_events"] += 1
                    bucket["genuine_buys"] += int(event["side"] == "BUY")
                    bucket["genuine_sells"] += int(event["side"] == "SELL")
                    bucket["unique_signatures"].add(str(raw.get("signature")))
                continue
            if provenance is None:
                continue
            tx_for_decode, pumpswap_variant = pumpswap_tx_with_extension(tx)
            transitions = pumpswap.validate_transaction(tx_for_decode)
            for index, transition in enumerate(transitions):
                if transition.get("user") != wallet:
                    continue
                event = {
                    "signature": raw.get("signature"), "slot": raw.get("slot"), "decoder_variant": pumpswap_variant,
                    "block_time": raw.get("block_time"), "event_index": index,
                    **provenance, "user": transition.get("user"), "side": transition.get("side"),
                    "mint": transition.get("baseMint"), "pool": transition.get("pool"),
                    "quote_mint": transition.get("quoteMint"),
                    "amount_raw": int(transition.get("amountRaw") or 0),
                    "user_quote_amount_raw": int(transition.get("userQuoteAmountRaw") or 0),
                    "pool_quote_delta_raw": int(transition.get("poolQuoteDeltaRaw") or 0),
                    "pre_state": transition.get("preState"), "post_state": transition.get("postState"),
                    "fees": {"lp_bps": int(transition.get("lpFeeBps") or 0), "protocol_bps": int(transition.get("protocolFeeBps") or 0), "creator_bps": int(transition.get("coinCreatorBps") or 0)},
                }
                events.append(event)
                bucket = routes.setdefault(event["route"], {"route": event["route"], "raw_transactions": 0, "genuine_exact_events": 0, "genuine_buys": 0, "genuine_sells": 0, "unique_signatures": set(), "decoder_failures": 0})
                bucket["genuine_exact_events"] += 1
                bucket["genuine_buys"] += int(event["side"] == "BUY")
                bucket["genuine_sells"] += int(event["side"] == "SELL")
                bucket["unique_signatures"].add(str(raw.get("signature")))
        except Exception as error:
            failures[type(error).__name__ + ":" + str(error)] = failures.get(type(error).__name__ + ":" + str(error), 0) + 1
    for raw in raw_rows:
        try:
            provenance = route_provenance(raw.get("transaction") or {})
            if provenance and provenance["route"] in routes:
                routes[provenance["route"]]["raw_transactions"] += 1
        except Exception:
            pass
    summaries = []
    for bucket in routes.values():
        bucket["unique_signatures"] = len(bucket["unique_signatures"])
        summaries.append(bucket)
    result = {
        "schema": "WALLET_ONLY_ROUTE_CENSUS_V1",
        "generated_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
        "read_only": True, "outcome_blind": True, "strategy_selection_performed": False,
        "tracked_wallet": wallet, "raw_transactions": len(raw_rows), "routes": summaries,
        "route_inventory": sorted(route_inventory.values(), key=lambda row: (-row["raw_transactions"], row["route"])),
        "decoder_failures": failures,
    }
    result["source_schema"] = "WALLET_FINALIZED_ROUTE_RAW_TX_V1"
    result["inputs"] = {"raw_path": str(args.raw), "raw_sha256": sha256_file(args.raw)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.events_out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.events_out.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in events), encoding="utf-8")
    print(json.dumps({"status": "WALLET_ONLY_ROUTE_CENSUS_COMPLETE", "wallet": wallet, "raw": len(raw_rows), "events": len(events)}, sort_keys=True))

if __name__ == "__main__":
    main()
