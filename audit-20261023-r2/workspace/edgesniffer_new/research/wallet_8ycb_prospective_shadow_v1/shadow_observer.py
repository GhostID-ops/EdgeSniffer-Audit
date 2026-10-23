#!/usr/bin/env python3
"""Prospective 8YCB observation-only cohort recorder and curve counterfactual scorer."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import signal
import statistics
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import resource
except ImportError:  # pragma: no cover - Linux service always provides resource.
    resource = None


TARGET = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ"
GUARD = "8YCB_OBSERVATION_ONLY_V1"
RUNNING = True


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def iso_ms(value: float | int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    encoded = (json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o640)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def quantile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    return ordered[round((len(ordered) - 1) * fraction)] if ordered else None


def finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def buy_tokens(vsol: int, vtok: int, gross: int) -> int:
    return max(0, vtok - (vsol * vtok) // (vsol + gross)) if gross > 0 else 0


def apply_buy(vsol: int, vtok: int, gross: int, fee_bps: int) -> tuple[int, int, int]:
    tokens = buy_tokens(vsol, vtok, gross)
    net = gross - gross * fee_bps // 10_000
    return vsol + net, vtok - tokens, tokens


def sell_value(vsol: int, vtok: int, tokens: int, fee_bps: int) -> int:
    if tokens <= 0:
        return 0
    gross = max(0, vsol - (vsol * vtok) // (vtok + tokens))
    return gross - gross * fee_bps // 10_000


def apply_sell(vsol: int, vtok: int, tokens: int) -> tuple[int, int]:
    return ((vsol * vtok) // (vtok + tokens), vtok + tokens) if tokens > 0 else (vsol, vtok)


def economics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    cumulative = peak = max_drawdown = 0.0
    for value in values:
        cumulative += value * 0.001
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    return {
        "n": len(values),
        "wins": sum(value > 0 for value in values),
        "win_rate": sum(value > 0 for value in values) / len(values) if values else None,
        "profit_factor": finite(gains / losses) if losses else None,
        "expectancy_pct": statistics.mean(values) if values else None,
        "median_return_pct": statistics.median(values) if values else None,
        "normalized_pnl_sol_at_0p1": sum(values) * 0.001,
        "maximum_drawdown_sol_at_0p1": max_drawdown,
    }


class FollowFile:
    def __init__(self, path: Path, position: int | None):
        self.path = path
        if not self.path.exists():
            self.path.touch()
        self.handle = self.path.open("r", encoding="utf-8", errors="replace")
        size = self.path.stat().st_size
        self.position = min(position, size) if position is not None else size
        self.handle.seek(self.position)

    def read_available(self, maximum: int = 1000) -> list[tuple[str, int]]:
        rows = []
        for _ in range(maximum):
            before = self.handle.tell()
            line = self.handle.readline()
            if not line:
                self.handle.seek(before)
                break
            self.position = self.handle.tell()
            rows.append((line, time.time_ns()))
        return rows


class Shadow:
    def __init__(self, spec_path: Path, output: Path, global_feed: Path, wallet_feed: Path):
        if os.environ.get("NO_SUBMIT_HARD_GUARD") != GUARD:
            raise RuntimeError("SHADOW_SAFETY_BLOCKER: hard guard mismatch")
        self.spec_path = spec_path
        self.spec = json.loads(spec_path.read_text(encoding="utf-8"))
        if self.spec.get("mode") != "OBSERVATION_ONLY_NO_SUBMIT" or self.spec.get("transaction_construction") != "OMITTED_V1":
            raise RuntimeError("SHADOW_SAFETY_BLOCKER: spec is not observation-only")
        self.spec_hash = canonical_sha(self.spec)
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        published_spec = output / "frozen_spec.json"
        if published_spec.exists() and canonical_sha(json.loads(published_spec.read_text(encoding="utf-8"))) != self.spec_hash:
            raise RuntimeError("published frozen_spec.json differs from canonical installed spec")
        if not published_spec.exists():
            atomic_json(published_spec, self.spec)
        manifest_path = output / "cohort_manifest.json"
        if manifest_path.exists():
            prior_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if prior_manifest.get("frozen_spec_sha256") != self.spec_hash:
                raise RuntimeError("cohort manifest frozen-spec mismatch")
        else:
            atomic_json(manifest_path, {
                "schema": "8ycb-prospective-cohort-manifest-v1",
                "cohort_start_utc": self.spec["cohort_start_utc"],
                "frozen_spec_sha256": self.spec_hash,
                "historical_events_allowed": False,
                "mode": "OBSERVATION_ONLY_NO_SUBMIT",
                "classification_before_n100": "NOT_ALPHA_PROOF",
            })
        self.ledger = output / "cohort_ledger.jsonl"
        self.state_path = output / "shadow_state.json"
        self.cohort_start_ms = parse_utc(self.spec["cohort_start_utc"])
        self.recurrent = self.load_recurrent(spec_path.parent / "v3_recurrent_wallets.json")
        self.seen_record_ids: set[str] = set()
        self.seen_event_ids: set[str] = set()
        self.pending_creates: dict[str, dict[str, Any]] = {}
        self.pending_atomic: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.active: dict[str, dict[str, Any]] = {}
        self.target_selectors: dict[str, dict[str, Any]] = {}
        self.outcomes: list[dict[str, Any]] = []
        self.signal_rows: list[dict[str, Any]] = []
        self.generic_rows: list[dict[str, Any]] = []
        self.excluded_control_ids: set[str] = set()
        self.external_rows: list[dict[str, Any]] = []
        self.collision_rows: list[dict[str, Any]] = []
        self.checkpoint_rows: list[dict[str, Any]] = []
        self.decoder_errors = 0
        self.dropped_rows = 0
        self.start_monotonic = time.monotonic()
        self.started_at = utc_now()
        self.last_signal: dict[str, Any] | None = None
        state = self.load_existing()
        self.global_tail = FollowFile(global_feed, state.get("global_offset"))
        self.wallet_tail = FollowFile(wallet_feed, state.get("wallet_offset"))
        self.last_state_write = self.last_report_write = self.last_manifest_write = 0.0

    def load_recurrent(self, path: Path) -> set[str]:
        value = json.loads(path.read_text(encoding="utf-8"))
        expected = self.spec["recurrent_wallet_definition"]["source_sha256"]
        if file_sha(path) != expected:
            raise RuntimeError("frozen recurrent-wallet file hash mismatch")
        return {
            row["wallet"] for row in value.get("top_repeated_wallets", [])
            if int(row.get("target_launches_bought_within_1s", 0)) >= 5
        }

    def load_existing(self) -> dict[str, Any]:
        state = {}
        if self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                if state.get("frozen_spec_sha256") != self.spec_hash:
                    raise RuntimeError("frozen spec hash differs from prior cohort state")
                self.pending_creates = state.get("pending_creates", {})
                self.pending_atomic = defaultdict(list, state.get("pending_atomic", {}))
            except json.JSONDecodeError:
                state = {}
        if not self.ledger.exists():
            return state
        with self.ledger.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = row.get("record_id")
                if rid:
                    self.seen_record_ids.add(rid)
                kind = row.get("kind")
                event_id = row.get("event_id")
                if kind == "signal":
                    self.signal_rows.append(row)
                    self.seen_event_ids.add(event_id)
                    self.active[event_id] = self.active_from_signal(row)
                    self.target_selectors[event_id] = {"selected": [], "deadline_ms": row["timing"]["signal_ready_unix_ns"] / 1e6 + 60_000}
                    self.last_signal = row
                elif kind == "generic_control":
                    self.generic_rows.append(row)
                    self.active[event_id] = self.active_from_control(row)
                    selector = self.target_selectors.get(row["matched_target_event_id"])
                    if selector is not None:
                        selector["selected"].append(event_id)
                elif kind == "generic_control_excluded":
                    self.excluded_control_ids.add(event_id)
                    self.active.pop(event_id, None)
                    selector = self.target_selectors.get(row.get("matched_target_event_id"))
                    if selector is not None and event_id in selector["selected"]:
                        selector["selected"].remove(event_id)
                elif kind == "control_atomic" and event_id in self.active:
                    self.active[event_id]["atomic_quote_lamports"] = int(row["atomic_quote_lamports"])
                    if row.get("atomic_tokens_raw"):
                        self.active[event_id]["atomic_tokens_raw"] = int(row["atomic_tokens_raw"])
                elif kind == "signal_enrichment":
                    event = self.active.get(event_id)
                    signal = next((item for item in self.signal_rows if item["event_id"] == event_id), None)
                    if event is not None:
                        event["atomic_quote_lamports"] = int(row["atomic_initial_buy"]["quote_lamports"])
                        event["atomic_tokens_raw"] = row["atomic_initial_buy"].get("tokens_out_raw")
                    if signal is not None:
                        signal["atomic_initial_buy"] = dict(row["atomic_initial_buy"])
                        if row.get("chain_or_block_timestamp_ms") is not None:
                            signal["timing"]["chain_or_block_timestamp_ms"] = row["chain_or_block_timestamp_ms"]
                elif kind == "flow" and event_id in self.active:
                    self.active[event_id]["flows"].append(row["flow"])
                elif kind == "outcome":
                    self.outcomes.append(row)
                    self.active.pop(event_id, None)
                elif kind == "external_flow":
                    self.external_rows.append(row)
                elif kind == "target_sell_collision":
                    self.collision_rows.append(row)
                elif kind == "checkpoint_snapshot":
                    self.checkpoint_rows.append(row)
        return state

    def active_from_signal(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": row["event_id"], "cohort": "TARGET", "mint": row["mint"],
            "signature": row["signature"], "receive_ms": row["timing"]["earliest_feed_receive_ms"],
            "signal_ready_ms": row["timing"]["signal_ready_unix_ns"] / 1e6,
            "atomic_quote_lamports": row["atomic_initial_buy"]["quote_lamports"],
            "atomic_tokens_raw": row["atomic_initial_buy"].get("tokens_out_raw"), "flows": [],
            "matched_target_event_id": None,
        }

    def active_from_control(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": row["event_id"], "cohort": "GENERIC_CONTROL", "mint": row["mint"],
            "signature": row["signature"], "receive_ms": row["feed_receive_ms"],
            "signal_ready_ms": row["control_selected_unix_ns"] / 1e6,
            "atomic_quote_lamports": 0, "atomic_tokens_raw": None, "flows": [],
            "matched_target_event_id": row["matched_target_event_id"],
        }

    def append(self, row: dict[str, Any]) -> bool:
        rid = row.setdefault("record_id", canonical_sha(row))
        if rid in self.seen_record_ids:
            return False
        append_jsonl(self.ledger, row)
        self.seen_record_ids.add(rid)
        return True

    def process_global(self, raw: str, line_unix_ns: int) -> None:
        decode_start = time.time_ns()
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            self.decoder_errors += 1
            return
        decode_end = time.time_ns()
        receive_ms = row.get("local_receive_ts_ms")
        if not isinstance(receive_ms, (int, float)) or receive_ms < self.cohort_start_ms:
            return
        signature = row.get("transaction_signature")
        mint = row.get("mint")
        side = str(row.get("side") or "").lower()
        if not signature or not mint:
            return
        if side == "create" and row.get("creation_proven") is True:
            create = {
                "signature": signature, "mint": mint, "receive_ms": receive_ms,
                "decode_complete_ms": row.get("decode_complete_ts_ms"),
                "source_slot": row.get("source_slot"), "source_transaction_index": row.get("source_transaction_index"),
                "line_read_unix_ns": line_unix_ns, "local_decode_start_unix_ns": decode_start,
                "local_decode_complete_unix_ns": decode_end, "raw_observation_id": row.get("observation_id"),
            }
            self.pending_creates[signature] = create
            for atomic in self.pending_atomic.pop(signature, []):
                if self.atomic_matches(create, atomic):
                    self.register_signal(create, atomic)
                    break
            self.assign_generic(create, line_unix_ns)
            self.prune_pending(receive_ms)
            return
        flow = self.global_flow(row)
        if flow:
            if side == "buy" and row.get("wallet") == TARGET:
                atomic = {
                    **flow, "source": "normalized_jito_shredstream", "line_read_unix_ns": line_unix_ns,
                    "local_decode_start_unix_ns": decode_start, "local_decode_complete_unix_ns": decode_end,
                    "decode_complete_ms": row.get("decode_complete_ts_ms"),
                }
                create = self.pending_creates.get(signature)
                if create and self.atomic_matches(create, atomic):
                    self.register_signal(create, atomic)
                else:
                    self.pending_atomic[signature].append(atomic)
            self.add_flow_to_active(flow)

    def process_wallet(self, raw: str, line_unix_ns: int) -> None:
        decode_start = time.time_ns()
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            self.decoder_errors += 1
            return
        decode_end = time.time_ns()
        if row.get("kind") != "wallet_transaction_observation":
            return
        received = row.get("providerReceivedAtMs")
        if not isinstance(received, (int, float)) or received < self.cohort_start_ms:
            return
        for swap in row.get("localSwaps") or []:
            if swap.get("wallet") != TARGET or swap.get("mint") is None:
                continue
            side = str(swap.get("side") or "").lower()
            flow = {
                "flow_id": canonical_sha({"provider": row.get("provider"), "signature": row.get("signature"), "wallet": TARGET, "mint": swap["mint"], "side": side}),
                "ts_ms": received, "slot": row.get("slot"), "tx_index": None, "ix_index": None,
                "signature": row.get("signature"), "mint": swap["mint"], "side": side, "wallet": TARGET,
                "quote_lamports": int(round(float(swap.get("sourceBuySol") or 0) * 1e9)),
                "sell_token_raw": int(swap.get("sourceSellTokensRaw") or 0) if side == "sell" else 0,
                "tokens_out_raw": int(swap.get("tokensOutRaw") or 0) if side == "buy" else None,
                "chain_or_block_timestamp_ms": float(swap["timestamp"]) * 1000 if swap.get("timestamp") is not None else None,
                "source": f"wallet_filtered_{row.get('provider')}",
            }
            if side == "buy":
                atomic = {
                    **flow, "source_ix_accounts": swap.get("sourceIxAccounts"),
                    "source_ix_data_base64": swap.get("sourceIxDataBase64"),
                    "line_read_unix_ns": line_unix_ns, "local_decode_start_unix_ns": decode_start,
                    "local_decode_complete_unix_ns": decode_end,
                    "stream_callback_unix_ns": int(row["callbackUnixNs"]) if row.get("callbackUnixNs") else None,
                    "stream_callback_monotonic_ns": int(row["callbackMonotonicNs"]) if row.get("callbackMonotonicNs") else None,
                    "decode_complete_ms": int(row["callbackUnixNs"]) / 1e6 if row.get("callbackUnixNs") else received,
                }
                create = self.pending_creates.get(row.get("signature"))
                if create and self.atomic_matches(create, atomic):
                    self.register_signal(create, atomic)
                else:
                    self.pending_atomic[row.get("signature")].append(atomic)
            self.add_flow_to_active(flow)

    def global_flow(self, row: dict[str, Any]) -> dict[str, Any] | None:
        side = str(row.get("side") or "").lower()
        if side not in {"buy", "sell"}:
            return None
        quote = int((row.get("quote_lamports") if side == "buy" else row.get("sell_quote_lamports")) or 0)
        tokens = int(row.get("sell_token_amount_raw") or 0)
        if side == "buy" and quote <= 0 or side == "sell" and tokens <= 0:
            return None
        return {
            "flow_id": canonical_sha({"observation_id": row.get("observation_id"), "signature": row.get("transaction_signature"), "wallet": row.get("wallet"), "side": side, "mint": row.get("mint")}),
            "ts_ms": row["local_receive_ts_ms"], "slot": row.get("source_slot"),
            "tx_index": row.get("source_transaction_index"), "ix_index": (row.get("raw_record") or {}).get("instructionIndex"),
            "signature": row.get("transaction_signature"), "mint": row.get("mint"), "side": side,
            "wallet": row.get("wallet"), "quote_lamports": quote, "sell_token_raw": tokens,
            "tokens_out_raw": None, "source": "normalized_jito_shredstream",
        }

    def atomic_matches(self, create: dict[str, Any], atomic: dict[str, Any]) -> bool:
        if atomic.get("wallet") != TARGET or atomic.get("side") != "buy" or atomic.get("quote_lamports", 0) <= 0:
            return False
        if create["signature"] != atomic.get("signature") or create["mint"] != atomic.get("mint"):
            return False
        if create.get("source_slot") is not None and atomic.get("slot") is not None and create["source_slot"] != atomic["slot"]:
            return False
        if create.get("source_transaction_index") is not None and atomic.get("tx_index") is not None and create["source_transaction_index"] != atomic["tx_index"]:
            return False
        return True

    def register_signal(self, create: dict[str, Any], atomic: dict[str, Any]) -> None:
        event_id = "8ycb-v1-" + hashlib.sha256(f"{create['signature']}:{create['mint']}".encode()).hexdigest()[:24]
        if event_id in self.seen_event_ids:
            self.enrich_signal(event_id, atomic)
            return
        ready_ns = time.time_ns()
        earliest = min(float(create["receive_ms"]), float(atomic["ts_ms"]))
        decoded_create_ms = create.get("decode_complete_ms") or create["local_decode_complete_unix_ns"] / 1e6
        decoded_atomic_ms = atomic.get("decode_complete_ms") or atomic["local_decode_complete_unix_ns"] / 1e6
        row = {
            "schema": "8ycb-prospective-signal-v1", "kind": "signal", "event_id": event_id,
            "cohort_start_utc": self.spec["cohort_start_utc"], "frozen_spec_sha256": self.spec_hash,
            "mint": create["mint"], "signature": create["signature"], "target_wallet": TARGET,
            "source_slot": create.get("source_slot") or atomic.get("slot"),
            "source_transaction_index": create.get("source_transaction_index") or atomic.get("tx_index"),
            "timing": {
                "chain_or_block_timestamp_ms": atomic.get("chain_or_block_timestamp_ms"),
                "earliest_feed_receive_ms": earliest,
                "earliest_feed_receive_utc": iso_ms(earliest),
                "decoded_create_ms": decoded_create_ms,
                "decoded_atomic_buy_ms": decoded_atomic_ms,
                "signal_ready_unix_ns": ready_ns,
                "signal_ready_utc": iso_ms(ready_ns / 1e6),
                "create_to_feed_receive_ms": None,
                "feed_receive_to_create_decode_ms": float(decoded_create_ms) - earliest,
                "feed_receive_to_atomic_decode_ms": float(decoded_atomic_ms) - earliest,
                "decode_to_signal_ready_ms": ready_ns / 1e6 - max(float(decoded_create_ms), float(decoded_atomic_ms)),
                "total_observable_detection_ms": ready_ns / 1e6 - earliest,
                "timestamp_semantics": "upstream application SystemTime milliseconds plus local observer wall-clock nanoseconds; no kernel timestamp available",
                "observer_create_line_read_unix_ns": create["line_read_unix_ns"],
                "observer_create_decode_start_unix_ns": create["local_decode_start_unix_ns"],
                "observer_create_decode_complete_unix_ns": create["local_decode_complete_unix_ns"],
                "observer_atomic_line_read_unix_ns": atomic["line_read_unix_ns"],
                "observer_atomic_decode_start_unix_ns": atomic["local_decode_start_unix_ns"],
                "observer_atomic_decode_complete_unix_ns": atomic["local_decode_complete_unix_ns"],
                "wallet_stream_callback_unix_ns": atomic.get("stream_callback_unix_ns"),
                "wallet_stream_callback_monotonic_ns": atomic.get("stream_callback_monotonic_ns"),
            },
            "atomic_initial_buy": {
                "quote_lamports": atomic["quote_lamports"],
                "tokens_out_raw": atomic.get("tokens_out_raw"),
                "source": atomic["source"],
            },
            "event_definition_result": "CREATE_AND_SAME_TRANSACTION_8YCB_ATOMIC_BUY_PROVEN",
            "transaction_construction": "OMITTED_V1",
        }
        if not self.append(row):
            return
        self.signal_rows.append(row)
        self.seen_event_ids.add(event_id)
        event = self.active_from_signal(row)
        self.active[event_id] = event
        self.target_selectors[event_id] = {"selected": [], "deadline_ms": ready_ns / 1e6 + 60_000}
        self.last_signal = row
        # Keep the CREATE in the bounded pending cache so a later observation of
        # the same atomic buy can enrich token allocation/block-time telemetry.
        self.exclude_target_from_controls(create, event_id)

    def enrich_signal(self, event_id: str, atomic: dict[str, Any]) -> None:
        signal = next((row for row in self.signal_rows if row["event_id"] == event_id), None)
        event = self.active.get(event_id)
        if signal is None:
            return
        current = signal["atomic_initial_buy"]
        tokens = current.get("tokens_out_raw") or atomic.get("tokens_out_raw")
        prefer_new_quote = current.get("source") != "normalized_jito_shredstream" and atomic.get("source") == "normalized_jito_shredstream"
        quote = int(atomic["quote_lamports"] if prefer_new_quote else current["quote_lamports"])
        source = atomic["source"] if prefer_new_quote else current["source"]
        chain_ms = signal["timing"].get("chain_or_block_timestamp_ms") or atomic.get("chain_or_block_timestamp_ms")
        updated = {"quote_lamports": quote, "tokens_out_raw": int(tokens) if tokens else None, "source": source}
        if updated == current and chain_ms == signal["timing"].get("chain_or_block_timestamp_ms"):
            return
        row = {
            "schema": "8ycb-prospective-signal-enrichment-v1", "kind": "signal_enrichment",
            "event_id": event_id, "at": utc_now(), "atomic_initial_buy": updated,
            "chain_or_block_timestamp_ms": chain_ms, "enrichment_source": atomic.get("source"),
        }
        self.append(row)
        signal["atomic_initial_buy"] = updated
        signal["timing"]["chain_or_block_timestamp_ms"] = chain_ms
        if event is not None:
            event["atomic_quote_lamports"] = quote
            event["atomic_tokens_raw"] = updated["tokens_out_raw"]

    def exclude_target_from_controls(self, create: dict[str, Any], target_event_id: str) -> None:
        for control in self.generic_rows:
            control_id = control["event_id"]
            if control_id in self.excluded_control_ids:
                continue
            if control["signature"] != create["signature"] and control["mint"] != create["mint"]:
                continue
            row = {
                "schema": "8ycb-prospective-generic-control-exclusion-v1",
                "kind": "generic_control_excluded", "event_id": control_id,
                "matched_target_event_id": control["matched_target_event_id"],
                "discovered_target_event_id": target_event_id, "at": utc_now(),
                "reason": "CREATE later proven to be a qualifying 8YCB target event",
            }
            self.append(row)
            self.excluded_control_ids.add(control_id)
            self.active.pop(control_id, None)
            selector = self.target_selectors.get(control["matched_target_event_id"])
            if selector is not None and control_id in selector["selected"]:
                selector["selected"].remove(control_id)

    def assign_generic(self, create: dict[str, Any], line_unix_ns: int) -> None:
        if create["mint"] in {event["mint"] for event in self.active.values() if event["cohort"] == "TARGET"}:
            return
        if any(self.atomic_matches(create, atomic) for atomic in self.pending_atomic.get(create["signature"], [])):
            return
        for target_id, selector in list(self.target_selectors.items()):
            if len(selector["selected"]) >= int(self.spec["generic_control_selection"]["controls_per_target"]):
                continue
            if line_unix_ns / 1e6 > selector["deadline_ms"]:
                continue
            target = self.active.get(target_id)
            if not target or line_unix_ns / 1e6 < target["signal_ready_ms"]:
                continue
            event_id = "control-v1-" + hashlib.sha256(f"{target_id}:{create['signature']}:{create['mint']}".encode()).hexdigest()[:24]
            row = {
                "schema": "8ycb-prospective-generic-control-v1", "kind": "generic_control",
                "event_id": event_id, "matched_target_event_id": target_id, "mint": create["mint"],
                "signature": create["signature"], "feed_receive_ms": create["receive_ms"],
                "control_selected_unix_ns": line_unix_ns, "source_slot": create.get("source_slot"),
                "selection_ordinal": len(selector["selected"]) + 1, "selection_rule": self.spec["generic_control_selection"]["rule"],
                "frozen_spec_sha256": self.spec_hash,
            }
            if self.append(row):
                self.generic_rows.append(row)
                selector["selected"].append(event_id)
                self.active[event_id] = self.active_from_control(row)

    def add_flow_to_active(self, flow: dict[str, Any]) -> None:
        for event in list(self.active.values()):
            if event["mint"] != flow["mint"] or flow["signature"] == event["signature"]:
                if event["mint"] == flow["mint"] and flow["signature"] == event["signature"] and event["cohort"] == "GENERIC_CONTROL" and flow["side"] == "buy":
                    quote = max(event["atomic_quote_lamports"], flow["quote_lamports"])
                    if quote > event["atomic_quote_lamports"] or (not event.get("atomic_tokens_raw") and flow.get("tokens_out_raw")):
                        record = {
                            "schema": "8ycb-prospective-control-atomic-v1", "kind": "control_atomic",
                            "event_id": event["event_id"], "atomic_quote_lamports": quote,
                            "atomic_tokens_raw": flow.get("tokens_out_raw"), "source": flow.get("source"),
                        }
                        self.append(record)
                        event["atomic_quote_lamports"] = quote
                        if flow.get("tokens_out_raw"):
                            event["atomic_tokens_raw"] = flow["tokens_out_raw"]
                continue
            if flow["ts_ms"] < event["receive_ms"] - 5 or flow["ts_ms"] > event["receive_ms"] + 122_000:
                continue
            if flow.get("wallet") == TARGET and any(
                old.get("signature") == flow.get("signature")
                and old.get("wallet") == TARGET
                and old.get("side") == flow.get("side")
                for old in event["flows"]
            ):
                continue
            record = {"schema": "8ycb-prospective-flow-v1", "kind": "flow", "event_id": event["event_id"], "flow": flow}
            if self.append(record):
                event["flows"].append(flow)

    def prune_pending(self, now_ms: float) -> None:
        cutoff = now_ms - 600_000
        self.pending_creates = {key: value for key, value in self.pending_creates.items() if value["receive_ms"] >= cutoff}
        self.pending_atomic = defaultdict(list, {key: values for key, values in self.pending_atomic.items() if any(value["ts_ms"] >= cutoff for value in values)})

    def replay_state(self, event: dict[str, Any], until_ms: float) -> tuple[int, int]:
        model = self.spec["curve_model"]
        state = (int(model["initial_virtual_sol_reserves"]), int(model["initial_virtual_token_reserves"]))
        fee = int(model["pump_fee_bps_each_side"])
        if event["atomic_quote_lamports"] > 0:
            state = apply_buy(*state, int(event["atomic_quote_lamports"]), fee)[:2]
        for flow in sorted(event["flows"], key=self.flow_order):
            if flow["ts_ms"] > until_ms:
                break
            state = self.apply_flow(state, flow)
        return state

    def first_external_buy_price(self, event: dict[str, Any]) -> float | None:
        model = self.spec["curve_model"]
        fee = int(model["pump_fee_bps_each_side"])
        state = (int(model["initial_virtual_sol_reserves"]), int(model["initial_virtual_token_reserves"]))
        if event["atomic_quote_lamports"] > 0:
            state = apply_buy(*state, int(event["atomic_quote_lamports"]), fee)[:2]
        for flow in sorted(event["flows"], key=self.flow_order):
            if flow["side"] == "buy" and flow.get("wallet") != TARGET:
                tokens = buy_tokens(*state, int(flow["quote_lamports"]))
                return flow["quote_lamports"] / 1e9 / (tokens / 1e6) if tokens > 0 else None
            state = self.apply_flow(state, flow)
        return None

    @staticmethod
    def flow_order(flow: dict[str, Any]) -> tuple:
        return (flow.get("ts_ms") or 0, flow.get("slot") or 0, flow.get("tx_index") or 0, flow.get("ix_index") or 0, flow.get("signature") or "")

    def apply_flow(self, state: tuple[int, int], flow: dict[str, Any]) -> tuple[int, int]:
        fee = int(self.spec["curve_model"]["pump_fee_bps_each_side"])
        if flow["side"] == "buy":
            return apply_buy(*state, int(flow["quote_lamports"]), fee)[:2]
        return apply_sell(*state, int(flow["sell_token_raw"]))

    def simulate(self, event: dict[str, Any], lane: str, entry_ms: float, sellable_delay: int, worse: bool) -> dict[str, Any]:
        fee = int(self.spec["curve_model"]["pump_fee_bps_each_side"])
        principal = int(self.spec["position_size_lamports"])
        state = self.replay_state(event, entry_ms)
        entry_curve_state = {"virtual_sol_reserves": state[0], "virtual_token_reserves": state[1]}
        state_after = apply_buy(*state, principal, fee)
        state = state_after[:2]
        tokens = max(1, int(state_after[2] / (1.05 if worse else 1.0)))
        sellable_ms = entry_ms + sellable_delay
        timeout_ms = entry_ms + int(self.spec["exit_policy"]["maximum_hold_ms"])
        future = [flow for flow in sorted(event["flows"], key=self.flow_order) if entry_ms < flow["ts_ms"] <= timeout_ms]
        index = 0
        while index < len(future) and future[index]["ts_ms"] <= sellable_ms:
            state = self.apply_flow(state, future[index]); index += 1
        roi = 100 * (sell_value(*state, tokens, fee) / principal - 1)
        exit_ms, reason = timeout_ms, "TIMEOUT_120S"
        tp = float(self.spec["exit_policy"]["take_profit_pct"])
        sl = float(self.spec["exit_policy"]["stop_loss_pct"])
        if roi >= tp:
            exit_ms, reason = sellable_ms, "TP20"
        elif roi <= sl:
            exit_ms, reason = sellable_ms, "SL20"
        else:
            for flow in future[index:]:
                state = self.apply_flow(state, flow)
                roi = 100 * (sell_value(*state, tokens, fee) / principal - 1)
                if roi >= tp or roi <= sl:
                    exit_ms, reason = flow["ts_ms"], "TP20" if roi >= tp else "SL20"
                    break
        if reason == "TIMEOUT_120S":
            roi = 100 * (sell_value(*state, tokens, fee) / principal - 1)
        costs = self.spec["cost_model"]
        extra_lamports = 2 * int(costs["network_base_fee_lamports_each_transaction"])
        extra_lamports += int((int(costs["buy_compute_unit_limit"]) + int(costs["sell_compute_unit_limit"])) * int(costs["priority_fee_micro_lamports_per_cu"]) / 1_000_000)
        extra_lamports += 2 * int(costs["jito_tip_lamports_each_transaction"])
        net_roi = roi - 100 * extra_lamports / principal
        entry_price = principal / 1e9 / (tokens / 1e6)
        atomic_price = None
        if event.get("atomic_tokens_raw") and int(event["atomic_tokens_raw"]) > 0:
            atomic_price = event["atomic_quote_lamports"] / 1e9 / (int(event["atomic_tokens_raw"]) / 1e6)
        first_external_price = self.first_external_buy_price(event)
        creator_sells = [flow for flow in event["flows"] if flow["wallet"] == TARGET and flow["side"] == "sell"]
        first_sell = min((flow["ts_ms"] for flow in creator_sells), default=None)
        return {
            "lane": lane, "first_sellable_ms_after_entry": sellable_delay, "adverse_entry_stress": worse,
            "entry_ts_ms": entry_ms, "entry_utc": iso_ms(entry_ms), "first_sellable_ts_ms": sellable_ms,
            "entry_latency_from_earliest_feed_receive_ms": entry_ms - event["receive_ms"],
            "entry_curve_state": entry_curve_state, "entry_reconstructable": True,
            "modeled_exit_ts_ms": exit_ms, "modeled_exit_utc": iso_ms(exit_ms), "exit_reason": reason,
            "tokens_out_raw": tokens, "entry_price_sol_per_token": entry_price,
            "entry_degradation_vs_atomic_pct": 100 * (entry_price / atomic_price - 1) if atomic_price else None,
            "entry_degradation_vs_first_external_buyer_pct": 100 * (entry_price / first_external_price - 1) if first_external_price else None,
            "gross_roi_pct": roi, "estimated_non_pump_cost_lamports": extra_lamports, "net_roi_pct": net_roi,
            "target_first_sell_ts_ms": first_sell,
            "target_sell_before_entry": first_sell is not None and first_sell <= entry_ms,
            "target_sell_entry_to_sellable": first_sell is not None and entry_ms < first_sell <= sellable_ms,
            "target_sell_after_sellable_before_exit": first_sell is not None and sellable_ms < first_sell <= exit_ms,
            "target_sell_after_exit": first_sell is not None and first_sell > exit_ms,
        }

    def external_summary(self, event: dict[str, Any]) -> dict[str, Any]:
        external = [flow for flow in event["flows"] if flow.get("wallet") != TARGET and flow["signature"] != event["signature"]]
        buys = [flow for flow in external if flow["side"] == "buy"]
        sells = [flow for flow in external if flow["side"] == "sell"]
        first = min((flow["ts_ms"] for flow in buys), default=None)
        windows = {}
        for window in self.spec["external_flow_windows_ms"]:
            subset = [flow for flow in buys if flow["ts_ms"] <= event["receive_ms"] + window]
            sell_subset = [flow for flow in sells if flow["ts_ms"] <= event["receive_ms"] + window]
            windows[str(window)] = {
                "buyers": len(subset), "unique_buyers": len({flow["wallet"] for flow in subset if flow.get("wallet")}),
                "gross_buy_sol": sum(flow["quote_lamports"] for flow in subset) / 1e9,
                "sellers": len(sell_subset), "unique_sellers": len({flow["wallet"] for flow in sell_subset if flow.get("wallet")}),
                "observed_sell_sol": sum(flow["quote_lamports"] for flow in sell_subset) / 1e9,
                "recurrent_wallet_present": any(flow.get("wallet") in self.recurrent for flow in subset),
                "recurrent_wallet_buy_sol": sum(flow["quote_lamports"] for flow in subset if flow.get("wallet") in self.recurrent) / 1e9,
            }
        return {
            "schema": "8ycb-prospective-external-flow-v1", "kind": "external_flow", "event_id": event["event_id"],
            "cohort": event["cohort"], "mint": event["mint"],
            "first_external_buy_ts_ms": first,
            "first_external_buy_latency_ms": first - event["receive_ms"] if first is not None else None,
            "windows": windows,
        }

    def finalize_ready(self) -> None:
        now = time.time_ns() / 1e6
        for event_id, event in list(self.active.items()):
            latest_entry = max(event["signal_ready_ms"], event["receive_ms"] + 1000)
            if now < latest_entry + int(self.spec["exit_policy"]["maximum_hold_ms"]) + 1000:
                continue
            lanes = {"ACTUAL_SHADOW_SIGNAL_READY": event["signal_ready_ms"]}
            lanes.update({f"FIXED_{delay}MS": event["receive_ms"] + delay for delay in self.spec["fixed_entry_lanes_ms_from_earliest_feed_receive"]})
            scenarios = []
            for lane, entry in lanes.items():
                for sellable in self.spec["first_sellable_models_ms_after_modeled_entry"]:
                    scenarios.append(self.simulate(event, lane, entry, int(sellable), False))
                    scenarios.append(self.simulate(event, lane, entry, int(sellable), True))
            outcome = {
                "schema": "8ycb-prospective-outcome-v1", "kind": "outcome", "event_id": event_id,
                "cohort": event["cohort"], "matched_target_event_id": event.get("matched_target_event_id"),
                "mint": event["mint"], "signature": event["signature"], "finalized_at": utc_now(),
                "scenario_rows": scenarios, "tape_flow_count": len(event["flows"]),
            }
            if self.append(outcome):
                self.outcomes.append(outcome)
            external = self.external_summary(event)
            if self.append(external):
                self.external_rows.append(external)
            if event["cohort"] == "TARGET":
                primary = next(row for row in scenarios if row["lane"] == "ACTUAL_SHADOW_SIGNAL_READY" and row["first_sellable_ms_after_entry"] == 178 and not row["adverse_entry_stress"])
                collision = {
                    "schema": "8ycb-prospective-target-sell-collision-v1", "kind": "target_sell_collision",
                    "event_id": event_id, "mint": event["mint"], "target_first_sell_ts_ms": primary["target_first_sell_ts_ms"],
                    "primary_actual_signal_ready_p75": {key: primary[key] for key in (
                        "entry_ts_ms", "first_sellable_ts_ms", "modeled_exit_ts_ms", "target_sell_before_entry",
                        "target_sell_entry_to_sellable", "target_sell_after_sellable_before_exit", "target_sell_after_exit")},
                    "all_lanes": [{key: row[key] for key in ("lane", "first_sellable_ms_after_entry", "adverse_entry_stress", "target_sell_before_entry", "target_sell_entry_to_sellable", "target_sell_after_sellable_before_exit", "target_sell_after_exit")} for row in scenarios],
                }
                if self.append(collision):
                    self.collision_rows.append(collision)
                self.freeze_due_checkpoint()
            self.active.pop(event_id, None)

    def primary_rows(self, cohort: str = "TARGET") -> list[dict[str, Any]]:
        return [scenario for outcome in self.outcomes if outcome["cohort"] == cohort and outcome["event_id"] not in self.excluded_control_ids for scenario in outcome["scenario_rows"] if scenario["lane"] == "ACTUAL_SHADOW_SIGNAL_READY" and scenario["first_sellable_ms_after_entry"] == 178 and not scenario["adverse_entry_stress"]]

    def freeze_due_checkpoint(self) -> None:
        completed = len(self.primary_rows())
        frozen = {int(row["checkpoint_n"]) for row in self.checkpoint_rows}
        if completed not in self.spec["checkpoint_sizes"] or completed in frozen:
            return
        snapshot = self.build_checkpoints(include_history=False)
        row = {
            "schema": "8ycb-prospective-checkpoint-snapshot-v1", "kind": "checkpoint_snapshot",
            "checkpoint_n": completed, "frozen_at": utc_now(), "frozen_spec_sha256": self.spec_hash,
            "snapshot": snapshot,
        }
        if self.append(row):
            self.checkpoint_rows.append(row)

    def max_open_one(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        accepted, open_until = [], -1.0
        for row in sorted(rows, key=lambda item: item["entry_ts_ms"]):
            if row["entry_ts_ms"] >= open_until:
                accepted.append(row); open_until = row["modeled_exit_ts_ms"]
        return accepted

    def classify(self, n: int, metrics: dict[str, Any], drift: bool) -> str:
        if self.decoder_errors > 1000:
            return "8YCB_SHADOW_DATA_BLOCKER"
        if n < 25:
            return "8YCB_SHADOW_HEALTHY_COLLECTING"
        if n < 100:
            return "8YCB_SHADOW_DESCRIPTIVE_CHECKPOINT"
        if drift:
            return "8YCB_PUBLIC_EVENT_REGIME_DRIFT"
        pf = metrics.get("profit_factor") or 0
        exp = metrics.get("expectancy_pct") or -999
        if pf >= 1.5 and exp >= 3:
            return "8YCB_PUBLIC_EVENT_PROSPECTIVE_ALPHA_STRONG"
        if pf > 1 and exp > 0:
            return "8YCB_PUBLIC_EVENT_PROSPECTIVE_ALPHA_PROMISING"
        return "8YCB_PUBLIC_EVENT_PROSPECTIVE_ALPHA_FAILS"

    def build_checkpoints(self, include_history: bool = True) -> dict[str, Any]:
        primary = self.primary_rows()
        control_primary = self.primary_rows("GENERIC_CONTROL")
        raw_net = economics(primary, "net_roi_pct")
        raw_gross = economics(primary, "gross_roi_pct")
        max1 = self.max_open_one(primary)
        external_target = [row for row in self.external_rows if row["cohort"] == "TARGET"]
        first_buy = [row["first_external_buy_latency_ms"] for row in external_target if row.get("first_external_buy_latency_ms") is not None]
        recurrent = [row["windows"].get("1000", {}).get("recurrent_wallet_present", False) for row in external_target]
        v3 = self.spec["historical_v3_reference"]
        current_first = quantile(first_buy, .5)
        drift = bool(len(primary) >= 25 and current_first is not None and (current_first > 2 * v3["external_first_buy_p50_ms"] or current_first < .5 * v3["external_first_buy_p50_ms"]))
        fixed = {}
        for delay in self.spec["fixed_entry_lanes_ms_from_earliest_feed_receive"]:
            name = f"FIXED_{delay}MS"
            rows = [scenario for outcome in self.outcomes if outcome["cohort"] == "TARGET" for scenario in outcome["scenario_rows"] if scenario["lane"] == name and scenario["first_sellable_ms_after_entry"] == 178 and not scenario["adverse_entry_stress"]]
            stressed = [scenario for outcome in self.outcomes if outcome["cohort"] == "TARGET" for scenario in outcome["scenario_rows"] if scenario["lane"] == name and scenario["first_sellable_ms_after_entry"] == 178 and scenario["adverse_entry_stress"]]
            fixed[name] = {"net": economics(rows, "net_roi_pct"), "curve_gross": economics(rows, "gross_roi_pct"), "+5pct_entry_net": economics(stressed, "net_roi_pct")}
        stress_primary = [scenario for outcome in self.outcomes if outcome["cohort"] == "TARGET" for scenario in outcome["scenario_rows"] if scenario["lane"] == "ACTUAL_SHADOW_SIGNAL_READY" and scenario["first_sellable_ms_after_entry"] == 178 and scenario["adverse_entry_stress"]]
        signals_per_hour = len(self.signal_rows) / max((time.time() * 1000 - self.cohort_start_ms) / 3_600_000, 1 / 3600)
        collisions = [row["primary_actual_signal_ready_p75"] for row in self.collision_rows]
        collision_rate = sum(row["target_sell_entry_to_sellable"] or row["target_sell_before_entry"] for row in collisions) / len(collisions) if collisions else None
        creator_sell_latency = [row["target_first_sell_ts_ms"] - primary_row["entry_ts_ms"] + primary_row["entry_latency_from_earliest_feed_receive_ms"] for row, primary_row in zip(self.collision_rows, primary) if row.get("target_first_sell_ts_ms") is not None]
        classification = self.classify(len(primary), raw_net, drift)
        by_day = {}
        for day in sorted({iso_ms(row["entry_ts_ms"])[:10] for row in primary}):
            day_rows = [row for row in primary if iso_ms(row["entry_ts_ms"]).startswith(day)]
            by_day[day] = economics(day_rows, "net_roi_pct")
        checkpoints = {
            "schema": "8ycb-prospective-checkpoints-v1", "updated_at": utc_now(),
            "classification": classification, "target_signals": len(self.signal_rows), "completed_target_outcomes": len(primary),
            "signals_per_hour": signals_per_hour, "next_checkpoint": next((n for n in self.spec["checkpoint_sizes"] if len(primary) < n), None),
            "primary_actual_signal_ready_p75_178ms": {
                "raw_net": raw_net, "raw_curve_gross": raw_gross,
                "+5pct_entry_net": economics(stress_primary, "net_roi_pct"),
                "max_open1_net": economics(max1, "net_roi_pct"),
                "entry_latency_ms": {
                    "p50": quantile([row["entry_latency_from_earliest_feed_receive_ms"] for row in primary], .5),
                    "p75": quantile([row["entry_latency_from_earliest_feed_receive_ms"] for row in primary], .75),
                    "p90": quantile([row["entry_latency_from_earliest_feed_receive_ms"] for row in primary], .9),
                },
                "entry_degradation_vs_atomic_pct": {
                    "p50": quantile([row["entry_degradation_vs_atomic_pct"] for row in primary if row.get("entry_degradation_vs_atomic_pct") is not None], .5),
                    "p75": quantile([row["entry_degradation_vs_atomic_pct"] for row in primary if row.get("entry_degradation_vs_atomic_pct") is not None], .75),
                    "p90": quantile([row["entry_degradation_vs_atomic_pct"] for row in primary if row.get("entry_degradation_vs_atomic_pct") is not None], .9),
                },
                "entry_degradation_vs_first_external_buyer_pct": {
                    "p50": quantile([row["entry_degradation_vs_first_external_buyer_pct"] for row in primary if row.get("entry_degradation_vs_first_external_buyer_pct") is not None], .5),
                    "p75": quantile([row["entry_degradation_vs_first_external_buyer_pct"] for row in primary if row.get("entry_degradation_vs_first_external_buyer_pct") is not None], .75),
                    "p90": quantile([row["entry_degradation_vs_first_external_buyer_pct"] for row in primary if row.get("entry_degradation_vs_first_external_buyer_pct") is not None], .9),
                },
                "target_sell_before_or_during_first_sellable_rate": collision_rate,
            },
            "fixed_delay_sensitivity_p75_178ms": fixed,
            "external_flow": {
                "first_external_buy_latency_ms": {"p50": quantile(first_buy, .5), "p75": quantile(first_buy, .75), "p90": quantile(first_buy, .9)},
                "recurrent_wallet_participation_within_1s": sum(recurrent) / len(recurrent) if recurrent else None,
            },
            "creator_first_sell_latency_from_event_receive_ms": {
                "p50": quantile(creator_sell_latency, .5), "p75": quantile(creator_sell_latency, .75), "p90": quantile(creator_sell_latency, .9),
            },
            "generic_control_comparison": {
                "completed_controls": len(control_primary),
                "primary_net": economics(control_primary, "net_roi_pct"),
                "primary_curve_gross": economics(control_primary, "gross_roi_pct"),
                "target_minus_control_net_expectancy_pct": (
                    raw_net["expectancy_pct"] - economics(control_primary, "net_roi_pct")["expectancy_pct"]
                    if raw_net["expectancy_pct"] is not None and economics(control_primary, "net_roi_pct")["expectancy_pct"] is not None else None
                ),
            },
            "distribution_drift": {"flag": drift, "rule": "prospective first-external-buy P50 outside 0.5x-2x historical V3 after N25"},
            "by_utc_day_primary_net": by_day,
        }
        if include_history:
            checkpoints["frozen_checkpoint_history"] = [
                {"checkpoint_n": row["checkpoint_n"], "frozen_at": row["frozen_at"], "snapshot": row["snapshot"]}
                for row in self.checkpoint_rows
            ]
        return checkpoints

    def write_outputs(self) -> None:
        checkpoints = self.build_checkpoints()
        detection = [row["timing"]["total_observable_detection_ms"] for row in self.signal_rows]
        report = {
            "schema": "8ycb-prospective-final-report-v1", "updated_at": utc_now(),
            "status": checkpoints["classification"], "cohort_start_utc": self.spec["cohort_start_utc"],
            "frozen_spec_sha256": self.spec_hash, "events_observed": len(self.signal_rows),
            "completed_outcomes": checkpoints["completed_target_outcomes"],
            "observation_latency_ms": {"p50": quantile(detection, .5), "p75": quantile(detection, .75), "p90": quantile(detection, .9)},
            "primary_metrics": checkpoints["primary_actual_signal_ready_p75_178ms"],
            "comparison_to_v3": {"historical_reference": self.spec["historical_v3_reference"], "prospective": checkpoints["external_flow"], "drift": checkpoints["distribution_drift"]},
            "safety": {"mode": "OBSERVATION_ONLY_NO_SUBMIT", "transaction_construction": "OMITTED_V1", "live_actions": 0},
            "important_note": "Before N100 this cohort cannot classify alpha as prospectively proven.",
        }
        collisions = {
            "schema": "8ycb-target-sell-collision-report-v1", "updated_at": utc_now(), "events": len(self.collision_rows),
            "primary_rows": [row["primary_actual_signal_ready_p75"] for row in self.collision_rows],
        }
        health = self.health(checkpoints["classification"])
        atomic_json(self.output / "checkpoints.json", checkpoints)
        atomic_json(self.output / "final_report.json", report)
        atomic_json(self.output / "service_health.json", health)
        atomic_json(self.output / "creator_sell_collision_report.json", collisions)
        self.write_snapshots()

    def write_snapshots(self) -> None:
        mappings = {
            "signal_events.jsonl.gz": self.signal_rows,
            "outcome_events.jsonl.gz": self.outcomes,
            "target_sell_collisions.jsonl.gz": self.collision_rows,
            "external_flow.jsonl.gz": self.external_rows,
            "generic_controls.jsonl.gz": [row for row in self.generic_rows if row["event_id"] not in self.excluded_control_ids],
        }
        included_outcomes = [row for row in self.outcomes if row["event_id"] not in self.excluded_control_ids]
        mappings["outcome_events.jsonl.gz"] = included_outcomes
        mappings["external_flow.jsonl.gz"] = [row for row in self.external_rows if row["event_id"] not in self.excluded_control_ids]
        entry_rows = [dict({"event_id": outcome["event_id"], "cohort": outcome["cohort"], "mint": outcome["mint"]}, **scenario) for outcome in included_outcomes for scenario in outcome["scenario_rows"]]
        mappings["entry_counterfactuals.jsonl.gz"] = entry_rows
        for name, rows in mappings.items():
            path = self.output / name
            tmp = path.with_suffix(path.suffix + ".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as handle:
                for row in rows:
                    handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
            os.replace(tmp, path)

    def health(self, classification: str) -> dict[str, Any]:
        usage = resource.getrusage(resource.RUSAGE_SELF) if resource is not None else None
        return {
            "schema": "8ycb-shadow-service-health-v1", "status": classification,
            "updated_at": utc_now(), "pid": os.getpid(), "uptime_seconds": time.monotonic() - self.start_monotonic,
            "target_events_observed": len(self.signal_rows), "last_event": self.last_signal,
            "decoder_errors": self.decoder_errors, "dropped_rows": self.dropped_rows,
            "active_horizons": len(self.active), "global_feed_offset": self.global_tail.position,
            "wallet_feed_offset": self.wallet_tail.position, "maximum_rss_kib": usage.ru_maxrss if usage else None,
            "user_cpu_seconds": usage.ru_utime if usage else None, "system_cpu_seconds": usage.ru_stime if usage else None,
            "ledger_integrity": {"bytes": self.ledger.stat().st_size if self.ledger.exists() else 0, "sha256": file_sha(self.ledger) if self.ledger.exists() else hashlib.sha256(b"").hexdigest()},
            "frozen_spec_sha256": self.spec_hash, "transaction_submission_count": 0,
        }

    def write_state(self) -> None:
        atomic_json(self.state_path, {
            "schema": "8ycb-shadow-state-v1", "updated_at": utc_now(), "frozen_spec_sha256": self.spec_hash,
            "global_offset": self.global_tail.position, "wallet_offset": self.wallet_tail.position,
            "pending_creates": self.pending_creates, "pending_atomic": dict(self.pending_atomic),
        })

    def write_manifest(self) -> None:
        files = {}
        for path in sorted(self.output.iterdir()):
            if not path.is_file() or path.name.endswith(".tmp") or path.name == "validation_manifest.json":
                continue
            files[path.name] = {"bytes": path.stat().st_size, "sha256": file_sha(path)}
        atomic_json(self.output / "validation_manifest.json", {
            "schema": "8ycb-prospective-validation-manifest-v1", "updated_at": utc_now(),
            "status": "PASS", "frozen_spec_sha256": self.spec_hash, "files": files,
        })

    def run_once(self) -> int:
        processed = 0
        for raw, stamp in self.wallet_tail.read_available(500):
            self.process_wallet(raw, stamp); processed += 1
        for raw, stamp in self.global_tail.read_available(2000):
            self.process_global(raw, stamp); processed += 1
        self.finalize_ready()
        now = time.monotonic()
        if now - self.last_state_write >= 1:
            self.write_state(); self.last_state_write = now
        if now - self.last_report_write >= 10:
            self.write_outputs(); self.last_report_write = now
        if now - self.last_manifest_write >= 300:
            self.write_manifest(); self.last_manifest_write = now
        return processed


def handle_stop(_signum, _frame) -> None:
    global RUNNING
    RUNNING = False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--global-feed", required=True)
    parser.add_argument("--wallet-feed", required=True)
    parser.add_argument("--watcher", required=True)
    parser.add_argument("--watcher-script", required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    shadow = Shadow(Path(args.spec), Path(args.output), Path(args.global_feed), Path(args.wallet_feed))
    watcher = subprocess.Popen([args.watcher, args.watcher_script], cwd=args.output, env=os.environ.copy())
    shadow.append({
        "schema": "8ycb-prospective-service-start-v1", "kind": "service_start", "at": utc_now(),
        "pid": os.getpid(), "watcher_pid": watcher.pid, "cohort_start_utc": shadow.spec["cohort_start_utc"],
        "frozen_spec_sha256": shadow.spec_hash, "mode": "OBSERVATION_ONLY_NO_SUBMIT",
    })
    try:
        while RUNNING:
            if watcher.poll() is not None:
                raise RuntimeError(f"read-only wallet watcher exited code={watcher.returncode}")
            if shadow.run_once() == 0:
                time.sleep(0.01)
    finally:
        watcher.terminate()
        try:
            watcher.wait(timeout=5)
        except subprocess.TimeoutExpired:
            watcher.kill()
        shadow.write_state()
        shadow.write_outputs()
        shadow.write_manifest()


if __name__ == "__main__":
    main()
