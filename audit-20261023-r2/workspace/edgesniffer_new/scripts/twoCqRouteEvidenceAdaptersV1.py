#!/usr/bin/env python3
"""Outcome-blind route resolution and selected-entry exact evidence adapters."""

from __future__ import annotations

import dataclasses
from typing import Any

from exactCurveEconomicsV1 import (
    ExactCurveError,
    extract_pump_source_observations,
)
from exactCurveEconomicsQuoteV2 import (
    decode_quote_trade_events,
    validate_quote_transition,
)
import launchLabExactEconomicsV1 as launchlab
import pumpSwapExactEconomicsV1 as pumpswap
import raydiumCpmmExactEconomicsV1 as cpmm
import raydiumClmmExactEconomicsV1 as clmm
import meteoraDbcExactEconomicsV1 as dbc


SCHEMA = "2CQ_ROUTE_EVIDENCE_ADAPTERS_V1"
WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP = pumpswap.PROGRAM_ID
PROGRAMS = {
    PUMP: "PUMP_BONDING_CURVE",
    PUMPSWAP: "PUMPSWAP",
    launchlab.PROGRAM_ID: "RAYDIUM_LAUNCHLAB",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "RAYDIUM_CPMM",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "RAYDIUM_CLMM",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "RAYDIUM_AMM_V4",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "METEORA_DLMM",
    "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN": "METEORA_DBC",
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG": "METEORA_DAMM_V2",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "ORCA_WHIRLPOOL",
}
ROUTERS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "JUPITER_V6",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "JUPITER_V4",
}
BENIGN = {
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ",
}


def resolved_keys(tx: dict[str, Any]) -> list[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = [
        str(row.get("pubkey")) if isinstance(row, dict) else str(row)
        for row in message.get("accountKeys") or []
    ]
    loaded = (tx.get("meta") or {}).get("loadedAddresses") or {}
    keys.extend(str(row) for row in loaded.get("writable") or [])
    keys.extend(str(row) for row in loaded.get("readonly") or [])
    return keys


def invoked_programs(tx: dict[str, Any]) -> tuple[list[str], list[str]]:
    keys = resolved_keys(tx)
    message = (tx.get("transaction") or {}).get("message") or {}
    top: list[str] = []
    inner: list[str] = []
    for instruction in message.get("instructions") or []:
        index = instruction.get("programIdIndex")
        program = instruction.get("programId")
        if program is None and index is not None and int(index) < len(keys):
            program = keys[int(index)]
        if program:
            top.append(str(program))
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        for instruction in group.get("instructions") or []:
            index = instruction.get("programIdIndex")
            program = instruction.get("programId")
            if program is None and index is not None and int(index) < len(keys):
                program = keys[int(index)]
            if program:
                inner.append(str(program))
    return list(dict.fromkeys(top)), list(dict.fromkeys(inner))


def resolve_route(tx: dict[str, Any]) -> dict[str, Any]:
    top, inner = invoked_programs(tx)
    top_set, inner_set = set(top), set(inner)
    routers = [ROUTERS[row] for row in top if row in ROUTERS]
    venues = [
        {"programId": row, "venue": PROGRAMS[row], "invocation": "TOP_LEVEL"}
        for row in top if row in PROGRAMS
    ]
    venues.extend(
        {"programId": row, "venue": PROGRAMS[row], "invocation": "CPI"}
        for row in inner if row in PROGRAMS and row not in top_set
    )
    unknown = [
        row for row in dict.fromkeys(top + inner)
        if row not in PROGRAMS and row not in ROUTERS and row not in BENIGN
    ]
    economic = [row for row in venues if row["venue"] != "PUMP_BONDING_CURVE"]
    if not economic and any(row["venue"] == "PUMP_BONDING_CURVE" for row in venues):
        economic = [row for row in venues if row["venue"] == "PUMP_BONDING_CURVE"]
    return {
        "schema": SCHEMA + "_ROUTE_RESOLUTION",
        "topLevelPrograms": top,
        "innerPrograms": inner,
        "routers": routers,
        "underlyingVenues": economic,
        "unknownPrograms": unknown,
        "resolved": len(economic) == 1,
        "resolution": economic[0] if len(economic) == 1 else None,
    }


def exact_wallet_settlement(
    tx: dict[str, Any], mint: str, wallet: str = WALLET
) -> dict[str, Any]:
    """Return finalized wallet accounting without claiming AMM curve semantics."""
    meta = tx.get("meta") or {}
    keys = resolved_keys(tx)
    balances = list(meta.get("preTokenBalances") or []) + list(
        meta.get("postTokenBalances") or []
    )
    owned_indices = {
        int(row["accountIndex"])
        for row in balances
        if str(row.get("mint") or "") == mint and str(row.get("owner") or "") == wallet
    }

    def token_total(name: str) -> int:
        total = 0
        for row in meta.get(name) or []:
            if int(row.get("accountIndex") or -1) not in owned_indices:
                continue
            if str(row.get("mint") or "") != mint:
                continue
            total += int(((row.get("uiTokenAmount") or {}).get("amount")) or 0)
        return total

    pre_tokens = token_total("preTokenBalances")
    post_tokens = token_total("postTokenBalances")
    wallet_index = keys.index(wallet) if wallet in keys else -1
    pre_lamports = meta.get("preBalances") or []
    post_lamports = meta.get("postBalances") or []
    lamport_delta = None
    if 0 <= wallet_index < len(pre_lamports) and wallet_index < len(post_lamports):
        lamport_delta = int(post_lamports[wallet_index]) - int(pre_lamports[wallet_index])
    if not owned_indices or post_tokens <= pre_tokens:
        raise ValueError("NO_EXACT_POSITIVE_WALLET_BASE_RECEIPT")
    return {
        "schema": SCHEMA + "_EXACT_WALLET_SETTLEMENT",
        "mint": mint,
        "wallet": wallet,
        "baseTokenAccountIndices": sorted(owned_indices),
        "preBaseAmountRaw": pre_tokens,
        "postBaseAmountRaw": post_tokens,
        "baseReceivedRaw": post_tokens - pre_tokens,
        "walletLamportDeltaRaw": lamport_delta,
        "transactionFeeLamports": int(meta.get("fee") or 0),
        "finalizedSuccessful": meta.get("err") is None,
        "exactBaseInventory": True,
        "exactCurveEconomics": False,
    }


def non_sol_pump_selected_evidence(
    tx: dict[str, Any], signature: str, mint: str
) -> dict[str, Any]:
    sources = extract_pump_source_observations(tx, signature, mint, 0, "finalized_chain_order")
    quote_events = [
        row for row in decode_quote_trade_events((tx.get("meta") or {}).get("logMessages") or [])
        if row.mint == mint and row.user == WALLET and row.is_buy
    ]
    sources = [row for row in sources if row.side == "buy"]
    if len(sources) != 1 or len(quote_events) != 1:
        raise ExactCurveError(
            f"non-SOL Pump source/event cardinality {len(sources)}/{len(quote_events)}"
        )
    result = validate_quote_transition(sources[0].instruction, quote_events[0])
    return result | {
        "instructionCoordinate": sources[0].instruction_index,
        "canonicalOrderOnly": True,
        "quoteScopedControlsRequired": True,
    }


def acquired_asset_transition(
    row: dict[str, Any], mint: str, *, base_key: str = "baseMint",
    quote_key: str = "quoteMint",
) -> dict[str, Any]:
    """Normalize a two-asset swap from the selected asset's perspective."""
    side = str(row.get("side") or "").upper()
    base_mint = str(row.get(base_key) or "")
    quote_mint = str(row.get(quote_key) or "")
    if side == "BUY":
        acquired_mint, spent_mint = base_mint, quote_mint
        acquired_amount = row.get("walletBaseDeltaRaw", row.get("amountRaw"))
        spent_amount = row.get("walletQuoteDeltaRaw", row.get("userQuoteAmountRaw"))
        if isinstance(spent_amount, int) and spent_amount < 0:
            spent_amount = -spent_amount
    elif side == "SELL":
        acquired_mint, spent_mint = quote_mint, base_mint
        acquired_amount = row.get("walletQuoteDeltaRaw", row.get("userQuoteAmountRaw"))
        spent_amount = row.get("walletBaseDeltaRaw", row.get("amountRaw"))
        if isinstance(spent_amount, int) and spent_amount < 0:
            spent_amount = -spent_amount
    else:
        raise ValueError(f"unsupported acquired-asset side {side!r}")
    if acquired_mint != mint:
        raise ValueError("selected mint is not the acquired asset")
    if acquired_amount is not None and int(acquired_amount) <= 0:
        raise ValueError("selected asset did not increase")
    return row | {
        "selectedAssetPerspective": True,
        "acquiredMint": acquired_mint,
        "spentMint": spent_mint,
        "acquiredAmountRaw": None if acquired_amount is None else int(acquired_amount),
        "spentAmountRaw": None if spent_amount is None else int(spent_amount),
    }


def selected_exact_evidence(
    family: str, tx: dict[str, Any], signature: str, mint: str,
    resolved_venue: str | None = None,
) -> dict[str, Any]:
    if family == "NON_SOL_PUMP_V2":
        return non_sol_pump_selected_evidence(tx, signature, mint)
    if family in {"PUMPSWAP", "PUMPSWAP_CPI"}:
        rows = [
            acquired_asset_transition(row, mint)
            for row in pumpswap.validate_transaction(tx)
            if row["user"] == WALLET and (
                (row["side"] == "BUY" and row["baseMint"] == mint)
                or (row["side"] == "SELL" and row["quoteMint"] == mint)
            )
        ]
        if len(rows) != 1:
            raise pumpswap.PumpSwapExactError(
                f"selected PumpSwap exact transition cardinality {len(rows)}"
            )
        return rows[0]
    if family == "OTHER_DEX":
        if resolved_venue == "METEORA_DBC":
            return dbc.selected_wallet_acquisition(tx, mint, WALLET)
        if resolved_venue == "RAYDIUM_CLMM":
            return clmm.selected_wallet_acquisition(tx, mint, WALLET)
        rows = [
            acquired_asset_transition(row, mint)
            for row in launchlab.validate_transaction(tx)
            if row["payer"] == WALLET and (
                (row["side"] == "BUY" and row["baseMint"] == mint)
                or (row["side"] == "SELL" and row["quoteMint"] == mint)
            )
        ]
        if len(rows) == 1:
            return rows[0] | {"underlyingVenue": "RAYDIUM_LAUNCHLAB"}
        if resolved_venue == "RAYDIUM_LAUNCHLAB":
            raise launchlab.LaunchLabExactError(
                "selected LaunchLab exact transition cardinality 0"
            )
        cpmm_rows = [
            row for row in cpmm.validate_transaction(tx)
            if row["outputMint"] == mint and row["outputOwner"] == WALLET
        ]
        if len(cpmm_rows) == 1:
            row = cpmm_rows[0]
            return row | {
                "underlyingVenue": "RAYDIUM_CPMM",
                "selectedAssetPerspective": True,
                "acquiredMint": row["outputMint"],
                "spentMint": row["inputMint"],
                "acquiredAmountRaw": row["walletOutputDeltaRaw"],
                "spentAmountRaw": -row["walletInputDeltaRaw"],
            }
        if resolved_venue == "RAYDIUM_CPMM":
            raise cpmm.RaydiumCpmmExactError(
                "selected CPMM exact transition cardinality 0"
            )
        clmm_route = clmm.selected_wallet_acquisition(tx, mint, WALLET)
        return clmm_route
    raise ValueError("NO_EXACT_ADAPTER_FOR_RESOLVED_ROUTE")
