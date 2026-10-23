#!/usr/bin/env python3
"""Fail-closed ingestion of retained Yellowstone frames into the 2CQ state plane.

The raw Yellowstone receipt remains the only arrival-clock source. Finality and
canonical ordering are separately proven from the closed exact partition before
any normalized transaction can enter the rebuildable state plane.
"""

from __future__ import annotations

import argparse
import base64
import collections
import datetime as dt
import json
import mmap
import os
import pathlib
import sqlite3
import subprocess
import tempfile
from typing import Any, Iterable

import twoCqIndexedCausalStatePlaneV1 as plane
import twoCqProspectiveSymmetricChoiceMaterializerV1 as legacy
import twoCqChoiceSetCoverageRepairV1 as coverage
import twoCqDecisionSystemDecompositionV1 as decision
from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "YELLOWSTONE_CANONICAL_STATE_PLANE_INGESTION_V1"
SOURCE = plane.YELLOWSTONE_SOURCE
MAX_AGE_NS = legacy.MAX_AGE_NS


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def raw_json_value_for_key(path: pathlib.Path, key: str) -> Any | None:
    """Read one value from a huge recovery map without materializing the map."""
    needle = (json.dumps(key) + ":").encode("utf-8")
    decoder = json.JSONDecoder()
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        position = data.find(needle)
        if position < 0:
            return None
        start = position + len(needle)
        # Recovery values are bounded signature-status objects / slot arrays.
        sample = data[start : start + 2_000_000].decode("utf-8")
        try:
            return decoder.raw_decode(sample.lstrip())[0]
        except json.JSONDecodeError:
            return None


def proof_for_frame(
    partition: pathlib.Path, signature: str, observed_slot: int, observed_index: int,
) -> tuple[bool, str]:
    finality_path = partition / "canonical-finality-recovery-v1.json"
    blocks_path = partition / "canonical-block-recovery-v1.json"
    if not finality_path.exists() or not blocks_path.exists():
        return False, "CANONICAL_PROOF_ARTIFACT_MISSING"
    finality = raw_json_value_for_key(finality_path, signature)
    if not isinstance(finality, dict):
        return False, "CANONICAL_FINALITY_PROOF_MISSING"
    if finality.get("confirmationStatus") != "finalized" or finality.get("successful") is not True:
        return False, "CANONICAL_FINALITY_NOT_SUCCESSFUL"
    if int(finality.get("slot") or 0) != observed_slot:
        return False, "CANONICAL_SLOT_MISMATCH"
    block = raw_json_value_for_key(blocks_path, str(observed_slot))
    if not isinstance(block, list):
        return False, "CANONICAL_BLOCK_PROOF_MISSING"
    if observed_index < 0 or observed_index >= len(block):
        return False, "CANONICAL_TRANSACTION_INDEX_INVALID"
    if str(block[observed_index]) != signature:
        return False, "CANONICAL_ORDER_SIGNATURE_MISMATCH"
    return True, "OK"


def target_rows(cohort_db: pathlib.Path, state_db: pathlib.Path) -> list[dict[str, Any]]:
    cohort = sqlite3.connect(cohort_db)
    cohort.row_factory = sqlite3.Row
    state = sqlite3.connect(state_db)
    rows: list[dict[str, Any]] = []
    try:
        for row in cohort.execute("SELECT * FROM current_decisions"):
            reasons = json.loads(str(row["reasons_json"] or "[]"))
            if "SELECTED_MINT_ABSENT_FROM_ONLINE_UNIVERSE" not in reasons:
                continue
            arrival = state.execute(
                "SELECT received_ns,slot FROM realtime_arrivals WHERE signature=? AND source=?",
                (str(row["signature"]), SOURCE),
            ).fetchone()
            if arrival is None:
                continue
            rows.append({
                "signature": str(row["signature"]), "mint": str(row["mint"]),
                "slot": int(row["slot"]), "transactionIndex": int(row["transaction_index"]),
                "entryReceivedNs": int(arrival[0]), "arrivalSlot": int(arrival[1]),
            })
    finally:
        cohort.close()
        state.close()
    return sorted(rows, key=lambda row: (row["entryReceivedNs"], row["slot"], row["transactionIndex"]))


def target_mints(rows: list[dict[str, Any]], registry_db: pathlib.Path) -> set[str]:
    registry = sqlite3.connect(registry_db)
    registry.row_factory = sqlite3.Row
    try:
        creates = registry.execute(
            """SELECT mint,realtime_receipt_ns FROM create_registry
               WHERE realtime_receipt_ns IS NOT NULL AND finalized_status='FINALIZED_SUCCESS'"""
        ).fetchall()
    finally:
        registry.close()
    # The selected transaction may expose its mint only through a CPI/log event.
    # The teacher wallet is a stable, explicit account-key selector for those
    # exact rows; finality/mint/order proof still gates every subsequent insert.
    result = {str(row["mint"]) for row in rows} | {decision.WALLET}
    intervals = [(int(row["entryReceivedNs"]) - MAX_AGE_NS, int(row["entryReceivedNs"])) for row in rows]
    for mint, received_ns in creates:
        if received_ns is not None and any(start <= int(received_ns) < end for start, end in intervals):
            result.add(str(mint))
    return result


def partitions_for_rows(exact_root: pathlib.Path, rows: list[dict[str, Any]]) -> list[pathlib.Path]:
    hours: set[pathlib.Path] = set()
    for row in rows:
        for timestamp_ns in (int(row["entryReceivedNs"]) - MAX_AGE_NS, int(row["entryReceivedNs"])):
            stamp = dt.datetime.fromtimestamp(timestamp_ns / 1_000_000_000, dt.timezone.utc)
            hours.add(exact_root / "partitions" / stamp.strftime("%Y/%m/%d/%H"))
    return sorted(path for path in hours if (path / "evidence.frames.zst").exists())


def in_target_window(received_ns: int, rows: list[dict[str, Any]]) -> bool:
    return any(int(row["entryReceivedNs"]) - MAX_AGE_NS <= received_ns < int(row["entryReceivedNs"]) for row in rows)


def normalized_tx_to_rpc(row: dict[str, Any], block_time_s: int) -> dict[str, Any]:
    tx = dict(row["normalized"])
    tx["blockTime"] = block_time_s
    tx["slot"] = int(tx["slot"])
    return {"result": tx}


def ingest_normalized(
    connection: sqlite3.Connection,
    normalized: dict[str, Any], metadata: dict[str, Any], evidence_path: pathlib.Path,
    signature: str, slot: int, tx_index: int, received_ns: int,
) -> tuple[bool, str, int]:
    """Insert one proof-gated transaction using the existing causal decoder."""
    if str(normalized.get("signature") or "") != signature:
        return False, "NORMALIZED_SIGNATURE_MISMATCH", 0
    if int(normalized.get("slot") or 0) != slot or int(normalized.get("transactionIndex") or -1) != tx_index:
        return False, "NORMALIZED_CANONICAL_IDENTITY_MISMATCH", 0
    tx = normalized_tx_to_rpc({"normalized": normalized}, received_ns // 1_000_000_000)
    body = tx["result"]
    try:
        create_mints = set(legacy.coverage.canonical_create_mints(body))
        decoded_hint = coverage.decode_trade_events((body.get("meta") or {}).get("logMessages") or [])
        hinted_mints = {str(event.mint) for event in decoded_hint} | create_mints
        decoded, failure = coverage.decode_causal_events(body, signature, received_ns, slot, tx_index)
    except Exception:
        return False, "YELLOWSTONE_CANONICAL_DECODE_EXCEPTION", 0
    if not decoded and not create_mints:
        return False, failure or "NO_PUMP_CAUSAL_STATE", 0
    kinds = sorted({"YELLOWSTONE_CANONICAL_STATE_PLANE"})
    connection.execute(
        "INSERT OR IGNORE INTO transactions VALUES (?,?,?,?,?,?)",
        (signature, received_ns // 1_000_000_000, slot, tx_index, canonical(kinds), canonical(sorted(hinted_mints))),
    )
    connection.execute(
        "INSERT OR IGNORE INTO transaction_evidence VALUES (?,?)", (signature, str(evidence_path.resolve()))
    )
    for mint in create_mints:
        connection.execute("INSERT OR IGNORE INTO create_events VALUES (?,?)", (signature, mint))
    if failure:
        for mint in hinted_mints:
            connection.execute("INSERT OR IGNORE INTO incomplete_events VALUES (?,?,?)", (signature, mint, failure))
        return True, failure, 0
    inserted = 0
    for event in decoded:
        before = connection.total_changes
        connection.execute(
            "INSERT OR IGNORE INTO causal_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event.signature, int(event.instruction_index), event.mint, int(event.is_buy), event.user,
             event.creator, int(event.quote_lamports), int(event.token_raw),
             int(event.pre_state.virtual_sol), int(event.pre_state.virtual_token),
             int(event.pre_state.real_sol), int(event.pre_state.real_token),
             int(event.post_state.virtual_sol), int(event.post_state.virtual_token),
             int(event.post_state.real_sol), int(event.post_state.real_token)),
        )
        inserted += int(connection.total_changes > before)
    return True, "OK", inserted


def run(args: argparse.Namespace) -> dict[str, Any]:
    rows = target_rows(args.cohort_db, args.state_db)
    if args.hour:
        rows = [row for row in rows if dt.datetime.fromtimestamp(
            int(row["entryReceivedNs"]) / 1_000_000_000, dt.timezone.utc
        ).strftime("%Y/%m/%d/%H") == args.hour]
    mints = target_mints(rows, args.registry_db)
    partitions = partitions_for_rows(args.exact_root, rows)
    report: dict[str, Any] = {
        "schema": SCHEMA, "dryRun": bool(args.dry_run), "targetRows": len(rows),
        "targetMints": len(mints), "partitions": [str(path) for path in partitions],
        "rejections": collections.Counter(), "decodable": 0, "canonicalProofFailures": 0,
        "identityMismatches": 0, "decodeFailures": collections.Counter(), "events": 0,
        "transactions": 0, "candidateStateTransactions": 0,
    }
    if not rows:
        return report
    with tempfile.TemporaryDirectory(prefix="2cq-yellowstone-state-plane-") as temporary:
        temp = pathlib.Path(temporary)
        mint_path = temp / "target-mints.txt"
        mint_path.write_text("\n".join(sorted(mints)) + "\n", encoding="utf-8")
        output_path = temp / "normalizer.jsonl"
        # A dry run uses a throwaway state DB so it exercises the exact same
        # decoder and uniqueness paths without mutating the research state.
        connection = plane.open_state(temp / "dry-run-state.sqlite3") if args.dry_run else plane.open_state(args.state_db)
        try:
            for partition in partitions:
                evidence_path = partition / "evidence.frames.zst"
                with output_path.open("wb") as output:
                    normalizer_args = ([str(args.normalizer), "--all"] if args.selected_only else
                                       [str(args.normalizer), "--target-mints", str(mint_path)])
                    process = subprocess.Popen(
                        normalizer_args,
                        stdin=subprocess.PIPE, stdout=output, stderr=subprocess.PIPE, text=True,
                    )
                    assert process.stdin is not None
                    selected_signatures = {str(row["signature"]) for row in rows}
                    for metadata, raw in iter_frames(evidence_path, False):
                        if metadata.get("kind") != "transaction" or metadata.get("rawFormat") != "yellowstone_subscribe_update_protobuf":
                            continue
                        received_ns = int(metadata.get("receivedAtUnixNs") or 0)
                        if not received_ns:
                            continue
                        signature = str(metadata.get("signature") or "")
                        if not signature:
                            continue
                        if args.selected_only and signature not in selected_signatures:
                            continue
                        if not args.selected_only and not in_target_window(received_ns, rows):
                            continue
                        process.stdin.write(canonical({"signature": signature, "rawBase64": base64.b64encode(raw).decode("ascii")}) + "\n")
                    process.stdin.close()
                    stderr = process.stderr.read() if process.stderr else ""
                    if process.wait() != 0:
                        raise RuntimeError(f"NORMALIZER_FAILED:{stderr[-1000:]}")
                raw_by_signature: dict[str, tuple[dict[str, Any], bytes]] = {}
                for metadata, raw in iter_frames(evidence_path, False):
                    signature = str(metadata.get("signature") or "")
                    if metadata.get("kind") == "transaction" and (
                        not args.selected_only or signature in selected_signatures
                    ):
                        raw_by_signature.setdefault(signature, (metadata, raw))
                for line in output_path.read_text(encoding="utf-8").splitlines():
                    item = json.loads(line)
                    if not item.get("ok"):
                        report["decodeFailures"][str(item.get("error") or "NORMALIZER_FAILURE")] += 1
                        continue
                    normalized = item["normalized"]
                    signature = str(normalized["signature"])
                    stored = raw_by_signature.get(signature)
                    if stored is None:
                        report["rejections"]["RAW_FRAME_LOOKUP_FAILED"] += 1
                        continue
                    metadata, _ = stored
                    slot = int(metadata.get("slot") or 0)
                    tx_index = int(metadata.get("transactionIndex") if metadata.get("transactionIndex") is not None else -1)
                    received_ns = int(metadata.get("receivedAtUnixNs") or 0)
                    proven, reason = proof_for_frame(partition, signature, slot, tx_index)
                    if not proven:
                        report["canonicalProofFailures"] += 1
                        report["rejections"][reason] += 1
                        continue
                    if int(normalized.get("slot") or 0) != slot or int(normalized.get("transactionIndex") or -1) != tx_index:
                        report["identityMismatches"] += 1
                        report["rejections"]["NORMALIZED_IDENTITY_MISMATCH"] += 1
                        continue
                    report["decodable"] += 1
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        accepted, result, events = ingest_normalized(connection, normalized, metadata, evidence_path, signature, slot, tx_index, received_ns)
                        if not accepted:
                            report["decodeFailures"][result] += 1
                        else:
                            report["transactions"] += 1
                            report["events"] += events
                            report["candidateStateTransactions"] += int(signature not in {row["signature"] for row in rows})
                        connection.commit()
                    except BaseException:
                        connection.rollback()
                        raise
        finally:
            connection.close()
    report["rejections"] = dict(sorted(report["rejections"].items()))
    report["decodeFailures"] = dict(sorted(report["decodeFailures"].items()))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort-db", type=pathlib.Path, required=True)
    parser.add_argument("--state-db", type=pathlib.Path, required=True)
    parser.add_argument("--registry-db", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--normalizer", type=pathlib.Path, required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--selected-only", action="store_true",
                        help="validate/apply only the proven selected-entry bridge before a broader candidate-state sweep")
    parser.add_argument("--hour", help="bounded UTC receive-hour subset, YYYY/MM/DD/HH")
    args = parser.parse_args()
    report = run(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
