#!/usr/bin/env python3
"""Build an outcome-blind, strictly pre-entry kEfi feature corpus.

The source corpus contains closed, immutable candidate-buy events and a sealed
kEfi selected-buy file.  This tool merges them in canonical source order,
captures a pool snapshot *before* every emitted target, and fails closed on
source-order regressions or duplicated selected identities.  It never reads
exit data, outcomes, or PnL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict, deque
from pathlib import Path


WINDOWS_SECONDS = (5, 20, 60)
SCHEMA = "KEFI_RICH_PREENTRY_RECONSTRUCTION_V1"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def key(row: dict, signature_field: str) -> tuple[int, int, str, int]:
    return (
        int(row["received_at_unix_ns"]),
        int(row.get("slot", 0)),
        str(row[signature_field]),
        int(row.get("event_index", 0)),
    )


def fee_bps(row: dict) -> float:
    return float(row.get("lp_fee_bps", 0)) + float(row.get("protocol_fee_bps", 0)) + float(row.get("coin_creator_bps", 0))


def identity(row: dict, signature_field: str) -> str:
    return f"{row[signature_field]}:{int(row.get('event_index', 0))}"


def target_sampled(identity_value: str, threshold_per_million: int) -> bool:
    value = int.from_bytes(hashlib.sha256(identity_value.encode()).digest()[:8], "big") % 1_000_000
    return value < threshold_per_million


def read_selected(path: Path) -> dict[str, list[dict]]:
    partitions: dict[str, list[dict]] = defaultdict(list)
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("side") != "BUY":
                continue
            required = ("source_partition", "signature", "received_at_unix_ns", "pool")
            if any(not row.get(field) for field in required):
                raise ValueError(f"FAIL_CLOSED: malformed selected row {line_number}")
            row["_identity"] = identity(row, "signature")
            if row["_identity"] in seen:
                raise ValueError(f"FAIL_CLOSED: duplicate selected identity {row['_identity']}")
            seen.add(row["_identity"])
            partitions[str(row["source_partition"])].append(row)
    for rows in partitions.values():
        rows.sort(key=lambda row: key(row, "signature"))
    return partitions


def raw_events(path: Path):
    previous = None
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            required = ("candidate_signature", "received_at_unix_ns", "pool", "source_partition")
            if any(not row.get(field) for field in required):
                raise ValueError(f"FAIL_CLOSED: malformed candidate {path}:{line_number}")
            current = key(row, "candidate_signature")
            if previous is not None and current < previous:
                raise ValueError(f"FAIL_CLOSED: source order regression {path}:{line_number}")
            previous = current
            yield row


class PoolState:
    def __init__(self) -> None:
        self.events: deque[tuple[int, float]] = deque()
        self.first_ns: int | None = None
        self.last_ns: int | None = None
        self.last_pre_quote: float | None = None

    def snapshot(self, timestamp_ns: int, row: dict) -> dict:
        cutoff = timestamp_ns - max(WINDOWS_SECONDS) * 1_000_000_000
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()
        pre_quote = float(row.get("pre_pool_quote", row.get("pool_quote_raw", 0)) or 0)
        pre_base = float(row.get("pre_pool_base", row.get("pool_base_raw", 0)) or 0)
        values = {
            "log_pre_pool_quote": math.log1p(pre_quote),
            "log_pre_pool_base": math.log1p(pre_base),
            "amm_fee_bps": fee_bps(row),
            "observed_pool_age_seconds": 0.0 if self.first_ns is None else (timestamp_ns - self.first_ns) / 1_000_000_000,
            "seconds_since_previous_observed_buy": None if self.last_ns is None else (timestamp_ns - self.last_ns) / 1_000_000_000,
            "prior_pre_pool_quote_log_change": 0.0 if not self.last_pre_quote or pre_quote <= 0 else math.log1p(pre_quote) - math.log1p(self.last_pre_quote),
        }
        for seconds in WINDOWS_SECONDS:
            floor = timestamp_ns - seconds * 1_000_000_000
            within = [quote for event_ns, quote in self.events if event_ns >= floor]
            values[f"prior_buy_count_{seconds}s"] = len(within)
            values[f"prior_buy_quote_{seconds}s"] = sum(within)
        return values

    def observe(self, timestamp_ns: int, row: dict) -> None:
        quote = float(row.get("user_quote_amount", 0) or 0)
        if self.first_ns is None:
            self.first_ns = timestamp_ns
        self.events.append((timestamp_ns, quote))
        self.last_ns = timestamp_ns
        pre_quote = float(row.get("pre_pool_quote", row.get("pool_quote_raw", 0)) or 0)
        if pre_quote > 0:
            self.last_pre_quote = pre_quote


def emit(out, row: dict, signature_field: str, label: int, state: PoolState) -> None:
    timestamp_ns = int(row["received_at_unix_ns"])
    item = {
        "schema": SCHEMA,
        "label": label,
        "identity": identity(row, signature_field),
        "source_partition": row["source_partition"],
        "pool": row["pool"],
        "mint": row.get("selected_mint", row.get("mint")),
        "received_at_unix_ns": timestamp_ns,
        "slot": int(row.get("slot", 0)),
        "preentry": state.snapshot(timestamp_ns, row),
    }
    out.write(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n")


def process_partition(candidate_path: Path, selected: list[dict], output, states: dict[str, PoolState], sample_threshold: int, counters: dict) -> None:
    selected_index = 0
    selected_ids = {row["_identity"] for row in selected}
    for candidate in raw_events(candidate_path):
        candidate_key = key(candidate, "candidate_signature")
        while selected_index < len(selected) and key(selected[selected_index], "signature") <= candidate_key:
            row = selected[selected_index]
            pool = states[row["pool"]]
            emit(output, row, "signature", 1, pool)
            pool.observe(int(row["received_at_unix_ns"]), row)
            counters["selected_rows"] += 1
            selected_index += 1
        candidate_id = identity(candidate, "candidate_signature")
        if candidate_id in selected_ids:
            raise ValueError(f"FAIL_CLOSED: selected identity present in candidate universe {candidate_id}")
        pool = states[candidate["pool"]]
        if target_sampled(candidate_id, sample_threshold):
            emit(output, candidate, "candidate_signature", 0, pool)
            counters["control_rows"] += 1
        pool.observe(int(candidate["received_at_unix_ns"]), candidate)
        counters["candidate_events"] += 1
    while selected_index < len(selected):
        row = selected[selected_index]
        pool = states[row["pool"]]
        emit(output, row, "signature", 1, pool)
        pool.observe(int(row["received_at_unix_ns"]), row)
        counters["selected_rows"] += 1
        selected_index += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected", required=True, type=Path)
    parser.add_argument("--shard-dir", required=True, type=Path)
    parser.add_argument("--shards", required=True, nargs="+")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--sample-threshold-per-million", type=int, default=2200)
    args = parser.parse_args()
    if not 0 < args.sample_threshold_per_million <= 1_000_000:
        raise SystemExit("sample threshold must be in 1..1000000")
    selected_by_partition = read_selected(args.selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    states: dict[str, PoolState] = defaultdict(PoolState)
    counters = {"candidate_events": 0, "selected_rows": 0, "control_rows": 0}
    shard_records = []
    with args.output.open("w", encoding="utf-8") as output:
        for name in args.shards:
            candidate_path = args.shard_dir / f"{name}.jsonl"
            if not candidate_path.exists():
                raise SystemExit(f"FAIL_CLOSED: missing sealed shard {candidate_path}")
            partition = None
            with candidate_path.open(encoding="utf-8") as probe:
                for line in probe:
                    if line.strip():
                        partition = json.loads(line)["source_partition"]
                        break
            if partition is None:
                raise SystemExit(f"FAIL_CLOSED: empty shard {candidate_path}")
            before = dict(counters)
            process_partition(candidate_path, selected_by_partition.pop(partition, []), output, states, args.sample_threshold_per_million, counters)
            shard_records.append({"name": name, "path": str(candidate_path), "source_partition": partition, "sha256": digest(candidate_path), "candidate_events": counters["candidate_events"] - before["candidate_events"]})
    if selected_by_partition:
        raise SystemExit(f"FAIL_CLOSED: selected rows outside supplied sealed shards: {sorted(selected_by_partition)}")
    manifest = {
        "schema": SCHEMA,
        "status": "PASS_OUTCOME_BLIND_DESCRIPTIVE",
        "strict_preentry": True,
        "canonical_order": "received_at_unix_ns, slot, signature, event_index",
        "target_excluded_from_own_snapshot": True,
        "features": [
            "log_pre_pool_quote", "log_pre_pool_base", "amm_fee_bps", "observed_pool_age_seconds",
            "seconds_since_previous_observed_buy", "prior_pre_pool_quote_log_change",
            *[f"prior_buy_count_{n}s" for n in WINDOWS_SECONDS], *[f"prior_buy_quote_{n}s" for n in WINDOWS_SECONDS],
        ],
        "control_sampling": {"method": "sha256 identity threshold", "threshold_per_million": args.sample_threshold_per_million},
        "inputs": {"selected": {"path": str(args.selected), "sha256": digest(args.selected)}, "shards": shard_records},
        "counts": counters,
        "output": {"path": str(args.output), "sha256": digest(args.output)},
        "outcomes_accessed": False,
        "pnl_accessed": False,
        "interpretation": "Outcome-blind reconstruction only. The selected class and controls are historical and require a fresh prospective gate before any predictive or execution claim.",
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "counts": counters, "output": str(args.output), "manifest": str(args.manifest)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
