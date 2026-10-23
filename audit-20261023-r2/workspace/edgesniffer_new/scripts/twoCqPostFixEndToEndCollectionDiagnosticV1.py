#!/usr/bin/env python3
"""Outcome-blind post-fix 2CQ collection and canonical-repair diagnostic."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import struct
import time
from typing import Any

from exactCurveEconomicsV1 import (
    decode_trade_events,
    extract_pump_source_observations,
    resolved_account_keys,
)
from reconcilePumpExactEvidenceLedgerV1 import iter_frames
from twoCqCanonicalFinalizedSlotReconciliationV1 import load_env, rpc


SCHEMA = "2CQ_POST_FIX_END_TO_END_COLLECTION_DIAGNOSTIC_V1"
WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
COMPACT_MAGIC = b"AHCDV1\n\0"
COMPACT_RECORD = struct.Struct("<32sqQ")
MIN_TARGET = 25
FAST_TARGET = 50


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def append_jsonl(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


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


def signature_digest(signature: str) -> bytes:
    from exactCurveEconomicsV1 import base58_decode

    return hashlib.sha256(base58_decode(signature)).digest()


def compact_arrivals(
    path: pathlib.Path, target_digests: set[bytes]
) -> tuple[dict[bytes, tuple[int, int]], set[int]]:
    arrivals: dict[bytes, tuple[int, int]] = {}
    slots: set[int] = set()
    with path.open("rb") as stream:
        if stream.read(len(COMPACT_MAGIC)) != COMPACT_MAGIC:
            raise RuntimeError("COMPACT_ARRIVAL_MAGIC_MISMATCH")
        while True:
            raw = stream.read(COMPACT_RECORD.size)
            if not raw:
                break
            if len(raw) != COMPACT_RECORD.size:
                break  # The writer may be appending the final record.
            digest, received_ns, slot = COMPACT_RECORD.unpack(raw)
            slots.add(int(slot))
            if digest in target_digests:
                prior = arrivals.get(digest)
                candidate = (int(received_ns), int(slot))
                if prior is None or candidate < prior:
                    arrivals[digest] = candidate
    return arrivals, slots


def rpc_result(url: str, method: str, params: list[Any], request_id: int = 1) -> Any:
    response = rpc(url, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    if response.get("error"):
        raise RuntimeError(f"RPC_{method.upper()}:{response['error']}")
    return response.get("result")


def transaction(url: str, signature: str, cache: pathlib.Path) -> dict[str, Any] | None:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"tx-{signature}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8")).get("result")
        if cached is not None:
            return cached
    params = [signature, {
        "commitment": "finalized",
        "encoding": "json",
        "maxSupportedTransactionVersion": 0,
    }]
    result = None
    for attempt in range(3):
        result = rpc_result(url, "getTransaction", params, attempt + 1)
        if result is not None:
            break
        if attempt < 2:
            time.sleep(0.25 * (attempt + 1))
    atomic_json(path, {"signature": signature, "result": result})
    return result


def block_transaction_indexes(url: str, slot: int, cache: pathlib.Path) -> dict[str, int]:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"block-index-{slot}.json"
    if path.exists():
        return {str(key): int(value) for key, value in json.loads(path.read_text(encoding="utf-8")).items()}
    block = rpc_result(url, "getBlock", [slot, {
        "commitment": "finalized",
        "encoding": "json",
        "transactionDetails": "full",
        "rewards": False,
        "maxSupportedTransactionVersion": 0,
    }])
    if block is None:
        raise RuntimeError(f"FINALIZED_BLOCK_UNAVAILABLE:{slot}")
    result = {
        str(row["transaction"]["signatures"][0]): index
        for index, row in enumerate(block.get("transactions") or [])
    }
    atomic_json(path, result)
    return result


def older_signatures(url: str, mint: str, before: str, cache: pathlib.Path) -> tuple[list[dict[str, Any]], bool]:
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{mint}:{before}".encode()).hexdigest()
    path = cache / f"signatures-{key}.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if bool(payload["historyBoundaryReached"]):
            return payload["rows"], True
    rows: list[dict[str, Any]] = []
    cursor = before
    boundary = False
    page = 0
    while True:
        batch = rpc_result(url, "getSignaturesForAddress", [mint, {
            "commitment": "finalized", "before": cursor, "limit": 1000,
        }], page + 1) or []
        page += 1
        rows.extend(batch)
        if len(batch) < 1000:
            boundary = True
            break
        cursor = str(batch[-1]["signature"])
    atomic_json(path, {"mint": mint, "before": before, "rows": rows, "historyBoundaryReached": boundary})
    return rows, boundary


def wallet_token_amount(tx: dict[str, Any], side: str, mint: str) -> int | None:
    values = []
    for row in (tx.get("meta") or {}).get(side) or []:
        if str(row.get("mint") or "") != mint or str(row.get("owner") or "") != WALLET:
            continue
        amount = ((row.get("uiTokenAmount") or {}).get("amount"))
        if amount is not None:
            values.append(int(amount))
    return sum(values) if values else None


def is_successful_teacher_buy(tx: dict[str, Any], mint: str) -> bool:
    if (tx.get("meta") or {}).get("err") is not None:
        return False
    return any(event.mint == mint and event.user == WALLET and event.is_buy for event in decode_trade_events((tx.get("meta") or {}).get("logMessages") or []))


def is_first_entry(tx: dict[str, Any], mint: str) -> bool:
    if not is_successful_teacher_buy(tx, mint):
        return False
    pre = wallet_token_amount(tx, "preTokenBalances", mint)
    post = wallet_token_amount(tx, "postTokenBalances", mint)
    effective_pre = 0 if pre is None and post is not None else pre
    return effective_pre == 0 and post is not None and post > 0


def is_create(tx: dict[str, Any], mint: str) -> bool:
    if (tx.get("meta") or {}).get("err") is not None or mint not in resolved_account_keys(tx):
        return False
    logs = (tx.get("meta") or {}).get("logMessages") or []
    pump_invoked = any(PUMP_PROGRAM in line and "invoke" in line for line in logs)
    named = any("Instruction: Create" in line for line in logs)
    return pump_invoked and named


def raw_exact_membership(exact_root: pathlib.Path, signatures: set[str], hours: set[str]) -> set[str]:
    found: set[str] = set()
    for hour in sorted(hours):
        directory = exact_root / "partitions" / hour
        candidates = [directory / "evidence.frames.zst", directory / "evidence.frames.partial"]
        path = next((item for item in candidates if item.exists()), None)
        if path is None:
            continue
        for metadata, _ in iter_frames(path, path.suffix == ".partial"):
            signature = str(metadata.get("signature") or "")
            if signature in signatures and metadata.get("kind") == "transaction":
                found.add(signature)
            if found == signatures:
                return found
    return found


def chronological_hour(timestamp_ms: int) -> str:
    return dt.datetime.fromtimestamp(timestamp_ms / 1000, dt.timezone.utc).strftime("%Y/%m/%d/%H")


def evaluate_history(
    url: str,
    candidate: dict[str, Any],
    entry_tx: dict[str, Any],
    arrivals: dict[bytes, tuple[int, int]],
    cache: pathlib.Path,
    observer_started_ns: int,
    observer_reconnects: int,
) -> tuple[bool, list[str], dict[str, Any]]:
    reasons: list[str] = []
    mint = str(candidate["outputMint"])
    entry_signature = str(candidate["signature"])
    entry_arrival = arrivals.get(signature_digest(entry_signature))
    if entry_arrival is None:
        reasons.append("ENTRY_ALLENHARK_ARRIVAL_MISSING")

    rows, boundary = older_signatures(url, mint, entry_signature, cache)
    if not boundary:
        return False, ["PREENTRY_SIGNATURE_HISTORY_BOUNDARY_UNPROVEN"], {
            "entryAllenharkArrivalPresent": entry_arrival is not None,
            "historyBoundaryReached": False,
            "preEntrySignaturesAtSafetyCap": len(rows),
        }
    successful: list[tuple[int, int, str, dict[str, Any]]] = []
    create: tuple[str, dict[str, Any]] | None = None
    fetch_failures = 0
    for row in rows:
        signature = str(row.get("signature") or "")
        try:
            tx = transaction(url, signature, cache)
        except Exception:
            fetch_failures += 1
            continue
        if tx is None:
            fetch_failures += 1
            continue
        if (tx.get("meta") or {}).get("err") is not None:
            continue
        slot = int(tx.get("slot") or row.get("slot") or 0)
        if is_create(tx, mint):
            create = (signature, tx)
        try:
            events = [event for event in decode_trade_events((tx.get("meta") or {}).get("logMessages") or []) if event.mint == mint]
        except Exception:
            fetch_failures += 1
            continue
        try:
            sources = extract_pump_source_observations(tx, signature, mint, 0, "canonical_preentry_scan")
        except Exception:
            sources = []
            if events:
                fetch_failures += 1
        if events or sources:
            try:
                transaction_index = block_transaction_indexes(url, slot, cache)[signature]
            except Exception:
                fetch_failures += 1
                continue
            successful.append((slot, transaction_index, signature, tx))

    if fetch_failures:
        reasons.append("PREENTRY_FINALIZED_TRANSACTION_FETCH_INCOMPLETE")
    if create is None:
        reasons.append("CANONICAL_CREATE_NOT_IDENTIFIED")
    create_arrival = arrivals.get(signature_digest(create[0])) if create else None
    if create_arrival is None:
        reasons.append("CREATE_ALLENHARK_ARRIVAL_MISSING")

    entry_arrival_ns = entry_arrival[0] if entry_arrival else 0
    if observer_reconnects != 0 or observer_started_ns <= 0:
        reasons.append("ALLENHARK_CONTINUITY_NOT_PROVEN")
    if create_arrival and not (observer_started_ns <= create_arrival[0] < entry_arrival_ns):
        reasons.append("CREATE_TO_ENTRY_REALTIME_ORDER_INVALID")

    causal_events: list[tuple[int, int, int, Any, str, int]] = []
    missing_arrivals = 0
    decode_failures = 0
    for slot, transaction_index, signature, tx in successful:
        arrival = arrivals.get(signature_digest(signature))
        if arrival is None:
            missing_arrivals += 1
        try:
            events = [event for event in decode_trade_events((tx.get("meta") or {}).get("logMessages") or []) if event.mint == mint]
        except Exception:
            decode_failures += 1
            continue
        try:
            sources = extract_pump_source_observations(tx, signature, mint, arrival[0] // 1_000_000 if arrival else 0, "allenhark")
        except Exception:
            decode_failures += 1
            continue
        sources = sorted(sources, key=lambda item: item.instruction_index)
        if len(sources) != len(events):
            decode_failures += 1
            continue
        for event, source in zip(events, sources):
            causal_events.append((slot, transaction_index, int(source.instruction_index), event, signature, arrival[0] if arrival else 0))
    if missing_arrivals:
        reasons.append("PREENTRY_ALLENHARK_ARRIVAL_MISSING")
    if decode_failures:
        reasons.append("PREENTRY_EXACT_EVENT_RECONSTRUCTION_INCOMPLETE")
    try:
        entry_events = [
            event for event in decode_trade_events((entry_tx.get("meta") or {}).get("logMessages") or [])
            if event.mint == mint and event.user == WALLET and event.is_buy
        ]
    except Exception:
        entry_events = []
    try:
        entry_index = block_transaction_indexes(url, int(entry_tx.get("slot") or 0), cache)[entry_signature]
        entry_sources = extract_pump_source_observations(
            entry_tx, entry_signature, mint, entry_arrival_ns // 1_000_000 if entry_arrival_ns else 0, "allenhark"
        )
    except Exception:
        entry_sources = []
        entry_index = -1
    if len(entry_events) != 1 or len(entry_sources) != 1 or entry_index < 0:
        reasons.append("ENTRY_EXACT_EVENT_RECONSTRUCTION_INCOMPLETE")
    else:
        causal_events.append((
            int(entry_tx.get("slot") or 0), entry_index, int(entry_sources[0].instruction_index),
            entry_events[0], entry_signature, entry_arrival_ns,
        ))
    causal_events.sort(key=lambda item: (item[0], item[1], item[2], item[4]))
    for left, right in zip(causal_events, causal_events[1:]):
        if left[3].post_state != right[3].pre_state:
            reasons.append("PREENTRY_RESERVE_CONTINUITY_MISMATCH")
            break
    controls = [item for item in causal_events if item[3].user != WALLET and 0 < item[5] < entry_arrival_ns]
    if not controls:
        reasons.append("NO_VALID_EARLIER_SAME_MINT_CONTROL")

    detail = {
        "createSignature": create[0] if create else None,
        "createAllenharkArrivalPresent": create_arrival is not None,
        "entryAllenharkArrivalPresent": entry_arrival is not None,
        "preEntrySuccessfulPumpTransactions": len(successful),
        "exactEventsThroughEntry": len(causal_events),
        "validEarlierSameMintControls": len(controls),
        "historyBoundaryReached": boundary,
        "transactionFetchFailures": fetch_failures,
        "missingPriorAllenharkArrivals": missing_arrivals,
        "exactEventDecodeFailures": decode_failures,
    }
    return not reasons, sorted(set(reasons)), detail


def read_socket_drops() -> dict[str, int]:
    rows: dict[str, int] = {}
    path = pathlib.Path("/proc/net/udp")
    if not path.exists():
        return rows
    for line in path.read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) < 13 or fields[1].upper() != "00000000:4E20":
            continue
        rows[fields[9]] = int(fields[12])
    return rows


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    contract_path = args.output / "contract.json"
    if contract_path.exists():
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    else:
        contract = {
            "schema": SCHEMA + "_CONTRACT",
            "createdAtUtc": now_utc(),
            "sampleStartUtc": args.sample_start_utc,
            "sampleStartUnixMs": int(parse_utc(args.sample_start_utc).timestamp() * 1000),
            "minimumCanonicalFirstEntries": MIN_TARGET,
            "fastAccumulationTarget": FAST_TARGET,
            "officialN500Cohort": False,
            "outcomesPermitted": False,
            "submissionsPermitted": False,
        }
        atomic_json(contract_path, contract)

    detector_rows = []
    seen: set[str] = set()
    for row in read_jsonl(args.detector):
        signature = str(row.get("signature") or "")
        if int(row.get("detectedAtMs") or 0) < int(contract["sampleStartUnixMs"]) or not signature or signature in seen:
            continue
        if row.get("wallet") != WALLET or row.get("kind") != "swap" or row.get("isNativeInput") is not True:
            continue
        seen.add(signature)
        detector_rows.append(row)
    detector_rows.sort(key=lambda row: (int(row["detectedAtMs"]), str(row["signature"])))

    env = load_env(args.env)
    url = env["SECURE_RPC_URL"]
    candidate_results: list[dict[str, Any]] = []
    first_entries: list[tuple[dict[str, Any], dict[str, Any]]] = []
    canonical_successful_pump_entries = 0
    finalized_failed = 0
    unresolved = 0
    for candidate in detector_rows:
        signature = str(candidate["signature"])
        mint = str(candidate["outputMint"])
        try:
            tx = transaction(url, signature, args.output / "rpc-cache")
        except Exception as error:
            candidate_results.append({"signature": signature, "mint": mint, "status": "UNRESOLVED", "error": str(error)})
            unresolved += 1
            continue
        if tx is None:
            candidate_results.append({"signature": signature, "mint": mint, "status": "UNRESOLVED"})
            unresolved += 1
            continue
        if (tx.get("meta") or {}).get("err") is not None:
            candidate_results.append({"signature": signature, "mint": mint, "status": "FINALIZED_FAILED"})
            finalized_failed += 1
            continue
        if is_successful_teacher_buy(tx, mint):
            canonical_successful_pump_entries += 1
            if is_first_entry(tx, mint):
                candidate_results.append({
                    "signature": signature, "mint": mint,
                    "status": "CANONICAL_SUCCESSFUL_FIRST_ENTRY",
                })
                first_entries.append((candidate, tx))
            else:
                candidate_results.append({
                    "signature": signature, "mint": mint,
                    "status": "FINALIZED_SUCCESS_NON_FIRST_ENTRY",
                })
        else:
            candidate_results.append({"signature": signature, "mint": mint, "status": "FINALIZED_SUCCESS_NOT_PUMP_BUY"})

    reached = len(first_entries) >= MIN_TARGET
    target = FAST_TARGET if len(first_entries) >= FAST_TARGET else MIN_TARGET
    selected = first_entries[:target] if reached else first_entries
    required_arrival_signatures = {str(candidate["signature"]) for candidate, _ in first_entries}
    if reached:
        for candidate, _ in selected:
            rows, boundary = older_signatures(
                url, str(candidate["outputMint"]), str(candidate["signature"]),
                args.output / "rpc-cache",
            )
            if boundary:
                required_arrival_signatures.update(str(row.get("signature") or "") for row in rows)
    required_arrival_digests = {
        signature_digest(signature) for signature in required_arrival_signatures if signature
    }
    if reached:
        arrivals, observed_slots = compact_arrivals(args.compact_arrivals, required_arrival_digests)
    else:
        arrivals, observed_slots = {}, set()
    observer_health = json.loads(args.observer_health.read_text(encoding="utf-8"))
    observer_started_ns = int(parse_utc(str(observer_health["startedAtUtc"])).timestamp() * 1_000_000_000)

    signatures = {str(candidate["signature"]) for candidate, _ in selected}
    hours = {chronological_hour(int(candidate["detectedAtMs"])) for candidate, _ in selected}
    direct_exact = raw_exact_membership(args.exact_root, signatures, hours) if reached else set()
    classifications: list[dict[str, Any]] = []
    fully_eligible = 0
    exclusions: collections.Counter[str] = collections.Counter()
    for candidate, tx in selected:
        signature = str(candidate["signature"])
        source_class = "DIRECT_REALTIME_EXACT" if signature in direct_exact else "FINALIZED_RECONCILED"
        eligible, reasons, detail = evaluate_history(
            url, candidate, tx, arrivals, args.output / "rpc-cache",
            observer_started_ns, int(observer_health.get("reconnects") or 0),
        ) if reached else (False, ["TARGET_NOT_YET_REACHED"], {})
        if eligible:
            fully_eligible += 1
        else:
            exclusions.update(reasons)
        classifications.append({
            "schema": SCHEMA + "_FIRST_ENTRY",
            "signature": signature,
            "mint": candidate["outputMint"],
            "detectedAtMs": candidate["detectedAtMs"],
            "slot": int(tx.get("slot") or candidate.get("sourceSlot") or 0),
            "finalizedSuccess": True,
            "knownFlatBeforeEntry": True,
            "canonicalSourceClass": source_class,
            "fullyEligible": eligible,
            "exclusionReasons": reasons,
            "detail": detail,
        })

    elapsed_hours = max((dt.datetime.now(dt.timezone.utc) - parse_utc(str(contract["sampleStartUtc"]))).total_seconds() / 3600, 1 / 3600)
    first_rate = len(first_entries) / elapsed_hours
    eligible_rate = fully_eligible / elapsed_hours if reached else 0.0
    socket_drops = read_socket_drops()
    report = {
        "schema": SCHEMA + "_REPORT",
        "implementationSha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "generatedAtUtc": now_utc(),
        "sampleStartUtc": contract["sampleStartUtc"],
        "elapsedHours": elapsed_hours,
        "targetReached": reached,
        "targetN": target,
        "raw2CqActivity": len(detector_rows),
        "canonicalSuccessfulPumpEntries": canonical_successful_pump_entries,
        "finalizedSuccessfulFirstEntries": len(first_entries),
        "finalizedFailedEntries": finalized_failed,
        "unresolvedCandidates": unresolved,
        "allenharkEntryCoverageN": sum(signature_digest(str(candidate["signature"])) in arrivals for candidate, _ in first_entries) if reached else None,
        "allenharkEntryCoveragePct": 100.0 * sum(signature_digest(str(candidate["signature"])) in arrivals for candidate, _ in first_entries) / len(first_entries) if reached and first_entries else None,
        "allenharkEntryCoveragePendingTarget": not reached,
        "directRealtimeExactN": sum(row["canonicalSourceClass"] == "DIRECT_REALTIME_EXACT" for row in classifications),
        "finalizedReconciledN": sum(row["canonicalSourceClass"] == "FINALIZED_RECONCILED" for row in classifications),
        "canonicalUnresolvedN": unresolved,
        "fullyEligibleN": fully_eligible,
        "fullyEligiblePct": 100.0 * fully_eligible / len(classifications) if classifications else 0.0,
        "exclusionsByReason": dict(sorted(exclusions.items())),
        "ratesPerHour": {
            "raw2CqEntries": len(detector_rows) / elapsed_hours,
            "canonicalFirstEntries": first_rate,
            "fullyEligible": eligible_rate,
        },
        "estimatedHours": {
            "n300": 300 / eligible_rate if eligible_rate else None,
            "n500": 500 / eligible_rate if eligible_rate else None,
        },
        "socketDrops": socket_drops,
        "socketDropTotal": sum(socket_drops.values()),
        "observer": {
            "connected": observer_health.get("connected"),
            "reconnects": observer_health.get("reconnects"),
            "decodeFailures": observer_health.get("decodeFailures"),
            "firstSlot": observer_health.get("firstSlot"),
            "lastSlot": observer_health.get("lastSlot"),
            "observedSlotN": len(observed_slots),
        },
        "canonicalReconciliationCoveragePct": 100.0 * sum(
            row["canonicalSourceClass"] in {"DIRECT_REALTIME_EXACT", "FINALIZED_RECONCILED"}
            for row in classifications
        ) / len(classifications) if classifications else 0.0,
        "candidateResults": candidate_results,
        "firstEntries": classifications,
        "outcomesRead": 0,
        "submissions": 0,
        "complete": reached,
    }
    atomic_json(args.output / "report.json", report)
    atomic_json(args.output / "status.json", {
        key: report[key] for key in (
            "generatedAtUtc", "sampleStartUtc", "elapsedHours", "targetReached", "targetN",
            "raw2CqActivity", "finalizedSuccessfulFirstEntries", "fullyEligibleN",
            "socketDropTotal", "outcomesRead", "submissions", "complete",
        )
    })
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detector", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--observer-health", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--sample-start-utc", required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
