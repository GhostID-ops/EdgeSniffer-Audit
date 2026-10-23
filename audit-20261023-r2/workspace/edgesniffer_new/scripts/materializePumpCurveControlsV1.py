#!/usr/bin/env python3
"""Materialize same-period Pump-curve controls from dedicated causal windows."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from materializeWalletRouteCensusV1 import curve_events_with_extension


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_anchor_index(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Load only compact anchor metadata and signature ownership.

    The prospective window tape contains large raw transactions. Keeping the
    complete tape in two in-memory dictionaries caused the control refresher
    to reach 15+ GB and get OOM-killed. The control pass only needs compact
    anchor fields plus the signatures that can fall inside each window.
    """
    anchors: dict[str, dict[str, Any]] = {}
    market_to_anchors: dict[str, list[str]] = {}
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("record_type") != "ANCHOR_WINDOW":
                continue
            anchor_id = str(row.get("anchor_id") or "")
            if not anchor_id:
                continue
            anchors[anchor_id] = {
                "anchor_slot": int(row.get("anchor_slot") or 0),
                "anchor_time": int(row.get("anchor_block_time") or 0),
                "anchor_sig": str(row.get("anchor_signature") or ""),
                "mint": str(row.get("mint") or ""),
                "quote_mint": str(row.get("quote_mint") or ""),
                "wallet": str(row.get("wallet") or ""),
                "route": row.get("route"),
            }
            for signature in row.get("window_signatures") or []:
                signature = str(signature)
                if signature:
                    market_to_anchors.setdefault(signature, []).append(anchor_id)
    return anchors, market_to_anchors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--control-seconds", type=int, default=5)
    args = parser.parse_args()
    anchors, market_to_anchors = load_anchor_index(args.windows)
    controls_count = 0
    seen = set()
    temporary_records = args.out.with_suffix(args.out.suffix + ".records.tmp")
    with temporary_records.open("w", encoding="utf-8") as records_file:
        market_transactions_loaded = 0
        with args.windows.open(encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("record_type") != "MARKET_TRANSACTION":
                    continue
                market_transactions_loaded += 1
                signature = str(row.get("signature") or "")
                for anchor_id in market_to_anchors.get(signature, ()):
                    window = anchors.get(anchor_id)
                    if not window or signature == window["anchor_sig"]:
                        continue
                    control_time = int(row.get("block_time") or 0)
                    anchor_time = window["anchor_time"]
                    if not control_time or (anchor_time and not (anchor_time + 1 <= control_time <= anchor_time + args.control_seconds)):
                        continue
                    try:
                        events, variant = curve_events_with_extension(row.get("raw_transaction") or {})
                    except Exception:
                        continue
                    for event_index, event in enumerate(events):
                        key = (anchor_id, signature, event_index)
                        if key in seen or event.mint != window["mint"] or event.quote_mint != window["quote_mint"] or not event.is_buy or event.user == window["wallet"]:
                            continue
                        seen.add(key)
                        record = {
                    "schema": "WALLET_PUMP_CURVE_SAME_PERIOD_CONTROL_V1",
                    "record_type": "CONTROL_SAME_PERIOD",
                    "control_id": f"{row.get('signature')}:{event_index}",
                    "anchor_id": anchor_id,
                    "anchor_signature": window["anchor_sig"],
                    "anchor_slot": window["anchor_slot"],
                    "control_signature": row.get("signature"),
                    "control_slot": row.get("slot"),
                    "control_block_time": row.get("block_time"),
                    "wallet": window["wallet"],
                    "control_user": event.user,
                    "mint": window["mint"],
                    "quote_mint": window["quote_mint"],
                    "route": window["route"],
                    "decoder_variant": variant,
                    "event_index": event_index,
                    "event": {
                        "mint": event.mint,
                        "sol_amount": event.sol_amount,
                        "token_amount": event.token_amount,
                        "is_buy": event.is_buy,
                        "user": event.user,
                        "timestamp": event.timestamp,
                        "post_state": event.post_state.__dict__,
                        "fee_basis_points": event.fee_basis_points,
                        "creator_fee_basis_points": event.creator_fee_basis_points,
                        "quote_mint": event.quote_mint,
                    },
                    "raw_transaction": row.get("raw_transaction"),
                        }
                        records_file.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                        controls_count += 1
    windows_sha256 = sha256_file(args.windows)
    result = {
        "schema": "WALLET_PUMP_CURVE_CONTROLS_V1",
        "read_only": True,
        "control_scope": f"same finalized market period (+1s through +{args.control_seconds}s), same Pump bonding-curve mint and quote mint, non-wallet BUY",
        "windows_loaded": len(anchors),
        "market_transactions_loaded": market_transactions_loaded,
        "controls": controls_count,
        "windows_sha256": windows_sha256,
        "policy_frozen": False,
        "trading_enabled": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = args.out.with_suffix(args.out.suffix + ".tmp")
    with temporary_output.open("w", encoding="utf-8") as output:
        prefix = json.dumps(result, sort_keys=True, separators=(",", ":"))[:-1]
        output.write(prefix + ',"records":[')
        first = True
        with temporary_records.open(encoding="utf-8") as records_file:
            for line in records_file:
                if not first:
                    output.write(",")
                output.write(line.strip())
                first = False
        output.write("]}\n")
    temporary_output.replace(args.out)
    temporary_records.unlink(missing_ok=True)
    print(json.dumps({"status": "PUMP_CURVE_CONTROLS_COMPLETE", "windows_loaded": len(anchors), "market_transactions_loaded": market_transactions_loaded, "controls": controls_count}, sort_keys=True))


if __name__ == "__main__":
    main()
