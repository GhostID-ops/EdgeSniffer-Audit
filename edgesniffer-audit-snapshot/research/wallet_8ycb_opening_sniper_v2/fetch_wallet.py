#!/usr/bin/env python3
"""Read-only native-ledger extractor for the isolated 8YCB v2 research lane.

The script uses Helius' parsed-transaction endpoint for successful swaps, the
standard Solana RPC for submitted/failed-signature counts, and Pump.fun's coin
endpoint for launch identity/metadata.  It writes only to --out-dir.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import gzip
import json
import math
import os
import statistics
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

import requests


WALLET = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ"
PUMP_FUN = "PUMP_FUN"
LAMPORTS_PER_SOL = 1_000_000_000


def iso(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def unique_nonempty(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in out:
            out.append(value)
    return out


class Helius:
    def __init__(self, keys: list[str], rpc_url: str):
        if not keys:
            raise RuntimeError("No Helius API key available")
        if not rpc_url:
            raise RuntimeError("No Solana RPC URL available")
        self.keys = keys
        self.rpc_url = rpc_url
        self.key_index = 0
        self.session = requests.Session()

    def parsed_page(self, before: str | None) -> list[dict[str, Any]]:
        last_error = "unknown error"
        for attempt in range(24):
            key = self.keys[self.key_index % len(self.keys)]
            self.key_index += 1
            # Deliberately unfiltered: Pump.fun CREATE transactions contain the
            # target's initial buy and therefore carry the true cost basis.
            params = {"api-key": key, "limit": 100}
            if before:
                params["before"] = before
            try:
                response = self.session.get(
                    f"https://api.helius.xyz/v0/addresses/{WALLET}/transactions",
                    params=params,
                    timeout=120,
                )
                if response.status_code == 200:
                    value = response.json()
                    if not isinstance(value, list):
                        raise RuntimeError("parsed endpoint returned non-list")
                    return value
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code not in (429, 500, 502, 503, 504):
                    break
            except Exception as exc:  # network retry boundary
                last_error = repr(exc)
            time.sleep(min(20.0, 1.0 + attempt * 0.75))
        raise RuntimeError(f"Helius parsed page failed: {last_error}")

    def rpc(self, method: str, params: list[Any]) -> Any:
        last_error = "unknown error"
        for attempt in range(12):
            try:
                response = self.session.post(
                    self.rpc_url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    timeout=120,
                )
                if response.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {response.status_code}"
                else:
                    response.raise_for_status()
                    value = response.json()
                    if "error" not in value:
                        return value.get("result")
                    last_error = str(value["error"])
            except Exception as exc:
                last_error = repr(exc)
            time.sleep(min(15.0, 0.5 + attempt * 0.75))
        raise RuntimeError(f"RPC {method} failed: {last_error}")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_jsonl_gz(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def fetch_transactions(api: Helius, cutoff_ts: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    before = None
    all_rows: list[dict[str, Any]] = []
    pages = 0
    while pages < 500:
        batch = api.parsed_page(before)
        pages += 1
        if not batch:
            break
        all_rows.extend(batch)
        before = batch[-1].get("signature")
        oldest = min((int(x.get("timestamp") or 0) for x in batch), default=0)
        print(f"parsed page={pages} rows={len(batch)} oldest={iso(oldest)}", flush=True)
        if oldest and oldest <= cutoff_ts:
            break
        if not before:
            break
    in_window = [x for x in all_rows if int(x.get("timestamp") or 0) >= cutoff_ts]
    target = [
        x
        for x in in_window
        if x.get("feePayer") == WALLET and x.get("transactionError") is None
    ]
    target.sort(key=lambda x: (int(x.get("timestamp") or 0), int(x.get("slot") or 0), x.get("signature") or ""))
    return target, {
        "parsed_pages": pages,
        "parsed_rows_seen": len(all_rows),
        "parsed_rows_in_window": len(in_window),
        "target_fee_payer_success_rows": len(target),
    }


def target_native_delta(tx: dict[str, Any]) -> int | None:
    for account in tx.get("accountData") or []:
        if account.get("account") == WALLET:
            value = account.get("nativeBalanceChange")
            return int(value) if value is not None else None
    return None


def target_token_changes(tx: dict[str, Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for account in tx.get("accountData") or []:
        for change in account.get("tokenBalanceChanges") or []:
            if change.get("userAccount") != WALLET:
                continue
            mint = change.get("mint")
            raw = (change.get("rawTokenAmount") or {}).get("tokenAmount")
            decimals = (change.get("rawTokenAmount") or {}).get("decimals")
            if not mint or raw is None:
                continue
            rec = merged.setdefault(mint, {"mint": mint, "token_delta_raw": 0, "decimals": decimals})
            rec["token_delta_raw"] += int(raw)
    if merged:
        return list(merged.values())
    # Fallback for responses that omit accountData token balance changes.
    for transfer in tx.get("tokenTransfers") or []:
        mint = transfer.get("mint")
        amount = transfer.get("tokenAmount")
        if not mint or amount is None:
            continue
        sign = 1 if transfer.get("toUserAccount") == WALLET else -1 if transfer.get("fromUserAccount") == WALLET else 0
        if not sign:
            continue
        decimals = 6
        raw = int(round(float(amount) * 10**decimals)) * sign
        rec = merged.setdefault(mint, {"mint": mint, "token_delta_raw": 0, "decimals": decimals})
        rec["token_delta_raw"] += raw
    return list(merged.values())


def trade_events(swaps: list[dict[str, Any]], cutoff_ts: int, end_ts: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for tx in swaps:
        native_delta = target_native_delta(tx)
        changes = [x for x in target_token_changes(tx) if x["token_delta_raw"]]
        if len(changes) != 1:
            rejected.append({
                "signature": tx.get("signature"),
                "reason": "target_token_change_count_not_one",
                "count": len(changes),
            })
            continue
        token = changes[0]
        side = "buy" if token["token_delta_raw"] > 0 else "sell"
        tx_type = tx.get("type")
        source = tx.get("source")
        is_pump_economic_event = source == PUMP_FUN and tx_type in ("CREATE", "SWAP")
        event = {
            "wallet": WALLET,
            "signature": tx.get("signature"),
            "slot": tx.get("slot"),
            "block_time": tx.get("timestamp"),
            "at": iso(tx.get("timestamp")),
            "source": source,
            "transaction_type": tx_type,
            "event_kind": (
                "create_initial_buy" if tx_type == "CREATE" and token["token_delta_raw"] > 0
                else "pump_buy" if is_pump_economic_event and token["token_delta_raw"] > 0
                else "pump_sell" if is_pump_economic_event and token["token_delta_raw"] < 0
                else "transfer_in" if token["token_delta_raw"] > 0
                else "transfer_out"
            ),
            "is_pump_economic_event": is_pump_economic_event,
            "mint": token["mint"],
            "side": side,
            "token_delta_raw": token["token_delta_raw"],
            "token_decimals": token.get("decimals"),
            "native_balance_delta_lamports": native_delta,
            "native_balance_delta_sol": None if native_delta is None else native_delta / LAMPORTS_PER_SOL,
            "network_fee_lamports": int(tx.get("fee") or 0),
            "network_fee_sol": int(tx.get("fee") or 0) / LAMPORTS_PER_SOL,
            "native_window_start": iso(cutoff_ts),
            "native_window_end": iso(end_ts),
        }
        if side == "buy":
            event["native_sol_spent"] = None if native_delta is None else -native_delta / LAMPORTS_PER_SOL
            event["tracker_comparable_sol_spent"] = None if native_delta is None else -(native_delta + int(tx.get("fee") or 0)) / LAMPORTS_PER_SOL
        else:
            event["native_sol_received"] = None if native_delta is None else native_delta / LAMPORTS_PER_SOL
            event["tracker_comparable_sol_received"] = None if native_delta is None else (native_delta + int(tx.get("fee") or 0)) / LAMPORTS_PER_SOL
        events.append(event)
    events.sort(key=lambda x: (x["block_time"], x.get("slot") or 0, x["signature"]))
    return events, rejected


def build_positions(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_mint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("is_pump_economic_event"):
            by_mint[event["mint"]].append(event)

    positions: list[dict[str, Any]] = []
    tolerance = 3  # raw token units; protects only against integer dust.
    for mint, rows in by_mint.items():
        rows.sort(key=lambda x: (x["block_time"], x.get("slot") or 0, x["signature"]))
        cycle = 0
        inventory = 0
        buys: list[dict[str, Any]] = []
        sells: list[dict[str, Any]] = []

        def finish(status: str) -> None:
            nonlocal cycle, inventory, buys, sells
            if not buys and not sells:
                return
            cycle += 1
            buy_spend = sum(float(x.get("native_sol_spent") or 0) for x in buys)
            sell_received = sum(float(x.get("native_sol_received") or 0) for x in sells)
            tracker_spend = sum(float(x.get("tracker_comparable_sol_spent") or 0) for x in buys)
            tracker_received = sum(float(x.get("tracker_comparable_sol_received") or 0) for x in sells)
            tokens_bought = sum(max(0, int(x["token_delta_raw"])) for x in buys)
            tokens_sold = sum(max(0, -int(x["token_delta_raw"])) for x in sells)
            native_pnl = sell_received - buy_spend
            tracker_pnl = tracker_received - tracker_spend
            positions.append({
                "wallet": WALLET,
                "mint": mint,
                "position_cycle": cycle,
                "status": status,
                "first_buy_at": buys[0]["at"] if buys else None,
                "first_buy_ts": buys[0]["block_time"] if buys else None,
                "first_buy_slot": buys[0].get("slot") if buys else None,
                "last_buy_at": buys[-1]["at"] if buys else None,
                "first_sell_at": sells[0]["at"] if sells else None,
                "last_sell_at": sells[-1]["at"] if sells else None,
                "last_sell_ts": sells[-1]["block_time"] if sells else None,
                "hold_seconds": None if not buys or not sells else sells[-1]["block_time"] - buys[0]["block_time"],
                "buy_count": len(buys),
                "sell_count": len(sells),
                "tokens_bought_raw": tokens_bought,
                "tokens_sold_raw": tokens_sold,
                "unresolved_tokens_raw": inventory,
                "native_sol_spent": buy_spend,
                "native_sol_received": sell_received,
                "native_pnl_sol": native_pnl,
                "native_roi_pct": None if buy_spend <= 0 else 100 * native_pnl / buy_spend,
                "tracker_comparable_sol_spent": tracker_spend,
                "tracker_comparable_sol_received": tracker_received,
                "tracker_comparable_pnl_sol": tracker_pnl,
                "tracker_comparable_roi_pct": None if tracker_spend <= 0 else 100 * tracker_pnl / tracker_spend,
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
                if inventory <= tolerance and (buys or sells):
                    finish("closed" if sells else "unresolved")
                inventory += delta
                buys.append(event)
            else:
                inventory += delta
                sells.append(event)
                if inventory <= tolerance:
                    inventory = max(0, inventory)
                    finish("closed")
        if buys or sells:
            finish("unresolved" if inventory > tolerance else "closed")

    positions.sort(key=lambda x: (x.get("first_buy_ts") or 0, x["mint"], x["position_cycle"]))
    closed = [x for x in positions if x["status"] == "closed" and x["buy_count"] > 0 and x["sell_count"] > 0]
    unresolved = [x for x in positions if x["status"] != "closed"]
    return positions, {"closed_positions": len(closed), "unresolved_positions": len(unresolved)}


def pump_metadata(mint: str) -> dict[str, Any]:
    last_error = None
    for attempt in range(6):
        try:
            response = requests.get(
                f"https://frontend-api-v3.pump.fun/coins/{mint}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=45,
            )
            if response.status_code == 200:
                raw = response.json()
                wanted = [
                    "mint", "name", "symbol", "description", "image_uri", "metadata_uri",
                    "twitter", "telegram", "website", "bonding_curve", "associated_bonding_curve",
                    "creator", "created_timestamp", "raydium_pool", "complete", "is_currently_live",
                    "is_mayhem_mode", "king_of_the_hill_timestamp", "total_supply", "usd_market_cap",
                    "market_cap", "reply_count", "last_reply",
                ]
                out = {key: raw.get(key) for key in wanted}
                out["metadata_observed_at"] = iso(time.time())
                out["historical_availability_proven"] = False
                return out
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:
            last_error = repr(exc)
        time.sleep(0.5 + attempt)
    return {"mint": mint, "metadata_error": last_error, "metadata_observed_at": iso(time.time())}


def fetch_metadata(mints: list[str], workers: int) -> list[dict[str, Any]]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(pump_metadata, mints))
    rows.sort(key=lambda x: x.get("created_timestamp") or 0)
    return rows


def fetch_signature_integrity(api: Helius, cutoff_ts: int) -> dict[str, Any]:
    before = None
    count = failed = success = 0
    oldest = newest = None
    by_day: dict[str, Counter[str]] = defaultdict(Counter)
    pages = 0
    while pages < 5000:
        cfg: dict[str, Any] = {"limit": 1000}
        if before:
            cfg["before"] = before
        batch = api.rpc("getSignaturesForAddress", [WALLET, cfg]) or []
        pages += 1
        if not batch:
            break
        stop = False
        for row in batch:
            ts = row.get("blockTime")
            if not ts:
                continue
            if ts < cutoff_ts:
                stop = True
                break
            newest = ts if newest is None else max(newest, ts)
            oldest = ts if oldest is None else min(oldest, ts)
            count += 1
            key = "failed" if row.get("err") is not None else "success"
            failed += key == "failed"
            success += key == "success"
            day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
            by_day[day][key] += 1
        print(f"signature page={pages} in_window={count} oldest={iso(oldest)}", flush=True)
        if stop or len(batch) < 1000:
            break
        before = batch[-1].get("signature")
        if not before:
            break
    return {
        "submitted_signatures": count,
        "failed_signatures": failed,
        "successful_signatures": success,
        "failure_rate": failed / count if count else None,
        "newest": iso(newest),
        "oldest": iso(oldest),
        "pages": pages,
        "by_day": {day: dict(counts) for day, counts in sorted(by_day.items())},
    }


def safe_median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def economics_summary(positions: list[dict[str, Any]], metadata: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [x for x in positions if x["status"] == "closed" and x["buy_count"] and x["sell_count"]]
    unresolved = [x for x in positions if x["status"] != "closed"]
    pnls = [float(x["native_pnl_sol"]) for x in closed]
    tracker_pnls = [float(x["tracker_comparable_pnl_sol"]) for x in closed]
    gains = sum(x for x in pnls if x > 0)
    losses = -sum(x for x in pnls if x < 0)
    tracker_gains = sum(x for x in tracker_pnls if x > 0)
    tracker_losses = -sum(x for x in tracker_pnls if x < 0)
    buys = [float(x["native_sol_spent"]) for x in positions if x["buy_count"]]
    creator = Counter(row.get("creator") or "__missing__" for row in metadata)
    positive_total = sum(x for x in pnls if x > 0)
    sorted_winners = sorted((x for x in closed if x["native_pnl_sol"] > 0), key=lambda x: x["native_pnl_sol"], reverse=True)
    return {
        "closed_positions": len(closed),
        "unresolved_positions": len(unresolved),
        "native_realized_wr": sum(x > 0 for x in pnls) / len(pnls) if pnls else None,
        "native_profit_factor": gains / losses if losses else (math.inf if gains else None),
        "native_expectancy_sol": statistics.mean(pnls) if pnls else None,
        "native_median_trade_sol": safe_median(pnls),
        "native_total_realized_pnl_sol": sum(pnls),
        "tracker_comparable_wr": sum(x > 0 for x in tracker_pnls) / len(tracker_pnls) if tracker_pnls else None,
        "tracker_comparable_profit_factor": tracker_gains / tracker_losses if tracker_losses else (math.inf if tracker_gains else None),
        "tracker_comparable_total_pnl_sol": sum(tracker_pnls),
        "position_size_native_sol": {
            "count": len(buys),
            "median": safe_median(buys),
            "mean": statistics.mean(buys) if buys else None,
            "p10": sorted(buys)[max(0, int(len(buys) * 0.10) - 1)] if buys else None,
            "p90": sorted(buys)[min(len(buys) - 1, int(len(buys) * 0.90))] if buys else None,
        },
        "profit_concentration_top1_share": (sorted_winners[0]["native_pnl_sol"] / positive_total) if sorted_winners and positive_total else None,
        "profit_concentration_top5_share": (sum(x["native_pnl_sol"] for x in sorted_winners[:5]) / positive_total) if positive_total else None,
        "best_positions": [{"mint": x["mint"], "pnl_sol": x["native_pnl_sol"], "roi_pct": x["native_roi_pct"]} for x in sorted(closed, key=lambda x: x["native_pnl_sol"], reverse=True)[:10]],
        "worst_positions": [{"mint": x["mint"], "pnl_sol": x["native_pnl_sol"], "roi_pct": x["native_roi_pct"]} for x in sorted(closed, key=lambda x: x["native_pnl_sol"])[:10]],
        "metadata_mints": len(metadata),
        "creator_counts": dict(creator),
        "creator_is_target_count": creator.get(WALLET, 0),
        "creator_is_target_rate": creator.get(WALLET, 0) / len(metadata) if metadata else None,
        "metadata_missing_count": creator.get("__missing__", 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--metadata-workers", type=int, default=10)
    parser.add_argument("--skip-signature-integrity", action="store_true")
    args = parser.parse_args()

    for path in [Path("/root/bot/EdgeSniffer/.env"), Path("/root/bot/EdgeSniffer/.env.live-v4-high-flow-runner")]:
        load_env(path)
    keys = unique_nonempty([os.environ.get("HELIUS_API_KEY", ""), *os.environ.get("HELIUS_API_KEYS", "").split(",")])
    rpc_url = (
        os.environ.get("EDGE_ONCHAIN_RPC_URL")
        or os.environ.get("SOLANA_RPC_URL")
        or os.environ.get("RPC_URL")
        or os.environ.get("EDGE_LIVE_CONFIRM_RPC_URLS", "").split(",")[0].strip()
        or os.environ.get("SECURE_RPC_URL", "")
    )
    api = Helius(keys, rpc_url)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    end_ts = int(time.time())
    cutoff_ts = end_ts - args.days * 86_400

    transactions, coverage = fetch_transactions(api, cutoff_ts)
    write_jsonl_gz(out / "target_enhanced_transactions.jsonl.gz", transactions)
    events, rejected = trade_events(transactions, cutoff_ts, end_ts)
    write_jsonl_gz(out / "target_trade_events.jsonl.gz", events)
    write_json(out / "target_trade_parse_rejections.json", rejected)
    positions, position_counts = build_positions(events)
    write_jsonl_gz(out / "target_trade_ledger.jsonl.gz", positions)

    pump_mints = sorted({x["mint"] for x in events if x.get("is_pump_economic_event")})
    metadata = fetch_metadata(pump_mints, args.metadata_workers)
    write_jsonl_gz(out / "metadata_features.jsonl.gz", metadata)
    metadata_by_mint = {row.get("mint"): row for row in metadata}
    launches = []
    for event in events:
        if event.get("event_kind") != "create_initial_buy":
            continue
        row = dict(event)
        row.update({f"metadata_{k}": v for k, v in metadata_by_mint.get(event["mint"], {}).items() if k != "mint"})
        row["target_bought"] = 1
        row["selection_interpretation"] = "self_created_initial_buy"
        launches.append(row)
    write_jsonl_gz(out / "target_launches.jsonl.gz", launches)

    integrity = None
    if not args.skip_signature_integrity:
        integrity = fetch_signature_integrity(api, cutoff_ts)
        write_json(out / "signature_integrity.json", integrity)

    summary = {
        "schema": "wallet-8ycb-opening-sniper-v2-native-ledger-v1",
        "wallet": WALLET,
        "generated_at": iso(time.time()),
        "requested_window_days": args.days,
        "window_start": iso(cutoff_ts),
        "window_end": iso(end_ts),
        "coverage": coverage,
        "trade_events": len(events),
        "pump_fun_trade_events": sum(bool(x.get("is_pump_economic_event")) for x in events),
        "target_self_created_launches": len(launches),
        "distinct_pump_fun_mints": len(pump_mints),
        "trade_parse_rejections": len(rejected),
        "position_counts": position_counts,
        "signature_integrity": integrity,
        "economics": economics_summary(positions, metadata),
        "accounting_note": "Native PnL uses the target account's actual transaction-level lamport delta, including network fees, priority/tip flows, Pump fees, and any rent movements. Tracker-comparable PnL adds back only the reported network fee.",
        "metadata_note": "Pump.fun metadata was observed after the fact. Creator and created_timestamp are used as identity/chronology evidence; narrative fields are not asserted to have been historically available without archival proof.",
    }
    write_json(out / "native_ledger_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
