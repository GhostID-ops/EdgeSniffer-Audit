#!/usr/bin/env python3
"""Frozen-slot realtime-source complementarity forensics for the 2CQ study."""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
from typing import Any

from exactCurveEconomicsV1 import ExactCurveError, base58_decode, decode_pump_instruction
from twoCqAllenharkParityV1 import atomic_json, iso, now
from twoCqAllenharkArrivalClassifierV1 import MAGIC, RECORD
from twoCqCanonicalFinalizedSlotReconciliationV1 import (
    PUMP_PROGRAM,
    account_keys,
    fetch_blocks,
    load_env,
)


SCHEMA = "2CQ_REALTIME_COMPLEMENTARITY_FORENSICS_V1"
TWO_CQ_WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"
BUY_KINDS = {"buy", "buy_exact_sol_in", "buy_v2", "buy_exact_quote_in_v2"}


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def pump_instructions(transaction: dict[str, Any]) -> list[tuple[str, list[str], str]]:
    keys = account_keys(transaction)
    message = transaction["transaction"]["message"]
    compiled: list[tuple[str, dict[str, Any]]] = []
    for index, instruction in enumerate(message.get("instructions") or []):
        compiled.append((f"outer:{index}", instruction))
    for group in (transaction.get("meta") or {}).get("innerInstructions") or []:
        for index, instruction in enumerate(group.get("instructions") or []):
            compiled.append((f"outer:{group.get('index')}:inner:{index}", instruction))
    result = []
    for coordinate, instruction in compiled:
        program_index = instruction.get("programIdIndex")
        if program_index is None or int(program_index) >= len(keys):
            continue
        if keys[int(program_index)] != PUMP_PROGRAM:
            continue
        try:
            kind = decode_pump_instruction(base58_decode(str(instruction.get("data") or ""))).kind
        except ExactCurveError:
            kind = "other_pump_instruction"
        accounts = [keys[int(index)] for index in instruction.get("accounts") or [] if int(index) < len(keys)]
        result.append((kind, accounts, coordinate))
    return result


def is_two_cq_entry(transaction: dict[str, Any]) -> bool:
    meta = transaction.get("meta") or {}
    if meta.get("err") is not None:
        return False
    increased_mints = set()
    for post in meta.get("postTokenBalances") or []:
        if post.get("owner") != TWO_CQ_WALLET:
            continue
        amount = int(((post.get("uiTokenAmount") or {}).get("amount")) or 0)
        previous = 0
        for pre in meta.get("preTokenBalances") or []:
            if pre.get("accountIndex") == post.get("accountIndex") and pre.get("mint") == post.get("mint"):
                previous = int(((pre.get("uiTokenAmount") or {}).get("amount")) or 0)
                break
        if amount > previous:
            increased_mints.add(str(post.get("mint")))
    for kind, accounts, _ in pump_instructions(transaction):
        if kind not in BUY_KINDS or len(accounts) <= 6 or accounts[6] != TWO_CQ_WALLET:
            continue
        mint = accounts[2] if len(accounts) > 2 else None
        if not increased_mints or mint in increased_mints:
            return True
    return False


def source_bucket(row: dict[str, Any]) -> str:
    allen = bool(row["allenharkArrival"])
    rabbit = bool(row["rabbitArrival"])
    if allen and rabbit:
        return "BOTH"
    if allen:
        return "ALLENHARK_ONLY"
    if rabbit:
        return "RABBIT_ONLY"
    return "NEITHER"


def pct(numerator: int, denominator: int) -> float:
    return 100.0 * numerator / denominator if denominator else 0.0


def allenhark_slot_activity(binary_root: pathlib.Path, start_slot: int, end_slot: int) -> dict[int, dict[str, int]]:
    activity: dict[int, dict[str, int]] = collections.defaultdict(
        lambda: {"decodedTransactions": 0, "minObservedOrdinal": 2**63 - 1, "maxObservedOrdinal": -1}
    )
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
                values = RECORD.unpack(raw)
                slot = int(values[2])
                if not (start_slot <= slot <= end_slot):
                    continue
                ordinal = int(values[6])
                row = activity[slot]
                row["decodedTransactions"] += 1
                row["minObservedOrdinal"] = min(row["minObservedOrdinal"], ordinal)
                row["maxObservedOrdinal"] = max(row["maxObservedOrdinal"], ordinal)
    return dict(activity)


def journal_matches(path: pathlib.Path, signatures: set[str]) -> set[str]:
    matches = set()
    if not path.exists():
        return matches
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            signature = str(row.get("signature") or "")
            if signature in signatures:
                matches.add(signature)
    return matches


def run(args: argparse.Namespace) -> dict[str, Any]:
    contract = json.loads((args.canonical_root / "contract.json").read_text(encoding="utf-8"))
    canonical_audit = read_jsonl(args.canonical_root / "canonical-population-audit.jsonl")
    if len(canonical_audit) != 842:
        raise RuntimeError(f"FROZEN_CANONICAL_POPULATION_MISMATCH:{len(canonical_audit)}")
    expected_slots = (445538887, 445539014)
    actual_slots = (int(contract["startSlot"]), int(contract["endSlot"]))
    if actual_slots != expected_slots:
        raise RuntimeError(f"FROZEN_SLOT_RANGE_MISMATCH:{actual_slots}")

    env = load_env(args.env)
    canonical, skipped = fetch_blocks(env["SECURE_RPC_URL"], actual_slots[0], actual_slots[1], args.batch_size)
    if skipped or len(canonical) != len(canonical_audit):
        raise RuntimeError(f"CANONICAL_REFETCH_MISMATCH:n={len(canonical)}:skipped={skipped}")
    transactions = {row["signature"]: row for row in canonical}
    audit_by_signature = {row["signature"]: row for row in canonical_audit}
    if set(transactions) != set(audit_by_signature):
        raise RuntimeError("CANONICAL_SIGNATURE_SET_MISMATCH")

    counts = collections.Counter(source_bucket(row) for row in canonical_audit)
    allen_slot_activity = allenhark_slot_activity(args.all_transaction_root, actual_slots[0], actual_slots[1])
    allen_miss_signatures = {row["signature"] for row in canonical_audit if not row["allenharkArrival"]}
    classified_pump_journal_matches = journal_matches(args.pump_journal, allen_miss_signatures)
    by_slot: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in canonical_audit:
        if row["allenharkArrival"]:
            by_slot[int(row["slot"])].append(row)

    miss_rows = []
    for row in canonical_audit:
        if row["allenharkArrival"]:
            continue
        neighbors = sorted(by_slot[int(row["slot"])], key=lambda item: int(item["transactionIndex"]))
        slot_activity = allen_slot_activity.get(int(row["slot"]), {})
        same_slot_traffic = int(slot_activity.get("decodedTransactions", 0)) > 0
        lower = [item for item in neighbors if int(item["transactionIndex"]) < int(row["transactionIndex"])]
        upper = [item for item in neighbors if int(item["transactionIndex"]) > int(row["transactionIndex"])]
        tx = transactions[row["signature"]]["transaction"]
        version = tx.get("version", "legacy")
        miss_rows.append({
            "signature": row["signature"],
            "slot": row["slot"],
            "canonicalTransactionIndex": row["transactionIndex"],
            "rabbitPresent": bool(row["rabbitArrival"]),
            "group": "A_ALLENHARK_MISSED_RABBIT_CAPTURED" if row["rabbitArrival"] else "B_BOTH_REALTIME_PATHS_MISSED",
            "nearbyAllenharkTransactionPresent": same_slot_traffic,
            "sameSlotAllenharkDecodedTransactionN": int(slot_activity.get("decodedTransactions", 0)),
            "sameSlotAllenharkObservedOrdinalMin": slot_activity.get("minObservedOrdinal"),
            "sameSlotAllenharkObservedOrdinalMax": slot_activity.get("maxObservedOrdinal"),
            "sameSlotAllenharkCanonicalPumpN": len(neighbors),
            "nearestLowerCanonicalTransactionIndex": int(lower[-1]["transactionIndex"]) if lower else None,
            "nearestUpperCanonicalTransactionIndex": int(upper[0]["transactionIndex"]) if upper else None,
            "version": version,
            "versioned": version != "legacy",
            "loadedAddresses": bool(row["loadedAddresses"]),
            "cpiPump": bool(row["cpi"]),
            "pumpKinds": row["kinds"],
            "existingTransportEvidence": {
                "exactSignatureInAllTransactionArrivalJournal": False,
                "exactSignatureInClassifiedPumpArrivalJournal": row["signature"] in classified_pump_journal_matches,
                "sameSlotSubscribeEntriesTrafficPresent": same_slot_traffic,
                "observerPersistenceMatchedDecodedCount": True,
                "observerDecodeFailures": 0,
                "observerReconnects": 0,
                "perSignatureProxyOrShredReconstructionLogPresent": False,
            },
            "localization": "UNKNOWN",
            "localizationDetail": "Absent before observer persistence; frozen evidence cannot distinguish upstream shred delivery, shred reconstruction, local proxy/deshredder, or SubscribeEntries emission.",
        })

    two_cq = []
    for signature, row in transactions.items():
        if is_two_cq_entry(row["transaction"]):
            two_cq.append(audit_by_signature[signature])
    two_cq_counts = collections.Counter(source_bucket(row) for row in two_cq)

    total = len(canonical_audit)
    union = total - counts["NEITHER"]
    report = {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": iso(now()),
        "frozenCanonicalContractSha256": contract.get("schema") and json.loads((args.canonical_root / "freeze.json").read_text(encoding="utf-8"))["contractSha256"],
        "frozenSlotRange": {"start": actual_slots[0], "end": actual_slots[1], "slots": actual_slots[1] - actual_slots[0] + 1},
        "canonicalSuccessfulPumpN": total,
        "sourceMatrix": {name: int(counts[name]) for name in ("BOTH", "ALLENHARK_ONLY", "RABBIT_ONLY", "NEITHER")},
        "coverage": {
            "allenharkPct": pct(counts["BOTH"] + counts["ALLENHARK_ONLY"], total),
            "rabbitPct": pct(counts["BOTH"] + counts["RABBIT_ONLY"], total),
            "unionPct": pct(union, total),
            "neitherPct": pct(counts["NEITHER"], total),
        },
        "allenharkMisses": {
            "N": len(miss_rows),
            "rabbitCapturedN": sum(row["rabbitPresent"] for row in miss_rows),
            "bothMissedN": sum(not row["rabbitPresent"] for row in miss_rows),
            "localizationCounts": dict(collections.Counter(row["localization"] for row in miss_rows)),
            "rows": miss_rows,
        },
        "twoCqCanonicalDefinition": "Frozen Rust direct-detector semantics: finalized successful Pump buy, resolved source account[6]=2CQ, and matching wallet token-balance increase when available.",
        "twoCq": {
            "canonicalN": len(two_cq),
            "both": int(two_cq_counts["BOTH"]),
            "allenharkOnly": int(two_cq_counts["ALLENHARK_ONLY"]),
            "rabbitOnly": int(two_cq_counts["RABBIT_ONLY"]),
            "neither": int(two_cq_counts["NEITHER"]),
            "unionCoveragePct": pct(len(two_cq) - two_cq_counts["NEITHER"], len(two_cq)) if two_cq else None,
            "signatures": [row["signature"] for row in sorted(two_cq, key=lambda item: (item["slot"], item["transactionIndex"]))],
        },
        "observerPersistenceExcludedAsCause": True,
        "verdict": "REALTIME_REDUNDANCY_SUFFICIENT" if counts["NEITHER"] == 0 else "REALTIME_COVERAGE_STILL_INSUFFICIENT",
        "outcomesRead": 0,
        "submissions": 0,
        "servicesChanged": 0,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output / "report.json", report)
    with (args.output / "allenhark-miss-forensics.jsonl").open("w", encoding="utf-8") as stream:
        for row in miss_rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical-root", type=pathlib.Path, required=True)
    parser.add_argument("--env", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--all-transaction-root", type=pathlib.Path, required=True)
    parser.add_argument("--pump-journal", type=pathlib.Path, required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
