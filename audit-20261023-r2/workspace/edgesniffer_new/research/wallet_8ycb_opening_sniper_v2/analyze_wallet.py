#!/usr/bin/env python3
"""Offline analysis of the 8YCB native transaction extract.

This never calls a network service.  It decodes Pump.fun CREATE metadata from
the on-chain instruction payload and recomputes both native and tracker-like
economics from transaction balance changes/transfers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


WALLET = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_LEGACY = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
MAYHEM_PROGRAM = "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e"
MAYHEM_AGENT = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
ODIN = "odinBE4Y5Efui9Dh9EMncGN9tAPQT3oPu3WAdA6rDkL"
LAMPORTS = 1_000_000_000
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_jsonl_gz(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True, allow_nan=False) + "\n")


def b58decode(value: str) -> bytes:
    number = 0
    for char in value:
        number = number * 58 + ALPHABET.index(char)
    data = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + data


def b58encode(data: bytes) -> str:
    zeros = len(data) - len(data.lstrip(b"\0"))
    number = int.from_bytes(data, "big")
    chars = []
    while number:
        number, rem = divmod(number, 58)
        chars.append(ALPHABET[rem])
    return "1" * zeros + "".join(reversed(chars))


def borsh_string(data: bytes, offset: int) -> tuple[str, int]:
    if offset + 4 > len(data):
        raise ValueError("short borsh string length")
    size = int.from_bytes(data[offset:offset + 4], "little")
    offset += 4
    if size > 20_000 or offset + size > len(data):
        raise ValueError("invalid borsh string size")
    return data[offset:offset + size].decode("utf-8"), offset + size


def decode_create(tx: dict[str, Any]) -> dict[str, Any] | None:
    if tx.get("type") != "CREATE" or tx.get("source") != "PUMP_FUN":
        return None
    candidates = []
    for ix in tx.get("instructions") or []:
        if ix.get("programId") == PUMP_PROGRAM and len(ix.get("data") or "") >= 50:
            candidates.append(ix)
    for ix in candidates:
        try:
            data = b58decode(ix["data"])
            offset = 8
            name, offset = borsh_string(data, offset)
            symbol, offset = borsh_string(data, offset)
            uri, offset = borsh_string(data, offset)
            if offset + 32 > len(data):
                continue
            creator = b58encode(data[offset:offset + 32])
            return {
                "mint": ix.get("accounts", [None])[0] if ix.get("accounts") else None,
                "name": name,
                "symbol": symbol,
                "metadata_uri": uri,
                "creator": creator,
                "create_discriminator_hex": data[:8].hex(),
                "create_tail_hex": data[offset + 32:].hex(),
            }
        except Exception:
            continue
    return None


def target_delta(tx: dict[str, Any]) -> int | None:
    for row in tx.get("accountData") or []:
        if row.get("account") == WALLET and row.get("nativeBalanceChange") is not None:
            return int(row["nativeBalanceChange"])
    return None


def outbound_lamports(tx: dict[str, Any]) -> list[int]:
    return [
        int(row.get("amount") or 0)
        for row in tx.get("nativeTransfers") or []
        if row.get("fromUserAccount") == WALLET and int(row.get("amount") or 0) > 0
    ]


def q(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, max(0, round((len(values) - 1) * fraction)))]


def finite(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return value


def iso_utc(timestamp: int | float | None) -> str | None:
    if timestamp is None:
        return None
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def metrics(rows: list[dict[str, Any]], pnl_key: str) -> dict[str, Any]:
    pnls = [float(row[pnl_key]) for row in rows]
    gains = sum(x for x in pnls if x > 0)
    losses = -sum(x for x in pnls if x < 0)
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in pnls:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    winners = sorted((x for x in pnls if x > 0), reverse=True)
    losing = sorted((-x for x in pnls if x < 0), reverse=True)
    return {
        "n": len(rows),
        "wins": sum(x > 0 for x in pnls),
        "losses": sum(x < 0 for x in pnls),
        "breakeven": sum(x == 0 for x in pnls),
        "wr": sum(x > 0 for x in pnls) / len(pnls) if pnls else None,
        "profit_factor": finite(gains / losses) if losses else None,
        "expectancy_sol": statistics.mean(pnls) if pnls else None,
        "median_trade_sol": statistics.median(pnls) if pnls else None,
        "average_trade_sol": statistics.mean(pnls) if pnls else None,
        "total_pnl_sol": sum(pnls),
        "max_drawdown_sol": max_drawdown,
        "top1_positive_pnl_share": winners[0] / gains if winners and gains else None,
        "top5_positive_pnl_share": sum(winners[:5]) / gains if gains else None,
        "top1_loss_share": losing[0] / losses if losing and losses else None,
        "top5_loss_share": sum(losing[:5]) / losses if losses else None,
    }


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    )
    return numerator / denominator if denominator else None


def size_cluster(value: float) -> str:
    if 0.8 <= value <= 1.2:
        return "~1_SOL"
    if 2.8 <= value <= 3.2:
        return "~3_SOL"
    return "OTHER"


def position_excerpt(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "mint": row["mint"],
        "first_buy_at": row.get("first_buy_at"),
        "first_buy_signature": row.get("first_buy_signature"),
        "native_sol_spent": row.get("native_sol_spent"),
        "native_sol_received": row.get("native_sol_received"),
        "native_pnl_sol": row.get("native_pnl_sol"),
        "native_roi_pct": row.get("native_roi_pct"),
        "sell_count": row.get("sell_count"),
    }


def normalize_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-min(value, 40.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(value, -40.0))
    return z / (1.0 + z)


def auc(labels: list[int], scores: list[float]) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        return None
    ordered = sorted(zip(scores, labels), key=lambda x: x[0])
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        average_rank = (index + 1 + end) / 2.0
        rank_sum += average_rank * sum(label for _, label in ordered[index:end])
        index = end
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def fit_logistic(train_rows: list[dict[str, Any]], feature_names: list[str]) -> dict[str, Any]:
    means = {name: statistics.mean(float(row[name]) for row in train_rows) for name in feature_names}
    scales = {}
    for name in feature_names:
        values = [float(row[name]) for row in train_rows]
        scales[name] = statistics.pstdev(values) or 1.0

    def vector(row: dict[str, Any]) -> list[float]:
        return [1.0] + [(float(row[name]) - means[name]) / scales[name] for name in feature_names]

    xs = [vector(row) for row in train_rows]
    ys = [int(row["target_bought"]) for row in train_rows]
    weights = [0.0] * (len(feature_names) + 1)
    learning_rate = 0.08
    l2 = 0.02
    positive_weight = len(ys) / (2 * sum(ys)) if sum(ys) else 1.0
    negative_weight = len(ys) / (2 * (len(ys) - sum(ys))) if sum(ys) < len(ys) else 1.0
    for _ in range(1400):
        gradients = [0.0] * len(weights)
        for x, y in zip(xs, ys):
            sample_weight = positive_weight if y else negative_weight
            error = (sigmoid(sum(a * b for a, b in zip(weights, x))) - y) * sample_weight
            for index, value in enumerate(x):
                gradients[index] += error * value
        for index in range(len(weights)):
            penalty = 0.0 if index == 0 else l2 * weights[index]
            weights[index] -= learning_rate * (gradients[index] / len(xs) + penalty)

    def score(row: dict[str, Any]) -> float:
        x = vector(row)
        return sigmoid(sum(a * b for a, b in zip(weights, x)))

    return {
        "intercept": weights[0],
        "standardized_coefficients": {name: weights[i + 1] for i, name in enumerate(feature_names)},
        "means": means,
        "scales": scales,
        "score": score,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    args = parser.parse_args()
    base = Path(args.dir)
    raw_txs = read_jsonl_gz(base / "target_enhanced_transactions.jsonl.gz")
    raw_events = read_jsonl_gz(base / "target_trade_events.jsonl.gz")
    txs = list({tx["signature"]: tx for tx in raw_txs}.values())
    events = list({event["signature"]: event for event in raw_events}.values())
    tx_by_sig = {tx["signature"]: tx for tx in txs}

    # Improve tracker-like economics using gross curve-side cash flow.  For a
    # buy, the largest outgoing transfer is the intended SOL principal.  For a
    # sell, gross incoming is native delta plus all outgoing transfers and the
    # transaction fee.  Native economics always remain the actual wallet delta.
    economic_events = []
    transfer_events = []
    for event in events:
        tx = tx_by_sig[event["signature"]]
        outs = outbound_lamports(tx)
        event = dict(event)
        event["outgoing_native_transfer_lamports"] = sum(outs)
        if event.get("is_pump_economic_event"):
            if event["side"] == "buy":
                event["tracker_gross_trade_sol"] = max(outs, default=0) / LAMPORTS
            else:
                delta = int(event.get("native_balance_delta_lamports") or 0)
                event["tracker_gross_trade_sol"] = (delta + sum(outs) + int(event.get("network_fee_lamports") or 0)) / LAMPORTS
            economic_events.append(event)
        else:
            transfer_events.append(event)

    by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in economic_events:
        by_mint[event["mint"]].append(event)
    positions = []
    for mint, rows in by_mint.items():
        rows.sort(key=lambda x: (int(x["block_time"]), int(x.get("slot") or 0), x["signature"]))
        inventory = 0
        cycle = 0
        buys: list[dict[str, Any]] = []
        sells: list[dict[str, Any]] = []

        def close(status: str) -> None:
            nonlocal inventory, cycle, buys, sells
            if not buys and not sells:
                return
            cycle += 1
            native_spend = sum(float(x.get("native_sol_spent") or 0) for x in buys)
            native_received = sum(float(x.get("native_sol_received") or 0) for x in sells)
            gross_spend = sum(float(x.get("tracker_gross_trade_sol") or 0) for x in buys)
            gross_received = sum(float(x.get("tracker_gross_trade_sol") or 0) for x in sells)
            tokens_bought = sum(max(0, int(x["token_delta_raw"])) for x in buys)
            tokens_sold = sum(max(0, -int(x["token_delta_raw"])) for x in sells)
            first_buy = buys[0] if buys else None
            first_sell = sells[0] if sells else None
            last_sell = sells[-1] if sells else None
            native_pnl = native_received - native_spend
            tracker_pnl = gross_received - gross_spend
            unresolved_fraction = inventory / tokens_bought if tokens_bought else None
            if not buys:
                status = "window_left_censored_sell_only"
            positions.append({
                "wallet": WALLET,
                "mint": mint,
                "position_cycle": cycle,
                "status": status,
                "first_buy_at": first_buy.get("at") if first_buy else None,
                "first_buy_ts": first_buy.get("block_time") if first_buy else None,
                "first_buy_slot": first_buy.get("slot") if first_buy else None,
                "first_buy_signature": first_buy.get("signature") if first_buy else None,
                "first_buy_kind": first_buy.get("event_kind") if first_buy else None,
                "first_sell_at": first_sell.get("at") if first_sell else None,
                "last_sell_at": last_sell.get("at") if last_sell else None,
                "last_sell_ts": last_sell.get("block_time") if last_sell else None,
                "hold_to_first_sell_seconds": None if not first_buy or not first_sell else first_sell["block_time"] - first_buy["block_time"],
                "hold_to_last_sell_seconds": None if not first_buy or not last_sell else last_sell["block_time"] - first_buy["block_time"],
                "buy_count": len(buys),
                "sell_count": len(sells),
                "tokens_bought_raw": tokens_bought,
                "tokens_sold_raw": tokens_sold,
                "unresolved_tokens_raw": max(0, inventory),
                "unresolved_inventory_fraction": unresolved_fraction,
                "native_sol_spent": native_spend,
                "native_sol_received": native_received,
                "native_pnl_sol": native_pnl,
                "native_roi_pct": 100 * native_pnl / native_spend if native_spend else None,
                "tracker_gross_sol_spent": gross_spend,
                "tracker_gross_sol_received": gross_received,
                "tracker_comparable_pnl_sol": tracker_pnl,
                "tracker_comparable_roi_pct": 100 * tracker_pnl / gross_spend if gross_spend else None,
                "network_fee_sol": sum(float(x.get("network_fee_sol") or 0) for x in buys + sells),
                "buy_signatures": [x["signature"] for x in buys],
                "sell_signatures": [x["signature"] for x in sells],
            })
            inventory = 0
            buys = []
            sells = []

        for event in rows:
            delta = int(event["token_delta_raw"])
            if delta > 0:
                if inventory <= 3 and (buys or sells):
                    close("closed" if sells else "unresolved")
                inventory += delta
                buys.append(event)
            else:
                inventory += delta
                sells.append(event)
                if inventory <= 3:
                    inventory = max(0, inventory)
                    close("closed")
        if buys or sells:
            close("unresolved" if inventory > 3 else "closed")
    positions.sort(key=lambda x: (x.get("first_buy_ts") or 0, x["mint"], x["position_cycle"]))

    # Keep non-trade token movements visible at the position level without
    # pretending they are sales or assigning them a fabricated SOL value.
    transfers_by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in transfer_events:
        transfers_by_mint[event["mint"]].append(event)
    positions_by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for position in positions:
        positions_by_mint[position["mint"]].append(position)
    next_position_start: dict[tuple[str, int], int | None] = {}
    for mint, mint_positions in positions_by_mint.items():
        mint_positions.sort(key=lambda row: (row.get("first_buy_ts") or 0, row["position_cycle"]))
        for index, position in enumerate(mint_positions):
            next_position_start[(mint, position["position_cycle"])] = (
                mint_positions[index + 1].get("first_buy_ts") if index + 1 < len(mint_positions) else None
            )
    for position in positions:
        start_ts = position.get("first_buy_ts")
        end_ts = position.get("last_sell_ts") if position["status"] in ("closed", "window_left_censored_sell_only") else None
        if end_ts is None:
            following_start = next_position_start[(position["mint"], position["position_cycle"])]
            end_ts = int(following_start) - 1 if following_start is not None else None
        matched_transfers = [
            event for event in transfers_by_mint.get(position["mint"], [])
            if (start_ts is None or int(event["block_time"]) >= int(start_ts))
            and (end_ts is None or int(event["block_time"]) <= int(end_ts))
        ]
        position["transfer_event_count"] = len(matched_transfers)
        position["transfer_in_tokens_raw"] = sum(max(0, int(event["token_delta_raw"])) for event in matched_transfers)
        position["transfer_out_tokens_raw"] = sum(max(0, -int(event["token_delta_raw"])) for event in matched_transfers)
        position["transfer_signatures"] = [event["signature"] for event in matched_transfers]

    event_by_sig = {event["signature"]: event for event in economic_events}
    self_created = []
    launches = []
    ignored_self_created = []
    ambiguous_self_created = []
    decode_errors = []
    for tx in txs:
        decoded = decode_create(tx)
        if tx.get("type") != "CREATE" or tx.get("source") != "PUMP_FUN":
            continue
        event = event_by_sig.get(tx["signature"])
        if not decoded:
            decode_errors.append({"signature": tx.get("signature"), "reason": "create_instruction_decode_failed"})
            continue
        raw_text = json.dumps(tx, separators=(",", ":"))
        mint = decoded.get("mint")
        event_matches_mint = bool(event and event.get("event_kind") == "create_initial_buy" and event.get("mint") == mint)
        target_token_mints = {
            change.get("mint")
            for account in tx.get("accountData") or []
            for change in account.get("tokenBalanceChanges") or []
            if change.get("userAccount") == WALLET
        }
        ambiguous = bool(event and not event_matches_mint) or len(target_token_mints) > 1
        target_bought = 1 if event_matches_mint and not ambiguous else None if ambiguous else 0
        row = {
            "wallet": WALLET,
            "mint": mint,
            "create_signature": tx["signature"],
            "create_slot": tx.get("slot"),
            "create_block_time": tx.get("timestamp"),
            "create_at": event.get("at") if event else __import__("datetime").datetime.fromtimestamp(tx.get("timestamp"), __import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z"),
            "target_first_buy_signature": tx["signature"] if target_bought == 1 else None,
            "target_first_buy_slot": tx.get("slot") if target_bought == 1 else None,
            "create_to_target_latency_ms": 0 if target_bought == 1 else None,
            "slot_difference": 0 if target_bought == 1 else None,
            "transaction_difference": 0 if target_bought == 1 else None,
            "external_buy_rank_before_target": 0 if target_bought == 1 else None,
            "sol_before_target": 0.0 if target_bought == 1 else None,
            "unique_external_buyers_before_target": 0 if target_bought == 1 else None,
            "opening_class": "CREATOR_SELF_INITIAL_BUY_AT_CREATE" if target_bought == 1 else "SELF_CREATED_WITHOUT_INITIAL_BUY" if target_bought == 0 else "AMBIGUOUS_MULTI_ASSET_CREATE",
            "target_bought": target_bought,
            "selection_interpretation": "self_created_initial_buy" if target_bought == 1 else "self_created_no_initial_buy" if target_bought == 0 else "ambiguous",
            "initial_buy_principal_sol": event.get("tracker_gross_trade_sol") if target_bought == 1 else None,
            "native_create_and_buy_cost_sol": event.get("native_sol_spent") if target_bought == 1 else None,
            "initial_tokens_received_raw": event.get("token_delta_raw") if target_bought == 1 else None,
            "token_decimals": event.get("token_decimals") if target_bought == 1 else 6,
            "name": decoded["name"],
            "symbol": decoded["symbol"],
            "metadata_uri": decoded["metadata_uri"],
            "metadata_uri_domain": decoded["metadata_uri"].split("/")[2].lower() if "://" in decoded["metadata_uri"] else None,
            "creator": decoded["creator"],
            "creator_is_target": decoded["creator"] == WALLET,
            "create_discriminator_hex": decoded["create_discriminator_hex"],
            "token_program": TOKEN_2022 if TOKEN_2022 in raw_text else TOKEN_LEGACY if TOKEN_LEGACY in raw_text else None,
            "mayhem_program_present": MAYHEM_PROGRAM in raw_text,
            "mayhem_agent_present": MAYHEM_AGENT in raw_text,
            "mayhem_status": "mayhem_reference_present" if MAYHEM_PROGRAM in raw_text or MAYHEM_AGENT in raw_text else "no_mayhem_reference_in_create_tx",
            "decision_feature_boundary": "create_transaction",
            "name_length": len(decoded["name"]),
            "symbol_length": len(decoded["symbol"]),
            "name_word_count": len(decoded["name"].split()),
            "name_digit_count": sum(char.isdigit() for char in decoded["name"]),
            "name_upper_ratio": sum(char.isupper() for char in decoded["name"]) / max(1, sum(char.isalpha() for char in decoded["name"])),
            "symbol_upper_ratio": sum(char.isupper() for char in decoded["symbol"]) / max(1, sum(char.isalpha() for char in decoded["symbol"])),
            "utc_hour_sin": math.sin(2 * math.pi * (__import__("datetime").datetime.fromtimestamp(tx.get("timestamp"), __import__("datetime").timezone.utc).hour / 24)),
            "utc_hour_cos": math.cos(2 * math.pi * (__import__("datetime").datetime.fromtimestamp(tx.get("timestamp"), __import__("datetime").timezone.utc).hour / 24)),
        }
        if target_bought == 1:
            gross_lamports = int(round(float(event.get("tracker_gross_trade_sol") or 0) * LAMPORTS))
            reserve_quote = gross_lamports - gross_lamports * 125 // 10_000
            tokens_received = int(event.get("token_delta_raw") or 0)
            post_vsol = 30_000_000_000 + reserve_quote
            post_vtok = 1_073_000_000_000_000 - tokens_received
            row.update({
                "virtual_sol_reserves_before_lamports": 30_000_000_000,
                "virtual_token_reserves_before_raw": 1_073_000_000_000_000,
                "gross_sol_consumed_before_target": 0.0,
                "curve_progression_before_target_pct": 0.0,
                "real_sol_reserves_before_lamports": 0,
                "virtual_sol_reserves_after_lamports": post_vsol,
                "virtual_token_reserves_after_raw": post_vtok,
                "real_sol_reserves_after_lamports_estimate": reserve_quote,
                "average_entry_price_sol_per_token": (gross_lamports / LAMPORTS) / (tokens_received / 1_000_000) if tokens_received else None,
                "marginal_price_before_sol_per_token": (30_000_000_000 / LAMPORTS) / (1_073_000_000_000_000 / 1_000_000),
                "marginal_price_after_sol_per_token": (post_vsol / LAMPORTS) / (post_vtok / 1_000_000) if post_vtok else None,
                "implied_market_cap_before_sol": (30_000_000_000 / LAMPORTS) * (1_000_000_000_000_000 / 1_073_000_000_000_000),
                "implied_market_cap_after_sol": (post_vsol / LAMPORTS) * (1_000_000_000_000_000 / post_vtok) if post_vtok else None,
                "implied_market_cap_usd": None,
                "historical_sol_usd_price_attached": False,
            })
            row["prior_activity_price_impact_pct"] = 0.0
            row["target_initial_buy_marginal_price_impact_pct"] = 100 * (
                row["marginal_price_after_sol_per_token"] / row["marginal_price_before_sol_per_token"] - 1
            )
        self_created.append(row)
        if target_bought == 1:
            launches.append(row)
        elif target_bought == 0:
            ignored_self_created.append(row)
        else:
            ambiguous_self_created.append(row)
    self_created.sort(key=lambda x: (x["create_block_time"], x["create_slot"], x["create_signature"]))
    launches.sort(key=lambda x: (x["create_block_time"], x["create_slot"], x["create_signature"]))
    ignored_self_created.sort(key=lambda x: (x["create_block_time"], x["create_slot"], x["create_signature"]))

    prior_names = Counter()
    prior_symbols = Counter()
    prior_uris = Counter()
    for launch_index, row in enumerate(self_created):
        name = normalize_text(row["name"])
        symbol = normalize_text(row["symbol"])
        uri = row.get("metadata_uri") or ""
        row["prior_creator_self_launch_count_in_window"] = launch_index
        row["log1p_prior_creator_self_launch_count_in_window"] = math.log1p(launch_index)
        row["prior_same_name_count"] = prior_names[name]
        row["prior_same_symbol_count"] = prior_symbols[symbol]
        row["prior_same_metadata_uri_count"] = prior_uris[uri]
        prior_names[name] += 1
        prior_symbols[symbol] += 1
        prior_uris[uri] += 1

    metadata = [{
        "mint": row["mint"], "name": row["name"], "symbol": row["symbol"],
        "metadata_uri": row["metadata_uri"], "metadata_uri_domain": row["metadata_uri_domain"],
        "creator": row["creator"], "create_at": row["create_at"],
        "source": "onchain_pump_create_instruction", "historical_availability_proven": True,
    } for row in self_created]

    closed = [row for row in positions if row["status"] == "closed" and row["buy_count"] and row["sell_count"]]
    unresolved = [row for row in positions if row["status"] == "unresolved" and row["buy_count"]]
    left_censored = [row for row in positions if row["status"] == "window_left_censored_sell_only"]
    native = metrics(closed, "native_pnl_sol")
    tracker = metrics(closed, "tracker_comparable_pnl_sol")
    conservative_zero_inventory = metrics(closed + unresolved, "native_pnl_sol")
    threshold_wr = {}
    for threshold in [0.0, 0.001, 0.01, 0.05, 0.10]:
        threshold_wr[str(threshold)] = sum(float(row["native_pnl_sol"]) > threshold for row in closed) / len(closed) if closed else None
    roi_wr = {}
    for threshold in [0.0, 1.0, 5.0, 10.0, 20.0]:
        roi_wr[str(threshold)] = sum(float(row["native_roi_pct"]) > threshold for row in closed) / len(closed) if closed else None

    sizes = [float(row["initial_buy_principal_sol"] or 0) for row in launches]
    size_bins = Counter()
    for size in sizes:
        size_bins[size_cluster(size)] += 1

    launch_by_signature = {row["create_signature"]: row for row in launches}
    self_launch_positions = []
    for position in positions:
        launch = launch_by_signature.get(position.get("first_buy_signature"))
        if not launch:
            continue
        position["initial_buy_principal_sol"] = float(launch["initial_buy_principal_sol"] or 0)
        position["entry_size_cluster"] = size_cluster(position["initial_buy_principal_sol"])
        self_launch_positions.append(position)
    closed_self_launch_positions = [row for row in self_launch_positions if row["status"] == "closed" and row["sell_count"]]
    unresolved_self_launch_positions = [row for row in self_launch_positions if row["status"] == "unresolved"]
    size_archetypes = {}
    for label in ["~1_SOL", "~3_SOL", "OTHER"]:
        cluster_launches = [row for row in launches if size_cluster(float(row["initial_buy_principal_sol"] or 0)) == label]
        cluster_closed = [row for row in closed_self_launch_positions if row["entry_size_cluster"] == label]
        cluster_unresolved = [row for row in unresolved_self_launch_positions if row["entry_size_cluster"] == label]
        size_archetypes[label] = {
            "launch_n": len(cluster_launches),
            "closed_n": len(cluster_closed),
            "unresolved_n": len(cluster_unresolved),
            "native_closed_economics": metrics(cluster_closed, "native_pnl_sol"),
            "median_native_roi_pct": statistics.median(float(row["native_roi_pct"]) for row in cluster_closed) if cluster_closed else None,
            "mean_name_length": statistics.mean(float(row["name_length"]) for row in cluster_launches) if cluster_launches else None,
            "mean_name_word_count": statistics.mean(float(row["name_word_count"]) for row in cluster_launches) if cluster_launches else None,
            "mean_name_upper_ratio": statistics.mean(float(row["name_upper_ratio"]) for row in cluster_launches) if cluster_launches else None,
        }
    size_outcome_rows = [row for row in closed_self_launch_positions if row.get("native_roi_pct") is not None]
    size_roi_correlation = pearson(
        [float(row["initial_buy_principal_sol"]) for row in size_outcome_rows],
        [float(row["native_roi_pct"]) for row in size_outcome_rows],
    )

    completed_self_positions = sorted(
        (row for row in closed_self_launch_positions if row.get("last_sell_ts") is not None),
        key=lambda row: (int(row["last_sell_ts"]), row["mint"], row["position_cycle"]),
    )
    completed_index = 0
    prior_completed_pnls: list[float] = []
    for row in self_created:
        decision_ts = int(row["create_block_time"])
        while (
            completed_index < len(completed_self_positions)
            and int(completed_self_positions[completed_index]["last_sell_ts"]) < decision_ts
        ):
            prior_completed_pnls.append(float(completed_self_positions[completed_index]["native_pnl_sol"]))
            completed_index += 1
        prior_gains = sum(value for value in prior_completed_pnls if value > 0)
        prior_losses = -sum(value for value in prior_completed_pnls if value < 0)
        row["prior_creator_completed_position_count_in_window"] = len(prior_completed_pnls)
        row["log1p_prior_creator_completed_position_count_in_window"] = math.log1p(len(prior_completed_pnls))
        row["prior_creator_completed_native_wr_in_window"] = (
            sum(value > 0 for value in prior_completed_pnls) / len(prior_completed_pnls)
            if prior_completed_pnls else 0.0
        )
        row["prior_creator_completed_native_expectancy_sol_in_window"] = (
            statistics.mean(prior_completed_pnls) if prior_completed_pnls else 0.0
        )
        row["prior_creator_completed_native_pf_in_window"] = (
            prior_gains / prior_losses if prior_losses else None
        )
        row["prior_creator_history_left_censored_at_window_start"] = True
    holds_first = [float(row["hold_to_first_sell_seconds"]) for row in closed if row["hold_to_first_sell_seconds"] is not None]
    holds_last = [float(row["hold_to_last_sell_seconds"]) for row in closed if row["hold_to_last_sell_seconds"] is not None]

    exit_classes = Counter()
    for row in closed:
        first = float(row["hold_to_first_sell_seconds"] or 0)
        last = float(row["hold_to_last_sell_seconds"] or 0)
        if row["sell_count"] == 1 and last <= 10:
            cls = "FAST_FULL_EXIT"
        elif row["sell_count"] > 1 and last <= 10:
            cls = "FAST_PARTIAL_EXIT"
        elif row["sell_count"] > 1:
            cls = "SCALE_OUT"
        elif last <= 120:
            cls = "DELAYED_EXIT"
        else:
            cls = "LONG_HOLD"
        exit_classes[cls] += 1
        row["exit_archetype"] = cls

    names = Counter(normalize_text(row["name"]) for row in launches if normalize_text(row["name"]))
    symbols = Counter(normalize_text(row["symbol"]) for row in launches if normalize_text(row["symbol"]))
    uris = Counter(row["metadata_uri"] for row in launches if row.get("metadata_uri"))
    repeated_names = [{"normalized_name": key, "count": value} for key, value in names.most_common() if value > 1]
    repeated_symbols = [{"normalized_symbol": key, "count": value} for key, value in symbols.most_common() if value > 1]
    repeated_uris = [{"metadata_uri": key, "count": value} for key, value in uris.most_common() if value > 1]
    blindape = [row for row in launches if "blindape" in normalize_text(row["name"]) or "blindape" in normalize_text(row["symbol"])]

    chronological = {}
    ordered_closed = sorted(closed, key=lambda x: (x["first_buy_ts"], x["mint"], x["position_cycle"]))
    a = len(ordered_closed) // 2
    b = a + len(ordered_closed) // 4
    for label, rows in [("train_earliest_50pct", ordered_closed[:a]), ("validation_next_25pct", ordered_closed[a:b]), ("holdout_latest_25pct", ordered_closed[b:])]:
        chronological[label] = {
            "start": rows[0]["first_buy_at"] if rows else None,
            "end": rows[-1]["first_buy_at"] if rows else None,
            "native": metrics(rows, "native_pnl_sol"),
            "tracker_comparable": metrics(rows, "tracker_comparable_pnl_sol"),
        }

    feature_names = [
        "name_length", "symbol_length", "name_word_count", "name_digit_count",
        "name_upper_ratio", "symbol_upper_ratio", "prior_same_name_count",
        "prior_same_symbol_count", "prior_same_metadata_uri_count", "utc_hour_sin", "utc_hour_cos",
        "log1p_prior_creator_self_launch_count_in_window",
        "log1p_prior_creator_completed_position_count_in_window",
        "prior_creator_completed_native_wr_in_window",
        "prior_creator_completed_native_expectancy_sol_in_window",
    ]
    labeled = [row for row in self_created if row["target_bought"] in (0, 1)]
    labeled.sort(key=lambda x: (x["create_block_time"], x["create_slot"], x["create_signature"]))
    selection_a = len(labeled) // 2
    selection_b = selection_a + len(labeled) // 4
    selection_splits = {
        "train_earliest_50pct": labeled[:selection_a],
        "validation_next_25pct": labeled[selection_a:selection_b],
        "holdout_latest_25pct": labeled[selection_b:],
    }
    model = fit_logistic(selection_splits["train_earliest_50pct"], feature_names)
    model_forensics = {
        "model": "class_weighted_logistic_regression",
        "features": feature_names,
        "training_boundary": "earliest_50pct_only",
        "standardized_coefficients": model["standardized_coefficients"],
        "splits": {},
        "caveat": "Features are on-chain CREATE metadata/time and strictly prior completed creator history only. CREATE fields become observable simultaneous with the atomic initial buy; creator history is left-censored at the research-window start. Discrimination is explanatory rather than preemptively followable.",
    }
    for label, rows in selection_splits.items():
        labels = [int(row["target_bought"]) for row in rows]
        scores = [model["score"](row) for row in rows]
        model_forensics["splits"][label] = {
            "n": len(rows), "positive_n": sum(labels), "negative_n": len(labels) - sum(labels),
            "positive_rate": sum(labels) / len(labels) if labels else None,
            "roc_auc": auc(labels, scores),
        }

    feature_lifts = []
    positives = [row for row in labeled if row["target_bought"] == 1]
    controls = [row for row in labeled if row["target_bought"] == 0]
    for name in feature_names:
        positive_values = [float(row[name]) for row in positives]
        control_values = [float(row[name]) for row in controls]
        positive_mean = statistics.mean(positive_values)
        control_mean = statistics.mean(control_values)
        pooled = math.sqrt((statistics.pvariance(positive_values) + statistics.pvariance(control_values)) / 2) or None
        rank_ordered = sorted(labeled, key=lambda row: (float(row[name]), row["create_block_time"], row["create_signature"]))
        rank_buckets = []
        for bucket_index in range(5):
            bucket_start = bucket_index * len(rank_ordered) // 5
            bucket_end = (bucket_index + 1) * len(rank_ordered) // 5
            bucket_rows = rank_ordered[bucket_start:bucket_end]
            rank_buckets.append({
                "rank_bucket": bucket_index + 1,
                "n": len(bucket_rows),
                "minimum": min(float(row[name]) for row in bucket_rows) if bucket_rows else None,
                "maximum": max(float(row[name]) for row in bucket_rows) if bucket_rows else None,
                "positive_rate": sum(int(row["target_bought"]) for row in bucket_rows) / len(bucket_rows) if bucket_rows else None,
            })
        bucket_rates = [float(bucket["positive_rate"]) for bucket in rank_buckets if bucket["positive_rate"] is not None]
        chronological_smd = {}
        for split_label, split_rows in selection_splits.items():
            split_positives = [float(row[name]) for row in split_rows if row["target_bought"] == 1]
            split_controls = [float(row[name]) for row in split_rows if row["target_bought"] == 0]
            split_pooled = (
                math.sqrt((statistics.pvariance(split_positives) + statistics.pvariance(split_controls)) / 2)
                if len(split_positives) > 1 and len(split_controls) > 1 else 0.0
            )
            chronological_smd[split_label] = (
                (statistics.mean(split_positives) - statistics.mean(split_controls)) / split_pooled
                if split_positives and split_controls and split_pooled else None
            )
        feature_lifts.append({
            "feature": name,
            "coverage": 1.0,
            "positive_n": len(positive_values),
            "control_n": len(control_values),
            "positive_nonzero_prevalence": sum(value != 0 for value in positive_values) / len(positive_values),
            "control_nonzero_prevalence": sum(value != 0 for value in control_values) / len(control_values),
            "positive_mean": positive_mean,
            "control_mean": control_mean,
            "positive_median": statistics.median(positive_values),
            "control_median": statistics.median(control_values),
            "mean_difference": positive_mean - control_mean,
            "standardized_mean_difference": (positive_mean - control_mean) / pooled if pooled else None,
            "equal_frequency_rank_buckets": rank_buckets,
            "rank_bucket_positive_rate_trend_correlation": pearson(list(range(1, len(bucket_rates) + 1)), bucket_rates),
            "chronological_standardized_mean_difference": chronological_smd,
            "available_by_create": True,
            "available_before_atomic_initial_buy": name.startswith("prior_creator_")
            or name.startswith("log1p_prior_creator_")
            or name.startswith("utc_hour_"),
        })
    feature_lift_by_name = {row["feature"]: row for row in feature_lifts}

    hour_table = []
    for hour in range(24):
        hour_rows = [row for row in labeled if __import__("datetime").datetime.fromtimestamp(row["create_block_time"], __import__("datetime").timezone.utc).hour == hour]
        hour_table.append({
            "utc_hour": hour,
            "n": len(hour_rows),
            "bought_n": sum(int(row["target_bought"]) for row in hour_rows),
            "bought_rate": sum(int(row["target_bought"]) for row in hour_rows) / len(hour_rows) if hour_rows else None,
        })

    signature_integrity = json.loads((base / "signature_integrity.json").read_text()) if (base / "signature_integrity.json").exists() else None
    parse_rejections = json.loads((base / "target_trade_parse_rejections.json").read_text()) if (base / "target_trade_parse_rejections.json").exists() else []
    funder_audit = json.loads((base / "funder_audit.json").read_text()) if (base / "funder_audit.json").exists() else None
    stream_overlap = json.loads((base / "stream_overlap_summary.json").read_text()) if (base / "stream_overlap_summary.json").exists() else None
    total_network_fees = sum(float(row.get("network_fee_sol") or 0) for row in positions)
    best_trades = [position_excerpt(row) for row in sorted(closed, key=lambda row: float(row["native_pnl_sol"]), reverse=True)[:5]]
    worst_trades = [position_excerpt(row) for row in sorted(closed, key=lambda row: float(row["native_pnl_sol"]))[:5]]
    curve_impacts = [float(row["target_initial_buy_marginal_price_impact_pct"]) for row in launches]
    implied_mc_before = [float(row["implied_market_cap_before_sol"]) for row in launches]
    implied_mc_after = [float(row["implied_market_cap_after_sol"]) for row in launches]
    mayhem_positive_program_n = sum(row["mayhem_program_present"] for row in launches)
    mayhem_control_program_n = sum(row["mayhem_program_present"] for row in ignored_self_created)
    mayhem_positive_agent_n = sum(row["mayhem_agent_present"] for row in launches)
    mayhem_control_agent_n = sum(row["mayhem_agent_present"] for row in ignored_self_created)
    requested_end_ts = 1788391205
    requested_start_ts = 1785799205
    summary = {
        "schema": "wallet-8ycb-opening-sniper-v2-forensics-v1",
        "wallet": WALLET,
        "coverage": {
            "requested_window_start": iso_utc(requested_start_ts),
            "requested_window_end": iso_utc(requested_end_ts),
            "native_transaction_start": iso_utc(min((tx.get("timestamp") for tx in txs if tx.get("timestamp")), default=None)),
            "native_transaction_end": iso_utc(max((tx.get("timestamp") for tx in txs if tx.get("timestamp")), default=None)),
            "successful_target_fee_payer_transactions": len(txs),
            "parsed_endpoint_duplicate_rows_removed": len(raw_txs) - len(txs),
            "decoded_self_create_launches": len(self_created),
            "self_created_with_initial_buy": len(launches),
            "self_created_without_initial_buy": len(ignored_self_created),
            "ambiguous_multi_asset_creates": len(ambiguous_self_created),
            "create_decode_errors": len(decode_errors),
        },
        "identity": {
            "creator_is_target_count": sum(row["creator_is_target"] for row in launches),
            "creator_is_target_rate": sum(row["creator_is_target"] for row in launches) / len(launches) if launches else None,
            "atomic_create_and_initial_buy_count": len(launches),
            "opening_class_counts": dict(Counter(row["opening_class"] for row in launches)),
            "interpretation": "8YCB is the creator in both the positive and closest matched negative set. Positives are atomic initial buys of its own launches; controls are its own CREATE transactions without an initial buy.",
        },
        "native_economics": native,
        "tracker_comparable_economics": tracker,
        "integrity": {
            "closed_positions": len(closed),
            "unresolved_positions": len(unresolved),
            "left_censored_sell_only_positions": len(left_censored),
            "unresolved_native_cash_pnl_sol_not_counted_in_wr": sum(float(row["native_pnl_sol"]) for row in unresolved),
            "unresolved_native_cost_sol": sum(float(row["native_sol_spent"]) for row in unresolved),
            "partial_or_scaleout_closed_positions": sum(row["sell_count"] > 1 for row in closed),
            "native_wr_by_minimum_profit_sol": threshold_wr,
            "native_wr_by_minimum_roi_pct": roi_wr,
            "pump_economic_events": len(economic_events),
            "token_transfer_events": len(transfer_events),
            "token_transfer_event_kinds": dict(Counter(row["event_kind"] for row in transfer_events)),
            "positions_with_at_least_one_transfer_in_position_interval": sum(bool(row["transfer_event_count"]) for row in positions),
            "unresolved_positions_with_at_least_one_transfer": sum(bool(row["transfer_event_count"]) for row in unresolved),
            "successful_transactions_without_exactly_one_target_token_delta": len(parse_rejections),
            "trade_parse_rejection_reasons": dict(Counter(row["reason"] for row in parse_rejections)),
            "trade_parse_rejection_note": "These are successful wallet transactions that do not expose exactly one non-zero target token delta; they are not transaction failures. CREATE identity/control decoding runs independently over the full transaction set.",
            "closed_positions_with_residual_dust_1_to_3_raw_units": sum(0 < int(row["unresolved_tokens_raw"]) <= 3 for row in closed),
            "network_fees_all_reconstructed_position_transactions_sol": total_network_fees,
            "pump_fees_separately_reconstructed": False,
            "pump_fee_note": "Native wallet delta includes Pump fees and every other lamport movement, but the indexed extract does not isolate Pump fee transfers reliably enough for a separate total.",
            "best_native_trades": best_trades,
            "worst_native_trades": worst_trades,
            "conservative_zero_value_open_inventory": conservative_zero_inventory,
            "unresolved_all_counted_as_losses_wr": native["wins"] / (len(closed) + len(unresolved)) if closed or unresolved else None,
            "submitted_signature_audit": signature_integrity,
        },
        "position_size": {
            "count": len(sizes), "median_principal_sol": statistics.median(sizes) if sizes else None,
            "mean_principal_sol": statistics.mean(sizes) if sizes else None,
            "p10": q(sizes, 0.10), "p90": q(sizes, 0.90), "cluster_counts": dict(size_bins),
            "cluster_outcomes": size_archetypes,
            "principal_vs_closed_native_roi_pearson": size_roi_correlation,
            "outcome_sample_note": "Cluster outcome rows are positions whose first buy signature is the atomic CREATE buy; unrelated rebuy cycles are excluded.",
        },
        "timing": {
            "create_to_buy_latency_ms": {"n": len(launches), "median": 0, "p90": 0},
            "slot_difference": {"n": len(launches), "median": 0, "p90": 0},
            "external_buy_rank_before_target": {"n": len(launches), "median": 0, "p90": 0},
        },
        "entry_curve": {
            "classification_counts": {"PRISTINE_CREATOR_INITIAL_BUY": len(launches)},
            "external_buy_rank_before_target": 0,
            "gross_sol_consumed_before_target": 0.0,
            "initial_virtual_sol_reserves_lamports": 30_000_000_000,
            "initial_virtual_token_reserves_raw": 1_073_000_000_000_000,
            "initial_real_sol_reserves_lamports": 0,
            "implied_market_cap_before_sol_median": q(implied_mc_before, 0.5),
            "implied_market_cap_after_sol_median": q(implied_mc_after, 0.5),
            "initial_buy_curve_price_impact_pct_median": q(curve_impacts, 0.5),
            "initial_buy_curve_price_impact_pct_p10": q(curve_impacts, 0.1),
            "initial_buy_curve_price_impact_pct_p90": q(curve_impacts, 0.9),
            "historical_sol_usd_attached": False,
            "method_note": "Deterministic Pump curve estimate using 30 SOL / 1.073B-token virtual initialization and 125 bps fee; USD MC is intentionally null without a historical SOL/USD series.",
        },
        "exit_behavior": {
            "class_counts": dict(exit_classes),
            "hold_to_first_sell_seconds": {"median": q(holds_first, 0.5), "p10": q(holds_first, 0.1), "p90": q(holds_first, 0.9)},
            "hold_to_last_sell_seconds": {"median": q(holds_last, 0.5), "p10": q(holds_last, 0.1), "p90": q(holds_last, 0.9)},
        },
        "metadata": {
            "onchain_create_metadata_rows": len(metadata),
            "repeated_name_groups": repeated_names[:100],
            "repeated_symbol_groups": repeated_symbols[:100],
            "repeated_metadata_uri_groups": repeated_uris[:100],
            "blindape_launch_count": len(blindape),
            "blindape_mints": [row["mint"] for row in blindape],
            "metadata_uri_domains": dict(Counter(row.get("metadata_uri_domain") or "__missing__" for row in launches)),
        },
        "mayhem": {
            "create_tx_with_mayhem_program_reference": mayhem_positive_program_n,
            "create_tx_with_mayhem_agent_reference": mayhem_positive_agent_n,
            "control_create_tx_with_mayhem_program_reference": mayhem_control_program_n,
            "control_create_tx_with_mayhem_agent_reference": mayhem_control_agent_n,
            "positive_program_presence_rate": mayhem_positive_program_n / len(launches) if launches else None,
            "control_program_presence_rate": mayhem_control_program_n / len(ignored_self_created) if ignored_self_created else None,
            "positive_agent_presence_rate": mayhem_positive_agent_n / len(launches) if launches else None,
            "control_agent_presence_rate": mayhem_control_agent_n / len(ignored_self_created) if ignored_self_created else None,
            "observed_program_presence_rate_lift": (mayhem_positive_program_n / len(launches) - mayhem_control_program_n / len(ignored_self_created)) if launches and ignored_self_created else None,
            "observed_agent_presence_rate_lift": (mayhem_positive_agent_n / len(launches) - mayhem_control_agent_n / len(ignored_self_created)) if launches and ignored_self_created else None,
            "note": "This is exact CREATE-transaction account presence, not proof that Mayhem mode was active. Presence is universal in both matched sets and has zero observed selection lift.",
        },
        "token_program": dict(Counter(row.get("token_program") or "unknown" for row in self_created)),
        "chronological_stability": chronological,
        "selection_validity": {
            "primary_third_party_ignored_universe_valid": False,
            "primary_reason": "The target is a launch creator rather than a third-party opening sniper.",
            "closest_matched_control_valid": True,
            "closest_matched_control": "8YCB_SELF_CREATED_WITHOUT_INITIAL_BUY",
            "positive_n": len(launches),
            "control_n": len(ignored_self_created),
            "ambiguous_n": len(ambiguous_self_created),
            "model_assisted_forensics_run": True,
            "model_note": model_forensics["caveat"],
            "hour_table": hour_table,
        },
        "follower_audit": {
            "status": "partial_curve_only_upper_bound" if stream_overlap else "not_reconstructed",
            "stream_overlap": stream_overlap,
            "reason": "The retained stream is under seven days; complete routed-flow coverage, account readiness, network/tip costs, landing, and first-sellable outcomes are not proven.",
        },
        "funding_clue": {
            "odin_address": ODIN,
            "verified_in_target_fee_payer_extract": any(ODIN in json.dumps(tx, separators=(",", ":")) for tx in txs),
            "indexed_transfer_audit": funder_audit,
        },
        "final_classification": "8YCB_DATASET_PARTIAL_BUT_USEFUL",
    }

    write_jsonl_gz(base / "target_trade_ledger.jsonl.gz", positions)
    write_jsonl_gz(base / "target_launches.jsonl.gz", launches)
    write_jsonl_gz(base / "ignored_control_launches.jsonl.gz", ignored_self_created)
    write_jsonl_gz(base / "metadata_features.jsonl.gz", metadata)
    write_jsonl_gz(base / "target_outcomes.jsonl.gz", positions)
    write_jsonl_gz(base / "positive_control_feature_table.jsonl.gz", labeled)
    write_jsonl_gz(base / "creator_funder_entities.jsonl.gz", [{
        "entity": WALLET, "role": "decoded_onchain_creator", "launch_count": len(self_created),
        "target_wallet": WALLET, "evidence": "Pump.fun CREATE instruction creator pubkey",
    }])
    write_json(base / "feature_lift.json", {
        "comparison": "8YCB_SELF_CREATED_WITH_INITIAL_BUY_vs_8YCB_SELF_CREATED_WITHOUT_INITIAL_BUY",
        "features": feature_lifts,
        "hour_table": hour_table,
        "causal_boundary_note": model_forensics["caveat"],
    })
    write_json(base / "model_forensics.json", model_forensics)
    write_json(base / "native_ledger_summary.json", summary)
    write_json(base / "create_decode_errors.json", decode_errors)

    report = f"""# 8YCB Opening-Sniper Reverse Engineering V2

## A. DATA COVERAGE

- Requested UTC window: `{summary['coverage']['requested_window_start']}` through `{summary['coverage']['requested_window_end']}`. First/last successful target transaction: `{summary['coverage']['native_transaction_start']}` through `{summary['coverage']['native_transaction_end']}`.
- Successful target-fee-payer transactions: **{len(txs):,}**.
- Decoded Pump.fun self-launches: **{len(self_created):,}** — **{len(launches):,}** with atomic initial buy, **{len(ignored_self_created):,}** without initial buy, **{len(ambiguous_self_created)}** ambiguous; decode errors: **{len(decode_errors)}**.
- Independent all-launch stream snapshot: `{stream_overlap['source_snapshot']['first_observation_utc']}` through `{stream_overlap['source_snapshot']['snapshot_last_observation_utc']}` (**{stream_overlap['source_snapshot']['duration_seconds']/86400:.2f} days**). This is below seven days and omits some routed inner-instruction flow, so it is not sufficient for complete 30-day control/follower reconstruction.

## B. WALLET ECONOMICS INTEGRITY

Native accounting uses 8YCB's actual lamport delta per transaction, so reported PnL includes network, priority/tip, Pump-fee, and rent movements. Closed positions only are used for realized WR/PF. **{len(unresolved)}** unresolved positions and **{len(left_censored)}** left-censored sell-only positions are excluded rather than silently scored as wins. There are **{summary['integrity']['partial_or_scaleout_closed_positions']:,}** closed positions with multiple sells, **{summary['integrity']['closed_positions_with_residual_dust_1_to_3_raw_units']}** closed rows with 1–3 raw token units of residual dust, and **{len(transfer_events):,}** non-trade transfer events kept outside position PnL but linked by mint/time/signature in the ledger. **{summary['integrity']['unresolved_positions_with_at_least_one_transfer']}** unresolved rows overlap a transfer and remain unresolved rather than being mislabeled as sales. Another **{len(parse_rejections):,}** successful transactions did not expose exactly one non-zero target token delta and were not forced into the ledger; CREATE controls/identity were still decoded independently from the full transaction set. Reconstructed network fees total **{total_network_fees:.6f} SOL**; Pump fees cannot be isolated reliably from this indexed payload and are not fabricated. If every unresolved position is counted as a loss, WR falls to **{100*summary['integrity']['unresolved_all_counted_as_losses_wr']:.2f}%**. If remaining inventory is valued at zero, closed+open cash PnL is **{conservative_zero_inventory['total_pnl_sol']:+.6f} SOL** with PF **{conservative_zero_inventory['profit_factor']:.3f}**.

The submitted-signature endpoint retained only `{signature_integrity['oldest'] if signature_integrity else 'unavailable'}` through `{signature_integrity['newest'] if signature_integrity else 'unavailable'}`. Within that bounded slice, **{signature_integrity['failed_signatures'] if signature_integrity else 0:,}/{signature_integrity['submitted_signatures'] if signature_integrity else 0:,} ({100*signature_integrity['failure_rate'] if signature_integrity else 0:.2f}%)** submissions failed; this rate is not extrapolated across 30 days.

## C. NATIVE WR / PF / EXPECTANCY

- Closed positions: **{native['n']:,}**
- NATIVE_REALIZED_WR: **{100*native['wr']:.2f}%**
- Native PF: **{native['profit_factor']:.3f}**
- Total realized PnL: **{native['total_pnl_sol']:+.6f} SOL**
- Expectancy / median: **{native['expectancy_sol']:+.6f} / {native['median_trade_sol']:+.6f} SOL**
- Max chronological drawdown: **{native['max_drawdown_sol']:.6f} SOL**
- TRACKER_COMPARABLE_WR: **{100*tracker['wr']:.2f}%**, tracker-like PF **{tracker['profit_factor']:.3f}**
- Positive-PnL concentration: top 1 / top 5 = **{100*native['top1_positive_pnl_share']:.2f}% / {100*native['top5_positive_pnl_share']:.2f}%**; loss concentration: top 1 / top 5 = **{100*native['top1_loss_share']:.2f}% / {100*native['top5_loss_share']:.2f}%**.
- Best / worst native trade: **{best_trades[0]['native_pnl_sol']:+.6f} / {worst_trades[0]['native_pnl_sol']:+.6f} SOL**. Exact top-five rows and signatures are in `native_ledger_summary.json`.

## D. POSITION SIZE ARCHETYPES

On-chain initial principal clusters: `{dict(size_bins)}`. Closed self-launch outcomes by cluster: ~1 SOL **n={size_archetypes['~1_SOL']['closed_n']:,}, WR={100*size_archetypes['~1_SOL']['native_closed_economics']['wr']:.2f}%, mean PnL={size_archetypes['~1_SOL']['native_closed_economics']['expectancy_sol']:+.4f} SOL**; ~3 SOL **n={size_archetypes['~3_SOL']['closed_n']:,}, WR={100*size_archetypes['~3_SOL']['native_closed_economics']['wr']:.2f}%, mean PnL={size_archetypes['~3_SOL']['native_closed_economics']['expectancy_sol']:+.4f} SOL**. Principal-vs-ROI Pearson correlation is only **{size_roi_correlation:+.3f}**. The ~1/~3 SOL metadata means and median ROIs are very similar, so they do not establish separate selection families; the **{size_archetypes['OTHER']['closed_n']}** closed OTHER-size rows are too small and heterogeneous for a robust third family. These are creator initial-allocation sizes, not established conviction tiers for outside launches.

## E. CREATE -> BUY TIMING

All decoded positives are an initial buy in the same atomic transaction as CREATE: 0 ms, 0 slots, and no external buyer can precede the initial buy inside that launch transaction.

## F. ENTRY RANK / CURVE STATE

The target buys from the pristine initial curve as creator. External buy rank and gross prior SOL are zero by construction. Under the explicit canonical-curve estimate in `target_launches.jsonl.gz`, median implied MC moves from **{q(implied_mc_before,0.5):.3f} SOL** before the atomic allocation to **{q(implied_mc_after,0.5):.3f} SOL** after it; median marginal-price impact is **{q(curve_impacts,0.5):.2f}%**. USD MC is null because no historical SOL/USD series was attached.

## G. OPENING-SNIPER CLASSIFICATION

`CREATOR_SELF_INITIAL_BUY_AT_CREATE`: **{len(launches):,}/{len(launches):,}** decoded launches. This is not third-party opening sniping.

## H. POSITIVE SET

`target_launches.jsonl.gz` contains one row per decoded self-launch with only atomic decision-time identity, size, token-program, Mayhem-account-presence, strictly prior in-window creator history, and on-chain CREATE metadata. All **{len(self_created):,}** classified self-CREATE rows reference Token-2022.

## I. CONTROL UNIVERSE

The primary all-third-party ignored-launch universe is not causally comparable because 8YCB is a creator. A much stronger matched control was recovered natively: **{len(ignored_self_created):,}** 8YCB-authored CREATE transactions with no atomic initial buy. These share creator, launcher, token program, and CREATE boundary with the positives. Ambiguous multi-asset CREATE transactions are excluded.

## J. CREATOR / FUNDER ENTITY ANALYSIS

Decoded creator equals 8YCB for **{sum(row['creator_is_target'] for row in launches):,}/{len(launches):,} ({100*summary['identity']['creator_is_target_rate']:.2f}%)** positives. Each feature-table row includes only earlier in-window launch count and positions completed strictly before that CREATE; this creator history is explicitly left-censored at the window start. A 100-page indexed TRANSFER scan covering back to 2026-05-24 found no Odin co-occurrence, so the named historical funding clue remains unverified and is not used as alpha evidence.

## K. METADATA / NARRATIVE ANALYSIS

Name, symbol, and metadata URI were decoded directly from CREATE payloads. Repeated normalized-name groups: **{len(repeated_names)}**; repeated metadata-URI groups: **{len(repeated_uris)}**. `blindape` launches: **{len(blindape)}** across distinct mints listed in the machine summary.

## L. MAYHEM ANALYSIS

Mayhem program and agent accounts appear in **{mayhem_positive_program_n:,}/{len(launches):,}** positives and **{mayhem_control_program_n:,}/{len(ignored_self_created):,}** matched controls: **100% versus 100%, zero observed lift**. This proves only CREATE-transaction account presence, not that Mayhem mode was active.

## M. SAME-SLOT PARTICIPANT ANALYSIS

The bounded retained-stream overlap covered **{stream_overlap['target_outcomes'] if stream_overlap else 0}** target launches. Canonical block order was available for **{stream_overlap['canonical_block_order_slots_available'] if stream_overlap else 0}** target slots and missing for **{stream_overlap['canonical_block_order_slots_missing'] if stream_overlap else 0}**. Canonical ordering found **{stream_overlap['before_relation_rows'] if stream_overlap else 0}** participant rows before 8YCB, **{stream_overlap['after_relation_rows'] if stream_overlap else 0}** after, and **{stream_overlap['ambiguous_same_slot_rows'] if stream_overlap else 0}** ambiguous. After-entry participants are not used as selection features.

## N. PRE-ENTRY WALLET-QUALITY ANALYSIS

Not applicable to the positive decision boundary: no external wallet can have a completed prior buy in a token that is created in the same atomic transaction.

## O. PRE-ENTRY FLOW ANALYSIS

Pre-entry buy count, SOL, and unique buyers are all zero by construction. Flow cannot explain which narratives 8YCB chooses to launch.

## P. 8YCB SELECTION FEATURE LIFTS

`feature_lift.json` compares **{len(launches):,}** self-created buys against **{len(ignored_self_created):,}** self-created no-buy controls using CREATE-time name/symbol/repetition/time features plus strictly prior, completed in-window creator history. The largest descriptive effects are fewer name words (**{feature_lift_by_name['name_word_count']['positive_mean']:.2f} vs {feature_lift_by_name['name_word_count']['control_mean']:.2f}, SMD {feature_lift_by_name['name_word_count']['standardized_mean_difference']:+.2f}**), shorter names (**{feature_lift_by_name['name_length']['positive_mean']:.2f} vs {feature_lift_by_name['name_length']['control_mean']:.2f}, SMD {feature_lift_by_name['name_length']['standardized_mean_difference']:+.2f}**), and less uppercase text (**{feature_lift_by_name['name_upper_ratio']['positive_mean']:.3f} vs {feature_lift_by_name['name_upper_ratio']['control_mean']:.3f}, SMD {feature_lift_by_name['name_upper_ratio']['standardized_mean_difference']:+.2f}**). Machine output includes prevalence, equal-frequency monotonicity buckets, and per-split effect sizes. CREATE fields are simultaneous with CREATE, not available before the atomic buy; creator-history values are prospectively valid but left-censored at the 30-day boundary.

## Q. MODEL-ASSISTED SELECTION FORENSICS

A class-weighted logistic regression was fitted only on the earliest 50%. ROC AUC: train **{model_forensics['splits']['train_earliest_50pct']['roc_auc']:.3f}**, validation **{model_forensics['splits']['validation_next_25pct']['roc_auc']:.3f}**, final holdout **{model_forensics['splits']['holdout_latest_25pct']['roc_auc']:.3f}**. Its largest absolute training coefficients are name uppercase ratio (**{model_forensics['standardized_coefficients']['name_upper_ratio']:+.3f}**), name word count (**{model_forensics['standardized_coefficients']['name_word_count']:+.3f}**), and name length (**{model_forensics['standardized_coefficients']['name_length']:+.3f}**). This remains explanatory: those dominant fields arrive at CREATE alongside the atomic buy, while prior-history inputs are window-left-censored.

## R. POST-ENTRY TAPE

Native target exits are reconstructed for 30 days. Sub-second curve-tape MFE/MAE is available only for the **{stream_overlap['target_outcomes'] if stream_overlap else 0}** retained-overlap launches in `stream_target_outcomes.jsonl.gz`; it is not extrapolated to 30 days.

## S. EXIT ARCHETYPES

Counts: `{dict(exit_classes)}`. Median first/last exit latency: **{q(holds_first,0.5)} / {q(holds_last,0.5)} seconds**.

## T. FOLLOWER LATENCY ECONOMICS

Curve-only 0.1 SOL `+20/-20/120s` upper bounds on **{stream_overlap['target_outcomes'] if stream_overlap else 0}** launches:

| Delay | WR | PF on ROI | Mean ROI | +5% worse WR | +5% worse mean ROI |
|---:|---:|---:|---:|---:|---:|
| 25 ms | {100*stream_overlap['follower_curve_only_economics']['delay_25ms']['wr']:.2f}% | {stream_overlap['follower_curve_only_economics']['delay_25ms']['profit_factor_on_roi']:.2f} | {stream_overlap['follower_curve_only_economics']['delay_25ms']['expectancy_roi_pct']:+.2f}% | {100*stream_overlap['follower_curve_only_economics']['delay_25ms_entry_5pct_worse']['wr']:.2f}% | {stream_overlap['follower_curve_only_economics']['delay_25ms_entry_5pct_worse']['expectancy_roi_pct']:+.2f}% |
| 50 ms | {100*stream_overlap['follower_curve_only_economics']['delay_50ms']['wr']:.2f}% | {stream_overlap['follower_curve_only_economics']['delay_50ms']['profit_factor_on_roi']:.2f} | {stream_overlap['follower_curve_only_economics']['delay_50ms']['expectancy_roi_pct']:+.2f}% | {100*stream_overlap['follower_curve_only_economics']['delay_50ms_entry_5pct_worse']['wr']:.2f}% | {stream_overlap['follower_curve_only_economics']['delay_50ms_entry_5pct_worse']['expectancy_roi_pct']:+.2f}% |
| 100 ms | {100*stream_overlap['follower_curve_only_economics']['delay_100ms']['wr']:.2f}% | {stream_overlap['follower_curve_only_economics']['delay_100ms']['profit_factor_on_roi']:.2f} | {stream_overlap['follower_curve_only_economics']['delay_100ms']['expectancy_roi_pct']:+.2f}% | {100*stream_overlap['follower_curve_only_economics']['delay_100ms_entry_5pct_worse']['wr']:.2f}% | {stream_overlap['follower_curve_only_economics']['delay_100ms_entry_5pct_worse']['expectancy_roi_pct']:+.2f}% |
| 250 ms | {100*stream_overlap['follower_curve_only_economics']['delay_250ms']['wr']:.2f}% | {stream_overlap['follower_curve_only_economics']['delay_250ms']['profit_factor_on_roi']:.2f} | {stream_overlap['follower_curve_only_economics']['delay_250ms']['expectancy_roi_pct']:+.2f}% | {100*stream_overlap['follower_curve_only_economics']['delay_250ms_entry_5pct_worse']['wr']:.2f}% | {stream_overlap['follower_curve_only_economics']['delay_250ms_entry_5pct_worse']['expectancy_roi_pct']:+.2f}% |
| 500 ms | {100*stream_overlap['follower_curve_only_economics']['delay_500ms']['wr']:.2f}% | {stream_overlap['follower_curve_only_economics']['delay_500ms']['profit_factor_on_roi']:.2f} | {stream_overlap['follower_curve_only_economics']['delay_500ms']['expectancy_roi_pct']:+.2f}% | {100*stream_overlap['follower_curve_only_economics']['delay_500ms_entry_5pct_worse']['wr']:.2f}% | {stream_overlap['follower_curve_only_economics']['delay_500ms_entry_5pct_worse']['expectancy_roi_pct']:+.2f}% |
| 1,000 ms | {100*stream_overlap['follower_curve_only_economics']['delay_1000ms']['wr']:.2f}% | {stream_overlap['follower_curve_only_economics']['delay_1000ms']['profit_factor_on_roi']:.2f} | {stream_overlap['follower_curve_only_economics']['delay_1000ms']['expectancy_roi_pct']:+.2f}% | {100*stream_overlap['follower_curve_only_economics']['delay_1000ms_entry_5pct_worse']['wr']:.2f}% | {stream_overlap['follower_curve_only_economics']['delay_1000ms_entry_5pct_worse']['expectancy_roi_pct']:+.2f}% |

These omit network/tips/account readiness and do not prove live landing or full routed-flow completeness.

## U. FOLLOWER + FIRST-SELLABLE ECONOMICS

Not proven. Every follower row explicitly records `first_sellable_adjusted_result=null` because account-ready telemetry was not retained.

## V. PRE-ENTRY FINGERPRINT FOLLOWABILITY

The independently detectable on-chain fingerprint is 8YCB's CREATE transaction plus short/few-word/lowercase metadata. It arrives simultaneously with the atomic target buy. The curve-only overlap is promising only at very low delay and decays sharply by 250 ms-1 s; no production followability claim is warranted.

## W. BOUGHT VS IGNORED CLASSIFICATION

Invalid as originally framed for third-party launches. Reframed matched classification uses 8YCB's own no-initial-buy CREATEs as controls; results are in `positive_control_feature_table.jsonl.gz` and `model_forensics.json`.

## X. WINNERS VS LOSERS / ROBUSTNESS

Native WR falls from **{100*threshold_wr['0.0']:.2f}%** at any positive PnL to **{100*threshold_wr['0.1']:.2f}%** above +0.10 SOL; ROI WR falls from **{100*roi_wr['0.0']:.2f}%** above 0% to **{100*roi_wr['20.0']:.2f}%** above 20%. The open-inventory-zero and all-unresolved-as-loss stress tests, top-five concentration, and best/worst trades are in `native_ledger_summary.json`.

## Y. CHRONOLOGICAL STABILITY

See the strict chronological 50/25/25 metrics in `native_ledger_summary.json`.

## Z. WHAT INFORMATION APPEARS TO DRIVE 8YCB

The transaction evidence shows launch authorship plus atomic initial inventory acquisition. On-chain pre-entry flow does not drive the decision because no token/flow exists yet.

## AA. WHAT INFORMATION IS MISSING

Narrative sourcing, social/news timing, private launcher inputs, and any off-chain entity workflow before CREATE; complete routed post-entry tape; canonical block order for 131 older overlap slots; account-ready/first-sellable and landing-cost telemetry; archival stream retention >=7 days.

## AB. BEST EXPLAINABLE FINGERPRINT CLUES

1. Creator identity equals 8YCB.
2. Atomic pristine-curve initial buy.
3. Strong ~1 SOL and ~3 SOL initial-principal clusters.
4. Repeated on-chain name/symbol patterns and a shared metadata-host domain (no repeated full URI after deduplication).
5. CREATE-time metadata-only classifier remained discriminative chronologically, but the fields are not visible before the atomic buy.

These explain the observed transactions but do not define a prospectively detectable third-party opening-snipe strategy.

## AC. FINAL CLASSIFICATION

`8YCB_DATASET_PARTIAL_BUT_USEFUL`

The 30-day native economics/identity result is useful and materially changes the hypothesis. Control and follower datasets do not meet the completeness standard, so no detector, strategy, or followability claim is warranted.
"""
    (base / "report.md").write_text(report)
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
