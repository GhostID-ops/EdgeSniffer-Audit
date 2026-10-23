#!/usr/bin/env python3
"""Reconcile current-codec target signatures with the finalized RPC source tape."""

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decoder-report", required=True, type=Path)
    parser.add_argument("--source-jsonl", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    decoder = json.loads(args.decoder_report.read_text(encoding="utf-8"))
    decoder_targets = {item["signature"]: item for item in decoder["target_signature_samples"]}
    source_by_signature = {}
    source_max_slot = None
    with args.source_jsonl.open(encoding="utf-8") as source_file:
        for line in source_file:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            slot = row.get("slot")
            if isinstance(slot, int):
                source_max_slot = slot if source_max_slot is None else max(source_max_slot, slot)
            signature = row.get("signature")
            transaction = row.get("transaction")
            if not signature and isinstance(transaction, dict):
                signatures = transaction.get("signatures")
                if isinstance(signatures, list) and signatures:
                    signature = signatures[0]
            if signature:
                source_by_signature[signature] = slot

    matches = []
    not_yet_in_source = []
    slot_mismatches = []
    for signature, decoded in decoder_targets.items():
        source_slot = source_by_signature.get(signature)
        item = {
            "signature": signature,
            "decoder_slot": decoded["slot"],
            "source_slot": source_slot,
            "fee_payer_match": decoded["fee_payer_match"],
        }
        if source_slot is None:
            not_yet_in_source.append(item)
        elif source_slot != decoded["slot"]:
            slot_mismatches.append(item)
        else:
            matches.append(item)

    result = {
        "tool": "reconcile-allenhark-current-codec-v1",
        "decoder_report": str(args.decoder_report),
        "source_jsonl": str(args.source_jsonl),
        "decoder_input_sha256": decoder["input_sha256"],
        "decoder_slots": decoder["slots"],
        "decoder_target_count": len(decoder_targets),
        "source_max_slot": source_max_slot,
        "rpc_source_exact_matches": len(matches),
        "decoder_targets_not_yet_in_source": len(not_yet_in_source),
        "slot_mismatches": len(slot_mismatches),
        "exact_matches": matches,
        "not_yet_in_source": not_yet_in_source,
        "slot_mismatch_rows": slot_mismatches,
        "identity_gate": "PASS_PARTIAL_EXACT_NO_SLOT_MISMATCH" if matches and not slot_mismatches else "PENDING",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
