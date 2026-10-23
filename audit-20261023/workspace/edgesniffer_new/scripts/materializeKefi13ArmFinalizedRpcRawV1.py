#!/usr/bin/env python3
"""Materialize finalized KEFI-wallet RPC rows into canonical arm observations.

The source is deliberately broader than the materializer.  A transaction is
assigned to an arm only when its venue/program evidence is sufficient for the
arm's frozen contract.  Unresolved rows are reported as quarantine evidence;
they are never forced into a strategy or evaluator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pumpSwapExactEconomicsV1 as pumpswap
import exactCurveEconomicsV1 as pumpcurve
import raydiumClmmExactEconomicsV1 as raydium_clmm
import launchLabExactEconomicsV1 as launchlab
import raydiumCpmmExactEconomicsV1 as raydium_cpmm
import meteoraDbcExactEconomicsV1 as meteora_dbc
import twoCqRouteEvidenceAdaptersV1 as route_adapters


RAW_SCHEMA = "KEFI_13_ARM_FINALIZED_RPC_RAW_TX_V1"
OBS_SCHEMA = "KEFI_13_ARM_OBSERVATION_V1"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
LAUNCHLAB = "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"
RAYDIUM_CPMM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
RAYDIUM_CLMM = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
RAYDIUM_AMM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
METEORA_DLMM = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
METEORA_DBC = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"
ORCA = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
JUPITERS = {"JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB"}


def sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def resolve_arm(program_ids: set[str]) -> tuple[str | None, str]:
    """Return (arm, reason), failing closed where the frozen arm is not proven."""
    if PUMPSWAP in program_ids:
        if program_ids & JUPITERS:
            return None, "PUMPSWAP_ROUTER_PATH_NOT_AN_ARM07_DIRECT_ENTRY"
        if PUMP in program_ids:
            return "ARM08_PUMPSWAP_CPI", "PUMPSWAP_AND_PUMP_PROGRAM_CPI_ATTRIBUTION"
        return "ARM07_PUMPSWAP_DIRECT", "PUMPSWAP_PROGRAM_ONLY_DIRECT"
    if program_ids & JUPITERS:
        underlying = program_ids & {RAYDIUM_CLMM, ORCA, METEORA_DLMM}
        if len(underlying) != 1:
            return None, "ROUTER_UNDERLYING_VENUE_NOT_UNIQUELY_RESOLVED"
        if RAYDIUM_CLMM in underlying:
            return "ARM09_ROUTER_RAYDIUM_CLMM", "JUPITER_PLUS_RAYDIUM_CLMM"
        if ORCA in underlying:
            return "ARM10_ROUTER_ORCA_WHIRLPOOL", "JUPITER_PLUS_ORCA_WHIRLPOOL"
        return "ARM11_ROUTER_METEORA_DLMM", "JUPITER_PLUS_METEORA_DLMM"
    if LAUNCHLAB in program_ids:
        return "ARM05_RAYDIUM_LAUNCHLAB", "LAUNCHLAB_PROGRAM_PRESENT_STONK_OVERLAY_NOT_ASSUMED"
    if program_ids & {RAYDIUM_CPMM, RAYDIUM_AMM, METEORA_DBC}:
        protocols = program_ids & {RAYDIUM_CPMM, RAYDIUM_AMM, METEORA_DBC}
        if len(protocols) == 1:
            return "ARM12_OTHER_DEX_PROTOCOL_NATIVE", "ONE_PROTOCOL_NATIVE_OTHER_DEX_PROGRAM"
        return None, "MULTIPLE_OTHER_DEX_PROTOCOLS_NOT_SPLIT"
    if PUMP in program_ids:
        return None, "PUMP_FAMILY_CURVE_CPI_SIBLING_NOT_UNIQUELY_RESOLVED"
    return None, "NO_CANONICAL_ARM_PROGRAM_OBSERVED"


def resolve_arm_for_row(row: dict[str, Any]) -> tuple[str | None, str]:
    """Resolve PumpSwap CPI from canonical inner-instruction route evidence.

    The finalized source's observed_program_ids are top-level program IDs.  A
    CPI PumpSwap trade can therefore have PumpSwap absent from that list while
    still being a genuine ARM08 route.  Route evidence is authoritative for
    this distinction; the program-ID fallback remains fail-closed for other
    venues and synthetic/unit-test rows without transaction evidence.
    """
    program_ids = set(row.get("observed_program_ids") or [])
    transaction = row.get("transaction") or {}
    if PUMPSWAP in program_ids or transaction:
        try:
            venues = route_adapters.resolve_route(transaction).get("underlyingVenues") or []
        except Exception:
            venues = []
        if len(venues) == 1 and venues[0].get("venue") == "PUMPSWAP":
            invocation = venues[0].get("invocation")
            if invocation == "CPI":
                return "ARM08_PUMPSWAP_CPI", "PUMPSWAP_ROUTE_EVIDENCE_CPI"
            if invocation == "TOP_LEVEL":
                return "ARM07_PUMPSWAP_DIRECT", "PUMPSWAP_ROUTE_EVIDENCE_TOP_LEVEL"
    return resolve_arm(program_ids)


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def decoded_pumpswap_entry(row: dict[str, Any], arm_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Decode current PumpSwap v1/v2 events and expose only teacher BUY entries."""
    if arm_id not in {"ARM07_PUMPSWAP_DIRECT", "ARM08_PUMPSWAP_CPI"}:
        return [], []
    arm08_settlement: dict[str, Any] | None = None
    if arm_id == "ARM08_PUMPSWAP_CPI":
        try:
            resolved = route_adapters.resolve_route(row["transaction"])
        except Exception as error:
            return [], ["ARM08_CPI_ROUTE_RESOLUTION:" + type(error).__name__ + ":" + str(error)]
        venues = resolved.get("underlyingVenues") or []
        if len(venues) != 1 or venues[0].get("venue") != "PUMPSWAP" or venues[0].get("invocation") != "CPI":
            return [], ["ARM08_CPI_ROUTE_NOT_PROVEN"]
    try:
        transitions = pumpswap.validate_transaction(row["transaction"])
    except Exception as error:
        return [], ["PUMPSWAP_EXACT_DECODER:" + type(error).__name__ + ":" + str(error)]
    if arm_id == "ARM08_PUMPSWAP_CPI":
        wallet_buys = [
            transition for transition in transitions
            if transition.get("user") == row.get("tracked_wallet") and transition.get("side") == "BUY"
        ]
        if len(wallet_buys) != 1:
            return [], [f"ARM08_NOT_EXACTLY_ONE_WALLET_BUY:{len(wallet_buys)}"]
        try:
            arm08_settlement = route_adapters.exact_wallet_settlement(
                row["transaction"], str(wallet_buys[0]["baseMint"]), str(row.get("tracked_wallet") or "")
            )
        except Exception as error:
            return [], ["ARM08_NO_EXACT_POSITIVE_WALLET_SETTLEMENT:" + type(error).__name__ + ":" + str(error)]
    entries: list[dict[str, Any]] = []
    ignored: list[str] = []
    for event_index, transition in enumerate(transitions):
        if transition.get("user") != row.get("tracked_wallet"):
            ignored.append("PUMPSWAP_EVENT_USER_NOT_TRACKED_WALLET")
            continue
        if transition.get("side") != "BUY":
            ignored.append("PUMPSWAP_SELL_NOT_ENTRY")
            continue
        post_state = transition["postState"]
        base_amount = int(transition["amountRaw"])
        block_time = int(row.get("block_time") or 0)
        event = {
            "candidate_signature": row["signature"],
            "event_index": event_index,
            "slot": int(row["slot"]),
            "received_at_unix_ns": block_time * 1_000_000_000 + event_index,
            "event_timestamp": block_time,
            "source_frame_sha256": row["transaction_sha256"],
            "source_partition": "finalized-rpc-wallet-capture",
            "is_tracked_teacher": True,
            "exact_transition_validated": True,
            "side": transition["side"],
            "amount_mode": transition["amountMode"],
            "base_amount": base_amount,
            "mint": transition["baseMint"],
            "pool": transition["pool"],
            "quote_mint": transition["quoteMint"],
            "pool_base_raw": int(post_state["base"]),
            "pool_quote_raw": int(post_state["quote"]),
            "pool_quote_delta": int(transition["poolQuoteDeltaRaw"]),
            "virtual_quote_reserves": int(post_state["virtual_quote"]),
            "curve_quote_amount": int(transition["curveQuoteAmountRaw"]),
            "user_quote_amount": int(transition["userQuoteAmountRaw"]),
            "limit_quote_amount": int(transition["limitQuoteAmount"]),
            "min_base_amount": int(transition["minBaseAmount"]),
            "lp_fee_bps": int(transition["lpFeeBps"]),
            "lp_fee": int(transition["lpFeeRaw"]),
            "protocol_fee_bps": int(transition["protocolFeeBps"]),
            "protocol_fee": int(transition["protocolFeeRaw"]),
            "coin_creator_bps": int(transition["coinCreatorBps"]),
            "coin_creator_fee": int(transition["creatorFeeRaw"]),
            "cashback_bps": int(transition["cashbackBps"]),
            "cashback": int(transition["cashbackRaw"]),
            "buyback_bps": int(transition["buybackBps"]),
            "buyback_fee": int(transition["buybackFeeRaw"]),
        }
        if arm08_settlement is not None:
            event["arm08_admission"] = {
                "genuine_cpi_wallet_buy": True,
                "positive_wallet_settlement": True,
                "settlement": arm08_settlement,
            }
        entries.append(event)
    return entries, ignored


def pump_activity_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Classify Pump-program activity without assigning a strategy arm.

    The wallet source deliberately includes every finalized transaction that
    mentions the Pump program.  Fee distribution, account maintenance, and
    other program instructions are not trades.  This helper uses only the
    exact Pump TradeEvent and instruction discriminators; it never promotes a
    row based on watch metadata or token-account balance changes.
    """
    transaction = row.get("transaction") or {}
    meta = transaction.get("meta") or {}
    events = pumpcurve.decode_trade_events(meta.get("logMessages") or [])
    keys = pumpcurve.resolved_account_keys(transaction)
    message = (transaction.get("transaction") or {}).get("message") or {}
    instructions: list[dict[str, Any]] = []
    compiled: list[dict[str, Any]] = list(message.get("instructions") or [])
    for group in meta.get("innerInstructions") or []:
        compiled.extend(group.get("instructions") or [])
    for instruction in compiled:
        if instruction.get("programId") != pumpcurve.PUMP_PROGRAM_ID:
            continue
        encoded = instruction.get("data")
        if not encoded:
            continue
        try:
            decoded = pumpcurve.decode_pump_instruction(pumpcurve.base58_decode(encoded))
        except pumpcurve.ExactCurveError:
            continue
        instructions.append({
            "kind": decoded.kind,
            "side": decoded.side,
            "exact_quote_in": decoded.exact_quote_in,
        })
    log_names = [
        line.split("Instruction:", 1)[1].strip()
        for line in (meta.get("logMessages") or [])
        if "Program log: Instruction:" in line
    ]
    return {
        "trade_event_count": len(events),
        "decoded_trade_instruction_count": len(instructions),
        "decoded_trade_instructions": instructions,
        "instruction_log_names": log_names,
        "is_exact_trade": bool(events and instructions),
    }


def decoded_non_sol_pump_entry(row: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Admit only one exact tracked-wallet non-SOL Pump V2 BUY.

    A Pump program reference alone is insufficient: the quote-native event,
    instruction, wallet, and causal source observation must all join exactly.
    SOL-quoted Pump trades intentionally return no arm here because their
    curve/CPI sibling identity is a separate frozen contract.
    """
    transaction = row.get("transaction") or {}
    events = [
        event for event in route_adapters.decode_quote_trade_events(
            (transaction.get("meta") or {}).get("logMessages") or []
        )
        if event.user == row.get("tracked_wallet") and event.is_buy
    ]
    if not events:
        return [], []
    if len(events) != 1:
        return [], [f"NON_SOL_PUMP_EXACT_EVENT_CARDINALITY:{len(events)}"]
    event = events[0]
    try:
        selected = route_adapters.non_sol_pump_selected_evidence(
            transaction, str(row["signature"]), str(event.mint)
        )
    except Exception as error:
        return [], ["NON_SOL_PUMP_EXACT_DECODER:" + type(error).__name__ + ":" + str(error)]
    return [{
        "candidate_signature": row["signature"],
        "event_index": 0,
        "slot": int(row["slot"]),
        "received_at_unix_ns": int(row.get("block_time") or 0) * 1_000_000_000,
        "event_timestamp": int(row.get("block_time") or 0),
        "source_frame_sha256": row["transaction_sha256"],
        "source_partition": "finalized-rpc-wallet-capture",
        "is_tracked_teacher": True,
        "exact_transition_validated": True,
        "side": "BUY",
        "mint": str(event.mint),
        "pool": None,
        "quote_mint": str(event.quote_mint),
        "venue_transition": selected,
    }], []


def decoded_native_entry(row: dict[str, Any], arm_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Materialize only a proven tracked-wallet acquisition for native adapters.

    A router/venue program ID alone is not an entry.  The exact underlying
    adapter must validate the transition and the tracked wallet's base asset
    balance must increase.  Other arms remain raw-only until their venue
    adapter exposes the same entry contract.
    """
    if arm_id not in {
        "ARM05_RAYDIUM_LAUNCHLAB",
        "ARM09_ROUTER_RAYDIUM_CLMM",
        "ARM12_OTHER_DEX_PROTOCOL_NATIVE",
    }:
        return [], []
    wallet = str(row.get("tracked_wallet") or "")
    entries: list[dict[str, Any]] = []
    transaction = row["transaction"]
    try:
        if arm_id == "ARM05_RAYDIUM_LAUNCHLAB":
            transitions = launchlab.validate_transaction(transaction)
            for event_index, transition in enumerate(transitions):
                if transition.get("payer") != wallet or transition.get("side") != "BUY":
                    continue
                entries.append({
                    "candidate_signature": row["signature"],
                    "event_index": event_index,
                    "slot": int(row["slot"]),
                    "received_at_unix_ns": int(row.get("block_time") or 0) * 1_000_000_000 + event_index,
                    "event_timestamp": int(row.get("block_time") or 0),
                    "source_frame_sha256": row["transaction_sha256"],
                    "source_partition": "finalized-rpc-wallet-capture",
                    "is_tracked_teacher": True,
                    "exact_transition_validated": True,
                    "side": "BUY",
                    "mint": str(transition["baseMint"]),
                    "pool": str(transition["poolState"]),
                    "quote_mint": str(transition["quoteMint"]),
                    "venue_transition": transition,
                })
        elif arm_id == "ARM09_ROUTER_RAYDIUM_CLMM":
            transitions = raydium_clmm.validate_transaction(transaction)
            for event_index, transition in enumerate(transitions):
                if transition.get("payer") != wallet or transition.get("outputOwner") != wallet:
                    continue
                mint = str(transition.get("outputMint") or "")
                try:
                    selected = raydium_clmm.selected_wallet_acquisition(transaction, mint, wallet)
                except Exception:
                    continue
                entries.append({
                    "candidate_signature": row["signature"],
                    "event_index": event_index,
                    "slot": int(row["slot"]),
                    "received_at_unix_ns": int(row.get("block_time") or 0) * 1_000_000_000 + event_index,
                    "event_timestamp": int(row.get("block_time") or 0),
                    "source_frame_sha256": row["transaction_sha256"],
                    "source_partition": "finalized-rpc-wallet-capture",
                    "is_tracked_teacher": True,
                    "exact_transition_validated": True,
                    "side": "BUY",
                    "mint": mint,
                    "pool": selected.get("routeLegs", [{}])[0].get("pool"),
                    "quote_mint": selected.get("spentMint"),
                    "venue_transition": selected,
                })
        else:
            protocols = set(row.get("observed_program_ids") or [])
            if RAYDIUM_CPMM in protocols:
                transitions = raydium_cpmm.validate_transaction(transaction)
                for event_index, transition in enumerate(transitions):
                    if transition.get("payer") != wallet or transition.get("outputOwner") != wallet:
                        continue
                    entries.append({
                        "candidate_signature": row["signature"],
                        "event_index": event_index,
                        "slot": int(row["slot"]),
                        "received_at_unix_ns": int(row.get("block_time") or 0) * 1_000_000_000 + event_index,
                        "event_timestamp": int(row.get("block_time") or 0),
                        "source_frame_sha256": row["transaction_sha256"],
                        "source_partition": "finalized-rpc-wallet-capture",
                        "is_tracked_teacher": True,
                        "exact_transition_validated": True,
                        "side": "BUY",
                        "mint": str(transition["outputMint"]),
                        "pool": str(transition["pool"]),
                        "quote_mint": str(transition["inputMint"]),
                        "venue_transition": transition,
                    })
            elif METEORA_DBC in protocols:
                transitions = meteora_dbc.validate_transaction(transaction)
                selected_mints = {
                    str(transition["outputMint"])
                    for transition in transitions
                    if transition.get("payer") == wallet or transition.get("outputOwner") == wallet
                }
                if len(selected_mints) == 1:
                    mint = next(iter(selected_mints))
                    selected = meteora_dbc.selected_wallet_acquisition(transaction, mint, wallet)
                    entries.append({
                        "candidate_signature": row["signature"],
                        "event_index": 0,
                        "slot": int(row["slot"]),
                        "received_at_unix_ns": int(row.get("block_time") or 0) * 1_000_000_000,
                        "event_timestamp": int(row.get("block_time") or 0),
                        "source_frame_sha256": row["transaction_sha256"],
                        "source_partition": "finalized-rpc-wallet-capture",
                        "is_tracked_teacher": True,
                        "exact_transition_validated": True,
                        "side": "BUY",
                        "mint": mint,
                        "pool": selected["routeLegs"][0]["pool"],
                        "quote_mint": selected["spentMint"],
                        "venue_transition": selected,
                    })
    except Exception as error:
        return [], ["NATIVE_EXACT_DECODER:" + type(error).__name__ + ":" + str(error)]
    if entries:
        return entries, []
    if arm_id == "ARM05_RAYDIUM_LAUNCHLAB":
        return [], [f"RAYDIUM_LAUNCHLAB_EXACT_ENTRY_NOT_MATERIALIZED:validated_transitions={len(locals().get('transitions', []))}:wallet_acquisitions=0"]
    if arm_id == "ARM12_OTHER_DEX_PROTOCOL_NATIVE":
        return [], ["OTHER_DEX_EXACT_ENTRY_NOT_MATERIALIZED"]
    return [], [
        "RAYDIUM_CLMM_EXACT_ENTRY_NOT_MATERIALIZED"
        f":validated_transitions={len(locals().get('transitions', []))}:wallet_acquisitions=0"
    ]


def materialize(rows: list[dict[str, Any]], manifest: dict[str, Any], registry: dict[str, Any], policies: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    arms = {row["id"]: row for row in registry["arms"]}
    policy_by_arm = {row["arm_id"]: row for row in policies["policies"]}
    partitions = {row["arm_id"]: row for row in manifest["partitions"]}
    observations: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    ignored_events: dict[str, int] = {}
    for index, row in enumerate(rows, 1):
        if row.get("schema") != RAW_SCHEMA:
            quarantined.append({"line": index, "reason": "RAW_SCHEMA_MISMATCH"})
            continue
        if not row.get("finalized"):
            quarantined.append({"line": index, "signature": row.get("signature"), "reason": "NOT_FINALIZED"})
            continue
        if parse_utc(str(row["captured_at_utc"])) < parse_utc(str(manifest["cohort_started_at_utc"])):
            quarantined.append({"line": index, "signature": row.get("signature"), "reason": "PRE_COHORT_BOUNDARY"})
            continue
        program_ids = set(row.get("observed_program_ids") or [])
        preselected_arm: tuple[str, str] | None = None
        if program_ids == {PUMP}:
            quote_entries, quote_errors = decoded_non_sol_pump_entry(row)
            if quote_entries:
                preselected_arm = ("ARM04_NON_SOL_PUMP_V2_QUOTE_NATIVE", "EXACT_NON_SOL_QUOTE_NATIVE_TRADE")
            elif quote_errors:
                quarantined.append({"line": index, "signature": row.get("signature"), "reason": quote_errors[0]})
                continue
            activity = pump_activity_evidence(row) if preselected_arm is None else None
            if preselected_arm is None and not activity["is_exact_trade"]:
                quarantined.append({
                    "line": index,
                    "signature": row.get("signature"),
                    "reason": "PUMP_PROGRAM_NON_TRADE_OR_UNSUPPORTED_EXACT_ACTIVITY",
                    "pump_activity": activity,
                })
                continue
        arm_id, reason = preselected_arm or resolve_arm_for_row(row)
        if not arm_id:
            quarantined.append({"line": index, "signature": row.get("signature"), "reason": reason, "observed_program_ids": sorted(program_ids)})
            continue
        arm = arms[arm_id]
        partition = partitions[arm_id]
        policy = policy_by_arm[arm_id]
        if preselected_arm is not None:
            decoded_entries, ignored = quote_entries, []
        elif arm_id in {"ARM07_PUMPSWAP_DIRECT", "ARM08_PUMPSWAP_CPI"}:
            decoded_entries, ignored = decoded_pumpswap_entry(row, arm_id)
        else:
            decoded_entries, ignored = decoded_native_entry(row, arm_id)
        for ignored_reason in ignored:
            ignored_events[ignored_reason] = ignored_events.get(ignored_reason, 0) + 1
        if not decoded_entries:
            decoder_errors = [item for item in ignored if item.startswith("PUMPSWAP_EXACT_DECODER:")]
            decoder_errors.extend(item for item in ignored if item.startswith("RAYDIUM_CLMM_EXACT_DECODER:"))
            decoder_errors.extend(item for item in ignored if item.startswith("NATIVE_EXACT_DECODER:"))
            if decoder_errors:
                quarantined.append({"line": index, "signature": row.get("signature"), "reason": decoder_errors[0]})
                continue
            if ignored:
                continue
            else:
                quarantined.append({"line": index, "signature": row.get("signature"), "reason": "EXACT_VENUE_ENTRY_NOT_MATERIALIZED"})
            continue
        event_entries = decoded_entries or [None]
        for decoded_event in event_entries:
            identity = {"cohort_id": manifest["cohort_id"], "signature": row["signature"], "arm_id": arm_id, "event_index": (decoded_event or {}).get("event_index", 0)}
            state = {
                "raw_evidence": {
                    "signature": row["signature"],
                    "transaction_sha256": row["transaction_sha256"],
                    "tracked_wallet": row.get("tracked_wallet"),
                    "observed_program_ids": sorted(program_ids),
                    "observed_program_families": row.get("observed_program_families", []),
                    "candidate_arm_ids": row.get("candidate_arm_ids", []),
                    "resolution_reason": reason,
                },
                "finalized": True,
            }
            if decoded_event is not None:
                state["decoded_event"] = decoded_event
            observations.append({
            "schema": OBS_SCHEMA,
            "observation_id": "obs-" + sha256(identity)[:32],
            "cohort_id": manifest["cohort_id"],
            "captured_at_utc": row["captured_at_utc"],
            "source": "kefi-finalized-rpc-wallet-capture-v1",
            "arm_id": arm_id,
            "venue": arm["venue"],
            "venue_class": arm["venue_class"],
            "policy_id": policy["policy_id"],
            "registry_sha256": manifest["registry_sha256"],
            "policies_sha256": manifest["policies_sha256"],
            "decoder_status": partition["decoder_status"],
            "evaluator_status": partition["evaluator_status"],
            "finalized_slot": int(row["slot"]),
            "state": state,
            })
    report = {
        "schema": "KEFI_13_ARM_FINALIZED_RPC_MATERIALIZATION_AUDIT_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "outcome_blind": True,
        "input_rows": len(rows),
        "materialized_observations": len(observations),
        "quarantined_rows": len(quarantined),
        "by_arm": {arm_id: sum(row["arm_id"] == arm_id for row in observations) for arm_id in arms},
        "quarantine_reasons": {reason: sum(row["reason"] == reason for row in quarantined) for reason in sorted({row["reason"] for row in quarantined})},
        "ignored_event_reasons": ignored_events,
        "quarantine": quarantined,
        "status": "PASS_OUTCOME_BLIND_FAIL_CLOSED",
    }
    return observations, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--policies", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    observations, report = materialize(rows, json.loads(args.manifest.read_text(encoding="utf-8")), json.loads(args.registry.read_text(encoding="utf-8")), json.loads(args.policies.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in observations), encoding="utf-8")
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("input_rows", "materialized_observations", "quarantined_rows", "status")}, sort_keys=True))


if __name__ == "__main__":
    main()
