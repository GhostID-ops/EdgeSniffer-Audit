#!/usr/bin/env python3
"""Outcome-blind causal entry-timing study for the frozen 2CQ wallet cohort."""

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
import statistics
from dataclasses import dataclass
from typing import Any, Iterable

from buildPumpExactAlphaResearchV1 import paths_for_report
from exactCurveEconomicsV1 import CurveState, decode_trade_events
from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "2CQ_CAUSAL_ENTRY_TIMING_FINGERPRINT_V1"
WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"
WINDOW_START = "2026-09-01T00:00:00Z"
WINDOW_END = "2026-09-08T00:00:00Z"
SIZE_BOUNDARIES = (1.4907741468071136, 2.3847213307824298, 3.8559888232072037, 5.558985446102086)
TRADE_KINDS = {"BUY", "BUY_EXACT_SOL_IN", "BUY_V2", "BUY_EXACT_QUOTE_IN_V2", "SELL", "SELL_V2"}
PROGRESSION_FEATURES = (
    "log_age_ms", "log_event_index", "curve_progress", "log_market_cap_sol",
    "distance_from_launch_log_price",
)
FLOW_FEATURES = (
    "gross_buy_sol_250ms", "gross_sell_sol_250ms", "net_flow_sol_250ms",
    "buy_count_250ms", "sell_count_250ms", "gross_buy_sol_1000ms",
    "gross_sell_sol_1000ms", "net_flow_sol_1000ms", "buy_count_1000ms",
    "sell_count_1000ms", "gross_buy_sol_2000ms", "gross_sell_sol_2000ms",
    "net_flow_sol_2000ms", "gross_buy_sol_5000ms", "gross_sell_sol_5000ms",
    "net_flow_sol_5000ms", "total_external_buy_sol", "total_external_sell_sol",
    "total_external_net_sol", "buy_sell_imbalance_1000ms", "largest_buy_sol_1000ms",
    "mean_buy_sol_1000ms", "net_acceleration_1s", "buy_count_acceleration_1s",
)
PARTICIPANT_FEATURES = (
    "unique_buyers_1000ms", "unique_sellers_1000ms", "repeat_buyer_share_1000ms",
    "buyer_hhi_1000ms", "top_buyer_share_1000ms", "creator_buy_sol_5000ms",
    "creator_participated", "unique_buyers_total", "unique_sellers_total",
)
SEQUENCE_FEATURES = (
    "last_external_side", "last_external_trade_sol", "consecutive_buys",
    "consecutive_sells", "log_ms_since_last_external", "log_median_interarrival_ms_5s",
    "log_price_change_250ms", "log_price_change_1000ms", "log_price_change_2000ms",
    "log_price_change_5000ms",
)
FULL_FEATURES = PROGRESSION_FEATURES + FLOW_FEATURES + PARTICIPANT_FEATURES + SEQUENCE_FEATURES


@dataclass(frozen=True)
class Event:
    mint: str
    signature: str
    slot: int
    transaction_index: int
    instruction_index: int
    observed_ms: int
    evidence_id: str
    is_buy: bool
    user: str
    creator: str
    quote_lamports: int
    token_raw: int
    pre_state: CurveState
    post_state: CurveState

    @property
    def order(self) -> tuple[int, int, int, str]:
        return (self.slot, self.transaction_index, self.instruction_index, self.signature)


def utc(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def atomic_write(path: pathlib.Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)


def load_episodes(path: pathlib.Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scope = 0
    accepted: list[dict[str, Any]] = []
    exclusions: collections.Counter[str] = collections.Counter()
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            episode = json.loads(line)
            if not episode.get("insideFrozenWindow"):
                continue
            scope += 1
            reasons: list[str] = []
            if not episode.get("pumpRelevant"):
                reasons.append("NOT_PUMP_RELEVANT")
            if episode.get("leftCensored"):
                reasons.append("LEFT_CENSORED")
            if int(episode.get("unknownOpeningInventoryRaw") or 0):
                reasons.append("UNKNOWN_OPENING_INVENTORY")
            if not episode.get("entrySignature"):
                reasons.append("ENTRY_SIGNATURE_MISSING")
            if episode.get("entryVenue") != "PUMP_BONDING_CURVE":
                reasons.append("ENTRY_NOT_PUMP_CURVE")
            entry_action = next(
                (row for row in episode.get("actions") or [] if row.get("signature") == episode.get("entrySignature")),
                None,
            )
            if not entry_action or entry_action.get("kind") != "ECONOMIC_BUY":
                reasons.append("FIRST_ACTION_NOT_ECONOMIC_BUY")
            if reasons:
                exclusions.update(set(reasons))
                continue
            accepted.append(episode)
    return accepted, {
        "frozenWindowEpisodeN": scope,
        "structurallyEntryUsableEpisodeN": len(accepted),
        "structurallyEntryUsableUniqueMints": len({row["mint"] for row in accepted}),
        "exclusionReasonCountsNonExclusive": dict(exclusions),
    }


def load_mint_index(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    with path.open("rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            index[row["mint"]] = row
    return index


def readiness_reports(root: pathlib.Path) -> dict[str, dict[str, Any]]:
    result = {}
    for path in sorted((root / "readiness-v1/partitions").glob("*-v4.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        result[str(row["partitionKey"])] = row
    return result


def scan_exact_events(
    evidence_root: pathlib.Path,
    target_index: dict[str, dict[str, Any]],
    entry_max_slots: dict[str, int],
    cache_path: pathlib.Path,
) -> tuple[dict[str, list[Event]], dict[str, Any]]:
    target_identity = hashlib.sha256(canonical({
        mint: {"partitionKey": target_index[mint]["partitionKey"], "maxEntrySlot": entry_max_slots[mint]}
        for mint in sorted(target_index)
    })).hexdigest()
    if cache_path.exists():
        events: dict[str, list[Event]] = collections.defaultdict(list)
        with gzip.open(cache_path, "rt", encoding="utf-8") as stream:
            header = json.loads(next(stream))
            if header.get("targetIdentitySha256") != target_identity:
                raise RuntimeError("causal event cache target identity mismatch")
            for line in stream:
                row = json.loads(line)
                events[row["mint"]].append(Event(
                    mint=row["mint"], signature=row["signature"], slot=row["slot"],
                    transaction_index=row["transactionIndex"], instruction_index=row["instructionIndex"],
                    observed_ms=row["observedUnixMs"], evidence_id=row["evidenceId"],
                    is_buy=row["isBuy"], user=row["user"], creator=row["creator"],
                    quote_lamports=row["quoteLamports"], token_raw=row["tokenRaw"],
                    pre_state=CurveState(**row["preState"]), post_state=CurveState(**row["postState"]),
                ))
        return dict(events), header["evidenceSummary"] | {"loadedFromCache": True}
    target_mints = set(target_index)
    reports = readiness_reports(evidence_root)
    selected_reports = []
    missing_readiness = []
    incomplete_readiness = []
    evidence_paths: set[pathlib.Path] = set()
    for partition in sorted({str(row["partitionKey"]) for row in target_index.values()}):
        report = reports.get(partition)
        if report is None:
            missing_readiness.append(partition)
            continue
        if report.get("sourceExactReplayComplete") is not True:
            incomplete_readiness.append(partition)
            continue
        selected_reports.append(report)
        evidence_paths.update(paths_for_report(evidence_root, report))

    arrivals: dict[str, tuple[int, str]] = {}
    provisional_events: list[Event] = []
    create_observations: dict[str, tuple[int, str]] = {}
    decode_failures: list[dict[str, str]] = []
    malformed_frames = 0
    for number, path in enumerate(sorted(evidence_paths), 1):
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            related = target_mints.intersection(metadata.get("discoveredMints") or [])
            signature = str(metadata.get("signature") or "")
            observed_ms = int(metadata.get("receivedAtUnixNs") or 0) // 1_000_000
            evidence_id = str(metadata.get("evidenceId") or "")
            kinds = set(metadata.get("pumpInstructionKinds") or [])
            if related and kinds.intersection({"CREATE", "CREATE_V2"}) and observed_ms:
                for mint in related:
                    current = create_observations.get(mint)
                    candidate = (observed_ms, evidence_id)
                    if current is None or candidate < current:
                        create_observations[mint] = candidate
            if metadata.get("kind") == "transaction" and signature and observed_ms and kinds.intersection(TRADE_KINDS):
                current = arrivals.get(signature)
                candidate = (observed_ms, evidence_id)
                if current is None or candidate < current:
                    arrivals[signature] = candidate
            elif metadata.get("kind") == "rpc_finalized_transaction" and signature and related:
                try:
                    transaction = json.loads(raw).get("result")
                    if not transaction or (transaction.get("meta") or {}).get("err") is not None:
                        continue
                    transaction_slot = int(metadata.get("slot") or transaction.get("slot") or 0)
                    if all(transaction_slot > entry_max_slots.get(mint, -1) for mint in related):
                        continue
                    decoded = decode_trade_events((transaction.get("meta") or {}).get("logMessages") or [])
                    tx_index = int(metadata.get("transactionIndex") if metadata.get("transactionIndex") is not None else -1)
                    for instruction_index, event in enumerate(decoded):
                        if event.mint not in target_mints or transaction_slot > entry_max_slots.get(event.mint, -1):
                            continue
                        provisional_events.append(Event(
                            mint=event.mint, signature=signature,
                            slot=transaction_slot,
                            transaction_index=tx_index, instruction_index=instruction_index,
                            observed_ms=0, evidence_id=str(metadata.get("evidenceId") or ""),
                            is_buy=bool(event.is_buy), user=event.user, creator=event.creator,
                            quote_lamports=int(event.immediate_quote_amount), token_raw=int(event.token_amount),
                            pre_state=event.pre_state, post_state=event.post_state,
                        ))
                except Exception as error:
                    decode_failures.append({"signature": signature, "error": str(error)})
        print(json.dumps({"phase": "scan_exact", "path": str(path), "index": number, "total": len(evidence_paths)}), flush=True)

    events: dict[str, list[Event]] = collections.defaultdict(list)
    missing_arrival = 0
    for event in provisional_events:
        arrival = arrivals.get(event.signature)
        if arrival is None:
            missing_arrival += 1
            continue
        events[event.mint].append(Event(
            mint=event.mint, signature=event.signature, slot=event.slot,
            transaction_index=event.transaction_index, instruction_index=event.instruction_index,
            observed_ms=arrival[0], evidence_id=event.evidence_id or arrival[1],
            is_buy=event.is_buy, user=event.user, creator=event.creator,
            quote_lamports=event.quote_lamports, token_raw=event.token_raw,
            pre_state=event.pre_state, post_state=event.post_state,
        ))
    for mint in events:
        deduped = {(row.signature, row.instruction_index): row for row in events[mint]}
        events[mint] = sorted(deduped.values(), key=lambda row: row.order)
    summary = {
        "targetMints": len(target_mints),
        "selectedReadinessPartitions": len(selected_reports),
        "evidencePathsScanned": len(evidence_paths),
        "missingReadinessPartitions": missing_readiness,
        "incompleteReadinessPartitions": incomplete_readiness,
        "rawTradeArrivals": len(arrivals),
        "finalizedTargetTradeEvents": len(provisional_events),
        "finalizedWithoutRawArrival": missing_arrival,
        "createObservations": len(create_observations),
        "decodeFailureN": len(decode_failures),
        "decodeFailureSamples": decode_failures[:10],
        "malformedFrames": malformed_frames,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as raw_stream:
        with gzip.GzipFile(filename="", fileobj=raw_stream, mode="wb", mtime=0) as compressed:
            compressed.write(canonical({"schema": SCHEMA + "_CAUSAL_EVENT_CACHE", "targetIdentitySha256": target_identity, "evidenceSummary": summary}) + b"\n")
            for mint in sorted(events):
                for event in events[mint]:
                    compressed.write(canonical({
                        "mint": mint, "signature": event.signature, "slot": event.slot,
                        "transactionIndex": event.transaction_index, "instructionIndex": event.instruction_index,
                        "observedUnixMs": event.observed_ms, "evidenceId": event.evidence_id,
                        "isBuy": event.is_buy, "user": event.user, "creator": event.creator,
                        "quoteLamports": event.quote_lamports, "tokenRaw": event.token_raw,
                        "preState": {"virtual_sol": event.pre_state.virtual_sol, "virtual_token": event.pre_state.virtual_token, "real_sol": event.pre_state.real_sol, "real_token": event.pre_state.real_token},
                        "postState": {"virtual_sol": event.post_state.virtual_sol, "virtual_token": event.post_state.virtual_token, "real_sol": event.post_state.real_sol, "real_token": event.post_state.real_token},
                    }) + b"\n")
    return dict(events), summary | {"loadedFromCache": False}


def safe_log(value: float) -> float:
    return math.log1p(max(0.0, value))


def price(state: CurveState) -> float:
    return state.virtual_sol / max(1, state.virtual_token)


def hhi(amounts: Iterable[int]) -> float:
    values = [max(0, int(value)) for value in amounts]
    total = sum(values)
    return sum((value / total) ** 2 for value in values) if total else 0.0


def state_features(
    current: Event,
    prior: list[Event],
    create_ms: int,
    initial_state: CurveState,
) -> dict[str, float]:
    external = [row for row in prior if row.user != WALLET]
    now = current.observed_ms
    current_state = current.pre_state
    initial_price = price(initial_state)
    current_price = price(current_state)
    features: dict[str, float] = {
        "log_age_ms": safe_log(now - create_ms),
        "log_event_index": safe_log(len(prior)),
        "curve_progress": max(0.0, (current_state.virtual_sol - initial_state.virtual_sol) / max(1, initial_state.virtual_sol)),
        "log_market_cap_sol": safe_log((current_state.virtual_sol / max(1, current_state.virtual_token)) * 1_000_000),
        "distance_from_launch_log_price": math.log(max(current_price, 1e-30) / max(initial_price, 1e-30)),
    }
    for window in (250, 1000, 2000, 5000):
        rows = [row for row in external if row.observed_ms >= now - window]
        buys = [row for row in rows if row.is_buy]
        sells = [row for row in rows if not row.is_buy]
        buy_sol = sum(row.quote_lamports for row in buys) / 1e9
        sell_sol = sum(row.quote_lamports for row in sells) / 1e9
        features[f"gross_buy_sol_{window}ms"] = buy_sol
        features[f"gross_sell_sol_{window}ms"] = sell_sol
        features[f"net_flow_sol_{window}ms"] = buy_sol - sell_sol
        features[f"buy_count_{window}ms"] = float(len(buys))
        features[f"sell_count_{window}ms"] = float(len(sells))
        if window in (250, 1000, 2000, 5000):
            boundary = next((row for row in reversed(prior) if row.observed_ms < now - window), None)
            boundary_state = boundary.post_state if boundary else initial_state
            features[f"log_price_change_{window}ms"] = math.log(max(current_price, 1e-30) / max(price(boundary_state), 1e-30))
    buys_total = [row for row in external if row.is_buy]
    sells_total = [row for row in external if not row.is_buy]
    features["total_external_buy_sol"] = sum(row.quote_lamports for row in buys_total) / 1e9
    features["total_external_sell_sol"] = sum(row.quote_lamports for row in sells_total) / 1e9
    features["total_external_net_sol"] = features["total_external_buy_sol"] - features["total_external_sell_sol"]
    gross_1s = features["gross_buy_sol_1000ms"] + features["gross_sell_sol_1000ms"]
    features["buy_sell_imbalance_1000ms"] = features["net_flow_sol_1000ms"] / gross_1s if gross_1s else 0.0
    buys_1s = [row for row in external if row.is_buy and row.observed_ms >= now - 1000]
    buy_sizes_1s = [row.quote_lamports / 1e9 for row in buys_1s]
    features["largest_buy_sol_1000ms"] = max(buy_sizes_1s, default=0.0)
    features["mean_buy_sol_1000ms"] = statistics.fmean(buy_sizes_1s) if buy_sizes_1s else 0.0
    previous_1s = [row for row in external if now - 2000 <= row.observed_ms < now - 1000]
    previous_buy = sum(row.quote_lamports for row in previous_1s if row.is_buy) / 1e9
    previous_sell = sum(row.quote_lamports for row in previous_1s if not row.is_buy) / 1e9
    features["net_acceleration_1s"] = features["net_flow_sol_1000ms"] - (previous_buy - previous_sell)
    features["buy_count_acceleration_1s"] = features["buy_count_1000ms"] - sum(row.is_buy for row in previous_1s)
    recent_1s = [row for row in external if row.observed_ms >= now - 1000]
    buyers = collections.Counter(row.user for row in recent_1s if row.is_buy)
    sellers = collections.Counter(row.user for row in recent_1s if not row.is_buy)
    features["unique_buyers_1000ms"] = float(len(buyers))
    features["unique_sellers_1000ms"] = float(len(sellers))
    features["repeat_buyer_share_1000ms"] = sum(value for value in buyers.values() if value > 1) / max(1, sum(buyers.values()))
    buyer_amounts = collections.Counter()
    for row in recent_1s:
        if row.is_buy:
            buyer_amounts[row.user] += row.quote_lamports
    features["buyer_hhi_1000ms"] = hhi(buyer_amounts.values())
    features["top_buyer_share_1000ms"] = max(buyer_amounts.values(), default=0) / max(1, sum(buyer_amounts.values()))
    creator = current.creator
    features["creator_buy_sol_5000ms"] = sum(
        row.quote_lamports for row in external if row.is_buy and row.user == creator and row.observed_ms >= now - 5000
    ) / 1e9
    features["creator_participated"] = float(any(row.user == creator for row in external))
    features["unique_buyers_total"] = float(len({row.user for row in buys_total}))
    features["unique_sellers_total"] = float(len({row.user for row in sells_total}))
    if external:
        last = external[-1]
        features["last_external_side"] = 1.0 if last.is_buy else -1.0
        features["last_external_trade_sol"] = last.quote_lamports / 1e9
        features["log_ms_since_last_external"] = safe_log(now - last.observed_ms)
    else:
        features["last_external_side"] = 0.0
        features["last_external_trade_sol"] = 0.0
        features["log_ms_since_last_external"] = safe_log(now - create_ms)
    consecutive_buys = consecutive_sells = 0
    for row in reversed(external):
        if row.is_buy and consecutive_sells == 0:
            consecutive_buys += 1
        elif not row.is_buy and consecutive_buys == 0:
            consecutive_sells += 1
        else:
            break
    features["consecutive_buys"] = float(consecutive_buys)
    features["consecutive_sells"] = float(consecutive_sells)
    recent_times = [row.observed_ms for row in external if row.observed_ms >= now - 5000]
    gaps = [right - left for left, right in zip(recent_times, recent_times[1:]) if right >= left]
    features["log_median_interarrival_ms_5s"] = safe_log(statistics.median(gaps) if gaps else 5000.0)
    return {name: float(features.get(name, 0.0)) for name in FULL_FEATURES}


def size_tier(size_sol: float) -> int:
    return sum(size_sol >= boundary for boundary in SIZE_BOUNDARIES)


def build_examples(
    episodes: list[dict[str, Any]],
    mint_index: dict[str, dict[str, Any]],
    events: dict[str, list[Event]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    exclusions: collections.Counter[str] = collections.Counter()
    for episode in sorted(episodes, key=lambda row: (row["openedTimestamp"], row["mint"], row["episodeIndex"])):
        mint = episode["mint"]
        if mint not in mint_index:
            exclusions["CAUSAL_SOURCE_NOT_INDEXED"] += 1
            continue
        sequence = events.get(mint) or []
        matching = [row for row in sequence if row.signature == episode["entrySignature"] and row.is_buy and row.user == WALLET]
        if len(matching) != 1:
            exclusions["ENTRY_EVENT_NOT_EXACTLY_JOINED"] += 1
            continue
        actual = matching[0]
        before = [row for row in sequence if row.order < actual.order]
        if any(row.signature == actual.signature for row in before):
            exclusions["SAME_TRANSACTION_PREENTRY_AMBIGUITY"] += 1
            continue
        controls = [row for row in before if row.user != WALLET]
        if not controls:
            exclusions["NO_EARLIER_EXTERNAL_TRANSACTION_CONTROL"] += 1
            continue
        create_ms = int(mint_index[mint]["createReceivedAtUnixNs"]) // 1_000_000
        if create_ms <= 0 or create_ms > actual.observed_ms:
            exclusions["INVALID_CREATE_CAUSAL_TIME"] += 1
            continue
        initial_state = sequence[0].pre_state
        states = []
        for candidate in controls + [actual]:
            prior = [row for row in before if row.order < candidate.order]
            states.append({
                "label": 1 if candidate is actual else 0,
                "candidateSignature": candidate.signature,
                "candidateObservedUnixMs": candidate.observed_ms,
                "candidateUtc": utc(candidate.observed_ms),
                "candidateOrder": list(candidate.order),
                "candidateEventIs2CqEntry": candidate is actual,
                "features": state_features(candidate, prior, create_ms, initial_state),
            })
        action = next(row for row in episode["actions"] if row["signature"] == episode["entrySignature"])
        size_sol = float(episode.get("firstBuySol") or abs(float(action.get("walletQuoteDeltaLamports") or 0)) / 1e9)
        examples.append({
            "episodeId": f"{mint}:{episode['episodeIndex']}",
            "mint": mint,
            "episodeIndex": episode["episodeIndex"],
            "entrySignature": episode["entrySignature"],
            "entryObservedUnixMs": actual.observed_ms,
            "entryUtc": utc(actual.observed_ms),
            "entrySlot": actual.slot,
            "entryTransactionIndex": actual.transaction_index,
            "entryInstructionIndex": actual.instruction_index,
            "entrySizeSol": size_sol,
            "sizeTier": size_tier(size_sol),
            "rightCensored": bool(episode.get("rightCensored")),
            "createObservedUnixMs": create_ms,
            "createUtc": utc(create_ms),
            "entryAgeMs": actual.observed_ms - create_ms,
            "controlN": len(controls),
            "states": states,
            "entryEvidenceId": actual.evidence_id,
        })
    return examples, {
        "causallyUsableEpisodeN": len(examples),
        "causallyUsableUniqueMints": len({row["mint"] for row in examples}),
        "rightCensoredIncludedN": sum(row["rightCensored"] for row in examples),
        "controlStateN": sum(row["controlN"] for row in examples),
        "causalExclusionReasonCounts": dict(exclusions),
    }


def split_examples(examples: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    ordered = sorted(examples, key=lambda row: (row["entryObservedUnixMs"], row["episodeId"]))
    boundary = ordered[max(0, math.ceil(len(ordered) * 0.70) - 1)]["entryObservedUnixMs"]
    train = [row for row in ordered if row["entryObservedUnixMs"] <= boundary]
    train_mints = {row["mint"] for row in train}
    validation = [row for row in ordered if row["entryObservedUnixMs"] > boundary and row["mint"] not in train_mints]
    overlap_dropped = [row for row in ordered if row["entryObservedUnixMs"] > boundary and row["mint"] in train_mints]
    return train, validation, overlap_dropped, boundary


def flatten(examples: list[dict[str, Any]], feature_names: tuple[str, ...]) -> tuple[list[list[float]], list[int], list[float], list[str]]:
    x: list[list[float]] = []
    y: list[int] = []
    weights: list[float] = []
    groups: list[str] = []
    for episode in examples:
        negatives = max(1, episode["controlN"])
        for state in episode["states"]:
            x.append([state["features"][name] for name in feature_names])
            y.append(int(state["label"]))
            weights.append(0.5 if state["label"] else 0.5 / negatives)
            groups.append(episode["episodeId"])
    return x, y, weights, groups


def standardizer(x: list[list[float]]) -> tuple[list[float], list[float]]:
    means = [statistics.fmean(row[index] for row in x) for index in range(len(x[0]))]
    scales = []
    for index, mean in enumerate(means):
        variance = statistics.fmean((row[index] - mean) ** 2 for row in x)
        scales.append(math.sqrt(variance) if variance > 1e-12 else 1.0)
    return means, scales


def transform(x: list[list[float]], means: list[float], scales: list[float]) -> list[list[float]]:
    return [[(value - means[index]) / scales[index] for index, value in enumerate(row)] for row in x]


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-min(value, 40.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(value, -40.0))
    return z / (1.0 + z)


def fit_logistic(x: list[list[float]], y: list[int], weights: list[float]) -> tuple[float, list[float]]:
    coefficients = [0.0] * len(x[0])
    intercept = 0.0
    total_weight = sum(weights)
    for iteration in range(800):
        gradient = [0.0] * len(coefficients)
        intercept_gradient = 0.0
        for row, label, weight in zip(x, y, weights):
            probability = sigmoid(intercept + sum(a * b for a, b in zip(coefficients, row)))
            error = (probability - label) * weight
            intercept_gradient += error
            for index, value in enumerate(row):
                gradient[index] += error * value
        rate = 0.08 / (1.0 + iteration / 300.0)
        intercept -= rate * intercept_gradient / total_weight
        for index in range(len(coefficients)):
            coefficients[index] -= rate * (gradient[index] / total_weight + 0.03 * coefficients[index])
    return intercept, coefficients


def predict(x: list[list[float]], model: tuple[float, list[float]]) -> list[float]:
    intercept, coefficients = model
    return [sigmoid(intercept + sum(a * b for a, b in zip(coefficients, row))) for row in x]


def auc(y: list[int], scores: list[float]) -> float | None:
    positive = [score for score, label in zip(scores, y) if label]
    negative = [score for score, label in zip(scores, y) if not label]
    if not positive or not negative:
        return None
    wins = sum((a > b) + 0.5 * (a == b) for a in positive for b in negative)
    return wins / (len(positive) * len(negative))


def average_precision(y: list[int], scores: list[float]) -> float | None:
    positives = sum(y)
    if not positives:
        return None
    ranked = sorted(zip(scores, y), reverse=True)
    seen = 0
    total = 0.0
    for rank, (_, label) in enumerate(ranked, 1):
        if label:
            seen += 1
            total += seen / rank
    return total / positives


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    left = int(position)
    right = min(left + 1, len(ordered) - 1)
    fraction = position - left
    return ordered[left] * (1 - fraction) + ordered[right] * fraction


def ranking_metrics(examples: list[dict[str, Any]], feature_names: tuple[str, ...], means: list[float], scales: list[float], model: tuple[float, list[float]]) -> dict[str, Any]:
    ranks = []
    sizes = []
    concordances = []
    for episode in examples:
        raw = [[state["features"][name] for name in feature_names] for state in episode["states"]]
        scores = predict(transform(raw, means, scales), model)
        actual_index = next(index for index, state in enumerate(episode["states"]) if state["label"])
        rank = 1 + sum(score > scores[actual_index] for index, score in enumerate(scores) if index != actual_index)
        ranks.append(rank)
        sizes.append(len(scores))
        controls = [score for index, score in enumerate(scores) if index != actual_index]
        concordances.append(statistics.fmean(
            (scores[actual_index] > score) + 0.5 * (scores[actual_index] == score)
            for score in controls
        ))
    n = len(ranks)
    return {
        "n": n,
        "actualRankedFirstPct": 100 * sum(rank == 1 for rank in ranks) / n if n else None,
        "actualRankedTop3Pct": 100 * sum(rank <= 3 for rank in ranks) / n if n else None,
        "actualRankedTop10Pct": 100 * sum(rank <= max(1, math.ceil(size * 0.10)) for rank, size in zip(ranks, sizes)) / n if n else None,
        "meanReciprocalRank": statistics.fmean(1 / rank for rank in ranks) if n else None,
        "mintBalancedPairwiseConcordance": statistics.fmean(concordances) if n else None,
        "medianRank": statistics.median(ranks) if n else None,
        "meanCandidateStates": statistics.fmean(sizes) if n else None,
        "chanceRankedFirstPct": 100 * statistics.fmean(1 / size for size in sizes) if n else None,
        "chanceRankedTop3Pct": 100 * statistics.fmean(min(3, size) / size for size in sizes) if n else None,
        "chanceRankedTop10Pct": 100 * statistics.fmean(max(1, math.ceil(size * 0.10)) / size for size in sizes) if n else None,
    }


def evaluate_model(train: list[dict[str, Any]], validation: list[dict[str, Any]], feature_names: tuple[str, ...]) -> tuple[dict[str, Any], tuple[list[float], list[float], tuple[float, list[float]]]]:
    train_x, train_y, train_w, _ = flatten(train, feature_names)
    validation_x, validation_y, _, _ = flatten(validation, feature_names)
    means, scales = standardizer(train_x)
    model = fit_logistic(transform(train_x, means, scales), train_y, train_w)
    train_scores = predict(transform(train_x, means, scales), model)
    validation_scores = predict(transform(validation_x, means, scales), model)
    report = {
        "featureNames": list(feature_names),
        "training": {
            "rowN": len(train_y), "positiveN": sum(train_y), "rocAuc": auc(train_y, train_scores),
            "averagePrecision": average_precision(train_y, train_scores),
            "withinMint": ranking_metrics(train, feature_names, means, scales, model),
        },
        "validation": {
            "rowN": len(validation_y), "positiveN": sum(validation_y), "rocAuc": auc(validation_y, validation_scores),
            "averagePrecision": average_precision(validation_y, validation_scores),
            "withinMint": ranking_metrics(validation, feature_names, means, scales, model),
        },
        "coefficients": sorted(
            ({"feature": name, "standardizedCoefficient": coefficient} for name, coefficient in zip(feature_names, model[1])),
            key=lambda row: abs(row["standardizedCoefficient"]), reverse=True,
        ),
        "intercept": model[0],
        "trainMeans": dict(zip(feature_names, means)),
        "trainScales": dict(zip(feature_names, scales)),
    }
    return report, (means, scales, model)


def univariate(train: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for name in FULL_FEATURES:
        differences = []
        for episode in train:
            actual = next(state["features"][name] for state in episode["states"] if state["label"])
            controls = [state["features"][name] for state in episode["states"] if not state["label"]]
            differences.append(actual - statistics.fmean(controls))
        scale = statistics.pstdev(differences) or 1.0
        rows.append({
            "feature": name,
            "pairedMeanDifference": statistics.fmean(differences),
            "pairedStandardizedDifference": statistics.fmean(differences) / scale,
            "actualAboveControlMeanPct": 100 * sum(value > 0 for value in differences) / len(differences),
        })
    return sorted(rows, key=lambda row: abs(row["pairedStandardizedDifference"]), reverse=True)


def softmax(values: list[float]) -> list[float]:
    maximum = max(values)
    exp = [math.exp(min(40.0, value - maximum)) for value in values]
    total = sum(exp)
    return [value / total for value in exp]


def fit_softmax(x: list[list[float]], y: list[int], classes: int = 5) -> tuple[list[float], list[list[float]]]:
    intercept = [0.0] * classes
    coefficients = [[0.0] * len(x[0]) for _ in range(classes)]
    for iteration in range(900):
        gradient_i = [0.0] * classes
        gradient = [[0.0] * len(x[0]) for _ in range(classes)]
        for row, label in zip(x, y):
            probabilities = softmax([intercept[c] + sum(a * b for a, b in zip(coefficients[c], row)) for c in range(classes)])
            for c in range(classes):
                error = probabilities[c] - (1.0 if c == label else 0.0)
                gradient_i[c] += error
                for index, value in enumerate(row):
                    gradient[c][index] += error * value
        rate = 0.06 / (1.0 + iteration / 350.0)
        for c in range(classes):
            intercept[c] -= rate * gradient_i[c] / len(x)
            for index in range(len(x[0])):
                coefficients[c][index] -= rate * (gradient[c][index] / len(x) + 0.04 * coefficients[c][index])
    return intercept, coefficients


def softmax_predict(x: list[list[float]], model: tuple[list[float], list[list[float]]]) -> tuple[list[int], list[float]]:
    predicted = []
    expected = []
    for row in x:
        probabilities = softmax([model[0][c] + sum(a * b for a, b in zip(model[1][c], row)) for c in range(5)])
        predicted.append(max(range(5), key=lambda c: probabilities[c]))
        expected.append(sum(c * probabilities[c] for c in range(5)))
    return predicted, expected


def rank_correlation(a: list[float], b: list[float]) -> float | None:
    if len(a) < 2:
        return None
    def ranks(values: list[float]) -> list[float]:
        ordered = sorted(range(len(values)), key=lambda index: values[index])
        result = [0.0] * len(values)
        position = 0
        while position < len(values):
            end = position + 1
            while end < len(values) and values[ordered[end]] == values[ordered[position]]:
                end += 1
            rank = (position + end - 1) / 2
            for index in ordered[position:end]:
                result[index] = rank
            position = end
        return result
    ra, rb = ranks(a), ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    numerator = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    denominator = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return numerator / denominator if denominator else None


def conviction_model(train: list[dict[str, Any]], validation: list[dict[str, Any]]) -> dict[str, Any]:
    def rows(examples: list[dict[str, Any]]) -> tuple[list[list[float]], list[int]]:
        x, y = [], []
        for episode in examples:
            actual = next(state for state in episode["states"] if state["label"])
            x.append([actual["features"][name] for name in FULL_FEATURES])
            y.append(int(episode["sizeTier"]))
        return x, y
    train_x, train_y = rows(train)
    validation_x, validation_y = rows(validation)
    means, scales = standardizer(train_x)
    model = fit_softmax(transform(train_x, means, scales), train_y)
    predicted, expected = softmax_predict(transform(validation_x, means, scales), model)
    majority = collections.Counter(train_y).most_common(1)[0][0]
    confusion = [[0] * 5 for _ in range(5)]
    for actual, guess in zip(validation_y, predicted):
        confusion[actual][guess] += 1
    n = len(validation_y)
    trend = []
    for name in FULL_FEATURES:
        values = [next(state for state in episode["states"] if state["label"])["features"][name] for episode in train]
        correlation = rank_correlation(values, [float(value) for value in train_y])
        trend.append({"feature": name, "spearmanWithSizeTierTrain": correlation})
    trend.sort(key=lambda row: abs(row["spearmanWithSizeTierTrain"] or 0.0), reverse=True)
    validation_trends = []
    for item in trend[:12]:
        name = item["feature"]
        values = [next(state for state in episode["states"] if state["label"])["features"][name] for episode in validation]
        validation_trends.append({
            "feature": name,
            "spearmanWithSizeTierValidation": rank_correlation(values, [float(value) for value in validation_y]),
        })
    return {
        "tierBoundariesSol": list(SIZE_BOUNDARIES),
        "trainingTierCounts": dict(collections.Counter(map(str, train_y))),
        "validationTierCounts": dict(collections.Counter(map(str, validation_y))),
        "validationN": n,
        "exactAccuracyPct": 100 * sum(a == b for a, b in zip(validation_y, predicted)) / n if n else None,
        "adjacentAccuracyPct": 100 * sum(abs(a - b) <= 1 for a, b in zip(validation_y, predicted)) / n if n else None,
        "meanAbsoluteTierError": statistics.fmean(abs(a - b) for a, b in zip(validation_y, predicted)) if n else None,
        "spearmanExpectedTier": rank_correlation([float(value) for value in validation_y], expected),
        "majorityBaselineTier": majority,
        "majorityBaselineAccuracyPct": 100 * sum(value == majority for value in validation_y) / n if n else None,
        "majorityBaselineAdjacentAccuracyPct": 100 * sum(abs(value - majority) <= 1 for value in validation_y) / n if n else None,
        "majorityBaselineMeanAbsoluteTierError": statistics.fmean(abs(value - majority) for value in validation_y) if n else None,
        "confusionActualRowsPredictedColumns": confusion,
        "strongerStateFeatureTrendsTrain": trend[:12],
        "frozenTrainTopFeatureTrendsValidation": validation_trends,
    }


def subgroup_rankings(examples: list[dict[str, Any]], feature_names: tuple[str, ...], fitted: tuple[list[float], list[float], tuple[float, list[float]]], key) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in examples:
        groups[str(key(row))].append(row)
    return {name: ranking_metrics(rows, feature_names, fitted[0], fitted[1], fitted[2]) for name, rows in sorted(groups.items())}


def render_markdown(report: dict[str, Any]) -> str:
    pop = report["population"]
    split = report["split"]
    base = report["entryTiming"]["progressionBaseline"]["validation"]
    full = report["entryTiming"]["fullCausalModel"]["validation"]
    conviction = report["conviction"]
    lines = [
        "# 2CQ Causal Entry Timing Fingerprint V1",
        "",
        f"**Verdict:** `{report['verdict']}`",
        "",
        "## Population",
        "",
        f"- Frozen-window episodes: {pop['frozenWindowEpisodeN']}",
        f"- Structurally entry-usable: {pop['structurallyEntryUsableEpisodeN']}",
        f"- Causally usable: {pop['causallyUsableEpisodeN']} episodes / {pop['causallyUsableUniqueMints']} mints",
        f"- Earlier same-mint controls: {pop['controlStateN']}",
        f"- Train / validation: {split['trainN']} / {split['validationN']}",
        "",
        "## Entry Timing",
        "",
        "| Model | Validation AUC | Top 1 | Top 3 | Top 10% | MRR |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Generic lifecycle | {base['rocAuc']:.3f} | {base['withinMint']['actualRankedFirstPct']:.2f}% | {base['withinMint']['actualRankedTop3Pct']:.2f}% | {base['withinMint']['actualRankedTop10Pct']:.2f}% | {base['withinMint']['meanReciprocalRank']:.3f} |",
        f"| Full causal | {full['rocAuc']:.3f} | {full['withinMint']['actualRankedFirstPct']:.2f}% | {full['withinMint']['actualRankedTop3Pct']:.2f}% | {full['withinMint']['actualRankedTop10Pct']:.2f}% | {full['withinMint']['meanReciprocalRank']:.3f} |",
        "",
        "## Conviction",
        "",
        f"- Exact tier accuracy: {conviction['exactAccuracyPct']:.2f}%",
        f"- Adjacent-tier accuracy: {conviction['adjacentAccuracyPct']:.2f}%",
        f"- Majority baseline: {conviction['majorityBaselineAccuracyPct']:.2f}%",
        f"- Spearman expected-vs-actual tier: {conviction['spearmanExpectedTier'] if conviction['spearmanExpectedTier'] is not None else 'n/a'}",
        "",
        "## Decision",
        "",
        report["plainLanguageDecision"],
        "",
        f"Chosen-vs-ignored next study justified: **{str(report['chosenVsIgnoredJustified']).upper()}**",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("teacher_dir", type=pathlib.Path)
    parser.add_argument("evidence_root", type=pathlib.Path)
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    episode_path = args.teacher_dir / "episodes.jsonl.gz"
    mint_index_path = args.research_root / "discovery-mint-index.jsonl"
    episodes, population = load_episodes(episode_path)
    all_index = load_mint_index(mint_index_path)
    target_index = {mint: all_index[mint] for mint in {row["mint"] for row in episodes} if mint in all_index}
    entry_max_slots = collections.defaultdict(int)
    for episode in episodes:
        if episode["mint"] not in target_index:
            continue
        action = next(
            row for row in episode["actions"]
            if row["signature"] == episode["entrySignature"]
        )
        entry_max_slots[episode["mint"]] = max(entry_max_slots[episode["mint"]], int(action["slot"]))
    population["causalSourceIndexedEpisodeN"] = sum(row["mint"] in target_index for row in episodes)
    population["causalSourceIndexedUniqueMints"] = len(target_index)
    args.output.mkdir(parents=True, exist_ok=True)
    events, evidence = scan_exact_events(
        args.evidence_root,
        target_index,
        dict(entry_max_slots),
        args.output / "causal-events-cache.jsonl.gz",
    )
    examples, causal_population = build_examples(episodes, target_index, events)
    population.update(causal_population)
    if len(examples) < 50:
        raise RuntimeError(f"insufficient causal examples: {len(examples)}")
    train, validation, overlap_dropped, boundary = split_examples(examples)
    if len(validation) < 20:
        raise RuntimeError(f"insufficient untouched validation examples: {len(validation)}")
    baseline, baseline_fit = evaluate_model(train, validation, PROGRESSION_FEATURES)
    progression_flow, progression_flow_fit = evaluate_model(train, validation, PROGRESSION_FEATURES + FLOW_FEATURES)
    progression_participant, progression_participant_fit = evaluate_model(train, validation, PROGRESSION_FEATURES + PARTICIPANT_FEATURES)
    progression_sequence, progression_sequence_fit = evaluate_model(train, validation, PROGRESSION_FEATURES + SEQUENCE_FEATURES)
    state_only, state_only_fit = evaluate_model(train, validation, FLOW_FEATURES + PARTICIPANT_FEATURES + SEQUENCE_FEATURES)
    full, full_fit = evaluate_model(train, validation, FULL_FEATURES)
    conviction = conviction_model(train, validation)
    full_validation = full["validation"]
    base_validation = baseline["validation"]
    auc_gain = (full_validation["rocAuc"] or 0.0) - (base_validation["rocAuc"] or 0.0)
    top3_gain = full_validation["withinMint"]["actualRankedTop3Pct"] - base_validation["withinMint"]["actualRankedTop3Pct"]
    validation_day_n = len({row["entryUtc"][:10] for row in validation})
    pairwise_gain = (
        full_validation["withinMint"]["mintBalancedPairwiseConcordance"]
        - base_validation["withinMint"]["mintBalancedPairwiseConcordance"]
    )
    if (
        len(validation) >= 50 and validation_day_n >= 2
        and (full_validation["rocAuc"] or 0.0) >= 0.68 and auc_gain >= 0.03
        and pairwise_gain >= 0.03
    ):
        verdict = "2CQ_ENTRY_TIMING_FINGERPRINT_FOUND"
        decision = "Pre-entry flow, participant, and sequence state adds material untouched-period discrimination beyond generic lifecycle progression."
        chosen = True
    elif (full_validation["rocAuc"] or 0.0) >= 0.58 and (auc_gain >= 0.01 or pairwise_gain >= 0.01):
        verdict = "2CQ_ENTRY_TIMING_SIGNAL_WEAK"
        decision = "Available causal state contains some timing information, but its incremental validation advantage over lifecycle progression is not yet strong enough to call a reproducible fingerprint."
        chosen = False
    else:
        verdict = "2CQ_ENTRY_TIMING_NOT_EXPLAINED_BY_AVAILABLE_STATE"
        decision = "The bounded observable state does not reliably identify 2CQ's entry moment beyond generic mint progression in the untouched validation period."
        chosen = False
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "verdict": verdict,
        "wallet": WALLET,
        "frozenWindow": {"startUtc": WINDOW_START, "endUtc": WINDOW_END},
        "causalBoundary": "features use only successful finalized events canonically and observationally before the candidate transaction; current 2CQ buy contributes zero information",
        "targets": {"entryTiming": "actual 2CQ entry state vs earlier same-mint external transaction states", "conviction": "outcome-blind natural first-buy SOL tier"},
        "population": population,
        "evidence": evidence,
        "split": {
            "method": "fixed 70/30 chronological boundary; later episodes of train-period mints dropped from validation",
            "boundaryUnixMsInclusiveTrain": boundary,
            "boundaryUtc": utc(boundary),
            "trainN": len(train),
            "trainUniqueMints": len({row["mint"] for row in train}),
            "validationN": len(validation),
            "validationUniqueMints": len({row["mint"] for row in validation}),
            "crossBoundaryRepeatEpisodeDroppedN": len(overlap_dropped),
            "validationUsedForFeatureOrModelTuning": False,
            "validationMetricsInspectedAfterPrimaryModelFit": True,
        },
        "entryTiming": {
            "univariateTrainingOnly": univariate(train),
            "progressionBaseline": baseline,
            "predeclaredFamilyAblations": {
                "progressionPlusFlow": progression_flow,
                "progressionPlusParticipant": progression_participant,
                "progressionPlusSequence": progression_sequence,
                "stateOnlyWithoutProgression": state_only,
            },
            "fullCausalModel": full,
            "incrementalValidation": {
                "rocAucGain": auc_gain,
                "top3PercentagePointGain": top3_gain,
                "mintBalancedPairwiseConcordanceGain": pairwise_gain,
                "validationUtcDayN": validation_day_n,
            },
            "validationStabilityByUtcDay": subgroup_rankings(validation, FULL_FEATURES, full_fit, lambda row: row["entryUtc"][:10]),
            "validationStabilityBySizeTier": subgroup_rankings(validation, FULL_FEATURES, full_fit, lambda row: row["sizeTier"]),
        },
        "conviction": conviction,
        "plainLanguageDecision": decision,
        "chosenVsIgnoredJustified": chosen,
        "integrity": {
            "futureOutcomesRead": 0,
            "pnlCalculated": False,
            "postEntryFeaturesUsed": False,
            "teacherCurrentBuyIncludedInFeatures": False,
            "ignoredMintPopulationRead": False,
            "liveChanges": 0,
            "8YcbTouched": False,
            "validationUsedForFeatureOrModelTuning": False,
        },
    }
    report["population"]["causallyUsableByUtcDay"] = dict(collections.Counter(row["entryUtc"][:10] for row in examples))
    args.output.mkdir(parents=True, exist_ok=True)
    rows_payload = b"".join(canonical(row) + b"\n" for row in examples)
    rows_path = args.output / "entry-timing-examples.jsonl.gz"
    with rows_path.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            compressed.write(rows_payload)
    report_payload = json.dumps(report, indent=2, sort_keys=True).encode() + b"\n"
    atomic_write(args.output / "report.json", report_payload)
    atomic_write(args.output / "report.md", render_markdown(report).encode())
    provenance = {
        "schema": SCHEMA + "_PROVENANCE",
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "episodesSha256": sha256_file(episode_path),
        "mintIndexSha256": sha256_file(mint_index_path),
        "examplesSha256": sha256_file(rows_path),
        "reportSha256": hashlib.sha256(report_payload).hexdigest(),
        "randomness": "none",
        "validationUsedForFeatureOrModelTuning": False,
        "validationMetricsInspectedAfterPrimaryModelFit": True,
    }
    atomic_write(args.output / "provenance.json", json.dumps(provenance, indent=2, sort_keys=True).encode() + b"\n")
    print(json.dumps({
        "verdict": verdict,
        "population": population,
        "split": report["split"],
        "baselineValidation": baseline["validation"],
        "fullValidation": full["validation"],
        "conviction": conviction,
        "chosenVsIgnoredJustified": chosen,
        "output": str(args.output),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
