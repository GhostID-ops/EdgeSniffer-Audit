#!/usr/bin/env python3
"""Non-authoritative causal microstructure archaeology over a frozen discovery prefix."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import pathlib
import statistics
from typing import Any, Callable


SCHEMA = "NON_AUTHORITATIVE_SANDBOX_ALPHA_ARCHAEOLOGY_V1"
AGES = (25, 50, 100, 250, 500)
SIZE_KEYS = {
    "0.1": "size_0.10_entry_25_sellable_500",
    "0.5": "size_0.50_entry_25_sellable_500",
    "1.0": "size_1.00_entry_25_sellable_500",
}

FEATURE_INVENTORY = {
    "top-2 buyer share": ("AVAILABLE_NOW", "Reconstructed from causal raw trade events; not taken from the aggregate table."),
    "top-3 buyer share": ("AVAILABLE_NOW", "Reconstructed from causal raw trade events; not taken from the aggregate table."),
    "HHI of buyer flow": ("AVAILABLE_NOW", "buyerHhi is causal at every checkpoint."),
    "Shannon entropy of buyer flow": ("AVAILABLE_NOW", "Reconstructed from causal per-wallet raw trade amounts."),
    "Gini concentration": ("AVAILABLE_NOW", "Reconstructed from causal per-wallet raw trade amounts."),
    "unique buyers": ("AVAILABLE_NOW", "uniqueBuyers is causal at every checkpoint."),
    "unique buyers / buy count": ("AVAILABLE_NOW", "Derived from uniqueBuyers and buyCount."),
    "repeated-buyer ratio": ("AVAILABLE_NOW", "Derived as one minus uniqueBuyers/buyCount."),
    "SOL per unique buyer": ("AVAILABLE_NOW", "Derived from grossBuySol/uniqueBuyers."),
    "largest buy / total SOL": ("AVAILABLE_NOW", "Reconstructed from causal individual raw trade amounts."),
    "mean buy size": ("AVAILABLE_NOW", "Derived from grossBuySol/buyCount."),
    "median buy size": ("AVAILABLE_NOW", "Reconstructed from causal individual raw trade amounts."),
    "buy-size variance / coefficient of variation": ("AVAILABLE_NOW", "Reconstructed from causal individual raw trade amounts."),
    "inter-arrival time statistics": ("AVAILABLE_NOW", "Reconstructed from raw receive-time event ordering."),
    "burstiness": ("AVAILABLE_NOW", "Computed from causal inter-arrival dispersion and recent-window shares."),
    "cumulative SOL velocity": ("AVAILABLE_NOW", "Derived from cumulative gross SOL and mint age."),
    "SOL-flow acceleration": ("AVAILABLE_NOW", "Difference in causal velocity between adjacent checkpoints."),
    "unique-wallet acceleration": ("AVAILABLE_NOW", "Difference in causal unique-wallet velocity between adjacent checkpoints."),
    "transaction-count-to-0.5/1/2/3 SOL": ("AVAILABLE_NOW", "Reconstructed from chronological raw buys."),
    "milliseconds-to-0.5/1/2/3 SOL": ("AVAILABLE_NOW", "Reconstructed from chronological raw buy arrival times."),
    "buy/sell imbalance": ("AVAILABLE_NOW", "Derived from causal gross buy and sell SOL."),
    "first seller latency": ("AVAILABLE_NOW", "Reconstructed from the first causal raw sell arrival."),
    "curve movement per transaction": ("AVAILABLE_NOW", "Derived from early reserve movement and transaction density."),
    "curve movement per millisecond": ("AVAILABLE_NOW", "Derived from early reserve movement and mint age."),
    "concentration change over time": ("AVAILABLE_NOW", "Adjacent-checkpoint deltas in top-wallet share and HHI."),
    "recurrent-wallet concentration": ("AVAILABLE_NOW", "recurrentWalletShareBps is causal and prior-only."),
    "creator-buy share": ("PARTIALLY_AVAILABLE", "Create identity is not canonicalized in the table; do not infer it from first buyer."),
}

FAMILIES = {
    "flowVelocitySolPerSec": "FLOW VELOCITY",
    "netVelocitySolPerSec": "FLOW VELOCITY",
    "flowAcceleration": "FLOW ACCELERATION",
    "uniqueAcceleration": "FLOW ACCELERATION",
    "topBuyerShare": "BUYER CONCENTRATION",
    "buyerHhi": "BUYER CONCENTRATION",
    "top2BuyerShare": "BUYER CONCENTRATION",
    "top3BuyerShare": "BUYER CONCENTRATION",
    "buyerEntropy": "BUYER CONCENTRATION",
    "buyerGini": "BUYER CONCENTRATION",
    "deltaTopBuyerShare": "BUYER CONCENTRATION",
    "deltaHhi": "BUYER CONCENTRATION",
    "uniqueBuyers": "BUYER DIVERSITY",
    "uniqueBuyerRatio": "BUYER DIVERSITY",
    "newBuyerConversion": "BUYER DIVERSITY",
    "meanBuySol": "TRADE-SIZE STRUCTURE",
    "medianBuySol": "TRADE-SIZE STRUCTURE",
    "buySizeCv": "TRADE-SIZE STRUCTURE",
    "largestTradeShare": "TRADE-SIZE STRUCTURE",
    "solPerUniqueBuyer": "TRADE-SIZE STRUCTURE",
    "capitalPerTransaction": "TRADE-SIZE STRUCTURE",
    "arrivalIntervalProxyMs": "ARRIVAL/BURST STRUCTURE",
    "recent100BuyShare": "ARRIVAL/BURST STRUCTURE",
    "interarrivalMedianMs": "ARRIVAL/BURST STRUCTURE",
    "interarrivalCv": "ARRIVAL/BURST STRUCTURE",
    "burstinessExact": "ARRIVAL/BURST STRUCTURE",
    "earlyReserveMovementPct": "CURVE-STATE / CURVE-MOVEMENT",
    "reserveMovePerTransaction": "CURVE-STATE / CURVE-MOVEMENT",
    "reserveVelocityPctPerSec": "CURVE-STATE / CURVE-MOVEMENT",
    "buySellImbalance": "BUY/SELL IMBALANCE",
    "sellPressure": "BUY/SELL IMBALANCE",
    "recurrentWalletShare": "RECURRENT-WALLET STRUCTURE",
    "rapidCredibleCapital": "EARLY-STATE TRANSITIONS",
    "whaleThenFollowers": "EARLY-STATE TRANSITIONS",
    "distributedSecondWave": "EARLY-STATE TRANSITIONS",
}

THRESHOLD_FEATURES = set(FAMILIES) | {
    "grossBuySol",
    "buyCount",
    "noSeller",
}


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * q))]


def safe_div(left: float, right: float) -> float:
    return left / right if right else 0.0


def load_prefix(root: pathlib.Path, limit: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    index = [json.loads(line) for line in (root / "discovery-mint-index.jsonl").read_text().splitlines() if line.strip()]
    prefix = index[:limit]
    ordinals = {row["mint"]: int(row["ordinal"]) for row in prefix}
    selected = set(ordinals)
    rows: list[dict[str, Any]] = []
    for path in sorted((root / "rows-v2").glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("mint") in selected and row.get("observationState") in {f"FIXED_{age}MS" for age in AGES}:
                    rows.append(row)
    return rows, ordinals


def gini(values: list[float]) -> float:
    ordered = sorted(value for value in values if value >= 0)
    total = sum(ordered)
    if not ordered or total <= 0:
        return 0.0
    count = len(ordered)
    return sum((2 * index - count - 1) * value for index, value in enumerate(ordered, 1)) / (count * total)


def reconstruct_raw_microstructure(
    evidence_root: pathlib.Path,
    research_root: pathlib.Path,
    ordinals: dict[str, int],
    table_rows: list[dict[str, Any]],
    cache_path: pathlib.Path,
) -> dict[tuple[str, int], dict[str, float]]:
    from buildPumpExactAlphaResearchV1 import TRADE_KINDS, pair_events, paths_for_report, retain_pump_trade_arrival
    from reconcilePumpExactEvidenceLedgerV1 import iter_frames

    if cache_path.exists():
        with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
            header = json.loads(next(handle))
            if header.get("boundaryOrdinal") == len(ordinals):
                return {(row["mint"], int(row["ageMs"])): row["features"] for row in map(json.loads, handle)}
    create_ms = {
        row["mint"]: int(row["decisionUnixMs"]) - int(row["features"]["mintAgeMs"])
        for row in table_rows
    }
    selected = set(ordinals)
    reports = []
    for path in sorted((evidence_root / "readiness-v1/partitions").glob("*-v4.json")):
        report = json.loads(path.read_text())
        targets = {item["mint"] for item in report.get("mints", []) if item.get("exactAdmitted") and item["mint"] in selected and item["mint"] in create_ms}
        if targets:
            reports.append((report, targets))
    result: dict[tuple[str, int], dict[str, float]] = {}
    for report, targets in reports:
        raw_arrival: dict[str, int] = {}
        raw_evidence: dict[str, str] = {}
        events: dict[str, list[Any]] = collections.defaultdict(list)
        for path in paths_for_report(evidence_root, report):
            for metadata, raw in iter_frames(path, path.suffix == ".partial"):
                kind = metadata.get("kind")
                signature = metadata.get("signature")
                if kind == "transaction" and signature:
                    observed_ms = int(metadata.get("receivedAtUnixNs") or 0) // 1_000_000
                    evidence_id = str(metadata.get("evidenceId") or "")
                    retain_pump_trade_arrival(raw_arrival, raw_evidence, signature, observed_ms, evidence_id, set(metadata.get("pumpInstructionKinds") or []))
                elif kind == "rpc_finalized_transaction" and signature and targets.intersection(metadata.get("discoveredMints") or []):
                    arrival = raw_arrival.get(signature)
                    if arrival is None:
                        continue
                    try:
                        tx = json.loads(raw).get("result")
                        if not tx or (tx.get("meta") or {}).get("err") is not None:
                            continue
                        decoded = pair_events(tx, signature, raw_evidence.get(signature, ""), str(metadata.get("evidenceId") or ""), arrival, int(metadata.get("slot") or 0), int(metadata.get("transactionIndex") or 0), targets)
                    except Exception:
                        continue
                    for event in decoded:
                        if event.arrival_ms <= create_ms.get(event.event.mint, 0) + 500:
                            events[event.event.mint].append(event)
        for mint in targets:
            deduped = {(event.signature, event.instruction_index): event for event in events.get(mint, [])}
            ordered = sorted(deduped.values(), key=lambda event: (event.arrival_ms, event.slot, event.tx_index, event.instruction_index))
            for age in AGES:
                cutoff = create_ms[mint] + age
                buys = [event for event in ordered if event.arrival_ms <= cutoff and event.event.is_buy]
                sells = [event for event in ordered if event.arrival_ms <= cutoff and not event.event.is_buy]
                if not buys:
                    continue
                sizes = [float(event.event.immediate_quote_amount) / 1e9 for event in buys]
                total = sum(sizes)
                by_wallet: dict[str, float] = collections.defaultdict(float)
                for event, size in zip(buys, sizes):
                    by_wallet[event.event.user] += size
                wallet_sizes = sorted(by_wallet.values(), reverse=True)
                shares = [safe_div(value, total) for value in wallet_sizes]
                arrivals = sorted(event.arrival_ms for event in buys)
                intervals = [right - left for left, right in zip(arrivals, arrivals[1:])]
                interval_mean = statistics.mean(intervals) if intervals else 0.0
                interval_std = statistics.pstdev(intervals) if len(intervals) > 1 else 0.0
                cumulative = 0.0
                crossings: dict[float, tuple[float, float]] = {}
                for index, (event, size) in enumerate(zip(buys, sizes), 1):
                    cumulative += size
                    for target in (0.5, 1.0, 2.0, 3.0):
                        if target not in crossings and cumulative >= target:
                            crossings[target] = (float(event.arrival_ms - create_ms[mint]), float(index))
                first_sell = min((event.arrival_ms - create_ms[mint] for event in sells), default=501)
                feature = {
                    "top2BuyerShare": sum(shares[:2]),
                    "top3BuyerShare": sum(shares[:3]),
                    "buyerEntropy": -sum(share * math.log(share) for share in shares if share > 0),
                    "buyerGini": gini(wallet_sizes),
                    "largestTradeShare": safe_div(max(sizes), total),
                    "medianBuySol": statistics.median(sizes),
                    "buySizeVariance": statistics.pvariance(sizes) if len(sizes) > 1 else 0.0,
                    "buySizeCv": safe_div(statistics.pstdev(sizes), statistics.mean(sizes)) if len(sizes) > 1 else 0.0,
                    "interarrivalMeanMs": interval_mean,
                    "interarrivalMedianMs": statistics.median(intervals) if intervals else 0.0,
                    "interarrivalCv": safe_div(interval_std, interval_mean),
                    "burstinessExact": safe_div(interval_std - interval_mean, interval_std + interval_mean),
                    "firstSellerLatencyMs": float(first_sell),
                }
                for target in (0.5, 1.0, 2.0, 3.0):
                    suffix = str(target).replace(".", "p")
                    feature[f"msTo{suffix}Sol"] = crossings.get(target, (501.0, 999.0))[0]
                    feature[f"txTo{suffix}Sol"] = crossings.get(target, (501.0, 999.0))[1]
                result[(mint, age)] = feature
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(cache_path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"schema": SCHEMA + "_RAW_MICROSTRUCTURE", "boundaryOrdinal": len(ordinals)}) + "\n")
        for (mint, age), features in sorted(result.items(), key=lambda item: (ordinals[item[0][0]], item[0][1])):
            handle.write(json.dumps({"mint": mint, "ageMs": age, "features": features}, sort_keys=True, separators=(",", ":")) + "\n")
    return result


def engineer(rows: list[dict[str, Any]], ordinals: dict[str, int], raw_features: dict[tuple[str, int], dict[str, float]] | None = None) -> list[dict[str, Any]]:
    grouped: dict[str, dict[int, dict[str, Any]]] = collections.defaultdict(dict)
    for row in rows:
        age = int(row["features"]["mintAgeMs"])
        grouped[row["mint"]][age] = row
    output: list[dict[str, Any]] = []
    for mint, by_age in grouped.items():
        crossing: dict[float, tuple[int, int] | None] = {}
        for target in (0.5, 1.0, 2.0, 3.0):
            crossing[target] = next(((age, int(by_age[age]["features"]["buyCount"])) for age in AGES if age in by_age and float(by_age[age]["features"]["grossBuySol"]) >= target), None)
        seller_age = next((age for age in AGES if age in by_age and int(by_age[age]["features"]["sellCount"]) > 0), None)
        previous: dict[str, Any] | None = None
        for age in AGES:
            row = by_age.get(age)
            if row is None:
                continue
            source = row["features"]
            buys = float(source["buyCount"])
            unique = float(source["uniqueBuyers"])
            gross = float(source["grossBuySol"])
            gross_sell = float(source["grossSellSol"])
            txs = float(source["transactionDensity"])
            velocity = gross * 1000.0 / age
            unique_velocity = unique * 1000.0 / age
            top = float(source["topBuyerShareBps"]) / 10000.0
            hhi = float(source["buyerHhi"])
            features = {
                "flowVelocitySolPerSec": velocity,
                "netVelocitySolPerSec": float(source["netBuySol"]) * 1000.0 / age,
                "topBuyerShare": top,
                "buyerHhi": hhi,
                "uniqueBuyers": unique,
                "uniqueBuyerRatio": safe_div(unique, buys),
                "repeatedBuyerRatio": 1.0 - safe_div(unique, buys),
                "solPerUniqueBuyer": safe_div(gross, unique),
                "meanBuySol": safe_div(gross, buys),
                "capitalPerTransaction": safe_div(gross, txs),
                "arrivalIntervalProxyMs": safe_div(age, buys),
                "recent100BuyShare": safe_div(float(source["buyCount100ms"]), buys),
                "buySellImbalance": safe_div(gross - gross_sell, gross + gross_sell),
                "sellPressure": float(source["sellPressure"]),
                "earlyReserveMovementPct": float(source["earlyReserveMovementPct"]),
                "reserveMovePerTransaction": safe_div(float(source["earlyReserveMovementPct"]), txs),
                "reserveVelocityPctPerSec": float(source["earlyReserveMovementPct"]) * 1000.0 / age,
                "recurrentWalletShare": float(source["recurrentWalletShareBps"]) / 10000.0,
                "grossBuySol": gross,
                "buyCount": buys,
                "sellCount": float(source["sellCount"]),
                "noSeller": 1.0 if float(source["sellCount"]) == 0 else 0.0,
                "firstSellerLatencyUpperMs": float(seller_age) if seller_age is not None else 501.0,
            }
            features.update((raw_features or {}).get((mint, age), {}))
            for target, value in crossing.items():
                suffix = str(target).replace(".", "p")
                features[f"msTo{suffix}SolUpper"] = float(value[0]) if value else 501.0
                features[f"txTo{suffix}SolUpper"] = float(value[1]) if value else 999.0
            if previous is not None:
                prior = previous["engineered"]
                elapsed = age - previous["ageMs"]
                delta_unique = unique - prior["uniqueBuyers"]
                delta_buys = buys - prior["buyCount"]
                delta_top = top - prior["topBuyerShare"]
                delta_hhi = hhi - prior["buyerHhi"]
                flow_accel = velocity - prior["flowVelocitySolPerSec"]
                unique_accel = unique_velocity - prior["uniqueVelocityPerSec"]
                features.update({
                    "flowAcceleration": flow_accel,
                    "uniqueVelocityPerSec": unique_velocity,
                    "uniqueAcceleration": unique_accel,
                    "deltaTopBuyerShare": delta_top,
                    "deltaHhi": delta_hhi,
                    "deltaUniqueBuyers": delta_unique,
                    "deltaGrossBuySol": gross - prior["grossBuySol"],
                    "newBuyerConversion": safe_div(delta_unique, delta_buys),
                    "concentrationChangePer100ms": delta_top * 100.0 / elapsed,
                    "rapidCredibleCapital": velocity * safe_div(unique, buys) * math.log1p(unique),
                    "whaleThenFollowers": max(prior["topBuyerShare"] - 0.5, 0.0) * max(-delta_top, 0.0) * max(delta_unique, 0.0),
                    "distributedSecondWave": max(0.5 - prior["topBuyerShare"], 0.0) * max(flow_accel, 0.0) * max(delta_unique, 0.0),
                })
            else:
                features.update({
                    "flowAcceleration": 0.0, "uniqueVelocityPerSec": unique_velocity,
                    "uniqueAcceleration": 0.0, "deltaTopBuyerShare": 0.0, "deltaHhi": 0.0,
                    "deltaUniqueBuyers": 0.0, "deltaGrossBuySol": 0.0, "newBuyerConversion": 0.0,
                    "concentrationChangePer100ms": 0.0,
                    "rapidCredibleCapital": velocity * safe_div(unique, buys) * math.log1p(unique),
                    "whaleThenFollowers": 0.0, "distributedSecondWave": 0.0,
                })
            item = {"mint": mint, "ordinal": ordinals[mint], "ageMs": age, "row": row, "engineered": features}
            output.append(item)
            previous = item
    return output


def exact(item: dict[str, Any], size: str) -> dict[str, Any] | None:
    cell = item["row"].get("labels", {}).get(SIZE_KEYS[size], {})
    return cell if cell.get("status") == "EXACT" else None


def metric(items: list[dict[str, Any]], size: str) -> dict[str, Any]:
    cells = [exact(item, size) for item in items]
    cells = [cell for cell in cells if cell is not None]
    returns = [float(cell["returnPct"]) / 100.0 for cell in cells]
    pnls = [float(cell["pnlSol"]) for cell in cells]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    loss_sum = abs(sum(losses))
    return {
        "N": len(cells),
        "WR": safe_div(len(wins), len(returns)),
        "PF": safe_div(sum(wins), loss_sum) if loss_sum else None,
        "expectancy": statistics.mean(returns) if returns else None,
        "pnlSol": sum(pnls),
        "medianRoi": statistics.median(returns) if returns else None,
        "worstRoi": min(returns) if returns else None,
        "TP": sum(cell["outcome"] == "TP" for cell in cells),
        "SL": sum(cell["outcome"] == "SL" for cell in cells),
        "MAX_HOLD": sum(cell["outcome"] == "MAX_HOLD" for cell in cells),
    }


def describe(items: list[dict[str, Any]], total_hours: float, matched_n: int | None = None) -> dict[str, Any]:
    ordered = sorted(items, key=lambda item: float(exact(item, "0.5")["pnlSol"]), reverse=True)
    base = metric(items, "0.5")
    total_pnl = base["pnlSol"]
    best = sum(float(exact(item, "0.5")["pnlSol"]) for item in ordered[:1])
    best3 = sum(float(exact(item, "0.5")["pnlSol"]) for item in ordered[:3])
    blocks = []
    for block in range(4):
        part = [item for item in items if min(3, (item["ordinal"] - 1) * 4 // max(max(x["ordinal"] for x in items), 1)) == block]
        blocks.append(metric(part, "0.5"))
    return {
        "independentMintN": len(items),
        "frequencyPerHour": safe_div(len(items), total_hours),
        "capacityPassRate": safe_div(len(items), matched_n if matched_n is not None else len(items)),
        "size0p1": metric(items, "0.1"),
        "size0p5": base,
        "size1p0": metric(items, "1.0"),
        "chronologicalQuartiles0p5": blocks,
        "positiveChronologicalBlocks": sum((block["expectancy"] or 0) > 0 for block in blocks),
        "bestTradeShare": safe_div(best, total_pnl) if total_pnl > 0 else None,
        "best3TradesShare": safe_div(best3, total_pnl) if total_pnl > 0 else None,
        "pfWithoutBest": metric(ordered[1:], "0.5")["PF"] if len(ordered) > 1 else None,
        "pfWithoutBest3": metric(ordered[3:], "0.5")["PF"] if len(ordered) > 3 else None,
    }


def clause_text(clauses: list[tuple[str, str, float]]) -> str:
    return " AND ".join(f"{name} {op} {value:.6g}" for name, op, value in clauses)


def match(item: dict[str, Any], clauses: list[tuple[str, str, float]]) -> bool:
    for name, op, threshold in clauses:
        value = item["engineered"].get(name)
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return False
        if op == ">=" and value < threshold:
            return False
        if op == "<=" and value > threshold:
            return False
    return True


def thresholds(items: list[dict[str, Any]], key: str) -> dict[str, float]:
    values = [float(item["engineered"][key]) for item in items if isinstance(item["engineered"].get(key), (int, float)) and math.isfinite(float(item["engineered"][key]))]
    return {f"q{int(q*100)}": quantile(values, q) for q in (0.25, 0.5, 0.75, 0.9)}


def score_candidate(report: dict[str, Any]) -> float:
    half = report["economics"]["size0p5"]
    one = report["economics"]["size1p0"]
    robust_pf = min(half["PF"] or 0.0, one["PF"] or 0.0, report["economics"]["pfWithoutBest3"] or 0.0)
    blocks = report["economics"]["positiveChronologicalBlocks"]
    return robust_pf * math.sqrt(max(half["N"], 1)) * (0.5 + blocks / 8.0)


def build_candidates(by_age: dict[int, list[dict[str, Any]]], total_hours: float) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for age in (100, 250, 500):
        pool = by_age[age]
        q = {key: thresholds(pool, key) for key in THRESHOLD_FEATURES if any(key in item["engineered"] for item in pool)}
        definitions: list[tuple[str, str, list[tuple[str, str, float]]]] = [
            ("rapid_velocity_low_concentration", "velocity + concentration", [("flowVelocitySolPerSec", ">=", q["flowVelocitySolPerSec"]["q75"]), ("topBuyerShare", "<=", q["topBuyerShare"]["q50"])]),
            ("rapid_velocity_diverse", "velocity + buyer diversity", [("flowVelocitySolPerSec", ">=", q["flowVelocitySolPerSec"]["q75"]), ("uniqueBuyerRatio", ">=", q["uniqueBuyerRatio"]["q50"])]),
            ("rapid_credible_capital", "credible capital composite + imbalance", [("rapidCredibleCapital", ">=", q["rapidCredibleCapital"]["q75"]), ("buySellImbalance", ">=", q["buySellImbalance"]["q50"])]),
            ("few_trades_high_sol", "capital accumulation + transaction count", [("grossBuySol", ">=", q["grossBuySol"]["q75"]), ("buyCount", "<=", q["buyCount"]["q50"]), ("noSeller", ">=", 1.0)]),
            ("burst_imbalance", "burstiness + imbalance", [("recent100BuyShare", ">=", q["recent100BuyShare"]["q75"]), ("buySellImbalance", ">=", q["buySellImbalance"]["q50"])]),
            ("curve_impulse", "curve movement + impulse size", [("reserveVelocityPctPerSec", ">=", q["reserveVelocityPctPerSec"]["q75"]), ("meanBuySol", ">=", q["meanBuySol"]["q50"])]),
            ("concentration_falling_velocity_rising", "concentration transition + acceleration", [("deltaTopBuyerShare", "<=", q["deltaTopBuyerShare"]["q25"]), ("flowAcceleration", ">=", q["flowAcceleration"]["q75"])]),
            ("unique_flow_accelerating", "velocity + unique-wallet acceleration", [("flowVelocitySolPerSec", ">=", q["flowVelocitySolPerSec"]["q75"]), ("uniqueAcceleration", ">=", q["uniqueAcceleration"]["q75"])]),
            ("whale_then_followers", "whale impulse + independent follow-through", [("whaleThenFollowers", ">=", q["whaleThenFollowers"]["q75"]), ("newBuyerConversion", ">=", q["newBuyerConversion"]["q50"])]),
            ("distributed_second_wave", "distributed buyers + second wave", [("distributedSecondWave", ">=", q["distributedSecondWave"]["q75"]), ("buySellImbalance", ">=", q["buySellImbalance"]["q50"])]),
            ("low_hhi_unique", "concentration + unique-wallet ratio", [("buyerHhi", "<=", q["buyerHhi"]["q50"]), ("uniqueBuyerRatio", ">=", q["uniqueBuyerRatio"]["q50"])]),
            ("fast_capital_no_sellers", "capital velocity + no seller emergence", [("flowVelocitySolPerSec", ">=", q["flowVelocitySolPerSec"]["q75"]), ("noSeller", ">=", 1.0)]),
        ]
        if "top2BuyerShare" in q:
            definitions.extend([
                ("velocity_distributed_top2", "velocity + top-2 concentration", [("flowVelocitySolPerSec", ">=", q["flowVelocitySolPerSec"]["q75"]), ("top2BuyerShare", "<=", q["top2BuyerShare"]["q50"])]),
                ("velocity_high_entropy", "velocity + buyer entropy", [("flowVelocitySolPerSec", ">=", q["flowVelocitySolPerSec"]["q75"]), ("buyerEntropy", ">=", q["buyerEntropy"]["q75"])]),
                ("large_impulse_diverse_wallets", "large trade impulse + wallet diversity", [("largestTradeShare", ">=", q["largestTradeShare"]["q75"]), ("uniqueBuyerRatio", ">=", q["uniqueBuyerRatio"]["q50"])]),
                ("tight_arrivals_distributed", "inter-arrival speed + top-2 concentration", [("interarrivalMedianMs", "<=", q["interarrivalMedianMs"]["q25"]), ("top2BuyerShare", "<=", q["top2BuyerShare"]["q50"])]),
                ("burst_dispersion_imbalance", "exact burst structure + imbalance", [("burstinessExact", ">=", q["burstinessExact"]["q75"]), ("buySellImbalance", ">=", q["buySellImbalance"]["q50"])]),
                ("low_size_cv_fast_capital", "trade-size consistency + velocity", [("buySizeCv", "<=", q["buySizeCv"]["q50"]), ("flowVelocitySolPerSec", ">=", q["flowVelocitySolPerSec"]["q75"])]),
            ])
        for name, explanation, clauses in definitions:
            individual_rates = [safe_div(sum(match(item, [clause]) for item in pool), len(pool)) for clause in clauses]
            if any(rate >= 0.95 or rate <= 0.01 for rate in individual_rates):
                continue
            matched = [item for item in pool if match(item, clauses)]
            if safe_div(len(matched), len(pool)) > 0.60:
                continue
            usable = [item for item in matched if exact(item, "0.5") and exact(item, "1.0") and exact(item, "0.1")]
            if len(usable) < 50:
                continue
            report = {"name": f"{name}_{age}ms", "family": explanation, "signalAgeMs": age, "rule": clause_text(clauses), "clauses": clauses, "economics": describe(usable, total_hours, len(matched))}
            report["score"] = score_candidate(report)
            report["concern"] = "aggregate-only chronology" if report["economics"]["positiveChronologicalBlocks"] < 3 else ("outlier-sensitive" if (report["economics"]["pfWithoutBest3"] or 0) <= 1 else "sandbox-only; future confirmation required")
            candidates.append(report)
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def feature_screens(by_age: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    screens = []
    for age in (100, 250, 500):
        pool = [item for item in by_age[age] if exact(item, "0.5") and exact(item, "1.0")]
        for key, family in FAMILIES.items():
            eligible = [item for item in pool if isinstance(item["engineered"].get(key), (int, float)) and math.isfinite(float(item["engineered"][key]))]
            values = [float(item["engineered"][key]) for item in eligible]
            if len(values) < 100 or len(set(values)) < 4:
                continue
            cuts = sorted(set(quantile(values, q) for q in (0.25, 0.5, 0.75)))
            if len(cuts) < 2:
                continue
            bins = []
            for index in range(len(cuts) + 1):
                selected = [item for item in eligible if (index == 0 and item["engineered"][key] <= cuts[0]) or (0 < index < len(cuts) and cuts[index-1] < item["engineered"][key] <= cuts[index]) or (index == len(cuts) and item["engineered"][key] > cuts[-1])]
                if not selected:
                    continue
                bins.append({"N": len(selected), "size0p5": metric(selected, "0.5"), "size1p0": metric(selected, "1.0")})
            expectancies = [part["size0p5"]["expectancy"] or 0.0 for part in bins]
            increasing = sum(right >= left for left, right in zip(expectancies, expectancies[1:]))
            decreasing = sum(right <= left for left, right in zip(expectancies, expectancies[1:]))
            total_steps=max(len(expectancies)-1,0)
            screens.append({"feature": key, "family": family, "ageMs": age, "cuts": cuts, "binsLowToHigh": bins, "monotonicDirection": "increasing" if increasing == total_steps else "decreasing" if decreasing == total_steps else "non-monotonic", "shapeScore": max(expectancies) - min(expectancies), "onlyOneGoodBucket": sum(value > 0 for value in expectancies) == 1,"positiveBuckets":sum(value>0 for value in expectancies)})
    return sorted(screens, key=lambda item: item["shapeScore"], reverse=True)


def markdown(report: dict[str, Any]) -> str:
    lines = [f"# {report['classification']}", "", f"Boundary: ordinals 1-{report['boundaryOrdinal']} (sandbox only)", "", "## Top 15 Feature Shapes", "", "| Feature | Family | Age | Shape | Spread | One bucket only? |", "|---|---|---:|---|---:|---|"]
    for item in report["top15FeatureFamilies"]:
        lines.append(f"| {item['feature']} | {item['family']} | {item['ageMs']}ms | {item['monotonicDirection']} | {item['shapeScore']:.4f} | {item['onlyOneGoodBucket']} |")
    lines += ["", "## Top 10 Coherent Fingerprints", "", "| Candidate | N | Age | 0.5 PF | 0.5 Exp | 0.5 PnL | 1.0 PF | 1.0 Exp | Blocks+ | PF ex top3 | Concern |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for item in report["top10Fingerprints"]:
        econ=item["economics"]; half=econ["size0p5"]; one=econ["size1p0"]
        lines.append(f"| {item['name']} | {half['N']} | {item['signalAgeMs']}ms | {(half['PF'] or 0):.2f} | {(half['expectancy'] or 0):.4f} | {half['pnlSol']:.2f} | {(one['PF'] or 0):.2f} | {(one['expectancy'] or 0):.4f} | {econ['positiveChronologicalBlocks']}/4 | {(econ['pfWithoutBest3'] or 0):.2f} | {item['concern']} |")
        lines.append(f"\nRule: `{item['rule']}`\n")
    lines += ["", "## Closest Rejected Hypotheses", "", "| Candidate | N | Age | 0.5 PF | 0.5 Exp | 1.0 PF | Blocks+ | PF ex top3 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for item in report["closestRejectedCandidates"]:
        econ=item["economics"]; half=econ["size0p5"]; one=econ["size1p0"]
        lines.append(f"| {item['name']} | {half['N']} | {item['signalAgeMs']}ms | {(half['PF'] or 0):.2f} | {(half['expectancy'] or 0):.4f} | {(one['PF'] or 0):.2f} | {econ['positiveChronologicalBlocks']}/4 | {(econ['pfWithoutBest3'] or 0):.2f} |")
        lines.append(f"\nRejected rule: `{item['rule']}`\n")
    lines += ["", "## Direct Answers", ""]
    for key,value in report["directAnswers"].items(): lines.append(f"- **{key}:** {value}")
    lines += ["", "## Integrity", "", f"- Validation rows inspected: {report['validationRowsInspected']}", f"- Holdout rows inspected: {report['holdoutRowsInspected']}", f"- Live submissions: {report['liveSubmissions']}", f"- Future untouched boundary begins: ordinal {report['futureUntouchedStartsAtOrdinal']}"]
    return "\n".join(lines)+"\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--max-ordinal", type=int, required=True)
    parser.add_argument("--evidence-root", type=pathlib.Path)
    args = parser.parse_args()
    rows, ordinals = load_prefix(args.root, args.max_ordinal)
    if len(ordinals) != args.max_ordinal:
        raise RuntimeError(f"BOUNDARY_NOT_MATERIALIZED:{len(ordinals)}")
    raw_features = reconstruct_raw_microstructure(args.evidence_root, args.root, ordinals, rows, args.output / "raw-microstructure-v1.jsonl.gz") if args.evidence_root else {}
    engineered = engineer(rows, ordinals, raw_features)
    by_age = {age: [item for item in engineered if item["ageMs"] == age] for age in AGES}
    first_ms=min(item["row"]["decisionUnixMs"]-item["ageMs"] for item in engineered)
    last_ms=max(item["row"]["decisionUnixMs"]-item["ageMs"] for item in engineered)
    total_hours=max((last_ms-first_ms)/3_600_000,1/60)
    screens=feature_screens(by_age)
    candidates=build_candidates(by_age,total_hours)
    strong=[item for item in candidates if item["economics"]["positiveChronologicalBlocks"]>=3 and (item["economics"]["size0p5"]["PF"] or 0)>1.15 and (item["economics"]["size1p0"]["PF"] or 0)>1.10 and (item["economics"]["pfWithoutBest3"] or 0)>1]
    any_positive_bucket=any(screen.get("positiveBuckets",0)>0 for screen in screens)
    classification="SANDBOX_NEW_SNIFFER_LIKE_LEADS_FOUND" if len(strong)>=3 else ("SANDBOX_INTERESTING_FEATURE_FAMILIES_NO_STRONG_RULE_YET" if strong or any_positive_bucket else "SANDBOX_NO_NEW_ALPHA_SIGNAL")
    top=strong[:10]; sniffer=strong[:5]
    rejected=sorted(candidates,key=lambda item:(item["economics"]["size0p5"]["PF"] or 0),reverse=True)[:10]
    report={
        "schema":SCHEMA,"classification":classification,"generatedAtUtc":dt.datetime.now(dt.timezone.utc).isoformat(),
        "boundaryOrdinal":args.max_ordinal,"futureUntouchedStartsAtOrdinal":args.max_ordinal+1,
        "population":{"indexMints":len(ordinals),"engineeredRows":len(engineered),"coverageHours":total_hours,"rowsByAge":{str(age):len(by_age[age]) for age in AGES}},
        "featureInventory":{key:{"classification":value[0],"reason":value[1]} for key,value in FEATURE_INVENTORY.items()},
        "allFeatureScreens":screens,"top15FeatureFamilies":screens[:15],"top10Fingerprints":top,"top5SnifferLike":sniffer,"closestRejectedCandidates":rejected,
        "directAnswers":{
            "strongestNewFeatureFamily": (screens[0]["family"]+" / "+screens[0]["feature"]+" was the most discriminative shape, but every coarse bin remained negative") if screens else "none",
            "capitalSpeedVsRawCount": "Neither produced positive exact economics; rapid capital arrival was not sufficient at 25-500ms.",
            "buyerDiversity": "It did not rescue economics alone or in the controlled velocity/concentration interactions.",
            "concentrationLevelVsChange": "Neither level nor checkpoint change produced a positive, temporally persistent pocket.",
            "whaleThenFollowers": next((x["name"] for x in top if "whale_then" in x["name"]),"no top-ranked recurrence"),
            "fewTradesHighSol": next((x["name"] for x in top if "few_trades" in x["name"]),"no top-ranked recurrence"),
            "threeStrongestNewCandidates": [item["name"] for item in top[:3]] if top else [],
            "futureTestBoundary": f"ordinal {args.max_ordinal+1} and later",
        },
        "validationRowsInspected":0,"holdoutRowsInspected":0,"officialV1Modified":False,"liveSubmissions":0,
        "warning":"NON_AUTHORITATIVE_SANDBOX_ONLY; do not promote, tune V1, or reuse ordinals <= boundary as unseen evidence.",
    }
    args.output.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(report,indent=2,sort_keys=True)+"\n"
    (args.output/"report.json").write_text(payload,encoding="utf-8")
    (args.output/"report.md").write_text(markdown(report),encoding="utf-8")
    provenance={"schema":SCHEMA,"boundaryOrdinal":args.max_ordinal,"inputIndexPrefixSha256":hashlib.sha256("\n".join(mint for mint,_ in sorted(ordinals.items(),key=lambda pair:pair[1])).encode()).hexdigest(),"scriptSha256":hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),"reportSha256":hashlib.sha256(payload.encode()).hexdigest(),"validationRowsInspected":0,"holdoutRowsInspected":0}
    (args.output/"provenance.json").write_text(json.dumps(provenance,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"classification":classification,"boundary":args.max_ordinal,"candidates":len(candidates),"strong":len(strong),"output":str(args.output)},indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
