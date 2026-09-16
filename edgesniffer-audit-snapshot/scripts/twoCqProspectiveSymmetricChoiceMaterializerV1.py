#!/usr/bin/env python3
"""Incrementally materialize the frozen prospective 2CQ choice census.

The job is deliberately outcome blind.  It reconstructs only finalized causal
state available before each independent 2CQ entry and applies the same evidence
requirements to the selected mint and every contemporaneous control.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import dataclasses
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import struct
from typing import Any, Iterable

import twoCqCaptureFirstDetectorRepairV1 as capture
import twoCqChoiceSetCoverageRepairV1 as coverage
import twoCqDecisionChoiceSetV1 as legacy
import twoCqDecisionSystemDecompositionV1 as decision
import twoCqPostFixEndToEndCollectionDiagnosticV1 as tier_a
import twoCqProspectiveTierAEntryCensusV3 as census_v3
from exactCurveEconomicsV1 import decode_trade_events
from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_MATERIALIZER_V1"
CONTRACT_SCHEMA = "2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_CENSUS_V1_CONTRACT"
DEVELOPMENT_N = 350
VALIDATION_N = 150
TARGET_N = DEVELOPMENT_N + VALIDATION_N
MAX_AGE_NS = 120_000_000_000
BLOCK_TIME_SAFETY_NS = 120_000_000_000
SELECTED_BATCH_N = 16
FORBIDDEN_KEY_PARTS = ("pnl", "roi", "profit", "future", "outcome", "exit")
COMPACT_MAGIC = b"AHCDV1\n\0"
COMPACT_RECORD = struct.Struct("<32sqQ")
COMPACT_LOOKBACK_RECORDS = 5_000_000
COMPACT_STOP_SLOT_MARGIN = 512


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("rb") as stream:
        for raw in stream:
            if not raw.strip():
                continue
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return rows


def assert_outcome_blind(value: Any, location: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                if lowered in {"outcomesread", "outcomes_read"} and int(item or 0) == 0:
                    continue
                raise RuntimeError(f"OUTCOME_FIELD_FORBIDDEN:{location}.{key}")
            assert_outcome_blind(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_outcome_blind(item, f"{location}[{index}]")


def invalid_intervals(root: pathlib.Path) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for row in read_jsonl(root / "invalid-collection-intervals.jsonl"):
        end = row.get("pauseEndUtc")
        if end is None:
            raise RuntimeError("OPEN_INVALID_COLLECTION_INTERVAL_FAIL_CLOSED")
        start_ms = int(tier_a.parse_utc(str(row["pauseStartUtc"])).timestamp() * 1000)
        end_ms = int(tier_a.parse_utc(str(end)).timestamp() * 1000)
        if end_ms <= start_ms:
            raise RuntimeError("INVALID_COLLECTION_INTERVAL_ORDER")
        result.append((start_ms, end_ms))
    return result


def in_invalid_interval(timestamp_ms: int, intervals: list[tuple[int, int]]) -> bool:
    return any(start <= timestamp_ms < end for start, end in intervals)


def initialize(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise RuntimeError("SYMMETRIC_CONTRACT_INVALID")
    if contract.get("integrity", {}).get("outcomesPermitted") is not False:
        raise RuntimeError("OUTCOME_BLIND_CONTRACT_INVALID")
    if contract.get("choiceUniverse", {}).get("selectedMintAndControlsUseIdenticalEvidenceRules") is not True:
        raise RuntimeError("SYMMETRIC_EVIDENCE_RULE_INVALID")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "development" / "decisions").mkdir(parents=True, exist_ok=True)
    sealed = args.output / "validation-sealed"
    sealed_decisions = sealed / "decisions"
    sealed_decisions.mkdir(parents=True, exist_ok=True)
    os.chmod(sealed, 0o700)
    os.chmod(sealed_decisions, 0o700)
    frozen = args.output / "contract.json"
    if frozen.exists():
        if sha256_file(frozen) != sha256_file(args.contract):
            raise RuntimeError("SYMMETRIC_CONTRACT_HASH_MISMATCH")
    else:
        frozen.write_bytes(args.contract.read_bytes())
        os.chmod(frozen, 0o444)
    state_path = args.output / "materializer-state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {
            "schema": SCHEMA + "_STATE",
            "contractSha256": sha256_file(frozen),
            "eligible": {},
            "terminalStatuses": {},
            "deferredStatuses": {},
            "lastRunUtc": None,
        }
        atomic_json(state_path, state)
    if state.get("contractSha256") != sha256_file(frozen):
        raise RuntimeError("MATERIALIZER_STATE_CONTRACT_MISMATCH")
    return contract, state


def decision_filename(ordinal: int, signature: str) -> str:
    return f"{ordinal:04d}-{signature}.json"


def recover_eligible_index(root: pathlib.Path, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    eligible = dict(state.get("eligible") or {})
    for directory, split in (
        (root / "development" / "decisions", "DEVELOPMENT"),
        (root / "validation-sealed" / "decisions", "UNTOUCHED_VALIDATION"),
    ):
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            prefix, signature = path.stem.split("-", 1)
            eligible.setdefault(signature, {"ordinal": int(prefix), "split": split, "path": str(path)})
    ordinals = sorted(int(row["ordinal"]) for row in eligible.values())
    if ordinals and ordinals != list(range(1, len(ordinals) + 1)):
        raise RuntimeError("ELIGIBLE_ORDINAL_GAP_OR_DUPLICATE")
    return eligible


def checkpoint_decision(
    root: pathlib.Path,
    state: dict[str, Any],
    signature: str,
    row: dict[str, Any] | None,
    status: dict[str, Any],
) -> None:
    eligible = recover_eligible_index(root, state)
    if row is not None and signature not in eligible:
        ordinal = len(eligible) + 1
        split = "DEVELOPMENT" if ordinal <= DEVELOPMENT_N else "UNTOUCHED_VALIDATION"
        row = row | {"eligibleOrdinal": ordinal, "split": split}
        assert_outcome_blind(row)
        directory = root / ("development" if split == "DEVELOPMENT" else "validation-sealed") / "decisions"
        mode = 0o600 if split == "UNTOUCHED_VALIDATION" else 0o644
        path = directory / decision_filename(ordinal, signature)
        atomic_json(path, row, mode)
        eligible[signature] = {"ordinal": ordinal, "split": split, "path": str(path)}
    state["eligible"] = eligible
    if signature in eligible:
        state.setdefault("terminalStatuses", {}).pop(signature, None)
        state.setdefault("deferredStatuses", {}).pop(signature, None)
    elif status.get("terminal"):
        state.setdefault("terminalStatuses", {})[signature] = status
        state.setdefault("deferredStatuses", {}).pop(signature, None)
    else:
        state.setdefault("deferredStatuses", {})[signature] = status
    state["lastRunUtc"] = now_utc()
    atomic_json(root / "materializer-state.json", state)


def publish_development(root: pathlib.Path, eligible: dict[str, dict[str, Any]]) -> None:
    development = sorted(
        (row for row in eligible.values() if row["split"] == "DEVELOPMENT"),
        key=lambda row: int(row["ordinal"]),
    )
    if len(development) < DEVELOPMENT_N:
        return
    destination = root / "development" / "choice-sets.jsonl.gz"
    if destination.exists():
        return
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            for index, item in enumerate(development, 1):
                if int(item["ordinal"]) != index:
                    raise RuntimeError("DEVELOPMENT_ORDINALS_NOT_EXACT")
                row = json.loads(pathlib.Path(item["path"]).read_text(encoding="utf-8"))
                assert_outcome_blind(row)
                stream.write(canonical(row) + b"\n")
    os.replace(temporary, destination)
    atomic_json(root / "development" / "freeze.json", {
        "schema": SCHEMA + "_DEVELOPMENT_FREEZE",
        "frozenAtUtc": now_utc(),
        "developmentN": DEVELOPMENT_N,
        "ordinalRange": [1, DEVELOPMENT_N],
        "choiceSetsSha256": sha256_file(destination),
        "outcomesRead": 0,
        "validationRowsRead": 0,
    })


def partition_paths_for_boundaries(
    exact_root: pathlib.Path, boundaries: list[int]
) -> tuple[list[pathlib.Path], dict[str, Any]]:
    hours = legacy.receive_hours(min(boundaries) - MAX_AGE_NS, max(boundaries))
    return legacy.finalized_evidence_paths(exact_root, hours)


def causal_before(event: decision.CausalEvent, boundary_ns: int, boundary_order: tuple[int, int, int, str]) -> bool:
    return event.received_ns < boundary_ns and event.order < boundary_order


def reserve_continuous(events: list[decision.CausalEvent]) -> bool:
    return all(left.post_state == right.pre_state for left, right in zip(events, events[1:]))


def inside_expanded_decision_window(block_time_ns: int, boundaries: list[int]) -> bool:
    """Conservatively prefilter finalized rows before the exact arrival join.

    Solana blockTime has one-second resolution and finalized evidence can be
    written later than execution.  A two-minute margin on both sides is much
    wider than observed arrival skew while preventing unrelated full-hour
    populations from entering memory.
    """
    left = bisect.bisect_left(boundaries, block_time_ns - BLOCK_TIME_SAFETY_NS)
    return (
        left < len(boundaries)
        and boundaries[left] <= block_time_ns + MAX_AGE_NS + BLOCK_TIME_SAFETY_NS
    )


def compact_slot_at(stream: Any, index: int) -> int:
    stream.seek(len(COMPACT_MAGIC) + index * COMPACT_RECORD.size)
    raw = stream.read(COMPACT_RECORD.size)
    if len(raw) != COMPACT_RECORD.size:
        raise RuntimeError("COMPACT_ARRIVAL_SHORT_RANDOM_READ")
    return int(COMPACT_RECORD.unpack(raw)[2])


def targeted_compact_arrivals_by_slot(
    path: pathlib.Path,
    target_digests: set[bytes],
    start_slot: int,
    end_slot: int,
) -> tuple[dict[bytes, tuple[int, int]], dict[str, int]]:
    """Read only the bounded slot region needed by this checkpoint.

    The observer journal is append-only and near slot-ordered.  The same
    conservative five-million-record lookback used by the validated compact
    window extractor protects against local slot inversions.  A missed target
    still fails closed during symmetric eligibility.
    """
    size = path.stat().st_size
    payload_size = size - len(COMPACT_MAGIC)
    if payload_size < 0:
        raise RuntimeError("COMPACT_ARRIVAL_INVALID_SIZE")
    total_records = payload_size // COMPACT_RECORD.size
    arrivals: dict[bytes, tuple[int, int]] = {}
    scanned = 0
    inversions = 0
    with path.open("rb") as stream:
        if stream.read(len(COMPACT_MAGIC)) != COMPACT_MAGIC:
            raise RuntimeError("COMPACT_ARRIVAL_MAGIC_MISMATCH")
        low, high = 0, total_records
        while low < high:
            middle = (low + high) // 2
            if compact_slot_at(stream, middle) < start_slot:
                low = middle + 1
            else:
                high = middle
        scan_start = max(0, low - COMPACT_LOOKBACK_RECORDS)
        stream.seek(len(COMPACT_MAGIC) + scan_start * COMPACT_RECORD.size)
        previous_slot: int | None = None
        while True:
            raw = stream.read(COMPACT_RECORD.size)
            if not raw:
                break
            if len(raw) != COMPACT_RECORD.size:
                break  # The append-only writer may be in the final record.
            digest, received_ns, slot = COMPACT_RECORD.unpack(raw)
            slot = int(slot)
            scanned += 1
            if previous_slot is not None and slot < previous_slot:
                inversions += 1
            previous_slot = slot
            if start_slot <= slot <= end_slot and digest in target_digests:
                candidate = (int(received_ns), slot)
                prior = arrivals.get(digest)
                if prior is None or candidate < prior:
                    arrivals[digest] = candidate
            if slot > end_slot + COMPACT_STOP_SLOT_MARGIN:
                break
    return arrivals, {
        "compactRecordsScanned": scanned,
        "compactSlotInversionsObserved": inversions,
        "compactTargetDigests": len(target_digests),
        "compactTargetMatches": len(arrivals),
    }


def scan_universe(
    evidence_paths: list[pathlib.Path],
    compact_path: pathlib.Path,
    boundaries: list[int],
) -> tuple[
    dict[str, list[decision.CausalEvent]],
    dict[str, dict[str, Any]],
    dict[str, list[tuple[tuple[int, int, int, str], str]]],
    dict[str, Any],
]:
    signatures: set[str] = set()
    creates: dict[str, dict[str, Any]] = {}
    canonical_rows: dict[str, dict[str, int]] = {}
    decoded_rows: dict[str, list[decision.CausalEvent]] = {}
    hinted_mints_by_signature: dict[str, set[str]] = {}
    pump_hint_signatures: set[str] = set()
    incomplete: dict[str, list[tuple[tuple[int, int, int, str], str]]] = collections.defaultdict(list)
    decode_failures: collections.Counter[str] = collections.Counter()
    create_decode_failures = 0
    missing_block_time = 0
    duplicate_signatures = 0
    for path in evidence_paths:
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            if metadata.get("kind") != "rpc_finalized_transaction" or metadata.get("successful") is not True:
                continue
            kinds = set(metadata.get("pumpInstructionKinds") or [])
            signature = str(metadata.get("signature") or "")
            if not signature:
                continue
            if signature in canonical_rows:
                duplicate_signatures += 1
                continue
            try:
                tx = json.loads(raw).get("result")
            except Exception:
                tx = None
            if not tx or (tx.get("meta") or {}).get("err") is not None:
                continue
            block_time = int(tx.get("blockTime") or 0)
            if block_time <= 0:
                missing_block_time += 1
                continue
            if not inside_expanded_decision_window(block_time * 1_000_000_000, boundaries):
                continue
            slot = int(metadata.get("slot") or tx.get("slot") or 0)
            tx_index = int(metadata.get("transactionIndex") if metadata.get("transactionIndex") is not None else -1)
            canonical_rows[signature] = {
                "slot": slot,
                "transactionIndex": tx_index,
            }
            signatures.add(signature)
            if kinds.intersection(decision.TRADE_KINDS | {"CREATE", "CREATE_V2"}):
                pump_hint_signatures.add(signature)
            try:
                for mint in coverage.canonical_create_mints(tx):
                    order = (slot, tx_index, -1, signature)
                    candidate = {
                        "mint": mint, "signature": signature, "order": order,
                        "blockTime": int(tx.get("blockTime") or 0),
                    }
                    if mint not in creates or order < tuple(creates[mint]["order"]):
                        creates[mint] = candidate
            except Exception:
                create_decode_failures += 1
            base_order = (slot, tx_index, -1, signature)
            try:
                decoded_hint = decode_trade_events((tx.get("meta") or {}).get("logMessages") or [])
                hinted_mints = {str(row.mint) for row in decoded_hint}
            except Exception:
                hinted_mints = {
                    str(mint) for mint in (metadata.get("discoveredMints") or []) if mint
                }
                if kinds.intersection(decision.TRADE_KINDS):
                    decode_failures["TRADE_LOG_DECODE_FAILED"] += 1
                    for mint in hinted_mints:
                        incomplete[mint].append((base_order, "TRADE_LOG_DECODE_FAILED"))
                continue
            hinted_mints_by_signature[signature] = hinted_mints
            try:
                decoded, failure = coverage.decode_causal_events(
                    tx, signature, 0, slot, tx_index
                )
            except Exception:
                decoded, failure = [], "TRANSACTION_DECODE_EXCEPTION"
            if failure:
                decode_failures[failure] += 1
                for mint in hinted_mints:
                    incomplete[mint].append((base_order, failure))
            elif decoded:
                decoded_rows[signature] = decoded
            elif kinds.intersection(decision.TRADE_KINDS):
                decode_failures["TRADE_EVENT_NOT_DECODED"] += 1
                for mint in {
                    str(mint) for mint in (metadata.get("discoveredMints") or []) if mint
                }:
                    incomplete[mint].append((base_order, "TRADE_EVENT_NOT_DECODED"))

    create_signatures = {str(row["signature"]) for row in creates.values()}
    pump_signatures = set(decoded_rows) | create_signatures | pump_hint_signatures
    canonical_rows = {
        signature: row for signature, row in canonical_rows.items()
        if signature in pump_signatures
    }
    signatures.intersection_update(pump_signatures)
    if canonical_rows:
        arrivals, compact_evidence = targeted_compact_arrivals_by_slot(
            compact_path,
            {tier_a.signature_digest(signature) for signature in signatures},
            min(row["slot"] for row in canonical_rows.values()),
            max(row["slot"] for row in canonical_rows.values()),
        )
    else:
        arrivals, compact_evidence = {}, {
            "compactRecordsScanned": 0,
            "compactSlotInversionsObserved": 0,
            "compactTargetDigests": 0,
            "compactTargetMatches": 0,
        }
    events: dict[str, dict[tuple[str, int], decision.CausalEvent]] = collections.defaultdict(dict)
    for signature, decoded in decoded_rows.items():
        record = canonical_rows[signature]
        base_order = (record["slot"], record["transactionIndex"], -1, signature)
        arrival = arrivals.get(tier_a.signature_digest(signature))
        if arrival is None:
            for mint in hinted_mints_by_signature.get(signature, set()):
                incomplete[mint].append((base_order, "ALLENHARK_TRADE_ARRIVAL_MISSING"))
            continue
        for event in decoded:
            causal_event = dataclasses.replace(event, received_ns=int(arrival[0]))
            events[causal_event.mint][
                (causal_event.signature, causal_event.instruction_index)
            ] = causal_event

    create_index: dict[str, dict[str, Any]] = {}
    for mint, row in creates.items():
        arrival = arrivals.get(tier_a.signature_digest(str(row["signature"])))
        if arrival is None:
            continue
        create_index[mint] = row | {"receivedNs": int(arrival[0]), "slot": int(arrival[1])}
    ordered = {
        mint: sorted(rows.values(), key=lambda event: event.order)
        for mint, rows in events.items()
    }
    return ordered, create_index, dict(incomplete), {
        "evidencePaths": len(evidence_paths),
        "canonicalSuccessfulPumpTransactions": len(canonical_rows),
        "duplicateEvidenceSignaturesIgnored": duplicate_signatures,
        "canonicalCreateMints": len(creates),
        "realtimeCreateMints": len(create_index),
        "decodedTradeEvents": sum(len(rows) for rows in ordered.values()),
        "incompleteMintHistories": len(incomplete),
        "successfulPumpTransactionsMissingBlockTime": missing_block_time,
        "createDecodeFailures": create_decode_failures,
        "tradeDecodeFailures": dict(sorted(decode_failures.items())),
        **compact_evidence,
    }


def build_choice(
    selected: dict[str, Any],
    events: dict[str, list[decision.CausalEvent]],
    creates: dict[str, dict[str, Any]],
    incomplete: dict[str, list[tuple[tuple[int, int, int, str], str]]],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    boundary_ns = int(selected["entryReceivedAtUnixNs"])
    boundary_order = (
        int(selected["entrySlot"]), int(selected["entryTransactionIndex"]),
        -1, "",
    )
    entry_matches = [
        row for row in events.get(str(selected["mint"]), [])
        if row.signature == selected["signature"]
        and row.user == decision.WALLET and row.is_buy
    ]
    if len(entry_matches) != 1:
        selected_incomplete = sorted({
            reason for order, reason in incomplete.get(str(selected["mint"]), [])
            if order[3] == selected["signature"]
        })
        return None, ["SELECTED_ENTRY_EVENT_NOT_EXACTLY_JOINED"], {
            "selectedEntryEventMatches": len(entry_matches),
            "selectedEntryEvidenceFailures": selected_incomplete,
            "candidateMintsWithRealtimeCreate": 0,
            "eligibleCandidateN": 0,
            "controlN": 0,
            "candidateExclusions": {},
        }
    creation_order = sorted(
        (int(row["receivedNs"]), mint) for mint, row in creates.items()
        if boundary_ns - MAX_AGE_NS <= int(row["receivedNs"]) < boundary_ns
        and tuple(row["order"]) < boundary_order
    )
    choices: list[dict[str, Any]] = []
    candidate_exclusions: collections.Counter[str] = collections.Counter()
    selected_exclusion_reasons: set[str] = set()
    for create_ns, mint in creation_order:
        sequence = [row for row in events.get(mint, []) if row.order < boundary_order]
        missing = [reason for order, reason in incomplete.get(mint, []) if order < boundary_order]
        if missing:
            candidate_exclusions.update(set(missing))
            if mint == selected["mint"]:
                selected_exclusion_reasons.update(missing)
            continue
        if not sequence or not reserve_continuous(sequence):
            candidate_exclusions["RESERVE_CONTINUITY_INCOMPLETE"] += 1
            if mint == selected["mint"]:
                selected_exclusion_reasons.add("RESERVE_CONTINUITY_INCOMPLETE")
            continue
        # Every state mutation through the chain boundary must have arrived
        # before the teacher decision. Otherwise executable state was unknown.
        if any(row.received_ns >= boundary_ns for row in sequence):
            candidate_exclusions["PREBOUNDARY_CHAIN_EVENT_NOT_CAUSALLY_OBSERVED"] += 1
            if mint == selected["mint"]:
                selected_exclusion_reasons.add("PREBOUNDARY_CHAIN_EVENT_NOT_CAUSALLY_OBSERVED")
            continue
        prior = [row for row in sequence if causal_before(row, boundary_ns, boundary_order)]
        external = [row for row in prior if row.user != decision.WALLET]
        if not prior or not external:
            candidate_exclusions["NO_PRIOR_EXTERNAL_STATE"] += 1
            if mint == selected["mint"]:
                selected_exclusion_reasons.add("NO_PRIOR_EXTERNAL_STATE")
            continue
        if legacy.teacher_exposure_raw(prior) > 1:
            candidate_exclusions["TEACHER_ALREADY_EXPOSED"] += 1
            if mint == selected["mint"]:
                selected_exclusion_reasons.add("TEACHER_ALREADY_EXPOSED")
            continue
        last = prior[-1]
        if int(last.post_state.real_token) <= 0:
            candidate_exclusions["CURVE_NOT_EXECUTABLE"] += 1
            if mint == selected["mint"]:
                selected_exclusion_reasons.add("CURVE_NOT_EXECUTABLE")
            continue
        last_external = external[-1]
        features = decision.state_features(
            last.post_state, boundary_ns, mint, last.creator, prior, create_ns
        )
        choices.append({
            "label": mint == selected["mint"],
            "mint": mint,
            "createReceivedAtUnixNs": create_ns,
            "stateSignature": last.signature,
            "stateSlot": last.slot,
            "stateTransactionIndex": last.transaction_index,
            "stateInstructionIndex": last.instruction_index,
            "stateReceivedAtUnixNs": last.received_ns,
            "lastExternalAgeMs": (boundary_ns - last_external.received_ns) / 1_000_000,
            "features": features,
        })
    chosen = [row for row in choices if row["label"]]
    reasons: list[str] = []
    if len(chosen) != 1:
        reasons.append("SELECTED_MINT_ABSENT_FROM_SYMMETRIC_UNIVERSE")
    if len(choices) <= 1:
        reasons.append("NO_CONTEMPORANEOUS_ALTERNATIVE")
    detail = {
        "candidateMintsWithRealtimeCreate": len(creation_order),
        "eligibleCandidateN": len(choices),
        "controlN": max(0, len(choices) - len(chosen)),
        "candidateExclusions": dict(sorted(candidate_exclusions.items())),
        "selectedExclusionReasons": sorted(selected_exclusion_reasons),
    }
    if reasons:
        return None, reasons, detail
    row = {
        "schema": SCHEMA + "_CHOICE_DECISION",
        "episodeId": selected["signature"],
        "signature": selected["signature"],
        "mint": selected["mint"],
        "route": selected["route"],
        "acquisitionClass": selected["acquisitionClass"],
        "decisionBoundary": {
            "arrivalUnixNs": boundary_ns,
            "slot": selected["entrySlot"],
            "transactionIndex": selected["entryTransactionIndex"],
            "semantics": "IMMEDIATELY_BEFORE_TEACHER_ENTRY_USING_CAUSALLY_ARRIVED_CANONICAL_STATE",
        },
        "controlN": len(choices) - 1,
        "states": choices,
        "evidenceSummary": detail,
        "outcomesRead": 0,
        "submissions": 0,
    }
    assert_outcome_blind(row)
    return row, [], detail


def selected_tier_a_rows(
    args: argparse.Namespace,
    env: dict[str, str],
    batch: list[dict[str, Any]],
    transactions: dict[str, dict[str, Any]],
    candidate_by_signature: dict[str, dict[str, Any]],
    continuity_segments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    observer_started_ns = min((int(row["startUnixNs"]) for row in continuity_segments), default=0)
    selected: list[dict[str, Any]] = []
    statuses: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(batch), SELECTED_BATCH_N):
        rows = batch[offset:offset + SELECTED_BATCH_N]
        required: set[bytes] = set()
        histories: dict[str, list[dict[str, Any]]] = {}
        for episode in rows:
            signature = str(episode["signature"])
            candidate = candidate_by_signature[signature]
            required.add(tier_a.signature_digest(signature))
            older, boundary = tier_a.older_signatures(
                env["SECURE_RPC_URL"], str(episode["mint"]), signature, args.tier_a_cache
            )
            histories[signature] = older
            if boundary:
                required.update(
                    tier_a.signature_digest(value)
                    for item in older if (value := str(item.get("signature") or ""))
                )
        arrivals = census_v3.targeted_compact_arrivals(args.compact_arrivals, required)
        for episode in rows:
            signature = str(episode["signature"])
            candidate = candidate_by_signature[signature]
            reasons: list[str] = []
            detail: dict[str, Any] = {}
            try:
                census_v3.prewarm_finalized_history(
                    env["SECURE_RPC_URL"], histories.get(signature, []), args.tier_a_cache
                )
                adapted = {
                    "signature": signature,
                    "outputMint": episode["mint"],
                    "detectedAtMs": int(candidate["receivedAtUnixNs"]) // 1_000_000,
                    "sourceSlot": candidate["slot"],
                }
                eligible, reasons, detail = tier_a.evaluate_history(
                    env["SECURE_RPC_URL"], adapted, transactions[signature], arrivals,
                    args.tier_a_cache, observer_started_ns, 0,
                )
                entry_arrival = arrivals.get(tier_a.signature_digest(signature))
                create_signature = str(detail.get("createSignature") or "")
                create_arrival = arrivals.get(tier_a.signature_digest(create_signature)) if create_signature else None
                segment = census_v3.covering_continuity_segment(
                    int(create_arrival[0]) if create_arrival else 0,
                    int(entry_arrival[0]) if entry_arrival else 0,
                    continuity_segments,
                )
                if segment is None:
                    reasons.append("ALLENHARK_SELECTED_HISTORY_CONTINUITY_NOT_PROVEN")
                    eligible = False
                if eligible:
                    selected.append({
                        "signature": signature,
                        "mint": episode["mint"],
                        "route": episode["route"],
                        "acquisitionClass": episode["classification"],
                        "entryReceivedAtUnixNs": int(candidate["receivedAtUnixNs"]),
                        "entrySlot": int(candidate["slot"]),
                        "entryTransactionIndex": int(episode.get("transactionIndex") or -1),
                        "tierAEvidence": detail,
                    })
            except Exception as error:
                reasons = ["TIER_A_EVALUATOR_TRANSIENT_FAILURE"]
                detail = {"errorType": type(error).__name__, "error": str(error)}
                eligible = False
            statuses[signature] = {
                "eligibleForSymmetricReconstruction": bool(eligible),
                "reasons": sorted(set(reasons)),
                "detail": detail,
                "terminal": bool(not eligible and "TIER_A_EVALUATOR_TRANSIENT_FAILURE" not in reasons),
                "atUtc": now_utc(),
            }
    return selected, statuses


def run(args: argparse.Namespace) -> dict[str, Any]:
    contract, state = initialize(args)
    eligible = recover_eligible_index(args.output, state)
    for signature in eligible:
        state.setdefault("terminalStatuses", {}).pop(signature, None)
        state.setdefault("deferredStatuses", {}).pop(signature, None)
    if len(eligible) >= TARGET_N and (args.output / "status.json").exists():
        return json.loads((args.output / "status.json").read_text(encoding="utf-8"))
    start_ms = int(tier_a.parse_utc(str(contract["cohortStartUtc"])).timestamp() * 1000)
    end_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000) - 2_000
    env = capture.denominator.load_env(args.env)
    signature_rows, transactions = capture.fetch_prospective_population(
        env["SECURE_RPC_URL"], start_ms, end_ms, args.output / "wallet-rpc-cache"
    )
    classified, _ = capture.classify_successful_transactions(signature_rows, transactions, start_ms, end_ms)
    episodes = [row for row in classified if row["isIndependentEntryEpisode"]]
    intervals = invalid_intervals(args.output)
    candidates = [
        row for row in read_jsonl(args.candidates)
        if int(row.get("receivedAtUnixNs") or 0) // 1_000_000 >= start_ms
    ]
    candidate_by_signature = {
        str(row.get("signature") or ""): row for row in candidates
        if not in_invalid_interval(int(row.get("receivedAtUnixNs") or 0) // 1_000_000, intervals)
    }
    terminal = dict(state.get("terminalStatuses") or {})
    pending = [
        row for row in episodes
        if str(row["signature"]) not in eligible and str(row["signature"]) not in terminal
    ]
    pending.sort(key=lambda row: (
        int((transactions[str(row["signature"])].get("blockTime") or 0)),
        int(row.get("transactionIndex") or -1), str(row["signature"]),
    ))
    compact_health = json.loads(args.compact_observer_health.read_text(encoding="utf-8"))
    continuity_segments = census_v3.compact_continuity_segments(
        args.compact_observer_continuity, compact_health
    )
    ready_by_hour: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    deferred_not_ready = 0
    prefiltered_statuses: dict[str, dict[str, Any]] = {}
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    for episode in pending:
        signature = str(episode["signature"])
        candidate = candidate_by_signature.get(signature)
        block_ms = int((transactions[signature].get("blockTime") or 0) * 1000)
        if in_invalid_interval(block_ms, intervals) or (
            candidate and in_invalid_interval(int(candidate["receivedAtUnixNs"]) // 1_000_000, intervals)
        ):
            prefiltered_statuses[signature] = {
                "terminal": True, "reasons": ["PAUSED_INVALID_COLLECTION_INTERVAL"], "atUtc": now_utc()
            }
        elif episode["route"] not in census_v3.SUPPORTED_TIER_A_ROUTES:
            prefiltered_statuses[signature] = {
                "terminal": True,
                "reasons": ["NON_PUMP_BONDING_ROUTE_NOT_SUPPORTED_BY_FROZEN_TIER_A_REPLAY"],
                "atUtc": now_utc(),
            }
        elif candidate is None:
            prefiltered_statuses[signature] = {
                "terminal": now_ms - block_ms > 600_000,
                "reasons": ["CANONICAL_ACQUISITION_WITHOUT_REALTIME_CANDIDATE"],
                "atUtc": now_utc(),
            }
        else:
            boundary = int(candidate["receivedAtUnixNs"])
            try:
                partition_paths_for_boundaries(args.exact_root, [boundary])
            except RuntimeError as error:
                if str(error).startswith("FINALIZED_PARTITIONS_NOT_READY:"):
                    deferred_not_ready += 1
                    prefiltered_statuses[signature] = {
                        "terminal": False, "reasons": ["FINALIZED_PARTITIONS_NOT_READY"],
                        "detail": {"error": str(error)}, "atUtc": now_utc(),
                    }
                    continue
                raise
            hour = dt.datetime.fromtimestamp(boundary / 1e9, dt.timezone.utc).strftime("%Y-%m-%dT%H")
            ready_by_hour[hour].append(episode)

    for signature, status in prefiltered_statuses.items():
        checkpoint_decision(args.output, state, signature, None, status)

    newly_materialized = 0
    examined = 0
    evidence_totals: collections.Counter[str] = collections.Counter()
    exclusion_totals: collections.Counter[str] = collections.Counter()
    for hour in sorted(ready_by_hour):
        if len(recover_eligible_index(args.output, state)) >= TARGET_N:
            break
        batch = ready_by_hour[hour]
        if args.max_new_decisions:
            remaining = args.max_new_decisions - newly_materialized
            if remaining <= 0:
                break
            batch = batch[:max(remaining * 4, remaining)]
        # The symmetric reconstruction is a strict superset of the selected-mint
        # Tier-A gate: canonical finalized entry, realtime entry/create/history,
        # reserve continuity, earlier external state, known-flat acquisition, and
        # full 120-second source continuity are all proven below. Replaying the
        # old RPC mint-history evaluator here would duplicate the same evidence
        # and materialize multi-GB history responses in memory.
        selected = [{
            "signature": str(episode["signature"]),
            "mint": str(episode["mint"]),
            "route": str(episode["route"]),
            "acquisitionClass": str(episode["classification"]),
            "entryReceivedAtUnixNs": int(candidate_by_signature[str(episode["signature"])]["receivedAtUnixNs"]),
            "entrySlot": int(candidate_by_signature[str(episode["signature"])]["slot"]),
            "entryTransactionIndex": int(episode["transactionIndex"] if episode.get("transactionIndex") is not None else -1),
        } for episode in batch]
        selected_by_signature = {row["signature"]: row for row in selected}
        selected_boundaries = [int(row["entryReceivedAtUnixNs"]) for row in selected]
        if selected_boundaries:
            paths, global_inexact = partition_paths_for_boundaries(args.exact_root, selected_boundaries)
            events, creates, incomplete, evidence = scan_universe(
                paths, args.compact_arrivals, selected_boundaries
            )
            evidence_totals.update({key: value for key, value in evidence.items() if isinstance(value, int)})
        else:
            events, creates, incomplete, global_inexact = {}, {}, {}, {}
        for episode in batch:
            if len(recover_eligible_index(args.output, state)) >= TARGET_N:
                break
            signature = str(episode["signature"])
            examined += 1
            selected_row = selected_by_signature.get(signature)
            boundary_ns = int(selected_row["entryReceivedAtUnixNs"])
            full_segment = census_v3.covering_continuity_segment(
                boundary_ns - MAX_AGE_NS, boundary_ns, continuity_segments
            )
            if full_segment is None:
                status = {
                    "terminal": True,
                    "reasons": ["GLOBAL_REALTIME_CONTINUITY_NOT_PROVEN_FOR_120S_UNIVERSE"],
                    "atUtc": now_utc(),
                }
                exclusion_totals.update(status["reasons"])
                checkpoint_decision(args.output, state, signature, None, status)
                continue
            choice, reasons, detail = build_choice(selected_row, events, creates, incomplete)
            status = {
                "terminal": choice is None,
                "reasons": reasons,
                "detail": detail | {
                    "fullUniverseContinuitySegment": full_segment,
                    "globalReconciliationExceptions": global_inexact,
                },
                "atUtc": now_utc(),
            }
            if choice is not None:
                choice["sourceProof"] = {
                    "contractSha256": sha256_file(args.output / "contract.json"),
                    "exactLedgerRoot": str(args.exact_root),
                    "allenharkArrivalLedger": str(args.compact_arrivals),
                    "fullUniverseContinuitySegment": full_segment,
                    "selectedEligibilitySemantics": "STRICT_SYMMETRIC_TIER_A_SUPERSET_V1",
                }
                assert_outcome_blind(choice)
                newly_materialized += 1
            else:
                exclusion_totals.update(reasons)
            checkpoint_decision(args.output, state, signature, choice, status)
        if args.max_new_decisions and newly_materialized >= args.max_new_decisions:
            break

    eligible = recover_eligible_index(args.output, state)
    publish_development(args.output, eligible)
    state["eligible"] = eligible
    state["lastRunUtc"] = now_utc()
    atomic_json(args.output / "materializer-state.json", state)
    reason_counts: collections.Counter[str] = collections.Counter()
    for row in state.get("terminalStatuses", {}).values():
        reason_counts.update(row.get("reasons") or [])
    status = {
        "schema": SCHEMA + "_STATUS",
        "atUtc": now_utc(),
        "cohortStartUtc": contract["cohortStartUtc"],
        "developmentEligibleN": min(len(eligible), DEVELOPMENT_N),
        "developmentTargetN": DEVELOPMENT_N,
        "sealedValidationEligibleN": max(0, len(eligible) - DEVELOPMENT_N),
        "sealedValidationTargetN": VALIDATION_N,
        "totalMaterializedN": len(eligible),
        "targetN": TARGET_N,
        "canonicalIndependentEntryEpisodes": len(episodes),
        "realtimeCandidatesSinceStart": len(candidate_by_signature),
        "newlyMaterializedThisRun": newly_materialized,
        "episodesExaminedThisRun": examined,
        "deferredFinalizedPartitionsNotReady": deferred_not_ready,
        "terminalExcludedN": len(state.get("terminalStatuses", {})),
        "deferredN": len(state.get("deferredStatuses", {})),
        "exclusionsByReason": dict(sorted(reason_counts.items())),
        "evidenceScannedThisRun": dict(sorted(evidence_totals.items())),
        "materializationMode": "DURABLE_INCREMENTAL_CLOSED_HOUR_BATCHES",
        "selectedAndControlsSymmetric": True,
        "developmentFreezeReady": (args.output / "development" / "choice-sets.jsonl.gz").exists(),
        "validationSealed": True,
        "validationRowsRead": 0,
        "outcomesRead": 0,
        "submissions": 0,
        "rawCollectionActive": True,
    }
    atomic_json(args.output / "status.json", status)
    atomic_json(args.output / "reconciliation-progress.json", {
        "schema": SCHEMA + "_PROGRESS",
        "atUtc": now_utc(),
        "state": "TARGET_COMPLETE" if len(eligible) >= TARGET_N else "CHECKPOINT_COMPLETE",
        "materializedN": len(eligible),
        "newlyMaterializedThisRun": newly_materialized,
        "episodesExaminedThisRun": examined,
    })
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--candidates", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--compact-observer-health", type=pathlib.Path, required=True)
    parser.add_argument("--compact-observer-continuity", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--max-new-decisions", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
