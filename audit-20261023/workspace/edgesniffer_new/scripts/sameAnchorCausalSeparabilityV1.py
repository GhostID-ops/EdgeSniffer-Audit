#!/usr/bin/env python3
"""Run the frozen univariate same-anchor causal separability study."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pathlib
import random
import statistics
from typing import Any

from buildPumpExactAlphaResearchV1 import NETWORK_FEE
from exactCurveEconomicsV1 import CurveState, ExactCurveError, quote_exact_quote_in_buy, quote_exact_token_sell
from executableOpportunityCensusV1 import group_events
from sandboxPumpExactFollowThroughAtlasV1 import SOL_QUOTES, collect_partition, load_prefix
from validatePumpExactEvidenceLedgerV1 import event_fees


SCHEMA = "SAME_ANCHOR_CAUSAL_SEPARABILITY_V1"
LAMPORTS = 1_000_000_000
ANCHORS = (
    "EXTERNAL_GROSS_BUY_GE_1_SOL",
    "EXTERNAL_GROSS_BUY_GE_2_SOL",
    "EXTERNAL_GROSS_BUY_GE_5_SOL",
)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = int(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def distribution(values: list[float]) -> dict[str, Any]:
    return {
        "N": len(values),
        "mean": statistics.mean(values) if values else None,
        "P10": percentile(values, 0.10),
        "P25": percentile(values, 0.25),
        "median": statistics.median(values) if values else None,
        "P75": percentile(values, 0.75),
        "P90": percentile(values, 0.90),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def coefficient_of_variation(values: list[float]) -> float | None:
    if not values:
        return None
    mean = statistics.mean(values)
    return statistics.pstdev(values) / mean if mean else None


def state_from_dict(value: dict[str, Any]) -> CurveState:
    return CurveState(
        virtual_sol=int(value["virtualSol"]),
        virtual_token=int(value["virtualToken"]),
        real_sol=int(value["realSol"]),
        real_token=int(value["realToken"]),
    )


def external_proceeds_after_flow(
    post_own_buy: CurveState,
    inventory: int,
    gross_external_lamports: int,
    fee_event: Any,
) -> int | None:
    try:
        state = post_own_buy
        if gross_external_lamports > 0:
            state = quote_exact_quote_in_buy(
                state, gross_external_lamports, event_fees(fee_event)
            ).post_state
        return quote_exact_token_sell(
            state,
            inventory,
            1,
            event_fees(fee_event),
            cashback_bps=int(fee_event.cashback_fee_basis_points or 0),
            buyback_share_bps=int(fee_event.buyback_fee_basis_points or 0),
        ).immediate_quote_out
    except (ExactCurveError, ValueError, OverflowError):
        return None


def gross_external_distance_to_return(
    post_own_buy: CurveState,
    inventory: int,
    fee_event: Any,
    target_return_pct: float,
    standardized_delivery_cost_sol: float,
) -> float | None:
    capital = 500_000_000 + NETWORK_FEE
    target = math.ceil(
        capital * (1.0 + target_return_pct / 100.0)
        + standardized_delivery_cost_sol * LAMPORTS
    )
    at_zero = external_proceeds_after_flow(post_own_buy, inventory, 0, fee_event)
    if at_zero is not None and at_zero >= target:
        return 0.0

    maximum = 1_000 * LAMPORTS
    low_valid = 0
    high = 1_000_000
    target_high: int | None = None
    invalid_high: int | None = None
    while high <= maximum:
        proceeds = external_proceeds_after_flow(post_own_buy, inventory, high, fee_event)
        if proceeds is None:
            invalid_high = high
            break
        low_valid = high
        if proceeds >= target:
            target_high = high
            break
        high *= 2

    if target_high is None and invalid_high is not None:
        lo, hi = low_valid, invalid_high - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if external_proceeds_after_flow(post_own_buy, inventory, mid, fee_event) is None:
                hi = mid - 1
            else:
                lo = mid
        proceeds = external_proceeds_after_flow(post_own_buy, inventory, lo, fee_event)
        if proceeds is not None and proceeds >= target:
            target_high = lo
    elif target_high is None:
        proceeds = external_proceeds_after_flow(post_own_buy, inventory, maximum, fee_event)
        if proceeds is not None and proceeds >= target:
            target_high = maximum

    if target_high is None:
        return None
    lo, hi = 0, target_high
    while lo < hi:
        mid = (lo + hi) // 2
        proceeds = external_proceeds_after_flow(post_own_buy, inventory, mid, fee_event)
        if proceeds is not None and proceeds >= target:
            hi = mid
        else:
            lo = mid + 1
    return lo / LAMPORTS


def labels_from_anchor(anchor_row: dict[str, Any], delivery_cost_sol: float) -> dict[str, Any]:
    cell = anchor_row.get("cells", {}).get("P50|0.5")
    if not cell or cell.get("status") != "EXACT":
        return {
            "POLICY_GE_10": None,
            "POLICY_POSITIVE": None,
            "OPPORTUNITY_GE_10": None,
            "policyReturnPct": None,
            "counterfactualDiverged": None,
        }
    capital = 500_000_000 + NETWORK_FEE
    overlay_pct = delivery_cost_sol * LAMPORTS * 100 / capital
    policy = (cell.get("policies") or {}).get("TP20_SL20_MAX120") or {}
    policy_return = None
    if policy.get("status") == "EXACT" and finite(policy.get("returnPct")):
        policy_return = float(policy["returnPct"]) - overlay_pct
    opportunity = (
        ((cell.get("opportunity") or {}).get(f"{delivery_cost_sol:.4f}") or {})
        .get("thresholds", {})
        .get("10", {})
        .get("reached")
    )
    return {
        "POLICY_GE_10": policy_return >= 10 if policy_return is not None else None,
        "POLICY_POSITIVE": policy_return > 0 if policy_return is not None else None,
        "OPPORTUNITY_GE_10": bool(opportunity) if opportunity is not None else None,
        "policyReturnPct": policy_return,
        "counterfactualDiverged": int(cell.get("counterfactualFailedTransactions") or 0) > 0,
    }


def extract_features(
    anchor_row: dict[str, Any],
    events: list[Any],
    delivery_cost_sol: float,
) -> tuple[dict[str, float | None], dict[str, Any]]:
    decision = int(anchor_row["decisionUnixMs"])
    eligible = [
        row
        for row in events
        if row.arrival_ms <= decision
        and row.event.quote_mint in SOL_QUOTES
        and not row.event.mayhem_mode
    ]
    eligible_groups = group_events(eligible)
    trigger_slot = int(anchor_row["triggerSlot"])
    trigger_index = next(
        (
            index
            for index, group in enumerate(eligible_groups)
            if group["signature"] == anchor_row["triggerSignature"]
            and group["slot"] == trigger_slot
        ),
        None,
    )
    visible_groups = eligible_groups[: trigger_index + 1] if trigger_index is not None else []
    visible = [row for group in visible_groups for row in group["rows"]]
    external = [row for row in visible if row.event.user != row.event.creator]
    external.sort(key=lambda row: (row.arrival_ms, row.slot, row.tx_index, row.instruction_index))
    buys = [row for row in external if row.event.is_buy]
    sells = [row for row in external if not row.event.is_buy]
    groups = group_events(external)
    signatures = [group["signature"] for group in groups]
    trigger_group = next(
        (group for group in groups if group["signature"] == anchor_row["triggerSignature"]),
        None,
    )
    create_ms = int(anchor_row["decisionUnixMs"]) - int(anchor_row["mintAgeMs"])
    causal_pass = trigger_index is not None and bool(visible) and all(
        0 < int(row.arrival_ms) <= decision and bool(row.observation_evidence_id)
        for row in visible
    )

    event_sequence = [row.event.is_buy for row in external]
    consecutive_buys = 0
    for is_buy in reversed(event_sequence):
        if not is_buy:
            break
        consecutive_buys += 1
    sell_then_buy = sum(
        (not previous) and current
        for previous, current in zip(event_sequence, event_sequence[1:])
    )
    group_times = [int(group["arrivalMs"]) for group in groups]
    gaps = [right - left for left, right in zip(group_times, group_times[1:])]
    buy_amounts = [float(row.event.immediate_quote_amount) / LAMPORTS for row in buys]
    split = max(1, len(buy_amounts) // 2)
    earlier = buy_amounts[:split]
    later = buy_amounts[split:]
    by_buyer: collections.Counter[str] = collections.Counter()
    for row in buys:
        by_buyer[row.event.user] += int(row.event.immediate_quote_amount)
    gross_buy_lamports = sum(by_buyer.values())
    gross_sell_lamports = sum(int(row.event.immediate_quote_amount) for row in sells)
    shares = sorted(
        (value / gross_buy_lamports for value in by_buyer.values()), reverse=True
    ) if gross_buy_lamports else []
    first_buyer = buys[0].event.user if buys else None
    first_share = by_buyer[first_buyer] / gross_buy_lamports if first_buyer and gross_buy_lamports else None
    buyer_totals_sol = [value / LAMPORTS for value in by_buyer.values()]

    features: dict[str, float | None] = {
        "external_buy_event_count": float(len(buys)),
        "external_sell_event_count": float(len(sells)),
        "successful_transaction_count": float(len(set(signatures))),
        "consecutive_buy_events_at_anchor": float(consecutive_buys),
        "sell_then_renewed_buy_count": float(sell_then_buy),
        "median_event_interarrival_ms": statistics.median(gaps) if gaps else None,
        "last_event_interarrival_ms": float(gaps[-1]) if gaps else None,
        "buy_size_progression_ratio": safe_ratio(statistics.mean(later), statistics.mean(earlier)) if later and earlier else None,
        "last_buy_size_vs_prior_median": safe_ratio(buy_amounts[-1], statistics.median(buy_amounts[:-1])) if len(buy_amounts) >= 2 else None,
        "external_buy_size_cv": coefficient_of_variation(buy_amounts),
        "unique_external_buyers": float(len(by_buyer)),
        "repeat_buy_event_count": float(max(0, len(buys) - len(by_buyer))),
        "repeat_buy_fraction": safe_ratio(max(0, len(buys) - len(by_buyer)), len(buys)),
        "first_buyer_share": first_share,
        "largest_buyer_share": shares[0] if shares else None,
        "top2_buyer_share": sum(shares[:2]) if shares else None,
        "top3_buyer_share": sum(shares[:3]) if shares else None,
        "buyer_hhi": sum(value * value for value in shares) if shares else None,
        "buyer_size_cv": coefficient_of_variation(buyer_totals_sol),
        "mint_age_ms": float(anchor_row["mintAgeMs"]),
        "first_external_buy_to_anchor_ms": float(decision - buys[0].arrival_ms) if buys else None,
        "actual_cumulative_external_gross_buy_sol": gross_buy_lamports / LAMPORTS,
        "overshoot_sol": float(anchor_row["anchorMetadata"]["overshootSol"]),
        "external_gross_sell_sol": gross_sell_lamports / LAMPORTS,
        "net_external_flow_sol": (gross_buy_lamports - gross_sell_lamports) / LAMPORTS,
        "gross_buy_sol_per_successful_tx": safe_ratio(gross_buy_lamports / LAMPORTS, len(set(signatures))),
        "decision_virtual_sol": int(anchor_row["anchorMetadata"]["decisionState"]["virtualSol"]) / LAMPORTS,
        "decision_real_sol": int(anchor_row["anchorMetadata"]["decisionState"]["realSol"]) / LAMPORTS,
        "decision_real_token": float(anchor_row["anchorMetadata"]["decisionState"]["realToken"]),
        "real_token_remaining_fraction": None,
        "hypothetical_entry_impact_bps": None,
        "hypothetical_inventory_raw": None,
        "immediate_roundtrip_return_pct": None,
        "curve_gross_sol_distance_to_plus5": None,
        "curve_gross_sol_distance_to_plus10": None,
        "trailing15m_anchor_speed_percentile": None,
        "trailing15m_unique_buyers_percentile": None,
        "trailing15m_tx_intensity_percentile": None,
        "trailing15m_external_flow_percentile": None,
    }

    if visible:
        initial_real_token = int(visible[0].event.pre_state.real_token)
        current_real_token = int(anchor_row["anchorMetadata"]["decisionState"]["realToken"])
        features["real_token_remaining_fraction"] = safe_ratio(current_real_token, initial_real_token)

    exact_state_status = "TRIGGER_GROUP_UNAVAILABLE"
    if trigger_group is not None:
        try:
            state = state_from_dict(anchor_row["anchorMetadata"]["decisionState"])
            fee_event = trigger_group["feeEvent"]
            own_buy = quote_exact_quote_in_buy(state, 500_000_000, event_fees(fee_event))
            no_impact = own_buy.pricing_quote_in * state.virtual_token / state.virtual_sol
            features["hypothetical_entry_impact_bps"] = (
                (1.0 - own_buy.token_out / no_impact) * 10_000 if no_impact else None
            )
            features["hypothetical_inventory_raw"] = float(own_buy.token_out)
            immediate = quote_exact_token_sell(
                own_buy.post_state,
                own_buy.token_out,
                1,
                event_fees(fee_event),
                cashback_bps=int(fee_event.cashback_fee_basis_points or 0),
                buyback_share_bps=int(fee_event.buyback_fee_basis_points or 0),
            )
            capital = 500_000_000 + NETWORK_FEE
            features["immediate_roundtrip_return_pct"] = (
                (immediate.immediate_quote_out - capital) * 100 / capital
            )
            features["curve_gross_sol_distance_to_plus5"] = gross_external_distance_to_return(
                own_buy.post_state, own_buy.token_out, fee_event, 5.0, delivery_cost_sol
            )
            features["curve_gross_sol_distance_to_plus10"] = gross_external_distance_to_return(
                own_buy.post_state, own_buy.token_out, fee_event, 10.0, delivery_cost_sol
            )
            exact_state_status = "EXACT_CAUSAL"
        except (ExactCurveError, ValueError, ZeroDivisionError, OverflowError) as error:
            exact_state_status = f"EXACT_STATE_UNAVAILABLE:{type(error).__name__}"

    return features, {
        "featureAvailableTimestamp": decision,
        "decisionTimestamp": decision,
        "causalAvailabilityPass": causal_pass,
        "visibleEventN": len(visible),
        "externalEventN": len(external),
        "sourceEvidenceIds": sorted({row.observation_evidence_id for row in visible if row.observation_evidence_id}),
        "maxSourceArrivalMs": max((row.arrival_ms for row in visible), default=None),
        "postTriggerRowsExcluded": len(eligible) - len(visible),
        "sameTimestampPostTriggerRowsExcluded": (
            sum(row.arrival_ms == decision for row in eligible)
            - sum(row.arrival_ms == decision for row in visible)
        ) if trigger_index is not None else None,
        "exactEconomicFeatureStatus": exact_state_status,
        "createObservationMs": create_ms,
    }


def trailing_percentile(value: float, priors: list[float]) -> float | None:
    if not priors:
        return None
    below = sum(item < value for item in priors)
    equal = sum(item == value for item in priors)
    return (below + 0.5 * equal) / len(priors)


def add_trailing_context(rows: list[dict[str, Any]], window_ms: int, minimum_n: int) -> None:
    by_anchor: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_anchor[row["anchor"]].append(row)
    mappings = {
        "trailing15m_anchor_speed_percentile": ("mint_age_ms", -1.0),
        "trailing15m_unique_buyers_percentile": ("unique_external_buyers", 1.0),
        "trailing15m_tx_intensity_percentile": ("successful_transaction_count", 1.0),
        "trailing15m_external_flow_percentile": ("actual_cumulative_external_gross_buy_sol", 1.0),
    }
    for values in by_anchor.values():
        values.sort(key=lambda row: (row["decisionUnixMs"], row["mint"]))
        history: list[dict[str, Any]] = []
        index = 0
        while index < len(values):
            timestamp = values[index]["decisionUnixMs"]
            end = index
            while end < len(values) and values[end]["decisionUnixMs"] == timestamp:
                end += 1
            priors = [
                prior for prior in history
                if timestamp - window_ms <= prior["decisionUnixMs"] < timestamp
            ]
            for row in values[index:end]:
                for output_id, (source_id, multiplier) in mappings.items():
                    source = row["features"].get(source_id)
                    prior_values = [
                        float(prior["features"][source_id]) * multiplier
                        for prior in priors if finite(prior["features"].get(source_id))
                    ]
                    if finite(source) and len(prior_values) >= minimum_n:
                        row["features"][output_id] = trailing_percentile(float(source) * multiplier, prior_values)
            history.extend(values[index:end])
            index = end


def average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for position in range(start, end):
            ranks[order[position]] = rank
        start = end
    return ranks


def auc_and_pvalue(values: list[float], labels: list[bool]) -> tuple[float | None, float | None]:
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None, None
    ranks = average_ranks(values)
    rank_sum = sum(rank for rank, label in zip(ranks, labels) if label)
    u = rank_sum - positives * (positives + 1) / 2
    auc = u / (positives * negatives)
    counts = collections.Counter(values)
    n = len(values)
    tie_term = sum(count ** 3 - count for count in counts.values())
    variance = positives * negatives / 12 * (
        n + 1 - tie_term / (n * (n - 1))
    ) if n > 1 else 0
    if variance <= 0:
        return auc, 1.0
    z = (u - positives * negatives / 2) / math.sqrt(variance)
    return auc, math.erfc(abs(z) / math.sqrt(2))


def bootstrap_auc_ci(
    values: list[float], labels: list[bool], direction: str, replicates: int, seed: int
) -> list[float | None]:
    rng = random.Random(seed)
    samples = []
    n = len(values)
    for _ in range(replicates):
        indexes = [rng.randrange(n) for _ in range(n)]
        auc, _ = auc_and_pvalue([values[index] for index in indexes], [labels[index] for index in indexes])
        if auc is not None:
            samples.append(auc if direction == "HIGHER" else 1.0 - auc)
    return [percentile(samples, 0.025), percentile(samples, 0.975)]


def decile_view(values: list[float], labels: list[bool]) -> list[dict[str, Any]]:
    ordered = sorted(zip(values, labels), key=lambda item: item[0])
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(10)]
    for index, item in enumerate(ordered):
        bin_index = min(9, index * 10 // len(ordered))
        bins[bin_index].append(item)
    output = []
    for index, bucket in enumerate(bins, 1):
        output.append({
            "decile": index,
            "N": len(bucket),
            "min": min((item[0] for item in bucket), default=None),
            "max": max((item[0] for item in bucket), default=None),
            "positiveRate": sum(item[1] for item in bucket) / len(bucket) if bucket else None,
        })
    return output


def spearman_bins(deciles: list[dict[str, Any]]) -> float | None:
    usable = [row for row in deciles if row["positiveRate"] is not None]
    if len(usable) < 2:
        return None
    x = [float(row["decile"]) for row in usable]
    y = [float(row["positiveRate"]) for row in usable]
    rx, ry = average_ranks(x), average_ranks(y)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    numerator = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denominator = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return numerator / denominator if denominator else None


def enrichment(values: list[float], labels: list[bool]) -> dict[str, Any]:
    ordered = sorted(zip(values, labels), key=lambda item: item[0])
    tail_n = max(1, math.ceil(len(ordered) * 0.10))
    low, high = ordered[:tail_n], ordered[-tail_n:]
    overall = sum(labels) / len(labels)
    low_rate = sum(item[1] for item in low) / len(low)
    high_rate = sum(item[1] for item in high) / len(high)
    return {
        "overallPositiveRate": overall,
        "topDecileN": len(high),
        "topDecilePositiveRate": high_rate,
        "topDecileEnrichment": high_rate / overall if overall else None,
        "bottomDecileN": len(low),
        "bottomDecilePositiveRate": low_rate,
        "bottomDecileEnrichment": low_rate / overall if overall else None,
    }


def block_for_ordinal(ordinal: int, blocks: list[list[int]]) -> int | None:
    for index, (start, end) in enumerate(blocks, 1):
        if start <= ordinal <= end:
            return index
    return None


def analyze_feature(
    population: list[dict[str, Any]],
    feature: dict[str, Any],
    target: str,
    blocks: list[list[int]],
    bootstrap_replicates: int,
    bootstrap_seed: int,
    nondivergent_only: bool = False,
) -> dict[str, Any]:
    label_population = [row for row in population if row["labels"].get(target) is not None]
    if nondivergent_only:
        label_population = [row for row in label_population if not row["labels"].get("counterfactualDiverged")]
    usable = [
        row for row in label_population
        if finite(row["features"].get(feature["id"]))
    ]
    values = [float(row["features"][feature["id"]]) for row in usable]
    labels = [bool(row["labels"][target]) for row in usable]
    auc, pvalue = auc_and_pvalue(values, labels)
    direction = "HIGHER" if auc is not None and auc >= 0.5 else "LOWER"
    directed_auc = max(auc, 1.0 - auc) if auc is not None else None
    enrich = enrichment(values, labels) if values and 0 < sum(labels) < len(labels) else {}
    deciles = decile_view(values, labels) if values else []
    chronological = []
    stable_usable = 0
    stable_matching = 0
    for index, boundary in enumerate(blocks, 1):
        subset = [row for row in usable if boundary[0] <= int(row["ordinal"]) <= boundary[1]]
        block_values = [float(row["features"][feature["id"]]) for row in subset]
        block_labels = [bool(row["labels"][target]) for row in subset]
        block_auc, _ = auc_and_pvalue(block_values, block_labels)
        oriented = None if block_auc is None else (block_auc if direction == "HIGHER" else 1 - block_auc)
        if oriented is not None:
            stable_usable += 1
            stable_matching += oriented >= 0.5
        block_enrichment = enrichment(block_values, block_labels) if block_values and 0 < sum(block_labels) < len(block_labels) else {}
        chronological.append({
            "block": index,
            "ordinalStart": boundary[0],
            "ordinalEnd": boundary[1],
            "usableN": len(subset),
            "positiveN": sum(block_labels),
            "rawAuc": block_auc,
            "orientedAuc": oriented,
            "topDecileEnrichment": block_enrichment.get("topDecileEnrichment"),
            "bottomDecileEnrichment": block_enrichment.get("bottomDecileEnrichment"),
        })
    positive_values = [value for value, label in zip(values, labels) if label]
    negative_values = [value for value, label in zip(values, labels) if not label]
    divergence_positive = [row for row in usable if row["labels"][target]]
    divergence_negative = [row for row in usable if not row["labels"][target]]
    seed_digest = int(hashlib.sha256(f"{bootstrap_seed}|{feature['id']}|{target}|{nondivergent_only}".encode()).hexdigest()[:16], 16)
    return {
        "feature": feature["id"],
        "family": feature["family"],
        "target": target,
        "labelN": len(label_population),
        "usableN": len(usable),
        "missingN": len(label_population) - len(usable),
        "missingRate": (len(label_population) - len(usable)) / len(label_population) if label_population else None,
        "positiveN": sum(labels),
        "negativeN": len(labels) - sum(labels),
        "positiveDistribution": distribution(positive_values),
        "negativeDistribution": distribution(negative_values),
        "rawAuc": auc,
        "aucDirection": direction,
        "directedAuc": directed_auc,
        "directedAucMintBootstrap95CI": bootstrap_auc_ci(values, labels, direction, bootstrap_replicates, seed_digest) if auc is not None else [None, None],
        "mannWhitneyTwoSidedP": pvalue,
        "benjaminiHochbergQ": None,
        "cliffsDelta": 2 * auc - 1 if auc is not None else None,
        **enrich,
        "signalTail": "TOP_DECILE" if direction == "HIGHER" else "BOTTOM_DECILE",
        "signalTailPositiveRate": enrich.get("topDecilePositiveRate") if direction == "HIGHER" else enrich.get("bottomDecilePositiveRate"),
        "signalTailEnrichment": enrich.get("topDecileEnrichment") if direction == "HIGHER" else enrich.get("bottomDecileEnrichment"),
        "deciles": deciles,
        "decilePositiveRateSpearman": spearman_bins(deciles),
        "chronologicalBlocks": chronological,
        "directionStableBlocks": stable_matching,
        "directionUsableBlocks": stable_usable,
        "divergenceRatePositive": sum(bool(row["labels"].get("counterfactualDiverged")) for row in divergence_positive) / len(divergence_positive) if divergence_positive else None,
        "divergenceRateNegative": sum(bool(row["labels"].get("counterfactualDiverged")) for row in divergence_negative) / len(divergence_negative) if divergence_negative else None,
        "nonDivergentOnly": nondivergent_only,
    }


def apply_bh(results: list[dict[str, Any]]) -> None:
    usable = [row for row in results if finite(row.get("mannWhitneyTwoSidedP"))]
    ordered = sorted(usable, key=lambda row: row["mannWhitneyTwoSidedP"])
    m = len(ordered)
    running = 1.0
    for reverse_index in range(m - 1, -1, -1):
        rank = reverse_index + 1
        value = min(1.0, ordered[reverse_index]["mannWhitneyTwoSidedP"] * m / rank)
        running = min(running, value)
        ordered[reverse_index]["benjaminiHochbergQ"] = running


def write_gzip_rows(path: pathlib.Path, header: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"header": header}, sort_keys=True, separators=(",", ":")) + "\n")
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def read_gzip_rows(path: pathlib.Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        header = json.loads(next(handle))["header"]
        rows = [json.loads(line) for line in handle if line.strip()]
    return header, rows


def compact_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# SAME_ANCHOR_CAUSAL_SEPARABILITY_V1",
        "",
        f"Contract SHA256: `{report['contractSha256']}`",
        f"Feature registry SHA256: `{report['featureRegistrySha256']}`",
        "",
        "This is a discovery-only univariate separability report, not a strategy or threshold search.",
        "",
        "## Population",
        "",
        "| Anchor | N | Policy >=10 | Policy positive | Opportunity >=10 |",
        "|---|---:|---:|---:|---:|",
    ]
    for anchor in ANCHORS:
        row = report["populations"][anchor]
        lines.append(
            f"| {anchor} | {row['N']} | {row['POLICY_GE_10']} | {row['POLICY_POSITIVE']} | {row['OPPORTUNITY_GE_10']} |"
        )
    lines += ["", "## Primary Anchor: strongest univariate POLICY_GE_10 associations", ""]
    lines += ["| Feature | Family | Coverage | Direction | AUC | 95% CI | Tail enrichment | Stable blocks | FDR q |", "|---|---|---:|---|---:|---:|---:|---:|---:|"]
    primary = report["results"]["EXTERNAL_GROSS_BUY_GE_2_SOL"]["POLICY_GE_10"][:15]
    for row in primary:
        ci = row["directedAucMintBootstrap95CI"]
        lines.append(
            f"| {row['feature']} | {row['family']} | {1-(row['missingRate'] or 0):.1%} | {row['aucDirection']} | "
            f"{(row['directedAuc'] or 0):.3f} | {(ci[0] or 0):.3f}-{(ci[1] or 0):.3f} | "
            f"{(row.get('signalTailEnrichment') or 0):.2f}x | {row['directionStableBlocks']}/{row['directionUsableBlocks']} | "
            f"{(row.get('benjaminiHochbergQ') or 1):.3g} |"
        )
    lines += [
        "",
        "No model, feature combination, trading threshold, shadow lane, or live submission was produced.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=pathlib.Path)
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("anchor_root", type=pathlib.Path)
    parser.add_argument("contract", type=pathlib.Path)
    parser.add_argument("feature_registry", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    registry = json.loads(args.feature_registry.read_text(encoding="utf-8"))
    if contract.get("schema") != SCHEMA + "_CONTRACT":
        raise RuntimeError("CONTRACT_SCHEMA_MISMATCH")
    if registry.get("schema") != SCHEMA + "_FEATURE_REGISTRY":
        raise RuntimeError("FEATURE_REGISTRY_SCHEMA_MISMATCH")
    if sha256_file(args.anchor_root / "contract.json") != contract["parents"]["lifecycleAnchorContractSha256"]:
        raise RuntimeError("ANCHOR_CONTRACT_HASH_MISMATCH")
    if sha256_file(args.anchor_root / "report.json") != contract["parents"]["lifecycleAnchorReportSha256"]:
        raise RuntimeError("ANCHOR_REPORT_HASH_MISMATCH")
    if sha256_file(args.anchor_root / "provenance.json") != contract["parents"]["lifecycleAnchorProvenanceSha256"]:
        raise RuntimeError("ANCHOR_PROVENANCE_HASH_MISMATCH")

    contract_sha = sha256_file(args.contract)
    registry_sha = sha256_file(args.feature_registry)
    script_sha = sha256_file(pathlib.Path(__file__))
    boundary = int(contract["population"]["discoveryOrdinalEnd"])
    prefix, ordinals = load_prefix(args.research_root, boundary)
    prefix_sha = hashlib.sha256(
        "\n".join(f"{row['ordinal']}:{row['mint']}" for row in prefix).encode()
    ).hexdigest()
    if prefix_sha != contract["parents"]["discoveryPrefixSha256"]:
        raise RuntimeError(f"DISCOVERY_PREFIX_HASH_MISMATCH:{prefix_sha}")

    identity = {
        "schema": SCHEMA + "_RUN_IDENTITY",
        "contractSha256": contract_sha,
        "featureRegistrySha256": registry_sha,
        "scriptSha256": script_sha,
        "discoveryPrefixSha256": prefix_sha,
        "boundaryOrdinal": boundary,
        "anchorReportSha256": contract["parents"]["lifecycleAnchorReportSha256"],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    identity_path = args.output / "run-identity.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise RuntimeError("IMMUTABLE_RUN_IDENTITY_MISMATCH")
    identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output / "contract.json").write_bytes(args.contract.read_bytes())
    (args.output / "feature-registry.json").write_bytes(args.feature_registry.read_bytes())

    anchor_provenance = json.loads((args.anchor_root / "provenance.json").read_text())
    manifests = []
    all_rows = []
    primary_cost = float(contract["economics"]["standardizedDeliveryCostSol"])
    for item in anchor_provenance["partitions"]:
        key = item["partitionKey"]
        key_file = key.replace("/", "-")
        destination = args.output / "partitions" / f"{key_file}.jsonl.gz"
        source_path = pathlib.Path(item["path"])
        readiness_path = args.evidence_root / "readiness-v1" / "partitions" / f"{key_file}-v4.json"
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        if destination.exists():
            header, rows = read_gzip_rows(destination)
            if header.get("contractSha256") != contract_sha or header.get("featureRegistrySha256") != registry_sha or header.get("anchorPartitionSha256") != item["sha256"]:
                raise RuntimeError(f"IMMUTABLE_PARTITION_MISMATCH:{key}")
            all_rows.extend(rows)
            manifests.append(header | {"path": str(destination), "sha256": sha256_file(destination), "reused": True})
            continue
        # Baseline files contain multiple rows per mint, so load them without mint-key collapsing.
        with gzip.open(source_path, "rt", encoding="utf-8") as handle:
            next(handle)
            source_rows = [json.loads(line) for line in handle if line.strip()]
        selected_rows = [row for row in source_rows if row["anchor"] in ANCHORS]
        targets = {row["mint"] for row in selected_rows}
        events_by_mint, counters = collect_partition(args.evidence_root, readiness, targets)
        rows = []
        for anchor_row in selected_rows:
            mint = anchor_row["mint"]
            features, availability = extract_features(anchor_row, events_by_mint.get(mint, []), primary_cost)
            rows.append({
                "schema": SCHEMA + "_FEATURE_ROW",
                "mint": mint,
                "ordinal": int(anchor_row["ordinal"]),
                "anchor": anchor_row["anchor"],
                "decisionUnixMs": int(anchor_row["decisionUnixMs"]),
                "decisionUtc": anchor_row["decisionUtc"],
                "triggerSignature": anchor_row["triggerSignature"],
                "features": features,
                "labels": labels_from_anchor(anchor_row, primary_cost),
                "availability": availability,
                "anchorParentEvidenceIds": anchor_row.get("parentEvidenceIds", []),
            })
        header = {
            "schema": SCHEMA + "_PARTITION",
            "partitionKey": key,
            "contractSha256": contract_sha,
            "featureRegistrySha256": registry_sha,
            "anchorPartitionSha256": item["sha256"],
            "sourceReconciliationManifestSha256": readiness.get("sourceReconciliationManifestSha256"),
            "rowCount": len(rows),
            "collectionCounters": counters,
        }
        write_gzip_rows(destination, header, rows)
        all_rows.extend(rows)
        manifests.append(header | {"path": str(destination), "sha256": sha256_file(destination), "reused": False})
        print(json.dumps({"partition": key, "rows": len(rows), "totalRows": len(all_rows)}), flush=True)

    add_trailing_context(
        all_rows,
        int(contract["analysis"]["trailingContextWindowMs"]),
        int(contract["analysis"]["trailingContextMinimumPriorN"]),
    )
    analysis_rows_path = args.output / "analysis-rows.jsonl.gz"
    write_gzip_rows(analysis_rows_path, {
        "schema": SCHEMA + "_ANALYSIS_ROWS",
        "contractSha256": contract_sha,
        "featureRegistrySha256": registry_sha,
        "rowCount": len(all_rows),
    }, sorted(all_rows, key=lambda row: (row["ordinal"], row["anchor"])))

    tested_features = [row for row in registry["features"] if row.get("tested")]
    excluded_features = [row for row in registry["features"] if not row.get("tested")]
    populations = {anchor: [row for row in all_rows if row["anchor"] == anchor] for anchor in ANCHORS}
    results: dict[str, Any] = {}
    blocks = contract["analysis"]["chronologicalBlocks"]
    replicates = int(contract["analysis"]["bootstrapReplicates"])
    seed = int(contract["analysis"]["bootstrapSeed"])
    for anchor, population in populations.items():
        results[anchor] = {}
        for target in ("POLICY_GE_10", "POLICY_POSITIVE", "OPPORTUNITY_GE_10"):
            target_results = [
                analyze_feature(population, feature, target, blocks, replicates, seed)
                for feature in tested_features
            ]
            apply_bh(target_results)
            for row in target_results:
                feature = next(item for item in tested_features if item["id"] == row["feature"])
                row["nonDivergentSensitivity"] = analyze_feature(
                    population, feature, target, blocks, replicates, seed, True
                )
            target_results.sort(key=lambda row: (-(row["directedAuc"] or 0), row["feature"]))
            results[anchor][target] = target_results

    population_summary = {}
    for anchor, population in populations.items():
        population_summary[anchor] = {
            "N": len(population),
            "uniqueMints": len({row["mint"] for row in population}),
            "POLICY_GE_10": sum(row["labels"]["POLICY_GE_10"] is True for row in population),
            "POLICY_POSITIVE": sum(row["labels"]["POLICY_POSITIVE"] is True for row in population),
            "OPPORTUNITY_GE_10": sum(row["labels"]["OPPORTUNITY_GE_10"] is True for row in population),
            "causalAvailabilityFailures": sum(not row["availability"]["causalAvailabilityPass"] for row in population),
            "divergentN": sum(row["labels"].get("counterfactualDiverged") is True for row in population),
        }

    family_counts = collections.Counter(row["family"] for row in tested_features)
    excluded_family_counts = collections.Counter(row["family"] for row in excluded_features)
    mint_anchor_counts = collections.Counter((row["mint"], row["anchor"]) for row in all_rows)
    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": iso_now(),
        **identity,
        "populationRoles": contract["anchors"],
        "populations": population_summary,
        "featureAudit": {
            "testedFeatureN": len(tested_features),
            "testedFamilyCounts": dict(family_counts),
            "excludedFeatureN": len(excluded_features),
            "excludedFamilyCounts": dict(excluded_family_counts),
            "excludedFeatures": excluded_features,
        },
        "results": results,
        "overlap": {
            "ge1Ge2": len({row["mint"] for row in populations[ANCHORS[0]]} & {row["mint"] for row in populations[ANCHORS[1]]}),
            "ge2Ge5": len({row["mint"] for row in populations[ANCHORS[1]]} & {row["mint"] for row in populations[ANCHORS[2]]}),
            "allThree": len(set.intersection(*({row["mint"] for row in populations[anchor]} for anchor in ANCHORS))),
            "warning": "Anchor populations overlap and are never pooled as independent samples.",
        },
        "integrity": {
            "featureRows": len(all_rows),
            "uniqueMintAnchorRows": len(mint_anchor_counts),
            "duplicateMintAnchorRows": [f"{mint}|{anchor}" for (mint, anchor), count in mint_anchor_counts.items() if count != 1],
            "ordinalsOutsideDiscovery": sorted({row["ordinal"] for row in all_rows if not 1 <= row["ordinal"] <= boundary}),
            "validationRowsInspected": 0,
            "holdoutRowsInspected": 0,
            "thresholdsSearched": 0,
            "featurePairsTested": 0,
            "modelsTrained": 0,
            "liveOrShadowChanges": 0,
        },
    }
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    (args.output / "report.md").write_text(compact_markdown(report), encoding="utf-8")
    provenance = {
        "schema": SCHEMA + "_PROVENANCE",
        **identity,
        "reportSha256": sha256_file(report_path),
        "analysisRowsSha256": sha256_file(analysis_rows_path),
        "partitions": manifests,
        "validationRowsInspected": 0,
        "holdoutRowsInspected": 0,
    }
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"complete": True, "rows": len(all_rows), "report": str(report_path)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
