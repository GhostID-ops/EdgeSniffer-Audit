#!/usr/bin/env python3
"""Outcome-blind duplicate provenance audit for a void qualification cohort."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from collections import defaultdict
from pathlib import Path


SCHEMA = "KEFI_PRE_H3_DUPLICATE_FORENSICS_V1"


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as handle:
        handle.write(canonical(payload) + "\n"); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_indexer(path: Path):
    spec = importlib.util.spec_from_file_location("kefi_forensic_index", path)
    if not spec or not spec.loader:
        raise RuntimeError("FAIL_CLOSED: cannot load canonical index module")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, action="append", required=True)
    parser.add_argument("--indexer", type=Path, required=True)
    parser.add_argument("--out-summary", type=Path, required=True)
    parser.add_argument("--out-details", type=Path, required=True)
    args = parser.parse_args()
    indexer = load_indexer(args.indexer)
    occurrences: dict[tuple[str, int], list[dict]] = defaultdict(list)
    receipt_bindings = []
    for receipt_path in sorted(args.receipt):
        receipt = json.loads(receipt_path.read_text())
        rows_path = Path(str(receipt.get("market_rows_path", "")))
        if not rows_path.is_file() or sha256(rows_path) != receipt.get("market_rows_sha256"):
            raise SystemExit(f"FAIL_CLOSED: receipt/rows hash mismatch {receipt_path}")
        binding = {"receipt_path": str(receipt_path), "receipt_sha256": sha256(receipt_path),
                   "window_id": receipt.get("window_id"), "market_rows_path": str(rows_path),
                   "market_rows_sha256": sha256(rows_path), "decoder_sha256": receipt.get("decoder", {}).get("decoder_sha256"),
                   "source_partition_refs": receipt.get("source_partition_refs", [])}
        receipt_bindings.append(binding)
        for line_number, line in enumerate(rows_path.read_text().splitlines(), start=1):
            if not line:
                continue
            row = json.loads(line)
            try:
                identity = (str(row["source_frame_sha256"]), int(row["event_index"]))
                order = {key: row[key] for key in ("source_instance_id", "continuity_epoch", "durable_output_sequence", "event_index", "source_frame_sha256")}
            except (KeyError, TypeError, ValueError) as error:
                raise SystemExit(f"FAIL_CLOSED: invalid canonical identity {rows_path}:{line_number}") from error
            intrinsic = indexer.intrinsic_event(row)
            exposure = {key: row[key] for key in sorted(indexer.WINDOW_SCOPED_FIELDS & row.keys())}
            occurrences[identity].append({"receipt_path": str(receipt_path), "receipt_sha256": binding["receipt_sha256"],
                                           "window_id": receipt.get("window_id"), "line_number": line_number,
                                           "source_order": order, "intrinsic_sha256": hashlib.sha256(canonical(intrinsic).encode()).hexdigest(),
                                           "full_row_sha256": hashlib.sha256(canonical(row).encode()).hexdigest(),
                                           "exposure": exposure})
    duplicate_items = []
    conflicting = 0
    same_receipt = cross_receipt = 0
    for identity, values in sorted(occurrences.items()):
        if len(values) < 2:
            continue
        intrinsic_hashes = sorted({value["intrinsic_sha256"] for value in values})
        receipts = sorted({value["receipt_path"] for value in values})
        if len(intrinsic_hashes) != 1:
            conflicting += 1
        if len(receipts) == 1:
            same_receipt += 1
        else:
            cross_receipt += 1
        duplicate_items.append({"canonical_event_identity": {"source_frame_sha256": identity[0], "event_index": identity[1]},
                                "occurrences": values, "intrinsic_sha256_values": intrinsic_hashes,
                                "classification": "CONFLICTING_INTRINSIC" if len(intrinsic_hashes) != 1 else ("DUPLICATE_WITHIN_RECEIPT" if len(receipts) == 1 else "IDENTICAL_CROSS_WINDOW_OVERLAP")})
    temporary = args.out_details.with_suffix(args.out_details.suffix + ".tmp")
    args.out_details.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w") as handle:
        for item in duplicate_items:
            handle.write(canonical(item) + "\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, args.out_details)
    summary = {"schema": SCHEMA, "outcome_blind": True, "pnl_accessed": False,
               "receipts": receipt_bindings, "input_occurrences": sum(len(value) for value in occurrences.values()),
               "unique_canonical_identities": len(occurrences), "repeated_canonical_identities": len(duplicate_items),
               "duplicates_within_one_receipt": same_receipt, "identical_cross_receipt_overlap_identities": cross_receipt,
               "conflicting_intrinsic_identities": conflicting, "details_path": str(args.out_details),
               "details_sha256": sha256(args.out_details),
               "root_cause": "IDENTICAL_CROSS_WINDOW_OVERLAP" if duplicate_items and not same_receipt and not conflicting else "MIXED_OR_CONFLICTING_REQUIRES_FAIL_CLOSED"}
    atomic(args.out_summary, summary)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
