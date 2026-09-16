#!/usr/bin/env python3
"""Versioned online-choice materializer for prospective 2CQ research.

V2 separates the live-observable imitation population from the stricter
forensic Tier-A audit subset.  It never reads outcomes and only makes a
terminal evidence-dependent decision from an exact reconciliation generation.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import pickle
import sqlite3
import struct
import time
from typing import Any

import twoCqCaptureFirstDetectorRepairV1 as capture
import twoCqDecisionChoiceSetV1 as legacy
import twoCqDecisionSystemDecompositionV1 as decision
import twoCqIndexedCausalStatePlaneV1 as indexed_state
import twoCqPostFixEndToEndCollectionDiagnosticV1 as tier_a
import twoCqProspectiveSymmetricChoiceMaterializerV1 as v1
import twoCqProspectiveTierAEntryCensusV3 as census_v3
from exactCurveEconomicsV1 import SOL_QUOTE_MINTS, decode_trade_events
from exactCurveEconomicsQuoteV2 import decode_quote_trade_events


SCHEMA = "2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_MATERIALIZER_V3"
CONTRACT_SCHEMA = "2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_CENSUS_V3_CONTRACT"
CLASSIFIER_VERSION = "ECONOMIC_FAMILY_V1"
IMPLEMENTATION_VERSION = "3.1.0"
ONLINE_SEMANTICS = "LIVE_OBSERVABLE_SYMMETRIC_CHOICE_V3_EXACT_MULTISOURCE"
FORENSIC_SEMANTICS = "STRICT_SYMMETRIC_TIER_A_AUDIT_V1"
DEVELOPMENT_N = 350
VALIDATION_N = 150
TARGET_N = DEVELOPMENT_N + VALIDATION_N
MAX_AGE_NS = 120_000_000_000
SUPPORTED_ONLINE_FAMILIES = {"SOL_PUMP_CURVE", "SOL_PUMP_CPI"}
STATIC_GENERATION = "STATIC_V2_2_1_0"
CAPTURE_ALL_SOURCE = "allenhark-capture-all-finalized-signature-join-v1"
YELLOWSTONE_SOURCE = "yellowstone-direct-exact-signature-v1"
MAX_CAPTURE_ALL_RECOVERIES_PER_RUN = 32
MAX_YELLOWSTONE_RECOVERIES_PER_RUN = 256
MAX_READY_HOURS_PER_RUN = 6
CALIBRATION_MAX_ABS_DELTA_MS = 250
CALIBRATION_MAX_DISTANCE_MS = 300_000
CALIBRATION_MIN_EXACT_ANCHORS = 25
SLOT_INDEX_MAGIC = b"AHIDXV1\0"
SLOT_INDEX_HEADER = struct.Struct("<8sQQ")
SLOT_INDEX_RECORD = struct.Struct("<QQQQ")


class EvidenceNotReady(RuntimeError):
    def __init__(self, hours: list[dict[str, Any]]) -> None:
        super().__init__("EXACT_RECONCILIATION_NOT_READY")
        self.hours = hours


def indexed_compact_arrivals_by_slot(
    path: pathlib.Path,
    target_digests: set[bytes],
    start_slot: int,
    end_slot: int,
    index_path: pathlib.Path,
) -> tuple[dict[bytes, tuple[int, int]], dict[str, int]]:
    """Exact compact lookup using block min/max slot metadata."""
    with index_path.open("rb") as stream:
        raw_header = stream.read(SLOT_INDEX_HEADER.size)
        if len(raw_header) != SLOT_INDEX_HEADER.size:
            raise RuntimeError("COMPACT_SLOT_INDEX_SHORT_HEADER")
        magic, record_size, block_records = SLOT_INDEX_HEADER.unpack(raw_header)
        if magic != SLOT_INDEX_MAGIC or record_size != v1.COMPACT_RECORD.size:
            raise RuntimeError("COMPACT_SLOT_INDEX_HEADER_MISMATCH")
        blocks: list[tuple[int, int, int, int]] = []
        while True:
            raw = stream.read(SLOT_INDEX_RECORD.size)
            if not raw:
                break
            if len(raw) != SLOT_INDEX_RECORD.size:
                raise RuntimeError("COMPACT_SLOT_INDEX_TRUNCATED")
            block = SLOT_INDEX_RECORD.unpack(raw)
            if block[2] <= end_slot and block[3] >= start_slot:
                blocks.append(block)
        indexed_records = 0
        stream.seek(-SLOT_INDEX_RECORD.size, os.SEEK_END)
        final = SLOT_INDEX_RECORD.unpack(stream.read(SLOT_INDEX_RECORD.size))
        indexed_records = int(final[0] + final[1])

    source_size = path.stat().st_size
    total_records = (source_size - len(v1.COMPACT_MAGIC)) // v1.COMPACT_RECORD.size
    if total_records < indexed_records:
        raise RuntimeError("COMPACT_JOURNAL_SHRANK_AFTER_INDEX")
    if total_records > indexed_records:
        blocks.append((indexed_records, total_records - indexed_records, 0, 2**64 - 1))

    arrivals: dict[bytes, tuple[int, int]] = {}
    scanned = 0
    inversions = 0
    previous_slot: int | None = None
    with path.open("rb") as source:
        if source.read(len(v1.COMPACT_MAGIC)) != v1.COMPACT_MAGIC:
            raise RuntimeError("COMPACT_ARRIVAL_MAGIC_MISMATCH")
        for start, count, _, _ in blocks:
            source.seek(len(v1.COMPACT_MAGIC) + int(start) * v1.COMPACT_RECORD.size)
            remaining = int(count)
            while remaining:
                batch_n = min(remaining, 32_768)
                raw = source.read(batch_n * v1.COMPACT_RECORD.size)
                if len(raw) != batch_n * v1.COMPACT_RECORD.size:
                    raise RuntimeError("COMPACT_ARRIVAL_INDEXED_READ_SHORT")
                for digest, received_ns, slot in v1.COMPACT_RECORD.iter_unpack(raw):
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
                remaining -= batch_n
    return arrivals, {
        "compactLookupMode": "EXACT_BLOCK_MIN_MAX_INDEX_V1",
        "compactIndexBlocksRead": len(blocks),
        "compactRecordsScanned": scanned,
        "compactSlotInversionsObserved": inversions,
        "compactTargetDigests": len(target_digests),
        "compactTargetMatches": len(arrivals),
        "compactIndexBlockRecords": int(block_records),
        "compactIndexedRecords": indexed_records,
        "compactUnindexedTailRecords": max(0, total_records - indexed_records),
    }


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


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


def recover_capture_all_candidates(
    compact_path: pathlib.Path,
    episodes: list[dict[str, Any]],
    candidate_by_signature: dict[str, dict[str, Any]],
    minimum_block_time_ms: int | None,
    completed_signatures: set[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Recover original AllenHark receipt clocks after finalized classification.

    The narrow capture-first journal can only recognize the teacher wallet from
    fields available in the realtime transaction envelope. The compact journal
    intentionally captures every decoded transaction, so finalized canonical
    acquisitions can be joined to their original arrival without inventing a
    timestamp. A frozen development boundary prevents historical membership
    from changing after the N350 freeze.
    """
    completed_signatures = completed_signatures or set()
    all_missing = sorted([
        row for row in episodes
        if str(row.get("signature") or "") not in candidate_by_signature
        and str(row.get("signature") or "") not in completed_signatures
        and (
            minimum_block_time_ms is None
            or int(row.get("blockTimeMs") or 0) > minimum_block_time_ms
        )
    ], key=lambda row: (
        int(row.get("blockTimeMs") or 0), int(row.get("slot") or 0),
        int(row.get("transactionIndex") or -1), str(row.get("signature") or ""),
    ), reverse=True)
    missing = all_missing[:MAX_CAPTURE_ALL_RECOVERIES_PER_RUN]
    evidence = {
        "captureAllJoinPending": len(all_missing),
        "captureAllJoinTargets": len(missing),
        "captureAllJoinMatches": 0,
        "captureAllJoinSlotMismatches": 0,
        "_captureAllJoinAttemptedSignatures": [
            str(row["signature"]) for row in missing
        ],
    }
    if not missing:
        return candidate_by_signature, evidence
    target_digests = {
        tier_a.signature_digest(str(row["signature"])) for row in missing
    }
    arrivals, compact_evidence = v1.targeted_compact_arrivals_by_slot(
        compact_path,
        target_digests,
        min(int(row["slot"]) for row in missing),
        max(int(row["slot"]) for row in missing),
    )
    evidence.update(compact_evidence)
    recovered = dict(candidate_by_signature)
    for row in missing:
        signature = str(row["signature"])
        arrival = arrivals.get(tier_a.signature_digest(signature))
        if arrival is None:
            continue
        received_ns, observed_slot = arrival
        if observed_slot != int(row["slot"]):
            evidence["captureAllJoinSlotMismatches"] += 1
            continue
        recovered[signature] = {
            "schema": "CAPTURE_ALL_FINALIZED_SIGNATURE_JOIN_V1",
            "source": CAPTURE_ALL_SOURCE,
            "signature": signature,
            "receivedAtUnixNs": received_ns,
            "receivedAtUtc": dt.datetime.fromtimestamp(
                received_ns / 1e9, dt.timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            "slot": observed_slot,
            "streamSequence": None,
            "finalizedClassification": True,
        }
        evidence["captureAllJoinMatches"] += 1
    return recovered, evidence


def decision_generation_version(candidate: dict[str, Any]) -> str:
    candidate_source = str(candidate.get("source") or "")
    if candidate_source == YELLOWSTONE_SOURCE:
        # Existing exact-signature fallback rows retain their proven generation.
        # Only rows admitted by the host clock-domain repair need a new replay.
        anchor = candidate.get("calibrationAnchor") or {}
        return IMPLEMENTATION_VERSION if anchor.get("clockDomainCalibrated") is True else "3.0.0"
    return "2.2.0" if candidate_source == CAPTURE_ALL_SOURCE else "2.1.0"


def earliest_rows_by_signature(path: pathlib.Path, timestamp_key: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        signature = str(row.get("signature") or "")
        timestamp = int(row.get(timestamp_key) or 0)
        if not signature or not timestamp:
            continue
        previous = rows.get(signature)
        if previous is None or timestamp < int(previous[timestamp_key]):
            rows[signature] = row
    return rows


def recover_yellowstone_candidates(
    direct_path: pathlib.Path,
    episodes: list[dict[str, Any]],
    candidate_by_signature: dict[str, dict[str, Any]],
    development_boundary_ms: int | None,
    completed_signatures: set[str],
    calibration_report_path: pathlib.Path,
    recovery_ledger_path: pathlib.Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Use an exact direct timestamp only inside a proven realtime clock epoch."""
    calibration = json.loads(calibration_report_path.read_text(encoding="utf-8"))
    anchors = sorted([
        row for row in calibration.get("matchedRows", [])
        if abs(float(row["yellowstoneMinusAllenharkMs"])) <= CALIBRATION_MAX_ABS_DELTA_MS
    ], key=lambda row: int(row["yellowstoneDetectedAtMs"]))
    clock_domain = calibration.get("clockDomain") or {}
    clock_domain_calibrated = bool(
        clock_domain.get("sameHostSources") is True
        and clock_domain.get("hostBootId")
        and clock_domain.get("ntpSynchronized") is True
        and int(clock_domain.get("validExactSignatureAnchors") or 0)
        >= CALIBRATION_MIN_EXACT_ANCHORS
    )
    direct = earliest_rows_by_signature(direct_path, "detectedAtMs")
    all_missing = sorted([
        row for row in episodes
        if str(row.get("signature") or "") not in candidate_by_signature
        and str(row.get("signature") or "") not in completed_signatures
        and str(row.get("signature") or "") in direct
        and (development_boundary_ms is None or int(row.get("blockTimeMs") or 0) > development_boundary_ms)
    ], key=lambda row: (
        int(row.get("blockTimeMs") or 0), int(row.get("slot") or 0),
        int(row.get("transactionIndex") or -1), str(row.get("signature") or ""),
    ))
    targets = all_missing[:MAX_YELLOWSTONE_RECOVERIES_PER_RUN]
    recovered = dict(candidate_by_signature)
    evidence: dict[str, Any] = {
        "pending": len(all_missing), "targets": len(targets), "matches": 0,
        "slotMismatches": 0, "uncalibratedClockEpoch": 0,
        "attemptedSignatures": [str(row["signature"]) for row in targets],
        "calibrationReportSha256": sha256_file(calibration_report_path),
        "calibrationMaximumAbsoluteDeltaMs": CALIBRATION_MAX_ABS_DELTA_MS,
        "calibrationMaximumDistanceMs": CALIBRATION_MAX_DISTANCE_MS,
        "calibrationMinimumExactAnchors": CALIBRATION_MIN_EXACT_ANCHORS,
        "clockDomainCalibrated": clock_domain_calibrated,
        "clockDomain": clock_domain,
    }
    anchor_times = [int(row["yellowstoneDetectedAtMs"]) for row in anchors]
    import bisect
    for episode in targets:
        signature = str(episode["signature"])
        row = direct[signature]
        timestamp_ms = int(row["detectedAtMs"])
        if int(row.get("sourceSlot") or -1) != int(episode["slot"]):
            evidence["slotMismatches"] += 1
            continue
        index = bisect.bisect_left(anchor_times, timestamp_ms)
        nearby = [anchors[item] for item in (index - 1, index) if 0 <= item < len(anchors)]
        anchor = min(nearby, key=lambda item: abs(int(item["yellowstoneDetectedAtMs"]) - timestamp_ms), default=None)
        distance_ms = abs(int(anchor["yellowstoneDetectedAtMs"]) - timestamp_ms) if anchor else None
        if anchor is None or distance_ms is None or (
            distance_ms > CALIBRATION_MAX_DISTANCE_MS and not clock_domain_calibrated
        ):
            evidence["uncalibratedClockEpoch"] += 1
            continue
        recovered[signature] = {
            "schema": "YELLOWSTONE_DIRECT_EXACT_SIGNATURE_JOIN_V1",
            "source": YELLOWSTONE_SOURCE,
            "signature": signature,
            "receivedAtUnixNs": timestamp_ms * 1_000_000,
            "receivedAtUtc": dt.datetime.fromtimestamp(timestamp_ms / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "slot": int(row["sourceSlot"]),
            "streamSequence": None,
            "timestampResolution": "1ms",
            "timestampInferred": False,
            "calibrationAnchor": {
                "signature": anchor["signature"],
                "distanceMs": distance_ms,
                "yellowstoneMinusAllenharkMs": anchor["yellowstoneMinusAllenharkMs"],
                "reportSha256": evidence["calibrationReportSha256"],
                "clockDomainCalibrated": clock_domain_calibrated,
                "hostBootId": clock_domain.get("hostBootId"),
            },
        }
        evidence["matches"] += 1
    existing = {
        str(row.get("signature") or "") for row in read_jsonl(recovery_ledger_path)
    }
    new_rows = [
        recovered[str(row["signature"])] for row in targets
        if str(row["signature"]) in recovered
        and str(row["signature"]) not in existing
        and recovered[str(row["signature"])].get("source") == YELLOWSTONE_SOURCE
    ]
    if new_rows:
        recovery_ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with recovery_ledger_path.open("ab") as stream:
            for row in new_rows:
                stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    evidence["persistedNew"] = len(new_rows)
    return recovered, evidence


def initialize_output(args: argparse.Namespace) -> dict[str, Any]:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise RuntimeError("V3_CONTRACT_INVALID")
    integrity = contract.get("integrity") or {}
    if integrity.get("outcomesPermitted") is not False:
        raise RuntimeError("V2_OUTCOME_BLIND_CONTRACT_INVALID")
    if integrity.get("validationRowsPermittedBeforeFreeze") is not False:
        raise RuntimeError("V2_VALIDATION_SEAL_INVALID")
    args.output.mkdir(parents=True, exist_ok=True)
    frozen = args.output / "contract.json"
    if frozen.exists():
        if sha256_file(frozen) != sha256_file(args.contract):
            raise RuntimeError("V2_CONTRACT_HASH_MISMATCH")
    else:
        frozen.write_bytes(args.contract.read_bytes())
        os.chmod(frozen, 0o444)
    invalid_target = args.output / "invalid-collection-intervals.jsonl"
    if invalid_target.exists():
        if sha256_file(invalid_target) != sha256_file(args.invalid_intervals):
            raise RuntimeError("V2_INVALID_INTERVAL_HASH_MISMATCH")
    else:
        invalid_target.write_bytes(args.invalid_intervals.read_bytes())
        os.chmod(invalid_target, 0o444)
    (args.output / "development").mkdir(exist_ok=True)
    sealed = args.output / "validation-sealed"
    sealed.mkdir(exist_ok=True)
    os.chmod(sealed, 0o700)
    return contract


def open_state(path: pathlib.Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=120)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS decision_versions (
          version_sha TEXT PRIMARY KEY,
          signature TEXT NOT NULL,
          generation_sha TEXT NOT NULL,
          status TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS decision_versions_signature
          ON decision_versions(signature);
        CREATE TABLE IF NOT EXISTS current_decisions (
          signature TEXT PRIMARY KEY,
          mint TEXT NOT NULL,
          route TEXT NOT NULL,
          economic_family TEXT NOT NULL,
          block_time_ms INTEGER NOT NULL,
          slot INTEGER NOT NULL,
          transaction_index INTEGER NOT NULL,
          generation_sha TEXT NOT NULL,
          status TEXT NOT NULL,
          reasons_json TEXT NOT NULL,
          detail_json TEXT NOT NULL,
          choice_json TEXT,
          forensic_json TEXT,
          version_sha TEXT NOT NULL,
          updated_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS current_decisions_status_order
          ON current_decisions(status, block_time_ms, slot, transaction_index, signature);
        CREATE TABLE IF NOT EXISTS partition_generations (
          partition_hour TEXT PRIMARY KEY,
          generation_sha TEXT NOT NULL,
          exact_complete INTEGER NOT NULL,
          status_json TEXT NOT NULL,
          observed_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )
    connection.commit()
    return connection


def exact_evidence_snapshot(
    root: pathlib.Path, boundary_ns: int
) -> tuple[list[pathlib.Path], str, list[dict[str, Any]]]:
    hours = legacy.receive_hours(boundary_ns - MAX_AGE_NS, boundary_ns)
    paths: set[pathlib.Path] = set()
    statuses: list[dict[str, Any]] = []
    not_ready: list[dict[str, Any]] = []
    for hour in hours:
        status_path = root / "partitions" / hour / "reconciliation-status.json"
        if not status_path.exists():
            not_ready.append({"hour": hour, "reason": "STATUS_MISSING"})
            continue
        status = json.loads(status_path.read_text(encoding="utf-8"))
        summary = {
            "hour": hour,
            "temporalHourClosed": status.get("temporalHourClosed"),
            "inputStableDuringRun": status.get("inputStableDuringRun"),
            "exactReplayComplete": status.get("exactReplayComplete"),
            "inputFingerprintSha256": status.get("inputFingerprintSha256"),
            "manifestSha256": status.get("manifestSha256"),
            "joinedEvidenceIdsSha256": status.get("joinedEvidenceIdsSha256"),
            "reconciliationVersion": status.get("reconciliationVersion"),
            "inputEvidence": status.get("inputEvidence") or [],
        }
        statuses.append(summary)
        if not (
            summary["temporalHourClosed"] is True
            and summary["inputStableDuringRun"] is True
            and summary["exactReplayComplete"] is True
        ):
            coverage = status.get("coverage") or {}
            not_ready.append({
                "hour": hour,
                "reason": "EXACT_REPLAY_INCOMPLETE",
                "missingFinality": int(coverage.get("missingFinality") or 0),
                "missingRawTx": int(coverage.get("missingRawTx") or 0),
                "missingCanonicalOrdering": int(coverage.get("missingCanonicalOrdering") or 0),
                "decodeFailures": int(coverage.get("decodeFailures") or 0),
            })
            continue
        for item in summary["inputEvidence"]:
            path = root / str(item["path"])
            if not path.exists():
                not_ready.append({"hour": hour, "reason": "EVIDENCE_PATH_MISSING", "path": str(path)})
            else:
                paths.add(path)
    if not_ready:
        raise EvidenceNotReady(not_ready)
    generation_sha = sha256_bytes(canonical(statuses))
    return sorted(paths), generation_sha, statuses


def refresh_partition_generations(
    connection: sqlite3.Connection, root: pathlib.Path
) -> dict[str, Any]:
    """Index reconciliation identities without reading evidence payloads."""
    exact_hours: list[str] = []
    incomplete_hours: list[str] = []
    newest_status_utc: str | None = None
    for status_path in sorted(root.glob("partitions/*/*/*/*/reconciliation-status.json")):
        status = json.loads(status_path.read_text(encoding="utf-8"))
        hour = status_path.parent.relative_to(root / "partitions").as_posix()
        exact = bool(
            status.get("temporalHourClosed") is True
            and status.get("inputStableDuringRun") is True
            and status.get("exactReplayComplete") is True
        )
        generation_sha = sha256_bytes(canonical({
            "hour": hour,
            "inputFingerprintSha256": status.get("inputFingerprintSha256"),
            "manifestSha256": status.get("manifestSha256"),
            "joinedEvidenceIdsSha256": status.get("joinedEvidenceIdsSha256"),
            "reconciliationVersion": status.get("reconciliationVersion"),
            "exactReplayComplete": status.get("exactReplayComplete"),
        }))
        observed_utc = dt.datetime.fromtimestamp(
            status_path.stat().st_mtime, dt.timezone.utc
        ).isoformat().replace("+00:00", "Z")
        connection.execute(
            """INSERT INTO partition_generations
               (partition_hour,generation_sha,exact_complete,status_json,observed_utc)
               VALUES (?,?,?,?,?)
               ON CONFLICT(partition_hour) DO UPDATE SET
                 generation_sha=excluded.generation_sha,
                 exact_complete=excluded.exact_complete,
                 status_json=excluded.status_json,
                 observed_utc=excluded.observed_utc""",
            (hour, generation_sha, int(exact), json.dumps(status, sort_keys=True), observed_utc),
        )
        (exact_hours if exact else incomplete_hours).append(hour)
        if newest_status_utc is None or observed_utc > newest_status_utc:
            newest_status_utc = observed_utc
    return {
        "indexedPartitionN": len(exact_hours) + len(incomplete_hours),
        "exactCompletePartitionN": len(exact_hours),
        "incompletePartitionN": len(incomplete_hours),
        "latestExactCompleteHour": max(exact_hours) if exact_hours else None,
        "latestIncompleteHour": max(incomplete_hours) if incomplete_hours else None,
        "newestReconciliationStatusUtc": newest_status_utc,
    }


def economic_family(route: str, tx: dict[str, Any], mint: str) -> tuple[str, dict[str, Any]]:
    if route == "PUMPSWAP":
        return "PUMPSWAP", {"classifierVersion": CLASSIFIER_VERSION}
    if route == "PUMPSWAP_CPI":
        return "PUMPSWAP_CPI", {"classifierVersion": CLASSIFIER_VERSION}
    if route == "JUPITER_OR_ROUTER":
        return "ROUTER", {"classifierVersion": CLASSIFIER_VERSION}
    if route not in {"PUMP_BONDING_CURVE", "PUMP_CPI"}:
        return "OTHER_DEX", {"classifierVersion": CLASSIFIER_VERSION, "sourceRoute": route}
    sol_decode_error: Exception | None = None
    try:
        events = [
            row for row in decode_trade_events((tx.get("meta") or {}).get("logMessages") or [])
            if row.mint == mint and row.user == decision.WALLET and row.is_buy
        ]
    except Exception as error:
        events = []
        sol_decode_error = error
    if len(events) == 1:
        detail = {
            "classifierVersion": CLASSIFIER_VERSION,
            "sourceRoute": route,
            "matchingTeacherBuyEvents": 1,
            "quoteMints": [events[0].quote_mint],
            "instructionKinds": [events[0].ix_name],
        }
        if events[0].quote_mint in SOL_QUOTE_MINTS:
            return ("SOL_PUMP_CPI" if route == "PUMP_CPI" else "SOL_PUMP_CURVE"), detail
        return "NON_SOL_PUMP_V2", detail
    try:
        quote_events = [
            row for row in decode_quote_trade_events((tx.get("meta") or {}).get("logMessages") or [])
            if row.mint == mint and row.user == decision.WALLET and row.is_buy
        ]
    except Exception as error:
        quote_events = []
        quote_decode_error: Exception | None = error
    else:
        quote_decode_error = None
    detail = {
        "classifierVersion": CLASSIFIER_VERSION,
        "sourceRoute": route,
        "matchingTeacherBuyEvents": len(events),
        "matchingQuoteNativeTeacherBuyEvents": len(quote_events),
        "quoteMints": sorted({row.quote_mint for row in events + quote_events}),
        "instructionKinds": sorted({row.ix_name for row in events + quote_events}),
    }
    if sol_decode_error is not None:
        detail["solDecodeErrorType"] = type(sol_decode_error).__name__
        detail["solDecodeError"] = str(sol_decode_error)
    if quote_decode_error is not None:
        detail["quoteDecodeErrorType"] = type(quote_decode_error).__name__
    if len(quote_events) == 1:
        return "NON_SOL_PUMP_V2", detail
    return "AMBIGUOUS_PUMP_FAMILY", detail


def causal_before(
    event: decision.CausalEvent, boundary_ns: int, boundary_order: tuple[int, int, int, str]
) -> bool:
    return event.received_ns < boundary_ns and event.order < boundary_order


def is_sol_curve_sequence(events: list[decision.CausalEvent]) -> bool:
    return bool(events) and all(
        int(row.pre_state.virtual_sol) > 0 and int(row.post_state.virtual_sol) > 0
        for row in events
    )


def build_online_choice(
    selected: dict[str, Any],
    events: dict[str, list[decision.CausalEvent]],
    creates: dict[str, dict[str, Any]],
    incomplete: dict[str, list[tuple[tuple[int, int, int, str], str]]],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    """Build the live-observable risk set immediately before the teacher action.

    The canonical teacher acquisition plus the capture-first AllenHark receipt
    establishes the action and boundary.  A redundant copy of that action in
    the compact observer journal is forensic evidence, not a feature input.
    """
    boundary_ns = int(selected["entryReceivedAtUnixNs"])
    boundary_order = (
        int(selected["entrySlot"]), int(selected["entryTransactionIndex"]), -1, ""
    )
    creation_order = sorted(
        (int(row["receivedNs"]), mint) for mint, row in creates.items()
        if boundary_ns - MAX_AGE_NS <= int(row["receivedNs"]) < boundary_ns
        and tuple(row["order"]) < boundary_order
    )
    choices: list[dict[str, Any]] = []
    candidate_exclusions: collections.Counter[str] = collections.Counter()
    selected_exclusions: set[str] = set()
    selected_mint = str(selected["mint"])
    for create_ns, mint in creation_order:
        sequence = [
            row for row in events.get(mint, [])
            if causal_before(row, boundary_ns, boundary_order)
        ]
        # A canonical transaction with no realtime arrival was genuinely absent
        # from the online state at the decision boundary.  It belongs in the
        # Tier-A missingness audit, but using that later fact to reject an online
        # snapshot would leak finalized hindsight into the imitation cohort.
        missing = [
            reason for order, reason in incomplete.get(mint, [])
            if order < boundary_order and reason != "ALLENHARK_TRADE_ARRIVAL_MISSING"
        ]
        if missing:
            candidate_exclusions.update(set(missing))
            if mint == selected_mint:
                selected_exclusions.update(missing)
            continue
        if not is_sol_curve_sequence(sequence):
            candidate_exclusions["NON_SOL_OR_UNSUPPORTED_CURVE_STATE"] += 1
            if mint == selected_mint:
                selected_exclusions.add("NON_SOL_OR_UNSUPPORTED_CURVE_STATE")
            continue
        if not v1.reserve_continuous(sequence):
            candidate_exclusions["RESERVE_CONTINUITY_INCOMPLETE"] += 1
            if mint == selected_mint:
                selected_exclusions.add("RESERVE_CONTINUITY_INCOMPLETE")
            continue
        prior = sequence
        external = [row for row in prior if row.user != decision.WALLET]
        if not prior or not external:
            candidate_exclusions["NO_PRIOR_EXTERNAL_STATE"] += 1
            if mint == selected_mint:
                selected_exclusions.add("NO_PRIOR_EXTERNAL_STATE")
            continue
        if legacy.teacher_exposure_raw(prior) > 1:
            candidate_exclusions["TEACHER_ALREADY_EXPOSED"] += 1
            if mint == selected_mint:
                selected_exclusions.add("TEACHER_ALREADY_EXPOSED")
            continue
        last = prior[-1]
        if int(last.post_state.real_token) <= 0:
            candidate_exclusions["CURVE_NOT_EXECUTABLE"] += 1
            if mint == selected_mint:
                selected_exclusions.add("CURVE_NOT_EXECUTABLE")
            continue
        last_external = external[-1]
        choices.append({
            "label": mint == selected_mint,
            "mint": mint,
            "economicFamily": "SOL_PUMP_CURVE_STATE",
            "createReceivedAtUnixNs": create_ns,
            "stateSignature": last.signature,
            "stateSlot": last.slot,
            "stateTransactionIndex": last.transaction_index,
            "stateInstructionIndex": last.instruction_index,
            "stateReceivedAtUnixNs": last.received_ns,
            "lastExternalAgeMs": (boundary_ns - last_external.received_ns) / 1_000_000,
            "features": decision.state_features(
                last.post_state, boundary_ns, mint, last.creator, prior, create_ns
            ),
        })
    chosen = [row for row in choices if row["label"]]
    reasons: list[str] = []
    if len(chosen) != 1:
        reasons.append("SELECTED_MINT_ABSENT_FROM_ONLINE_UNIVERSE")
        selected_create = creates.get(selected_mint)
        if selected_create is None:
            selected_exclusions.add("SELECTED_CREATE_NOT_REALTIME_INDEXED")
        elif int(selected_create["receivedNs"]) >= boundary_ns:
            selected_exclusions.add("SELECTED_CREATE_NOT_CAUSALLY_BEFORE_BOUNDARY")
        elif boundary_ns - int(selected_create["receivedNs"]) > MAX_AGE_NS:
            selected_exclusions.add("SELECTED_OUTSIDE_LAUNCHED_120S")
        elif tuple(selected_create["order"]) >= boundary_order:
            selected_exclusions.add("SELECTED_CREATE_CANONICAL_ORDER_INVALID")
        elif not any(mint == selected_mint for _, mint in creation_order):
            selected_exclusions.add("SELECTED_CREATE_NOT_IN_FROZEN_UNIVERSE")
    if len(choices) <= 1:
        reasons.append("NO_CONTEMPORANEOUS_ALTERNATIVE")
    detail = {
        "candidateMintsWithRealtimeCreate": len(creation_order),
        "eligibleCandidateN": len(choices),
        "controlN": max(0, len(choices) - len(chosen)),
        "candidateExclusions": dict(sorted(candidate_exclusions.items())),
        "selectedExclusionReasons": sorted(selected_exclusions),
    }
    if reasons:
        return None, reasons, detail
    row = {
        "schema": SCHEMA + "_ONLINE_CHOICE_DECISION",
        "episodeId": selected["signature"],
        "signature": selected["signature"],
        "mint": selected_mint,
        "route": selected["route"],
        "economicFamily": selected["economicFamily"],
        "acquisitionClass": selected["acquisitionClass"],
        "decisionBoundary": {
            "arrivalUnixNs": boundary_ns,
            "slot": selected["entrySlot"],
            "transactionIndex": selected["entryTransactionIndex"],
            "source": "allenhark-subscribe-entries:capture-first",
            "semantics": "IMMEDIATELY_BEFORE_EXACT_TEACHER_ACTION",
        },
        "teacherActionProof": selected["teacherActionProof"],
        "controlN": len(choices) - 1,
        "states": choices,
        "evidenceSummary": detail,
        "outcomesRead": 0,
        "submissions": 0,
    }
    v1.assert_outcome_blind(row)
    return row, [], detail


def scan_cache_key(paths: list[pathlib.Path], boundaries: list[int]) -> str:
    return sha256_bytes(canonical({
        "scanner": "SYMMETRIC_SCAN_UNIVERSE_V1",
        "paths": [
            {"path": str(path), "size": path.stat().st_size, "mtimeNs": path.stat().st_mtime_ns}
            for path in paths
        ],
        "boundaries": sorted(boundaries),
    }))


def load_or_scan_universe(
    output: pathlib.Path,
    paths: list[pathlib.Path],
    compact_arrivals: pathlib.Path,
    boundaries: list[int],
    evidence_hashes: dict[str, str],
) -> tuple[Any, Any, Any, dict[str, Any], bool]:
    cache_key = scan_cache_key(paths, boundaries)
    cache_dir = output / "causal-cache"
    cache_dir.mkdir(exist_ok=True)
    cache_path = cache_dir / f"{cache_key}.pickle.gz"
    if cache_path.exists():
        with gzip.open(cache_path, "rb") as stream:
            payload = pickle.load(stream)
        if payload.get("cacheKey") != cache_key:
            raise RuntimeError("CAUSAL_CACHE_KEY_MISMATCH")
        evidence = dict(payload["evidence"])
        evidence["evidenceFilesImportedThisRun"] = 0
        evidence["importCounts"] = {}
        evidence["generationCacheHit"] = True
        return (
            payload["events"], payload["creates"], payload["incomplete"],
            evidence, True,
        )
    events, creates, incomplete, evidence = indexed_state.ensure_and_materialize(
        output / "causal-state-plane-v1.sqlite3",
        paths,
        evidence_hashes,
        compact_arrivals,
        boundaries,
        integrity_pragma="none",
    )
    evidence["generationCacheHit"] = False
    temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
    with gzip.open(temporary, "wb", compresslevel=3) as stream:
        pickle.dump({
            "cacheKey": cache_key,
            "events": events,
            "creates": creates,
            "incomplete": incomplete,
            "evidence": evidence,
        }, stream, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(temporary, cache_path)
    return events, creates, incomplete, evidence, False


def current_for_generation(
    connection: sqlite3.Connection, signature: str, generation_sha: str
) -> bool:
    row = connection.execute(
        "SELECT generation_sha FROM current_decisions WHERE signature=?", (signature,)
    ).fetchone()
    return bool(row and row[0] == generation_sha)


def checkpoint(
    connection: sqlite3.Connection,
    episode: dict[str, Any],
    family: str,
    generation_sha: str,
    status: str,
    reasons: list[str],
    detail: dict[str, Any],
    choice: dict[str, Any] | None = None,
    forensic: dict[str, Any] | None = None,
) -> None:
    payload = {
        "schema": SCHEMA + "_DECISION_VERSION",
        "signature": str(episode["signature"]),
        "mint": str(episode["mint"]),
        "route": str(episode["route"]),
        "economicFamily": family,
        "generationSha256": generation_sha,
        "status": status,
        "reasons": sorted(set(reasons)),
        "detail": detail,
        "choice": choice,
        "forensicTierA": forensic,
        "outcomesRead": 0,
        "submissions": 0,
    }
    v1.assert_outcome_blind(payload)
    payload_json = canonical(payload).decode()
    version_sha = sha256_bytes(payload_json.encode())
    timestamp = now_utc()
    signature = str(episode["signature"])
    block_time_ms = int(episode.get("blockTimeMs") or 0)
    connection.execute(
        "INSERT OR IGNORE INTO decision_versions VALUES (?,?,?,?,?,?)",
        (version_sha, signature, generation_sha, status, payload_json, timestamp),
    )
    connection.execute(
        """INSERT INTO current_decisions
        (signature,mint,route,economic_family,block_time_ms,slot,transaction_index,
         generation_sha,status,reasons_json,detail_json,choice_json,forensic_json,
         version_sha,updated_utc)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(signature) DO UPDATE SET
          mint=excluded.mint,route=excluded.route,economic_family=excluded.economic_family,
          block_time_ms=excluded.block_time_ms,slot=excluded.slot,
          transaction_index=excluded.transaction_index,generation_sha=excluded.generation_sha,
          status=excluded.status,reasons_json=excluded.reasons_json,
          detail_json=excluded.detail_json,choice_json=excluded.choice_json,
          forensic_json=excluded.forensic_json,version_sha=excluded.version_sha,
          updated_utc=excluded.updated_utc""",
        (
            signature, str(episode["mint"]), str(episode["route"]), family,
            block_time_ms, int(episode.get("slot") or 0),
            int(episode.get("transactionIndex") if episode.get("transactionIndex") is not None else -1),
            generation_sha, status, json.dumps(sorted(set(reasons))),
            json.dumps(detail, sort_keys=True),
            json.dumps(choice, sort_keys=True) if choice is not None else None,
            json.dumps(forensic, sort_keys=True) if forensic is not None else None,
            version_sha, timestamp,
        ),
    )


def active_collection_hours(start_ms: int, end_ms: int, intervals: list[tuple[int, int]]) -> float:
    duration = max(0, end_ms - start_ms)
    for left, right in intervals:
        duration -= max(0, min(end_ms, right) - max(start_ms, left))
    return max(0, duration) / 3_600_000


def publish_development(connection: sqlite3.Connection, output: pathlib.Path) -> None:
    rows = connection.execute(
        """SELECT signature,choice_json FROM current_decisions
           WHERE status='ONLINE_ELIGIBLE'
           ORDER BY block_time_ms,slot,transaction_index,signature LIMIT ?""",
        (DEVELOPMENT_N,),
    ).fetchall()
    if len(rows) < DEVELOPMENT_N:
        return
    freeze_path = output / "development" / "freeze.json"
    signatures = [row[0] for row in rows]
    if freeze_path.exists():
        frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
        if frozen.get("signatures") != signatures:
            raise RuntimeError("FROZEN_DEVELOPMENT_MEMBERSHIP_CHANGED")
        return
    destination = output / "development" / "choice-sets.jsonl.gz"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            for ordinal, (_, payload) in enumerate(rows, 1):
                choice = json.loads(payload) | {"eligibleOrdinal": ordinal, "split": "DEVELOPMENT"}
                v1.assert_outcome_blind(choice)
                stream.write(canonical(choice) + b"\n")
    os.replace(temporary, destination)
    atomic_json(freeze_path, {
        "schema": SCHEMA + "_DEVELOPMENT_FREEZE",
        "frozenAtUtc": now_utc(),
        "developmentN": DEVELOPMENT_N,
        "signatures": signatures,
        "choiceSetsSha256": sha256_file(destination),
        "outcomesRead": 0,
        "validationRowsRead": 0,
    })


def run(args: argparse.Namespace) -> dict[str, Any]:
    run_started_monotonic = time.monotonic()
    run_started_utc = now_utc()
    contract = initialize_output(args)
    # Both the selected-action recovery and causal state materializer import the
    # same legacy module. Replace its exact lookup only for this V3 process.
    v1.targeted_compact_arrivals_by_slot = lambda path, targets, start, end: (
        indexed_compact_arrivals_by_slot(path, targets, start, end, args.compact_slot_index)
    )
    connection = open_state(args.output / "causal-state-v2.sqlite3")
    reconciliation_frontier = refresh_partition_generations(connection, args.exact_root)
    start_ms = int(tier_a.parse_utc(str(contract["cohortStartUtc"])).timestamp() * 1000)
    end_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000) - 2_000
    env = capture.denominator.load_env(args.env)
    signature_rows, transactions = capture.fetch_prospective_population(
        env["SECURE_RPC_URL"], start_ms, end_ms, args.output / "wallet-rpc-cache"
    )
    classified, _ = capture.classify_successful_transactions(
        signature_rows, transactions, start_ms, end_ms
    )
    episodes = [dict(row) for row in classified if row["isIndependentEntryEpisode"]]
    for episode in episodes:
        tx = transactions[str(episode["signature"])]
        episode["blockTimeMs"] = int((tx.get("blockTime") or 0) * 1000)
    intervals = v1.invalid_intervals(args.output)
    candidates = [
        row for row in read_jsonl(args.candidates)
        if int(row.get("receivedAtUnixNs") or 0) // 1_000_000 >= start_ms
    ]
    candidate_by_signature = {
        str(row.get("signature") or ""): row for row in candidates
        if not v1.in_invalid_interval(
            int(row.get("receivedAtUnixNs") or 0) // 1_000_000, intervals
        )
    }
    yellowstone_recovery_ledger = args.output / "yellowstone-exact-signature-candidates-v1.jsonl"
    for row in read_jsonl(yellowstone_recovery_ledger):
        signature = str(row.get("signature") or "")
        if signature and signature not in candidate_by_signature:
            candidate_by_signature[signature] = row
    development_boundary_ms: int | None = None
    if (args.output / "development" / "freeze.json").exists():
        boundary = connection.execute(
            """SELECT MAX(block_time_ms) FROM (
                 SELECT block_time_ms FROM current_decisions
                 WHERE status='ONLINE_ELIGIBLE'
                 ORDER BY block_time_ms,slot,transaction_index,signature LIMIT ?
               )""",
            (DEVELOPMENT_N,),
        ).fetchone()
        development_boundary_ms = int(boundary[0]) if boundary and boundary[0] is not None else None
        if development_boundary_ms is None:
            raise RuntimeError("FROZEN_DEVELOPMENT_BOUNDARY_UNAVAILABLE")
    recovery_episodes = [
        episode for episode in episodes
        if economic_family(
            str(episode["route"]), transactions[str(episode["signature"])],
            str(episode["mint"]),
        )[0] in SUPPORTED_ONLINE_FAMILIES
    ]
    candidate_by_signature, capture_all_evidence = recover_capture_all_candidates(
        args.compact_arrivals,
        recovery_episodes,
        candidate_by_signature,
        development_boundary_ms,
        {
            str(signature) for signature, detail_json in connection.execute(
                "SELECT signature,detail_json FROM current_decisions"
            )
            if json.loads(detail_json).get("realtimeCandidateSource") == CAPTURE_ALL_SOURCE
            or json.loads(detail_json).get("captureAllJoinAttempted") is True
        },
    )
    capture_all_attempted = set(
        capture_all_evidence.pop("_captureAllJoinAttemptedSignatures", [])
    )
    yellowstone_completed = {
        str(signature) for signature, detail_json in connection.execute(
            "SELECT signature,detail_json FROM current_decisions"
        )
        if json.loads(detail_json).get("realtimeCandidateSource") == YELLOWSTONE_SOURCE
    }
    candidate_by_signature, yellowstone_evidence = recover_yellowstone_candidates(
        args.yellowstone_direct,
        recovery_episodes,
        candidate_by_signature,
        development_boundary_ms,
        yellowstone_completed,
        args.clock_calibration,
        yellowstone_recovery_ledger,
    )
    compact_health = json.loads(args.compact_observer_health.read_text(encoding="utf-8"))
    continuity_segments = census_v3.compact_continuity_segments(
        args.compact_observer_continuity, compact_health
    )
    ready_work: list[tuple[
        dict[str, Any], dict[str, Any], str, dict[str, Any],
        list[dict[str, Any]], str, list[pathlib.Path],
    ]] = []
    processed = 0
    skipped_cached = 0
    for episode in episodes:
        signature = str(episode["signature"])
        tx = transactions[signature]
        candidate = candidate_by_signature.get(signature)
        block_ms = int(episode["blockTimeMs"])
        family, family_detail = economic_family(str(episode["route"]), tx, str(episode["mint"]))
        if v1.in_invalid_interval(block_ms, intervals) or (
            candidate and v1.in_invalid_interval(
                int(candidate["receivedAtUnixNs"]) // 1_000_000, intervals
            )
        ):
            checkpoint(connection, episode, family, STATIC_GENERATION, "TERMINAL", [
                "PAUSED_INVALID_COLLECTION_INTERVAL"
            ], {"economicFamily": family_detail})
            continue
        if family not in SUPPORTED_ONLINE_FAMILIES:
            checkpoint(connection, episode, family, STATIC_GENERATION, "TERMINAL", [
                "ECONOMIC_FAMILY_OUTSIDE_SOL_PUMP_ONLINE_COHORT"
            ], {"economicFamily": family_detail})
            continue
        if candidate is None:
            attempted_capture_all = signature in capture_all_attempted
            status = (
                "TERMINAL"
                if attempted_capture_all or end_ms - block_ms > 600_000
                else "DEFERRED"
            )
            checkpoint(connection, episode, family, "REALTIME_CANDIDATE_V1", status, [
                "CANONICAL_ACQUISITION_WITHOUT_REALTIME_CANDIDATE"
            ], {
                "economicFamily": family_detail,
                "captureAllJoinAttempted": attempted_capture_all,
            })
            continue
        if str(candidate.get("source")) not in {
            "allenhark-subscribe-entries", CAPTURE_ALL_SOURCE, YELLOWSTONE_SOURCE,
        }:
            checkpoint(connection, episode, family, STATIC_GENERATION, "TERMINAL", [
                "TEACHER_ACTION_NOT_OBSERVED_BY_FROZEN_ALLENHARK_SOURCE"
            ], {"economicFamily": family_detail, "candidateSource": candidate.get("source")})
            continue
        boundary_ns = int(candidate["receivedAtUnixNs"])
        try:
            paths, generation_sha, statuses = exact_evidence_snapshot(args.exact_root, boundary_ns)
        except EvidenceNotReady as error:
            generation_sha = sha256_bytes(canonical({
                "implementationVersion": IMPLEMENTATION_VERSION,
                "notReadyHours": error.hours,
            }))
            checkpoint(connection, episode, family, generation_sha, "DEFERRED", [
                "EXACT_RECONCILIATION_NOT_READY"
            ], {"economicFamily": family_detail, "hours": error.hours})
            continue
        generation_sha = sha256_bytes(canonical({
            "implementationVersion": decision_generation_version(candidate),
            "reconciliationGenerationSha256": generation_sha,
        }))
        if current_for_generation(connection, signature, generation_sha):
            skipped_cached += 1
            continue
        ready_work.append((
            episode, candidate, family, family_detail, statuses, generation_sha, paths
        ))

    evidence_totals: collections.Counter[str] = collections.Counter()
    causal_evidence: dict[str, Any] = {}
    causal_cache_hit = False
    scan_paths: list[pathlib.Path] = []
    ready_work_total = len(ready_work)
    ready_work_deferred_by_hour_batch = 0
    if ready_work:
        ready_hours = sorted({
            int(candidate["receivedAtUnixNs"]) // 3_600_000_000_000
            for _, candidate, *_ in ready_work
        })[:MAX_READY_HOURS_PER_RUN]
        hour_work = [
            work for work in ready_work
            if int(work[1]["receivedAtUnixNs"]) // 3_600_000_000_000 in ready_hours
        ]
        ready_work_deferred_by_hour_batch = len(ready_work) - len(hour_work)
        ready_work = hour_work
    if ready_work:
        scan_paths = sorted({path for *_, item_paths in ready_work for path in item_paths})
        boundaries = [int(candidate["receivedAtUnixNs"]) for _, candidate, *_ in ready_work]
        evidence_hashes = {
            str((args.exact_root / str(item["path"])).resolve()): str(item.get("sha256") or "")
            for work in ready_work
            for status in work[4]
            for item in (status.get("inputEvidence") or [])
        }
        events, creates, incomplete, evidence, causal_cache_hit = load_or_scan_universe(
            args.output, scan_paths, args.compact_arrivals, boundaries, evidence_hashes
        )
        causal_evidence = evidence
        evidence_totals.update({key: value for key, value in evidence.items() if isinstance(value, int)})
        for episode, candidate, family, family_detail, statuses, generation_sha, _ in ready_work:
            processed += 1
            signature = str(episode["signature"])
            boundary_ns = int(candidate["receivedAtUnixNs"])
            selected = {
                "signature": signature,
                "mint": str(episode["mint"]),
                "route": str(episode["route"]),
                "economicFamily": family,
                "acquisitionClass": str(episode["classification"]),
                "entryReceivedAtUnixNs": boundary_ns,
                "entrySlot": int(candidate["slot"]),
                "entryTransactionIndex": int(
                    episode.get("transactionIndex") if episode.get("transactionIndex") is not None else -1
                ),
                "teacherActionProof": {
                    "canonicalFinalizedSuccess": True,
                    "captureSource": candidate["source"],
                    "captureStreamSequence": candidate.get("streamSequence"),
                    "classifierVersion": CLASSIFIER_VERSION,
                    "economicFamilyEvidence": family_detail,
                    "timestampResolution": candidate.get("timestampResolution", "observer-nanoseconds"),
                    "timestampInferred": bool(candidate.get("timestampInferred", False)),
                    "sameSignatureClockCalibration": candidate.get("calibrationAnchor"),
                },
            }
            segment = census_v3.covering_continuity_segment(
                boundary_ns - MAX_AGE_NS, boundary_ns, continuity_segments
            )
            if segment is None:
                checkpoint(connection, episode, family, generation_sha, "TERMINAL", [
                    "GLOBAL_REALTIME_CONTINUITY_NOT_PROVEN_FOR_120S_UNIVERSE"
                ], {
                    "economicFamily": family_detail,
                    "reconciliation": statuses,
                    "realtimeCandidateSource": candidate["source"],
                })
                continue
            choice, reasons, detail = build_online_choice(selected, events, creates, incomplete)
            strict_row, strict_reasons, strict_detail = v1.build_choice(
                selected, events, creates, incomplete
            )
            forensic = {
                "semantics": FORENSIC_SEMANTICS,
                "eligible": strict_row is not None,
                "reasons": strict_reasons,
                "detail": strict_detail,
            }
            detail = detail | {
                "economicFamily": family_detail,
                "realtimeCandidateSource": candidate["source"],
                "reconciliationGenerationSha256": generation_sha,
                "reconciliation": statuses,
                "fullUniverseContinuitySegment": segment,
            }
            if choice is not None:
                choice["sourceProof"] = {
                    "contractSha256": sha256_file(args.output / "contract.json"),
                    "reconciliationGenerationSha256": generation_sha,
                    "exactLedgerRoot": str(args.exact_root),
                    "allenharkArrivalLedger": str(args.compact_arrivals),
                    "yellowstoneDirectLedger": str(args.yellowstone_direct),
                    "clockCalibrationReport": str(args.clock_calibration),
                    "onlineSemantics": ONLINE_SEMANTICS,
                    "forensicTierAEligible": forensic["eligible"],
                }
                checkpoint(
                    connection, episode, family, generation_sha, "ONLINE_ELIGIBLE", [],
                    detail, choice, forensic,
                )
            else:
                checkpoint(
                    connection, episode, family, generation_sha, "TERMINAL", reasons,
                    detail, None, forensic,
                )
    connection.commit()

    publish_development(connection, args.output)
    status_counts = dict(connection.execute(
        "SELECT status,COUNT(*) FROM current_decisions GROUP BY status"
    ).fetchall())
    reason_counts: collections.Counter[str] = collections.Counter()
    family_counts = dict(connection.execute(
        "SELECT economic_family,COUNT(*) FROM current_decisions GROUP BY economic_family"
    ).fetchall())
    forensic_eligible = 0
    for reasons_json, forensic_json in connection.execute(
        "SELECT reasons_json,forensic_json FROM current_decisions"
    ):
        reason_counts.update(json.loads(reasons_json))
        if forensic_json and json.loads(forensic_json).get("eligible"):
            forensic_eligible += 1
    online_n = int(status_counts.get("ONLINE_ELIGIBLE", 0))
    active_hours = active_collection_hours(start_ms, end_ms, intervals)
    decision_versions_n = int(connection.execute(
        "SELECT COUNT(*) FROM decision_versions"
    ).fetchone()[0])
    current_decisions_n = int(connection.execute(
        "SELECT COUNT(*) FROM current_decisions"
    ).fetchone()[0])
    status = {
        "schema": SCHEMA + "_STATUS",
        "implementationVersion": IMPLEMENTATION_VERSION,
        "materializerSourceSha256": sha256_file(pathlib.Path(__file__)),
        "indexedStateSourceSha256": sha256_file(pathlib.Path(indexed_state.__file__)),
        "atUtc": now_utc(),
        "runStartedAtUtc": run_started_utc,
        "runDurationMs": round((time.monotonic() - run_started_monotonic) * 1000, 3),
        "cohortStartUtc": contract["cohortStartUtc"],
        "canonicalIndependentEntryEpisodes": len(episodes),
        "nonPausedIndependentEpisodes": sum(
            not v1.in_invalid_interval(int(row["blockTimeMs"]), intervals) for row in episodes
        ),
        "activeCollectionHours": active_hours,
        "rawNonPausedEpisodesPerHour": (
            sum(not v1.in_invalid_interval(int(row["blockTimeMs"]), intervals) for row in episodes) / active_hours
            if active_hours else None
        ),
        "onlineEligibleN": online_n,
        "developmentEligibleN": min(online_n, DEVELOPMENT_N),
        "developmentTargetN": DEVELOPMENT_N,
        "remainingToDevelopmentN350": max(0, DEVELOPMENT_N - online_n),
        "sealedValidationEligibleN": max(0, online_n - DEVELOPMENT_N),
        "sealedValidationTargetN": VALIDATION_N,
        "forensicTierAEligibleN": forensic_eligible,
        "statusCounts": status_counts,
        "exclusionsByReason": dict(sorted(reason_counts.items())),
        "economicFamilies": family_counts,
        "processedExactGenerationThisRun": processed,
        "skippedCachedGenerationThisRun": skipped_cached,
        "readyWorkN": len(ready_work),
        "readyWorkTotalBeforeHourBatch": ready_work_total,
        "readyWorkDeferredByHourBatch": ready_work_deferred_by_hour_batch,
        "causalUniverseScanRequiredThisRun": bool(
            causal_evidence.get("evidenceFilesImportedThisRun", 0)
        ),
        "causalUniverseCacheUsedThisRun": bool(ready_work and causal_cache_hit),
        "causalStatePlaneQueriedThisRun": bool(ready_work and not causal_cache_hit),
        "evidencePathCountThisRun": len(scan_paths),
        "evidenceBytesIndexedThisRun": int(
            (causal_evidence.get("importCounts") or {}).get("evidenceBytes", 0)
        ),
        "evidenceScannedThisRun": dict(sorted(evidence_totals.items())),
        "causalStatePlaneThisRun": causal_evidence,
        "captureAllFinalizedJoinThisRun": capture_all_evidence,
        "yellowstoneExactSignatureJoinThisRun": yellowstone_evidence,
        "realtimeSourcePrecedence": [
            "allenhark-subscribe-entries", CAPTURE_ALL_SOURCE, YELLOWSTONE_SOURCE,
        ],
        "causalCacheHitThisRun": causal_cache_hit,
        "decisionVersionsN": decision_versions_n,
        "currentDecisionsN": current_decisions_n,
        "supersededDecisionVersionsN": max(0, decision_versions_n - current_decisions_n),
        "reconciliationFrontier": reconciliation_frontier,
        "materializationMode": "VERSIONED_SQLITE_INCREMENTAL_EXACT_GENERATIONS_V2",
        "onlineAndForensicPopulationsSeparated": True,
        "developmentFreezeReady": (args.output / "development" / "freeze.json").exists(),
        "validationSealed": True,
        "validationRowsRead": 0,
        "outcomesRead": 0,
        "submissions": 0,
        "rawCollectionActive": True,
    }
    v1.assert_outcome_blind(status)
    atomic_json(args.output / "status.json", status)
    connection.execute(
        "INSERT OR REPLACE INTO metadata VALUES (?,?)", ("lastStatus", json.dumps(status, sort_keys=True))
    )
    connection.commit()
    connection.close()
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--invalid-intervals", type=pathlib.Path, required=True)
    parser.add_argument("--candidates", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--compact-slot-index", type=pathlib.Path, required=True)
    parser.add_argument("--compact-observer-health", type=pathlib.Path, required=True)
    parser.add_argument("--compact-observer-continuity", type=pathlib.Path, required=True)
    parser.add_argument("--yellowstone-direct", type=pathlib.Path, required=True)
    parser.add_argument("--clock-calibration", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
