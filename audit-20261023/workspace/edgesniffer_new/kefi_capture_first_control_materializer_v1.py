#!/usr/bin/env python3
"""Materialize sealed capture-first windows into a verified control-read surface.

This is deliberately outcome-blind. It never scores, fits, or evaluates the
fingerprint; it verifies and decodes only windows that the collector already
sealed with a COMPLETE promotion receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


SCHEMA = "KEFI_CAPTURE_FIRST_CONTROL_MATERIALIZER_V1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def complete_signatures(root: Path) -> set[str]:
    result: set[str] = set()
    for receipt in (root / "promotion-results").glob("*.json"):
        payload = json.loads(receipt.read_text())
        if payload.get("status") == "COMPLETE":
            result.add(str(payload["signature"]))
    return result


def windows(root: Path, completed: set[str]) -> list[tuple[str, Path, dict]]:
    result: list[tuple[str, Path, dict]] = []
    for manifest_path in sorted((root / "shared-windows").glob("*/manifest.json")):
        payload = json.loads(manifest_path.read_text())
        if payload.get("schema") != "TRACKED_WALLET_SHARED_BURST_WINDOW_V1":
            continue
        candidates = payload.get("candidates", [])
        signatures = {str(candidate.get("signature", "")) for candidate in candidates}
        if not signatures or not signatures.issubset(completed):
            continue
        window = manifest_path.parent
        if not list(window.glob("partitions/**/manifest.json")):
            continue
        result.append((window.name, window, payload))
    return result


def decode_window(decoder: Path, window_id: str, window: Path, output: Path) -> dict:
    partition_manifests = sorted(window.glob("partitions/**/manifest.json"))
    keys = [manifest.parent.relative_to(window / "partitions").as_posix() for manifest in partition_manifests]
    if not keys:
        raise RuntimeError(f"no sealed partitions in {window}")
    with tempfile.TemporaryDirectory(prefix="kefi-capture-first-control-") as temporary:
        temporary_root = Path(temporary)
        expected = temporary_root / "data" / "source-spool"
        expected.mkdir(parents=True)
        os.symlink(window / "partitions", expected / "partitions")
        rows_tmp = output.with_suffix(".jsonl.tmp")
        summary_tmp = output.with_suffix(".summary.json.tmp")
        subprocess.run(
            [str(decoder), "--unified-market", str(temporary_root), str(rows_tmp), str(summary_tmp), *keys],
            check=True,
            capture_output=True,
            text=True,
        )
        summary = json.loads(summary_tmp.read_text())
        if summary.get("source_frames_scanned", 0) <= 0:
            raise RuntimeError(f"decoder emitted no source frames for {window_id}")
        os.replace(rows_tmp, output)
        os.replace(summary_tmp, output.with_suffix(".summary.json"))
    return summary


def build_deduplicated_market(out: Path) -> tuple[int, int, str]:
    """Merge overlapping sealed windows by canonical transaction/event identity.

    Overlap is expected because every teacher window includes pre-entry history.
    A conflicting duplicate is evidence corruption, not an excuse to choose one.
    """
    events: dict[tuple[str, int], str] = {}
    duplicates = 0
    for rows_path in sorted((out / "market-rows").glob("*.jsonl")):
        with rows_path.open() as handle:
            for line in handle:
                row = json.loads(line)
                key = (str(row["candidate_signature"]), int(row["event_index"]))
                canonical = json.dumps(row, sort_keys=True, separators=(",", ":"))
                previous = events.get(key)
                if previous is None:
                    events[key] = canonical
                elif previous != canonical:
                    raise RuntimeError(f"FAIL_CLOSED: conflicting overlap for {key[0]}:{key[1]}")
                else:
                    duplicates += 1
    ordered = sorted(
        (json.loads(value) for value in events.values()),
        key=lambda row: (int(row["received_at_unix_ns"]), int(row["slot"]), int(row["event_index"]), str(row["candidate_signature"])),
    )
    path = out / "market-deduplicated.jsonl"
    temporary = path.with_suffix(".jsonl.tmp")
    with temporary.open("w") as handle:
        for row in ordered:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)
    return len(ordered), duplicates, sha256(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--decoder", type=Path, required=True)
    parser.add_argument("--max-windows", type=int, default=0)
    parser.add_argument("--not-before-window-id", default="")
    args = parser.parse_args()

    if not args.decoder.is_file():
        raise SystemExit("FAIL_CLOSED: decoder missing")
    completed = complete_signatures(args.root)
    eligible = windows(args.root, completed)
    if args.not_before_window_id:
        eligible = [entry for entry in eligible if entry[0] >= args.not_before_window_id]
    if args.max_windows:
        eligible = eligible[: args.max_windows]
    state_path = args.out / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"processed": []}
    processed = set(state["processed"])
    results: list[dict] = []
    for window_id, window, manifest in eligible:
        if window_id in processed:
            continue
        destination = args.out / "market-rows" / f"{window_id}.jsonl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        summary = decode_window(args.decoder, window_id, window, destination)
        receipt = {
            "schema": SCHEMA + "_WINDOW_RECEIPT_V1",
            "window_id": window_id,
            "window_manifest_sha256": sha256(window / "manifest.json"),
            "candidate_signatures": sorted(str(c["signature"]) for c in manifest["candidates"]),
            "market_rows_path": str(destination),
            "market_rows_sha256": sha256(destination),
            "decoder_summary": summary,
            "outcome_blind": True,
            "pnl_accessed": False,
        }
        atomic_json(args.out / "receipts" / f"{window_id}.json", receipt)
        processed.add(window_id)
        state = {"schema": SCHEMA + "_STATE_V1", "processed": sorted(processed), "outcome_blind": True}
        atomic_json(state_path, state)
        results.append(receipt)
    unique_rows, duplicate_rows, deduplicated_sha256 = build_deduplicated_market(args.out)
    atomic_json(args.out / "checkpoint.json", {
        "schema": SCHEMA + "_CHECKPOINT_V1",
        "eligible_windows": len(eligible),
        "processed_windows": len(processed),
        "new_windows": len(results),
        "deduplicated_market_rows": unique_rows,
        "overlap_duplicates_verified_identical": duplicate_rows,
        "deduplicated_market_sha256": deduplicated_sha256,
        "outcome_blind": True,
        "pnl_accessed": False,
    })
    print(json.dumps({"processed": len(processed), "new": len(results)}, sort_keys=True))


if __name__ == "__main__":
    main()
