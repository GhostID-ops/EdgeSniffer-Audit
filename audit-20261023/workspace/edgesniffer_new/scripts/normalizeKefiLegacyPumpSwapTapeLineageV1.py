#!/usr/bin/env python3
"""Add deterministic offline lineage to legacy exact PumpSwap tape rows.

The running receive-only tape collector predates the durable lineage fields
used by the causal discovery consumer.  This adapter preserves every exact
economic field and only adds a deterministic source identity, continuity
epoch, and file-order sequence.  It is research-only and never changes the
collector's source file or claims live-parity coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REQUIRED = {
    "lp_fee_bps", "protocol_fee_bps", "coin_creator_bps", "virtual_quote_reserves",
    "pool_base_raw", "pool_quote_raw", "pool_quote_delta", "base_amount",
    "received_at_unix_ns", "exact_transition_validated", "quote_mint", "mint",
    "side", "source_frame_sha256", "event_index",
}
WSOL = "So11111111111111111111111111111111111111112"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(input_path: Path, output_path: Path) -> dict[str, object]:
    input_sha = file_sha256(input_path)
    source_instance_id = "legacy-tape-" + input_sha[:32]
    accepted = 0
    rejected = 0
    with input_path.open(encoding="utf-8") as source, output_path.open("w", encoding="utf-8") as target:
        for line_number, line in enumerate(source):
            if not line.strip():
                continue
            row = json.loads(line)
            valid = (
                REQUIRED.issubset(row)
                and row.get("exact_transition_validated") is True
                and row.get("quote_mint") == WSOL
                and row.get("mint") != WSOL
                and row.get("side") in {"BUY", "SELL"}
            )
            if not valid:
                rejected += 1
                continue
            normalized = dict(row)
            normalized["source_instance_id"] = source_instance_id
            normalized["continuity_epoch"] = 1
            normalized["durable_output_sequence"] = accepted
            target.write(json.dumps(normalized, sort_keys=True) + "\n")
            accepted += 1
    return {
        "schema": "KEFI_LEGACY_PUMPSWAP_TAPE_LINEAGE_NORMALIZATION_V1",
        "input_path": str(input_path),
        "input_sha256": input_sha,
        "output_path": str(output_path),
        "output_sha256": file_sha256(output_path),
        "accepted_rows": accepted,
        "rejected_rows": rejected,
        "source_instance_id": source_instance_id,
        "continuity_epoch": 1,
        "lineage_is_synthetic": True,
        "read_only_source": True,
        "live_parity_claim": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = normalize(args.input, args.output)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "LEGACY_TAPE_LINEAGE_NORMALIZED_READ_ONLY",
        "accepted_rows": report["accepted_rows"],
        "rejected_rows": report["rejected_rows"],
        "lineage_is_synthetic": report["lineage_is_synthetic"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
