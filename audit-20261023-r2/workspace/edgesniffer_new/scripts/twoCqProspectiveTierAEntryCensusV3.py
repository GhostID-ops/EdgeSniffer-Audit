#!/usr/bin/env python3
"""Future-only, outcome-blind official Tier-A census for the 2CQ wallet."""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import datetime as dt
import gc
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
from typing import Any

import twoCqCaptureFirstDetectorRepairV1 as capture
import twoCqPostFixEndToEndCollectionDiagnosticV1 as tier_a


SCHEMA = "2CQ_PROSPECTIVE_TIER_A_ENTRY_CENSUS_V3"
TARGET_N = 500
DEVELOPMENT_N = 350
VALIDATION_N = 150
COMPACT_FILTER = pathlib.Path(__file__).with_name("compact-arrival-filter-v1")
RECONCILIATION_BATCH_EPISODES = 16
SUPPORTED_TIER_A_ROUTES = {"PUMP_BONDING_CURVE", "PUMP_CPI"}


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def append_jsonl(path: pathlib.Path, rows: list[dict[str, Any]], mode: int = 0o644) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def eligible_rows_by_signature(root: pathlib.Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for relative in ("development/eligible.jsonl", "validation-sealed/eligible.jsonl"):
        for row in read_jsonl(root / relative):
            signature = str(row.get("signature") or "")
            if signature:
                rows[signature] = row
    return rows


def pending_episode_rows(
    episodes: list[dict[str, Any]],
    eligible_signatures: set[str],
    terminal_statuses: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only episodes that have never received a durable decision."""
    processed = eligible_signatures | set(terminal_statuses)
    return [row for row in episodes if str(row["signature"]) not in processed]


def checkpoint_batch(
    root: pathlib.Path,
    state: dict[str, Any],
    batch_eligible: list[dict[str, Any]],
    batch_statuses: dict[str, dict[str, Any]],
) -> None:
    """Durably publish one reconciliation batch without duplicating entries."""
    existing = eligible_rows_by_signature(root)
    fresh = [row for row in batch_eligible if str(row["signature"]) not in existing]
    append_jsonl(
        root / "development" / "eligible.jsonl",
        [row for row in fresh if row["split"] == "DEVELOPMENT"],
    )
    append_jsonl(
        root / "validation-sealed" / "eligible.jsonl",
        [row for row in fresh if row["split"] == "UNTOUCHED_VALIDATION"],
        0o600,
    )
    eligible_signatures = list(dict.fromkeys([
        *(state.get("eligibleSignatures") or []),
        *existing,
        *(str(row["signature"]) for row in fresh),
    ]))
    statuses = dict(state.get("lastStatuses") or {})
    statuses.update(batch_statuses)
    state.update({
        "eligibleSignatures": eligible_signatures,
        "lastStatuses": statuses,
        "lastCheckpointUtc": now_utc(),
        "lastRunUtc": now_utc(),
    })
    atomic_json(root / "state.json", state)


def targeted_compact_arrivals(
    path: pathlib.Path, target_digests: set[bytes]
) -> dict[bytes, tuple[int, int]]:
    """Use the native journal scanner without changing the frozen evaluator."""
    if not COMPACT_FILTER.exists() or not os.access(COMPACT_FILTER, os.X_OK):
        return tier_a.compact_arrivals(path, target_digests)[0]
    arrivals: dict[bytes, tuple[int, int]] = {}
    with tempfile.NamedTemporaryFile() as targets:
        for digest in target_digests:
            targets.write(digest)
        targets.flush()
        # The native helper owns the target hash set while it scans. Releasing
        # the Python copy avoids holding two large representations at once.
        target_digests.clear()
        gc.collect()
        process = subprocess.Popen(
            [str(COMPACT_FILTER), str(path), targets.name],
            stdout=subprocess.PIPE,
        )
        assert process.stdout is not None
        while True:
            raw = process.stdout.read(tier_a.COMPACT_RECORD.size)
            if not raw:
                break
            if len(raw) != tier_a.COMPACT_RECORD.size:
                process.kill()
                raise RuntimeError("COMPACT_FILTER_TRUNCATED_OUTPUT")
            digest, received_ns, slot = tier_a.COMPACT_RECORD.unpack(raw)
            prior = arrivals.get(digest)
            candidate = (int(received_ns), int(slot))
            if prior is None or candidate < prior:
                arrivals[digest] = candidate
        if process.wait() != 0:
            raise RuntimeError(f"COMPACT_FILTER_FAILED:{process.returncode}")
    return arrivals


def prewarm_finalized_history(
    url: str,
    rows: list[dict[str, Any]],
    cache: pathlib.Path,
    max_workers: int = 8,
) -> None:
    """Populate the frozen evaluator's immutable RPC cache with bounded I/O."""
    by_signature = {
        str(row.get("signature") or ""): row
        for row in rows
        if row.get("signature")
    }

    def fetch_one(item: tuple[str, dict[str, Any]]) -> int | None:
        signature, row = item
        try:
            tx = tier_a.transaction(url, signature, cache)
        except Exception:
            return None
        if tx is None or (tx.get("meta") or {}).get("err") is not None:
            return None
        logs = (tx.get("meta") or {}).get("logMessages") or []
        if not any(tier_a.PUMP_PROGRAM in line and "invoke" in line for line in logs):
            return None
        return int(tx.get("slot") or row.get("slot") or 0) or None

    slots: set[int] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for slot in executor.map(fetch_one, by_signature.items()):
            if slot is not None:
                slots.add(slot)

    def fetch_block_index(slot: int) -> None:
        try:
            tier_a.block_transaction_indexes(url, slot, cache)
        except Exception:
            # The frozen evaluator remains authoritative and records any
            # unresolved fetch exactly as it did before prewarming.
            return

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(fetch_block_index, sorted(slots)))


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("rb") as stream:
        for raw in stream:
            if not raw.strip():
                continue
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return rows


def compact_continuity_segments(
    continuity_path: pathlib.Path,
    health: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build conservative connected intervals for the compact arrival source."""
    events = sorted(
        read_jsonl(continuity_path),
        key=lambda row: tier_a.parse_utc(str(row["timestampUtc"])),
    )
    open_segments: dict[str, int] = {}
    segments: list[dict[str, Any]] = []
    for row in events:
        process = str(row.get("processInstance") or "")
        event = str(row.get("event") or "")
        if not process or not row.get("timestampUtc"):
            continue
        timestamp_ns = int(
            tier_a.parse_utc(str(row["timestampUtc"])).timestamp() * 1_000_000_000
        )
        if event == "CONNECTED":
            # A replacement process conservatively terminates any prior process
            # that did not get a shutdown callback.
            for prior_process, start_ns in list(open_segments.items()):
                if prior_process == process:
                    continue
                if timestamp_ns > start_ns:
                    segments.append({
                        "processInstance": prior_process,
                        "startUnixNs": start_ns,
                        "endUnixNs": timestamp_ns,
                        "endReason": "SUPERSEDED_BY_NEW_PROCESS",
                    })
                del open_segments[prior_process]
            prior_start = open_segments.get(process)
            if prior_start is not None and timestamp_ns > prior_start:
                segments.append({
                    "processInstance": process,
                    "startUnixNs": prior_start,
                    "endUnixNs": timestamp_ns,
                    "endReason": "RECONNECTED",
                })
            open_segments[process] = timestamp_ns
        elif event in {"DISCONNECTED", "DISCONNECTED_ERROR"}:
            start_ns = open_segments.pop(process, None)
            if start_ns is not None and timestamp_ns > start_ns:
                segments.append({
                    "processInstance": process,
                    "startUnixNs": start_ns,
                    "endUnixNs": timestamp_ns,
                    "endReason": event,
                })

    current_process = str(health.get("processInstance") or "")
    current_start = open_segments.get(current_process)
    last_receipt = health.get("lastReceiptAtUtc")
    if health.get("connected") is True and current_start is not None and last_receipt:
        end_ns = int(tier_a.parse_utc(str(last_receipt)).timestamp() * 1_000_000_000)
        if end_ns >= current_start:
            segments.append({
                "processInstance": current_process,
                "startUnixNs": current_start,
                "endUnixNs": end_ns,
                "endReason": "CURRENT_HEALTH_BOUNDARY",
            })
    return sorted(segments, key=lambda row: (row["startUnixNs"], row["endUnixNs"]))


def covering_continuity_segment(
    start_ns: int,
    end_ns: int,
    segments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if start_ns <= 0 or end_ns <= start_ns:
        return None
    return next(
        (
            segment
            for segment in segments
            if segment["startUnixNs"] <= start_ns
            and end_ns <= segment["endUnixNs"]
        ),
        None,
    )


def invalid_intervals(root: pathlib.Path) -> list[tuple[int, int]]:
    path = root / "invalid-collection-intervals.jsonl"
    intervals: list[tuple[int, int]] = []
    for row in read_jsonl(path):
        start = int(tier_a.parse_utc(str(row["pauseStartUtc"])).timestamp() * 1000)
        end_value = row.get("pauseEndUtc")
        if end_value is None:
            raise RuntimeError("OPEN_INVALID_COLLECTION_INTERVAL_FAIL_CLOSED")
        end = int(tier_a.parse_utc(str(end_value)).timestamp() * 1000)
        if end <= start:
            raise RuntimeError("INVALID_COLLECTION_INTERVAL_ORDER")
        intervals.append((start, end))
    return intervals


def in_invalid_interval(timestamp_ms: int, intervals: list[tuple[int, int]]) -> bool:
    return any(start <= timestamp_ms < end for start, end in intervals)


def episode_in_invalid_interval(
    episode: dict[str, Any], intervals: list[tuple[int, int]]
) -> bool:
    value = episode.get("blockTimeUtc")
    if not value:
        return False
    timestamp_ms = int(tier_a.parse_utc(str(value)).timestamp() * 1000)
    return in_invalid_interval(timestamp_ms, intervals)


def initialize(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    contract_path = args.output / "cohort-contract.json"
    if contract_path.exists():
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        expected = {
            "schema": SCHEMA + "_CONTRACT",
            "targetEligibleN": TARGET_N,
            "developmentN": DEVELOPMENT_N,
            "sealedValidationN": VALIDATION_N,
            "captureFirstBinarySha256": args.capture_binary_sha256,
            "tierAEvaluatorSha256": sha256_file(args.tier_a_evaluator),
            "researchSpecSha256": sha256_file(args.research_spec),
        }
        for key, value in expected.items():
            if contract.get(key) != value:
                raise RuntimeError(f"OFFICIAL_COHORT_FREEZE_MISMATCH:{key}")
        return contract

    started = now_utc()
    contract = {
        "schema": SCHEMA + "_CONTRACT",
        "officialCohortStartUtc": started,
        "officialCohortStartUnixMs": int(tier_a.parse_utc(started).timestamp() * 1000),
        "targetEligibleN": TARGET_N,
        "developmentN": DEVELOPMENT_N,
        "sealedValidationN": VALIDATION_N,
        "captureFirstBinarySha256": args.capture_binary_sha256,
        "tierAEvaluatorPath": str(args.tier_a_evaluator),
        "tierAEvaluatorSha256": sha256_file(args.tier_a_evaluator),
        "researchSpecPath": str(args.research_spec),
        "researchSpecSha256": sha256_file(args.research_spec),
        "candidateJournal": str(args.candidates),
        "allenharkArrivalLedger": str(args.compact_arrivals),
        "allenharkContinuityHealth": str(args.observer_health),
        "exactLedgerRoot": str(args.exact_root),
        "observationalUnit": ["NEW_FROM_FLAT", "FLAT_TO_REENTRY"],
        "excludedUnits": ["ADD_WHILE_ALREADY_EXPOSED", "FAILED", "TRANSFER", "NON_TRADE"],
        "unsupportedQuoteSemantics": "non-SOL quote-mint Pump V2 excluded fail-closed",
        "outcomesPermittedBeforeDevelopmentFreeze": False,
        "submissionsPermitted": False,
        "validationSealed": True,
        "preStartObservationsCounted": False,
    }
    atomic_json(contract_path, contract)
    atomic_json(args.output / "freeze.json", {
        "schema": SCHEMA + "_FREEZE",
        "createdAtUtc": started,
        "contractSha256": sha256_file(contract_path),
    })
    atomic_json(args.output / "choice-set-preservation.json", {
        "schema": SCHEMA + "_CHOICE_SET_PRESERVATION",
        "createdAtUtc": started,
        "mode": "CAUSAL_SOURCE_REFERENCES_ONLY_NO_ANALYSIS",
        "exactLedgerRoot": str(args.exact_root),
        "allenharkArrivalLedger": str(args.compact_arrivals),
        "reconstructionBoundary": "entry AllenHark receipt plus canonical slot/transaction/instruction order",
        "limitation": "No materialized contemporaneous feasible-universe snapshot; reconstruct later from retained full-universe exact ledger and arrival journal.",
        "outcomesRead": 0,
    })
    atomic_json(args.output / "state.json", {
        "schema": SCHEMA + "_STATE",
        "eligibleSignatures": [],
        "lastStatuses": {},
        "lastRunUtc": None,
    })
    (args.output / "development").mkdir(exist_ok=True)
    (args.output / "validation-sealed").mkdir(exist_ok=True)
    (args.output / "validation-sealed").chmod(0o700)
    return contract


def status_row(
    episode: dict[str, Any],
    candidate: dict[str, Any] | None,
    reasons: list[str],
    detail: dict[str, Any],
) -> dict[str, Any]:
    return {
        "signature": episode["signature"],
        "mint": episode["mint"],
        "route": episode["route"],
        "acquisitionClass": episode["classification"],
        "candidatePresent": candidate is not None,
        "eligible": not reasons,
        "reasons": sorted(set(reasons)),
        "detail": detail,
        "classifiedAtUtc": now_utc(),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    contract = initialize(args)
    state_path = args.output / "state.json"
    preflight_state = json.loads(state_path.read_text(encoding="utf-8"))
    preflight_eligible = set(preflight_state.get("eligibleSignatures") or [])
    preflight_eligible.update(eligible_rows_by_signature(args.output))
    status_path = args.output / "status.json"
    if len(preflight_eligible) >= TARGET_N and status_path.exists():
        return json.loads(status_path.read_text(encoding="utf-8"))
    start_ms = int(contract["officialCohortStartUnixMs"])
    end_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000) - 2_000
    env = capture.denominator.load_env(args.env)
    signature_rows, transactions = capture.fetch_prospective_population(
        env["SECURE_RPC_URL"], start_ms, end_ms, args.output / "wallet-rpc-cache"
    )
    classified, _ = capture.classify_successful_transactions(signature_rows, transactions, start_ms, end_ms)
    episodes = [row for row in classified if row["isIndependentEntryEpisode"]]
    intervals = invalid_intervals(args.output)
    all_candidates = [
        row for row in read_jsonl(args.candidates)
        if int(row.get("receivedAtUnixNs") or 0) // 1_000_000 >= start_ms
    ]
    invalid_candidates = {
        str(row.get("signature") or ""): row for row in all_candidates
        if in_invalid_interval(int(row.get("receivedAtUnixNs") or 0) // 1_000_000, intervals)
    }
    candidates = [
        row for row in all_candidates
        if str(row.get("signature") or "") not in invalid_candidates
    ]
    candidate_by_signature = {str(row.get("signature") or ""): row for row in candidates}

    state = json.loads(state_path.read_text(encoding="utf-8"))
    persisted_eligible = sorted(
        eligible_rows_by_signature(args.output).values(),
        key=lambda row: int(row.get("eligibleOrdinal") or 0),
    )
    eligible_signatures = list(dict.fromkeys([
        *(state.get("eligibleSignatures") or []),
        *(str(row["signature"]) for row in persisted_eligible),
    ]))
    eligible_set = set(eligible_signatures)
    statuses: dict[str, dict[str, Any]] = dict(state.get("lastStatuses") or {})
    terminal_statuses = {
        signature: row for signature, row in statuses.items()
        if signature not in eligible_set
    }
    pending_episodes = pending_episode_rows(episodes, eligible_set, terminal_statuses)
    progress_path = args.output / "reconciliation-progress.json"
    atomic_json(progress_path, {
        "schema": SCHEMA + "_RECONCILIATION_PROGRESS",
        "atUtc": now_utc(),
        "state": "RUNNING",
        "canonicalIndependentEntryEpisodes": len(episodes),
        "previouslyDecidedEpisodes": len(eligible_set | set(terminal_statuses)),
        "newEpisodesPendingAtStart": len(pending_episodes),
        "newEpisodesEvaluated": 0,
        "newEligible": 0,
        "batchSize": RECONCILIATION_BATCH_EPISODES,
    })
    observer_health = json.loads(args.observer_health.read_text(encoding="utf-8"))
    compact_health = json.loads(args.compact_observer_health.read_text(encoding="utf-8"))
    continuity_segments = compact_continuity_segments(
        args.compact_observer_continuity, compact_health
    )
    observer_started_ns = min(
        (int(segment["startUnixNs"]) for segment in continuity_segments),
        default=0,
    )

    newly_eligible: list[dict[str, Any]] = []
    evaluated_this_run = 0
    for offset in range(0, len(pending_episodes), RECONCILIATION_BATCH_EPISODES):
        if len(eligible_signatures) >= TARGET_N:
            break
        batch = pending_episodes[offset:offset + RECONCILIATION_BATCH_EPISODES]
        required_digests: set[bytes] = set()
        history_rows: dict[str, list[dict[str, Any]]] = {}
        for episode in batch:
            signature = str(episode["signature"])
            candidate = candidate_by_signature.get(signature)
            if (
                signature in invalid_candidates
                or episode_in_invalid_interval(episode, intervals)
                or candidate is None
                or episode["route"] not in SUPPORTED_TIER_A_ROUTES
            ):
                continue
            required_digests.add(tier_a.signature_digest(signature))
            older, boundary = tier_a.older_signatures(
                env["SECURE_RPC_URL"], str(episode["mint"]), signature,
                args.output / "tier-a-rpc-cache",
            )
            history_rows[signature] = older
            if boundary:
                required_digests.update(
                    tier_a.signature_digest(value)
                    for item in older
                    if (value := str(item.get("signature") or ""))
                )
        arrivals = (
            targeted_compact_arrivals(args.compact_arrivals, required_digests)
            if required_digests else {}
        )

        for episode in batch:
            if len(eligible_signatures) >= TARGET_N:
                break
            signature = str(episode["signature"])
            candidate = candidate_by_signature.get(signature)
            reasons: list[str] = []
            detail: dict[str, Any] = {}
            eligible_row: dict[str, Any] | None = None
            atomic_json(progress_path, {
                "schema": SCHEMA + "_RECONCILIATION_PROGRESS",
                "atUtc": now_utc(),
                "state": "RUNNING",
                "canonicalIndependentEntryEpisodes": len(episodes),
                "previouslyDecidedEpisodes": len(eligible_set | set(terminal_statuses)),
                "newEpisodesPendingAtStart": len(pending_episodes),
                "newEpisodesEvaluated": evaluated_this_run,
                "newEligible": len(newly_eligible),
                "currentEpisodeSignature": signature,
                "currentEpisodeMint": episode["mint"],
                "currentEpisodeRoute": episode["route"],
                "batchSize": RECONCILIATION_BATCH_EPISODES,
            })
            if signature in invalid_candidates or episode_in_invalid_interval(episode, intervals):
                reasons = ["PAUSED_INVALID_COLLECTION_INTERVAL"]
                detail = {
                    "receivedAtUnixNs": (
                        int(invalid_candidates[signature]["receivedAtUnixNs"])
                        if signature in invalid_candidates else None
                    ),
                    "canonicalBlockTimeUtc": episode.get("blockTimeUtc"),
                    "admissionPermanentlyExcluded": True,
                }
            elif candidate is None:
                reasons = ["CANONICAL_ACQUISITION_WITHOUT_REALTIME_CANDIDATE"]
            elif episode["route"] not in SUPPORTED_TIER_A_ROUTES:
                reasons = ["NON_PUMP_BONDING_ROUTE_NOT_SUPPORTED_BY_FROZEN_TIER_A_REPLAY"]
            else:
                prewarm_finalized_history(
                    env["SECURE_RPC_URL"],
                    history_rows.get(signature, []),
                    args.output / "tier-a-rpc-cache",
                )
                adapted = {
                    "signature": signature,
                    "outputMint": episode["mint"],
                    "detectedAtMs": int(candidate["receivedAtUnixNs"]) // 1_000_000,
                    "sourceSlot": candidate["slot"],
                }
                try:
                    eligible, reasons, detail = tier_a.evaluate_history(
                        env["SECURE_RPC_URL"], adapted, transactions[signature], arrivals,
                        args.output / "tier-a-rpc-cache", observer_started_ns,
                        0,
                    )
                    entry_arrival = arrivals.get(tier_a.signature_digest(signature))
                    create_signature = str(detail.get("createSignature") or "")
                    create_arrival = (
                        arrivals.get(tier_a.signature_digest(create_signature))
                        if create_signature else None
                    )
                    segment = covering_continuity_segment(
                        int(create_arrival[0]) if create_arrival else 0,
                        int(entry_arrival[0]) if entry_arrival else 0,
                        continuity_segments,
                    )
                    if segment is None:
                        reasons.append("ALLENHARK_CONTINUITY_NOT_PROVEN")
                        eligible = False
                    detail["continuityPolicy"] = "SAME_COMPACT_SOURCE_CAUSAL_INTERVAL_V1"
                    detail["continuitySegment"] = segment
                except Exception as error:
                    eligible = False
                    reasons = ["TIER_A_EVALUATOR_EXCEPTION"]
                    detail = {"errorType": type(error).__name__, "error": str(error)}
                if eligible:
                    ordinal = len(eligible_signatures) + 1
                    split = "DEVELOPMENT" if ordinal <= DEVELOPMENT_N else "UNTOUCHED_VALIDATION"
                    eligible_row = {
                        "schema": SCHEMA + "_ELIGIBLE_ENTRY",
                        "eligibleOrdinal": ordinal,
                        "split": split,
                        "signature": signature,
                        "mint": episode["mint"],
                        "route": episode["route"],
                        "acquisitionClass": episode["classification"],
                        "entryReceivedAtUnixNs": int(candidate["receivedAtUnixNs"]),
                        "entrySlot": int(candidate["slot"]),
                        "entryTransactionIndex": episode.get("transactionIndex"),
                        "tierAEvidence": detail,
                        "choiceSetBoundary": {
                            "arrivalUnixNs": int(candidate["receivedAtUnixNs"]),
                            "slot": int(candidate["slot"]),
                            "transactionIndex": episode.get("transactionIndex"),
                            "exactLedgerRoot": str(args.exact_root),
                            "allenharkArrivalLedger": str(args.compact_arrivals),
                        },
                        "outcomesRead": 0,
                    }
            episode_status = status_row(episode, candidate, reasons, detail)
            checkpoint_batch(
                args.output,
                state,
                [eligible_row] if eligible_row else [],
                {signature: episode_status},
            )
            evaluated_this_run += 1
            if eligible_row:
                newly_eligible.append(eligible_row)
            statuses[signature] = episode_status
            eligible_signatures = list(state.get("eligibleSignatures") or [])
            eligible_set = set(eligible_signatures)
            atomic_json(progress_path, {
                "schema": SCHEMA + "_RECONCILIATION_PROGRESS",
                "atUtc": now_utc(),
                "state": "RUNNING",
                "canonicalIndependentEntryEpisodes": len(episodes),
                "previouslyDecidedEpisodes": len(eligible_set | set(terminal_statuses)),
                "newEpisodesPendingAtStart": len(pending_episodes),
                "newEpisodesEvaluated": evaluated_this_run,
                "newEligible": len(newly_eligible),
                "lastCompletedEpisodeSignature": signature,
                "batchSize": RECONCILIATION_BATCH_EPISODES,
            })
        del arrivals
        gc.collect()

    state.update({
        "eligibleSignatures": eligible_signatures,
        "lastStatuses": statuses,
        "lastRunUtc": now_utc(),
    })
    atomic_json(state_path, state)
    reason_counts: collections.Counter[str] = collections.Counter()
    for row in statuses.values():
        if not row.get("eligible"):
            reason_counts.update(row.get("reasons") or [])

    elapsed_hours = max((end_ms - start_ms) / 3_600_000, 1 / 3600)
    status = {
        "schema": SCHEMA + "_STATUS",
        "atUtc": now_utc(),
        "officialCohortStartUtc": contract["officialCohortStartUtc"],
        "targetN": TARGET_N,
        "developmentTargetN": DEVELOPMENT_N,
        "sealedValidationTargetN": VALIDATION_N,
        "currentEligibleN": len(eligible_signatures),
        "developmentEligibleN": min(len(eligible_signatures), DEVELOPMENT_N),
        "sealedValidationEligibleN": max(0, len(eligible_signatures) - DEVELOPMENT_N),
        "canonicalIndependentEntryEpisodes": len(episodes),
        "realtimeCandidatesSinceStart": len(candidates),
        "invalidIntervalCandidateN": len(invalid_candidates),
        "invalidCollectionIntervals": len(intervals),
        "canonicalWithoutRealtimeCandidate": sum(
            1 for row in episodes
            if not episode_in_invalid_interval(row, intervals)
            and str(row["signature"]) not in candidate_by_signature
        ),
        "pendingOrExcludedCurrentN": sum(
            1 for row in statuses.values() if not row.get("eligible")
        ),
        "currentReasons": dict(sorted(reason_counts.items())),
        "wallClockEligiblePerHour": len(eligible_signatures) / elapsed_hours,
        "captureObserverConnected": observer_health.get("connected"),
        "captureObserverReconnects": observer_health.get("reconnects"),
        "captureObserverDecodeFailures": observer_health.get("decodeFailures"),
        "compactObserverConnected": compact_health.get("connected"),
        "compactObserverReconnects": compact_health.get("reconnects"),
        "compactObserverDecodeFailures": compact_health.get("decodeFailures"),
        "compactContinuitySegments": len(continuity_segments),
        "continuityPolicy": "SAME_COMPACT_SOURCE_CAUSAL_INTERVAL_V1",
        "reconciliationBatchEpisodes": RECONCILIATION_BATCH_EPISODES,
        "reconciliationMode": "DURABLE_INCREMENTAL_NEW_EPISODES_ONLY",
        "newEpisodesEvaluatedThisRun": evaluated_this_run,
        "newEligibleThisRun": len(newly_eligible),
        "transactionsMemoryPolicy": "DISK_BACKED_ONE_ENTRY_CACHE",
        "blindingActive": len(eligible_signatures) < DEVELOPMENT_N,
        "validationSealed": True,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(args.output / "status.json", status)
    atomic_json(progress_path, {
        "schema": SCHEMA + "_RECONCILIATION_PROGRESS",
        "atUtc": now_utc(),
        "state": "COMPLETE",
        "canonicalIndependentEntryEpisodes": len(episodes),
        "previouslyDecidedEpisodes": len(eligible_set | set(terminal_statuses)),
        "newEpisodesPendingAtStart": len(pending_episodes),
        "newEpisodesEvaluated": evaluated_this_run,
        "newEligible": len(newly_eligible),
        "batchSize": RECONCILIATION_BATCH_EPISODES,
    })
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--observer-health", type=pathlib.Path, required=True)
    parser.add_argument("--compact-observer-health", type=pathlib.Path, required=True)
    parser.add_argument("--compact-observer-continuity", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--capture-binary-sha256", required=True)
    parser.add_argument("--tier-a-evaluator", type=pathlib.Path, required=True)
    parser.add_argument("--research-spec", type=pathlib.Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
