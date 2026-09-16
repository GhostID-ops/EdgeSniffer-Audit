#!/usr/bin/env python3
"""Build an immutable N25-v2 live/on-chain/shadow economics closeout."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import urllib.request
from collections import defaultdict
from pathlib import Path


def rows(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        line_number = 0
        while True:
            try:
                line = handle.readline()
            except EOFError:
                # A snapshot of an actively appended gzip member may end between
                # writes. Every complete JSONL row before that boundary is valid.
                break
            if not line:
                break
            line_number += 1
            try:
                yield line_number, json.loads(line)
            except json.JSONDecodeError:
                continue


def rpc(url: str, method: str, params: list):
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if body.get("error"):
        raise RuntimeError(f"{method}: {body['error']}")
    return body.get("result")


def account_keys(transaction: dict) -> list[str]:
    message = transaction["transaction"]["message"]
    keys = [key["pubkey"] if isinstance(key, dict) else key for key in message["accountKeys"]]
    loaded = transaction.get("meta", {}).get("loadedAddresses") or {}
    return keys + loaded.get("writable", []) + loaded.get("readonly", [])


def wallet_delta(transaction: dict, wallet: str) -> dict:
    keys = account_keys(transaction)
    index = keys.index(wallet)
    meta = transaction["meta"]
    delta = int(meta["postBalances"][index]) - int(meta["preBalances"][index])
    return {
        "walletDeltaLamports": delta,
        "networkFeeLamports": int(meta["fee"]),
        "slot": transaction.get("slot"),
        "err": meta.get("err"),
    }


def token_delta(transaction: dict, wallet: str, mint: str) -> int:
    meta = transaction["meta"]

    def owned_amount(balances: list[dict]) -> int:
        total = 0
        for balance in balances:
            if balance.get("owner") == wallet and balance.get("mint") == mint:
                total += int(balance["uiTokenAmount"]["amount"])
        return total

    return owned_amount(meta.get("postTokenBalances") or []) - owned_amount(
        meta.get("preTokenBalances") or []
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--shadow-outcomes", required=True, type=Path)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--rpc-url", default=os.environ.get("SECURE_RPC_URL"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--recovery-mint")
    parser.add_argument("--recovery-signature")
    args = parser.parse_args()
    if not args.rpc_url:
        raise SystemExit("--rpc-url or SECURE_RPC_URL is required")

    events: dict[str, list[dict]] = defaultdict(list)
    malformed = 0
    with args.audit.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            mint = event.get("mint")
            if mint:
                event["_line"] = line_number
                events[mint].append(event)

    shadow = {}
    for _, outcome in rows(args.shadow_outcomes):
        mint = outcome.get("mint")
        if mint:
            shadow[mint] = outcome

    attempts = []
    for mint, mint_events in events.items():
        buy = next(
            (event for event in mint_events if event.get("kind") == "rustSelectedCandidateBuyResult"),
            None,
        )
        if not buy or not buy.get("ok"):
            continue
        sell = next(
            (
                event
                for event in reversed(mint_events)
                if event.get("kind") == "rustExitSellFinality"
                and event.get("status") == "landed_ok"
                and event.get("error") is None
            ),
            None,
        )
        decision = next(
            (event for event in reversed(mint_events) if event.get("kind") == "rustExitDecision"),
            None,
        )
        buy_tx = rpc(
            args.rpc_url,
            "getTransaction",
            [buy["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        )
        buy_chain = wallet_delta(buy_tx, args.wallet)
        actual_tokens_received = token_delta(buy_tx, args.wallet, mint)
        sell_chain = None
        sell_signature = sell.get("signature") if sell else None
        recovery = mint == args.recovery_mint and args.recovery_signature
        if recovery:
            sell_signature = args.recovery_signature
        if sell_signature:
            sell_tx = rpc(
                args.rpc_url,
                "getTransaction",
                [sell_signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
            sell_chain = wallet_delta(sell_tx, args.wallet)

        sol_in = int(buy.get("solInLamports") or 0)
        gross_proceeds = None
        strategy_pnl = None
        live_roi_pct = None
        wallet_pnl = buy_chain["walletDeltaLamports"]
        if sell_chain:
            gross_proceeds = sell_chain["walletDeltaLamports"] + sell_chain["networkFeeLamports"]
            strategy_pnl = gross_proceeds - sol_in
            live_roi_pct = strategy_pnl / sol_in * 100 if sol_in else None
            wallet_pnl += sell_chain["walletDeltaLamports"]

        shadow_outcome = shadow.get(mint)
        frozen = shadow_outcome.get("p75_primary") if shadow_outcome else None
        shadow_pnl_lamports = None
        live_minus_shadow = None
        if frozen and frozen.get("pnl") is not None:
            shadow_pnl_lamports = round(sol_in * float(frozen["pnl"]) / 100)
            if strategy_pnl is not None:
                live_minus_shadow = strategy_pnl - shadow_pnl_lamports

        exact_events = [
            event
            for event in mint_events
            if event.get("kind")
            in {
                "rustSelectedCandidateStreamingExactBalanceActivation",
                "rustSelectedCandidateExactBalanceProof",
            }
            and int(event.get("amountRaw") or event.get("actualTokensOutRaw") or 0) > 0
        ]
        first_sellable_at_ms = min(
            (int(event["atMs"]) for event in exact_events if event.get("atMs") is not None),
            default=None,
        )
        expected_tokens = int(buy.get("preSignEconomicGuardExpectedTokensRaw") or 0)
        pre_sign_mc = buy.get("preSignEconomicGuardEntryMarketCapUsd")
        output_degradation_pct = None
        implied_live_mc = None
        if expected_tokens and actual_tokens_received:
            output_degradation_pct = (expected_tokens - actual_tokens_received) / expected_tokens * 100
            if pre_sign_mc is not None:
                implied_live_mc = float(pre_sign_mc) * expected_tokens / actual_tokens_received
        shadow_entry_mc = frozen.get("entry_mc") if frozen else None
        live_vs_shadow_entry_mc_pct = None
        if implied_live_mc is not None and shadow_entry_mc:
            live_vs_shadow_entry_mc_pct = (implied_live_mc / float(shadow_entry_mc) - 1) * 100
        broadcast = next(
            (event for event in reversed(mint_events) if event.get("kind") == "rustExitSellBroadcast"),
            None,
        )
        sell_finality = next(
            (event for event in reversed(mint_events) if event.get("kind") == "rustExitSellFinality"),
            None,
        )
        attempts.append(
            {
                "mint": mint,
                "buyAtMs": buy.get("atMs"),
                "selectedAtMs": buy.get("candidateSelectedAtMs"),
                "buySignature": buy.get("signature"),
                "liveEntryLamports": sol_in,
                "buyWalletDeltaLamports": buy_chain["walletDeltaLamports"],
                "buyNetworkFeeLamports": buy_chain["networkFeeLamports"],
                "ataRentAndOtherEntryEffectsLamports": -buy_chain["walletDeltaLamports"]
                - sol_in
                - buy_chain["networkFeeLamports"],
                "expectedTokensAtPreSignGuardRaw": expected_tokens,
                "actualTokensReceivedRaw": actual_tokens_received,
                "entryOutputDegradationPct": output_degradation_pct,
                "preSignGuardMcUsd": pre_sign_mc,
                "impliedLiveEntryMcUsd": implied_live_mc,
                "shadowEntryMcUsd": shadow_entry_mc,
                "liveVsShadowEntryMcPct": live_vs_shadow_entry_mc_pct,
                "selectedToFirstSendMs": buy.get("selectedToFirstSendMs"),
                "selectedToBuyResultMs": buy.get("selectedToBuyResultMs"),
                "firstSellableAtMs": first_sellable_at_ms,
                "selectedToFirstSellableMs": first_sellable_at_ms
                - int(buy.get("candidateSelectedAtMs") or 0)
                if first_sellable_at_ms and buy.get("candidateSelectedAtMs")
                else None,
                "sellSignature": sell_signature,
                "exitDecisionAtMs": decision.get("exitDecisionAtMs") if decision else None,
                "selectedToExitDecisionMs": int(decision["exitDecisionAtMs"])
                - int(buy.get("candidateSelectedAtMs") or 0)
                if decision and decision.get("exitDecisionAtMs") and buy.get("candidateSelectedAtMs")
                else None,
                "sellFirstSendAtMs": broadcast.get("sellFirstSendAtMs") if broadcast else None,
                "sellFirstSendToFinalityMs": sell_finality.get("firstSendToFinalizedMs")
                if sell_finality
                else None,
                "liveGrossExitLamports": gross_proceeds,
                "sellNetworkFeeLamports": sell_chain["networkFeeLamports"] if sell_chain else None,
                "liveRoiPct": live_roi_pct,
                "strategyEconomicPnlLamports": strategy_pnl,
                "walletPnlLamports": wallet_pnl if sell_chain else None,
                "exitReason": "incident-recovery" if recovery else decision.get("reason") if decision else None,
                "stateAnomaly": recovery,
                "shadowOutcome": frozen.get("outcome") if frozen else None,
                "shadowReturnPct": frozen.get("pnl") if frozen else None,
                "shadowPnlLamports": shadow_pnl_lamports,
                "liveMinusShadowLamports": live_minus_shadow,
            }
        )

    attempts.sort(key=lambda item: (item["buyAtMs"] or 0, item["mint"]))
    complete = [item for item in attempts if item["strategyEconomicPnlLamports"] is not None]
    ordinary = [item for item in complete if not item["stateAnomaly"]]
    output = {
        "schema": "ranker-v2-n25-v2-immutable-economics-v1",
        "source": str(args.audit),
        "malformedAuditRows": malformed,
        "attemptCount": len(attempts),
        "completedCount": len(complete),
        "attempts": attempts,
        "summary": {
            "ordinaryCompleted": len(ordinary),
            "ordinaryStrategyPnlLamports": sum(item["strategyEconomicPnlLamports"] for item in ordinary),
            "ordinaryWalletPnlLamports": sum(item["walletPnlLamports"] for item in ordinary),
            "ordinaryShadowPnlLamports": sum(item["shadowPnlLamports"] or 0 for item in ordinary),
            "ordinaryLiveMinusShadowLamports": sum(item["liveMinusShadowLamports"] or 0 for item in ordinary),
            "allStrategyPnlLamports": sum(item["strategyEconomicPnlLamports"] for item in complete),
            "allWalletPnlLamports": sum(item["walletPnlLamports"] for item in complete),
            "allNetworkFeesLamports": sum(
                item["buyNetworkFeeLamports"] + (item["sellNetworkFeeLamports"] or 0)
                for item in complete
            ),
            "allAtaRentAndOtherEntryEffectsLamports": sum(
                item["ataRentAndOtherEntryEffectsLamports"] for item in complete
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["summary"], indent=2))


if __name__ == "__main__":
    main()
