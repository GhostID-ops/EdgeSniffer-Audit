#!/usr/bin/env python3
"""One-shot, outcome-separated actionability evaluator for the sealed kEFi selection cohort.

This produces no PnL.  It only answers whether a fixed delayed entry and a
fixed later observed state exist under the exact-transition tape.  Economics
must consume its sealed assignments separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

try:
    import kefi_h3_pumpswap_mechanics_v1 as pumpswap_mechanics
    import kefi_pump_curve_mechanics_v1 as pump_curve_mechanics
except ModuleNotFoundError:
    from scripts import kefi_h3_pumpswap_mechanics_v1 as pumpswap_mechanics
    import kefi_pump_curve_mechanics_v1 as pump_curve_mechanics

SCHEMA = "KEFI_FROZEN_SELECTION_ACTIONABILITY_EVALUATOR_V1"
EXIT_AFTER_NS = 120_000_000_000
CONTROLS_PER_TARGET = 4


def stable(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as f:
        f.write(stable(value)); f.flush(); os.fsync(f.fileno())
    os.replace(temporary, path)


_TAPE_FIELD_ALIASES = {
    "sourceInstanceId": "source_instance_id",
    "continuityEpoch": "continuity_epoch",
    "durableOutputSequence": "durable_output_sequence",
    "receivedAtUnixNs": "received_at_unix_ns",
    "sourceFrameSha256": "source_frame_sha256",
    "logIndex": "log_index",
    "quoteMint": "quote_mint",
    "exactTransitionValidated": "exact_transition_validated",
    "solAmountRaw": "sol_amount_raw",
    "tokenAmountRaw": "token_amount_raw",
}


def normalize_tape_row(row: dict) -> dict:
    for camel, snake in _TAPE_FIELD_ALIASES.items():
        if snake not in row and camel in row:
            row[snake] = row[camel]
    return row


def read_jsonl(paths: list[Path]):
    for path in paths:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield normalize_tape_row(json.loads(line))


def snapshot_files(root: Path) -> list[Path]:
    files = sorted((root / "snapshots").glob("*.json"))
    if not files:
        raise RuntimeError("FAIL_CLOSED: no sealed snapshots")
    return files


def locate_target(snapshot: dict, rows: list[dict], venue: str) -> dict | None:
    """Locate the first public observation at the sealed decision boundary.

    The teacher transaction is intentionally absent from the public source, so
    its signature is not a valid join key here.  Admission is supplied by the
    sealed snapshot's timestamp, pool, and mint.  A missing public observation
    is an explicit no-entry outcome, not a reason to remove the snapshot from
    the denominator.
    """
    try:
        boundary = int(snapshot["teacher_received_at_unix_ns"])
        identity = str(
            snapshot["chosen_curve"] if venue == "pump-curve" and "chosen_curve" in snapshot
            else snapshot["chosen_pool"]
        )
        mint = str(snapshot["chosen_mint"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("FAIL_CLOSED: snapshot decision boundary is malformed") from error
    matches = [
        row for row in rows
        if int(row.get("received_at_unix_ns", -1)) >= boundary
        and str(row.get("curve" if venue == "pump-curve" else "pool")) == identity
        and str(row.get("mint")) == mint
        and str(row.get("side", "")).upper() == "BUY"
    ]
    if not matches:
        return None
    matches.sort(key=lambda row: (int(row["received_at_unix_ns"]), *row_key(row)))
    return matches[0]


def join_key(snapshot: dict, venue: str) -> tuple[str, str]:
    """Return the venue-specific identity plus mint used for the lookup."""
    try:
        identity = str(
            snapshot["chosen_curve"] if venue == "pump-curve" and "chosen_curve" in snapshot
            else snapshot["chosen_pool"]
        )
        return identity, str(snapshot["chosen_mint"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("FAIL_CLOSED: snapshot decision identity is malformed") from error


def row_join_key(row: dict, venue: str) -> tuple[str, str]:
    field = "curve" if venue == "pump-curve" else "pool"
    return str(row.get(field)), str(row.get("mint"))


def outcome(rows: list[dict], signal: dict | None, entry_delay_ns: int, mechanics,
            exit_after_ns: int = EXIT_AFTER_NS) -> dict:
    if signal is None:
        return {
            "status": "NO_ENTRY",
            "complete_actionable": False,
            "reason": "NO_PUBLIC_TARGET_OBSERVATION_AFTER_SNAPSHOT_BOUNDARY",
            "entry": None,
            "exit": None,
        }
    entry = mechanics.delayed_entry_state(rows, signal, entry_delay_ns)
    if entry["status"] != "TRADEABLE":
        return {
            "status": "NON_TRADEABLE",
            "complete_actionable": False,
            "reason": entry.get("reason"),
            "entry": entry,
            "exit": None,
        }
    entry_row = next(row for row in rows if tuple(row_key(row)) == tuple(entry["entry_event_key"]))
    mark = mechanics.observed_exit_mark(rows, entry_row, exit_after_ns)
    return {
        "status": "MARK_AVAILABLE" if mark["status"] == "MARK_AVAILABLE" else "NO_MARK",
        "complete_actionable": mark["status"] == "MARK_AVAILABLE",
        "reason": "" if mark["status"] == "MARK_AVAILABLE" else mark.get("reason"),
        "entry": entry,
        "exit": mark,
    }


def row_key(row: dict):
    index = row.get("event_index", row.get("log_index"))
    return (str(row["source_instance_id"]), int(row["continuity_epoch"]), int(row["durable_output_sequence"]), int(index), str(row["source_frame_sha256"]))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--snapshots", type=Path, required=True)
    p.add_argument("--tape", type=Path, required=True)
    p.add_argument("--entry-delay-ns", type=int, required=True)
    p.add_argument("--exit-after-ns", type=int, default=EXIT_AFTER_NS)
    p.add_argument("--venue", choices=("pump-curve", "pumpswap"), required=True)
    p.add_argument("--seed", required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    mechanics = pump_curve_mechanics if args.venue == "pump-curve" else pumpswap_mechanics
    if args.entry_delay_ns <= 0:
        raise SystemExit("FAIL_CLOSED: entry delay must be positive")
    if args.exit_after_ns <= 0:
        raise SystemExit("FAIL_CLOSED: exit horizon must be positive")
    if args.out.exists():
        raise SystemExit("FAIL_CLOSED: evaluator output already exists")
    chunks = sorted((args.tape / "chunks").glob("*.jsonl"))
    if not chunks or not (args.tape / "consumer-checkpoint.json").exists():
        raise SystemExit("FAIL_CLOSED: incomplete tape")
    rows = list(read_jsonl(chunks))
    rows.sort(key=lambda row: (int(row["received_at_unix_ns"]), *row_key(row)))
    # The tape is intentionally large.  Indexing by the immutable venue
    # identity and mint preserves the exact lookup semantics while avoiding a
    # full-tape scan for every sealed snapshot and every delayed mark.
    grouped_rows: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        grouped_rows.setdefault(row_join_key(row, args.venue), []).append(row)
    assignments = []
    for number, file in enumerate(snapshot_files(args.snapshots), 1):
        snapshot = json.loads(file.read_text())
        # Admission is defined by the sealed selection snapshot, before delayed
        # entry actionability is known.  Restricting this lookup to `valid`
        # would silently remove selected but non-tradeable targets from A_T_all.
        target_rows_for_snapshot = grouped_rows.get(join_key(snapshot, args.venue), [])
        target = locate_target(snapshot, target_rows_for_snapshot, args.venue)
        target_outcome = outcome(target_rows_for_snapshot, target, args.entry_delay_ns, mechanics, args.exit_after_ns)
        target_key = row_key(target) if target is not None else None
        assignments.append({
            "target_id": hashlib.sha256(stable({"snapshot": digest_path(file), "target": target_key})).hexdigest(),
            "snapshot_sha256": digest_path(file), "pool": snapshot["chosen_pool"], "mint": snapshot["chosen_mint"],
            "target": target, "target_complete_actionable": target_outcome["complete_actionable"],
            "target_status": target_outcome["status"], "target_reason": target_outcome["reason"],
            "target_entry": target_outcome["entry"], "target_exit": target_outcome["exit"],
            "chronological_block": number, "controls": [],
        })
    target_rows = [item["target"] for item in assignments if item["target"] is not None]
    controls = mechanics.deterministic_controls(target_rows, rows, bucket_ns=mechanics.CONTROL_BUCKET_NS, per_target=CONTROLS_PER_TARGET, seed=args.seed)
    for item in assignments:
        if item["target"] is None:
            continue
        chosen = controls.get(row_key(item["target"]), [])
        rendered = []
        for control in chosen:
            control_rows = grouped_rows.get(row_join_key(control, args.venue), [])
            control_outcome = outcome(control_rows, control, args.entry_delay_ns, mechanics, args.exit_after_ns)
            rendered.append({"row": control, "complete_actionable": control_outcome["complete_actionable"], "reason": control_outcome["reason"], "entry": control_outcome["entry"], "exit": control_outcome["exit"]})
        item["controls"] = rendered
    target_status_counts = {}
    for item in assignments:
        status = item["target_status"]
        target_status_counts[status] = target_status_counts.get(status, 0) + 1
    payload = {
        "schema": SCHEMA, "venue": args.venue, "entry_delay_ns": args.entry_delay_ns, "exit_after_ns": args.exit_after_ns,
        "controls_per_target": CONTROLS_PER_TARGET, "seed": args.seed,
        "snapshots_root": str(args.snapshots), "tape_root": str(args.tape),
        "tape_checkpoint_sha256": digest_path(args.tape / "consumer-checkpoint.json"),
        "pnl_accessed": False, "target_status_counts": target_status_counts,
        "assignments": assignments,
    }
    atomic(args.out, payload)
    print(json.dumps({"status": "ASSIGNMENTS_SEALED", "targets": len(assignments), "output_sha256": digest_path(args.out)}, sort_keys=True))


if __name__ == "__main__":
    main()
