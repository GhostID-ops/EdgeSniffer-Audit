#!/usr/bin/env python3
"""Immutable V3-to-Rust adapter and no-submit fixture/prospective harness."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import select
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TARGET = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ"
V3_HASH = "6f42c24221a50341c8c5626ae3a987b564065ba0c9de9a1e3d948fa5ea432817"
V2_HASH = "274d04efb9fea673c60251cc99280a8f52a0d00b085e0636163092bc0dcb89f7"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
SIGNAL_SCHEMA = "8ycb-production-signal-v1"
SOURCE_SIGNAL_SCHEMA = "8ycb-prospective-signal-v3"
SOURCE_RESULT = "CREATE_AND_SAME_TRANSACTION_8YCB_ATOMIC_BUY_PROVEN"
BASELINE_V3_IDS = {
    "8ycb-v3-d0acf0dfdd2e7429809c415c",
    "8ycb-v3-05b90c336b0c502e3398a6a4",
    "8ycb-v3-db84568c1bbce7f93f36fe21",
    "8ycb-v3-a5e4f7e99ee24f37b43fb66f",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_hash(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values: Iterable[int], divisor: float = 1000.0) -> dict[str, Any]:
    raw = [float(value) / divisor for value in values]
    return {
        "n": len(raw),
        "unit": "ms",
        "p50": percentile(raw, 0.50),
        "p75": percentile(raw, 0.75),
        "p90": percentile(raw, 0.90),
        "p95": percentile(raw, 0.95),
        "max": max(raw) if raw else None,
    }


def validate_source_signal(row: dict[str, Any]) -> None:
    if row.get("kind") != "signal" or row.get("schema") != SOURCE_SIGNAL_SCHEMA:
        raise ValueError("not a canonical V3 signal row")
    if row.get("event_definition_result") != SOURCE_RESULT:
        raise ValueError("canonical detector decision is not qualifying")
    if row.get("frozen_spec_sha256") != V3_HASH:
        raise ValueError("V3 frozen-spec hash mismatch")
    if row.get("target_wallet") != TARGET:
        raise ValueError("target wallet mismatch")
    required = ("event_id", "mint", "signature", "source_slot", "atomic_initial_buy", "timing")
    if any(row.get(field) is None for field in required):
        raise ValueError("required canonical signal field missing")
    atomic = row["atomic_initial_buy"]
    timing = row["timing"]
    for value in (
        row["source_slot"],
        atomic.get("quote_lamports"),
        timing.get("observer_create_line_read_unix_ns"),
        timing.get("observer_atomic_line_read_unix_ns"),
        timing.get("signal_ready_unix_ns"),
    ):
        if not isinstance(value, int) or value <= 0:
            raise ValueError("required canonical numeric field is invalid")
    tokens_out = atomic.get("tokens_out_raw")
    if tokens_out is not None and (not isinstance(tokens_out, int) or tokens_out <= 0):
        raise ValueError("optional canonical tokens-out field is invalid")


def adapt_v3_signal(row: dict[str, Any]) -> dict[str, Any]:
    """Map, but never re-decide, a canonical V3 detector admission."""
    validate_source_signal(row)
    timing = row["timing"]
    create_receipt = timing["observer_create_line_read_unix_ns"]
    final_receipt = max(create_receipt, timing["observer_atomic_line_read_unix_ns"])
    ready = timing["signal_ready_unix_ns"]
    if ready < final_receipt:
        raise ValueError("canonical signal timestamps are non-monotonic")
    signal = {
        "schema": SIGNAL_SCHEMA,
        "mint": row["mint"],
        "createSignature": row["signature"],
        "slot": row["source_slot"],
        "transactionIndex": row.get("source_transaction_index"),
        "targetWallet": row["target_wallet"],
        "atomicTargetBuyLamports": row["atomic_initial_buy"]["quote_lamports"],
        "atomicTargetTokensOutRaw": row["atomic_initial_buy"].get("tokens_out_raw"),
        "createReceiveUnixNs": create_receipt,
        "finalTriggerInputReceiveUnixNs": final_receipt,
        "signalReadyUnixNs": ready,
        "uniqueSignalId": row["event_id"],
        "frozenSpecSha256": V3_HASH,
    }
    signal["interfaceSha256"] = canonical_hash(signal)
    return signal


class Builder:
    def __init__(self, executable: Path) -> None:
        self.process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
        )
        if self.process.stdout is None:
            raise RuntimeError("builder stdout unavailable")
        ready, _, _ = select.select([self.process.stdout], [], [], 20.0)
        if not ready:
            self.process.kill()
            raise RuntimeError("builder did not become ready in 20 seconds")
        self.ready = json.loads(self.process.stdout.readline())
        if self.ready.get("kind") != "8ycbProductionNoSubmitBuilderReady":
            raise RuntimeError(f"unexpected builder greeting: {self.ready}")

    def build(self, signal: dict[str, Any], receipt_ns: int) -> dict[str, Any]:
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("builder pipes unavailable")
        wire_signal = {key: value for key, value in signal.items() if key != "interfaceSha256"}
        request = {
            "kind": "build8ycbProductionNoSubmit",
            "signal": wire_signal,
            "bondingCurve": "",
            "tokenProgramId": TOKEN_2022,
            "adapterReceiptUnixNs": receipt_ns,
        }
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        response = json.loads(self.process.stdout.readline())
        if not response.get("ok"):
            raise RuntimeError(f"builder rejected {signal['uniqueSignalId']}: {response}")
        if response.get("submissionCount") != 0 or not response.get("broadcastSuppressed"):
            raise RuntimeError("builder safety invariant failed")
        return response

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=3)


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def synthetic_source_row(spec: dict[str, Any], ordinal: int) -> dict[str, Any]:
    base = 1_700_000_000_000_000_000 + ordinal * 10_000_000
    return {
        "kind": "signal",
        "schema": SOURCE_SIGNAL_SCHEMA,
        "event_definition_result": SOURCE_RESULT,
        "event_id": spec["id"],
        "frozen_spec_sha256": V3_HASH,
        "mint": spec["mint"],
        "signature": spec["signature"],
        "source_slot": spec["slot"],
        "source_transaction_index": spec.get("index"),
        "target_wallet": TARGET,
        "atomic_initial_buy": {
            "quote_lamports": spec["lamports"],
            "tokens_out_raw": 97_545_454_545_454,
        },
        "timing": {
            "observer_create_line_read_unix_ns": base,
            "observer_atomic_line_read_unix_ns": base + 1_000_000,
            "signal_ready_unix_ns": base + 2_000_000,
        },
    }


FIXTURE_SPECS = [
    {"label":"V3_1","id":"8ycb-v3-d0acf0dfdd2e7429809c415c","mint":"DFJAQTdQ2ncV5pZTo51KjijgfDyTXiJjdeTXqpMFpump","signature":"2hK46g3jBxXgdqSYE1zASfKnWZKEWzhk27ATdNeefJ8wNyGcro4UH4T2simayz5AhY8Bj1uKfiQgimGpu6BBxHzm","slot":444089793,"index":22,"lamports":3078996480},
    {"label":"V3_2","id":"8ycb-v3-05b90c336b0c502e3398a6a4","mint":"2ST98uEyA72ZKTqNUhdE1o1oKWf3UmMghXxr9BjNpump","signature":"48p1neE8CBuASq422GaNr6ekFbPDbQtYNZTTdiuDEkSczhhboqjfwLFMJar4ZRrswxznGKZeAcMVhdM1mR4KbgKj","slot":444092503,"index":22,"lamports":1023940800},
    {"label":"V3_3","id":"8ycb-v3-db84568c1bbce7f93f36fe21","mint":"Dydcnpin4LytrCAk2AHajqF1NY3m1gSfEbdZbiR9pump","signature":"U8B4E1KbDqGJrTkvVSAVDZovM9sKmtLMt8i9BeVmbXFumJPEtp8i83v8chokuiNeQ3D8zT62jtGXSq6dp7aqX8b","slot":444099658,"index":42,"lamports":3048205209},
    {"label":"V3_4","id":"8ycb-v3-a5e4f7e99ee24f37b43fb66f","mint":"aaoZKcFoekn9LHGn5PwE5H7DkXuEPMh7s37X94upump","signature":"45Ybi55hau4mHcRASMomzZXvdrTVJNCkHdqqMC2xTPPzpwnQPtYwuZGYvJ3VZWHfwedGM5GSpRrPrtderQZ3j1U1","slot":444100416,"index":22,"lamports":3048186210},
    {"label":"V2_1","id":"fixture-v2-7446f60892db0c62ffd89926","mint":"63cHccqc1MmKeAD1GWjFd27i7W6jUaQpB3GBqhXBpump","signature":"4tBfwAcMiYJ62VNJNsr4rJRGZpHmjKRjh1ixBWWf1qmgcHPMx7xmmp9PrSvJy8yZQ7PqyJner4f5tEMoSQAPezNz","slot":443893304,"index":45,"lamports":3048968640},
    {"label":"V2_2","id":"fixture-v2-d5b3f9245d5e9dcf30723df9","mint":"CFyewCh4o2GeRYnjCNPKota7Bs5YWxcaWqjozT6tpump","signature":"2ktyFCwCVh1attrFHU7b82KA8fQjXpZteLyuM2RsfNdkS9d8SqddD8higo7YaW4LCfv3tLVgxxcsJGzARkQ2DRRn","slot":444014706,"index":15,"lamports":3048947760},
    {"label":"V2_3","id":"fixture-v2-3897c9b8adce73f401544bf0","mint":"RSSZscEHBNoph7SmeqAvBt2aMF3Gz76UAKuStXGpump","signature":"tBpDrLbS3d6HyaJPJghV6ujS46pqcrrMHzk9cQZj6oXwPtW1JqRBxQkywBbhkC7i3nU7b5z9qUbvYduQiKoRyoX","slot":444049080,"index":14,"lamports":3078982560},
    {"label":"V2_4","id":"fixture-v2-91225e1d1a0edbd683ab5373","mint":"62qsCvd6cF7yacKLEenYdS3ymcX9cju3zjx8MGL6pump","signature":"5HsXBy1pvko6uZGSDtnwRQQv4Uo1Fcu1QPtxpsXuw3Rx5i9UMGWtJzFyhFk8ZkUEfUv2uDHXa5fw3Smhk8PfnGLu","slot":444050603,"index":2,"lamports":3048982560},
    {"label":"V2_5","id":"fixture-v2-19e0e2216cd443ddef71993a","mint":"3hgstunsHGUZyTVF8CTiSvUqZNoTwCBybY1oAfospump","signature":"UeUiyqpPmvwZnWexBTWu5VQRAxNYMaNPj6WGjsoU8s2gcvmTjR8r9WAo8Y2Bv6VqvFS5wy9VrGG7hPVnAWiN8rs","slot":444050838,"index":10,"lamports":3078968640},
    {"label":"V2_6","id":"fixture-v2-4f9acce7396781a286a260fa","mint":"6dz4AmTxnT9sBf6w9xnNyGMrwmnvZCSg2e7qTSiSpump","signature":"m71szyHTxZtieWzqJmXLFssptnAceDcKF5vp5QDaYB4rvMLb4T18BQSisy9DKDoyXXTatK3dJyCdAjLeBPuZUnG","slot":444052066,"index":25,"lamports":3048996480},
    {"label":"EP1B_HANDOFF","id":"fixture-ep1b-handoff","mint":"EP1BszqYXvMitfV58vVfdEn6WJEEJ8vmbGsSuAGxpump","signature":"rcoEigR5rupo1aRAkiaDo9rC5VxKyM92ofsdW6wsrxKFVFR5oAYBsmwW8WbQYBDXD3wk8gD1EA1dVkkisgmGjfD","slot":444071980,"index":573,"lamports":3078968640},
    {"label":"TAHKR4_REGRESSION","id":"fixture-tahkr4-regression","mint":"tahKR4TA337akDkgfGZ5Qaeng4ABdov49rVePABpump","signature":"3msUjoGrsRfbXTrn3Q6CLpRHGtx1F8LFMkKA587BN75WXCKPWMywbwEsCw7j6PVPUVLfYWvjTcAzkuYnBpxjiQ3i","slot":443866447,"index":44,"lamports":3048926880},
]


def parity_mode(args: argparse.Namespace) -> int:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    source_rows = {row.get("event_id"): row for row in read_json_lines(Path(args.ledger)) if row.get("kind") == "signal"}
    rows: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for ordinal, spec in enumerate(FIXTURE_SPECS):
        if spec["id"] in BASELINE_V3_IDS:
            row = source_rows.get(spec["id"])
            if row is None:
                raise RuntimeError(f"missing canonical V3 fixture {spec['id']}")
            provenance = "actual immutable V3 cohort ledger"
        else:
            row = synthetic_source_row(spec, ordinal)
            provenance = "canonical identity transcribed from frozen V2/forensic artifact; timing rebased for adapter test"
        rows.append(row)
        manifest.append({"label": spec["label"], "expected": spec, "provenance": provenance})

    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates = 0
    mismatches: list[dict[str, Any]] = []
    for row, spec in zip(rows, FIXTURE_SPECS):
        for occurrence in range(2):
            signal = adapt_v3_signal(row)
            if signal["uniqueSignalId"] in seen:
                duplicates += 1
                continue
            seen.add(signal["uniqueSignalId"])
            accepted.append(signal)
            for field, expected in (
                ("mint", spec["mint"]),
                ("createSignature", spec["signature"]),
                ("targetWallet", TARGET),
            ):
                if signal[field] != expected:
                    mismatches.append({"label": spec["label"], "field": field, "actual": signal[field], "expected": expected})

    negative_rows = []
    for label, mutation in (
        ("wrong_target", {"target_wallet": "11111111111111111111111111111111"}),
        ("wrong_spec_hash", {"frozen_spec_sha256": V2_HASH}),
        ("nonqualifying_decision", {"event_definition_result": "REJECTED"}),
        ("generic_control", {"kind": "generic_control"}),
    ):
        row = copy.deepcopy(rows[0])
        row.update(mutation)
        negative_rows.append((label, row))
    false_positives = []
    for label, row in negative_rows:
        try:
            adapt_v3_signal(row)
            false_positives.append(label)
        except ValueError:
            pass

    false_negatives = [spec["label"] for spec in FIXTURE_SPECS if spec["id"] not in seen]
    parity = {
        "schema": "8ycb-production-translation-parity-v1",
        "generatedAtUtc": utc_now(),
        "fixtureCount": len(FIXTURE_SPECS),
        "acceptedUniqueIntents": len(accepted),
        "falseNegatives": false_negatives,
        "falsePositives": false_positives,
        "duplicateSourcePresentationsSuppressed": duplicates,
        "duplicateIntents": 0,
        "identityMismatches": mismatches,
        "exactMintSignatureTargetAgreement": not mismatches,
        "status": "PASS" if not false_negatives and not false_positives and not mismatches and len(accepted) == 12 else "FAIL",
    }
    write_json(output / "fixture_manifest.json", manifest)
    write_json(output / "signal_adapter_parity.json", parity)
    with (output / "immutable_signal_fixtures.jsonl").open("w", encoding="utf-8") as handle:
        for signal in accepted:
            handle.write(json.dumps(signal, sort_keys=True, separators=(",", ":")) + "\n")

    if parity["status"] != "PASS":
        return 1

    builder = Builder(Path(args.builder))
    results: list[dict[str, Any]] = []
    try:
        for repetition in range(args.repetitions):
            for base in accepted:
                signal = copy.deepcopy(base)
                now = time.time_ns()
                signal["createReceiveUnixNs"] = now - 2_000_000
                signal["finalTriggerInputReceiveUnixNs"] = now - 1_000_000
                signal["signalReadyUnixNs"] = now
                signal["uniqueSignalId"] = f"{base['uniqueSignalId']}-bench-{repetition:03d}"
                signal["interfaceSha256"] = canonical_hash({key: value for key, value in signal.items() if key != "interfaceSha256"})
                receipt = time.time_ns()
                result = builder.build(signal, receipt)
                result["fixtureOnly"] = True
                result["fixtureBaseSignalId"] = base["uniqueSignalId"]
                results.append(result)
    finally:
        builder.close()

    result_path = output / "fixture_build_results.jsonl"
    if result_path.exists():
        result_path.unlink()
    for result in results:
        append_jsonl(result_path, result)
    buy = [result["v3SignalToBuyTxReadyUs"] for result in results]
    sell = [probe["decisionToTxReadyUs"] for result in results for probe in result["sellProbes"]]
    benchmark = {
        "schema": "8ycb-production-translation-fixture-benchmark-v1",
        "generatedAtUtc": utc_now(),
        "fixtureOnlyNotProspective": True,
        "transactionsBuiltAndSigned": len(results) * 4,
        "transactionsSubmitted": 0,
        "buySignalToTransactionReady": distribution(buy),
        "buyThresholdHitRate": {f"le{limit}ms": sum(value <= limit * 1000 for value in buy) / len(buy) for limit in (10, 25, 50, 100)},
        "sellDecisionToTransactionReady": distribution(sell),
        "buyBuildDuration": distribution([result["buyBuildDurationUs"] for result in results]),
        "sellIntentToTransactionReady": distribution([probe["intentToTxReadyUs"] for result in results for probe in result["sellProbes"]]),
        "exitReasonsExercised": sorted({probe["decisionReason"] for result in results for probe in result["sellProbes"]}),
        "duplicateSellBeforeAck": sum(bool(probe["duplicateSellBeforeAck"]) for result in results for probe in result["sellProbes"]),
        "duplicateSellAfterTerminalAck": sum(bool(probe["duplicateSellAfterTerminalAck"]) for result in results for probe in result["sellProbes"]),
        "submissionCount": 0,
    }
    write_json(output / "fixture_benchmark.json", benchmark)
    return 0


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema":"8ycb-production-translation-state-v1","ledgerOffset":0,"processedSignalIds":[],"externalTelemetryIds":[],"submissionCount":0,"duplicateIntents":0,"buildErrors":0}
    return json.loads(path.read_text(encoding="utf-8"))


def prospective_summary(output: Path, state: dict[str, Any]) -> dict[str, Any]:
    results_path = output / "prospective_build_results.jsonl"
    results = read_json_lines(results_path) if results_path.exists() else []
    eligible = [row for row in results if row.get("realTimeLatencyEligible")]
    buys = [row["v3SignalToBuyTxReadyUs"] for row in eligible]
    sells = [probe["decisionToTxReadyUs"] for row in results for probe in row.get("sellProbes", [])]
    return {
        "schema":"8ycb-production-translation-prospective-summary-v1",
        "updatedAtUtc":utc_now(),
        "productionSignalsObserved":len(results),
        "realTimeLatencyEligible":len(eligible),
        "lateStartupHandoffs":len(results)-len(eligible),
        "buySignalToTransactionReady":distribution(buys),
        "buyThresholdHitRate":({f"le{limit}ms":sum(value <= limit*1000 for value in buys)/len(buys) for limit in (10,25,50,100)} if buys else {f"le{limit}ms":None for limit in (10,25,50,100)}),
        "sellDecisionToTransactionReady":distribution(sells),
        "submissionCount":state.get("submissionCount",0),
        "duplicateIntents":state.get("duplicateIntents",0),
        "buildErrors":state.get("buildErrors",0),
    }


def service_mode(args: argparse.Namespace) -> int:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "state.json"
    state = load_state(state_path)
    processed = set(state["processedSignalIds"])
    external_done = set(state["externalTelemetryIds"])
    builder = Builder(Path(args.builder))
    append_jsonl(output / "service_events.jsonl", {"kind":"serviceStart","atUtc":utc_now(),"pid":os.getpid(),"builder":builder.ready,"baselineSignalIds":sorted(BASELINE_V3_IDS),"submissionCount":0})

    ledger = Path(args.ledger)
    try:
        with ledger.open("r", encoding="utf-8", errors="strict") as handle:
            offset = int(state.get("ledgerOffset", 0))
            if offset > ledger.stat().st_size:
                raise RuntimeError("ledger truncated below committed offset")
            handle.seek(offset)
            checkpoint_dirty = False
            last_checkpoint = time.monotonic()
            while True:
                position = handle.tell()
                line = handle.readline()
                if not line:
                    handle.seek(position)
                    if checkpoint_dirty and time.monotonic() - last_checkpoint >= 1.0:
                        write_json(state_path, state)
                        checkpoint_dirty = False
                        last_checkpoint = time.monotonic()
                    time.sleep(0.002)
                    continue
                if not line.endswith("\n"):
                    # The producer may be between write(2) calls. Never advance or
                    # classify an incomplete active record as malformed.
                    handle.seek(position)
                    time.sleep(0.002)
                    continue
                state["ledgerOffset"] = handle.tell()
                checkpoint_dirty = True
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as error:
                    append_jsonl(output / "adapter_errors.jsonl", {"atUtc":utc_now(),"kind":"malformedLedgerLine","offset":position,"error":str(error),"submissionCount":0})
                    state["buildErrors"] += 1
                    write_json(state_path, state)
                    continue

                event_id = row.get("event_id")
                if row.get("kind") == "signal" and event_id not in BASELINE_V3_IDS and event_id not in processed:
                    receipt_ns = time.time_ns()
                    try:
                        signal = adapt_v3_signal(row)
                        lag_ns = receipt_ns - signal["signalReadyUnixNs"]
                        append_jsonl(output / "immutable_signals.jsonl", {"kind":"immutableSignalAccepted","adapterReceiptUnixNs":receipt_ns,"signal":signal,"submissionCount":0})
                        result = builder.build(signal, receipt_ns)
                        result["prospectiveProductionShadow"] = True
                        result["realTimeLatencyEligible"] = 0 <= lag_ns <= 5_000_000_000
                        result["lateStartupHandoff"] = lag_ns > 5_000_000_000
                        append_jsonl(output / "prospective_build_results.jsonl", result)
                        processed.add(event_id)
                        state["processedSignalIds"] = sorted(processed)
                        write_json(state_path, state)
                        write_json(output / "prospective_summary.json", prospective_summary(output, state))
                        checkpoint_dirty = False
                        last_checkpoint = time.monotonic()
                    except Exception as error:
                        state["buildErrors"] += 1
                        append_jsonl(output / "adapter_errors.jsonl", {"atUtc":utc_now(),"kind":"signalBuildError","eventId":event_id,"error":repr(error),"submissionCount":0})
                        write_json(state_path, state)
                        checkpoint_dirty = False
                        last_checkpoint = time.monotonic()
                elif row.get("kind") == "signal" and event_id in processed:
                    state["duplicateIntents"] += 0
                elif row.get("kind") == "external_flow" and row.get("cohort") != "GENERIC_CONTROL" and event_id in processed and event_id not in external_done:
                    windows = row.get("windows", {})
                    append_jsonl(output / "recurrent_wallet_telemetry.jsonl", {
                        "kind":"frozenRecurrentWalletTelemetry",
                        "eventId":event_id,
                        "mint":row.get("mint"),
                        "windows":{key:windows.get(key) for key in ("100","250","500","1000")},
                        "triggerUsed":False,
                        "submissionCount":0,
                    })
                    external_done.add(event_id)
                    state["externalTelemetryIds"] = sorted(external_done)
                    write_json(state_path, state)
                    checkpoint_dirty = False
                    last_checkpoint = time.monotonic()

                state["submissionCount"] = 0
                if time.monotonic() - last_checkpoint >= 1.0:
                    write_json(state_path, state)
                    write_json(output / "prospective_summary.json", prospective_summary(output, state))
                    checkpoint_dirty = False
                    last_checkpoint = time.monotonic()
    finally:
        builder.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    parity = sub.add_parser("parity")
    parity.add_argument("--ledger", required=True)
    parity.add_argument("--builder", required=True)
    parity.add_argument("--output", required=True)
    parity.add_argument("--repetitions", type=int, default=20)
    service = sub.add_parser("service")
    service.add_argument("--ledger", required=True)
    service.add_argument("--builder", required=True)
    service.add_argument("--output", required=True)
    args = parser.parse_args()
    return parity_mode(args) if args.mode == "parity" else service_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
