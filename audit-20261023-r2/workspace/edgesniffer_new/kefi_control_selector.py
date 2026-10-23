#!/usr/bin/env python3
"""Select a deterministic, outcome-blind non-selected control sample.

Controls are matched only on source partition and UTC hour. The selection is
fixed by SHA-256 rank before any replay or exit result is examined.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from collections import Counter, defaultdict
from pathlib import Path


def rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def bucket(row: dict) -> str:
    return f"{row['source_partition']}|{int(row['event_timestamp']) // 3600}"


def rank(signature: str) -> int:
    return int.from_bytes(hashlib.sha256(signature.encode()).digest()[:8], "big")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selected", type=Path, required=True)
    ap.add_argument("--controls", type=Path, nargs="+", required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--controls-per-selected", type=int, default=3)
    args = ap.parse_args()

    selected = list(rows(args.selected))
    selected_sigs = {str(row["signature"]) for row in selected}
    selected_counts = Counter(bucket(row) for row in selected)
    quotas = {key: args.controls_per_selected * count for key, count in selected_counts.items()}

    # Keep the lowest deterministic ranks in each matched bucket. The heap is
    # bounded by the predeclared quota, so the full candidate universe is not
    # loaded into memory.
    heaps: dict[str, list[tuple[int, str, dict]]] = defaultdict(list)
    seen: set[str] = set()
    scanned = 0
    for path in args.controls:
        for row in rows(path):
            scanned += 1
            sig = str(row.get("candidate_signature", ""))
            if not sig or sig in selected_sigs or sig in seen:
                continue
            key = bucket(row)
            quota = quotas.get(key, 0)
            if not quota:
                continue
            seen.add(sig)
            item = (-rank(sig), sig, row)
            heap = heaps[key]
            if len(heap) < quota:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)

    chosen = []
    for key in sorted(heaps):
        for _, sig, row in sorted(heaps[key], key=lambda item: (item[0], item[1])):
            chosen.append(
                {
                    "control_signature": sig,
                    "matched_bucket": key,
                    "matched_selected_count": selected_counts[key],
                    "match_rule": "same_closed_source_partition_and_utc_hour",
                    "side": "BUY",
                    "selected_mint": row["mint"],
                    "signature": sig,
                    "source_partition": row["source_partition"],
                    "source_frame_sha256": row["source_frame_sha256"],
                    "received_at_unix_ns": row["received_at_unix_ns"],
                    "slot": row["slot"],
                    "event_index": row["event_index"],
                    "event_timestamp": row["event_timestamp"],
                    "pool": row["pool"],
                    "pre_pool_base": row["pool_base_raw"],
                    "pre_pool_quote": row["pool_quote_raw"],
                    "base_amount": row["base_amount"],
                    "pool_quote_delta": row["pool_quote_delta"],
                    "lp_fee_bps": row["lp_fee_bps"],
                    "lp_fee": row["lp_fee"],
                    "protocol_fee_bps": row["protocol_fee_bps"],
                    "protocol_fee": row["protocol_fee"],
                    "coin_creator_bps": row["coin_creator_bps"],
                    "coin_creator_fee": row["coin_creator_fee"],
                    "virtual_quote_reserves": row["virtual_quote_reserves"],
                    "buyback_bps": row["buyback_bps"],
                    "buyback_fee": row["buyback_fee"],
                    "cashback_bps": row["cashback_bps"],
                    "cashback": row["cashback"],
                    "user_quote_amount": row["user_quote_amount"],
                    "outcome_blind": True,
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in chosen:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    manifest = {
        "schema": "KEFI_MATCHED_CONTROL_SAMPLE_V1",
        "status": "READY" if all(len(heaps[key]) == quotas[key] for key in quotas) else "INCOMPLETE_BUCKETS",
        "outcome_blind": True,
        "selection_frozen_before_replay": True,
        "match_rule": "same_closed_source_partition_and_utc_hour",
        "controls_per_selected": args.controls_per_selected,
        "selected_rows": len(selected),
        "selected_signatures_excluded": len(selected_sigs),
        "candidate_rows_scanned": scanned,
        "control_rows": len(chosen),
        "matched_buckets": len(selected_counts),
        "selected_bucket_counts": dict(sorted(selected_counts.items())),
        "control_bucket_counts": dict(sorted(Counter(row["matched_bucket"] for row in chosen).items())),
        "short_buckets": {key: quotas[key] - len(heaps[key]) for key in quotas if len(heaps[key]) < quotas[key]},
        "scientific_use": "predeclared control cohort for outcome-blind context/replay comparison",
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
