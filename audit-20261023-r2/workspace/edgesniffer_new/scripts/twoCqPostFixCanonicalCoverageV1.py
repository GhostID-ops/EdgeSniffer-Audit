#!/usr/bin/env python3
"""Bounded post-fix finalized Pump coverage census for the AllenHark path."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import struct
from typing import Any

from twoCqAllenharkParityV1 import atomic_json, iso, now, sha256
from twoCqCanonicalFinalizedSlotReconciliationV1 import (
    classify_pump,
    exact_evidence,
    fetch_blocks,
    load_env,
    rpc,
)


SCHEMA = "2CQ_POST_FIX_CANONICAL_COVERAGE_V1"
COMPACT_MAGIC = b"AHCDV1\n\0"
COMPACT_RECORD = struct.Struct("<32sqQ")


def compact_arrivals(
    path: pathlib.Path, start_slot: int, end_slot: int
) -> tuple[dict[bytes, dict[str, int]], set[int]]:
    arrivals: dict[bytes, dict[str, int]] = {}
    slots: set[int] = set()
    with path.open("rb") as stream:
        if stream.read(len(COMPACT_MAGIC)) != COMPACT_MAGIC:
            raise RuntimeError("COMPACT_ARRIVAL_MAGIC_MISMATCH")
        while True:
            raw = stream.read(COMPACT_RECORD.size)
            if not raw:
                break
            if len(raw) != COMPACT_RECORD.size:
                raise RuntimeError("COMPACT_ARRIVAL_TRUNCATED_RECORD")
            digest, received_ns, slot = COMPACT_RECORD.unpack(raw)
            if slot < start_slot or slot > end_slot:
                continue
            slots.add(slot)
            previous = arrivals.get(digest)
            if previous is None or received_ns < previous["receivedAtUnixNs"]:
                arrivals[digest] = {"receivedAtUnixNs": received_ns, "slot": slot}
    return arrivals, slots


def signature_digest(signature: str) -> bytes:
    from exactCurveEconomicsV1 import base58_decode

    return hashlib.sha256(base58_decode(signature)).digest()


def pct(numerator: int, denominator: int) -> float:
    return 100.0 * numerator / denominator if denominator else 0.0


def fetch_blocks_with_single_fallback(
    url: str, start_slot: int, end_slot: int, batch_size: int
) -> tuple[list[dict[str, Any]], list[int]]:
    try:
        return fetch_blocks(url, start_slot, end_slot, batch_size)
    except Exception:
        canonical: list[dict[str, Any]] = []
        skipped: list[int] = []
        for slot in range(start_slot, end_slot + 1):
            response = rpc(url, {
                "jsonrpc": "2.0",
                "id": slot,
                "method": "getBlock",
                "params": [slot, {
                    "commitment": "finalized",
                    "encoding": "json",
                    "transactionDetails": "full",
                    "rewards": False,
                    "maxSupportedTransactionVersion": 0,
                }],
            })
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
                canonical.append({
                    "signature": str(transaction["transaction"]["signatures"][0]),
                    "slot": slot,
                    "transactionIndex": transaction_index,
                    "blockTime": block.get("blockTime"),
                    "kinds": kinds,
                    "coordinates": coordinates,
                    "transaction": transaction,
                })
        return canonical, skipped


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    contract_path = args.output / "contract.json"
    if contract_path.exists():
        raise RuntimeError("POST_FIX_CENSUS_CONTRACT_ALREADY_EXISTS")
    if args.end_slot < args.start_slot:
        raise RuntimeError("INVALID_SLOT_RANGE")

    health = json.loads(args.observer_health.read_text(encoding="utf-8"))
    contract = {
        "schema": SCHEMA + "_CONTRACT",
        "createdAtUtc": iso(now()),
        "startSlot": args.start_slot,
        "endSlot": args.end_slot,
        "slotCount": args.end_slot - args.start_slot + 1,
        "canonicalAuthority": "independent getBlock(commitment=finalized) enumeration",
        "allenharkAuthority": "compact capture-all SubscribeEntries signature journal",
        "observerProcessInstance": health.get("processInstance"),
        "observerStartedAtUtc": health.get("startedAtUtc"),
        "proxyDropsBeforeWindow": args.drops_start,
        "proxyDropsAfterWindow": args.drops_end,
        "healthGate": {"allenharkCoveragePctMin": 99.9, "canonicalAfterRepairPct": 100.0},
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(contract_path, contract)
    atomic_json(args.output / "freeze.json", {
        "schema": SCHEMA + "_FREEZE",
        "createdAtUtc": contract["createdAtUtc"],
        "contractSha256": sha256(contract_path),
    })

    env = load_env(args.env)
    canonical, skipped = fetch_blocks_with_single_fallback(
        env["SECURE_RPC_URL"], args.start_slot, args.end_slot, args.batch_size
    )
    canonical_by_signature = {row["signature"]: row for row in canonical}
    compact, compact_slots = compact_arrivals(
        args.compact_arrivals, args.start_slot, args.end_slot
    )
    raw, finalized, evidence_paths = exact_evidence(args.exact_root, args.start_slot, args.end_slot)

    rows = []
    counts: collections.Counter[str] = collections.Counter()
    for signature, row in sorted(
        canonical_by_signature.items(), key=lambda item: (item[1]["slot"], item[1]["transactionIndex"])
    ):
        allen = signature_digest(signature) in compact
        rabbit = signature in raw
        bucket = "BOTH" if allen and rabbit else "ALLENHARK_ONLY" if allen else "RABBIT_ONLY" if rabbit else "NEITHER"
        counts[bucket] += 1
        rows.append({
            "signature": signature,
            "slot": row["slot"],
            "transactionIndex": row["transactionIndex"],
            "kinds": row["kinds"],
            "allenharkArrival": allen,
            "rabbitArrival": rabbit,
            "sourceBucket": bucket,
            "exactLedgerFinalized": signature in finalized,
            "canonicalRepairAdded": signature not in finalized,
        })

    with (args.output / "canonical-population-audit.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    repair_rows = []
    for signature, row in canonical_by_signature.items():
        if signature in finalized:
            continue
        payload = json.dumps(row["transaction"], sort_keys=True, separators=(",", ":")).encode()
        repair_rows.append({
            "schema": SCHEMA + "_CANONICAL_REPAIR",
            "signature": signature,
            "slot": row["slot"],
            "transactionIndex": row["transactionIndex"],
            "kinds": row["kinds"],
            "source": "independent finalized getBlock",
            "rawTransactionSha256": hashlib.sha256(payload).hexdigest(),
            "transaction": row["transaction"],
        })
    with (args.output / "canonical-repair-evidence.jsonl").open("w", encoding="utf-8") as stream:
        for row in repair_rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    canonical_slots = {int(row["slot"]) for row in canonical}
    slots_with_allenhark_pump = {int(row["slot"]) for row in rows if row["allenharkArrival"]}
    entire_slot_misses = sorted(canonical_slots - compact_slots)
    pump_slot_misses = sorted(canonical_slots - slots_with_allenhark_pump)
    n = len(rows)
    allen_n = counts["BOTH"] + counts["ALLENHARK_ONLY"]
    rabbit_n = counts["BOTH"] + counts["RABBIT_ONLY"]
    union_n = n - counts["NEITHER"]
    allen_pct = pct(allen_n, n)
    union_pct = pct(union_n, n)
    exact_before_n = len(set(canonical_by_signature) & set(finalized))
    exact_after_pct = pct(exact_before_n + len(repair_rows), n)
    drop_delta = max(0, args.drops_end - args.drops_start)
    passed = (
        n > 0
        and allen_pct >= 99.9
        and exact_after_pct == 100.0
        and drop_delta == 0
        and not entire_slot_misses
    )
    kinds = collections.Counter(kind for row in canonical for kind in row["kinds"])
    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": iso(now()),
        "contractSha256": sha256(contract_path),
        "slotRange": {
            "start": args.start_slot,
            "end": args.end_slot,
            "slots": args.end_slot - args.start_slot + 1,
            "finalizedNoBlockOrSkippedSlots": skipped,
        },
        "canonicalSuccessfulPumpN": n,
        "canonicalKinds": dict(sorted(kinds.items())),
        "sourceMatrix": {name: int(counts[name]) for name in ("BOTH", "ALLENHARK_ONLY", "RABBIT_ONLY", "NEITHER")},
        "allenharkPathN": allen_n,
        "allenharkPathCoveragePct": allen_pct,
        "rabbitN": rabbit_n,
        "rabbitCoveragePct": pct(rabbit_n, n),
        "unionN": union_n,
        "unionCoveragePct": union_pct,
        "neitherN": int(counts["NEITHER"]),
        "transactionLevelAllenharkMisses": n - allen_n,
        "entireSlotMisses": entire_slot_misses,
        "canonicalPumpSlotMisses": pump_slot_misses,
        "observer": {
            "processInstance": health.get("processInstance"),
            "connected": health.get("connected"),
            "reconnects": health.get("reconnects"),
            "decodeFailures": health.get("decodeFailures"),
            "compactRecords": health.get("compactRecordsWritten"),
            "firstSlot": health.get("firstSlot"),
            "lastSlot": health.get("lastSlot"),
        },
        "kernelUdpDrops": {"before": args.drops_start, "after": args.drops_end, "delta": drop_delta},
        "exactLedger": {
            "coveredBeforeRepairN": exact_before_n,
            "repairsWrittenN": len(repair_rows),
            "coverageAfterReconciliationPct": exact_after_pct,
            "evidencePaths": [str(path) for path in evidence_paths],
        },
        "gatePassed": passed,
        "outcomesRead": 0,
        "submissions": 0,
    }
    atomic_json(args.output / "report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--observer-health", type=pathlib.Path, required=True)
    parser.add_argument("--start-slot", type=int, required=True)
    parser.add_argument("--end-slot", type=int, required=True)
    parser.add_argument("--drops-start", type=int, required=True)
    parser.add_argument("--drops-end", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
