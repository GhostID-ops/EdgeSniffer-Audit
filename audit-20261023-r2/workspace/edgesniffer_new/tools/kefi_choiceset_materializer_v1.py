#!/usr/bin/env python3
"""Emit bounded, teacher-independent kEFi decision-time choice sets.

This tool deliberately refuses selected-centred promotion inputs.  It consumes
an already-canonical *independent* public-event stream and compact teacher
event references, then snapshots public pool state immediately before each
teacher event.  It performs no ranking, economics, or PRE_H3 access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


SCHEMA = "KEFI_CHOICESET_MATERIALIZER_V1"
MAX_SELECTIONS = 150
MAX_DERIVED_BYTES = 100 * 1024 * 1024
REQUIRED_EVENT_FIELDS = {
    "source_frame_sha256", "event_index", "received_at_unix_ns",
    "source_instance_id", "continuity_epoch", "durable_output_sequence",
    "pool", "mint", "side", "pool_quote_raw", "pool_base_raw",
}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(canonical(value) + "\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def event_key(row: dict) -> tuple[str, int]:
    return str(row["source_frame_sha256"]), int(row["event_index"])


def order_key(row: dict) -> tuple[str, int, int, int, str]:
    try:
        value = (
            str(row["source_instance_id"]), int(row["continuity_epoch"]),
            int(row["durable_output_sequence"]), int(row["event_index"]),
            str(row["source_frame_sha256"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("FAIL_CLOSED: invalid canonical source order") from error
    if not value[0] or value[1] < 0 or value[2] <= 0 or value[3] < 0 or not value[4]:
        raise RuntimeError("FAIL_CLOSED: invalid canonical source order")
    return value


def load_jsonl(path: Path) -> list[dict]:
    """Read canonical JSONL without silently expanding a raw source archive.

    The new pool-state feed is Zstd-compressed on disk.  We deliberately use
    the system decoder only for a bounded materialization invocation; absence
    of the decoder is a fail-closed integration error rather than a reason to
    fall back to a different or selected-centred input.
    """
    rows = []
    if path.suffix == ".zst":
        try:
            decoded = subprocess.run(
                ["zstd", "-q", "-dc", str(path)],
                check=True, capture_output=True, text=True,
            ).stdout.splitlines()
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise RuntimeError("FAIL_CLOSED: zstd canonical input cannot be decoded") from error
    else:
        decoded = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(decoded, start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"FAIL_CLOSED: invalid JSONL {path}:{number}") from error
    return rows


def validate_manifest(path: Path, events_path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "KEFI_CHOICESET_CANONICAL_INPUT_V1":
        raise RuntimeError("FAIL_CLOSED: unsupported canonical input manifest")
    if manifest.get("teacher_independent") is not True:
        raise RuntimeError("FAIL_CLOSED: canonical source is not teacher-independent")
    if manifest.get("selected_centred") is not False:
        raise RuntimeError("FAIL_CLOSED: selected-centred source rejected")
    if manifest.get("all_supported_pools_covered") is not True:
        raise RuntimeError("FAIL_CLOSED: source does not cover all supported pools")
    if Path(str(manifest.get("events_path", ""))).resolve() != events_path.resolve():
        raise RuntimeError("FAIL_CLOSED: manifest event path mismatch")
    if manifest.get("events_sha256") != sha256(events_path):
        raise RuntimeError("FAIL_CLOSED: manifest event hash mismatch")
    return manifest


@dataclass(frozen=True)
class PoolState:
    first_seen_ns: int
    last_event: dict


def eligible(state: PoolState) -> bool:
    row = state.last_event
    try:
        return bool(str(row["pool"])) and bool(str(row["mint"])) and \
            int(row["pool_quote_raw"]) > 0 and int(row["pool_base_raw"]) > 0 and \
            bool(str(row.get("layout", "PUMPSWAP_SUPPORTED")))
    except (KeyError, TypeError, ValueError):
        return False


def compact_state(state: PoolState) -> dict:
    row = state.last_event
    return {
        "pool": str(row["pool"]), "mint": str(row["mint"]),
        "first_public_observation_unix_ns": state.first_seen_ns,
        "pool_age_ns": int(row["received_at_unix_ns"]) - state.first_seen_ns,
        "pool_quote_raw": int(row["pool_quote_raw"]),
        "pool_base_raw": int(row["pool_base_raw"]),
        "fee_tuple": row.get("fee_tuple"), "layout": row.get("layout", "PUMPSWAP_SUPPORTED"),
        "last_public_event_key": list(event_key(row)),
        "last_public_order": list(order_key(row)),
    }


def build_choice_sets(events: list[dict], teacher_events: list[dict]) -> list[dict]:
    """Return deterministic choice sets without consulting future rows."""
    if len(teacher_events) > MAX_SELECTIONS:
        raise RuntimeError(f"FAIL_CLOSED: teacher selection bound exceeds {MAX_SELECTIONS}")
    for row in events:
        missing = REQUIRED_EVENT_FIELDS - row.keys()
        if missing:
            raise RuntimeError(f"FAIL_CLOSED: canonical event missing {sorted(missing)}")
    ordered = sorted(events, key=order_key)
    if ordered != events or len({event_key(row) for row in events}) != len(events):
        raise RuntimeError("FAIL_CLOSED: canonical events not strictly unique ordered")
    teacher_by_key = {event_key(row): row for row in teacher_events}
    if len(teacher_by_key) != len(teacher_events):
        raise RuntimeError("FAIL_CLOSED: duplicate teacher selection reference")
    prior_wallet_mints: set[str] = set()
    states: dict[tuple[str, str], PoolState] = {}
    outputs: list[dict] = []
    for event in ordered:
        key = event_key(event)
        teacher = teacher_by_key.get(key)
        if teacher is not None:
            if str(teacher.get("mint")) != str(event["mint"]) or str(teacher.get("pool")) != str(event["pool"]):
                raise RuntimeError("FAIL_CLOSED: teacher selection identity mismatch")
            state_key = (str(event["pool"]), str(event["mint"]))
            chosen = states.get(state_key)
            candidates = [compact_state(value) for value in states.values() if eligible(value)]
            candidates.sort(key=lambda row: (row["pool"], row["mint"]))
            chosen_ids = {(row["pool"], row["mint"]) for row in candidates}
            if chosen is None or state_key not in chosen_ids or len(candidates) <= 1:
                outputs.append({"teacher_event_key": list(key), "status": "REJECTED_NO_VALID_CHOICE_SET"})
            else:
                new = [row for row in candidates if row["mint"] not in prior_wallet_mints]
                outputs.append({
                    "schema": SCHEMA, "teacher_event_key": list(key),
                    "teacher_order": list(order_key(event)), "chosen_pool": state_key[0], "chosen_mint": state_key[1],
                    "status": "VALID", "ALL_ELIGIBLE_POOLS": candidates,
                    "NEW_TO_OBSERVED_WALLET": new,
                })
            prior_wallet_mints.add(str(event["mint"]))
        state_key = (str(event["pool"]), str(event["mint"]))
        states[state_key] = PoolState(states[state_key].first_seen_ns if state_key in states else int(event["received_at_unix_ns"]), event)
    unseen = set(teacher_by_key) - {event_key(row) for row in events}
    if unseen:
        raise RuntimeError("FAIL_CLOSED: teacher selection not present in canonical stream")
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical-events", type=Path, required=True)
    parser.add_argument("--canonical-manifest", type=Path, required=True)
    parser.add_argument("--teacher-events", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    if args.out_root.exists():
        raise RuntimeError("FAIL_CLOSED: output root already exists")
    manifest = validate_manifest(args.canonical_manifest, args.canonical_events)
    events, teachers = load_jsonl(args.canonical_events), load_jsonl(args.teacher_events)
    choices = build_choice_sets(events, teachers)
    args.out_root.mkdir(parents=True)
    artifact = args.out_root / "choice-sets.jsonl"
    with artifact.open("w", encoding="utf-8") as handle:
        for row in choices:
            handle.write(canonical(row) + "\n")
        handle.flush(); os.fsync(handle.fileno())
    valid = [row for row in choices if row["status"] == "VALID"]
    manifest_out = {
        "schema": SCHEMA + "_MANIFEST_V1", "canonical_manifest_sha256": sha256(args.canonical_manifest),
        "canonical_events_sha256": sha256(args.canonical_events), "teacher_events_sha256": sha256(args.teacher_events),
        "choice_sets_sha256": sha256(artifact), "teacher_decisions": len(teachers),
        "valid_choice_sets": len(valid), "candidate_rows": sum(len(row["ALL_ELIGIBLE_POOLS"]) for row in valid),
        "derived_bytes": artifact.stat().st_size, "source_bytes_read": args.canonical_events.stat().st_size,
        "runtime_seconds": round(time.monotonic() - started, 6), "source_instance": manifest.get("source_instance"),
        "continuity_epoch": manifest.get("continuity_epoch"), "outcome_blind": True, "pnl_accessed": False,
    }
    if manifest_out["derived_bytes"] > MAX_DERIVED_BYTES:
        raise RuntimeError("FAIL_CLOSED: compact choice-set artifact exceeds 100 MiB")
    atomic_json(args.out_root / "manifest.json", manifest_out)
    print(canonical(manifest_out))


if __name__ == "__main__":
    main()
