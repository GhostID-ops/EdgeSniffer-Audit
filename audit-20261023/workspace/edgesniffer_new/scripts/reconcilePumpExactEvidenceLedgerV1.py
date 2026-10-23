#!/usr/bin/env python3
"""Prove receive-hour completeness across delayed finality partitions."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from collections.abc import Iterator
from typing import Any, BinaryIO

try:
    import resource
except ImportError:  # pragma: no cover - Windows development hosts
    resource = None


MAGIC = b"PEL1FRM\0"
RECONCILIATION_VERSION = "PUMP_EXACT_EVIDENCE_LEDGER_V1_RECONCILIATION_V11_CANONICAL_FORK_SLOTS"
COMPATIBLE_EXACT_VERSIONS = {
    "PUMP_EXACT_EVIDENCE_LEDGER_V1_RECONCILIATION_V9_INCREMENTAL",
    "PUMP_EXACT_EVIDENCE_LEDGER_V1_RECONCILIATION_V10_CANONICAL_FORKS",
    RECONCILIATION_VERSION,
}
SUPPORT_HORIZON_HOURS = 3
JSON_SIGNATURE_PATTERN = re.compile(rb'"signature"\s*:\s*"([^"\\]+)"')
JSON_SLOT_PATTERN = re.compile(rb'"slot"\s*:\s*(\d+)')


class ReconciliationTelemetry:
    """Append-only timing and resource evidence; never affects reconciliation."""

    def __init__(
        self,
        output: pathlib.Path | None,
        dispatch_stamp: pathlib.Path | None = None,
    ) -> None:
        self.output = output
        self.started_utc = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        self.started_ns = time.monotonic_ns()
        self.cpu_started = time.process_time_ns()
        self.invocation_stages: dict[str, float] = {}
        self.partitions: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.dispatch_to_process_ms: float | None = None
        if dispatch_stamp and dispatch_stamp.exists():
            try:
                stamp = json.loads(dispatch_stamp.read_text(encoding="utf-8"))
                self.dispatch_to_process_ms = max(
                    0.0, (self.started_ns - int(stamp["monotonicNs"])) / 1_000_000
                )
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                self.dispatch_to_process_ms = None

    @contextlib.contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.monotonic_ns()
        try:
            yield
        finally:
            elapsed = (time.monotonic_ns() - started) / 1_000_000
            target = self.current["stageMs"] if self.current is not None else self.invocation_stages
            target[name] = target.get(name, 0.0) + elapsed

    def begin_partition(self, key: str, paths: list[pathlib.Path]) -> None:
        self.current = {
            "partitionKey": key,
            "inputPathN": len(paths),
            "inputBytes": sum(path.stat().st_size for path in paths if path.exists()),
            "stageMs": {},
            "counters": {},
        }

    def count(self, name: str, amount: int = 1) -> None:
        if self.current is None:
            return
        counters = self.current["counters"]
        counters[name] = counters.get(name, 0) + amount

    def end_partition(self, report: dict[str, Any]) -> None:
        if self.current is None:
            return
        self.current["exactReplayComplete"] = report.get("exactReplayComplete")
        self.current["coverage"] = report.get("coverage")
        self.partitions.append(self.current)
        self.current = None

    def finish(self, status: str, error: str | None = None) -> dict[str, Any]:
        usage = resource.getrusage(resource.RUSAGE_SELF) if resource is not None else None
        row = {
            "schema": "PUMP_EXACT_RECONCILER_STAGE_TELEMETRY_V1",
            "startedAtUtc": self.started_utc,
            "finishedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": status,
            "error": error,
            "dispatchToProcessStartMs": self.dispatch_to_process_ms,
            "runtimeMs": (time.monotonic_ns() - self.started_ns) / 1_000_000,
            "cpuMs": (time.process_time_ns() - self.cpu_started) / 1_000_000,
            "peakRssKiB": int(usage.ru_maxrss) if usage is not None else None,
            "invocationStageMs": self.invocation_stages,
            "partitions": self.partitions,
        }
        if self.output is not None:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            with self.output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return row


def load_rpc_urls(env_path: pathlib.Path) -> list[str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    urls = [values.get("SECURE_RPC_URL", "")]
    keys = values.get("HELIUS_API_KEYS", values.get("HELIUS_API_KEY", ""))
    urls.extend(
        f"https://mainnet.helius-rpc.com/?api-key={key.strip()}"
        for key in keys.split(",") if key.strip()
    )
    return list(dict.fromkeys(url for url in urls if url))


def fetch_finalized_statuses(rpc_urls: list[str], signatures: list[str]) -> dict[str, dict[str, Any]]:
    recovered: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(signatures), 256):
        batch = signatures[offset:offset + 256]
        payload = canonical_json({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignatureStatuses",
            "params": [batch, {"searchTransactionHistory": True}],
        })
        result = None
        for attempt in range(max(8, len(rpc_urls) * 2)):
            request = urllib.request.Request(
                rpc_urls[attempt % len(rpc_urls)], data=payload,
                headers={"Content-Type": "application/json"}, method="POST"
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    result = json.loads(response.read()).get("result", {}).get("value", [])
                break
            except urllib.error.HTTPError as error:
                if error.code != 429:
                    raise
                time.sleep(min(5.0, 0.5 * (attempt + 1)))
        if result is None:
            raise RuntimeError("all configured RPC endpoints rate-limited for signature status recovery")
        for signature, status in zip(batch, result):
            if not isinstance(status, dict) or status.get("confirmationStatus") != "finalized":
                recovered[signature] = {
                    "slot": status.get("slot") if isinstance(status, dict) else None,
                    "successful": None,
                    "err": status.get("err") if isinstance(status, dict) else None,
                    "confirmationStatus": (
                        status.get("confirmationStatus") if isinstance(status, dict) else None
                    ),
                    "notFinalizedAtRecovery": True,
                }
                continue
            recovered[signature] = {
                "slot": status.get("slot"),
                "successful": status.get("err") is None,
                "err": status.get("err"),
                "confirmationStatus": status.get("confirmationStatus"),
            }
    return recovered


def fetch_canonical_blocks(
    rpc_urls: list[str],
    slots: list[int],
    progress: Any = None,
    max_workers: int = 8,
) -> tuple[dict[str, list[str]], set[int]]:
    def fetch(index_and_slot: tuple[int, int]) -> tuple[str, list[str] | None, bool]:
        index, slot = index_and_slot
        payload = canonical_json({
            "jsonrpc": "2.0", "id": slot, "method": "getBlock",
            "params": [slot, {
                "commitment": "finalized",
                "transactionDetails": "signatures",
                "rewards": False,
                "maxSupportedTransactionVersion": 0,
            }],
        })
        endpoint_responses = 0
        for endpoint_offset in range(len(rpc_urls)):
            url = rpc_urls[(index + endpoint_offset) % len(rpc_urls)]
            document: dict[str, Any] | None = None
            for attempt in range(8):
                try:
                    request = urllib.request.Request(
                        url, data=payload,
                        headers={"Content-Type": "application/json"}, method="POST"
                    )
                    with urllib.request.urlopen(request, timeout=60) as response:
                        document = json.loads(response.read())
                    endpoint_responses += 1
                    break
                except urllib.error.HTTPError as error:
                    if error.code != 429 or attempt == 7:
                        break
                    retry_after = error.headers.get("Retry-After")
                    try:
                        delay = float(retry_after) if retry_after else 0.0
                    except ValueError:
                        retry_at = parsedate_to_datetime(retry_after)
                        delay = max(0.0, (retry_at - dt.datetime.now(dt.timezone.utc)).total_seconds())
                    if not retry_after:
                        delay = min(30.0, 1.0 * (2 ** attempt))
                    time.sleep(delay)
                except (urllib.error.URLError, TimeoutError):
                    break
            result = document.get("result") if isinstance(document, dict) else None
            signatures = result.get("signatures") if isinstance(result, dict) else None
            if isinstance(signatures, list):
                return str(slot), signatures, False
        return str(slot), None, endpoint_responses == len(rpc_urls)

    recovered: dict[str, list[str]] = {}
    unavailable: set[int] = set()
    workers = max(1, min(max_workers, len(slots))) if slots else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch, item) for item in enumerate(slots)]
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            slot, signatures, proven_unavailable = future.result()
            if signatures is not None:
                recovered[slot] = signatures
            elif proven_unavailable:
                unavailable.add(int(slot))
            if progress and (completed % 100 == 0 or completed == len(slots)):
                progress(recovered)
    return recovered, unavailable


def fetch_finalized_transactions(
    rpc_urls: list[str],
    signatures: list[str],
    max_workers: int = 8,
) -> dict[str, dict[str, Any]]:
    """Recover finalized success/failure when signature-status history expired."""
    def fetch(index_and_signature: tuple[int, str]) -> tuple[str, dict[str, Any] | None]:
        index, signature = index_and_signature
        payload = canonical_json({
            "jsonrpc": "2.0", "id": index + 1, "method": "getTransaction",
            "params": [signature, {
                "commitment": "finalized",
                "encoding": "json",
                "maxSupportedTransactionVersion": 0,
            }],
        })
        for attempt in range(max(8, len(rpc_urls) * 2)):
            request = urllib.request.Request(
                rpc_urls[(index + attempt) % len(rpc_urls)], data=payload,
                headers={"Content-Type": "application/json"}, method="POST"
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    document = json.loads(response.read())
                result = document.get("result") if isinstance(document, dict) else None
                if not isinstance(result, dict) or not isinstance(result.get("meta"), dict):
                    return signature, None
                err = result["meta"].get("err")
                return signature, {
                    "slot": result.get("slot"),
                    "successful": err is None,
                    "err": err,
                    "confirmationStatus": "finalized",
                    "source": "getTransaction(finalized)",
                }
            except urllib.error.HTTPError as error:
                if error.code != 429:
                    raise
                time.sleep(min(5.0, 0.5 * (attempt + 1)))
            except (urllib.error.URLError, TimeoutError):
                if attempt + 1 == max(8, len(rpc_urls) * 2):
                    return signature, None
        return signature, None

    recovered: dict[str, dict[str, Any]] = {}
    workers = max(1, min(max_workers, len(signatures))) if signatures else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch, item) for item in enumerate(signatures)]
        for future in concurrent.futures.as_completed(futures):
            signature, row = future.result()
            if row is not None:
                recovered[signature] = row
    return recovered


def atomic_json(path: pathlib.Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def finalized_manifest_has_immutable_inputs(report: dict[str, Any]) -> bool:
    inputs = report.get("inputEvidence")
    fingerprint = report.get("inputFingerprintSha256")
    return bool(
        isinstance(inputs, list)
        and inputs
        and fingerprint
        and report.get("inputStableDuringRun") is True
        and report.get("postRunInputFingerprintSha256") == fingerprint
    )


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextlib.contextmanager
def evidence_stream(path: pathlib.Path) -> Iterator[BinaryIO]:
    if path.suffix == ".zst":
        process = subprocess.Popen(
            # Completed partitions may be path-preservingly offloaded to a
            # different filesystem. zstd otherwise refuses a symlink input.
            ["zstd", "-q", "-dc", "-f", str(path)], stdout=subprocess.PIPE
        )
        assert process.stdout is not None
        try:
            yield process.stdout
        finally:
            process.stdout.close()
            if process.wait() != 0:
                raise RuntimeError(f"zstd failed for {path}")
    else:
        with path.open("rb") as stream:
            yield stream


def iter_frame_parts(
    path: pathlib.Path, tolerate_partial_tail: bool
) -> Iterator[tuple[bytes, bytes]]:
    with evidence_stream(path) as stream:
        if stream.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"invalid frame magic: {path}")
        while True:
            prefix = stream.read(8)
            if not prefix:
                return
            if prefix == MAGIC:
                continue
            if len(prefix) != 8:
                if tolerate_partial_tail:
                    return
                raise ValueError(f"truncated frame prefix: {path}")
            metadata_len, raw_len = struct.unpack("<II", prefix)
            if metadata_len + raw_len > 128 * 1024 * 1024:
                raise ValueError(f"unsafe frame length: {path}")
            body = stream.read(metadata_len + raw_len)
            checksum = stream.read(32)
            if len(body) != metadata_len + raw_len or len(checksum) != 32:
                if tolerate_partial_tail:
                    return
                raise ValueError(f"truncated frame body: {path}")
            if hashlib.sha256(body).digest() != checksum:
                raise ValueError(f"frame checksum mismatch: {path}")
            yield body[:metadata_len], body[metadata_len:]


def iter_frames(path: pathlib.Path, tolerate_partial_tail: bool) -> Iterator[tuple[dict, bytes]]:
    for metadata, raw in iter_frame_parts(path, tolerate_partial_tail):
        yield json.loads(metadata), raw


def iter_support_frames(
    path: pathlib.Path,
    tolerate_partial_tail: bool,
    observed_signatures: set[bytes],
    observed_slots: set[int],
) -> Iterator[tuple[dict | None, bytes]]:
    """Validate every frame but decode only support rows relevant to the target."""
    transaction_marker = b"rpc_finalized_transaction"
    block_marker = b"rpc_finalized_block"
    relevant_kinds = {"rpc_finalized_transaction", "rpc_finalized_block"}
    for metadata_bytes, raw in iter_frame_parts(path, tolerate_partial_tail):
        if transaction_marker in metadata_bytes:
            signature = JSON_SIGNATURE_PATTERN.search(metadata_bytes)
            if signature is None or signature.group(1) not in observed_signatures:
                yield None, b""
                continue
        elif block_marker in metadata_bytes:
            slot = JSON_SLOT_PATTERN.search(metadata_bytes)
            if slot is None or int(slot.group(1)) not in observed_slots:
                yield None, b""
                continue
        else:
            yield None, b""
            continue
        metadata = json.loads(metadata_bytes)
        if metadata.get("kind") not in relevant_kinds:
            yield None, b""
            continue
        yield metadata, raw


def partition_hour(path: pathlib.Path, root: pathlib.Path) -> dt.datetime:
    relative = path.relative_to(root / "partitions")
    year, month, day, hour = map(int, relative.parts[:4])
    return dt.datetime(year, month, day, hour, tzinfo=dt.timezone.utc)


def partition_data_path(directory: pathlib.Path) -> pathlib.Path | None:
    compressed = directory / "evidence.frames.zst"
    partial = directory / "evidence.frames.partial"
    if compressed.exists():
        return compressed
    if partial.exists():
        return partial
    return None


def immutable_support_path(directory: pathlib.Path) -> pathlib.Path | None:
    compressed = directory / "evidence.frames.zst"
    return compressed if compressed.exists() else None


def input_fingerprint(paths: list[pathlib.Path], root: pathlib.Path) -> tuple[list[dict], str]:
    inputs = []
    for path in paths:
        stat = path.stat()
        manifest_path = path.parent / "manifest.json"
        frozen_sha256 = None
        if path.name == "evidence.frames.zst" and manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            frozen_sha256 = manifest.get("compressedSha256")
        inputs.append(
            {
                "path": str(path.relative_to(root)),
                "size": stat.st_size,
                "mtimeNs": stat.st_mtime_ns,
                "sha256": frozen_sha256 or sha256_file(path),
                "sha256Source": "partition-manifest" if frozen_sha256 else "direct-file-hash",
            }
        )
    return inputs, hashlib.sha256(canonical_json(inputs)).hexdigest()


def reconcile(
    root: pathlib.Path,
    now: dt.datetime,
    grace_seconds: int,
    rpc_urls: list[str] | None = None,
    only_partition: str | None = None,
    recent_hours: int | None = None,
    max_partitions: int | None = None,
    telemetry: ReconciliationTelemetry | None = None,
) -> list[dict]:
    partition_root = root / "partitions"
    if telemetry is not None:
        with telemetry.stage("manifestScan"):
            manifests = sorted(partition_root.glob("*/*/*/*/manifest.json"))
    else:
        manifests = sorted(partition_root.glob("*/*/*/*/manifest.json"))
    results: list[dict] = []
    work_completed = 0
    for manifest_path in manifests:
        directory = manifest_path.parent
        if only_partition and directory.relative_to(partition_root).as_posix() != only_partition:
            continue
        if recent_hours is not None:
            hour = partition_hour(directory, root)
            if hour < now.replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=recent_hours):
                continue
        final_path = directory / "reconciliation-manifest.json"
        final_requires_upgrade = False
        if final_path.exists():
            final_report = json.loads(final_path.read_text())
            if finalized_manifest_has_immutable_inputs(final_report):
                results.append(final_report)
                continue
            final_requires_upgrade = True
        raw_manifest = json.loads(manifest_path.read_text())
        temporal_closed = bool(
            raw_manifest.get("temporalHourClosed")
            or raw_manifest.get("completeUtcHour")
        )
        if not temporal_closed:
            continue
        hour = partition_hour(directory, root)
        if now < hour + dt.timedelta(hours=1, seconds=grace_seconds):
            continue
        if max_partitions is not None and work_completed >= max_partitions:
            break
        target_path = partition_data_path(directory)
        if target_path is None:
            continue
        support_paths = [target_path]
        for offset in range(1, SUPPORT_HORIZON_HOURS + 1):
            support_hour = hour + dt.timedelta(hours=offset)
            support_dir = partition_root / support_hour.strftime("%Y/%m/%d/%H")
            support_path = immutable_support_path(support_dir)
            if support_path is not None:
                support_paths.append(support_path)
        if telemetry is not None:
            telemetry.begin_partition(directory.relative_to(partition_root).as_posix(), support_paths)
            with telemetry.stage("inputFingerprint"):
                inputs, inputs_sha256 = input_fingerprint(support_paths, root)
        else:
            inputs, inputs_sha256 = input_fingerprint(support_paths, root)
        status_path = directory / "reconciliation-status.json"
        if status_path.exists():
            prior = json.loads(status_path.read_text())
            if (
                prior.get("inputFingerprintSha256") == inputs_sha256
                and prior.get("reconciliationVersion") in COMPATIBLE_EXACT_VERSIONS
            ):
                exhausted = bool(prior.get("reconciliationRetryExhausted")) or bool(
                    prior.get("reconciliationVersion") == RECONCILIATION_VERSION
                    and prior.get("exactReplayComplete") is not True
                    and now >= hour + dt.timedelta(hours=5)
                )
                if exhausted:
                    if prior.get("reconciliationRetryExhausted") is not True:
                        prior["reconciliationRetryExhausted"] = True
                        prior["retryExhaustedAtUtc"] = now.isoformat().replace("+00:00", "Z")
                        prior["manifestSha256"] = hashlib.sha256(canonical_json({
                            key: value for key, value in prior.items() if key != "manifestSha256"
                        })).hexdigest()
                        atomic_json(status_path, prior)
                    if telemetry is not None:
                        telemetry.current["cachedUnchanged"] = True
                        telemetry.current["retryExhausted"] = True
                        telemetry.end_partition(prior)
                    results.append(prior)
                    continue
                # Exact generations are immutable and safely cacheable. An
                # incomplete generation must retry canonical RPC recovery even
                # when its evidence fingerprint is unchanged; otherwise a
                # transient null status becomes permanently deferred.
                if prior.get("exactReplayComplete") is True:
                    if final_requires_upgrade and finalized_manifest_has_immutable_inputs(prior):
                        final_tmp = final_path.with_suffix(".json.tmp")
                        final_tmp.write_text(json.dumps(prior, indent=2, sort_keys=True) + "\n")
                        os.replace(final_tmp, final_path)
                    if not final_requires_upgrade or finalized_manifest_has_immutable_inputs(prior):
                        if telemetry is not None:
                            telemetry.current["cachedUnchanged"] = True
                            telemetry.end_partition(prior)
                        results.append(prior)
                        continue

        observed: dict[str, dict] = {}
        duplicate_observations = 0
        start_slot: int | None = None
        end_slot: int | None = None
        target_scan = telemetry.stage("targetFrameScan") if telemetry is not None else contextlib.nullcontext()
        with target_scan:
            for metadata, _ in iter_frames(target_path, target_path.suffix == ".partial"):
                if telemetry is not None:
                    telemetry.count("targetFrames")
                slot = metadata.get("slot")
                if isinstance(slot, int):
                    start_slot = slot if start_slot is None else min(start_slot, slot)
                    end_slot = slot if end_slot is None else max(end_slot, slot)
                if metadata.get("kind") != "transaction" or not metadata.get("signature"):
                    continue
                if telemetry is not None:
                    telemetry.count("targetTransactionFrames")
                signature = metadata["signature"]
                if signature in observed:
                    duplicate_observations += 1
                else:
                    observed[signature] = {
                        "evidenceId": metadata.get("evidenceId"),
                        "successful": metadata.get("successful"),
                        "slot": metadata.get("slot"),
                        "missingRawTx": metadata.get("missingRawTx"),
                        "missingTradeEvent": metadata.get("missingTradeEvent"),
                        "missingReserveTransition": metadata.get("missingReserveTransition"),
                        "missingFeeEvidence": metadata.get("missingFeeEvidence"),
                        "decodeFailed": metadata.get("decodeFailed"),
                    }

        # Keep only compact finalized metadata for signatures observed in the
        # target receive hour. Raw transaction bodies are never retained.
        finalized: dict[str, dict] = {}
        observed_slots = {
            int(metadata["slot"]) for metadata in observed.values()
            if isinstance(metadata.get("slot"), int)
        }
        observed_signature_bytes = {
            signature.encode("ascii") for signature in observed
        }
        finalized_block_signatures: dict[int, tuple[list[str], str | None]] = {}
        support_scan = telemetry.stage("supportFrameScan") if telemetry is not None else contextlib.nullcontext()
        with support_scan:
            for path in support_paths:
                for metadata, raw in iter_support_frames(
                    path,
                    path.suffix == ".partial",
                    observed_signature_bytes,
                    observed_slots,
                ):
                    if telemetry is not None:
                        telemetry.count("supportFrames")
                    if metadata is None:
                        continue
                    if telemetry is not None:
                        telemetry.count("supportSelectedFrames")
                    signature = metadata.get("signature")
                    if (
                        metadata.get("kind") == "rpc_finalized_transaction"
                        and signature in observed
                    ):
                        finalized[signature] = {
                            key: metadata.get(key)
                            for key in (
                                "evidenceId",
                                "finalized",
                                "successful",
                                "failed",
                                "missingRawTx",
                                "missingTradeEvent",
                                "missingReserveTransition",
                                "missingFeeEvidence",
                                "decodeFailed",
                                "slot",
                                "transactionIndex",
                            )
                        }
                    slot = metadata.get("slot")
                    if metadata.get("kind") == "rpc_finalized_block" and slot in observed_slots:
                        try:
                            signatures = json.loads(raw).get("result", {}).get("signatures", [])
                        except Exception:
                            signatures = []
                        if isinstance(signatures, list):
                            finalized_block_signatures[int(slot)] = (
                                signatures, metadata.get("evidenceId")
                            )

        # Resolve only the block indexes needed by target signatures. This is
        # a second streaming pass so complete block payloads do not accumulate.
        required_by_slot: dict[int, list[tuple[int, str]]] = {}
        unindexed_signatures: set[str] = set()
        unindexed_by_slot: dict[int, set[str]] = {}
        for signature, metadata in finalized.items():
            slot = metadata.get("slot")
            tx_index = metadata.get("transactionIndex")
            if isinstance(slot, int) and isinstance(tx_index, int):
                required_by_slot.setdefault(slot, []).append((tx_index, signature))
            elif isinstance(slot, int):
                unindexed_signatures.add(signature)
                unindexed_by_slot.setdefault(slot, set()).add(signature)
        # Missing RPC-finality rows can still be canonically ordered from the
        # finalized block using the slot carried by the preserved raw update.
        for signature, metadata in observed.items():
            if signature in finalized:
                continue
            slot = metadata.get("slot")
            if isinstance(slot, int):
                unindexed_signatures.add(signature)
                unindexed_by_slot.setdefault(slot, set()).add(signature)
        canonical_ids: dict[str, str] = {}
        canonical_indexes: dict[str, int] = {}
        for slot, (signatures, evidence_id) in finalized_block_signatures.items():
            for tx_index, signature in required_by_slot.get(slot, []):
                if tx_index < len(signatures) and signatures[tx_index] == signature:
                    canonical_ids[signature] = evidence_id
                    canonical_indexes[signature] = tx_index
            # Older collector rows can lack the precomputed index even when
            # the immutable block-signature list proves it unambiguously.
            for tx_index, signature in enumerate(signatures):
                if (
                    signature in unindexed_signatures
                    and signature in unindexed_by_slot.get(slot, set())
                ):
                    if signature in finalized:
                        finalized[signature]["transactionIndex"] = tx_index
                    canonical_ids[signature] = evidence_id
                    canonical_indexes[signature] = tx_index

        block_recovery_path = directory / "canonical-block-recovery-v1.json"
        recovered_blocks: dict[str, list[str]] = {}
        unavailable_block_slots: set[int] = set()
        if block_recovery_path.exists():
            block_document = json.loads(block_recovery_path.read_text(encoding="utf-8"))
            recovered_blocks = dict(block_document.get("blocks") or {})
            unavailable_block_slots = {
                int(slot) for slot in block_document.get("unavailableSlots") or []
            }
        missing_block_slots = sorted({
            int(metadata["slot"])
            for signature, metadata in observed.items()
            if signature not in canonical_ids and isinstance(metadata.get("slot"), int)
        })
        uncached_slots = [
            slot for slot in missing_block_slots
            if (
                str(slot) not in recovered_blocks
                and slot not in finalized_block_signatures
                and slot not in unavailable_block_slots
            )
        ]
        if uncached_slots and rpc_urls:
            if telemetry is not None:
                telemetry.count("canonicalBlockRpcSlots", len(uncached_slots))
                telemetry.count("canonicalBlockRpcWorkers", min(8, len(uncached_slots)))
            def persist_blocks(batch: dict[str, list[str]]) -> None:
                recovered_blocks.update(batch)
                atomic_json(block_recovery_path, {
                    "schema": "PUMP_EXACT_CANONICAL_BLOCK_RECOVERY_V1",
                    "partitionKey": raw_manifest.get("partitionKey"),
                    "updatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                    "source": "getBlock(finalized,transactionDetails=signatures)",
                    "blocks": recovered_blocks,
                    "unavailableSlots": sorted(unavailable_block_slots),
                })

            block_recovery = telemetry.stage("canonicalBlockRpc") if telemetry is not None else contextlib.nullcontext()
            with block_recovery:
                fetched_blocks, fetched_unavailable = fetch_canonical_blocks(
                    rpc_urls, uncached_slots, persist_blocks
                )
                recovered_blocks.update(fetched_blocks)
                unavailable_block_slots.update(fetched_unavailable)
            persist_blocks({})
        for slot_text, signatures in recovered_blocks.items():
            if not isinstance(signatures, list):
                continue
            slot = int(slot_text)
            wanted = unindexed_by_slot.get(slot, set())
            if not wanted:
                continue
            block_evidence_id = "canonical-block-status:" + hashlib.sha256(
                canonical_json({"slot": slot, "signatures": signatures})
            ).hexdigest()
            for tx_index, signature in enumerate(signatures):
                if signature in wanted:
                    canonical_ids[signature] = block_evidence_id
                    canonical_indexes[signature] = tx_index

        recovery_path = directory / "canonical-finality-recovery-v1.json"
        recovery_rows: dict[str, dict[str, Any]] = {}
        if recovery_path.exists():
            recovery_document = json.loads(recovery_path.read_text(encoding="utf-8"))
            recovery_rows = dict(recovery_document.get("signatures") or {})
        # Query every unresolved realtime observation, not only signatures
        # already found in the finalized block. A fork observation is proven
        # noncanonical only when its observed slot is finalized without that
        # signature and archival signature status also has no finalized row.
        recoverable = sorted(
            signature for signature in observed
            if (
                signature not in finalized
                or finalized[signature].get("finalized") is not True
                or finalized[signature].get("missingRawTx") is True
                or finalized[signature].get("decodeFailed") is True
            )
        )
        missing_recovery = [
            signature for signature in recoverable
            if (
                signature not in recovery_rows
                or (
                    signature in canonical_ids
                    and recovery_rows[signature].get("confirmationStatus") != "finalized"
                )
            )
        ]
        if missing_recovery and rpc_urls:
            if telemetry is not None:
                telemetry.count("finalityRpcSignatures", len(missing_recovery))
                telemetry.count("finalityRpcBatches", (len(missing_recovery) + 255) // 256)
            finality_recovery = telemetry.stage("finalityStatusRpc") if telemetry is not None else contextlib.nullcontext()
            with finality_recovery:
                recovery_rows.update(fetch_finalized_statuses(rpc_urls, missing_recovery))
            atomic_json(recovery_path, {
                "schema": "PUMP_EXACT_CANONICAL_FINALITY_RECOVERY_V1",
                "partitionKey": raw_manifest.get("partitionKey"),
                "updatedAtUtc": now.isoformat().replace("+00:00", "Z"),
                "source": "getSignatureStatuses(searchTransactionHistory=true)",
                "signatures": recovery_rows,
            })

        transaction_recovery_path = directory / "canonical-transaction-recovery-v1.json"
        transaction_recovery_rows: dict[str, dict[str, Any]] = {}
        if transaction_recovery_path.exists():
            transaction_document = json.loads(
                transaction_recovery_path.read_text(encoding="utf-8")
            )
            transaction_recovery_rows = dict(transaction_document.get("signatures") or {})
        archival_needed = [
            signature for signature in recoverable
            if (
                signature in canonical_ids
                and recovery_rows.get(signature, {}).get("confirmationStatus") != "finalized"
                and signature not in transaction_recovery_rows
            )
        ]
        if archival_needed and rpc_urls:
            if telemetry is not None:
                telemetry.count("archivalTransactionRpcSignatures", len(archival_needed))
                telemetry.count("archivalTransactionRpcWorkers", min(8, len(archival_needed)))
            archival_recovery = telemetry.stage("archivalTransactionRpc") if telemetry is not None else contextlib.nullcontext()
            with archival_recovery:
                transaction_recovery_rows.update(
                    fetch_finalized_transactions(rpc_urls, archival_needed)
                )
            atomic_json(transaction_recovery_path, {
                "schema": "PUMP_EXACT_CANONICAL_TRANSACTION_RECOVERY_V1",
                "partitionKey": raw_manifest.get("partitionKey"),
                "updatedAtUtc": now.isoformat().replace("+00:00", "Z"),
                "source": "getTransaction(finalized)",
                "signatures": transaction_recovery_rows,
            })
        for signature in recoverable:
            status = transaction_recovery_rows.get(signature) or recovery_rows.get(signature)
            early = observed[signature]
            if (
                not status
                or status.get("confirmationStatus") != "finalized"
                or status.get("slot") != early.get("slot")
            ):
                continue
            successful = status.get("successful") is True
            finalized[signature] = {
                "evidenceId": "canonical-status:" + hashlib.sha256(
                    canonical_json({"signature": signature, **status})
                ).hexdigest(),
                "finalized": True,
                "successful": successful,
                "failed": not successful,
                "missingRawTx": bool(early.get("missingRawTx")),
                "missingTradeEvent": bool(early.get("missingTradeEvent")),
                "missingReserveTransition": bool(early.get("missingReserveTransition")),
                "missingFeeEvidence": bool(early.get("missingFeeEvidence")),
                "decodeFailed": bool(early.get("decodeFailed")),
                "slot": early.get("slot"),
                "transactionIndex": canonical_indexes.get(signature),
            }

        canonical_slot_signatures: dict[int, set[str]] = {
            slot: set(signatures)
            for slot, (signatures, _) in finalized_block_signatures.items()
        }
        canonical_block_evidence_ids: dict[int, str | None] = {
            slot: evidence_id
            for slot, (_, evidence_id) in finalized_block_signatures.items()
        }
        for slot_text, signatures in recovered_blocks.items():
            if not isinstance(signatures, list):
                continue
            slot = int(slot_text)
            canonical_slot_signatures[slot] = set(signatures)
            canonical_block_evidence_ids[slot] = "canonical-block-status:" + hashlib.sha256(
                canonical_json({"slot": slot, "signatures": signatures})
            ).hexdigest()
        for slot in unavailable_block_slots:
            canonical_block_evidence_ids[slot] = "canonical-block-unavailable:" + hashlib.sha256(
                canonical_json({"slot": slot, "finalizedBlockUnavailable": True})
            ).hexdigest()
        noncanonical_signatures = {
            signature
            for signature, early in observed.items()
            if (
                signature not in finalized
                and isinstance(early.get("slot"), int)
                and (
                    (
                        int(early["slot"]) in canonical_slot_signatures
                        and signature not in canonical_slot_signatures[int(early["slot"])]
                    )
                    or int(early["slot"]) in unavailable_block_slots
                )
                and recovery_rows.get(signature, {}).get("notFinalizedAtRecovery") is True
            )
        }

        counts = {
            "relevantTransactionsObserved": len(observed),
            "duplicateObservations": duplicate_observations,
            "nonCanonicalObservations": 0,
            "finalized": 0,
            "successful": 0,
            "failed": 0,
            "unresolved": 0,
            "missingFinality": 0,
            "missingRawTx": 0,
            "missingTradeEvent": 0,
            "missingReserveTransition": 0,
            "missingCanonicalOrdering": 0,
            "missingFeeEvidence": 0,
            "decodeFailures": 0,
            "successFailureMismatch": 0,
        }
        joined_ids: list[str] = []
        unresolved_signatures: list[str] = []
        missing_canonical_signatures: list[str] = []
        for signature, early in observed.items():
            final = finalized.get(signature)
            if final is None:
                if signature in noncanonical_signatures:
                    counts["nonCanonicalObservations"] += 1
                    joined_ids.append(early["evidenceId"])
                    block_id = canonical_block_evidence_ids.get(int(early["slot"]))
                    if block_id:
                        joined_ids.append(block_id)
                    continue
                counts["unresolved"] += 1
                counts["missingFinality"] += 1
                unresolved_signatures.append(signature)
                continue
            joined_ids.extend([early["evidenceId"], final["evidenceId"]])
            if final.get("finalized"):
                counts["finalized"] += 1
            else:
                counts["unresolved"] += 1
                counts["missingFinality"] += 1
            if final.get("successful") is True:
                counts["successful"] += 1
            elif final.get("failed") is True:
                counts["failed"] += 1
            early_success = early.get("successful")
            if early_success is not None and early_success != final.get("successful"):
                counts["successFailureMismatch"] += 1
            for field, key in (
                ("missingRawTx", "missingRawTx"),
                ("missingTradeEvent", "missingTradeEvent"),
                ("missingReserveTransition", "missingReserveTransition"),
                ("missingFeeEvidence", "missingFeeEvidence"),
                ("decodeFailed", "decodeFailures"),
            ):
                counts[key] += int(bool(final.get(field)))

            canonical_id = canonical_ids.get(signature)
            canonical_ok = bool(canonical_id)
            if canonical_id:
                joined_ids.append(canonical_id)
            if not canonical_ok:
                counts["missingCanonicalOrdering"] += 1
                missing_canonical_signatures.append(signature)

        blocking_keys = (
            "unresolved",
            "missingFinality",
            "missingRawTx",
            "missingTradeEvent",
            "missingReserveTransition",
            "missingCanonicalOrdering",
            "missingFeeEvidence",
            "decodeFailures",
            "successFailureMismatch",
        )
        final_fingerprint = telemetry.stage("postRunInputFingerprint") if telemetry is not None else contextlib.nullcontext()
        with final_fingerprint:
            final_inputs, final_inputs_sha256 = input_fingerprint(support_paths, root)
        input_stable = final_inputs_sha256 == inputs_sha256
        exact_complete = all(counts[key] == 0 for key in blocking_keys) and input_stable
        retry_exhausted = bool(
            not exact_complete
            and input_stable
            and now >= hour + dt.timedelta(hours=5)
            and (counts["unresolved"] == 0 or bool(rpc_urls))
        )
        joined_ids.sort()
        report = {
            "reconciliationVersion": RECONCILIATION_VERSION,
            "schemaVersion": raw_manifest.get("schemaVersion"),
            "schemaSha256": raw_manifest.get("schemaSha256"),
            "partitionKey": raw_manifest.get("partitionKey"),
            "temporalHourClosed": True,
            "exactReplayComplete": exact_complete,
            "reconciliationRetryExhausted": retry_exhausted,
            "retryExhaustedAtUtc": (
                now.isoformat().replace("+00:00", "Z") if retry_exhausted else None
            ),
            "reconciledAtUtc": now.isoformat().replace("+00:00", "Z"),
            "reconciliationScope": (
                "bounded-memory streaming join of target receive-hour observations "
                f"to finalized transaction and finalized block evidence across target and next {SUPPORT_HORIZON_HOURS} receive-hour partitions"
            ),
            "inputEvidence": inputs,
            "inputFingerprintSha256": inputs_sha256,
            "inputStableDuringRun": input_stable,
            "postRunInputFingerprintSha256": final_inputs_sha256,
            "startSlot": start_slot,
            "endSlot": end_slot,
            "targetCompressedSha256": raw_manifest.get("compressedSha256"),
            "targetManifestSha256": sha256_file(manifest_path),
            "joinedEvidenceIdsSha256": hashlib.sha256("\n".join(joined_ids).encode()).hexdigest(),
            "joinedEvidenceIdCount": len(joined_ids),
            "canonicalFinalityRecovery": {
                "path": str(recovery_path.relative_to(root)) if recovery_path.exists() else None,
                "sha256": sha256_file(recovery_path) if recovery_path.exists() else None,
                "recoveredSignatureCount": sum(
                    1 for signature in recoverable if signature in finalized
                ),
            },
            "canonicalBlockRecovery": {
                "path": str(block_recovery_path.relative_to(root)) if block_recovery_path.exists() else None,
                "sha256": sha256_file(block_recovery_path) if block_recovery_path.exists() else None,
                "recoveredSlotCount": len(recovered_blocks),
                "unavailableFinalizedSlotCount": len(unavailable_block_slots),
            },
            "canonicalTransactionRecovery": {
                "path": str(transaction_recovery_path.relative_to(root)) if transaction_recovery_path.exists() else None,
                "sha256": sha256_file(transaction_recovery_path) if transaction_recovery_path.exists() else None,
                "recoveredSignatureCount": sum(
                    1 for signature in recoverable if signature in transaction_recovery_rows
                ),
            },
            "nonCanonicalObservationProof": {
                "classification": "observed realtime signature absent from its finalized canonical block and not finalized in archival signature status",
                "count": len(noncanonical_signatures),
                "signaturesSha256": hashlib.sha256(
                    "\n".join(sorted(noncanonical_signatures)).encode()
                ).hexdigest(),
            },
            "coverage": counts,
            "unresolvedSignatures": unresolved_signatures[:100],
            "unresolvedSignaturesTruncated": len(unresolved_signatures) > 100,
            "missingCanonicalSignatures": missing_canonical_signatures[:100],
            "missingCanonicalSignaturesTruncated": len(missing_canonical_signatures) > 100,
        }
        report["manifestSha256"] = hashlib.sha256(canonical_json(report)).hexdigest()
        output_write = telemetry.stage("outputWrite") if telemetry is not None else contextlib.nullcontext()
        with output_write:
            temporary = status_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            os.replace(temporary, status_path)
            if exact_complete:
                final_tmp = final_path.with_suffix(".json.tmp")
                final_tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
                os.replace(final_tmp, final_path)
        if telemetry is not None:
            telemetry.end_partition(report)
        results.append(report)
        work_completed += 1
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=pathlib.Path)
    parser.add_argument("--grace-seconds", type=int, default=90)
    parser.add_argument("--env", type=pathlib.Path, default=pathlib.Path("/root/bot/EdgeSniffer/.env"))
    parser.add_argument("--partition", help="Reconcile one YYYY/MM/DD/HH partition")
    parser.add_argument("--recent-hours", type=int, help="Only revisit this many recent receive hours")
    parser.add_argument("--max-partitions", type=int, help="Bound unfinished partitions processed per invocation")
    parser.add_argument("--telemetry-jsonl", type=pathlib.Path)
    parser.add_argument("--dispatch-stamp", type=pathlib.Path)
    args = parser.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    telemetry = ReconciliationTelemetry(args.telemetry_jsonl, args.dispatch_stamp)
    try:
        reports = reconcile(
            args.root, now, args.grace_seconds, load_rpc_urls(args.env), args.partition,
            args.recent_hours, args.max_partitions, telemetry,
        )
        telemetry.finish("SUCCESS")
        print(json.dumps({"atUtc": now.isoformat(), "partitions": reports}, indent=2))
        return 0
    except Exception as error:
        telemetry.finish("ERROR", f"{type(error).__name__}: {error}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
