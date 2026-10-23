#!/usr/bin/env python3
"""Outcome-first follow-through atlas over a frozen exact-admitted discovery prefix."""

from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pathlib
import statistics
from typing import Any

from buildPumpExactAlphaResearchV1 import (
    NETWORK_FEE,
    TRADE_KINDS,
    apply_group,
    pair_events,
    paths_for_report,
    retain_pump_trade_arrival,
    retain_target_arrival,
    state_before,
)
from exactCurveEconomicsV1 import quote_exact_quote_in_buy, quote_exact_token_sell
from reconcilePumpExactEvidenceLedgerV1 import iter_frames
from validatePumpExactEvidenceLedgerV1 import event_fees


SCHEMA = "OUTCOME_FIRST_FOLLOW_THROUGH_ATLAS_V1"
CHECKPOINTS = (100, 250, 500, 750, 1000, 1500, 2000, 3000, 5000)
WINDOWS = ((0, 100), (100, 250), (250, 500), (500, 750), (750, 1000), (1000, 2000), (2000, 5000))
SIZES = (0.5, 1.0)
ENTRY_DELAY_MS = 25
FIRST_SELLABLE_DELAY_MS = 500
MAX_HOLD_MS = 120_000
SOL_QUOTES = {"11111111111111111111111111111111", "So11111111111111111111111111111111111111112"}
TWO_CQ = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"


def div(a: float, b: float) -> float:
    return a / b if b else 0.0


def q(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lo, hi = int(position), math.ceil(position)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def stats(values: list[float]) -> dict[str, float | None]:
    return {
        "n": len(values),
        "median": statistics.median(values) if values else None,
        "p25": q(values, 0.25),
        "p75": q(values, 0.75),
        "mean": statistics.mean(values) if values else None,
    }


def metric(rows: list[dict[str, Any]], size: float) -> dict[str, Any]:
    cells = [row["economics"].get(str(size)) for row in rows]
    cells = [cell for cell in cells if cell and cell.get("status") == "EXACT"]
    returns = [float(cell["returnPct"]) / 100.0 for cell in cells]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    return {
        "N": len(cells),
        "profitableFrequency": div(len(wins), len(cells)),
        "WR": div(len(wins), len(cells)),
        "PF": div(sum(wins), abs(sum(losses))) if losses else None,
        "expectancy": statistics.mean(returns) if returns else None,
        "pnlSol": sum(float(cell["pnlSol"]) for cell in cells),
        "TP": sum(cell["outcome"] == "TP" for cell in cells),
        "SL": sum(cell["outcome"] == "SL" for cell in cells),
        "MAX_HOLD": sum(cell["outcome"] == "MAX_HOLD" for cell in cells),
    }


def load_prefix(research_root: pathlib.Path, limit: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    index = [json.loads(line) for line in (research_root / "discovery-mint-index.jsonl").read_text().splitlines() if line.strip()]
    prefix = index[:limit]
    if len(prefix) != limit:
        raise RuntimeError(f"PREFIX_NOT_AVAILABLE:{len(prefix)}/{limit}")
    return prefix, {row["mint"]: int(row["ordinal"]) for row in prefix}


def load_table_controls(research_root: pathlib.Path, selected: set[str]) -> dict[tuple[str, int], dict[str, float]]:
    controls: dict[tuple[str, int], dict[str, float]] = {}
    wanted = {100, 250, 500, 1000, 2000, 5000}
    for path in sorted((research_root / "rows-v2").glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                mint = row.get("mint")
                age = int((row.get("features") or {}).get("mintAgeMs") or -1)
                if mint not in selected or age not in wanted or row.get("observationState") != f"FIXED_{age}MS":
                    continue
                feature = row["features"]
                controls[(mint, age)] = {
                    "globalTrades1sPrior": float(feature.get("globalTrades1sPrior") or 0),
                    "globalTrades5sPrior": float(feature.get("globalTrades5sPrior") or 0),
                    "globalTrades60sPrior": float(feature.get("globalTrades60sPrior") or 0),
                    "globalLaunches60sPrior": float(feature.get("globalLaunches60sPrior") or 0),
                }
    return controls


def causal_controls(controls: dict[tuple[str, int], dict[str, float]], mint: str, checkpoint: int) -> dict[str, float]:
    ages = [age for age in (100, 250, 500, 1000, 2000, 5000) if age <= checkpoint and (mint, age) in controls]
    return controls[(mint, max(ages))] if ages else {}


def collect_partition(evidence_root: pathlib.Path, report: dict[str, Any], targets: set[str]) -> tuple[dict[str, list[Any]], dict[str, int]]:
    raw_arrival: dict[str, int] = {}
    raw_evidence: dict[str, str] = {}
    by_mint: dict[str, list[Any]] = collections.defaultdict(list)
    counters = collections.Counter()
    for path in paths_for_report(evidence_root, report):
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            kind = metadata.get("kind")
            signature = metadata.get("signature")
            if kind == "transaction" and signature:
                observed_ms = int(metadata.get("receivedAtUnixNs") or 0) // 1_000_000
                evidence_id = str(metadata.get("evidenceId") or "")
                kinds = set(metadata.get("pumpInstructionKinds") or [])
                retain_pump_trade_arrival(raw_arrival, raw_evidence, signature, observed_ms, evidence_id, kinds)
                retain_target_arrival(raw_arrival, raw_evidence, signature, observed_ms, evidence_id, targets.intersection(metadata.get("discoveredMints") or []))
                if metadata.get("successful") is False and targets.intersection(metadata.get("discoveredMints") or []):
                    counters["observedFailedTransactions"] += 1
            elif kind == "rpc_finalized_transaction" and signature and targets.intersection(metadata.get("discoveredMints") or []):
                arrival = raw_arrival.get(signature)
                if arrival is None:
                    counters["missingRawArrival"] += 1
                    continue
                try:
                    transaction = json.loads(raw).get("result")
                    if not transaction or (transaction.get("meta") or {}).get("err") is not None:
                        counters["failedOrMissingFinalizedTransactions"] += 1
                        continue
                    decoded = pair_events(
                        transaction, signature, raw_evidence.get(signature, ""),
                        str(metadata.get("evidenceId") or ""), arrival,
                        int(metadata.get("slot") or 0), int(metadata.get("transactionIndex") or 0), targets,
                    )
                except Exception:
                    counters["decodeFailures"] += 1
                    continue
                by_mint.update()  # Keep defaultdict materialized under static checkers.
                for event in decoded:
                    by_mint[event.event.mint].append(event)
    for mint in list(by_mint):
        deduped = {(event.signature, event.instruction_index): event for event in by_mint[mint]}
        by_mint[mint] = sorted(deduped.values(), key=lambda row: (row.arrival_ms, row.slot, row.tx_index, row.instruction_index))
    return by_mint, dict(counters)


def transition_features(events: list[Any], create_ms: int, checkpoint: int) -> dict[str, float | int | bool | None]:
    visible = [row for row in events if row.arrival_ms <= create_ms + checkpoint]
    buys = [row for row in visible if row.event.is_buy]
    sells = [row for row in visible if not row.event.is_buy]
    first_buyer = buys[0].event.user if buys else None
    initial_sol = sum(row.event.immediate_quote_amount for row in buys if row.event.user == first_buyer) / 1e9
    output: dict[str, float | int | bool | None] = {
        "buyCount": len(buys), "sellCount": len(sells),
        "grossBuySol": sum(row.event.immediate_quote_amount for row in buys) / 1e9,
        "grossSellSol": sum(row.event.immediate_quote_amount for row in sells) / 1e9,
        "uniqueBuyers": len({row.event.user for row in buys}),
        "firstSellerLatencyMs": min((row.arrival_ms - create_ms for row in sells), default=None),
        "twoCqBuySeen": any(row.event.is_buy and row.event.user == TWO_CQ for row in visible),
        "twoCqFirstEntryAgeMs": min((row.arrival_ms - create_ms for row in buys if row.event.user == TWO_CQ), default=None),
    }
    follower_buys = [row for row in buys if row.event.user != first_buyer]
    follower_wallets = collections.Counter()
    for row in follower_buys:
        follower_wallets[row.event.user] += row.event.immediate_quote_amount / 1e9
    follower_total = sum(follower_wallets.values())
    shares = [value / follower_total for value in follower_wallets.values()] if follower_total else []
    output.update({
        "followerSolCumulative": follower_total,
        "followerWalletsCumulative": len(follower_wallets),
        "followerToInitialSolRatio": div(follower_total, initial_sol),
        "followerHhi": sum(value * value for value in shares),
        "followerEntropy": -sum(value * math.log(value) for value in shares if value > 0),
        "capitalPerFollower": div(follower_total, len(follower_wallets)),
        "repeatFollowerRatio": 1.0 - div(len(follower_wallets), len(follower_buys)),
    })
    unique_order: list[str] = []
    for row in buys:
        if row.event.user not in unique_order:
            unique_order.append(row.event.user)
    for rank in (2, 3, 5):
        wallet = unique_order[rank - 1] if len(unique_order) >= rank else None
        output[f"timeToBuyer{rank}Ms"] = min((row.arrival_ms - create_ms for row in buys if row.event.user == wallet), default=None) if wallet else None

    window_net: list[float] = []
    window_follower_velocity: list[float] = []
    window_unique_velocity: list[float] = []
    seen_followers: set[str] = set()
    for start, end in WINDOWS:
        if end > checkpoint:
            continue
        rows = [row for row in visible if create_ms + start < row.arrival_ms <= create_ms + end]
        wb = [row for row in rows if row.event.is_buy]
        ws = [row for row in rows if not row.event.is_buy]
        followers = [row for row in wb if row.event.user != first_buyer]
        follower_sol = sum(row.event.immediate_quote_amount for row in followers) / 1e9
        sell_sol = sum(row.event.immediate_quote_amount for row in ws) / 1e9
        new_wallets = {row.event.user for row in followers} - seen_followers
        seen_followers.update(row.event.user for row in followers)
        duration_s = (end - start) / 1000
        prefix = f"w{start}_{end}"
        output.update({
            f"{prefix}_followerSol": follower_sol,
            f"{prefix}_newFollowerWallets": len(new_wallets),
            f"{prefix}_followerVelocity": div(follower_sol, duration_s),
            f"{prefix}_uniqueVelocity": div(len(new_wallets), duration_s),
            f"{prefix}_sellSol": sell_sol,
            f"{prefix}_netSol": follower_sol - sell_sol,
        })
        window_net.append(follower_sol - sell_sol)
        window_follower_velocity.append(div(follower_sol, duration_s))
        window_unique_velocity.append(div(len(new_wallets), duration_s))
    output["positiveFlowWindows"] = sum(value > 0 for value in window_net)
    output["consecutivePositiveFlowWindows"] = max((sum(1 for _ in group) for positive, group in __import__("itertools").groupby(window_net, key=lambda value: value > 0) if positive), default=0)
    output["followerVelocityAcceleration"] = window_follower_velocity[-1] - window_follower_velocity[-2] if len(window_follower_velocity) >= 2 else 0.0
    output["uniqueVelocityAcceleration"] = window_unique_velocity[-1] - window_unique_velocity[-2] if len(window_unique_velocity) >= 2 else 0.0
    output["reacceleratedAfterSlowdown"] = any(a > b and c > a for b, a, c in zip(window_follower_velocity, window_follower_velocity[1:], window_follower_velocity[2:]))

    if sells:
        first_sell = sells[0]
        before = [row.event.post_state.virtual_sol for row in visible if row.arrival_ms <= first_sell.arrival_ms]
        prior_high = max(before) if before else first_sell.event.pre_state.virtual_sol
        after = [row for row in visible if row.arrival_ms > first_sell.arrival_ms]
        recovery = next((row for row in after if row.event.post_state.virtual_sol >= prior_high), None)
        post_sell_buys = [row for row in after if row.event.is_buy]
        pre_sell_wallets = {row.event.user for row in buys if row.arrival_ms <= first_sell.arrival_ms}
        output.update({
            "sellAbsorptionRecovered": recovery is not None,
            "sellRecoveryLatencyMs": recovery.arrival_ms - first_sell.arrival_ms if recovery else None,
            "newWalletsAfterFirstSell": len({row.event.user for row in post_sell_buys} - pre_sell_wallets),
            "postSellBuyVelocitySolPerSec": div(sum(row.event.immediate_quote_amount for row in post_sell_buys) / 1e9, max((create_ms + checkpoint - first_sell.arrival_ms) / 1000, 0)),
            "newHighAfterSell": any(row.event.post_state.virtual_sol > prior_high for row in after),
            "netFlowDrawdownSol": max(0.0, (prior_high - min((row.event.post_state.virtual_sol for row in after), default=prior_high)) / 1e9),
        })
    else:
        output.update({"sellAbsorptionRecovered": False, "sellRecoveryLatencyMs": None, "newWalletsAfterFirstSell": 0, "postSellBuyVelocitySolPerSec": 0.0, "newHighAfterSell": False, "netFlowDrawdownSol": 0.0})
    return output


def replay_economics(events: list[Any], decision_ms: int, size: float) -> dict[str, Any]:
    entry_ms = decision_ms + ENTRY_DELAY_MS
    base = state_before(events, entry_ms)
    if base is None:
        return {"status": "ENTRY_UNRESOLVED"}
    state, fee_event, evidence_id = base
    gross = int(round(size * 1e9))
    try:
        buy = quote_exact_quote_in_buy(state, gross, event_fees(fee_event))
    except Exception as error:
        return {"status": "ENTRY_UNSUPPORTED", "reason": str(error)}
    inventory = buy.token_out
    cost = gross + NETWORK_FEE
    state = buy.post_state
    first_sellable = decision_ms + FIRST_SELLABLE_DELAY_MS
    max_hold = decision_ms + MAX_HOLD_MS
    grouped: dict[tuple[Any, ...], list[Any]] = collections.defaultdict(list)
    for row in events:
        if entry_ms < row.arrival_ms <= max_hold:
            grouped[(row.arrival_ms, row.slot, row.tx_index, row.signature)].append(row)
    timeline: list[tuple[int, Any, Any, str]] = [(entry_ms, state, fee_event, evidence_id)]
    counterfactual_failed = 0
    for key, group in sorted(grouped.items()):
        try:
            state = apply_group(state, sorted(group, key=lambda row: row.instruction_index))
            fee_event = group[-1].event
            evidence_id = group[-1].canonical_evidence_id
            timeline.append((int(key[0]), state, fee_event, evidence_id))
        except Exception:
            counterfactual_failed += 1  # Atomic zero-state mutation for this failed counterfactual tx.

    def roi_at(at_ms: int) -> tuple[float, Any]:
        point = max((item for item in timeline if item[0] <= at_ms), key=lambda item: item[0])
        sell = quote_exact_token_sell(point[1], inventory, 1, event_fees(point[2]), cashback_bps=point[2].cashback_fee_basis_points, buyback_share_bps=point[2].buyback_fee_basis_points)
        proceeds = sell.immediate_quote_out - NETWORK_FEE
        return (proceeds - cost) * 100 / cost, sell

    sample_times = sorted({first_sellable, max_hold, *(decision_ms + offset for offset in (1000, 2000, 5000, 15000, 120000)), *(item[0] for item in timeline if item[0] >= first_sellable)})
    samples: list[tuple[int, float, Any]] = []
    for at_ms in sample_times:
        try:
            roi, sell = roi_at(at_ms)
            samples.append((at_ms, roi, sell))
        except Exception:
            pass
    if not samples:
        return {"status": "EXIT_UNRESOLVED"}
    outcome = "MAX_HOLD"
    exit_sample = samples[-1]
    for sample in samples:
        if sample[0] < first_sellable:
            continue
        if sample[1] >= 20 or sample[1] <= -20:
            outcome = "TP" if sample[1] >= 20 else "SL"
            exit_sample = sample
            break
    thresholds: dict[str, int | None] = {}
    for level in (5, 10, 20, -10, -20):
        hit = next((at_ms - decision_ms for at_ms, roi, _ in samples if (roi >= level if level > 0 else roi <= level)), None)
        thresholds[f"timeTo{level:+d}PctMs"] = hit
    profitable_duration = 0
    longest_profitable = 0
    current_run = 0
    eligible = [(at_ms, roi) for at_ms, roi, _ in samples if at_ms >= first_sellable]
    for (left_ms, left_roi), (right_ms, _) in zip(eligible, eligible[1:]):
        duration = max(0, right_ms - left_ms)
        if left_roi > 0:
            profitable_duration += duration
            current_run += duration
            longest_profitable = max(longest_profitable, current_run)
        else:
            current_run = 0
    return {
        "status": "EXACT", "sizeSol": size, "entryMs": entry_ms,
        "entryStateEvidenceId": base[2], "tokensReceivedRaw": inventory,
        "entryVirtualSol": base[0].virtual_sol, "entryVirtualToken": base[0].virtual_token,
        "ownBuyPostVirtualSol": buy.post_state.virtual_sol, "ownBuyPostVirtualToken": buy.post_state.virtual_token,
        "ownCurveImpactPct": (buy.post_state.virtual_sol / base[0].virtual_sol - 1) * 100,
        "inventoryToRealReservePct": div(inventory * 100, base[0].real_token),
        "firstSellableReturnPct": next((roi for at_ms, roi, _ in samples if at_ms >= first_sellable), None),
        "MFE": max(roi for at_ms, roi, _ in samples if at_ms >= first_sellable),
        "MAE": min(roi for at_ms, roi, _ in samples if at_ms >= first_sellable),
        **thresholds,
        "roiAtDecisionOffsets": {str(offset): next((roi for at_ms, roi, _ in samples if at_ms >= decision_ms + offset), None) for offset in (1000, 2000, 5000, 15000, 120000)},
        "profitableExitDurationMs": profitable_duration,
        "longestProfitableExitDurationMs": longest_profitable,
        "outcome": outcome, "exitMs": exit_sample[0], "returnPct": exit_sample[1],
        "pnlSol": (exit_sample[2].immediate_quote_out - NETWORK_FEE - cost) / 1e9,
        "counterfactualFailedTransactions": counterfactual_failed,
    }


def build_rows_for_partition(report: dict[str, Any], events_by_mint: dict[str, list[Any]], ordinals: dict[str, int], controls: dict[tuple[str, int], dict[str, float]]) -> list[dict[str, Any]]:
    create = {row["mint"]: int(row.get("createReceivedAtUnixNs") or 0) // 1_000_000 for row in report.get("mints", []) if row.get("mint") in ordinals and row.get("exactAdmitted")}
    rows: list[dict[str, Any]] = []
    for mint, create_ms in sorted(create.items(), key=lambda item: ordinals[item[0]]):
        events = [row for row in events_by_mint.get(mint, []) if row.event.quote_mint in SOL_QUOTES and not row.event.mayhem_mode and row.arrival_ms <= create_ms + MAX_HOLD_MS + max(CHECKPOINTS)]
        if not events:
            continue
        for checkpoint in CHECKPOINTS:
            decision_ms = create_ms + checkpoint
            features = transition_features(events, create_ms, checkpoint)
            features.update(causal_controls(controls, mint, checkpoint))
            base = state_before(events, decision_ms + ENTRY_DELAY_MS)
            if base:
                features.update({
                    "curveVirtualSol": base[0].virtual_sol / 1e9,
                    "curveVirtualToken": float(base[0].virtual_token),
                    "curveRealSol": base[0].real_sol / 1e9,
                    "curveRealToken": float(base[0].real_token),
                })
            rows.append({
                "schema": SCHEMA, "mint": mint, "ordinal": ordinals[mint],
                "createUnixMs": create_ms, "decisionUnixMs": decision_ms,
                "checkpointMs": checkpoint, "features": features,
                "economics": {str(size): replay_economics(events, decision_ms, size) for size in SIZES},
            })
    return rows


def write_partition(path: pathlib.Path, rows: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"header": metadata}, sort_keys=True, separators=(",", ":")) + "\n")
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def load_atlas(output: pathlib.Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((output / "partitions").glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            next(handle)
            rows.extend(json.loads(line) for line in handle if line.strip())
    return rows


def standardized_effect(winners: list[float], losers: list[float]) -> float | None:
    if len(winners) < 2 or len(losers) < 2:
        return None
    pooled = math.sqrt((statistics.pvariance(winners) + statistics.pvariance(losers)) / 2)
    return div(statistics.mean(winners) - statistics.mean(losers), pooled)


def overlap_coefficient(winners: list[float], losers: list[float]) -> float | None:
    if not winners or not losers:
        return None
    combined = winners + losers
    lo, hi = min(combined), max(combined)
    if hi == lo:
        return 1.0
    bins = 20
    wh = [0] * bins
    lh = [0] * bins
    for value in winners:
        wh[min(bins - 1, int((value - lo) / (hi - lo) * bins))] += 1
    for value in losers:
        lh[min(bins - 1, int((value - lo) / (hi - lo) * bins))] += 1
    return sum(min(a / len(winners), b / len(losers)) for a, b in zip(wh, lh))


def matched_effects(rows: list[dict[str, Any]], checkpoint: int) -> dict[str, Any]:
    pool = [row for row in rows if row["checkpointMs"] == checkpoint and row["economics"]["0.5"].get("status") == "EXACT"]
    winners = [row for row in pool if row["economics"]["0.5"]["outcome"] == "TP"]
    losers = [row for row in pool if float(row["economics"]["0.5"]["returnPct"]) <= 0]
    controls = ("curveVirtualSol", "curveVirtualToken", "grossBuySol", "buyCount", "curveRealToken", "globalTrades60sPrior", "globalLaunches60sPrior")
    scales = {}
    for key in controls:
        values = [math.log1p(max(float(row["features"].get(key) or 0), 0)) for row in pool]
        scales[key] = (statistics.mean(values), statistics.pstdev(values) or 1.0)
    max_ordinal = max((row["ordinal"] for row in pool), default=1)
    def chronological_block(row: dict[str, Any]) -> int:
        return min(3, (row["ordinal"] - 1) * 4 // max(max_ordinal, 1))
    loser_index: dict[int, list[tuple[float, dict[str, Any]]]] = collections.defaultdict(list)
    for loser in losers:
        primary = (math.log1p(max(float(loser["features"].get("curveVirtualSol") or 0), 0)) - scales["curveVirtualSol"][0]) / scales["curveVirtualSol"][1]
        loser_index[chronological_block(loser)].append((primary, loser))
    for block in loser_index:
        loser_index[block].sort(key=lambda item: item[0])
    pairs = []
    for winner in winners:
        block = chronological_block(winner)
        index = loser_index.get(block, [])
        if not index:
            continue
        primary = (math.log1p(max(float(winner["features"].get("curveVirtualSol") or 0), 0)) - scales["curveVirtualSol"][0]) / scales["curveVirtualSol"][1]
        position = bisect.bisect_left([item[0] for item in index], primary)
        candidates = [item[1] for item in index[max(0, position - 128):position + 129]]
        def distance(row: dict[str, Any]) -> float:
            return sum(((math.log1p(max(float(winner["features"].get(key) or 0), 0)) - math.log1p(max(float(row["features"].get(key) or 0), 0))) / scales[key][1]) ** 2 for key in controls)
        pairs.append((winner, min(candidates, key=distance)))
    excluded = set(controls) | {"twoCqBuySeen", "twoCqFirstEntryAgeMs"}
    feature_names = sorted({key for winner, loser in pairs for key, value in winner["features"].items() if key not in excluded and isinstance(value, (int, float)) and isinstance(loser["features"].get(key), (int, float))})
    effects = []
    for key in feature_names:
        valid_pairs = [
            (a, b) for a, b in pairs
            if isinstance(a["features"].get(key), (int, float))
            and isinstance(b["features"].get(key), (int, float))
            and math.isfinite(float(a["features"][key]))
            and math.isfinite(float(b["features"][key]))
        ]
        if len(valid_pairs) < 2:
            continue
        w = [float(a["features"][key]) for a, b in valid_pairs]
        l = [float(b["features"][key]) for a, b in valid_pairs]
        effect = standardized_effect(w, l)
        blocks = []
        for block in range(4):
            subset = [(a, b) for a, b in valid_pairs if chronological_block(a) == block]
            blocks.append(statistics.mean(float(a["features"][key]) - float(b["features"][key]) for a, b in subset) if subset else None)
        nonzero = [value for value in blocks if value is not None and value != 0]
        effects.append({
            "feature": key, "winner": stats(w), "loser": stats(l), "standardizedEffect": effect,
            "overlap": overlap_coefficient(w, l), "chronologicalBlockMeanDifferences": blocks,
            "consistentBlocks": max(sum(value > 0 for value in nonzero), sum(value < 0 for value in nonzero)),
        })
    effects.sort(key=lambda item: (item["consistentBlocks"], abs(item["standardizedEffect"] or 0)), reverse=True)
    return {"checkpointMs": checkpoint, "poolN": len(pool), "winnerN": len(winners), "loserN": len(losers), "matchedPairs": len(pairs), "controls": controls, "featureEffects": effects}


def classify_archetype(cell: dict[str, Any], features: dict[str, Any]) -> str:
    if cell.get("outcome") != "TP":
        return "OTHER"
    if (cell.get("MAE") or 0) <= -5 or features.get("sellAbsorptionRecovered"):
        return "RECOVERY_ABSORPTION"
    if (cell.get("longestProfitableExitDurationMs") or 0) < 500:
        return "TRANSIENT_SPIKE"
    if (cell.get("timeTo+20PctMs") or 0) > 5000:
        return "SLOW_BUILD"
    if (cell.get("MAE") or 0) > -5:
        return "CONTINUATION"
    return "OTHER"


def summarize(rows: list[dict[str, Any]], boundary: int) -> dict[str, Any]:
    by_checkpoint = {checkpoint: [row for row in rows if row["checkpointMs"] == checkpoint] for checkpoint in CHECKPOINTS}
    earliest_inventory = {row["mint"]: row["economics"]["0.5"].get("tokensReceivedRaw") for row in by_checkpoint[100] if row["economics"]["0.5"].get("status") == "EXACT"}
    tradeoff = []
    for checkpoint in CHECKPOINTS:
        group = by_checkpoint[checkpoint]
        degradations = []
        for row in group:
            current = row["economics"]["0.5"].get("tokensReceivedRaw")
            earliest = earliest_inventory.get(row["mint"])
            if current and earliest:
                degradations.append((1 - current / earliest) * 100)
        tradeoff.append({
            "checkpointMs": checkpoint,
            "eligibleMintN": len({row["mint"] for row in group}),
            "capacityPassRate0p5": div(metric(group, 0.5)["N"], len(group)),
            "medianEntryInventoryDeteriorationPctVs100ms": statistics.median(degradations) if degradations else None,
            "p75EntryInventoryDeteriorationPctVs100ms": q(degradations, 0.75),
            "p90EntryInventoryDeteriorationPctVs100ms": q(degradations, 0.90),
            "p95EntryInventoryDeteriorationPctVs100ms": q(degradations, 0.95),
            "shareWithEntryDeterioration": div(sum(value > 0 for value in degradations), len(degradations)),
            "size0p5": metric(group, 0.5), "size1p0": metric(group, 1.0),
        })
    matching = [matched_effects(rows, checkpoint) for checkpoint in CHECKPOINTS]
    stable = []
    for match_report in matching:
        for effect in match_report["featureEffects"]:
            if effect["consistentBlocks"] >= 3 and abs(effect["standardizedEffect"] or 0) >= 0.2:
                stable.append({"checkpointMs": match_report["checkpointMs"], **effect})
    stable.sort(key=lambda item: (item["consistentBlocks"], abs(item["standardizedEffect"] or 0)), reverse=True)
    archetypes = collections.Counter()
    archetype_rows: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    focus_checkpoint = max(matching, key=lambda item: sum(1 for effect in item["featureEffects"] if effect["consistentBlocks"] >= 3 and abs(effect["standardizedEffect"] or 0) >= 0.2))["checkpointMs"]
    for row in by_checkpoint[focus_checkpoint]:
        cell = row["economics"]["0.5"]
        kind = classify_archetype(cell, row["features"])
        archetypes[kind] += 1
        archetype_rows[kind].append(row)
    two_cq_rows = [row for row in rows if row["features"].get("twoCqBuySeen")]
    coherent = [item for item in stable if item["consistentBlocks"] == 4 and abs(item["standardizedEffect"] or 0) >= 0.5 and (item["overlap"] or 1) <= 0.65]
    classification = "FOLLOW_THROUGH_STATES_SEPARATE_WINNERS_FROM_LOSERS" if len(coherent) >= 5 else ("SOME_FOLLOW_THROUGH_SIGNAL_BUT_WEAK" if stable else "NO_USEFUL_FOLLOW_THROUGH_SEPARATION_FOUND")
    two_cq_focus = [row for row in by_checkpoint[focus_checkpoint] if row["features"].get("twoCqBuySeen") and row["economics"]["0.5"].get("status") == "EXACT"]
    two_cq_archetypes = collections.Counter(classify_archetype(row["economics"]["0.5"], row["features"]) for row in two_cq_focus)
    return {
        "schema": SCHEMA, "classification": classification,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "boundaryOrdinal": boundary, "futureUntouchedStartsAtOrdinal": boundary + 1,
        "population": {"atlasRows": len(rows), "uniqueMints": len({row["mint"] for row in rows}), "rowsByCheckpoint": {str(key): len(value) for key, value in by_checkpoint.items()}},
        "informationPriceTradeoff": tradeoff,
        "matchedWinnerLoser": matching,
        "topStableTransitionEffects": stable[:20],
        "focusCheckpointMs": focus_checkpoint,
        "moveArchetypes": {key: {"N": len(value), "share": div(len(value), sum(archetypes.values())), "size0p5": metric(value, 0.5), "size1p0": metric(value, 1.0)} for key, value in archetype_rows.items()},
        "twoCqOverlay": {"authoritativeWallet": TWO_CQ, "atlasRowsWithEntry": len(two_cq_rows), "uniqueMints": len({row["mint"] for row in two_cq_rows}), "status": "OVERLAY_AVAILABLE" if two_cq_rows else "NO_2CQ_ENTRIES_IN_FROZEN_WINDOW", "focusCheckpointMs": focus_checkpoint, "focusEconomics0p5": metric(two_cq_focus, 0.5), "focusArchetypes": dict(two_cq_archetypes), "medianObservedEntryAgeMs": statistics.median([row["features"]["twoCqFirstEntryAgeMs"] for row in two_cq_focus]) if two_cq_focus else None},
        "integrity": {"validationRowsInspected": 0, "holdoutRowsInspected": 0, "officialV1Modified": False, "liveSubmissions": 0, "rankingModelTrained": False},
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report['classification']}", "", f"Frozen discovery prefix: 1-{report['boundaryOrdinal']}; sandbox only.", "", "## Information / Price", "", "| Age | N | Entry deterioration | 0.5 PF | 0.5 Exp | 1.0 PF | 1.0 Exp |", "|---:|---:|---:|---:|---:|---:|---:|"]
    for row in report["informationPriceTradeoff"]:
        half, one = row["size0p5"], row["size1p0"]
        lines.append(f"| {row['checkpointMs']}ms | {row['eligibleMintN']} | {(row['medianEntryInventoryDeteriorationPctVs100ms'] or 0):.2f}% | {(half['PF'] or 0):.2f} | {(half['expectancy'] or 0):.4f} | {(one['PF'] or 0):.2f} | {(one['expectancy'] or 0):.4f} |")
    lines += ["", "## Stable Matched Effects", "", "| Age | Feature | Effect | Overlap | Stable blocks | Winner median | Loser median |", "|---:|---|---:|---:|---:|---:|---:|"]
    for row in report["topStableTransitionEffects"][:10]:
        lines.append(f"| {row['checkpointMs']}ms | {row['feature']} | {(row['standardizedEffect'] or 0):.3f} | {(row['overlap'] or 0):.3f} | {row['consistentBlocks']}/4 | {(row['winner']['median'] or 0):.4g} | {(row['loser']['median'] or 0):.4g} |")
    lines += ["", "## Move Archetypes", "", "| Type | N | Share | 0.5 PF | 0.5 Exp | 1.0 PF |", "|---|---:|---:|---:|---:|---:|"]
    for name, row in sorted(report["moveArchetypes"].items()):
        lines.append(f"| {name} | {row['N']} | {row['share']:.1%} | {(row['size0p5']['PF'] or 0):.2f} | {(row['size0p5']['expectancy'] or 0):.4f} | {(row['size1p0']['PF'] or 0):.2f} |")
    lines += ["", "## Integrity", "", f"- Validation rows inspected: {report['integrity']['validationRowsInspected']}", f"- Holdout rows inspected: {report['integrity']['holdoutRowsInspected']}", f"- Official V1 modified: {report['integrity']['officialV1Modified']}", f"- Ranking model trained: {report['integrity']['rankingModelTrained']}", f"- 2CQ overlay: {report['twoCqOverlay']['status']}"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=pathlib.Path)
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--max-ordinal", type=int, required=True)
    args = parser.parse_args()
    prefix, ordinals = load_prefix(args.research_root, args.max_ordinal)
    selected = set(ordinals)
    controls = load_table_controls(args.research_root, selected)
    reports = []
    for path in sorted((args.evidence_root / "readiness-v1" / "partitions").glob("*-v4.json")):
        report = json.loads(path.read_text())
        targets = {row["mint"] for row in report.get("mints", []) if row.get("exactAdmitted") and row["mint"] in selected}
        if targets:
            reports.append((report, targets))
    manifests = []
    for report, targets in reports:
        key = report["partitionKey"].replace("/", "-")
        destination = args.output / "partitions" / f"{key}.jsonl.gz"
        source_sha = str(report.get("sourceReconciliationManifestSha256") or "")
        if destination.exists():
            try:
                with gzip.open(destination, "rt", encoding="utf-8") as handle:
                    header = json.loads(next(handle))["header"]
                if header.get("sourceSha256") == source_sha and header.get("boundaryOrdinal") == args.max_ordinal:
                    manifests.append(header | {"reused": True, "path": str(destination)})
                    continue
            except Exception:
                pass
        events, counters = collect_partition(args.evidence_root, report, targets)
        rows = build_rows_for_partition(report, events, ordinals, controls)
        metadata = {"schema": SCHEMA + "_PARTITION", "partitionKey": report["partitionKey"], "sourceSha256": source_sha, "boundaryOrdinal": args.max_ordinal, "targetMints": len(targets), "builtMints": len({row['mint'] for row in rows}), "rows": len(rows), "counters": counters}
        write_partition(destination, rows, metadata)
        manifests.append(metadata | {"reused": False, "path": str(destination), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()})
        print(json.dumps({"partition": report["partitionKey"], "targets": len(targets), "rows": len(rows)}, sort_keys=True), flush=True)
    rows = load_atlas(args.output)
    report = summarize(rows, args.max_ordinal)
    args.output.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (args.output / "report.json").write_text(payload, encoding="utf-8")
    (args.output / "report.md").write_text(markdown(report), encoding="utf-8")
    provenance = {
        "schema": SCHEMA + "_PROVENANCE", "boundaryOrdinal": args.max_ordinal,
        "indexPrefixSha256": hashlib.sha256("\n".join(row["mint"] for row in prefix).encode()).hexdigest(),
        "scriptSha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "reportSha256": hashlib.sha256(payload.encode()).hexdigest(), "partitions": manifests,
        "validationRowsInspected": 0, "holdoutRowsInspected": 0,
    }
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"classification": report["classification"], "rows": len(rows), "mints": report["population"]["uniqueMints"], "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
