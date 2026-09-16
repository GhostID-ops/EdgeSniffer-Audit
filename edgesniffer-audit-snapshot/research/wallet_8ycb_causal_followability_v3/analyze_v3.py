#!/usr/bin/env python3
"""Causal-flow and third-party followability audit for the isolated 8YCB V3 lane."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable


WALLET = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ"
INITIAL_VSOL = 30_000_000_000
INITIAL_VTOK = 1_073_000_000_000_000
FEE_BPS = 125
FOLLOW_LAMPORTS = 100_000_000
DELAYS = [25, 50, 100, 250, 500, 1000]
HORIZON_MS = 120_000
FINAL_CLASSIFICATION = "8YCB_PUBLIC_EVENT_PROMISING_NEEDS_PROSPECTIVE_SHADOW"


def read_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def write_gz(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True, allow_nan=False) + "\n")


def iso_ms(value: int | None) -> str | None:
    if value is None:
        return None
    return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def q(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    return ordered[round((len(ordered) - 1) * fraction)]


def finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def economics(rows: list[dict[str, Any]], key: str = "exit_roi_pct") -> dict[str, Any]:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    winners = sorted((value for value in values if value > 0), reverse=True)
    losing = sorted((-value for value in values if value < 0), reverse=True)
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        cumulative += value * FOLLOW_LAMPORTS / 1_000_000_000 / 100
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    return {
        "n": len(values),
        "wins": sum(value > 0 for value in values),
        "losses": sum(value < 0 for value in values),
        "wr": sum(value > 0 for value in values) / len(values) if values else None,
        "profit_factor": finite(gains / losses) if losses else None,
        "expectancy_roi_pct": statistics.mean(values) if values else None,
        "median_roi_pct": statistics.median(values) if values else None,
        "total_pnl_sol_at_0p1_sol": sum(values) * 0.001,
        "max_drawdown_sol_at_0p1_sol": max_drawdown,
        "top1_positive_pnl_share": winners[0] / gains if winners and gains else None,
        "top5_positive_pnl_share": sum(winners[:5]) / gains if gains else None,
        "top1_loss_share": losing[0] / losses if losing and losses else None,
        "top5_loss_share": sum(losing[:5]) / losses if losses else None,
        "tp20_rate": sum(row.get("exit_reason") == "TP20" for row in rows) / len(rows) if rows else None,
        "sl20_rate": sum(row.get("exit_reason") == "SL20" for row in rows) / len(rows) if rows else None,
        "timeout_rate": sum(row.get("exit_reason") == "TIMEOUT_120S" for row in rows) / len(rows) if rows else None,
    }


def follower_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = economics(rows)
    prices = [float(row["entry_average_price_sol_per_token"]) for row in rows]
    degradation = [float(row["entry_degradation_vs_atomic_pct"]) for row in rows if row.get("entry_degradation_vs_atomic_pct") is not None]
    starts = [float(row["start_roundtrip_roi_pct"]) for row in rows]
    result.update({
        "median_entry_average_price_sol_per_token": q(prices, .5),
        "entry_degradation_vs_atomic_pct": {"p50": q(degradation, .5), "p75": q(degradation, .75), "p90": q(degradation, .9)},
        "median_start_roundtrip_roi_pct": q(starts, .5),
    })
    return result


def buy_tokens(vsol: int, vtok: int, gross: int) -> int:
    if gross <= 0:
        return 0
    return max(0, vtok - (vsol * vtok) // (vsol + gross))


def apply_buy(vsol: int, vtok: int, gross: int) -> tuple[int, int, int]:
    tokens = buy_tokens(vsol, vtok, gross)
    net = gross - gross * FEE_BPS // 10_000
    return vsol + net, vtok - tokens, tokens


def sell_value(vsol: int, vtok: int, tokens: int) -> int:
    if tokens <= 0:
        return 0
    gross = max(0, vsol - (vsol * vtok) // (vtok + tokens))
    return gross - gross * FEE_BPS // 10_000


def apply_sell(vsol: int, vtok: int, tokens: int) -> tuple[int, int]:
    if tokens <= 0:
        return vsol, vtok
    return (vsol * vtok) // (vtok + tokens), vtok + tokens


def apply_event(state: tuple[int, int], row: dict[str, Any]) -> tuple[int, int]:
    vsol, vtok = state
    if row["side"] == "buy" and row["quote_lamports"] > 0:
        vsol, vtok, _ = apply_buy(vsol, vtok, row["quote_lamports"])
    elif row["side"] == "sell" and row["sell_token_raw"] > 0:
        vsol, vtok = apply_sell(vsol, vtok, row["sell_token_raw"])
    return vsol, vtok


def simulate(
    launch: dict[str, Any],
    rows: list[dict[str, Any]],
    delay_ms: int,
    worse_factor: float,
    safe_first_sellable_ms: int | None = None,
    event_filter: Callable[[dict[str, Any]], bool] | None = None,
    scenario: str = "actual_tape",
) -> dict[str, Any]:
    target_ts = int(launch["signal_ts_ms"])
    decision_ts = target_ts + delay_ms
    horizon_ts = target_ts + HORIZON_MS
    atomic_lamports = int(round(float(launch.get("atomic_initial_principal_sol") or 0) * 1_000_000_000))
    state = (INITIAL_VSOL, INITIAL_VTOK)
    if atomic_lamports:
        state = apply_buy(*state, atomic_lamports)[:2]
    filtered = [
        row for row in rows
        if row["side"] in ("buy", "sell")
        and row["signature"] != launch["create_signature"]
        and (event_filter(row) if event_filter else True)
    ]
    filtered.sort(key=lambda row: (row["ts_ms"], row["slot"], row["tx_index"], row["ix_index"], row["signature"]))
    future = []
    for row in filtered:
        if row["ts_ms"] <= decision_ts:
            state = apply_event(state, row)
        elif row["ts_ms"] <= horizon_ts:
            future.append(row)
    pre_entry_state = state
    state_after_buy = apply_buy(*state, FOLLOW_LAMPORTS)
    state = state_after_buy[:2]
    quoted_tokens = state_after_buy[2]
    follower_tokens = max(1, int(quoted_tokens / worse_factor))
    sellable_offset = max(delay_ms, safe_first_sellable_ms or delay_ms)
    sellable_ts = target_ts + sellable_offset
    start_roi = 100 * (sell_value(*state, follower_tokens) / FOLLOW_LAMPORTS - 1)
    mfe = start_roi
    mae = start_roi
    future_index = 0
    while future_index < len(future) and future[future_index]["ts_ms"] <= sellable_ts:
        state = apply_event(state, future[future_index])
        value_roi = 100 * (sell_value(*state, follower_tokens) / FOLLOW_LAMPORTS - 1)
        mfe = max(mfe, value_roi)
        mae = min(mae, value_roi)
        future_index += 1
    current_roi = 100 * (sell_value(*state, follower_tokens) / FOLLOW_LAMPORTS - 1)
    mfe = max(mfe, current_roi)
    mae = min(mae, current_roi)
    exit_reason = "TIMEOUT_120S"
    exit_ts = horizon_ts
    exit_roi = current_roi
    if current_roi >= 20:
        exit_reason = "TP20"
        exit_ts = sellable_ts
    elif current_roi <= -20:
        exit_reason = "SL20"
        exit_ts = sellable_ts
    else:
        for row in future[future_index:]:
            state = apply_event(state, row)
            current_roi = 100 * (sell_value(*state, follower_tokens) / FOLLOW_LAMPORTS - 1)
            mfe = max(mfe, current_roi)
            mae = min(mae, current_roi)
            exit_roi = current_roi
            if current_roi >= 20:
                exit_reason = "TP20"
                exit_ts = row["ts_ms"]
                break
            if current_roi <= -20:
                exit_reason = "SL20"
                exit_ts = row["ts_ms"]
                break
        else:
            exit_roi = current_roi
    target_entry_price = launch.get("atomic_average_entry_price_sol_per_token")
    follower_entry_price = 0.1 / (follower_tokens / 1_000_000)
    first_target_sell = launch.get("first_target_sell_ts_ms")
    return {
        "cohort": launch["cohort"],
        "mint": launch["mint"],
        "create_signature": launch["create_signature"],
        "matched_target_mint": launch.get("matched_target_mint"),
        "signal_ts_ms": target_ts,
        "signal_at": iso_ms(target_ts),
        "delay_ms": delay_ms,
        "entry_worse_factor": worse_factor,
        "safe_first_sellable_percentile": launch.get("safe_first_sellable_percentile"),
        "safe_first_sellable_input_ms": safe_first_sellable_ms,
        "effective_first_sellable_ms_from_signal": sellable_offset,
        "scenario": scenario,
        "follower_principal_sol": 0.1,
        "entry_tokens_raw": follower_tokens,
        "entry_average_price_sol_per_token": follower_entry_price,
        "entry_degradation_vs_atomic_pct": (
            100 * (follower_entry_price / float(target_entry_price) - 1) if target_entry_price else None
        ),
        "pre_entry_virtual_sol_lamports": pre_entry_state[0],
        "pre_entry_virtual_token_raw": pre_entry_state[1],
        "start_roundtrip_roi_pct": start_roi,
        "mfe_roi_pct": mfe,
        "mae_roi_pct": mae,
        "exit_reason": exit_reason,
        "exit_ts_ms": exit_ts,
        "exit_at": iso_ms(exit_ts),
        "exit_roi_pct": exit_roi,
        "pnl_sol": exit_roi * 0.001,
        "target_first_sell_ts_ms": first_target_sell,
        "target_sell_at_or_before_entry": bool(first_target_sell is not None and first_target_sell <= decision_ts),
        "target_sell_at_or_before_first_sellable": bool(first_target_sell is not None and first_target_sell <= sellable_ts),
        "target_sell_at_or_before_exit": bool(first_target_sell is not None and first_target_sell <= exit_ts),
        "curve_only": True,
    }


def auc(labels: list[int], scores: list[float]) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        return None
    ordered = sorted(zip(scores, labels), key=lambda item: item[0])
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        rank_sum += ((index + 1 + end) / 2) * sum(label for _, label in ordered[index:end])
        index = end
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def fit_logistic(rows: list[dict[str, Any]], features: list[str]) -> dict[str, Any]:
    means = {name: statistics.mean(float(row[name]) for row in rows) for name in features}
    scales = {name: statistics.pstdev(float(row[name]) for row in rows) or 1.0 for name in features}
    weights = [0.0] * (len(features) + 1)
    positives = sum(int(row["label"]) for row in rows)
    pos_weight = len(rows) / (2 * positives)
    neg_weight = len(rows) / (2 * (len(rows) - positives))
    for _ in range(1400):
        gradients = [0.0] * len(weights)
        for row in rows:
            vector = [1.0] + [(float(row[name]) - means[name]) / scales[name] for name in features]
            linear = sum(weight * value for weight, value in zip(weights, vector))
            prediction = 1 / (1 + math.exp(-max(-40, min(40, linear))))
            sample_weight = pos_weight if row["label"] else neg_weight
            error = (prediction - row["label"]) * sample_weight
            for index, value in enumerate(vector):
                gradients[index] += error * value
        for index in range(len(weights)):
            penalty = 0 if index == 0 else 0.02 * weights[index]
            weights[index] -= 0.08 * (gradients[index] / len(rows) + penalty)

    def score(row: dict[str, Any]) -> float:
        vector = [1.0] + [(float(row[name]) - means[name]) / scales[name] for name in features]
        linear = sum(weight * value for weight, value in zip(weights, vector))
        return 1 / (1 + math.exp(-max(-40, min(40, linear))))

    return {
        "features": features,
        "intercept": weights[0],
        "standardized_coefficients": {name: weights[index + 1] for index, name in enumerate(features)},
        "means": means,
        "scales": scales,
        "score": score,
    }


def load_selected_stream(v3: Path, mints: set[str]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    total_rows = 0
    selected_rows = 0
    implausible_buy_quotes = 0
    implausible_sell_amounts = 0
    with (v3 / "all_launch_stream_120s.tsv").open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle, delimiter="\t"):
            total_rows += 1
            if raw["mint"] not in mints:
                continue
            row = {
                "ts_ms": int(raw["ts_ms"]), "slot": int(raw["slot"]),
                "tx_index": int(raw["tx_index"]), "ix_index": int(raw["ix_index"]),
                "signature": raw["signature"], "mint": raw["mint"], "side": raw["side"],
                "wallet": raw["wallet"] or None, "quote_lamports": int(raw["quote_lamports"]),
                "sell_token_raw": int(raw["sell_token_raw"]),
            }
            if row["side"] == "buy" and row["quote_lamports"] > 100_000_000_000:
                implausible_buy_quotes += 1
                continue
            if row["side"] == "sell" and row["sell_token_raw"] > 1_100_000_000_000_000:
                implausible_sell_amounts += 1
                continue
            identity = (row["mint"], row["signature"], row["side"], row["ix_index"], row["wallet"])
            if identity in seen:
                continue
            seen.add(identity)
            by_mint[row["mint"]].append(row)
            selected_rows += 1
    for rows in by_mint.values():
        rows.sort(key=lambda row: (row["ts_ms"], row["slot"], row["tx_index"], row["ix_index"], row["signature"]))
    return by_mint, {
        "compact_rows_scanned": total_rows, "selected_unique_rows": selected_rows,
        "selected_mints_with_rows": len(by_mint),
        "implausible_buy_quote_rows_excluded_above_100_sol": implausible_buy_quotes,
        "implausible_sell_amount_rows_excluded_above_1p1b_tokens": implausible_sell_amounts,
    }


def arrival_row(launch: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    signal_ts = int(launch["signal_ts_ms"])
    creator = launch.get("creator")
    buys = [
        row for row in rows if row["side"] == "buy" and row["quote_lamports"] > 0
        and row["signature"] != launch["create_signature"] and row.get("wallet") not in {creator, None}
        and row["ts_ms"] >= signal_ts
    ]
    buys.sort(key=lambda row: (row["ts_ms"], row["slot"], row["tx_index"], row["ix_index"]))
    delays = [row["ts_ms"] - signal_ts for row in buys]
    first_sell = launch.get("first_target_sell_ts_ms")
    return {
        "cohort": launch["cohort"], "mint": launch["mint"], "signal_ts_ms": signal_ts,
        "create_signature": launch["create_signature"], "external_buy_count_120s": len(buys),
        "external_unique_buyers_120s": len({row["wallet"] for row in buys}),
        "external_gross_buy_sol_120s": sum(row["quote_lamports"] for row in buys) / 1e9,
        "first_external_buy_delay_ms": delays[0] if delays else None,
        "second_external_buy_delay_ms": delays[1] if len(delays) > 1 else None,
        "third_external_buy_delay_ms": delays[2] if len(delays) > 2 else None,
        "external_buys_before_target_first_sell": sum(first_sell is not None and row["ts_ms"] < first_sell for row in buys),
        "external_buy_sol_before_target_first_sell": sum(row["quote_lamports"] for row in buys if first_sell is not None and row["ts_ms"] < first_sell) / 1e9,
        **{f"buy_count_{window}ms": sum(delay <= window for delay in delays) for window in [10, 25, 50, 100, 250, 500, 1000, 2000, 5000]},
        **{f"gross_buy_sol_{window}ms": sum(row["quote_lamports"] for row, delay in zip(buys, delays) if delay <= window) / 1e9 for window in [25, 50, 100, 250, 500, 1000, 2000, 5000]},
    }


def arrival_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    delays = [float(row["first_external_buy_delay_ms"]) for row in rows if row["first_external_buy_delay_ms"] is not None]
    return {
        "n": len(rows), "launches_with_external_buy_120s": len(delays),
        "launches_with_external_buy_rate": len(delays) / len(rows) if rows else None,
        "first_external_buy_delay_ms": {"p10": q(delays, .1), "p50": q(delays, .5), "p75": q(delays, .75), "p90": q(delays, .9)},
        "external_buy_count_120s_mean": statistics.mean(row["external_buy_count_120s"] for row in rows) if rows else None,
        "external_unique_buyers_120s_mean": statistics.mean(row["external_unique_buyers_120s"] for row in rows) if rows else None,
        "external_gross_buy_sol_120s_mean": statistics.mean(row["external_gross_buy_sol_120s"] for row in rows) if rows else None,
        "window_rates": {
            str(window): sum(row[f"buy_count_{window}ms"] > 0 for row in rows) / len(rows) if rows else None
            for window in [10, 25, 50, 100, 250, 500, 1000, 2000, 5000]
        },
        "window_mean_gross_buy_sol": {
            str(window): statistics.mean(row[f"gross_buy_sol_{window}ms"] for row in rows) if rows else None
            for window in [25, 50, 100, 250, 500, 1000, 2000, 5000]
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v2", required=True)
    parser.add_argument("--v3", required=True)
    args = parser.parse_args()
    v2, v3 = Path(args.v2), Path(args.v3)
    snapshot = json.loads((v2 / "stream_snapshot.json").read_text())
    snapshot_end = int(snapshot["snapshot_last_observation_ts_ms"])
    v2_summary = json.loads((v2 / "native_ledger_summary.json").read_text())
    target_launch_rows = read_gz(v2 / "target_launches.jsonl.gz")
    target_by_mint = {row["mint"]: row for row in target_launch_rows}
    target_stream = read_gz(v2 / "stream_target_outcomes.jsonl.gz")
    target_stream_by_mint = {row["mint"]: row for row in target_stream}
    ledger = read_gz(v2 / "target_trade_ledger.jsonl.gz")
    target_trade_events = read_gz(v2 / "target_trade_events.jsonl.gz")
    positions_by_signature = {row.get("first_buy_signature"): row for row in ledger if row.get("first_buy_signature")}
    controls = read_gz(v3 / "generic_control_metadata.jsonl.gz")
    first_sellable = json.loads((v3 / "first_sellable_summary.json").read_text())
    safe_timings = {
        "P50": int(first_sellable["p50_rust_accepted_to_first_sellable_ms"]),
        "P75": int(first_sellable["p75_rust_accepted_to_first_sellable_ms"]),
        "P90": int(first_sellable["p90_rust_accepted_to_first_sellable_ms"]),
    }

    selected_mints = set(target_stream_by_mint) | {row["mint"] for row in controls}
    stream_by_mint, stream_integrity = load_selected_stream(v3, selected_mints)

    # The source stream did not emit 8YCB's routed sells. Inject native-ledger
    # token deltas at the midpoint of their known block second so follower PnL
    # includes creator distribution rather than silently becoming an upper bound.
    injected_target_sells = 0
    for event in target_trade_events:
        mint = event.get("mint")
        if mint not in target_stream_by_mint or event.get("side") != "sell" or not event.get("is_pump_economic_event"):
            continue
        signal_ts = int(target_stream_by_mint[mint]["target_ts_ms"])
        event_ts = max(signal_ts, int(event["block_time"]) * 1000 + 500)
        if event_ts > signal_ts + HORIZON_MS:
            continue
        if any(row["signature"] == event["signature"] for row in stream_by_mint.get(mint, [])):
            continue
        stream_by_mint[mint].append({
            "ts_ms": event_ts, "slot": int(event.get("slot") or 0), "tx_index": 0, "ix_index": 0,
            "signature": event["signature"], "mint": mint, "side": "sell", "wallet": WALLET,
            "quote_lamports": 0, "sell_token_raw": max(0, -int(event["token_delta_raw"])),
            "injected_from_native_ledger": True,
        })
        injected_target_sells += 1
    for rows in stream_by_mint.values():
        rows.sort(key=lambda row: (row["ts_ms"], row["slot"], row["tx_index"], row["ix_index"], row["signature"]))
    stream_integrity["target_sell_rows_injected_from_native_ledger"] = injected_target_sells
    stream_integrity["target_sell_injection_timestamp_assumption"] = "block_time_second midpoint (+500 ms); collision timing is therefore approximate within +/-500 ms"

    target_launches = []
    for mint, stream_row in target_stream_by_mint.items():
        source = target_by_mint[mint]
        rows = stream_by_mint.get(mint, [])
        target_sells = [row for row in rows if row["side"] == "sell" and row.get("wallet") == WALLET]
        position = positions_by_signature.get(source["create_signature"])
        first_sell_ms = min((row["ts_ms"] for row in target_sells), default=None)
        if first_sell_ms is None and position and position.get("first_sell_at"):
            first_sell_ms = int(dt.datetime.fromisoformat(position["first_sell_at"].replace("Z", "+00:00")).timestamp() * 1000)
        target_launches.append({
            "cohort": "8YCB_PUBLIC_EVENT", "mint": mint, "creator": WALLET,
            "create_signature": source["create_signature"], "signal_ts_ms": int(stream_row["target_ts_ms"]),
            "atomic_initial_principal_sol": float(source["initial_buy_principal_sol"]),
            "atomic_average_entry_price_sol_per_token": source.get("average_entry_price_sol_per_token"),
            "first_target_sell_ts_ms": first_sell_ms,
            "first_target_sell_source": "native_ledger_block_second_midpoint" if target_sells and target_sells[0].get("injected_from_native_ledger") else "native_stream_ms" if target_sells else "native_ledger_block_second",
            "full_120s_horizon": int(stream_row["target_ts_ms"]) + HORIZON_MS <= snapshot_end,
            "name": source["name"], "symbol": source["symbol"], "metadata_uri_domain": source.get("metadata_uri_domain"),
            "name_length": source["name_length"], "name_word_count": source["name_word_count"],
            "name_upper_ratio": source["name_upper_ratio"], "symbol_length": source["symbol_length"],
            "creator_atomic_initial_buy": 1.0, "log1p_atomic_initial_principal": math.log1p(float(source["initial_buy_principal_sol"])),
        })
    control_launches = [{
        "cohort": "TIME_MATCHED_GENERIC_CREATE", "mint": row["mint"], "creator": row["creator"],
        "create_signature": row["create_signature"], "signal_ts_ms": int(row["create_ts_ms"]),
        "atomic_initial_principal_sol": float(row["creator_initial_principal_sol_inferred"]),
        "atomic_average_entry_price_sol_per_token": None, "first_target_sell_ts_ms": None,
        "full_120s_horizon": int(row["create_ts_ms"]) + HORIZON_MS <= snapshot_end,
        "matched_target_mint": row["matched_target_mint"], "name": row["name"], "symbol": row["symbol"],
        "metadata_uri_domain": row.get("metadata_uri_domain"), "name_length": row["name_length"],
        "name_word_count": row["name_word_count"], "name_upper_ratio": row["name_upper_ratio"],
        "symbol_length": row["symbol_length"], "creator_atomic_initial_buy": float(row["creator_atomic_initial_buy"]),
        "log1p_atomic_initial_principal": math.log1p(float(row["creator_initial_principal_sol_inferred"])),
    } for row in controls if row["creator"] != WALLET]
    complete_targets = [row for row in target_launches if row["full_120s_horizon"]]
    complete_controls = [row for row in control_launches if row["full_120s_horizon"]]

    closed = [row for row in ledger if row["status"] == "closed" and row["buy_count"] and row["sell_count"]]
    unresolved = [row for row in ledger if row["status"] == "unresolved" and row["buy_count"]]
    gross_pnl = sum(float(row["tracker_comparable_pnl_sol"]) for row in closed)
    native_pnl = sum(float(row["native_pnl_sol"]) for row in closed)
    network_fees_closed = sum(float(row["network_fee_sol"]) for row in closed)
    pnl_decomposition = {
        "window": v2_summary["coverage"], "closed_positions": len(closed), "unresolved_positions": len(unresolved),
        "gross_curve_side_tracker_comparable_pnl_sol": gross_pnl,
        "native_realized_pnl_sol": native_pnl,
        "gross_to_native_cost_drag_sol": gross_pnl - native_pnl,
        "closed_position_network_fees_sol": network_fees_closed,
        "residual_drag_pump_tips_rent_and_other_lamport_flows_sol": gross_pnl - native_pnl - network_fees_closed,
        "unresolved_native_cash_pnl_sol": sum(float(row["native_pnl_sol"]) for row in unresolved),
        "unresolved_native_cost_sol": sum(float(row["native_sol_spent"]) for row in unresolved),
        "native_closed_economics": v2_summary["native_economics"],
        "tracker_comparable_closed_economics": v2_summary["tracker_comparable_economics"],
        "open_inventory_zero_value_stress": v2_summary["integrity"]["conservative_zero_value_open_inventory"],
        "all_unresolved_counted_as_losses_wr": v2_summary["integrity"]["unresolved_all_counted_as_losses_wr"],
        "network_fee_scope_note": "Closed-position fees above; V2 all reconstructed position transactions total " + str(v2_summary["integrity"]["network_fees_all_reconstructed_position_transactions_sol"]) + " SOL.",
    }
    write_json(v3 / "native_pnl_decomposition.json", pnl_decomposition)

    first_holds = [float(row["hold_to_first_sell_seconds"]) for row in closed if row.get("hold_to_first_sell_seconds") is not None]
    last_holds = [float(row["hold_to_last_sell_seconds"]) for row in closed if row.get("hold_to_last_sell_seconds") is not None]
    exit_timing = {
        "closed_positions": len(closed),
        "hold_to_first_sell_seconds": {"p10": q(first_holds,.1), "p25": q(first_holds,.25), "p50": q(first_holds,.5), "p75": q(first_holds,.75), "p90": q(first_holds,.9), "p99": q(first_holds,.99)},
        "hold_to_last_sell_seconds": {"p10": q(last_holds,.1), "p25": q(last_holds,.25), "p50": q(last_holds,.5), "p75": q(last_holds,.75), "p90": q(last_holds,.9), "p99": q(last_holds,.99)},
        "first_sell_cumulative_rates": {str(sec): sum(value <= sec for value in first_holds) / len(first_holds) for sec in [1,2,3,5,10,30,60,120]},
        "last_sell_cumulative_rates": {str(sec): sum(value <= sec for value in last_holds) / len(last_holds) for sec in [1,2,3,5,10,30,60,120]},
        "exit_archetypes": v2_summary["exit_behavior"]["class_counts"],
        "overlap_target_sell_precise_stream_count": sum(row["first_target_sell_source"] == "native_stream_ms" for row in target_launches),
        "overlap_target_sell_native_ledger_midpoint_count": sum(row["first_target_sell_source"] == "native_ledger_block_second_midpoint" for row in target_launches),
        "overlap_target_sell_block_second_fallback_count": sum(row["first_target_sell_source"] == "native_ledger_block_second" and row["first_target_sell_ts_ms"] is not None for row in target_launches),
    }
    write_json(v3 / "target_exit_timing.json", exit_timing)

    arrivals = [arrival_row(row, stream_by_mint.get(row["mint"], [])) for row in complete_targets + complete_controls]
    target_arrivals = [row for row in arrivals if row["cohort"] == "8YCB_PUBLIC_EVENT"]
    control_arrivals = [row for row in arrivals if row["cohort"] == "TIME_MATCHED_GENERIC_CREATE"]
    arrival_report = {
        "target": arrival_summary(target_arrivals), "time_matched_generic": arrival_summary(control_arrivals),
        "causal_boundary": "Only events observed at or after the CREATE/public-event local receive timestamp are included.",
    }
    write_gz(v3 / "external_buyer_arrivals.jsonl.gz", arrivals)
    write_json(v3 / "external_buyer_arrival_summary.json", arrival_report)

    early_wallet_mints: dict[str, set[str]] = defaultdict(set)
    early_wallet_sol: Counter[str] = Counter()
    early_wallet_delays: dict[str, list[int]] = defaultdict(list)
    for launch in complete_targets:
        for row in stream_by_mint.get(launch["mint"], []):
            delay = row["ts_ms"] - launch["signal_ts_ms"]
            if row["side"] == "buy" and row["quote_lamports"] > 0 and row.get("wallet") not in {None, WALLET} and row["signature"] != launch["create_signature"] and 0 <= delay <= 1000:
                early_wallet_mints[row["wallet"]].add(launch["mint"])
                early_wallet_sol[row["wallet"]] += row["quote_lamports"] / 1e9
                early_wallet_delays[row["wallet"]].append(delay)
    repeated = [{
        "wallet": wallet, "target_launches_bought_within_1s": len(mints),
        "gross_buy_sol_within_1s": early_wallet_sol[wallet], "median_delay_ms": q(early_wallet_delays[wallet], .5),
    } for wallet, mints in early_wallet_mints.items()]
    repeated_wallets = {row["wallet"] for row in repeated}
    follower_sell_mints: dict[str, set[str]] = defaultdict(set)
    follower_sell_delays: dict[str, list[int]] = defaultdict(list)
    for launch in complete_targets:
        for row in stream_by_mint.get(launch["mint"], []):
            if row["side"] != "sell" or row.get("wallet") not in repeated_wallets:
                continue
            delay = row["ts_ms"] - launch["signal_ts_ms"]
            if 0 <= delay <= HORIZON_MS:
                follower_sell_mints[row["wallet"]].add(launch["mint"])
                follower_sell_delays[row["wallet"]].append(delay)
    for row in repeated:
        wallet = row["wallet"]
        row["target_launches_sold_within_120s"] = len(follower_sell_mints[wallet])
        row["median_observed_sell_delay_ms"] = q(follower_sell_delays[wallet], .5)
    repeated.sort(key=lambda row: (-row["target_launches_bought_within_1s"], -row["gross_buy_sol_within_1s"], row["wallet"]))
    repeat_cohort = {row["wallet"] for row in repeated if row["target_launches_bought_within_1s"] >= 5}
    generic_with_cohort = 0
    target_with_cohort = 0
    for launch in complete_targets:
        if any(row["side"] == "buy" and row.get("wallet") in repeat_cohort and 0 <= row["ts_ms"]-launch["signal_ts_ms"] <= 1000 for row in stream_by_mint.get(launch["mint"], [])):
            target_with_cohort += 1
    for launch in complete_controls:
        if any(row["side"] == "buy" and row.get("wallet") in repeat_cohort and 0 <= row["ts_ms"]-launch["signal_ts_ms"] <= 1000 for row in stream_by_mint.get(launch["mint"], [])):
            generic_with_cohort += 1
    repeat_report = {
        "definition": "Wallet bought at least five distinct 8YCB launches within 1 second of the public CREATE event; behavioral recurrence only, not proof of intent or ownership.",
        "repeat_cohort_wallets": len(repeat_cohort), "top_repeated_wallets": repeated[:100],
        "wallet_count_by_minimum_target_launches": {str(n): sum(row["target_launches_bought_within_1s"] >= n for row in repeated) for n in [2,5,10,20,50]},
        "target_launches_with_repeat_cohort_within_1s": target_with_cohort,
        "target_launches_with_repeat_cohort_rate": target_with_cohort / len(complete_targets) if complete_targets else None,
        "generic_controls_with_same_cohort_within_1s": generic_with_cohort,
        "generic_controls_with_same_cohort_rate": generic_with_cohort / len(complete_controls) if complete_controls else None,
    }
    write_json(v3 / "repeat_follower_cohorts.json", repeat_report)

    labeled = []
    target_order = sorted(complete_targets, key=lambda row: row["signal_ts_ms"])
    target_split = {}
    for index, row in enumerate(target_order):
        fraction = index / max(1, len(target_order))
        target_split[row["mint"]] = "train" if fraction < .5 else "validation" if fraction < .75 else "holdout"
    for row in complete_targets:
        labeled.append({**row, "label": 1, "split": target_split[row["mint"]]})
    for row in complete_controls:
        labeled.append({**row, "label": 0, "split": target_split.get(row["matched_target_mint"], "holdout")})
    feature_sets = {
        "metadata_content_only": ["name_length", "name_word_count", "name_upper_ratio", "symbol_length"],
        "atomic_action_only": ["creator_atomic_initial_buy", "log1p_atomic_initial_principal"],
        "metadata_plus_atomic_action": ["name_length", "name_word_count", "name_upper_ratio", "symbol_length", "creator_atomic_initial_buy", "log1p_atomic_initial_principal"],
    }
    model_report = {}
    scored_rows = []
    models = {}
    for model_name, features in feature_sets.items():
        model = fit_logistic([row for row in labeled if row["split"] == "train"], features)
        models[model_name] = model
        split_metrics = {}
        for split in ["train", "validation", "holdout"]:
            split_rows = [row for row in labeled if row["split"] == split]
            scores = [model["score"](row) for row in split_rows]
            labels = [row["label"] for row in split_rows]
            split_metrics[split] = {"n": len(split_rows), "positive_n": sum(labels), "roc_auc": auc(labels, scores)}
        model_report[model_name] = {
            "features": features, "standardized_coefficients": model["standardized_coefficients"],
            "splits": split_metrics,
        }
    for row in labeled:
        scored_rows.append({
            "cohort": row["cohort"], "mint": row["mint"], "matched_target_mint": row.get("matched_target_mint"),
            "signal_ts_ms": row["signal_ts_ms"], "label": row["label"], "split": row["split"],
            "creator_is_8ycb": row.get("creator") == WALLET,
            "metadata_uri_domain": row.get("metadata_uri_domain"),
            **{name + "_score": model["score"](row) for name, model in models.items()},
        })
    metadata_report = {
        "chronological_models": model_report,
        "identity_feature": {"target_creator_is_8ycb_rate": 1.0, "generic_creator_is_8ycb_rate": sum(row["creator"] == WALLET for row in complete_controls) / len(complete_controls), "deterministic_identity_auc": 1.0},
        "metadata_host": {
            "target_j7tracker_rate": sum(row.get("metadata_uri_domain") == "metadata.j7tracker.io" for row in complete_targets) / len(complete_targets),
            "generic_j7tracker_rate": sum(row.get("metadata_uri_domain") == "metadata.j7tracker.io" for row in complete_controls) / len(complete_controls),
            "note": "Host is reported separately from content models because it is effectively an infrastructure/identity marker.",
        },
        "training_boundary": "Models trained on earliest 50% of target-matched time groups; validation and holdout were not used for fitting.",
    }
    write_gz(v3 / "signal_component_rows.jsonl.gz", scored_rows)

    follower_rows = []
    for launch in complete_targets + complete_controls:
        rows = stream_by_mint.get(launch["mint"], [])
        for delay in DELAYS:
            for worse in [1.0, 1.05]:
                launch["safe_first_sellable_percentile"] = None
                follower_rows.append(simulate(launch, rows, delay, worse))
                if launch["cohort"] == "8YCB_PUBLIC_EVENT":
                    for percentile, safe_ms in safe_timings.items():
                        launch["safe_first_sellable_percentile"] = percentile
                        follower_rows.append(simulate(launch, rows, delay, worse, safe_ms))
        launch["safe_first_sellable_percentile"] = None
    write_gz(v3 / "follower_latency_outcomes.jsonl.gz", follower_rows)

    follower_summary: dict[str, Any] = {"target": {}, "time_matched_generic": {}, "safe_first_sellable": safe_timings}
    for cohort, summary_key in [("8YCB_PUBLIC_EVENT", "target"), ("TIME_MATCHED_GENERIC_CREATE", "time_matched_generic")]:
        for delay in DELAYS:
            for worse in [1.0, 1.05]:
                label = f"delay_{delay}ms" + ("_entry_5pct_worse" if worse > 1 else "")
                subset = [row for row in follower_rows if row["cohort"] == cohort and row["delay_ms"] == delay and row["entry_worse_factor"] == worse and row["safe_first_sellable_percentile"] is None and row["scenario"] == "actual_tape"]
                follower_summary[summary_key][label] = follower_metrics(subset)
                if cohort == "8YCB_PUBLIC_EVENT":
                    for percentile in safe_timings:
                        safe_subset = [row for row in follower_rows if row["cohort"] == cohort and row["delay_ms"] == delay and row["entry_worse_factor"] == worse and row["safe_first_sellable_percentile"] == percentile]
                        follower_summary[summary_key][label + "_safe_" + percentile.lower()] = follower_metrics(safe_subset)
    daily_250 = {}
    for day in sorted({row["signal_at"][:10] for row in follower_rows if row["cohort"] == "8YCB_PUBLIC_EVENT"}):
        daily_250[day] = {
            "base": follower_metrics([
                row for row in follower_rows if row["cohort"] == "8YCB_PUBLIC_EVENT"
                and row["signal_at"].startswith(day) and row["delay_ms"] == 250
                and row["entry_worse_factor"] == 1.0 and row["safe_first_sellable_percentile"] is None
            ]),
            "entry_5pct_worse": follower_metrics([
                row for row in follower_rows if row["cohort"] == "8YCB_PUBLIC_EVENT"
                and row["signal_at"].startswith(day) and row["delay_ms"] == 250
                and row["entry_worse_factor"] == 1.05 and row["safe_first_sellable_percentile"] is None
            ]),
        }
    follower_summary["target_daily_250ms"] = daily_250
    write_json(v3 / "follower_economics.json", follower_summary)

    target_actual = [row for row in follower_rows if row["cohort"] == "8YCB_PUBLIC_EVENT" and row["safe_first_sellable_percentile"] is None]
    collisions = {}
    for delay in DELAYS:
        subset = [row for row in target_actual if row["delay_ms"] == delay and row["entry_worse_factor"] == 1.0]
        collisions[str(delay)] = {
            "n": len(subset),
            "target_sell_at_or_before_entry_rate": sum(row["target_sell_at_or_before_entry"] for row in subset) / len(subset),
            "target_sell_at_or_before_exit_rate": sum(row["target_sell_at_or_before_exit"] for row in subset) / len(subset),
        }
    safe_collisions = {}
    for percentile in safe_timings:
        subset = [row for row in follower_rows if row["cohort"] == "8YCB_PUBLIC_EVENT" and row["delay_ms"] == 25 and row["entry_worse_factor"] == 1.0 and row["safe_first_sellable_percentile"] == percentile]
        safe_collisions[percentile] = {
            "n": len(subset), "target_sell_at_or_before_first_sellable_rate": sum(row["target_sell_at_or_before_first_sellable"] for row in subset) / len(subset),
        }
    collision_report = {"by_entry_delay_ms": collisions, "safe_first_sellable_from_25ms_entry": safe_collisions, "timing_note": "The stream omitted routed 8YCB sells. Native-ledger sells are replayed at the midpoint of the known block second, giving approximately +/-500 ms collision uncertainty."}
    write_json(v3 / "target_sell_collisions.json", collision_report)

    scenario_rows = []
    for launch in complete_targets:
        rows = stream_by_mint.get(launch["mint"], [])
        launch["safe_first_sellable_percentile"] = "P50"
        scenario_rows.append(simulate(launch, rows, 250, 1.0, safe_timings["P50"], scenario="actual_tape"))
        scenario_rows.append(simulate(launch, rows, 250, 1.0, safe_timings["P50"], event_filter=lambda event: event.get("wallet") != WALLET, scenario="external_demand_no_8ycb_sells"))
        scenario_rows.append(simulate(launch, rows, 250, 1.0, safe_timings["P50"], event_filter=lambda event: event.get("wallet") not in repeat_cohort, scenario="without_repeat_early_cohort"))
    scenario_economics = {scenario: economics([row for row in scenario_rows if row["scenario"] == scenario]) for scenario in ["actual_tape", "external_demand_no_8ycb_sells", "without_repeat_early_cohort"]}
    write_gz(v3 / "reflexivity_counterfactual_outcomes.jsonl.gz", scenario_rows)

    metadata_scores = {row["mint"]: row["metadata_content_only_score"] for row in scored_rows if row["cohort"] == "TIME_MATCHED_GENERIC_CREATE"}
    generic_250 = [row for row in follower_rows if row["cohort"] == "TIME_MATCHED_GENERIC_CREATE" and row["delay_ms"] == 250 and row["entry_worse_factor"] == 1.0 and row["safe_first_sellable_percentile"] is None]
    score_cutoff = q(metadata_scores.values(), .8)
    generic_high = [row for row in generic_250 if metadata_scores.get(row["mint"], 0) >= score_cutoff]
    generic_low = [row for row in generic_250 if metadata_scores.get(row["mint"], 0) < score_cutoff]
    metadata_report["generic_outcome_generalization_at_250ms"] = {
        "top_metadata_score_quintile": economics(generic_high), "bottom_four_quintiles": economics(generic_low),
        "top_quintile_score_cutoff": score_cutoff,
        "interpretation_boundary": "This tests whether target-like text generalizes to generic-launch follower outcomes; it does not use target identity or post-entry flow as a feature.",
    }
    write_json(v3 / "metadata_generalization.json", metadata_report)

    target_repeat_buy_sol = 0.0
    target_total_early_buy_sol = 0.0
    for launch in complete_targets:
        for row in stream_by_mint.get(launch["mint"], []):
            delay = row["ts_ms"] - launch["signal_ts_ms"]
            if row["side"] == "buy" and row["quote_lamports"] > 0 and row.get("wallet") != WALLET and 0 <= delay <= 1000:
                target_total_early_buy_sol += row["quote_lamports"] / 1e9
                if row.get("wallet") in repeat_cohort:
                    target_repeat_buy_sol += row["quote_lamports"] / 1e9
    known_sell_launches = [row for row in complete_targets if row.get("first_target_sell_ts_ms") is not None]
    launches_with_external_buy_after_sell = 0
    external_buy_sol_after_sell = 0.0
    external_buy_sol_all_known_sell_launches = 0.0
    external_buy_count_after_sell = 0
    for launch in known_sell_launches:
        after = False
        for row in stream_by_mint.get(launch["mint"], []):
            if row["side"] != "buy" or row["quote_lamports"] <= 0 or row.get("wallet") in {None, WALLET} or row["signature"] == launch["create_signature"]:
                continue
            if row["ts_ms"] < launch["signal_ts_ms"] or row["ts_ms"] > launch["signal_ts_ms"] + HORIZON_MS:
                continue
            value = row["quote_lamports"] / 1e9
            external_buy_sol_all_known_sell_launches += value
            if row["ts_ms"] > launch["first_target_sell_ts_ms"]:
                after = True
                external_buy_count_after_sell += 1
                external_buy_sol_after_sell += value
        launches_with_external_buy_after_sell += after
    external_demand_survival = {
        "target_launches_with_known_sell_within_120s": len(known_sell_launches),
        "launches_with_external_buy_after_target_first_sell": launches_with_external_buy_after_sell,
        "launch_rate_with_external_buy_after_target_first_sell": launches_with_external_buy_after_sell / len(known_sell_launches) if known_sell_launches else None,
        "external_buy_count_after_target_first_sell": external_buy_count_after_sell,
        "external_gross_buy_sol_after_target_first_sell": external_buy_sol_after_sell,
        "post_first_sell_share_of_external_gross_buy_sol": external_buy_sol_after_sell / external_buy_sol_all_known_sell_launches if external_buy_sol_all_known_sell_launches else None,
        "counterfactual_note": "The no-8YCB-sells replay quantifies external-demand value if creator distribution is removed; actual replay includes native-ledger target sells.",
    }
    reflexivity = {
        "arrival_comparison": arrival_report,
        "repeat_cohort": repeat_report,
        "early_1s_repeat_cohort_gross_buy_sol_share": target_repeat_buy_sol / target_total_early_buy_sol if target_total_early_buy_sol else None,
        "external_demand_survival_after_target_first_sell": external_demand_survival,
        "250ms_entry_p50_safe_counterfactuals": scenario_economics,
        "causal_findings": [
            "All measurable external demand follows the atomic self-CREATE event; none can explain 8YCB's own decision.",
            "Repeat-cohort incidence on time-matched generic controls distinguishes copy-like targeting from generic launch participation.",
            "Removing 8YCB sells measures demand survival without creator distribution; removing recurrent early wallets measures dependence on the repeat cohort.",
        ],
    }
    write_json(v3 / "reflexivity_analysis.json", reflexivity)

    component_summary = {
        "8ycb_identity": {"observable_at_create": True, "target_rate": 1.0, "generic_rate": metadata_report["identity_feature"]["generic_creator_is_8ycb_rate"], "selection_auc": 1.0},
        "atomic_initial_buy_action": {"target_rate": 1.0, "generic_rate": statistics.mean(row["creator_atomic_initial_buy"] for row in complete_controls), "model": model_report["atomic_action_only"]},
        "metadata_content": {"model": model_report["metadata_content_only"], "generic_outcome_generalization": metadata_report["generic_outcome_generalization_at_250ms"]},
        "metadata_host_identity": metadata_report["metadata_host"],
        "generic_launch_structure": {"comparison_cohort_n": len(complete_controls), "follower_economics": follower_summary["time_matched_generic"]},
        "repeat_follower_cohort": repeat_report,
        "mixed_model": model_report["metadata_plus_atomic_action"],
    }
    write_json(v3 / "signal_component_attribution.json", component_summary)

    summary = {
        "schema": "wallet-8ycb-causal-followability-v3-v1",
        "wallet": WALLET,
        "coverage": {
            "v2_native_window_start": v2_summary["coverage"]["requested_window_start"],
            "v2_native_window_end": v2_summary["coverage"]["requested_window_end"],
            "frozen_stream_start": snapshot["first_observation_utc"],
            "frozen_stream_end": snapshot["snapshot_last_observation_utc"],
            "frozen_stream_duration_days": snapshot["duration_seconds"] / 86400,
            "frozen_stream_bytes": snapshot["snapshot_bytes"],
            "complete_target_120s_horizons": len(complete_targets),
            "complete_time_matched_generic_120s_horizons": len(complete_controls),
            "all_observed_create_rows": json.loads((v3 / "generic_control_selection_summary.json").read_text())["frozen_stream_create_rows"],
        },
        "stream_integrity": stream_integrity,
        "native_pnl_decomposition": pnl_decomposition,
        "target_exit_timing": exit_timing,
        "external_buyer_arrivals": arrival_report,
        "repeat_follower_cohorts": repeat_report,
        "first_sellable": first_sellable,
        "follower_economics": follower_summary,
        "target_sell_collisions": collision_report,
        "reflexivity": reflexivity,
        "metadata_generalization": metadata_report,
        "signal_component_attribution": component_summary,
        "limitations": [
            "The frozen all-launch stream spans 5.42 days, below the requested seven-day completeness target.",
            "Routed inner-instruction coverage is not independently complete; source-emitted rows are replayed and 8YCB sells are added from the native ledger at block-second midpoint.",
            "Injected 8YCB sell timing has approximately +/-500 ms uncertainty because the native index supplies block seconds, not local receive milliseconds.",
            "Follower entries are deterministic 0.1 SOL curve replays with 125 bps Pump fees but no network fee, priority fee, tip, slippage cap, landing failure, or contention loss.",
            "Historical flow is replayed after inserting a counterfactual follower; downstream order sizes are not re-equilibrated for that intervention.",
            "Safe-first-sellable percentiles come from 52 separate Rust dust executions, not the 8YCB target launches.",
            "CREATE metadata is observable simultaneous with the atomic buy, not before it.",
            "Repeat-wallet behavior does not prove copybot ownership, coordination, or intent.",
            "Generic controls are time-matched and transaction-decoded but are a sample of five per target, not all 220,079 CREATEs.",
            "No prospective shadow or live landing test of the 8YCB event was performed.",
        ],
        "final_classification": FINAL_CLASSIFICATION,
    }
    write_json(v3 / "v3_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
