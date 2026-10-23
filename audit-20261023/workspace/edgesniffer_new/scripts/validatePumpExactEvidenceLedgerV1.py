#!/usr/bin/env python3
"""Validate PEL1 evidence from decompressed frames without historical RPC."""

from __future__ import annotations

import argparse
import base64
import collections
import hashlib
import json
import pathlib
import sqlite3
import struct
import sys
from typing import Any, BinaryIO

from exactCurveEconomicsV1 import (
    Fees,
    SOL_QUOTE_MINTS,
    decode_trade_events,
    extract_pump_source_observations,
    fee_ceil,
    validate_actual_trade_transition,
)


MAGIC = b"PEL1FRM\0"
TRADE_EVENT_DISCRIMINATOR = bytes([189, 219, 127, 211, 78, 230, 97, 238])


def has_trade_event_payload(logs: list[str]) -> bool:
    for line in logs:
        marker = "Program data: "
        if marker not in line:
            continue
        try:
            payload = base64.b64decode(line.split(marker, 1)[1].strip(), validate=True)
        except Exception:
            continue
        if payload.startswith(TRADE_EVENT_DISCRIMINATOR):
            return True
    return False


def read_frames(stream: BinaryIO):
    if stream.read(len(MAGIC)) != MAGIC:
        raise ValueError("invalid PEL1 frame magic")
    while True:
        prefix = stream.read(8)
        if not prefix:
            return
        if prefix == MAGIC:
            continue
        if len(prefix) != 8:
            raise ValueError("truncated frame prefix")
        metadata_len, raw_len = struct.unpack("<II", prefix)
        body = stream.read(metadata_len + raw_len)
        digest = stream.read(32)
        if len(body) != metadata_len + raw_len or len(digest) != 32:
            raise ValueError("truncated frame")
        if hashlib.sha256(body).digest() != digest:
            raise ValueError("frame digest mismatch")
        yield json.loads(body[:metadata_len]), body[metadata_len:]


def event_fees(event) -> Fees:
    creator_total = event.creator_fee + event.cashback
    return Fees(
        protocol_bps=event.fee_basis_points,
        creator_bps=(
            0
            if creator_total == 0
            else event.creator_fee_basis_points + event.cashback_fee_basis_points
        ),
        lp_bps=0,
    )


def verify_event_fee_amounts(event) -> list[str]:
    edge_cases: list[str] = []
    expected_protocol = fee_ceil(event.sol_amount, event.fee_basis_points)
    expected_creator = fee_ceil(
        event.sol_amount,
        event.creator_fee_basis_points + event.cashback_fee_basis_points,
    )
    if event.fee != expected_protocol:
        if abs(event.fee - expected_protocol) == 1:
            edge_cases.append("exact_quote_fee_remainder")
        else:
            raise ValueError(f"protocol fee mismatch {event.fee}/{expected_protocol}")
    creator_total = event.creator_fee + event.cashback
    if creator_total != expected_creator:
        if creator_total == 0 and expected_creator > 0:
            edge_cases.append("configured_creator_fee_waived")
        else:
            raise ValueError(
                "creator/cashback fee mismatch "
                f"{creator_total}/{expected_creator}"
            )
    if event.buyback_fee > event.fee:
        raise ValueError("buyback exceeds protocol fee")
    if event.buyback_fee_basis_points:
        expected_buyback = event.fee * event.buyback_fee_basis_points // 10_000
        if event.buyback_fee != expected_buyback:
            raise ValueError(f"buyback mismatch {event.buyback_fee}/{expected_buyback}")
    return edge_cases


def validate(stream: BinaryIO) -> dict[str, Any]:
    # Raw finalized transaction payloads are large. Keep the causal join on disk
    # and decode one transaction at a time so validation RSS is partition-bounded.
    database = sqlite3.connect("")
    database.execute("PRAGMA temp_store=FILE")
    database.execute("PRAGMA journal_mode=OFF")
    database.execute("CREATE TABLE observed(signature TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
    database.execute("CREATE TABLE finalized(signature TEXT PRIMARY KEY, metadata TEXT NOT NULL, raw BLOB NOT NULL)")
    database.execute("CREATE TABLE blocks(slot INTEGER PRIMARY KEY, signatures TEXT NOT NULL)")
    database.execute(
        "CREATE TABLE reserve_events(mint TEXT NOT NULL, slot INTEGER NOT NULL, tx_index INTEGER NOT NULL, "
        "instruction_order INTEGER NOT NULL, signature TEXT NOT NULL, "
        "pre_vsol INTEGER NOT NULL, pre_vtoken INTEGER NOT NULL, pre_rsol INTEGER NOT NULL, pre_rtoken INTEGER NOT NULL, "
        "post_vsol INTEGER NOT NULL, post_vtoken INTEGER NOT NULL, post_rsol INTEGER NOT NULL, post_rtoken INTEGER NOT NULL)"
    )
    snapshots: set[str] = set()
    malformed_finality = 0
    for metadata, raw in read_frames(stream):
        kind = metadata.get("kind")
        signature = metadata.get("signature")
        if kind == "transaction" and signature:
            database.execute(
                "INSERT OR IGNORE INTO observed VALUES (?, ?)",
                (signature, json.dumps(metadata, separators=(",", ":"))),
            )
        elif kind == "rpc_finalized_transaction" and signature:
            try:
                envelope = json.loads(raw)
                result = envelope.get("result")
            except Exception:
                result = None
            if isinstance(result, dict):
                database.execute(
                    "INSERT OR REPLACE INTO finalized VALUES (?, ?, ?)",
                    (signature, json.dumps(metadata, separators=(",", ":")), raw),
                )
            else:
                malformed_finality += 1
        elif kind == "rpc_finalized_block" and metadata.get("slot") is not None:
            try:
                envelope = json.loads(raw)
                signatures = envelope.get("result", {}).get("signatures")
            except Exception:
                signatures = None
            if isinstance(signatures, list):
                database.execute(
                    "INSERT OR REPLACE INTO blocks VALUES (?, ?)",
                    (int(metadata["slot"]), json.dumps(signatures, separators=(",", ":"))),
                )
        elif kind == "rpc_account_snapshot" and metadata.get("accountPubkey"):
            snapshots.add(metadata["accountPubkey"])
    database.commit()

    observed_count = int(database.execute("SELECT COUNT(*) FROM observed").fetchone()[0])
    finalized_count = int(database.execute("SELECT COUNT(*) FROM finalized").fetchone()[0])
    orphan_finality = int(
        database.execute(
            "SELECT COUNT(*) FROM finalized f LEFT JOIN observed o USING(signature) WHERE o.signature IS NULL"
        ).fetchone()[0]
    )
    finalized_block_proofs = int(database.execute("SELECT COUNT(*) FROM blocks").fetchone()[0])

    counters: collections.Counter[str] = collections.Counter()
    fresh_mints: set[str] = set()
    fresh_mints_finalized: set[str] = set()
    tainted_mints: set[str] = set()
    taint_reasons: dict[str, set[str]] = collections.defaultdict(set)
    errors: list[dict[str, Any]] = []
    error_count = 0

    def record_error(value: dict[str, Any]) -> None:
        nonlocal error_count
        error_count += 1
        if len(errors) < 100:
            errors.append(value)

    def taint(mints: set[str] | list[str], reason: str) -> None:
        for mint in mints:
            tainted_mints.add(mint)
            taint_reasons[mint].add(reason)

    lookup = database.cursor()
    for signature, early_json in database.execute(
        "SELECT signature, metadata FROM observed ORDER BY rowid"
    ):
        early = json.loads(early_json)
        final_row = lookup.execute(
            "SELECT metadata, raw FROM finalized WHERE signature=?", (signature,)
        ).fetchone()
        if final_row is None:
            counters["missing_finality"] += 1
            continue
        counters["finalized"] += 1
        final_metadata = json.loads(final_row[0])
        tx = json.loads(final_row[1]).get("result")
        finalized_mints = set(final_metadata.get("discoveredMints") or [])
        canonical_index = final_metadata.get("transactionIndex")
        block_row = lookup.execute(
            "SELECT signatures FROM blocks WHERE slot=?", (int(tx.get("slot") or 0),)
        ).fetchone()
        block_signatures = json.loads(block_row[0]) if block_row else None
        if canonical_index is None or block_signatures is None:
            counters["missing_canonical_ordering"] += 1
            taint(finalized_mints, "missing_canonical_ordering")
        elif (
            int(canonical_index) >= len(block_signatures)
            or block_signatures[int(canonical_index)] != signature
        ):
            counters["canonical_ordering_proof_mismatch"] += 1
            taint(finalized_mints, "canonical_ordering_proof_mismatch")
        meta = tx.get("meta") or {}
        success = meta.get("err") is None
        early_success = early.get("successful")
        if early_success is not None and bool(early_success) != success:
            counters["success_failure_mismatch"] += 1
            record_error({"signature": signature, "type": "success_failure_mismatch"})
        kinds = set(early.get("pumpInstructionKinds") or [])
        discovered = set(early.get("discoveredMints") or [])
        if kinds.intersection({"CREATE", "CREATE_V2"}):
            fresh_mints.update(discovered)
            if success:
                fresh_mints_finalized.update(discovered)
        if not success:
            counters["failed_zero_state"] += 1
            if has_trade_event_payload(meta.get("logMessages") or []):
                counters["failed_with_trade_event"] += 1
                record_error({"signature": signature, "type": "failed_with_trade_event"})
            continue

        counters["successful"] += 1
        try:
            events = decode_trade_events(meta.get("logMessages") or [])
        except Exception as error:
            counters["unsupported_trade_event_schema"] += 1
            taint(finalized_mints, "unsupported_trade_event_schema")
            record_error(
                {
                    "signature": signature,
                    "type": "unsupported_trade_event_schema",
                    "detail": str(error),
                }
            )
            continue
        if not events:
            if kinds.intersection(
                {"BUY", "BUY_EXACT_SOL_IN", "BUY_V2", "BUY_EXACT_QUOTE_IN_V2", "SELL", "SELL_V2"}
            ):
                counters["successful_trade_missing_event"] += 1
                record_error({"signature": signature, "type": "successful_trade_missing_event"})
            continue

        sources = []
        for mint in sorted({event.mint for event in events}):
            try:
                sources.extend(
                    extract_pump_source_observations(tx, signature, mint, 0, "ledger_only")
                )
            except Exception as error:
                counters["unsupported_instruction_schema"] += 1
                record_error(
                    {
                        "signature": signature,
                        "mint": mint,
                        "type": "unsupported_instruction_schema",
                        "detail": str(error),
                    }
                )
        sources.sort(key=lambda source: source.instruction_index)
        unused_sources = list(sources)
        for event_order, event in enumerate(events):
            if event.mayhem_mode:
                counters["unsupported_mayhem_transition"] += 1
                taint([event.mint], "unsupported_mayhem_transition")
                continue
            if event.quote_mint not in SOL_QUOTE_MINTS:
                counters["unsupported_non_sol_quote_transition"] += 1
                taint([event.mint], "unsupported_non_sol_quote_transition")
                continue
            source_index = next(
                (
                    index
                    for index, source in enumerate(unused_sources)
                    if source.mint == event.mint and ((source.side == "buy") == event.is_buy)
                ),
                None,
            )
            if source_index is None:
                counters["event_instruction_pair_missing"] += 1
                taint([event.mint], "event_instruction_pair_missing")
                record_error(
                    {"signature": signature, "mint": event.mint, "type": "event_instruction_pair_missing"}
                )
                continue
            source = unused_sources.pop(source_index)
            try:
                for edge_case in verify_event_fee_amounts(event):
                    counters[edge_case] += 1
                validate_actual_trade_transition(source, event, event_fees(event))
            except Exception as error:
                counters["reserve_or_fee_mismatch"] += 1
                taint([event.mint], "reserve_or_fee_mismatch")
                record_error(
                    {
                        "signature": signature,
                        "mint": event.mint,
                        "type": "reserve_or_fee_mismatch",
                        "detail": str(error),
                        "mayhemMode": event.mayhem_mode,
                        "instructionKind": source.instruction.kind,
                        "instructionArg0": source.instruction.arg0,
                        "instructionArg1": source.instruction.arg1,
                        "eventSolAmount": event.sol_amount,
                        "eventTokenAmount": event.token_amount,
                        "eventProtocolBps": event.fee_basis_points,
                        "eventCreatorBps": event.creator_fee_basis_points,
                        "eventCashbackBps": event.cashback_fee_basis_points,
                    }
                )
                continue
            counters["admitted_transitions"] += 1
            database.execute(
                "INSERT INTO reserve_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.mint,
                    int(tx.get("slot") or early.get("slot") or 0),
                    int(canonical_index or 0),
                    source.instruction_index * 1000 + event_order,
                    signature,
                    event.pre_state.virtual_sol,
                    event.pre_state.virtual_token,
                    event.pre_state.real_sol,
                    event.pre_state.real_token,
                    event.post_state.virtual_sol,
                    event.post_state.virtual_token,
                    event.post_state.real_sol,
                    event.post_state.real_token,
                ),
            )
        if unused_sources:
            counters["successful_instructions_without_event"] += len(unused_sources)

    database.commit()
    continuity_checked = 0
    reserve_mints: set[str] = set()
    previous_mint = None
    previous_post = None
    previous_signature = None
    for row in database.execute(
        "SELECT mint,signature,pre_vsol,pre_vtoken,pre_rsol,pre_rtoken,post_vsol,post_vtoken,post_rsol,post_rtoken "
        "FROM reserve_events ORDER BY mint,slot,tx_index,instruction_order"
    ):
        mint, signature = row[0], row[1]
        pre_state = tuple(row[2:6])
        post_state = tuple(row[6:10])
        reserve_mints.add(mint)
        if mint == previous_mint and mint not in tainted_mints:
            continuity_checked += 1
            if previous_post != pre_state:
                counters["ordering_or_reserve_continuity_mismatch"] += 1
                taint([mint], "ordering_or_reserve_continuity_mismatch")
                record_error(
                    {
                        "mint": mint,
                        "type": "ordering_or_reserve_continuity_mismatch",
                        "previousSignature": previous_signature,
                        "currentSignature": signature,
                    }
                )
        previous_mint = mint
        previous_post = post_state
        previous_signature = signature

    fresh_with_snapshot = sum(1 for mint in fresh_mints_finalized if mint in snapshots)
    admitted_mints = {
        mint
        for mint in reserve_mints
        if mint not in tainted_mints
    }
    fresh_admitted_mints = admitted_mints.intersection(fresh_mints_finalized)
    result = {
        "schema": "PUMP_EXACT_EVIDENCE_LEDGER_V1_VALIDATION",
        "observedTransactions": observed_count,
        "finalizedTransactions": finalized_count,
        "orphanFinality": orphan_finality,
        "finalizedBlockProofs": finalized_block_proofs,
        "malformedFinality": malformed_finality,
        "counters": dict(sorted(counters.items())),
        "freshMintsObserved": len(fresh_mints),
        "freshMintsFinalizedSuccessful": len(fresh_mints_finalized),
        "freshMintsWithFinalizedSnapshot": fresh_with_snapshot,
        "mintsWithAdmittedTransitions": len(admitted_mints),
        "freshMintsWithAdmittedTransitions": len(fresh_admitted_mints),
        "explicitlyUnsupportedOrTaintedMints": len(tainted_mints),
        "freshMintsObservedValues": sorted(fresh_mints),
        "freshMintsFinalizedSuccessfulValues": sorted(fresh_mints_finalized),
        "freshMintsWithFinalizedSnapshotValues": sorted(
            mint for mint in fresh_mints_finalized if mint in snapshots
        ),
        "admittedMintsValues": sorted(admitted_mints),
        "freshMintsWithAdmittedTransitionsValues": sorted(fresh_admitted_mints),
        "taintedMintReasons": {
            mint: sorted(reasons) for mint, reasons in sorted(taint_reasons.items())
        },
        "reserveContinuityLinksChecked": continuity_checked,
        "zeroAdmittedReserveMismatch": True,
        "zeroAdmittedOrderingMismatch": True,
        "zeroSuccessFailureMismatch": counters["success_failure_mismatch"] == 0,
        "zeroCanonicalOrderingProofMismatch": (
            counters["missing_canonical_ordering"] == 0
            and counters["canonical_ordering_proof_mismatch"] == 0
        ),
        "allObservedFinalized": counters["missing_finality"] == 0,
        "errorCount": error_count,
        "errorsTruncated": error_count > len(errors),
        "errors": errors,
    }
    result["classification"] = (
        "LEDGER_ONLY_REPLAY_PASS"
        if result["zeroAdmittedReserveMismatch"]
        and result["zeroAdmittedOrderingMismatch"]
        and result["zeroSuccessFailureMismatch"]
        and result["zeroCanonicalOrderingProofMismatch"]
        and result["allObservedFinalized"]
        and counters["admitted_transitions"] > 0
        and len(fresh_mints_finalized) > 0
        and len(fresh_admitted_mints) > 0
        and fresh_with_snapshot == len(fresh_mints_finalized)
        else "LEDGER_ONLY_REPLAY_BLOCKER"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    result = validate(sys.stdin.buffer)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result["classification"] == "LEDGER_ONLY_REPLAY_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
