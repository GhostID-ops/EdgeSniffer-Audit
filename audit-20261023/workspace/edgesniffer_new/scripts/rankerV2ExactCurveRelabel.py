#!/usr/bin/env python3
"""Frozen Ranker V2 relabel using EXACT_CURVE_ECONOMICS_V1.

This is an analytics-only driver. It verifies and imports the immutable exact
curve evaluator, snapshots the frozen signal populations, and evaluates true
nonlinear Pump curve outcomes across size and timing matrices. It never submits
transactions and never changes Ranker selection semantics.
"""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import importlib.util
import json
import math
import pathlib
import sqlite3
import statistics
import subprocess
import sys
import time
import zlib
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Optional


EXPECTED_EVALUATOR_SHA256 = (
    "ed95c5de839428ae5fe238905e59f26d8956ff59f1819fda6f8ea9da9e1c732f"
)
THRESHOLD = 0.590596
SIZES_LAMPORTS = (10_000_000, 100_000_000, 500_000_000, 1_000_000_000)
ENTRY_DELAYS_MS = (25, 50, 100)
FIRST_SELLABLE_DELAYS_MS = (250, 500, 1000, 1476)
TP_PCT = 20.0
SL_PCT = -20.0
MAX_HOLD_MS = 120_000
BOT_WALLET = "2qfCBm5bNcQhDuikruVk91QQm6yVH9ZjjTaPfCRUGFyd"
RECENT_PROSPECTIVE_BOUNDARY_MS = 1788474382198  # frozen V2 cohort start + 8h


class RelabelError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class Signal:
    signal_id: str
    population: str
    mint: str
    decision_ms: int
    score: float
    state: str
    split: str
    old_outcome: Optional[str]
    old_return_pct: Optional[float]
    tags: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class StateSnapshot:
    at_ms: int
    end_ms: int
    state: Any
    creator_bps: int
    cashback_bps: int
    buyback_share_bps: int
    slot: int
    source: str
    timing_source: str


@dataclasses.dataclass(frozen=True)
class PreparedChain:
    entry_ms: int
    deadline_ms: int
    entry_state: Any
    entry_slot: int
    entry_source: str
    entry_state_age_ms: int
    creator_bps: int
    cashback_bps: int
    buyback_share_bps: int
    future_successful: tuple[dict[str, Any], ...]
    future_failed: tuple[dict[str, Any], ...]
    actual_transition_count: int


def sha256_path(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def percentile(values: Iterable[float], percentile_value: float) -> Optional[float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile_value
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def complete_gzip_rows(path: pathlib.Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read all complete gzip JSONL rows, tolerating an active partial tail."""
    process = subprocess.Popen(
        ["gzip", "-cd", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    rows: list[dict[str, Any]] = []
    malformed = 0
    for line in process.stdout:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
    stderr = process.stderr.read() if process.stderr else ""
    return_code = process.wait()
    return rows, {
        "path": str(path),
        "complete_rows": len(rows),
        "malformed_complete_rows": malformed,
        "gzip_return_code": return_code,
        "partial_tail_tolerated": return_code != 0,
        "stderr_tail": stderr[-500:] if return_code else "",
    }


def write_json(path: pathlib.Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_gzip_jsonl(path: pathlib.Path, rows: Iterable[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def read_gzip_jsonl(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def load_evaluator(path: pathlib.Path) -> Any:
    actual = sha256_path(path)
    if actual != EXPECTED_EVALUATOR_SHA256:
        raise RelabelError(
            f"evaluator hash mismatch: expected={EXPECTED_EVALUATOR_SHA256} actual={actual}"
        )
    spec = importlib.util.spec_from_file_location("exact_curve_economics_v1_frozen", path)
    if spec is None or spec.loader is None:
        raise RelabelError(f"cannot import evaluator {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_historical_signals(
    trigger_path: pathlib.Path, economics_path: pathlib.Path
) -> tuple[list[Signal], dict[str, Any]]:
    economics = {row["mint"]: row for row in read_gzip_jsonl(economics_path)}
    signals: list[Signal] = []
    duplicate = 0
    seen: set[str] = set()
    for row in read_gzip_jsonl(trigger_path):
        mint = str(row["mint"])
        if mint in seen:
            duplicate += 1
            continue
        seen.add(mint)
        old = economics.get(mint, {}).get("p75_1476ms") or {}
        decision_ms = int(row["ts"])
        signals.append(
            Signal(
                signal_id=f"historical:{mint}:{decision_ms}",
                population="historical",
                mint=mint,
                decision_ms=decision_ms,
                score=float(row["score"]),
                state=str(row["snapshot_kind"]),
                split=str(row["split"]),
                old_outcome=old.get("outcome"),
                old_return_pct=float(old["pnl"]) if old.get("pnl") is not None else None,
            )
        )
    signals.sort(key=lambda row: (row.decision_ms, row.mint))
    counts = Counter(signal.split for signal in signals)
    expected = {"train": 964, "validation": 428, "final_test": 766}
    if dict(counts) != expected or duplicate:
        raise RelabelError(
            f"historical population mismatch: counts={dict(counts)} duplicate={duplicate}"
        )
    return signals, {
        "rows": len(signals),
        "split_counts": dict(counts),
        "duplicates": duplicate,
        "trigger_sha256": sha256_path(trigger_path),
        "economics_sha256": sha256_path(economics_path),
    }


def load_prospective_signals(
    signal_path: pathlib.Path,
    outcome_path: pathlib.Path,
    n25_v2_signal_path: pathlib.Path,
    snapshot_now_ms: int,
) -> tuple[list[Signal], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    signal_rows, signal_health = complete_gzip_rows(signal_path)
    outcome_rows, outcome_health = complete_gzip_rows(outcome_path)
    n25_rows, n25_health = complete_gzip_rows(n25_v2_signal_path)
    n25_keys = {
        (str(row.get("mint")), int(row.get("threshold_decision_ts_ms") or 0))
        for row in n25_rows
        if row.get("mint")
    }
    outcomes: dict[str, dict[str, Any]] = {}
    for row in outcome_rows:
        mint = row.get("mint")
        if mint:
            outcomes[str(mint)] = row
    first: dict[str, dict[str, Any]] = {}
    threshold_mismatch = 0
    for row in signal_rows:
        mint = row.get("mint")
        if not mint:
            continue
        score = float(row.get("score") or 0)
        threshold = float(row.get("score_threshold") or THRESHOLD)
        if threshold != THRESHOLD or score < THRESHOLD or row.get("passes_threshold") is not True:
            threshold_mismatch += 1
            continue
        decision_ms = int(row.get("threshold_decision_ts_ms") or 0)
        if decision_ms <= 0:
            continue
        previous = first.get(str(mint))
        if previous is None or decision_ms < int(previous["threshold_decision_ts_ms"]):
            first[str(mint)] = row
    signals: list[Signal] = []
    for mint, row in first.items():
        decision_ms = int(row["threshold_decision_ts_ms"])
        old = (outcomes.get(mint) or {}).get("p75_primary") or {}
        tags = ["full_prospective"]
        if decision_ms >= RECENT_PROSPECTIVE_BOUNDARY_MS:
            tags.append("recent_prospective")
        if decision_ms >= snapshot_now_ms - 24 * 60 * 60 * 1000:
            tags.append("last_24h")
        if (mint, decision_ms) in n25_keys or any(key[0] == mint for key in n25_keys):
            tags.append("n25_v2_window")
        signals.append(
            Signal(
                signal_id=f"prospective:{mint}:{decision_ms}",
                population="prospective",
                mint=mint,
                decision_ms=decision_ms,
                score=float(row["score"]),
                state=str(row["state"]),
                split="full_prospective",
                old_outcome=old.get("outcome"),
                old_return_pct=float(old["pnl"]) if old.get("pnl") is not None else None,
                tags=tuple(tags),
            )
        )
    signals.sort(key=lambda row: (row.decision_ms, row.mint))
    return signals, {
        "signals": signal_health,
        "outcomes": outcome_health,
        "n25_v2": n25_health,
        "unique_signals": len(signals),
        "threshold_mismatches": threshold_mismatch,
        "resolved_old_outcomes": sum(signal.old_return_pct is not None for signal in signals),
        "tag_counts": dict(Counter(tag for signal in signals for tag in signal.tags)),
    }, signal_rows, outcome_rows


class RpcEvidenceCache:
    def __init__(self, path: pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS transactions "
            "(signature TEXT PRIMARY KEY, available INTEGER NOT NULL, payload BLOB)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS supplies "
            "(mint TEXT PRIMARY KEY, amount TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS supply_failures "
            "(mint TEXT PRIMARY KEY, reason TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS curve_history_windows ("
            "signal_id TEXT PRIMARY KEY, curve TEXT NOT NULL, start_ms INTEGER NOT NULL, "
            "end_ms INTEGER NOT NULL, complete INTEGER NOT NULL, error TEXT, "
            "pages INTEGER NOT NULL, payload BLOB NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        semantic = self.connection.execute(
            "SELECT value FROM metadata WHERE key='null_transaction_semantics'"
        ).fetchone()
        self.invalidated_ambiguous_nulls = 0
        if semantic is None or semantic[0] != "confirmed_rpc_result_null_v1":
            self.invalidated_ambiguous_nulls = int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM transactions WHERE available=0"
                ).fetchone()[0]
            )
            self.connection.execute("DELETE FROM transactions WHERE available=0")
            self.connection.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
                ("null_transaction_semantics", "confirmed_rpc_result_null_v1"),
            )
        self.connection.commit()

    def transaction(self, signature: str) -> tuple[bool, Optional[dict[str, Any]]]:
        row = self.connection.execute(
            "SELECT available,payload FROM transactions WHERE signature=?", (signature,)
        ).fetchone()
        if row is None:
            return False, None
        if not row[0]:
            return True, None
        return True, json.loads(zlib.decompress(row[1]))

    def put_transactions(self, rows: Iterable[tuple[str, Optional[dict[str, Any]]]]) -> None:
        encoded = []
        for signature, value in rows:
            payload = zlib.compress(json.dumps(value, separators=(",", ":")).encode(), 6) if value else None
            encoded.append((signature, 1 if value else 0, payload))
        self.connection.executemany(
            "INSERT OR REPLACE INTO transactions(signature,available,payload) VALUES(?,?,?)",
            encoded,
        )
        self.connection.commit()

    def supply(self, mint: str) -> Optional[int]:
        row = self.connection.execute("SELECT amount FROM supplies WHERE mint=?", (mint,)).fetchone()
        return int(row[0]) if row else None

    def put_supplies(self, rows: Iterable[tuple[str, int]]) -> None:
        self.connection.executemany(
            "INSERT OR REPLACE INTO supplies(mint,amount) VALUES(?,?)",
            [(mint, str(amount)) for mint, amount in rows],
        )
        self.connection.commit()

    def supply_failure(self, mint: str) -> Optional[str]:
        row = self.connection.execute(
            "SELECT reason FROM supply_failures WHERE mint=?", (mint,)
        ).fetchone()
        return str(row[0]) if row else None

    def put_supply_failures(self, rows: Iterable[tuple[str, str]]) -> None:
        self.connection.executemany(
            "INSERT OR REPLACE INTO supply_failures(mint,reason) VALUES(?,?)", list(rows)
        )
        self.connection.commit()

    def curve_history(self, signal_id: str) -> Optional[dict[str, Any]]:
        row = self.connection.execute(
            "SELECT curve,start_ms,end_ms,complete,error,pages,payload "
            "FROM curve_history_windows WHERE signal_id=?", (signal_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "signal_id": signal_id,
            "curve": row[0],
            "start_ms": int(row[1]),
            "end_ms": int(row[2]),
            "complete": bool(row[3]),
            "error": row[4],
            "pages": int(row[5]),
            "rows": json.loads(zlib.decompress(row[6])),
        }

    def put_curve_history(self, value: dict[str, Any]) -> None:
        payload = zlib.compress(
            json.dumps(value["rows"], separators=(",", ":")).encode(), 6
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO curve_history_windows"
            "(signal_id,curve,start_ms,end_ms,complete,error,pages,payload) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                value["signal_id"], value["curve"], value["start_ms"], value["end_ms"],
                int(value["complete"]), value.get("error"), value["pages"], payload,
            ),
        )
        self.connection.commit()

    def stats(self) -> dict[str, int]:
        tx_total, tx_available = self.connection.execute(
            "SELECT COUNT(*),SUM(available) FROM transactions"
        ).fetchone()
        supply_total = self.connection.execute("SELECT COUNT(*) FROM supplies").fetchone()[0]
        supply_failures = self.connection.execute(
            "SELECT COUNT(*) FROM supply_failures"
        ).fetchone()[0]
        history_windows = self.connection.execute(
            "SELECT COUNT(*) FROM curve_history_windows"
        ).fetchone()[0]
        return {
            "transactions": int(tx_total or 0),
            "transactions_available": int(tx_available or 0),
            "supplies": int(supply_total or 0),
            "supply_failures": int(supply_failures or 0),
            "curve_history_windows": int(history_windows or 0),
            "ambiguous_nulls_invalidated": self.invalidated_ambiguous_nulls,
        }


def rpc_calls_exact(
    rpc: Any, calls: list[tuple[str, list[Any]]]
) -> list[tuple[str, Any, Optional[str]]]:
    """Keep confirmed null distinct from transport or JSON-RPC failure."""
    request_ids = list(range(rpc._next_id, rpc._next_id + len(calls)))
    rpc._next_id += len(calls)

    def invoke(item: tuple[int, tuple[str, list[Any]]]) -> tuple[str, Any, Optional[str]]:
        request_id, (method, params) = item
        try:
            response = rpc._post(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )
        except Exception as exc:
            return "transport_error", None, f"{type(exc).__name__}:{exc}"
        if response.get("error") is not None:
            return "rpc_error", None, json.dumps(response["error"], sort_keys=True)
        result = response.get("result")
        return ("confirmed_null" if result is None else "available"), result, None

    with ThreadPoolExecutor(max_workers=min(12, max(1, len(calls)))) as executor:
        return list(executor.map(invoke, zip(request_ids, calls)))


def rpc_calls_exact_with_retries(
    rpc: Any,
    calls: list[tuple[str, list[Any]]],
    *,
    attempts: int = 3,
    allow_invalid_param: bool = False,
) -> tuple[list[tuple[str, Any, Optional[str]]], Counter[str]]:
    results: list[Optional[tuple[str, Any, Optional[str]]]] = [None] * len(calls)
    pending = list(range(len(calls)))
    transient_counts: Counter[str] = Counter()
    for attempt in range(attempts):
        current = [calls[index] for index in pending]
        current_results = rpc_calls_exact(rpc, current)
        retry: list[int] = []
        for index, result in zip(pending, current_results):
            status, _, error = result
            if status == "rpc_error" and allow_invalid_param:
                try:
                    code = int(json.loads(error or "{}").get("code"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    code = 0
                if code == -32602:
                    results[index] = ("deterministic_rpc_error", None, error)
                    continue
            if status in {"transport_error", "rpc_error"}:
                transient_counts[status] += 1
                retry.append(index)
            else:
                results[index] = result
        pending = retry
        if not pending:
            break
        time.sleep(0.5 * (2**attempt))
    if pending:
        details = Counter((results[index] or ("unresolved", None, None))[0] for index in pending)
        raise RelabelError(
            f"rpc_evidence_fetch_unresolved:count={len(pending)} details={dict(details)}"
        )
    return [result for result in results if result is not None], transient_counts


def fetch_rpc_evidence(
    ev: Any,
    rpc: Any,
    cache: RpcEvidenceCache,
    observations: dict[str, list[Any]],
    mints: list[str],
    extra_signatures: Iterable[str] = (),
) -> dict[str, Any]:
    signatures = sorted(
        {row.signature for rows in observations.values() for row in rows}
        | set(extra_signatures)
    )
    missing = [signature for signature in signatures if not cache.transaction(signature)[0]]
    fetched = 0
    transient_counts: Counter[str] = Counter()
    for offset in range(0, len(missing), 240):
        batch = missing[offset : offset + 240]
        calls = [
            (
                "getTransaction",
                [
                    signature,
                    {
                        "encoding": "json",
                        "commitment": "finalized",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            )
            for signature in batch
        ]
        results, transients = rpc_calls_exact_with_retries(rpc, calls)
        transient_counts.update(transients)
        cache.put_transactions(
            (signature, result if status == "available" else None)
            for signature, (status, result, _) in zip(batch, results)
        )
        fetched += len(batch)
        if fetched % 2400 == 0 or fetched == len(missing):
            print(f"rpc transactions {fetched}/{len(missing)}", flush=True)
    missing_supplies = [
        mint for mint in mints
        if cache.supply(mint) is None and cache.supply_failure(mint) is None
    ]
    for offset in range(0, len(missing_supplies), 240):
        batch = missing_supplies[offset : offset + 240]
        calls = [("getTokenSupply", [mint, {"commitment": "finalized"}]) for mint in batch]
        results, transients = rpc_calls_exact_with_retries(
            rpc, calls, allow_invalid_param=True
        )
        transient_counts.update(transients)
        available = []
        failures = []
        for mint, (status, result, error) in zip(batch, results):
            if status == "available" and result.get("value") and result["value"].get("amount") is not None:
                available.append((mint, int(result["value"]["amount"])))
            elif status == "deterministic_rpc_error":
                failures.append((mint, f"getTokenSupply_invalid_param:{error}"))
            elif status == "confirmed_null":
                failures.append((mint, "getTokenSupply_confirmed_null"))
        cache.put_supplies(available)
        cache.put_supply_failures(failures)
        print(
            f"rpc supplies {min(offset + len(batch), len(missing_supplies))}/"
            f"{len(missing_supplies)} available={len(available)}",
            flush=True,
        )
    return {
        "unique_signatures": len(signatures),
        "transactions_fetched_now": len(missing),
        "supplies_requested_now": len(missing_supplies),
        "cache": cache.stats(),
        "transient_rpc_attempts_retried": dict(transient_counts),
    }


def tx_map_for_mint(cache: RpcEvidenceCache, rows: list[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for signature in {row.signature for row in rows}:
        present, tx = cache.transaction(signature)
        result[signature] = tx if present else None
    return result


def infer_bonding_curve(observations: list[Any]) -> tuple[Optional[str], Optional[str]]:
    indexes = {
        "buy": 3,
        "buy_exact_sol_in": 3,
        "sell": 3,
        "buy_v2": 10,
        "buy_exact_quote_in_v2": 10,
        "sell_v2": 9,
    }
    values = set()
    for observation in observations:
        index = indexes.get(observation.instruction.kind)
        if index is not None and len(observation.source_accounts) > index:
            values.add(str(observation.source_accounts[index]))
    if not values:
        return None, "bonding_curve_unavailable"
    if len(values) != 1:
        return None, "multiple_bonding_curves_for_mint"
    return next(iter(values)), None


def fetch_one_curve_history(
    ev: Any, rpc: Any, signal: Signal, curve: str, boundary_signature: str
) -> dict[str, Any]:
    start_ms = signal.decision_ms - 2_000
    end_ms = signal.decision_ms + max(ENTRY_DELAYS_MS) + MAX_HOLD_MS + 2_000
    start_sec = start_ms // 1000
    end_sec = end_ms // 1000
    client = ev.RpcClient(rpc.url, timeout=rpc.timeout, retries=rpc.retries)
    rows: list[dict[str, Any]] = []
    before: Optional[str] = None
    complete = False
    error: Optional[str] = None
    pages = 0
    for _ in range(100):
        options: dict[str, Any] = {"limit": 1000, "commitment": "finalized"}
        if before:
            options["before"] = before
        try:
            result, _ = rpc_calls_exact_with_retries(
                client, [("getSignaturesForAddress", [curve, options])]
            )
        except Exception as exc:
            error = f"curve_history_rpc:{exc}"
            break
        status, page, detail = result[0]
        if status != "available":
            error = f"curve_history_{status}:{detail}"
            break
        page = page or []
        pages += 1
        if not page:
            error = "curve_history_boundary_not_retained"
            break
        usable_times = [int(item["blockTime"]) for item in page if item.get("blockTime")]
        if len(usable_times) != len(page):
            error = "curve_history_missing_block_time"
            break
        for item in page:
            block_time = int(item["blockTime"])
            if start_sec <= block_time <= end_sec:
                rows.append(
                    {
                        "signature": str(item["signature"]),
                        "slot": int(item.get("slot") or 0),
                        "transaction_index": int(item.get("transactionIndex") or -1),
                        "block_time": block_time,
                        "err": item.get("err"),
                    }
                )
        if any(str(item["signature"]) == boundary_signature for item in page):
            complete = True
            break
        oldest = min(usable_times)
        if oldest <= start_sec:
            error = "curve_history_anchor_signature_not_found"
            break
        next_before = str(page[-1]["signature"])
        if next_before == before:
            error = "curve_history_pagination_stalled"
            break
        before = next_before
    else:
        error = "curve_history_page_cap"
    dedup = {row["signature"]: row for row in rows}
    return {
        "signal_id": signal.signal_id,
        "curve": curve,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "complete": complete,
        "error": None if complete else (error or "curve_history_incomplete"),
        "pages": pages,
        "rows": sorted(
            dedup.values(),
            key=lambda row: (row["slot"], row["transaction_index"], row["signature"]),
        ),
    }


def fetch_curve_histories(
    ev: Any,
    rpc: Any,
    cache: RpcEvidenceCache,
    signals: list[Signal],
    observations: dict[str, list[Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    histories: dict[str, dict[str, Any]] = {}
    pending: list[tuple[Signal, str, str]] = []
    curve_errors: Counter[str] = Counter()
    cached_count = 0
    for signal in signals:
        cached = cache.curve_history(signal.signal_id)
        if cached is not None:
            histories[signal.signal_id] = cached
            cached_count += 1
            continue
        curve, error = infer_bonding_curve(observations.get(signal.mint, []))
        if curve is None:
            histories[signal.signal_id] = {
                "signal_id": signal.signal_id,
                "curve": "",
                "start_ms": signal.decision_ms - 2_000,
                "end_ms": signal.decision_ms + max(ENTRY_DELAYS_MS) + MAX_HOLD_MS + 2_000,
                "complete": False,
                "error": error,
                "pages": 0,
                "rows": [],
            }
            curve_errors[error or "bonding_curve_unavailable"] += 1
            continue
        mint_rows = observations.get(signal.mint, [])
        transactions = tx_map_for_mint(cache, mint_rows)
        records = ev.transaction_event_records(mint_rows, transactions)
        successful = [
            row for row in records
            if row["success"] is True and row["event"] is not None
        ]
        try:
            anchor = choose_entry_anchor(
                ev, successful, signal.decision_ms + min(ENTRY_DELAYS_MS)
            )
        except Exception as exc:
            value = {
                "signal_id": signal.signal_id,
                "curve": curve,
                "start_ms": signal.decision_ms - 2_000,
                "end_ms": signal.decision_ms + max(ENTRY_DELAYS_MS) + MAX_HOLD_MS + 2_000,
                "complete": False,
                "error": f"curve_history_anchor:{exc}",
                "pages": 0,
                "rows": [],
            }
            cache.put_curve_history(value)
            histories[signal.signal_id] = value
            curve_errors[value["error"]] += 1
            continue
        pending.append((signal, curve, str(anchor["source"].signature)))
    completed = 0
    for offset in range(0, len(pending), 24):
        batch = pending[offset : offset + 24]
        with ThreadPoolExecutor(max_workers=min(12, len(batch))) as executor:
            values = list(
                executor.map(
                    lambda item: fetch_one_curve_history(
                        ev, rpc, item[0], item[1], item[2]
                    ),
                    batch,
                )
            )
        for value in values:
            cache.put_curve_history(value)
            histories[value["signal_id"]] = value
            if not value["complete"]:
                curve_errors[value["error"] or "curve_history_incomplete"] += 1
        completed += len(batch)
        if completed % 240 == 0 or completed == len(pending):
            print(f"curve histories {completed}/{len(pending)}", flush=True)
    return histories, {
        "signals": len(signals),
        "cached": cached_count,
        "fetched_now": len(pending),
        "complete": sum(value["complete"] for value in histories.values()),
        "incomplete": sum(not value["complete"] for value in histories.values()),
        "errors": dict(curve_errors),
        "rows": sum(len(value["rows"]) for value in histories.values()),
    }


def augment_observations_from_curve_history(
    ev: Any,
    observations: dict[str, list[Any]],
    signals: list[Signal],
    histories: dict[str, dict[str, Any]],
    cache: RpcEvidenceCache,
) -> dict[str, Any]:
    added = 0
    timing_block_only = 0
    missing_success_details = 0
    for signal in signals:
        history = histories[signal.signal_id]
        if not history["complete"]:
            continue
        mint_rows = observations.setdefault(signal.mint, [])
        existing = {
            (row.signature, row.instruction.kind, row.instruction.arg0, row.instruction.arg1): index
            for index, row in enumerate(mint_rows)
        }
        local_by_signature = {row.signature: row for row in mint_rows}
        for item in history["rows"]:
            if item["err"] is not None:
                continue
            present, tx = cache.transaction(item["signature"])
            if not present or tx is None:
                missing_success_details += 1
                continue
            local = local_by_signature.get(item["signature"])
            timing_ms = local.observed_ms if local else int(item["block_time"]) * 1000
            timing_source = local.timing_source if local else "chain_block_time_only"
            for source in ev.extract_pump_source_observations(
                tx, item["signature"], signal.mint, timing_ms, timing_source
            ):
                source = dataclasses.replace(
                    source, transaction_index=int(item["transaction_index"])
                )
                key = (
                    source.signature, source.instruction.kind,
                    source.instruction.arg0, source.instruction.arg1,
                )
                if key in existing:
                    index = existing[key]
                    mint_rows[index] = dataclasses.replace(
                        mint_rows[index], transaction_index=int(item["transaction_index"])
                    )
                    continue
                mint_rows.append(source)
                existing[key] = len(mint_rows) - 1
                added += 1
                timing_block_only += int(timing_source == "chain_block_time_only")
        mint_rows.sort(
            key=lambda row: (row.slot, row.transaction_index, row.instruction_index)
        )
    return {
        "observations_added": added,
        "chain_block_time_only_added": timing_block_only,
        "successful_history_transactions_missing_details": missing_success_details,
    }


def transactions_for_signal(
    cache: RpcEvidenceCache,
    observations: list[Any],
    history: dict[str, Any],
) -> dict[str, Any]:
    result = tx_map_for_mint(cache, observations)
    if not history["complete"]:
        return result
    history_by_signature = {row["signature"]: row for row in history["rows"]}
    for observation in observations:
        if result.get(observation.signature) is not None:
            continue
        if not (history["start_ms"] <= observation.observed_ms <= history["end_ms"]):
            continue
        item = history_by_signature.get(observation.signature)
        if item is None or item["err"] is not None:
            result[observation.signature] = {
                "slot": int(item["slot"] if item else observation.slot),
                "blockTime": int(item["block_time"] if item else observation.observed_ms // 1000),
                "meta": {"err": item["err"] if item else {"confirmedDropped": True}},
            }
    return result


def actual_row_interval(ev: Any, row: dict[str, Any]) -> tuple[int, int]:
    return ev.observation_interval_ms(row["source"])


def choose_entry_anchor(
    ev: Any, successful: list[dict[str, Any]], entry_ms: int
) -> dict[str, Any]:
    before = [row for row in successful if ev.temporal_side(row["source"], entry_ms) == "before"]
    ambiguous = [
        row for row in successful if ev.temporal_side(row["source"], entry_ms) == "ambiguous"
    ]
    if ambiguous:
        raise RelabelError("entry_timing_ambiguous")
    if not before:
        raise RelabelError("no_authoritative_causal_entry_state")
    before_ids = {id(row) for row in before}
    successor_pre_states = {row["event"].pre_state for row in before}
    sinks = [row for row in before if row["event"].post_state not in successor_pre_states]
    if not sinks:
        raise RelabelError("ambiguous_pre_entry_reserve_cycle")
    latest_time = max(actual_row_interval(ev, row)[1] for row in sinks)
    candidates = [row for row in sinks if actual_row_interval(ev, row)[1] == latest_time]
    if len(candidates) != 1:
        raise RelabelError("multiple_disconnected_entry_state_candidates")
    anchor = candidates[0]
    if id(anchor) not in before_ids:
        raise RelabelError("internal_entry_anchor_error")
    return anchor


def build_successor_chain(
    ev: Any,
    successful: list[dict[str, Any]],
    anchor: dict[str, Any],
    entry_ms: int,
    deadline_ms: int,
) -> list[dict[str, Any]]:
    by_pre: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in successful:
        by_pre[row["event"].pre_state].append(row)
    chain: list[dict[str, Any]] = []
    current_state = anchor["event"].post_state
    previous_ms = actual_row_interval(ev, anchor)[1]
    seen = {id(anchor)}
    while True:
        candidates = [row for row in by_pre.get(current_state, []) if id(row) not in seen]
        if not candidates:
            break
        if len(candidates) != 1:
            raise RelabelError("ambiguous_reserve_successor")
        row = candidates[0]
        start_ms, end_ms = actual_row_interval(ev, row)
        if end_ms < previous_ms:
            raise RelabelError("non_monotonic_reserve_event_time")
        seen.add(id(row))
        current_state = row["event"].post_state
        previous_ms = end_ms
        if start_ms > entry_ms:
            chain.append(row)
        if start_ms > deadline_ms:
            break
    relevant = [
        row
        for row in successful
        if actual_row_interval(ev, row)[0] > entry_ms
        and actual_row_interval(ev, row)[0] <= deadline_ms
    ]
    missing = [row for row in relevant if id(row) not in {id(item) for item in chain}]
    if missing:
        raise RelabelError("reserve_continuity_gap_in_horizon")
    return [row for row in chain if actual_row_interval(ev, row)[0] <= deadline_ms]


def prepare_chain(
    ev: Any,
    signal: Signal,
    entry_delay_ms: int,
    observations: list[Any],
    transactions: dict[str, Any],
    fee_config: Any,
    supply: int,
    corpus_end_ms: int,
) -> PreparedChain:
    entry_ms = signal.decision_ms + entry_delay_ms
    deadline_ms = entry_ms + MAX_HOLD_MS
    if corpus_end_ms < deadline_ms:
        raise RelabelError("horizon_unresolved")
    records = ev.transaction_event_records(observations, transactions)
    missing = [
        row
        for row in records
        if row["success"] is None and actual_row_interval(ev, row)[0] <= deadline_ms
    ]
    if missing:
        raise RelabelError("missing_finalized_transaction_status")
    event_missing = [
        row
        for row in records
        if row["success"] is True
        and row["event"] is None
        and actual_row_interval(ev, row)[0] <= deadline_ms
    ]
    if event_missing:
        raise RelabelError("successful_transaction_missing_trade_event")
    successful = [row for row in records if row["success"] is True and row["event"] is not None]
    if not successful:
        raise RelabelError("no_successful_trade_events")
    validated = 0
    for row in successful:
        if actual_row_interval(ev, row)[0] > deadline_ms:
            continue
        event = row["event"]
        try:
            config_fees = fee_config.fees_for(event.pre_state, supply)
            fees = ev.effective_event_fees(config_fees, event)
            ev.validate_actual_trade_transition(row["source"], event, fees)
        except Exception as exc:
            raise RelabelError(f"actual_transition_validation:{exc}") from exc
        validated += 1
    anchor = choose_entry_anchor(ev, successful, entry_ms)
    future = build_successor_chain(ev, successful, anchor, entry_ms, deadline_ms)
    anchor_end_ms = actual_row_interval(ev, anchor)[1]
    event = anchor["event"]
    failed = tuple(
        row
        for row in records
        if row["success"] is False
        and actual_row_interval(ev, row)[0] > entry_ms
        and actual_row_interval(ev, row)[0] <= deadline_ms
    )
    return PreparedChain(
        entry_ms=entry_ms,
        deadline_ms=deadline_ms,
        entry_state=event.post_state,
        entry_slot=int(anchor["source"].slot),
        entry_source=str(anchor["source"].signature),
        entry_state_age_ms=max(0, entry_ms - anchor_end_ms),
        creator_bps=int(event.creator_fee_basis_points + event.cashback_fee_basis_points),
        cashback_bps=int(event.cashback_fee_basis_points),
        buyback_share_bps=int(event.buyback_fee_basis_points),
        future_successful=tuple(future),
        future_failed=failed,
        actual_transition_count=validated,
    )


def apply_unbounded(ev: Any, state: Any, instruction: Any, fees: Any, cashback: int, buyback: int) -> Any:
    if instruction.side == "sell":
        return ev.quote_exact_token_sell(
            state, instruction.arg0, 0, fees,
            cashback_bps=cashback, buyback_share_bps=buyback,
        )
    if instruction.exact_quote_in:
        return ev.quote_exact_quote_in_buy(state, instruction.arg0, fees)
    return ev.quote_exact_token_buy(state, instruction.arg0, (1 << 63) - 1, fees)


def marginal_entry_tokens(state: Any, pricing_quote_in: int) -> int:
    return pricing_quote_in * state.virtual_token // state.virtual_sol


def marginal_sell_quote(ev: Any, state: Any, inventory: int, fees: Any, cashback: int, buyback: int) -> Any:
    gross = inventory * state.virtual_sol // state.virtual_token
    if gross <= 0 or gross > state.real_sol:
        raise RelabelError("marginal_sell_exceeds_real_reserves")
    protocol = ev.fee_ceil(gross, fees.protocol_bps)
    creator = ev.fee_ceil(gross, fees.creator_bps)
    lp = ev.fee_ceil(gross, fees.lp_bps)
    immediate = gross - protocol - creator - lp
    return ev.SellQuote(
        token_in=inventory,
        gross_quote_out=gross,
        immediate_quote_out=immediate,
        protocol_fee=protocol,
        creator_fee=creator,
        lp_fee=lp,
        cashback_accrual=ev.fee_ceil(gross, cashback) if cashback else 0,
        buyback_fee_allocation=protocol * buyback // 10_000 if buyback else 0,
        post_state=state,
    )


def transaction_groups(rows: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    by_signature: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        signature = str(row["source"].signature)
        if signature not in by_signature:
            by_signature[signature] = []
            groups.append(by_signature[signature])
        by_signature[signature].append(row)
    return groups


def apply_external_transaction(
    ev: Any,
    rows: list[dict[str, Any]],
    state: Any,
    fee_config: Any,
    supply: int,
    *,
    honor_constraints: bool,
) -> tuple[Any, bool]:
    """Apply one transaction atomically and report whether amounts changed."""
    candidate_state = state
    economic_change = False
    for row in rows:
        source = row["source"]
        event = row["event"]
        creator_bps = int(event.creator_fee_basis_points + event.cashback_fee_basis_points)
        config_fees = fee_config.fees_for(candidate_state, supply)
        if creator_bps not in (0, config_fees.creator_bps):
            raise RelabelError(
                f"counterfactual_creator_fee_regime_unsupported:{source.signature}"
            )
        fees = ev.Fees(config_fees.protocol_bps, creator_bps, config_fees.lp_bps)
        if honor_constraints:
            applied = ev.apply_instruction(
                candidate_state,
                source.instruction,
                fees,
                cashback_bps=int(event.cashback_fee_basis_points),
                buyback_share_bps=int(event.buyback_fee_basis_points),
            )
        else:
            applied = apply_unbounded(
                ev,
                candidate_state,
                source.instruction,
                fees,
                int(event.cashback_fee_basis_points),
                int(event.buyback_fee_basis_points),
            )
        if isinstance(applied, ev.BuyQuote):
            if applied.token_out != event.token_amount or applied.curve_quote_in != event.sol_amount:
                economic_change = True
        elif applied.token_in != event.token_amount or applied.gross_quote_out != event.sol_amount:
            economic_change = True
        candidate_state = applied.post_state
    return candidate_state, economic_change


def replay_size(
    ev: Any,
    chain: PreparedChain,
    size_lamports: int,
    fee_config: Any,
    supply: int,
    *,
    variant: str = "full",
) -> dict[str, Any]:
    entry_config = fee_config.fees_for(chain.entry_state, supply)
    if chain.creator_bps not in (0, entry_config.creator_bps):
        raise RelabelError("counterfactual_entry_creator_fee_regime_unsupported")
    entry_fees = ev.Fees(
        entry_config.protocol_bps,
        chain.creator_bps,
        entry_config.lp_bps,
    )
    own_entry_fees = ev.Fees(0, 0, 0) if variant == "no_own_fees" else entry_fees
    buy = ev.quote_exact_quote_in_buy(chain.entry_state, size_lamports, own_entry_fees)
    zero_impact_tokens = marginal_entry_tokens(chain.entry_state, buy.pricing_quote_in)
    if variant == "no_entry_impact":
        inventory = zero_impact_tokens
        state = chain.entry_state
    else:
        inventory = buy.token_out
        state = buy.post_state
    creator_bps = chain.creator_bps
    cashback_bps = chain.cashback_bps
    buyback_share_bps = chain.buyback_share_bps
    snapshots = [
        StateSnapshot(
            chain.entry_ms, chain.entry_ms, state, creator_bps, cashback_bps,
            buyback_share_bps, chain.entry_slot, "hypothetical_entry", "exact_signal_clock"
        )
    ]
    ext_total = 0
    ext_status_flips = 0
    ext_economic_changes = 0
    own_live_zeroed = 0
    for tx_rows in transaction_groups(chain.future_successful):
        source = tx_rows[-1]["source"]
        event = tx_rows[-1]["event"]
        creator_bps = int(event.creator_fee_basis_points + event.cashback_fee_basis_points)
        cashback_bps = int(event.cashback_fee_basis_points)
        buyback_share_bps = int(event.buyback_fee_basis_points)
        if any(row["event"].user == BOT_WALLET for row in tx_rows):
            own_live_zeroed += 1
        else:
            ext_total += 1
            try:
                candidate_state, changed = apply_external_transaction(
                    ev, tx_rows, state, fee_config, supply, honor_constraints=True
                )
            except Exception:
                ext_status_flips += 1
                if variant == "force_external_success":
                    try:
                        candidate_state, changed = apply_external_transaction(
                            ev, tx_rows, state, fee_config, supply,
                            honor_constraints=False,
                        )
                    except Exception:
                        candidate_state = None
                        changed = False
                else:
                    candidate_state = None
                    changed = False
            if candidate_state is not None:
                state = candidate_state
                ext_economic_changes += int(changed)
        observed_ms = max(actual_row_interval(ev, row)[0] for row in tx_rows)
        observed_end_ms = max(actual_row_interval(ev, row)[1] for row in tx_rows)
        timing_source = (
            "chain_block_time_only"
            if any(row["source"].timing_source == "chain_block_time_only" for row in tx_rows)
            else "canonical_local_receive"
        )
        snapshots.append(
            StateSnapshot(
                observed_ms, observed_end_ms, state, creator_bps, cashback_bps,
                buyback_share_bps, int(source.slot), str(source.signature), timing_source
            )
        )
    failed_constraint_counts: Counter[str] = Counter()
    for tx_rows in transaction_groups(chain.future_failed):
        source = tx_rows[-1]["source"]
        snapshot = max(
            (item for item in snapshots if item.at_ms <= source.observed_ms),
            key=lambda item: item.at_ms,
            default=None,
        )
        if snapshot is None:
            failed_constraint_counts["AMBIGUOUS_NO_STATE"] += 1
            continue
        try:
            apply_external_transaction(
                ev, tx_rows, snapshot.state, fee_config, supply,
                honor_constraints=True,
            )
        except Exception:
            failed_constraint_counts["CONSTRAINT_FAIL_ZERO_MUTATION"] += 1
        else:
            failed_constraint_counts["CONSTRAINT_PASS_CHAIN_FAILED_ZERO_MUTATION"] += 1
    entry_curve_impact_pct = (
        (zero_impact_tokens / buy.token_out - 1) * 100 if buy.token_out else None
    )
    return {
        "buy": buy,
        "inventory": inventory,
        "snapshots": snapshots,
        "entry_fees": entry_fees,
        "entry_curve_impact_pct": entry_curve_impact_pct,
        "external_transactions": ext_total,
        "external_status_flips": ext_status_flips,
        "external_economic_changes": ext_economic_changes,
        "own_live_transactions_zeroed": own_live_zeroed,
        "failed_constraint_counts": dict(failed_constraint_counts),
        "variant": variant,
    }


def exit_quote_for_snapshot(
    ev: Any,
    replay: dict[str, Any],
    snapshot: StateSnapshot,
    fee_config: Any,
    supply: int,
    variant: str,
) -> Any:
    config = fee_config.fees_for(snapshot.state, supply)
    fees = ev.Fees(config.protocol_bps, snapshot.creator_bps, config.lp_bps)
    if variant == "no_own_fees":
        fees = ev.Fees(0, 0, 0)
    if variant == "no_exit_impact":
        return marginal_sell_quote(
            ev, snapshot.state, replay["inventory"], fees,
            snapshot.cashback_bps, snapshot.buyback_share_bps,
        )
    return ev.quote_exact_token_sell(
        snapshot.state,
        replay["inventory"],
        0,
        fees,
        cashback_bps=snapshot.cashback_bps,
        buyback_share_bps=snapshot.buyback_share_bps,
    )


def lifecycle_outcome(
    ev: Any,
    replay: dict[str, Any],
    first_sellable_delay_ms: int,
    fee_config: Any,
    supply: int,
    variant: str = "full",
) -> dict[str, Any]:
    snapshots: list[StateSnapshot] = replay["snapshots"]
    entry_ms = snapshots[0].at_ms
    first_sellable_ms = entry_ms + first_sellable_delay_ms
    deadline_ms = entry_ms + MAX_HOLD_MS
    if any(
        snapshot.at_ms <= first_sellable_ms < snapshot.end_ms
        for snapshot in snapshots
    ):
        raise RelabelError("sellability_boundary_timing_ambiguous")
    if any(snapshot.at_ms <= deadline_ms < snapshot.end_ms for snapshot in snapshots):
        raise RelabelError("max_hold_boundary_timing_ambiguous")
    before = [snapshot for snapshot in snapshots if snapshot.end_ms <= first_sellable_ms]
    if not before:
        raise RelabelError("no_state_at_first_sellable")
    candidates = [max(before, key=lambda item: item.at_ms)] + [
        snapshot
        for snapshot in snapshots
        if first_sellable_ms < snapshot.at_ms and snapshot.end_ms <= deadline_ms
    ]
    selected: Optional[tuple[str, StateSnapshot, Any, float, int]] = None
    for index, snapshot in enumerate(candidates):
        try:
            quote = exit_quote_for_snapshot(ev, replay, snapshot, fee_config, supply, variant)
        except Exception as exc:
            if index == len(candidates) - 1:
                raise RelabelError(f"exit_quote_unavailable:{exc}") from exc
            continue
        roi = (quote.immediate_quote_out / replay["buy"].gross_quote_in - 1) * 100
        at_ms = first_sellable_ms if index == 0 else snapshot.end_ms
        if roi >= TP_PCT:
            selected = ("TP", snapshot, quote, roi, at_ms)
            break
        if roi <= SL_PCT:
            selected = ("SL", snapshot, quote, roi, at_ms)
            break
    if selected is None:
        snapshot = max(
            (item for item in snapshots if item.end_ms <= deadline_ms),
            key=lambda item: item.end_ms,
        )
        try:
            quote = exit_quote_for_snapshot(ev, replay, snapshot, fee_config, supply, variant)
        except Exception as exc:
            raise RelabelError(f"max_hold_exit_unavailable:{exc}") from exc
        roi = (quote.immediate_quote_out / replay["buy"].gross_quote_in - 1) * 100
        selected = ("TRUE_120S_MAX_HOLD", snapshot, quote, roi, deadline_ms)
    outcome, snapshot, quote, roi, exit_ms = selected
    marginal_gross = replay["inventory"] * snapshot.state.virtual_sol // snapshot.state.virtual_token
    exit_curve_impact_pct = (
        (1 - quote.gross_quote_out / marginal_gross) * 100 if marginal_gross else None
    )
    entry_fee_lamports = (
        replay["buy"].protocol_fee + replay["buy"].creator_fee + replay["buy"].lp_fee
    )
    exit_fee_lamports = quote.protocol_fee + quote.creator_fee + quote.lp_fee
    pnl_lamports = quote.immediate_quote_out - replay["buy"].gross_quote_in
    return {
        "outcome": outcome,
        "return_pct": roi,
        "pnl_lamports": pnl_lamports,
        "exit_ms": exit_ms,
        "exit_slot": snapshot.slot,
        "exit_source": snapshot.source,
        "exit_state_interval_ms": [snapshot.at_ms, snapshot.end_ms],
        "exit_state_timing_source": snapshot.timing_source,
        "exit_state_age_ms": max(0, exit_ms - snapshot.end_ms),
        "exit_gross_lamports": quote.gross_quote_out,
        "exit_net_lamports": quote.immediate_quote_out,
        "entry_fee_lamports": entry_fee_lamports,
        "exit_fee_lamports": exit_fee_lamports,
        "total_pump_fee_lamports": entry_fee_lamports + exit_fee_lamports,
        "fee_burden_pct_of_size": (entry_fee_lamports + exit_fee_lamports)
        / replay["buy"].gross_quote_in
        * 100,
        "exit_curve_impact_pct": exit_curve_impact_pct,
    }


def scenario_row(
    signal: Signal,
    chain: PreparedChain,
    size_lamports: int,
    entry_delay_ms: int,
    first_sellable_delay_ms: int,
    replay: dict[str, Any],
    outcome: dict[str, Any],
) -> dict[str, Any]:
    return {
        "signal_id": signal.signal_id,
        "population": signal.population,
        "split": signal.split,
        "tags": signal.tags,
        "mint": signal.mint,
        "decision_ms": signal.decision_ms,
        "decision_utc": utc_iso(signal.decision_ms),
        "score": signal.score,
        "state": signal.state,
        "size_lamports": size_lamports,
        "size_sol": size_lamports / 1_000_000_000,
        "entry_delay_ms": entry_delay_ms,
        "first_sellable_delay_ms": first_sellable_delay_ms,
        "entry_ms": chain.entry_ms,
        "entry_state_slot": chain.entry_slot,
        "entry_state_source": chain.entry_source,
        "entry_state_age_ms": chain.entry_state_age_ms,
        "entry_state": dataclasses.asdict(chain.entry_state),
        "tokens_received_raw": replay["inventory"],
        "entry_curve_impact_pct": replay["entry_curve_impact_pct"],
        "external_transactions": replay["external_transactions"],
        "external_status_flips": replay["external_status_flips"],
        "external_economic_changes": replay["external_economic_changes"],
        "external_status_flip_fraction": replay["external_status_flips"]
        / replay["external_transactions"]
        if replay["external_transactions"]
        else 0.0,
        "failed_transactions": sum(replay["failed_constraint_counts"].values()),
        "failed_constraint_counts": replay["failed_constraint_counts"],
        "actual_transitions_validated": chain.actual_transition_count,
        "old_outcome": signal.old_outcome,
        "old_return_pct": signal.old_return_pct,
        **outcome,
    }


def population_memberships(signal: Signal) -> list[str]:
    if signal.population == "historical":
        return [signal.split]
    return list(signal.tags)


def exclusion_fields(error: Any) -> dict[str, str]:
    detail = str(error) if error is not None else "unknown_inexact_evidence"
    reason = detail.split(":", 1)[0]
    return {"reason": reason, "reason_detail": detail}


def metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [float(row["return_pct"]) for row in rows]
    pnls = [int(row["pnl_lamports"]) / 1_000_000_000 for row in rows]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in sorted(rows, key=lambda item: (int(item["exit_ms"]), item["signal_id"])):
        equity += int(row["pnl_lamports"]) / 1_000_000_000
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    impacts_entry = [float(row["entry_curve_impact_pct"]) for row in rows]
    impacts_exit = [float(row["exit_curve_impact_pct"]) for row in rows]
    fees = [float(row["fee_burden_pct_of_size"]) for row in rows]
    ext_total = sum(int(row["external_transactions"]) for row in rows)
    ext_flips = sum(int(row["external_status_flips"]) for row in rows)
    return {
        "resolved": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": 100 * len(wins) / len(rows) if rows else None,
        "profit_factor": gross_win / gross_loss if gross_loss else (math.inf if gross_win else None),
        "expectancy_pct": statistics.fmean(returns) if returns else None,
        "median_return_pct": statistics.median(returns) if returns else None,
        "total_pnl_sol": sum(pnls),
        "max_drawdown_sol": max_drawdown,
        "tp": sum(row["outcome"] == "TP" for row in rows),
        "sl": sum(row["outcome"] == "SL" for row in rows),
        "max_hold": sum(row["outcome"] == "TRUE_120S_MAX_HOLD" for row in rows),
        "entry_impact_pct": {
            "mean": statistics.fmean(impacts_entry) if impacts_entry else None,
            "p50": percentile(impacts_entry, 0.50),
            "p75": percentile(impacts_entry, 0.75),
            "p90": percentile(impacts_entry, 0.90),
        },
        "exit_impact_pct": {
            "mean": statistics.fmean(impacts_exit) if impacts_exit else None,
            "p50": percentile(impacts_exit, 0.50),
            "p75": percentile(impacts_exit, 0.75),
            "p90": percentile(impacts_exit, 0.90),
        },
        "fee_burden_pct": {
            "mean": statistics.fmean(fees) if fees else None,
            "p50": percentile(fees, 0.50),
            "p75": percentile(fees, 0.75),
            "p90": percentile(fees, 0.90),
        },
        "external_transactions": ext_total,
        "external_status_flips": ext_flips,
        "external_status_flip_fraction": ext_flips / ext_total if ext_total else 0.0,
    }


def build_matrix(
    signals: list[Signal],
    outcomes: list[dict[str, Any]],
    exclusions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    signal_counts = Counter(
        membership for signal in signals for membership in population_memberships(signal)
    )
    exclusion_counts: Counter[tuple[str, int, int, int, str]] = Counter()
    unresolved_counts: Counter[tuple[str, int, int, int]] = Counter()
    for row in exclusions:
        for membership in row["memberships"]:
            key = (
                membership,
                int(row["size_lamports"]),
                int(row["entry_delay_ms"]),
                int(row["first_sellable_delay_ms"]),
            )
            if row["reason"] == "horizon_unresolved":
                unresolved_counts[key] += 1
            else:
                exclusion_counts[key + (row["reason"],)] += 1
    grouped: dict[tuple[str, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in outcomes:
        for membership in row["tags"] if row["population"] == "prospective" else [row["split"]]:
            grouped[
                (
                    membership,
                    int(row["size_lamports"]),
                    int(row["entry_delay_ms"]),
                    int(row["first_sellable_delay_ms"]),
                )
            ].append(row)
    matrix = []
    memberships = sorted(signal_counts)
    for membership in memberships:
        for size in SIZES_LAMPORTS:
            for entry_delay in ENTRY_DELAYS_MS:
                for sellable in FIRST_SELLABLE_DELAYS_MS:
                    key = (membership, size, entry_delay, sellable)
                    rows = grouped.get(key, [])
                    reasons = {
                        reason: count
                        for (group, row_size, row_entry, row_sellable, reason), count
                        in exclusion_counts.items()
                        if (group, row_size, row_entry, row_sellable) == key
                    }
                    matrix.append(
                        {
                            "population": membership,
                            "size_lamports": size,
                            "size_sol": size / 1_000_000_000,
                            "entry_delay_ms": entry_delay,
                            "first_sellable_delay_ms": sellable,
                            "total_signals": signal_counts[membership],
                            "exactly_evaluable": len(rows),
                            "unresolved": unresolved_counts[key],
                            "excluded_inexact": sum(reasons.values()),
                            "exclusion_reasons": reasons,
                            **metric_summary(rows),
                        }
                    )
    return matrix


def max_open_one(
    signals: list[Signal], outcomes: list[dict[str, Any]], exclusions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    outcome_map = {
        (
            row["signal_id"], int(row["size_lamports"]),
            int(row["entry_delay_ms"]), int(row["first_sellable_delay_ms"]),
        ): row
        for row in outcomes
    }
    exclusion_map = {
        (
            row["signal_id"], int(row["size_lamports"]),
            int(row["entry_delay_ms"]), int(row["first_sellable_delay_ms"]),
        ): row
        for row in exclusions
    }
    memberships = sorted(
        {membership for signal in signals for membership in population_memberships(signal)}
    )
    result = []
    for membership in memberships:
        candidates = [signal for signal in signals if membership in population_memberships(signal)]
        candidates.sort(key=lambda row: (row.decision_ms, row.mint))
        for size in SIZES_LAMPORTS:
            for entry_delay in ENTRY_DELAYS_MS:
                for sellable in FIRST_SELLABLE_DELAYS_MS:
                    free_ms = -1
                    accepted_rows = []
                    accepted = rejected = accepted_inexact = accepted_unresolved = 0
                    for signal in candidates:
                        entry_ms = signal.decision_ms + entry_delay
                        if entry_ms < free_ms:
                            rejected += 1
                            continue
                        accepted += 1
                        key = (signal.signal_id, size, entry_delay, sellable)
                        row = outcome_map.get(key)
                        if row is not None:
                            accepted_rows.append(row)
                            free_ms = int(row["exit_ms"])
                        else:
                            exclusion = exclusion_map.get(key)
                            free_ms = entry_ms + MAX_HOLD_MS
                            if exclusion and exclusion["reason"] == "horizon_unresolved":
                                accepted_unresolved += 1
                            else:
                                accepted_inexact += 1
                    result.append(
                        {
                            "population": membership,
                            "size_lamports": size,
                            "size_sol": size / 1_000_000_000,
                            "entry_delay_ms": entry_delay,
                            "first_sellable_delay_ms": sellable,
                            "total_signals": len(candidates),
                            "accepted": accepted,
                            "capacity_rejected": rejected,
                            "accepted_inexact": accepted_inexact,
                            "accepted_unresolved": accepted_unresolved,
                            **metric_summary(accepted_rows),
                        }
                    )
    return result


def old_model_comparison(
    outcomes: list[dict[str, Any]], sizes: Iterable[int] = SIZES_LAMPORTS
) -> list[dict[str, Any]]:
    result = []
    primary = [
        row for row in outcomes
        if row["entry_delay_ms"] == 25 and row["first_sellable_delay_ms"] == 1476
    ]
    memberships = sorted(
        {membership for row in primary for membership in (
            row["tags"] if row["population"] == "prospective" else [row["split"]]
        )}
    )
    for membership in memberships:
        for size in sizes:
            rows = [
                row for row in primary
                if row["size_lamports"] == size
                and membership in (row["tags"] if row["population"] == "prospective" else [row["split"]])
                and row.get("old_return_pct") is not None
            ]
            old_rows = [
                {
                    **row,
                    "return_pct": float(row["old_return_pct"]),
                    "pnl_lamports": int(round(size * float(row["old_return_pct"]) / 100)),
                    "outcome": row.get("old_outcome") or "UNKNOWN",
                }
                for row in rows
            ]
            exact = metric_summary(rows)
            old = metric_summary(old_rows)
            result.append(
                {
                    "population": membership,
                    "size_lamports": size,
                    "size_sol": size / 1_000_000_000,
                    "matched_n": len(rows),
                    "exact": exact,
                    "old_mc_proxy": old,
                    "delta_old_minus_exact": {
                        "profit_factor": (old["profit_factor"] - exact["profit_factor"])
                        if old["profit_factor"] is not None and exact["profit_factor"] is not None
                        else None,
                        "expectancy_pct": old["expectancy_pct"] - exact["expectancy_pct"]
                        if old["expectancy_pct"] is not None and exact["expectancy_pct"] is not None
                        else None,
                        "total_pnl_sol": old["total_pnl_sol"] - exact["total_pnl_sol"],
                        "win_rate_pct": old["win_rate_pct"] - exact["win_rate_pct"]
                        if old["win_rate_pct"] is not None and exact["win_rate_pct"] is not None
                        else None,
                        "tp": old["tp"] - exact["tp"],
                        "sl": old["sl"] - exact["sl"],
                        "max_hold": old["max_hold"] - exact["max_hold"],
                    },
                }
            )
    return result


def robustness(outcomes: list[dict[str, Any]], signals: list[Signal]) -> list[dict[str, Any]]:
    primary = [
        row for row in outcomes
        if row["entry_delay_ms"] == 25 and row["first_sellable_delay_ms"] == 1476
    ]
    memberships = sorted(
        {membership for signal in signals for membership in population_memberships(signal)}
    )
    signal_counts = Counter(
        membership for signal in signals for membership in population_memberships(signal)
    )
    result = []
    for membership in memberships:
        for size in SIZES_LAMPORTS:
            rows = [
                row for row in primary
                if row["size_lamports"] == size
                and membership in (row["tags"] if row["population"] == "prospective" else [row["split"]])
            ]
            ordered_winners = sorted(
                (row for row in rows if row["pnl_lamports"] > 0),
                key=lambda row: row["pnl_lamports"], reverse=True,
            )
            remove_one_pct = max(1, math.ceil(len(rows) * 0.01)) if rows else 0
            ex_top5_ids = {row["signal_id"] for row in ordered_winners[:5]}
            ex_top1_ids = {row["signal_id"] for row in ordered_winners[:remove_one_pct]}
            missing = signal_counts[membership] - len(rows)
            policy_worst = list(rows)
            full_loss_worst = list(rows)
            template = rows[0] if rows else None
            if template:
                for index in range(missing):
                    policy_worst.append(
                        {**template, "signal_id": f"worst-policy:{index}", "return_pct": -20.0,
                         "pnl_lamports": int(round(-0.20 * size)), "outcome": "SL", "exit_ms": 0}
                    )
                    full_loss_worst.append(
                        {**template, "signal_id": f"worst-full:{index}", "return_pct": -100.0,
                         "pnl_lamports": -size, "outcome": "SL", "exit_ms": 0}
                    )
            chronological = sorted(rows, key=lambda row: (row["decision_ms"], row["mint"]))
            slices = []
            for slice_index in range(4):
                start = len(chronological) * slice_index // 4
                end = len(chronological) * (slice_index + 1) // 4
                slices.append({"slice": slice_index + 1, **metric_summary(chronological[start:end])})
            result.append(
                {
                    "population": membership,
                    "size_sol": size / 1_000_000_000,
                    "baseline": metric_summary(rows),
                    "remove_top_5_winners": metric_summary(
                        [row for row in rows if row["signal_id"] not in ex_top5_ids]
                    ),
                    "remove_top_1pct_winners": metric_summary(
                        [row for row in rows if row["signal_id"] not in ex_top1_ids]
                    ),
                    "missing_or_inexact_count": missing,
                    "missing_as_policy_sl20": metric_summary(policy_worst),
                    "missing_as_total_loss": metric_summary(full_loss_worst),
                    "chronological_quartiles": slices,
                }
            )
    return result


def report_markdown(
    matrix: list[dict[str, Any]],
    max_open: list[dict[str, Any]],
    comparison: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> str:
    primary = [
        row for row in matrix
        if row["entry_delay_ms"] == 25 and row["first_sellable_delay_ms"] == 1476
    ]
    lines = [
        "# Ranker V2 Exact-Curve Relabel",
        "",
        f"Generated: {manifest['generated_utc']}",
        "",
        "## Frozen Population",
        "",
        f"Historical: {manifest['population']['historical']['rows']} earliest-per-mint signals.",
        f"Prospective snapshot: {manifest['population']['prospective']['unique_signals']} signals.",
        "",
        "## Primary 25ms Entry / 1476ms First-Sellable",
        "",
        "| Population | Size | Total | Exact | Excluded | Unresolved | WR | PF | Expectancy | PnL SOL |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in primary:
        pf = row["profit_factor"]
        lines.append(
            f"| {row['population']} | {row['size_sol']:.2f} | {row['total_signals']} | "
            f"{row['exactly_evaluable']} | {row['excluded_inexact']} | {row['unresolved']} | "
            f"{(row['win_rate_pct'] or 0):.2f}% | {(pf if pf is not None else 0):.3f} | "
            f"{(row['expectancy_pct'] or 0):.3f}% | {row['total_pnl_sol']:.4f} |"
        )
    lines += [
        "",
        "## MAX_OPEN1 Primary",
        "",
        "| Population | Size | Accepted | Rejected | Resolved | PF | Expectancy | PnL SOL |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in max_open:
        if row["entry_delay_ms"] != 25 or row["first_sellable_delay_ms"] != 1476:
            continue
        lines.append(
            f"| {row['population']} | {row['size_sol']:.2f} | {row['accepted']} | "
            f"{row['capacity_rejected']} | {row['resolved']} | "
            f"{(row['profit_factor'] if row['profit_factor'] is not None else 0):.3f} | "
            f"{(row['expectancy_pct'] or 0):.3f}% | {row['total_pnl_sol']:.4f} |"
        )
    lines += [
        "",
        "## Old Model Comparison",
        "",
        "The machine-readable comparison reports old-minus-exact PF, expectancy, PnL, WR, and outcome counts on the exact same admitted rows.",
        "",
        "## Integrity",
        "",
        f"Evaluator SHA-256: `{EXPECTED_EVALUATOR_SHA256}`",
        "",
        "No market-cap proxy fallback, no live submission, no Ranker tuning, and no threshold change were used.",
    ]
    return "\n".join(lines) + "\n"


def corpus_end_ms(path: pathlib.Path, ev: Any) -> int:
    process = subprocess.run(
        ["tail", "-n", "20", str(path)], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    values = []
    for line in process.stdout.splitlines():
        row = ev.parse_json_line_tolerant(line)
        if row and row.get("local_receive_ts_ms"):
            values.append(int(row["local_receive_ts_ms"]))
    if not values:
        raise RelabelError("cannot determine canonical corpus end")
    return max(values)


def run(args: argparse.Namespace) -> int:
    output = pathlib.Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    snapshot_now_ms = int(time.time() * 1000)
    evaluator_path = pathlib.Path(args.evaluator)
    ev = load_evaluator(evaluator_path)
    historical, historical_manifest = load_historical_signals(
        pathlib.Path(args.historical_triggers), pathlib.Path(args.historical_economics)
    )
    prospective, prospective_manifest, prospective_rows, prospective_outcomes = load_prospective_signals(
        pathlib.Path(args.prospective_signals),
        pathlib.Path(args.prospective_outcomes),
        pathlib.Path(args.n25_v2_signals),
        snapshot_now_ms,
    )
    signals = historical + prospective
    if len({signal.signal_id for signal in signals}) != len(signals):
        raise RelabelError("duplicate signal IDs")
    write_gzip_jsonl(output / "prospective-signals-source-snapshot.jsonl.gz", prospective_rows)
    write_gzip_jsonl(output / "prospective-outcomes-source-snapshot.jsonl.gz", prospective_outcomes)
    write_gzip_jsonl(output / "frozen-signals.jsonl.gz", [dataclasses.asdict(row) for row in signals])
    mints = sorted({signal.mint for signal in signals})
    observations, scan_stats = ev.scan_observations(
        pathlib.Path(args.observations), set(mints), output
    )
    corpus_end = corpus_end_ms(pathlib.Path(args.observations), ev)
    rpc_url = args.rpc_url or ev.rpc_url_from_env_file(args.env_file, args.rpc_env)
    rpc = ev.RpcClient(rpc_url, timeout=args.rpc_timeout)
    cache = RpcEvidenceCache(pathlib.Path(args.rpc_cache))
    base_rpc_stats = fetch_rpc_evidence(ev, rpc, cache, observations, mints)
    histories, history_stats = fetch_curve_histories(
        ev, rpc, cache, signals, observations
    )
    history_success_signatures = {
        row["signature"]
        for history in histories.values()
        if history["complete"]
        for row in history["rows"]
        if row["err"] is None
    }
    history_rpc_stats = fetch_rpc_evidence(
        ev, rpc, cache, observations, mints, history_success_signatures
    )
    rpc_stats = {
        "source_observations": base_rpc_stats,
        "curve_history": history_rpc_stats,
        "final_cache": cache.stats(),
    }
    augmentation_stats = augment_observations_from_curve_history(
        ev, observations, signals, histories, cache
    )
    fee_configs: dict[str, Any] = {}
    fee_config_errors: dict[str, str] = {}
    for mint in mints:
        address = ev.infer_fee_config_address(observations.get(mint, []))
        if not address:
            fee_config_errors[mint] = "fee_config_account_unavailable"
            continue
        if address not in fee_configs:
            try:
                fee_configs[address] = ev.fetch_fee_config(rpc, address)
            except Exception as exc:
                fee_config_errors[mint] = f"fee_config_fetch:{exc}"
    outcomes: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    attribution: list[dict[str, Any]] = []
    for index, signal in enumerate(signals, 1):
        mint = signal.mint
        mint_rows = observations.get(mint, [])
        history = histories[signal.signal_id]
        txs = transactions_for_signal(cache, mint_rows, history)
        supply = cache.supply(mint)
        supply_failure = cache.supply_failure(mint)
        fee_address = ev.infer_fee_config_address(mint_rows)
        base_error = fee_config_errors.get(mint)
        if not history["complete"]:
            base_error = f"curve_history_incomplete:{history.get('error')}"
        elif augmentation_stats["successful_history_transactions_missing_details"]:
            missing_for_signal = [
                row for row in history["rows"]
                if row["err"] is None and cache.transaction(row["signature"])[1] is None
            ]
            if missing_for_signal:
                base_error = "curve_history_success_transaction_detail_unavailable"
        if not mint_rows:
            base_error = "no_source_observations"
        elif supply is None:
            base_error = f"mint_supply_unavailable:{supply_failure or 'not_returned'}"
        elif fee_address not in fee_configs:
            base_error = base_error or "fee_config_unavailable"
        for entry_delay in ENTRY_DELAYS_MS:
            chain: Optional[PreparedChain] = None
            chain_error = base_error
            if chain_error is None:
                try:
                    chain = prepare_chain(
                        ev, signal, entry_delay, mint_rows, txs,
                        fee_configs[fee_address], int(supply), corpus_end,
                    )
                except Exception as exc:
                    chain_error = str(exc)
            for size in SIZES_LAMPORTS:
                replay: Optional[dict[str, Any]] = None
                replay_error = chain_error
                if replay_error is None and chain is not None:
                    try:
                        replay = replay_size(
                            ev, chain, size, fee_configs[fee_address], int(supply), variant="full"
                        )
                    except Exception as exc:
                        replay_error = f"counterfactual_replay:{exc}"
                for sellable in FIRST_SELLABLE_DELAYS_MS:
                    if replay_error is not None or replay is None or chain is None:
                        exclusions.append(
                            {
                                "signal_id": signal.signal_id,
                                "mint": mint,
                                "population": signal.population,
                                "memberships": population_memberships(signal),
                                "size_lamports": size,
                                "entry_delay_ms": entry_delay,
                                "first_sellable_delay_ms": sellable,
                                **exclusion_fields(replay_error),
                            }
                        )
                        continue
                    try:
                        result = lifecycle_outcome(
                            ev, replay, sellable, fee_configs[fee_address], int(supply)
                        )
                    except Exception as exc:
                        exclusions.append(
                            {
                                "signal_id": signal.signal_id,
                                "mint": mint,
                                "population": signal.population,
                                "memberships": population_memberships(signal),
                                "size_lamports": size,
                                "entry_delay_ms": entry_delay,
                                "first_sellable_delay_ms": sellable,
                                **exclusion_fields(f"lifecycle:{exc}"),
                            }
                        )
                    else:
                        outcomes.append(
                            scenario_row(
                                signal, chain, size, entry_delay, sellable, replay, result
                            )
                        )
                if entry_delay == 25 and chain is not None and replay_error is None:
                    variants = {}
                    for variant in (
                        "no_entry_impact", "no_exit_impact", "no_own_fees", "force_external_success"
                    ):
                        try:
                            variant_replay = replay_size(
                                ev, chain, size, fee_configs[fee_address], int(supply), variant=variant
                            )
                            variant_result = lifecycle_outcome(
                                ev, variant_replay, 1476, fee_configs[fee_address], int(supply), variant
                            )
                            variants[variant] = {
                                "return_pct": variant_result["return_pct"],
                                "pnl_lamports": variant_result["pnl_lamports"],
                                "outcome": variant_result["outcome"],
                            }
                        except Exception as exc:
                            variants[variant] = {"error": str(exc)}
                    attribution.append(
                        {
                            "signal_id": signal.signal_id,
                            "mint": mint,
                            "memberships": population_memberships(signal),
                            "size_lamports": size,
                            "variants": variants,
                        }
                    )
        if index % 100 == 0 or index == len(signals):
            print(
                f"relabel signals {index}/{len(signals)} outcomes={len(outcomes)} "
                f"exclusions={len(exclusions)}",
                flush=True,
            )
    matrix = build_matrix(signals, outcomes, exclusions)
    max_open = max_open_one(signals, outcomes, exclusions)
    comparison = old_model_comparison(outcomes)
    robust = robustness(outcomes, signals)
    attribution_summary = summarize_attribution(outcomes, attribution)
    manifest = {
        "classification": "RANKER_V2_EXACT_CURVE_RELABEL_COMPLETE",
        "generated_utc": utc_iso(snapshot_now_ms),
        "snapshot_now_ms": snapshot_now_ms,
        "evaluator": "EXACT_CURVE_ECONOMICS_V1",
        "evaluator_path": str(evaluator_path),
        "evaluator_sha256": EXPECTED_EVALUATOR_SHA256,
        "threshold": THRESHOLD,
        "entry_delays_ms": ENTRY_DELAYS_MS,
        "first_sellable_delays_ms": FIRST_SELLABLE_DELAYS_MS,
        "sizes_lamports": SIZES_LAMPORTS,
        "tp_pct": TP_PCT,
        "sl_pct": SL_PCT,
        "max_hold_ms": MAX_HOLD_MS,
        "population": {
            "historical": historical_manifest,
            "prospective": prospective_manifest,
            "total_signals": len(signals),
            "unique_mints": len(mints),
        },
        "corpus": {
            "path": args.observations,
            "end_ms": corpus_end,
            "end_utc": utc_iso(corpus_end),
            "scan": scan_stats,
        },
        "rpc_evidence": rpc_stats,
        "curve_history": history_stats,
        "curve_history_augmentation": augmentation_stats,
        "no_live_changes": True,
        "no_market_cap_fallback": True,
    }
    write_gzip_jsonl(output / "exact-outcomes.jsonl.gz", outcomes)
    write_gzip_jsonl(output / "exclusions.jsonl.gz", exclusions)
    write_gzip_jsonl(output / "capacity-attribution-rows.jsonl.gz", attribution)
    write_json(output / "matrix-all-signals.json", matrix)
    write_json(output / "matrix-max-open1.json", max_open)
    write_json(output / "old-model-comparison.json", comparison)
    write_json(output / "robustness.json", robust)
    write_json(output / "capacity-attribution.json", attribution_summary)
    write_json(output / "manifest.json", manifest)
    (output / "report.md").write_text(
        report_markdown(matrix, max_open, comparison, manifest), encoding="utf-8"
    )
    source_copy = output / "rankerV2ExactCurveRelabel.py"
    source_copy.write_bytes(pathlib.Path(__file__).read_bytes())
    print(json.dumps({"manifest": manifest, "primary": [
        row for row in matrix
        if row["entry_delay_ms"] == 25 and row["first_sellable_delay_ms"] == 1476
    ]}, indent=2))
    return 0


def summarize_attribution(
    outcomes: list[dict[str, Any]], attribution: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    primary = {
        (row["signal_id"], row["size_lamports"]): row
        for row in outcomes
        if row["entry_delay_ms"] == 25 and row["first_sellable_delay_ms"] == 1476
    }
    result = []
    memberships = sorted({membership for row in attribution for membership in row["memberships"]})
    for membership in memberships:
        for size in SIZES_LAMPORTS:
            rows = [
                row for row in attribution
                if row["size_lamports"] == size and membership in row["memberships"]
                and (row["signal_id"], size) in primary
            ]
            baseline = [primary[(row["signal_id"], size)] for row in rows]
            variants = {}
            for variant in (
                "no_entry_impact", "no_exit_impact", "no_own_fees", "force_external_success"
            ):
                variant_rows = []
                for row, base in zip(rows, baseline):
                    value = row["variants"].get(variant) or {}
                    if value.get("return_pct") is None:
                        continue
                    variant_rows.append(
                        {
                            **base,
                            "return_pct": value["return_pct"],
                            "pnl_lamports": value["pnl_lamports"],
                            "outcome": value["outcome"],
                        }
                    )
                variant_metrics = metric_summary(variant_rows)
                base_matched = [
                    primary[(row["signal_id"], size)] for row in rows
                    if (row["variants"].get(variant) or {}).get("return_pct") is not None
                ]
                base_metrics = metric_summary(base_matched)
                variants[variant] = {
                    "baseline_matched": base_metrics,
                    "ablation": variant_metrics,
                    "ablation_minus_full": {
                        "expectancy_pct": variant_metrics["expectancy_pct"] - base_metrics["expectancy_pct"]
                        if variant_metrics["expectancy_pct"] is not None and base_metrics["expectancy_pct"] is not None
                        else None,
                        "profit_factor": variant_metrics["profit_factor"] - base_metrics["profit_factor"]
                        if variant_metrics["profit_factor"] is not None and base_metrics["profit_factor"] is not None
                        else None,
                        "total_pnl_sol": variant_metrics["total_pnl_sol"] - base_metrics["total_pnl_sol"],
                    },
                }
            result.append(
                {
                    "population": membership,
                    "size_sol": size / 1_000_000_000,
                    "note": "Ablations are nonadditive; each delta is versus the same full exact baseline.",
                    "variants": variants,
                }
            )
    return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--evaluator", required=True)
    value.add_argument("--historical-triggers", required=True)
    value.add_argument("--historical-economics", required=True)
    value.add_argument("--prospective-signals", required=True)
    value.add_argument("--prospective-outcomes", required=True)
    value.add_argument("--n25-v2-signals", required=True)
    value.add_argument("--observations", required=True)
    value.add_argument("--output-dir", required=True)
    value.add_argument("--rpc-cache", required=True)
    value.add_argument("--rpc-url")
    value.add_argument("--env-file", default="/root/bot/FinalBot-cya/.env")
    value.add_argument("--rpc-env", default="SECURE_RPC_URL")
    value.add_argument("--rpc-timeout", type=float, default=30.0)
    return value


def main() -> int:
    try:
        return run(parser().parse_args())
    except (RelabelError, Exception) as exc:
        print(f"RANKER_V2_EXACT_CURVE_RELABEL_BLOCKER: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
