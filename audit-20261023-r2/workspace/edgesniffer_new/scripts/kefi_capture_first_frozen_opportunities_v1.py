#!/usr/bin/env python3
"""Create outcome-blind paper opportunities from the frozen kEFi rule.

This adapter consumes a frozen, deduplicated capture-first market tape.  It
reproduces the already-frozen pre-entry fingerprint and crowded-impulse rule
without reading any post-entry outcome.  A separate strict replay owns all
economic calculations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_FINGERPRINT_SHA256 = "db08899d92f4d69d8c0234cfff9cbd05bd27dfb1365aed88d08b85f88cfc1b4a"
EXPECTED_RULE_SHA256 = "b298e50e9a1a976ed8f4562197b3510c86389c2eaf324aa56716744da507cc3e"
SCHEMA = "KEFI_CAPTURE_FIRST_FROZEN_OPPORTUNITIES_V1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_ns(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1_000_000_000)


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def fee_bps(row: dict) -> float:
    return float(row.get("lp_fee_bps", 0)) + float(row.get("protocol_fee_bps", 0)) + float(row.get("coin_creator_bps", 0))


class PoolState:
    windows = (5, 20, 60)

    def __init__(self) -> None:
        self.events: deque[tuple[int, float]] = deque()
        self.first: int | None = None
        self.last: int | None = None
        self.last_quote: float | None = None

    def snapshot(self, timestamp_ns: int, row: dict) -> list[float]:
        while self.events and self.events[0][0] < timestamp_ns - 60_000_000_000:
            self.events.popleft()
        quote = float(row.get("pool_quote_raw", 0) or 0) - float(row.get("pool_quote_delta", 0) or 0)
        base = float(row.get("pool_base_raw", 0) or 0) + float(row.get("base_amount", 0) or 0)
        age = (timestamp_ns - self.first) / 1e9 if self.first is not None else 0.0
        gap = (timestamp_ns - self.last) / 1e9 if self.last is not None else 86_400.0
        values = [
            math.log1p(max(quote, 0.0)),
            math.log1p(max(base, 0.0)),
            fee_bps(row),
            math.log1p(min(age, 86_400.0)),
            math.log1p(min(gap, 86_400.0)),
            0.0 if not self.last_quote or quote <= 0 else math.log1p(quote) - math.log1p(self.last_quote),
        ]
        for window in self.windows:
            values.append(math.log1p(sum(1 for event_time, _ in self.events if event_time >= timestamp_ns - window * 1_000_000_000)))
        for window in self.windows:
            values.append(math.log1p(sum(value for event_time, value in self.events if event_time >= timestamp_ns - window * 1_000_000_000)))
        values.append(1.0 if self.last is None else 0.0)
        return values

    def observe(self, timestamp_ns: int, row: dict) -> None:
        if self.first is None:
            self.first = timestamp_ns
        self.events.append((timestamp_ns, float(row.get("user_quote_amount", 0) or 0)))
        self.last = timestamp_ns
        quote = float(row.get("pool_quote_raw", 0) or 0) - float(row.get("pool_quote_delta", 0) or 0)
        if quote > 0:
            self.last_quote = quote


def score(features: list[float], model: dict) -> float:
    standardized = [
        (value - mean) / scale
        for value, mean, scale in zip(features, model["standardization_means"], model["standardization_scales"])
    ]
    linear = float(model["intercept"]) + sum(
        float(model["coefficients"][name]) * value
        for name, value in zip(model["features"], standardized)
    )
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, linear))))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--fingerprint", type=Path, required=True)
    parser.add_argument("--rule", type=Path, required=True)
    parser.add_argument("--boundary-utc", required=True)
    parser.add_argument("--cutoff-utc", required=True)
    parser.add_argument("--out-opportunities", type=Path, required=True)
    parser.add_argument("--out-summary", type=Path, required=True)
    args = parser.parse_args()

    if sha256(args.fingerprint) != EXPECTED_FINGERPRINT_SHA256:
        raise SystemExit("FAIL_CLOSED: frozen fingerprint hash mismatch")
    if sha256(args.rule) != EXPECTED_RULE_SHA256:
        raise SystemExit("FAIL_CLOSED: frozen rule hash mismatch")
    model = json.loads(args.fingerprint.read_text())
    rule = json.loads(args.rule.read_text())
    if model.get("status") != "FROZEN_FOR_PROSPECTIVE_OUTCOME_BLIND_SHADOW" or not model.get("strict_preentry"):
        raise SystemExit("FAIL_CLOSED: invalid frozen fingerprint contract")
    frozen_rule = rule.get("rule", {})
    if (rule.get("status") != "FROZEN_FOR_FRESH_OUTCOME_BLIND_PAPER_TEST"
            or frozen_rule.get("feature") != "prior_buy_count_60s"
            or frozen_rule.get("direction") != "<="):
        raise SystemExit("FAIL_CLOSED: invalid frozen pre-entry rule")

    boundary_ns = epoch_ns(args.boundary_utc)
    cutoff_ns = epoch_ns(args.cutoff_utc)
    warmup_ns = boundary_ns + int(float(model["warmup_seconds"]) * 1_000_000_000)
    max_entry_ns = cutoff_ns - 120_000_000_000
    if max_entry_ns <= warmup_ns:
        raise SystemExit("FAIL_CLOSED: insufficient sealed post-boundary horizon")

    pools: dict[str, PoolState] = defaultdict(PoolState)
    seen_buckets: set[tuple[str, int]] = set()
    opportunities: list[dict] = []
    previous_key: tuple[int, int, str, int] | None = None
    rows_seen = rows_after_boundary = 0
    with args.market.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            timestamp_ns = int(row["received_at_unix_ns"])
            key = (timestamp_ns, int(row["slot"]), str(row["candidate_signature"]), int(row.get("event_index", 0)))
            if previous_key is not None and key <= previous_key:
                raise SystemExit("FAIL_CLOSED: market tape order regression")
            previous_key = key
            rows_seen += 1
            if timestamp_ns < boundary_ns:
                continue
            if timestamp_ns > cutoff_ns:
                break
            rows_after_boundary += 1
            pool = str(row["pool"])
            state = pools[pool]
            features = state.snapshot(timestamp_ns, row)
            if timestamp_ns >= warmup_ns and timestamp_ns <= max_entry_ns and not bool(row.get("is_tracked_teacher")):
                bucket = (pool, timestamp_ns // 20_000_000_000)
                if bucket not in seen_buckets:
                    seen_buckets.add(bucket)
                    base_match = score(features, model) >= float(model["threshold"])
                    prior_buy_count = len(state.events)
                    crowded_match = base_match and prior_buy_count <= float(frozen_rule["threshold"])
                    opportunities.append({
                        "candidate_signature": str(row["candidate_signature"]),
                        "event_index": int(row.get("event_index", 0)),
                        "pool": pool,
                        "mint": str(row["mint"]),
                        "received_at_unix_ns": timestamp_ns,
                        "source_partition": row.get("source_partition"),
                        "fingerprint_match": crowded_match,
                        "base_fingerprint_match": base_match,
                        "crowded_impulse_match": crowded_match,
                        "prior_buy_count_60s": prior_buy_count,
                        "outcome_blind": True,
                    })
            state.observe(timestamp_ns, row)

    if not opportunities:
        raise SystemExit("FAIL_CLOSED: no frozen-rule opportunities")
    atomic_jsonl(args.out_opportunities, opportunities)
    summary = {
        "schema": SCHEMA,
        "created_at_utc": utc_now(),
        "market_sha256": sha256(args.market),
        "fingerprint_sha256": sha256(args.fingerprint),
        "rule_sha256": sha256(args.rule),
        "boundary_utc": args.boundary_utc,
        "cutoff_utc": args.cutoff_utc,
        "warmup_seconds": model["warmup_seconds"],
        "horizon_seconds": 120,
        "rows_seen": rows_seen,
        "rows_after_boundary": rows_after_boundary,
        "opportunities": len(opportunities),
        "matches": sum(row["crowded_impulse_match"] for row in opportunities),
        "non_matches": sum(not row["crowded_impulse_match"] for row in opportunities),
        "outcome_blind": True,
        "pnl_accessed": False,
        "execution_enabled": False,
    }
    atomic_json(args.out_summary, summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
