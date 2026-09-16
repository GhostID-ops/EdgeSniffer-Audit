#!/usr/bin/env python3
"""Track immutable, chronological exact-evidence research checkpoints."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
import statistics
import struct
import tempfile
from typing import Any, Iterable

from reconcilePumpExactEvidenceLedgerV1 import iter_frames, partition_data_path
from validatePumpExactEvidenceLedgerV1 import MAGIC, validate


SCHEMA = "PUMP_EXACT_EVIDENCE_READINESS_V1"
IMPLEMENTATION_VERSION = 4
CREATE_KINDS = {"CREATE", "CREATE_V2"}
WINDOW_NS = 125 * 1_000_000_000
CHECKPOINTS = (3_000, 5_000, 10_000, 15_000, 25_000, 50_000)


def atomic_json(path: pathlib.Path, value: Any, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        return
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)


def persist_current_partition_report(path: pathlib.Path, report: dict[str, Any]) -> None:
    """Replace a stale current report while retaining its immutable provenance copy."""
    if path.exists():
        prior = json.loads(path.read_text(encoding="utf-8"))
        if prior == report:
            return
        prior_source = str(prior.get("sourceReconciliationManifestSha256") or "unfingerprinted")
        archive = path.parent / "superseded" / f"{path.stem}-{prior_source}.json"
        atomic_json(archive, prior, immutable=True)
    atomic_json(path, report)


def parse_hour(directory: pathlib.Path, root: pathlib.Path) -> dt.datetime:
    relative = directory.relative_to(root / "partitions")
    year, month, day, hour = map(int, relative.parts[:4])
    return dt.datetime(year, month, day, hour, tzinfo=dt.timezone.utc)


def hour_directory(root: pathlib.Path, hour: dt.datetime) -> pathlib.Path:
    return root / "partitions" / hour.strftime("%Y/%m/%d/%H")


def evidence_paths(root: pathlib.Path, hour: dt.datetime) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for offset in range(3):
        path = partition_data_path(hour_directory(root, hour + dt.timedelta(hours=offset)))
        if path is not None:
            paths.append(path)
    return paths


def related_mints(metadata: dict[str, Any], target: set[str]) -> set[str]:
    return set(metadata.get("discoveredMints") or []).intersection(target)


def frame_bytes(metadata: dict[str, Any], raw: bytes) -> bytes:
    encoded = json.dumps(metadata, separators=(",", ":")).encode()
    body = encoded + raw
    return struct.pack("<II", len(encoded), len(raw)) + body + hashlib.sha256(body).digest()


def target_creates(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    creates: dict[str, dict[str, Any]] = {}
    for metadata, _ in iter_frames(path, False):
        if metadata.get("kind") != "transaction":
            continue
        if not CREATE_KINDS.intersection(metadata.get("pumpInstructionKinds") or []):
            continue
        for mint in metadata.get("discoveredMints") or []:
            received_ns = int(metadata.get("receivedAtUnixNs") or 0)
            prior = creates.get(mint)
            if prior is None or received_ns < int(prior.get("receivedAtUnixNs") or 0):
                creates[mint] = metadata
    return creates


def is_in_window(metadata: dict[str, Any], creates: dict[str, dict[str, Any]]) -> bool:
    received_ns = int(metadata.get("receivedAtUnixNs") or 0)
    for mint in related_mints(metadata, set(creates)):
        start_ns = int(creates[mint].get("receivedAtUnixNs") or 0)
        start_slot = int(creates[mint].get("slot") or 0)
        slot = int(metadata.get("slot") or 0)
        if metadata.get("kind") == "rpc_finalized_transaction":
            if start_slot <= slot <= start_slot + 400:
                return True
        elif start_ns <= received_ns <= start_ns + WINDOW_NS:
            return True
    return False


def select_evidence(
    paths: list[pathlib.Path], creates: dict[str, dict[str, Any]]
) -> tuple[set[str], set[int], int, int, dict[str, int]]:
    signatures: set[str] = set()
    slots: set[int] = set()
    latest_ns = 0
    latest_slot = 0
    create_block_times: dict[str, int] = {}
    create_signatures = {
        metadata.get("signature"): mint for mint, metadata in creates.items()
    }
    for path in paths:
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            latest_ns = max(latest_ns, int(metadata.get("receivedAtUnixNs") or 0))
            latest_slot = max(latest_slot, int(metadata.get("slot") or 0))
            if (
                metadata.get("kind") == "rpc_finalized_transaction"
                and metadata.get("signature") in create_signatures
            ):
                try:
                    block_time = json.loads(raw).get("result", {}).get("blockTime")
                except Exception:
                    block_time = None
                if isinstance(block_time, int):
                    create_block_times[create_signatures[metadata["signature"]]] = block_time
            if metadata.get("kind") not in {"transaction", "rpc_finalized_transaction"} or not is_in_window(metadata, creates):
                continue
            signature = metadata.get("signature")
            slot = metadata.get("slot")
            if signature:
                signatures.add(signature)
            if isinstance(slot, int):
                slots.add(slot)
    return signatures, slots, latest_ns, latest_slot, create_block_times


def compose_validation_stream(
    paths: list[pathlib.Path],
    creates: dict[str, dict[str, Any]],
    signatures: set[str],
    slots: set[int],
    destination,
) -> int:
    destination.write(MAGIC)
    target_mints = set(creates)
    written_ids: set[str] = set()
    count = 0
    for path in paths:
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            kind = metadata.get("kind")
            include = (
                (kind == "transaction" and metadata.get("signature") in signatures)
                or (kind == "rpc_finalized_transaction" and metadata.get("signature") in signatures)
                or (kind == "rpc_finalized_block" and metadata.get("slot") in slots)
                or (kind == "rpc_account_snapshot" and metadata.get("accountPubkey") in target_mints)
            )
            evidence_id = metadata.get("evidenceId")
            if not include or evidence_id in written_ids:
                continue
            destination.write(frame_bytes(metadata, raw))
            if evidence_id:
                written_ids.add(evidence_id)
            count += 1
    destination.flush()
    destination.seek(0)
    return count


def process_partition(root: pathlib.Path, manifest_path: pathlib.Path) -> dict[str, Any]:
    directory = manifest_path.parent
    hour = parse_hour(directory, root)
    target_path = partition_data_path(directory)
    if target_path is None or target_path.suffix != ".zst":
        raise RuntimeError(f"exact partition has no immutable compressed evidence: {directory}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    creates = target_creates(target_path)
    paths = evidence_paths(root, hour)
    signatures, slots, latest_ns, latest_slot, create_block_times = select_evidence(paths, creates)
    with tempfile.TemporaryFile() as stream:
        selected_frames = compose_validation_stream(
            paths, creates, signatures, slots, stream
        )
        validation = validate(stream)

    finalized = set(validation["freshMintsFinalizedSuccessfulValues"])
    snapshotted = set(validation["freshMintsWithFinalizedSnapshotValues"])
    admitted = set(validation["freshMintsWithAdmittedTransitionsValues"])
    taint_reasons = validation["taintedMintReasons"]
    rows: list[dict[str, Any]] = []
    exclusions: collections.Counter[str] = collections.Counter()
    for mint, create in sorted(
        creates.items(), key=lambda pair: int(pair[1].get("receivedAtUnixNs") or 0)
    ):
        reasons: list[str] = []
        if mint not in finalized:
            reasons.append("create_not_finalized_successful")
        if mint not in snapshotted:
            reasons.append("missing_finalized_mint_snapshot")
        reasons.extend(taint_reasons.get(mint, []))
        if mint not in admitted:
            reasons.append("no_supported_exact_transition")
        create_slot = int(create.get("slot") or 0)
        if latest_slot < create_slot + 320:
            reasons.append("lifecycle_window_not_yet_complete")
        reasons = sorted(set(reasons))
        for reason in reasons:
            exclusions[reason] += 1
        block_time = create_block_times.get(mint)
        chain_timestamp = (
            dt.datetime.fromtimestamp(block_time, tz=dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
            if block_time is not None
            else None
        )
        rows.append(
            {
                "mint": mint,
                "chainTimestampUtc": chain_timestamp,
                "createReceivedAtUtc": create.get("receivedAtUtc"),
                "createReceivedAtUnixNs": create.get("receivedAtUnixNs"),
                "createSlot": create.get("slot"),
                "createSignature": create.get("signature"),
                "exactAdmitted": not reasons,
                "exclusionReasons": reasons,
            }
        )

    exact_rows = [row for row in rows if row["exactAdmitted"]]
    return {
        "schema": SCHEMA,
        "implementationVersion": IMPLEMENTATION_VERSION,
        "partitionKey": manifest.get("partitionKey"),
        "partitionHourUtc": hour.isoformat().replace("+00:00", "Z"),
        "sourceReconciliationManifestSha256": manifest.get("manifestSha256"),
        "sourceExactReplayComplete": manifest.get("exactReplayComplete") is True,
        "selectedEvidenceFrames": selected_frames,
        "freshMintsObserved": len(rows),
        "exactAdmittedN": len(exact_rows),
        "excludedN": len(rows) - len(exact_rows),
        "exclusionReasons": dict(sorted(exclusions.items())),
        "featureCompleteness": {
            "finalizedCreateN": len(finalized.intersection(creates)),
            "finalizedMintSnapshotN": len(snapshotted.intersection(creates)),
            "supportedTransitionN": len(admitted.intersection(creates)),
        },
        "validationClassification": validation["classification"],
        "validationCounters": validation["counters"],
        "mints": rows,
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return ordered[index]


def aggregate(root: pathlib.Path, report_paths: Iterable[pathlib.Path]) -> dict[str, Any]:
    database = sqlite3.connect("")
    database.execute("PRAGMA temp_store=FILE")
    database.execute("PRAGMA journal_mode=OFF")
    database.execute(
        "CREATE TABLE mints(mint TEXT PRIMARY KEY, sort_ts TEXT NOT NULL, create_ns INTEGER NOT NULL, "
        "chain_ts TEXT, received_ts TEXT, admitted INTEGER NOT NULL, reasons TEXT NOT NULL)"
    )
    activity = []
    report_count = 0
    for report_path in sorted(report_paths):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report_count += 1
        for row in sorted(
            report["mints"],
            key=lambda item: (
                item.get("chainTimestampUtc") or item.get("createReceivedAtUtc") or "",
                int(item.get("createReceivedAtUnixNs") or 0),
                item["mint"],
            ),
        ):
            reasons = row["exclusionReasons"]
            database.execute(
                "INSERT INTO mints VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(mint) DO UPDATE SET "
                "sort_ts=excluded.sort_ts,create_ns=excluded.create_ns," 
                "chain_ts=excluded.chain_ts,received_ts=excluded.received_ts," 
                "admitted=excluded.admitted,reasons=excluded.reasons "
                "WHERE mints.admitted=0 AND excluded.admitted=1",
                (
                    row["mint"],
                    row.get("chainTimestampUtc") or row.get("createReceivedAtUtc") or "",
                    int(row.get("createReceivedAtUnixNs") or 0),
                    row.get("chainTimestampUtc"),
                    row.get("createReceivedAtUtc"),
                    1 if row["exactAdmitted"] else 0,
                    json.dumps(reasons, separators=(",", ":")),
                ),
            )
        manifest_path = (
            hour_directory(root, dt.datetime.fromisoformat(report["partitionHourUtc"].replace("Z", "+00:00")))
            / "reconciliation-manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        coverage = manifest.get("coverage") or {}
        activity.append(
            {
                "hourUtc": report["partitionHourUtc"],
                "observedTransactions": coverage.get("relevantTransactionsObserved", 0),
                "successfulTransactions": coverage.get("successful", 0),
                "failedTransactions": coverage.get("failed", 0),
                "freshMints": report["freshMintsObserved"],
                "exactAdmittedMints": report["exactAdmittedN"],
            }
        )
    database.commit()

    exclusions: collections.Counter[str] = collections.Counter()
    for (encoded_reasons,) in database.execute(
        "SELECT reasons FROM mints WHERE admitted=0"
    ):
        exclusions.update(json.loads(encoded_reasons))

    row_count = int(database.execute("SELECT COUNT(*) FROM mints").fetchone()[0])
    admitted_count = int(
        database.execute("SELECT COUNT(*) FROM mints WHERE admitted=1").fetchone()[0]
    )
    first_timestamp, last_timestamp = database.execute(
        "SELECT MIN(NULLIF(sort_ts,'')), MAX(NULLIF(sort_ts,'')) FROM mints"
    ).fetchone()
    first_admitted, last_admitted = database.execute(
        "SELECT MIN(NULLIF(sort_ts,'')), MAX(NULLIF(sort_ts,'')) FROM mints WHERE admitted=1"
    ).fetchone()
    hours = [
        row[0] + ":00:00Z"
        for row in database.execute(
            "SELECT DISTINCT substr(sort_ts,1,13) FROM mints WHERE admitted=1 AND sort_ts!='' ORDER BY 1"
        )
    ]
    elapsed_hours = 0.0
    if admitted_count >= 2 and first_admitted and last_admitted:
        start = dt.datetime.fromisoformat(first_admitted.replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(last_admitted.replace("Z", "+00:00"))
        elapsed_hours = max((end - start).total_seconds() / 3600, 0.0)
    rate = admitted_count / elapsed_hours if elapsed_hours > 0 else None
    target_n = 3_000
    eta_hours = max(target_n - admitted_count, 0) / rate if rate else None
    exact_rate = [float(row["exactAdmittedMints"]) for row in activity]

    checkpoints = [
        {
            "N": checkpoint,
            "reached": admitted_count >= checkpoint,
            "remaining": max(checkpoint - admitted_count, 0),
        }
        for checkpoint in CHECKPOINTS
    ]
    now = dt.datetime.now(dt.timezone.utc)
    summary = {
        "schema": SCHEMA,
        "implementationVersion": IMPLEMENTATION_VERSION,
        "generatedAtUtc": now.isoformat().replace("+00:00", "Z"),
        "countingRule": "unique fresh mints in immutable receive-hour partitions with exact reconciliation, finalized successful create, finalized mint snapshot, supported non-tainted exact transition, and complete 120s evidence window",
        "exactAdmittedN": admitted_count,
        "excludedN": row_count - admitted_count,
        "freshMintsObservedN": row_count,
        "exclusionReasons": dict(sorted(exclusions.items())),
        "utcCoverage": {
            "firstCreateUtc": first_timestamp,
            "lastCreateUtc": last_timestamp,
            "exactPartitionHours": report_count,
            "admittedMintHoursRepresented": len(hours),
            "hours": hours,
        },
        "regimeActivityDiversity": {
            "activityByReceiveHour": activity,
            "exactAdmittedPerHourMin": min(exact_rate) if exact_rate else None,
            "exactAdmittedPerHourMedian": statistics.median(exact_rate) if exact_rate else None,
            "exactAdmittedPerHourMax": max(exact_rate) if exact_rate else None,
            "assessment": "INSUFFICIENT_DIVERSITY" if len(hours) < 24 else "MINIMUM_24H_DIVERSITY_MET",
        },
        "featureCompleteness": {
            "exactAdmittedShare": admitted_count / row_count if row_count else None,
            "allAdmittedHaveFinalizedCreateSnapshotAndTransition": database.execute(
                "SELECT COUNT(*) FROM mints WHERE admitted=1 AND reasons!='[]'"
            ).fetchone()[0] == 0,
        },
        "replayCompleteness": {
            "exactReconciledPartitionN": report_count,
            "allCountedRowsHaveComplete120sWindow": database.execute(
                "SELECT COUNT(*) FROM mints WHERE admitted=1 AND reasons LIKE '%lifecycle_window_not_yet_complete%'"
            ).fetchone()[0] == 0,
        },
        "checkpoints": checkpoints,
        "researchPlan": {
            "earliestExploratoryN": 3_000,
            "minimumDiversityHours": 24,
            "discovery": "first 10,000 chronological exact-admitted mints",
            "validation": "next untouched 10,000 chronological exact-admitted mints",
            "holdout": "next untouched 10,000 chronological exact-admitted mints",
            "startDiscoveryNow": admitted_count >= 3_000,
            "etaToExploratoryHoursAtObservedRate": eta_hours,
            "observedExactAdmissionRatePerHour": rate,
            "rateCaveat": "provisional until at least 24 exact-admitted UTC hours are represented",
        },
        "chronologicalSplitBoundaries": {
            "discoveryEndMint": nth_admitted_mint(database, 10_000),
            "validationEndMint": nth_admitted_mint(database, 20_000),
            "holdoutEndMint": nth_admitted_mint(database, 30_000),
        },
    }
    return summary


def nth_admitted_mint(database: sqlite3.Connection, ordinal: int) -> str | None:
    row = database.execute(
        "SELECT mint FROM mints WHERE admitted=1 ORDER BY sort_ts, create_ns, mint LIMIT 1 OFFSET ?",
        (ordinal - 1,),
    ).fetchone()
    return row[0] if row else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=pathlib.Path)
    args = parser.parse_args()
    root = args.root
    output_root = root / "readiness-v1"
    partition_output = output_root / "partitions"
    report_paths: list[pathlib.Path] = []
    for manifest_path in sorted((root / "partitions").glob("*/*/*/*/reconciliation-manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("exactReplayComplete") is not True:
            continue
        partition_key = str(manifest.get("partitionKey") or manifest_path.parent.name).replace("/", "-")
        report_path = partition_output / f"{partition_key}-v{IMPLEMENTATION_VERSION}.json"
        report = None
        if report_path.exists():
            cached = json.loads(report_path.read_text(encoding="utf-8"))
            if (
                cached.get("implementationVersion") == IMPLEMENTATION_VERSION
                and cached.get("sourceReconciliationManifestSha256") == manifest.get("manifestSha256")
            ):
                report = cached
        if report is None:
            report = process_partition(root, manifest_path)
            persist_current_partition_report(report_path, report)
        report_paths.append(report_path)

    summary = aggregate(root, report_paths)
    atomic_json(output_root / "current.json", summary)
    for checkpoint in summary["checkpoints"]:
        if checkpoint["reached"]:
            atomic_json(
                output_root / "checkpoints" / f"N{checkpoint['N']:05d}.json",
                summary,
                immutable=True,
            )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
