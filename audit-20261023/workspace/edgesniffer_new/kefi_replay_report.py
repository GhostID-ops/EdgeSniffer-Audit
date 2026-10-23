#!/usr/bin/env python3
"""Outcome-blind summary for the isolated kEFi 120-second replay.

This reads only discovery/replay JSONL artifacts and never accesses outcomes,
PnL, or live scientific databases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


def rows(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", required=True, type=Path)
    ap.add_argument("--replay", required=True, type=Path, nargs="+")
    ap.add_argument("--output", required=True, type=Path)
    ns = ap.parse_args()

    entries = list(rows(ns.entries))
    entry_required = ("signature", "selected_mint", "pool", "received_at_unix_ns")
    malformed_entries = sum(1 for r in entries if any(k not in r for k in entry_required))
    entry_signatures = [r.get("signature") for r in entries]
    duplicate_entry_signatures = sum(v - 1 for v in Counter(entry_signatures).values() if v > 1)
    by_sig = {r["signature"]: r for r in entries if r.get("signature") is not None}
    event_counts_by_sig = Counter()
    sides_by_sig = defaultdict(set)
    max_offset_by_sig = {}
    last_order_by_sig = {}
    duplicate_keys = Counter()
    out_of_window = 0
    malformed = 0
    unknown_targets = 0
    identity_mismatches = 0
    missing_fee_fields = 0
    ordering_violations = 0
    for replay_path in ns.replay:
        for r in rows(replay_path):
            required = ("target_signature", "signature", "event_index", "window_offset_ns")
            if any(k not in r for k in required):
                malformed += 1
                raise ValueError(f"malformed replay row in {replay_path}")
            target = by_sig.get(r["target_signature"])
            if target is None:
                unknown_targets += 1
            elif (r.get("target_mint"), r.get("target_pool")) != (target.get("selected_mint"), target.get("pool")):
                identity_mismatches += 1
            key = (r["target_signature"], r["signature"], r["event_index"])
            duplicate_keys[key] += 1
            sig = r["target_signature"]
            event_counts_by_sig[sig] += 1
            sides_by_sig[sig].add(r.get("side", "UNKNOWN"))
            max_offset_by_sig[sig] = max(max_offset_by_sig.get(sig, -1), r["window_offset_ns"])
            order = (r.get("received_at_unix_ns"), r.get("slot"), r.get("event_index"))
            if sig in last_order_by_sig and order < last_order_by_sig[sig]:
                ordering_violations += 1
            last_order_by_sig[sig] = order
            if r["window_offset_ns"] < 0 or r["window_offset_ns"] > 120_000_000_000:
                out_of_window += 1
            if any(k not in r for k in ("lp_fee_bps", "lp_fee", "protocol_fee_bps", "protocol_fee")):
                missing_fee_fields += 1

    replayed = set(event_counts_by_sig)
    missing = sorted(set(by_sig) - replayed)
    duplicate_count = sum(v - 1 for v in duplicate_keys.values() if v > 1)
    event_counts = list(event_counts_by_sig.values())
    all_event_counts = [event_counts_by_sig.get(sig, 0) for sig in by_sig]
    side_counts = Counter()
    fee_rows = 0
    for sig, count in event_counts_by_sig.items():
        side_counts.update(sides_by_sig[sig])
        fee_rows += count

    per_entry = []
    for sig, entry in by_sig.items():
        count = event_counts_by_sig.get(sig, 0)
        per_entry.append({
            "signature": sig,
            "mint": entry.get("selected_mint"),
            "pool": entry.get("pool"),
            "replay_event_count": count,
            "has_replay_events": bool(count),
            "sides": sorted(sides_by_sig.get(sig, set())),
            "max_window_offset_ns": max_offset_by_sig.get(sig),
        })

    report = {
        "schema": "KEFI_REPLAY_REPORT_V1",
        "outcome_blind": True,
        "pnl_read": False,
        "entries": len(entries),
        "malformed_entries": malformed_entries,
        "duplicate_entry_signatures": duplicate_entry_signatures,
        "distinct_mints": len({r.get("selected_mint") for r in entries}),
        "replay_events": sum(event_counts),
        "entries_with_replay_events": len(replayed),
        "entries_without_replay_events": len(missing),
        "entries_with_zero_events": sum(1 for n in all_event_counts if n == 0),
        "missing_entry_signatures": missing,
        "duplicate_replay_event_keys": duplicate_count,
        "malformed_replay_rows": malformed,
        "unknown_replay_targets": unknown_targets,
        "target_identity_mismatches": identity_mismatches,
        "replay_rows_missing_fee_fields": missing_fee_fields,
        "out_of_window_rows": out_of_window,
        "entry_ordering_violations": ordering_violations,
        "replay_event_sides": dict(side_counts),
        "entries_with_fee_fields": fee_rows,
        "replay_event_count_min": min(event_counts, default=0),
        "replay_event_count_max": max(event_counts, default=0),
        "replay_event_count_mean_all_entries": (sum(all_event_counts) / len(all_event_counts)) if all_event_counts else 0.0,
        "replay_event_count_mean_replayed_entries": (sum(event_counts) / len(event_counts)) if event_counts else 0.0,
        "entry_artifact_sha256": sha256(ns.entries),
        "replay_artifact_sha256": [sha256(p) for p in ns.replay],
        "per_entry": per_entry,
    }
    report["fail_closed"] = any((
        malformed_entries, duplicate_entry_signatures, malformed, unknown_targets,
        identity_mismatches, missing_fee_fields, out_of_window, duplicate_count, ordering_violations,
    ))
    ns.output.parent.mkdir(parents=True, exist_ok=True)
    ns.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "entries", "distinct_mints", "replay_events", "entries_with_replay_events",
        "entries_without_replay_events", "duplicate_replay_event_keys",
    )}, sort_keys=True))
    return 2 if report["fail_closed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
