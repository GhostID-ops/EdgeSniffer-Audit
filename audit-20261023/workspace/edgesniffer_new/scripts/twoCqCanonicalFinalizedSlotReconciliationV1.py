#!/usr/bin/env python3
"""Independent finalized-slot Pump census and arrival-ledger reconciliation."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import time
import urllib.request
from typing import Any

from exactCurveEconomicsV1 import ExactCurveError, base58_decode, decode_pump_instruction, resolved_account_keys
from reconcilePumpExactEvidenceLedgerV1 import iter_frames
from twoCqAllenharkArrivalClassifierV1 import MAGIC, RECORD, target_arrivals
from twoCqAllenharkFinalizedParityV1 import quantile
from twoCqAllenharkParityV1 import atomic_json, iso, now, partition_paths, sha256


SCHEMA = "2CQ_CANONICAL_FINALIZED_SLOT_RECONCILIATION_V1"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
BUY_KINDS = {"buy", "buy_exact_sol_in", "buy_v2", "buy_exact_quote_in_v2"}
SELL_KINDS = {"sell", "sell_v2"}
CREATE_KINDS = {"create", "create_v2"}


def load_env(path: pathlib.Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def rpc(url: str, payload: Any, attempts: int = 5) -> Any:
    body = json.dumps(payload, separators=(",", ":")).encode()
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
            with urllib.request.urlopen(request, timeout=90) as response:
                value = json.loads(response.read())
            return value
        except Exception:
            if attempt + 1 == attempts:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    contract_path = args.output / "contract.json"
    if contract_path.exists():
        raise RuntimeError("CANONICAL_CENSUS_CONTRACT_ALREADY_EXISTS")
    env = load_env(args.env)
    url = env["SECURE_RPC_URL"]
    response = rpc(url, {"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": [{"commitment": "finalized"}]})
    finalized_tip = int(response["result"])
    end_slot = finalized_tip - args.tip_distance
    start_slot = end_slot - args.slot_count + 1
    health = json.loads(args.allenhark_health.read_text(encoding="utf-8"))
    contract = {
        "schema": SCHEMA + "_CONTRACT", "createdAtUtc": iso(now()),
        "startSlot": start_slot, "endSlot": end_slot, "slotCount": args.slot_count,
        "finalizedTipAtFreeze": finalized_tip, "tipDistance": args.tip_distance,
        "canonicalAuthority": "independent getBlock(commitment=finalized) enumeration",
        "rpcEndpointIdentitySha256": hashlib.sha256(url.encode()).hexdigest(),
        "allenharkProcessInstance": health.get("processInstance"),
        "allenharkReconnectsAtStart": int(health.get("reconnects") or 0),
        "allenharkDecodeFailuresAtStart": int(health.get("decodeFailures") or 0),
        "healthGate": {
            "canonicalExactCoverageAfterRepairPct": 100.0,
            "allenharkRealtimeCoveragePctMin": 99.9,
            "unexplainedClusteredOutagePermitted": False,
            "silentContinuityGapPermitted": False,
        },
        "outcomesRead": 0, "submissions": 0,
    }
    atomic_json(contract_path, contract)
    atomic_json(args.output / "freeze.json", {
        "schema": SCHEMA + "_FREEZE", "createdAtUtc": contract["createdAtUtc"],
        "contractSha256": sha256(contract_path),
    })
    return contract


def account_keys(transaction: dict[str, Any]) -> list[str]:
    message = transaction["transaction"]["message"]
    keys = [str(row.get("pubkey")) if isinstance(row, dict) else str(row) for row in message.get("accountKeys") or []]
    loaded = (transaction.get("meta") or {}).get("loadedAddresses") or {}
    keys.extend(str(value) for value in loaded.get("writable") or [])
    keys.extend(str(value) for value in loaded.get("readonly") or [])
    return keys


def classify_pump(transaction: dict[str, Any]) -> tuple[list[str], list[str]]:
    keys = account_keys(transaction)
    message = transaction["transaction"]["message"]
    compiled: list[tuple[str, dict[str, Any]]] = []
    for index, instruction in enumerate(message.get("instructions") or []):
        compiled.append((f"outer:{index}", instruction))
    for group in (transaction.get("meta") or {}).get("innerInstructions") or []:
        for index, instruction in enumerate(group.get("instructions") or []):
            compiled.append((f"outer:{group.get('index')}:inner:{index}", instruction))
    kinds: list[str] = []
    coordinates: list[str] = []
    for coordinate, instruction in compiled:
        program_index = instruction.get("programIdIndex")
        if program_index is None or int(program_index) >= len(keys) or keys[int(program_index)] != PUMP_PROGRAM:
            continue
        encoded = instruction.get("data")
        if not encoded:
            continue
        try:
            decoded = decode_pump_instruction(base58_decode(encoded))
            kind = decoded.kind
        except ExactCurveError:
            kind = "other_pump_instruction"
        kinds.append(kind)
        coordinates.append(coordinate)
    return kinds, coordinates


def fetch_blocks(url: str, start_slot: int, end_slot: int, batch_size: int) -> tuple[list[dict[str, Any]], list[int]]:
    canonical: list[dict[str, Any]] = []
    skipped: list[int] = []
    request_id = 0
    for first in range(start_slot, end_slot + 1, batch_size):
        slots = list(range(first, min(end_slot + 1, first + batch_size)))
        payload = []
        by_id = {}
        for slot in slots:
            request_id += 1
            by_id[request_id] = slot
            payload.append({
                "jsonrpc": "2.0", "id": request_id, "method": "getBlock",
                "params": [slot, {"commitment": "finalized", "encoding": "json", "transactionDetails": "full", "rewards": False, "maxSupportedTransactionVersion": 0}],
            })
        responses = rpc(url, payload)
        for response in responses:
            slot = by_id[int(response["id"])]
            block = response.get("result")
            if block is None:
                skipped.append(slot)
                continue
            for transaction_index, transaction in enumerate(block.get("transactions") or []):
                if (transaction.get("meta") or {}).get("err") is not None:
                    continue
                kinds, coordinates = classify_pump(transaction)
                if not kinds:
                    continue
                signature = str(transaction["transaction"]["signatures"][0])
                canonical.append({
                    "signature": signature, "slot": slot, "transactionIndex": transaction_index,
                    "blockTime": block.get("blockTime"), "kinds": kinds, "coordinates": coordinates,
                    "hasTopLevelPump": any(value.startswith("outer:") and ":inner:" not in value for value in coordinates),
                    "hasCpiPump": any(":inner:" in value for value in coordinates),
                    "usedLoadedAddresses": bool(((transaction.get("meta") or {}).get("loadedAddresses") or {}).get("writable") or ((transaction.get("meta") or {}).get("loadedAddresses") or {}).get("readonly")),
                    "transaction": transaction,
                })
    return canonical, skipped


def exact_evidence(
    exact_root: pathlib.Path, start_slot: int, end_slot: int
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[pathlib.Path]]:
    utc_now = now()
    paths = partition_paths(exact_root, utc_now - dt.timedelta(hours=2), utc_now)
    raw: dict[str, dict[str, Any]] = {}
    finalized: dict[str, dict[str, Any]] = {}
    for path in paths:
        for metadata, _ in iter_frames(path, path.suffix == ".partial"):
            slot = int(metadata.get("slot") or 0)
            signature = str(metadata.get("signature") or "")
            if not signature or not (start_slot <= slot <= end_slot):
                continue
            if metadata.get("kind") == "transaction":
                raw.setdefault(signature, metadata)
            elif metadata.get("kind") == "rpc_finalized_transaction" and metadata.get("finalized") is True:
                finalized[signature] = metadata
    return raw, finalized, paths


def allenhark_observed_slots(binary_root: pathlib.Path, start_slot: int, end_slot: int) -> set[int]:
    slots: set[int] = set()
    for path in sorted(binary_root.glob("*/*/*/*/arrivals.bin.partial")):
        with path.open("rb") as stream:
            if stream.read(len(MAGIC)) != MAGIC:
                raise RuntimeError(f"ALLENHARK_BINARY_MAGIC_MISMATCH:{path}")
            while True:
                raw = stream.read(RECORD.size)
                if not raw:
                    break
                if len(raw) != RECORD.size:
                    break
                slot = int(RECORD.unpack(raw)[2])
                if start_slot <= slot <= end_slot:
                    slots.add(slot)
    return slots


def miss_clusters(rows: list[dict[str, Any]]) -> list[dict[str, int]]:
    slots = sorted(set(int(row["slot"]) for row in rows))
    if not slots:
        return []
    groups: list[list[int]] = [[slots[0]]]
    for slot in slots[1:]:
        if slot == groups[-1][-1] + 1:
            groups[-1].append(slot)
        else:
            groups.append([slot])
    return [{"startSlot": group[0], "endSlot": group[-1], "slotCount": len(group)} for group in groups]


def run(args: argparse.Namespace) -> dict[str, Any]:
    contract_path = args.output / "contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    frozen = json.loads((args.output / "freeze.json").read_text(encoding="utf-8"))
    if sha256(contract_path) != frozen["contractSha256"]:
        raise RuntimeError("CANONICAL_CENSUS_CONTRACT_CHANGED_FAIL_CLOSED")
    env = load_env(args.env)
    canonical, skipped = fetch_blocks(env["SECURE_RPC_URL"], int(contract["startSlot"]), int(contract["endSlot"]), args.batch_size)
    canonical_by_sig = {row["signature"]: row for row in canonical}
    raw, exact_finalized, evidence_paths = exact_evidence(args.exact_root, int(contract["startSlot"]), int(contract["endSlot"]))
    allen = target_arrivals(args.all_transaction_root, set(canonical_by_sig), 0)
    allen_slots = allenhark_observed_slots(args.all_transaction_root, int(contract["startSlot"]), int(contract["endSlot"]))

    exact_missing = sorted(set(canonical_by_sig) - set(exact_finalized))
    exact_missing_with_raw = sorted(set(exact_missing) & set(raw))
    exact_missing_without_raw = sorted(set(exact_missing) - set(raw))
    repair_rows = []
    for signature in exact_missing:
        row = canonical_by_sig[signature]
        payload = json.dumps(row["transaction"], sort_keys=True, separators=(",", ":")).encode()
        repair_rows.append({
            "schema": SCHEMA + "_CANONICAL_REPAIR", "signature": signature,
            "slot": row["slot"], "transactionIndex": row["transactionIndex"],
            "kinds": row["kinds"], "coordinates": row["coordinates"],
            "source": "independent finalized getBlock", "rawTransactionSha256": hashlib.sha256(payload).hexdigest(),
            "transaction": row["transaction"],
        })
    with (args.output / "canonical-repair-evidence.jsonl").open("w", encoding="utf-8") as stream:
        for row in repair_rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    audit_rows = []
    allen_misses = []
    rabbit_misses = []
    allen_miss_reasons: collections.Counter[str] = collections.Counter()
    for signature, row in sorted(canonical_by_sig.items(), key=lambda item: (item[1]["slot"], item[1]["transactionIndex"])):
        has_allen = signature in allen
        has_rabbit = signature in raw
        if not has_allen:
            allen_misses.append(row)
            allen_miss_reasons[
                "ACTUAL_ALLENHARK_TRANSACTION_DELIVERY_GAP"
                if row["slot"] in allen_slots
                else "ACTUAL_ALLENHARK_SLOT_DELIVERY_GAP"
            ] += 1
        if not has_rabbit:
            rabbit_misses.append(row)
        audit_rows.append({
            "signature": signature, "slot": row["slot"], "transactionIndex": row["transactionIndex"],
            "kinds": row["kinds"], "topLevel": row["hasTopLevelPump"], "cpi": row["hasCpiPump"],
            "loadedAddresses": row["usedLoadedAddresses"], "exactLedgerFinalized": signature in exact_finalized,
            "canonicalRepairAdded": signature in exact_missing, "allenharkArrival": has_allen,
            "rabbitArrival": has_rabbit,
            "allenharkMissReason": None if has_allen else (
                "ACTUAL_ALLENHARK_TRANSACTION_DELIVERY_GAP"
                if row["slot"] in allen_slots
                else "ACTUAL_ALLENHARK_SLOT_DELIVERY_GAP"
            ),
        })
    with (args.output / "canonical-population-audit.jsonl").open("w", encoding="utf-8") as stream:
        for row in audit_rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    health = json.loads(args.allenhark_health.read_text(encoding="utf-8"))
    continuity = []
    if args.continuity.exists():
        for line in args.continuity.read_text(encoding="utf-8").splitlines():
            if line.strip():
                continuity.append(json.loads(line))
    process_changed = health.get("processInstance") != contract.get("allenharkProcessInstance")
    reconnect_delta = int(health.get("reconnects") or 0) - int(contract["allenharkReconnectsAtStart"])
    decode_delta = int(health.get("decodeFailures") or 0) - int(contract["allenharkDecodeFailuresAtStart"])
    disconnects = [row for row in continuity if str(row.get("event") or "").startswith("DISCONNECTED") and row.get("processInstance") == contract.get("allenharkProcessInstance")]
    n = len(canonical)
    exact_before = 100.0 * (n - len(exact_missing)) / n if n else 0.0
    exact_after = 100.0 if n and len(repair_rows) == len(exact_missing) else exact_before
    allen_coverage = 100.0 * (n - len(allen_misses)) / n if n else 0.0
    rabbit_coverage = 100.0 * (n - len(rabbit_misses)) / n if n else 0.0
    dual = sorted(set(canonical_by_sig) & set(allen) & set(raw))
    deltas = [
        (int(allen[signature]["receivedAtUnixNs"]) - int(raw[signature]["receivedAtUnixNs"])) / 1_000_000
        for signature in dual
    ]
    allen_first = sum(value < 0 for value in deltas)
    rabbit_first = sum(value > 0 for value in deltas)
    ties = len(deltas) - allen_first - rabbit_first
    comparable = disagreements = 0
    ordered = sorted(dual, key=lambda signature: (allen[signature]["receivedAtUnixNs"], signature))
    for left, right in zip(ordered, ordered[1:]):
        allen_left, allen_right = allen[left]["receivedAtUnixNs"], allen[right]["receivedAtUnixNs"]
        rabbit_left = int(raw[left]["receivedAtUnixNs"])
        rabbit_right = int(raw[right]["receivedAtUnixNs"])
        if allen_left == allen_right or rabbit_left == rabbit_right:
            continue
        comparable += 1
        if (allen_left < allen_right) != (rabbit_left < rabbit_right):
            disagreements += 1
    clusters = miss_clusters(allen_misses)
    gate = contract["healthGate"]
    passed = (
        n > 0 and exact_after == gate["canonicalExactCoverageAfterRepairPct"]
        and allen_coverage >= gate["allenharkRealtimeCoveragePctMin"]
        and not process_changed and reconnect_delta == 0 and decode_delta == 0
        and not disconnects and not allen_misses
    )
    kinds = collections.Counter(kind for row in canonical for kind in row["kinds"])
    report = {
        "schema": SCHEMA + "_REPORT", "generatedAtUtc": iso(now()),
        "contractSha256": frozen["contractSha256"],
        "slotRange": {"start": contract["startSlot"], "end": contract["endSlot"], "requested": contract["slotCount"], "skippedSlots": skipped},
        "canonicalFinalizedSuccessfulPumpN": n,
        "canonicalKinds": dict(sorted(kinds.items())),
        "topLevelN": sum(row["hasTopLevelPump"] for row in canonical),
        "cpiN": sum(row["hasCpiPump"] for row in canonical),
        "loadedAddressN": sum(row["usedLoadedAddresses"] for row in canonical),
        "exactLedger": {"coveredBeforeRepairN": n - len(exact_missing), "missingBeforeRepairN": len(exact_missing), "missingWithRabbitRawN": len(exact_missing_with_raw), "missingWithoutRabbitRawN": len(exact_missing_without_raw), "coverageBeforePct": exact_before, "repairsWrittenN": len(repair_rows), "coverageAfterPct": exact_after},
        "allenhark": {"coveredN": n - len(allen_misses), "missingN": len(allen_misses), "coveragePct": allen_coverage, "missesByReason": dict(sorted(allen_miss_reasons.items())), "observedCanonicalRangeSlots": len(allen_slots), "missClusters": clusters, "processChanged": process_changed, "reconnectDelta": reconnect_delta, "decodeFailureDelta": decode_delta, "continuityDisconnectRecords": len(disconnects)},
        "rabbit": {"coveredN": n - len(rabbit_misses), "missingN": len(rabbit_misses), "coveragePct": rabbit_coverage},
        "dualArrivalTiming": {
            "matchedN": len(dual),
            "deltaAllenMinusRabbitMs": {
                "p10": quantile(deltas, .10), "p50": quantile(deltas, .50),
                "p75": quantile(deltas, .75), "p90": quantile(deltas, .90),
                "p99": quantile(deltas, .99), "min": min(deltas) if deltas else None,
                "max": max(deltas) if deltas else None,
            },
            "allenharkFirstN": allen_first, "rabbitFirstN": rabbit_first, "tiesN": ties,
            "comparableAdjacentPairs": comparable, "orderingDisagreements": disagreements,
            "orderingAgreementPct": 100.0 * (comparable - disagreements) / comparable if comparable else None,
        },
        "evidencePaths": [str(path) for path in evidence_paths],
        "canonicalCoverageGatePassed": passed,
        "outcomesRead": 0, "submissions": 0,
    }
    atomic_json(args.output / "report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--all-transaction-root", type=pathlib.Path, required=True)
    parser.add_argument("--allenhark-health", type=pathlib.Path, required=True)
    parser.add_argument("--continuity", type=pathlib.Path, required=True)
    parser.add_argument("--slot-count", type=int, default=128)
    parser.add_argument("--tip-distance", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    result = freeze(args) if args.freeze else run(args)
    print(json.dumps(result, indent=2, sort_keys=True)) if args.freeze else None


if __name__ == "__main__":
    main()
