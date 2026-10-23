#!/usr/bin/env python3
"""Frozen retrospective and prospective gates for the 2CQ capture-first repair."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import pathlib
import sys
from collections.abc import Mapping
from typing import Any, Iterator

import twoCqPostFixEndToEndCollectionDiagnosticV1 as tier_a
import twoCqTrueActivityDenominatorAuditV1 as denominator


SCHEMA = "2CQ_CAPTURE_FIRST_DETECTOR_REPAIR_V1"
WALLET = denominator.WALLET
PUMP = denominator.PUMP
PUMPSWAP = denominator.PUMPSWAP
SUPPORTED_BUY_DISCRIMINATORS = {
    "66063d1201daebea",  # Buy
    "38fc74089edfcd5f",  # BuyExactSolIn
    "b817ee6167c5d33d",  # BuyV2
    "c2ab1c46684d5b2f",  # BuyExactQuoteInV2
}


class CachedTransactionStore(Mapping[str, dict[str, Any] | None]):
    """Disk-backed transaction map with a one-entry memory footprint.

    Prospective reconciliation can span tens of thousands of verbose Solana
    transactions. Loading every cached JSON document into one Python dict makes
    the materializer's heap scale with the full cohort. Consumers only use
    Mapping.get/subscript access, so transactions can remain authoritative on
    disk and be parsed on demand.
    """

    def __init__(self, paths: dict[str, pathlib.Path]) -> None:
        self._paths = paths
        self._last_signature: str | None = None
        self._last_value: dict[str, Any] | None = None

    def __len__(self) -> int:
        return len(self._paths)

    def __iter__(self) -> Iterator[str]:
        return iter(self._paths)

    def __getitem__(self, signature: str) -> dict[str, Any] | None:
        path = self._paths.get(signature)
        if path is None:
            raise KeyError(signature)
        if signature == self._last_signature:
            return self._last_value
        value = json.loads(path.read_text(encoding="utf-8"))
        self._last_signature = signature
        self._last_value = value
        return value


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("rb") as stream:
        for raw in stream:
            try:
                rows.append(json.loads(raw))
            except Exception:
                continue
    return rows


def read_jsonl_gz(path: pathlib.Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def resolved_static_keys(tx: dict[str, Any]) -> list[str]:
    message = (tx.get("transaction") or {}).get("message") or {}
    return [
        str(row.get("pubkey")) if isinstance(row, dict) else str(row)
        for row in message.get("accountKeys") or []
    ]


def classify_successful_transactions(
    signature_rows: list[dict[str, Any]],
    transactions: Mapping[str, dict[str, Any] | None],
    start_ms: int,
    end_ms: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(
        signature_rows,
        key=lambda row: (
            int(row.get("slot") or 0),
            row.get("transactionIndex") is None,
            int(row["transactionIndex"]) if row.get("transactionIndex") is not None else 0,
            str(row.get("signature") or ""),
        ),
    )
    semantic_rows: list[dict[str, Any]] = []
    transaction_classes: list[dict[str, Any]] = []
    seen_trade_mints: set[str] = set()
    for signature_row in ordered:
        signature = str(signature_row.get("signature") or "")
        tx = transactions.get(signature)
        if tx is None:
            continue
        block_time_ms = int(tx.get("blockTime") or signature_row.get("blockTime") or 0) * 1000
        if not start_ms <= block_time_ms <= end_ms:
            continue
        if (tx.get("meta") or {}).get("err") is not None:
            transaction_classes.append({"signature": signature, "classification": "NOT_ACQUISITION", "route": "UNKNOWN"})
            continue
        pre, post, decimals = denominator.wallet_token_states(tx)
        native_delta, fee = denominator.wallet_native_delta(tx)
        route, route_evidence = denominator.route_for(tx)
        positive = 0
        unknown = False
        for mint in sorted(set(pre) | set(post)):
            before = int(pre.get(mint, 0))
            after = int(post.get(mint, 0))
            delta = after - before
            if delta <= 0:
                continue
            positive += 1
            if mint == denominator.WSOL:
                classification = "NON_TRADE_TOKEN_CHANGE"
                is_trade = False
                semantic_route = None
            elif route in {
                "PUMP_BONDING_CURVE", "PUMPSWAP", "PUMP_CPI", "PUMPSWAP_CPI", "JUPITER_OR_ROUTER"
            }:
                is_trade = True
                semantic_route = route
                if before > 0:
                    classification = "ADD_WHILE_ALREADY_EXPOSED"
                elif mint in seen_trade_mints:
                    classification = "FLAT_TO_REENTRY"
                else:
                    classification = "NEW_FROM_FLAT"
            elif route_evidence["nonBenignPrograms"] and native_delta is not None and native_delta < -(fee + 10_000):
                is_trade = True
                semantic_route = route
                if before > 0:
                    classification = "ADD_WHILE_ALREADY_EXPOSED"
                elif mint in seen_trade_mints:
                    classification = "FLAT_TO_REENTRY"
                else:
                    classification = "NEW_FROM_FLAT"
            elif not route_evidence["nonBenignPrograms"]:
                classification = "TRANSFER_RECEIPT"
                semantic_route = None
                is_trade = False
            else:
                classification = "UNKNOWN"
                semantic_route = "UNKNOWN"
                is_trade = False
                unknown = True
            if is_trade and before == 0:
                seen_trade_mints.add(mint)
            semantic_rows.append({
                "schema": SCHEMA + "_FINALIZED_CLASSIFICATION",
                "signature": signature,
                "mint": mint,
                "slot": int(tx.get("slot") or signature_row.get("slot") or 0),
                "transactionIndex": signature_row.get("transactionIndex"),
                "blockTimeUtc": denominator.iso_from_ms(block_time_ms),
                "preRaw": before,
                "postRaw": after,
                "deltaRaw": delta,
                "decimals": decimals.get(mint),
                "classification": classification,
                "route": semantic_route,
                "isTradeAcquisition": is_trade,
                "isIndependentEntryEpisode": is_trade and classification in {"NEW_FROM_FLAT", "FLAT_TO_REENTRY"},
            })
        if positive == 0:
            tx_class = "NOT_ACQUISITION"
        elif unknown:
            tx_class = "UNKNOWN"
        else:
            tx_class = "CLASSIFIED"
        transaction_classes.append({"signature": signature, "classification": tx_class, "route": route})
    semantic_rows.sort(key=lambda row: (
        row["slot"], row.get("transactionIndex") is None,
        int(row["transactionIndex"]) if row.get("transactionIndex") is not None else 0,
        row["signature"], row["mint"],
    ))
    return semantic_rows, transaction_classes


def program_id(instruction: dict[str, Any], keys: list[str]) -> str | None:
    if instruction.get("programId"):
        return str(instruction["programId"])
    index = instruction.get("programIdIndex")
    return keys[int(index)] if index is not None and int(index) < len(keys) else None


def pump_instruction_shapes(tx: dict[str, Any]) -> list[dict[str, Any]]:
    keys = denominator.resolved_keys(tx)
    groups: list[tuple[str, list[dict[str, Any]]]] = [
        ("TOP_LEVEL", ((tx.get("transaction") or {}).get("message") or {}).get("instructions") or [])
    ]
    groups.extend(("CPI", group.get("instructions") or []) for group in (tx.get("meta") or {}).get("innerInstructions") or [])
    shapes: list[dict[str, Any]] = []
    for level, instructions in groups:
        for instruction in instructions:
            if program_id(instruction, keys) != PUMP:
                continue
            accounts = [keys[int(index)] for index in instruction.get("accounts") or [] if int(index) < len(keys)]
            try:
                raw = denominator.b58decode(str(instruction.get("data") or ""))
            except Exception:
                raw = b""
            shapes.append({
                "level": level,
                "discriminatorHex": raw[:8].hex(),
                "accountCount": len(accounts),
                "walletAccountIndexes": [index for index, value in enumerate(accounts) if value == WALLET],
                "supportedBuyDiscriminator": raw[:8].hex() in SUPPORTED_BUY_DISCRIMINATORS,
            })
    return shapes


def old_miss_forensics(
    missed: list[dict[str, Any]],
    transactions: dict[str, dict[str, Any] | None],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    causes: collections.Counter[str] = collections.Counter()
    rows: list[dict[str, Any]] = []
    for truth in missed:
        tx = transactions.get(str(truth["signature"]))
        shapes = pump_instruction_shapes(tx) if tx else []
        route = str(truth["route"])
        current_extractor_compatible = any(
            shape["supportedBuyDiscriminator"] and 6 in shape["walletAccountIndexes"]
            for shape in shapes
        )
        if route == "PUMPSWAP":
            cause = "PUMPSWAP_PROGRAM_UNSUPPORTED_BY_OLD_DETECTOR"
        elif current_extractor_compatible:
            cause = "OLD_REALTIME_SOURCE_OR_DETECTOR_SEQUENCING_MISS"
        elif route == "PUMP_CPI":
            cause = "PUMP_CPI_UNSUPPORTED_OR_LAYOUT_MISMATCH"
        elif not shapes:
            cause = "PUMP_INSTRUCTION_NOT_AVAILABLE_TO_OLD_DETECTOR"
        elif not any(shape["supportedBuyDiscriminator"] for shape in shapes):
            cause = "INSTRUCTION_VARIANT_UNSUPPORTED"
        else:
            cause = "ACCOUNT_LAYOUT_MISMATCH"
        causes[cause] += 1
        rows.append({
            "signature": truth["signature"],
            "mint": truth["mint"],
            "route": route,
            "cause": cause,
            "currentExtractorCompatibleFromFinalizedMetadata": current_extractor_compatible,
            "transactionVersioned": bool(tx and ((tx.get("transaction") or {}).get("message") or {}).get("addressTableLookups") is not None),
            "loadedAddressCount": len(((tx or {}).get("meta") or {}).get("loadedAddresses", {}).get("writable") or [])
                + len(((tx or {}).get("meta") or {}).get("loadedAddresses", {}).get("readonly") or []),
            "pumpInstructionShapes": shapes,
        })
    return dict(sorted(causes.items())), rows


def write_jsonl_gz(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def retrospective(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=False)
    denominator_rows = read_jsonl_gz(args.frozen_root / "independent-denominator.jsonl.gz")
    frozen_report = json.loads((args.frozen_root / "report.json").read_text(encoding="utf-8"))
    signature_rows = read_jsonl_gz(args.frozen_root / "wallet-signatures-bounded-context.jsonl.gz")
    transaction_rows = read_jsonl_gz(args.frozen_root / "wallet-finalized-success-transactions.jsonl.gz")
    transactions = {str(row["signature"]): row.get("transaction") for row in transaction_rows}
    start_ms = denominator.parse_ms(str(frozen_report["window"]["startUtc"]))
    end_ms = denominator.parse_ms(str(frozen_report["window"]["endUtc"]))
    canonical_start_ms = ((start_ms + 999) // 1000) * 1000
    canonical_end_ms = (end_ms // 1000) * 1000

    truth = [row for row in denominator_rows if row["isIndependentEntryEpisode"]]
    proposed_present: set[str] = set()
    candidate_evidence: list[dict[str, Any]] = []
    for row in truth:
        tx = transactions.get(str(row["signature"]))
        wallet_static = bool(tx and WALLET in resolved_static_keys(tx))
        allen_arrival = bool(row.get("allenharkArrivalAtUtc"))
        if wallet_static and allen_arrival:
            proposed_present.add(str(row["signature"]))
        candidate_evidence.append({
            "signature": row["signature"],
            "mint": row["mint"],
            "route": row["route"],
            "walletPresentInStaticKeys": wallet_static,
            "allenharkArrivalPresent": allen_arrival,
            "captureFirstCandidateWouldBePresent": wallet_static and allen_arrival,
        })

    classified, transaction_classes = classify_successful_transactions(
        signature_rows, transactions, canonical_start_ms, canonical_end_ms
    )
    predicted = {
        (str(row["signature"]), str(row["mint"]))
        for row in classified if row["isTradeAcquisition"]
    }
    actual = {(str(row["signature"]), str(row["mint"])) for row in denominator_rows if row["isTradeAcquisition"]}
    true_positive = predicted & actual
    false_negative = actual - predicted
    false_positive = predicted - actual
    unknown = [row for row in classified if row["classification"] == "UNKNOWN"]
    predicted_classes = collections.Counter(row["classification"] for row in classified)

    misses = list(frozen_report["missedEpisodes"])
    miss_causes, miss_rows = old_miss_forensics(misses, transactions)
    route_truth = collections.Counter(str(row["route"]) for row in truth)
    route_present = collections.Counter(str(row["route"]) for row in truth if str(row["signature"]) in proposed_present)
    report = {
        "schema": SCHEMA + "_RETROSPECTIVE_REPORT",
        "generatedAtUtc": now_utc(),
        "implementationSha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "frozenDenominatorSha256": frozen_report["denominatorSha256"],
        "window": frozen_report["window"],
        "frozenTrueAcquisitions": len(truth),
        "captureFirstCandidatePresent": len(proposed_present),
        "captureFirstCandidateMissing": len(truth) - len(proposed_present),
        "captureFirstCandidateRecallPct": 100.0 * len(proposed_present) / len(truth),
        "candidateRecallByRoute": {
            route: {"true": route_truth[route], "present": route_present[route], "missing": route_truth[route] - route_present[route]}
            for route in sorted(route_truth)
        },
        "finalizedClassifier": {
            "trueTradeAcquisitions": len(actual),
            "predictedTradeAcquisitions": len(predicted),
            "truePositives": len(true_positive),
            "falseNegatives": len(false_negative),
            "falsePositives": len(false_positive),
            "unknown": len(unknown),
            "recallPct": 100.0 * len(true_positive) / len(actual),
            "precisionPct": 100.0 * len(true_positive) / len(predicted) if predicted else 0.0,
            "classifications": dict(sorted(predicted_classes.items())),
            "expected": {"NEW_FROM_FLAT": 110, "FLAT_TO_REENTRY": 4, "ADD_WHILE_ALREADY_EXPOSED": 0},
        },
        "oldDetector": {
            "missed": len(misses),
            "missedDirectPump": sum(row["route"] == "PUMP_BONDING_CURVE" for row in misses),
            "missedPumpCpi": sum(row["route"] == "PUMP_CPI" for row in misses),
            "missedPumpSwap": sum(row["route"] == "PUMPSWAP" for row in misses),
            "rootCauses": miss_causes,
        },
        "outcomesRead": 0,
        "submissions": 0,
        "gates": {
            "candidateRecall114Of114": len(proposed_present) == 114,
            "classifierEquivalent": len(true_positive) == 114 and not false_negative and not false_positive and not unknown,
        },
    }
    denominator.atomic_json(args.output / "report.json", report)
    write_jsonl_gz(args.output / "capture-first-frozen-114-evidence.jsonl.gz", candidate_evidence)
    write_jsonl_gz(args.output / "finalized-classifications.jsonl.gz", classified)
    write_jsonl_gz(args.output / "old-detector-miss-forensics.jsonl.gz", miss_rows)
    denominator.atomic_json(args.output / "contract.json", {
        "schema": SCHEMA + "_RETROSPECTIVE_CONTRACT",
        "frozenDenominatorSha256": frozen_report["denominatorSha256"],
        "denominatorReconstructed": False,
        "outcomesPermitted": False,
        "submissionsPermitted": False,
    })
    return report


def fetch_prospective_population(
    url: str, start_ms: int, end_ms: int, cache: pathlib.Path
) -> tuple[list[dict[str, Any]], CachedTransactionStore]:
    cache.mkdir(parents=True, exist_ok=True)
    rows = denominator.fetch_signatures_incremental(
        url,
        start_ms // 1000,
        cache.parent / "wallet-signature-population-v1.jsonl.gz",
    )
    bounded = [row for row in rows if start_ms // 1000 <= int(row.get("blockTime") or 0) <= end_ms // 1000]
    paths: dict[str, pathlib.Path] = {}
    for row in bounded:
        signature = str(row["signature"])
        path = cache / f"tx-{signature}.json"
        if not path.exists():
            tx = denominator.fetch_transaction(url, signature)
            denominator.atomic_json(path, tx)
        paths[signature] = path
    return bounded, CachedTransactionStore(paths)


def prospective(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / "report.json"
    if report_path.exists():
        prior = json.loads(report_path.read_text(encoding="utf-8"))
        if prior.get("targetReached") is True:
            return prior
    contract_path = args.output / "contract.json"
    if not contract_path.exists():
        denominator.atomic_json(contract_path, {
            "schema": SCHEMA + "_PROSPECTIVE_CONTRACT",
            "sampleStartUtc": args.sample_start_utc,
            "minimumCanonicalAcquisitions": 25,
            "outcomesPermitted": False,
            "submissionsPermitted": False,
        })
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    start_ms = denominator.parse_ms(str(contract["sampleStartUtc"]))
    end_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000) - 2_000
    env = denominator.load_env(args.env)
    signature_rows, transactions = fetch_prospective_population(env["SECURE_RPC_URL"], start_ms, end_ms, args.output / "rpc-cache")
    classified, transaction_classes = classify_successful_transactions(signature_rows, transactions, start_ms, end_ms)
    true_entries = [row for row in classified if row["isIndependentEntryEpisode"]]
    candidate_rows = [row for row in read_jsonl(args.candidates) if int(row.get("receivedAtUnixNs") or 0) // 1_000_000 >= start_ms]
    candidate_by_signature = {str(row["signature"]): row for row in candidate_rows}
    captured = [row for row in true_entries if str(row["signature"]) in candidate_by_signature]
    misses = [row for row in true_entries if str(row["signature"]) not in candidate_by_signature]
    failed_candidates = 0
    unresolved_candidates = 0
    for row in candidate_rows:
        tx = transactions.get(str(row["signature"]))
        if tx is None:
            unresolved_candidates += 1
        elif (tx.get("meta") or {}).get("err") is not None:
            failed_candidates += 1

    reached = len(true_entries) >= 25
    selected = true_entries[:25] if reached else []
    fully_eligible = 0
    eligibility_rows: list[dict[str, Any]] = []
    exclusions: collections.Counter[str] = collections.Counter()
    if reached:
        required_signatures: set[str] = {str(row["signature"]) for row in selected}
        for row in selected:
            older, boundary = tier_a.older_signatures(
                env["SECURE_RPC_URL"], str(row["mint"]), str(row["signature"]), args.output / "rpc-cache"
            )
            if boundary:
                required_signatures.update(str(item.get("signature") or "") for item in older)
        arrivals, _ = tier_a.compact_arrivals(
            args.compact_arrivals,
            {tier_a.signature_digest(signature) for signature in required_signatures if signature},
        )
        observer_health = json.loads(args.observer_health.read_text(encoding="utf-8"))
        observer_started_ns = int(tier_a.parse_utc(str(observer_health["startedAtUtc"])).timestamp() * 1_000_000_000)
        for row in selected:
            signature = str(row["signature"])
            candidate = candidate_by_signature.get(signature)
            tx = transactions[signature]
            reasons: list[str] = []
            detail: dict[str, Any] = {}
            if candidate is None:
                reasons = ["ENTRY_CAPTURE_FIRST_ARRIVAL_MISSING"]
            elif row["route"] in {"PUMPSWAP", "PUMPSWAP_CPI"}:
                reasons = ["PUMPSWAP_NOT_SUPPORTED_BY_FROZEN_TIER_A_REPLAY"]
            else:
                adapted = {
                    "signature": signature,
                    "outputMint": row["mint"],
                    "detectedAtMs": int(candidate["receivedAtUnixNs"]) // 1_000_000,
                    "sourceSlot": candidate["slot"],
                }
                try:
                    eligible, reasons, detail = tier_a.evaluate_history(
                        env["SECURE_RPC_URL"], adapted, tx, arrivals, args.output / "rpc-cache",
                        observer_started_ns, int(observer_health.get("reconnects") or 0),
                    )
                except Exception as error:
                    eligible = False
                    reasons = ["PREENTRY_EXACT_EVENT_RECONSTRUCTION_INCOMPLETE"]
                    detail = {
                        "evaluationErrorType": type(error).__name__,
                        "evaluationError": str(error),
                    }
                if eligible:
                    fully_eligible += 1
            exclusions.update(reasons)
            eligibility_rows.append({
                "signature": signature,
                "mint": row["mint"],
                "route": row["route"],
                "fullyTierAEligible": not reasons,
                "exclusionReasons": sorted(set(reasons)),
                "detail": detail,
            })

    elapsed_hours = max((end_ms - start_ms) / 3_600_000, 1 / 3600)
    eligible_rate = fully_eligible / elapsed_hours if reached else None
    report = {
        "schema": SCHEMA + "_PROSPECTIVE_REPORT",
        "generatedAtUtc": now_utc(),
        "sampleStartUtc": contract["sampleStartUtc"],
        "sampleEndUtc": denominator.iso_from_ms(end_ms),
        "elapsedHours": elapsed_hours,
        "candidatesObserved": len(candidate_rows),
        "finalizedSuccessfulEntries": len(true_entries),
        "finalizedFailedCandidateTransactions": failed_candidates,
        "unresolvedCandidateTransactions": unresolved_candidates,
        "successfulExactLedgerJoins": sum(transactions.get(str(row["signature"])) is not None for row in true_entries),
        "canonicalTrueAcquisitions": len(true_entries),
        "realtimeCandidateCaptured": len(captured),
        "realtimeCandidateMissing": len(misses),
        "realtimeCandidateRecallPct": 100.0 * len(captured) / len(true_entries) if true_entries else None,
        "fullyTierAEligible": fully_eligible if reached else None,
        "fullyEligiblePct": 100.0 * fully_eligible / len(selected) if selected else None,
        "fullyEligiblePerHour": eligible_rate,
        "estimatedN300Hours": 300 / eligible_rate if eligible_rate else None,
        "estimatedN500Hours": 500 / eligible_rate if eligible_rate else None,
        "exclusions": dict(sorted(exclusions.items())) if reached else {},
        "targetReached": reached,
        "candidateMisses": misses,
        "outcomesRead": 0,
        "submissions": 0,
    }
    denominator.atomic_json(report_path, report)
    write_jsonl_gz(args.output / "finalized-classifications.jsonl.gz", classified)
    if reached:
        write_jsonl_gz(args.output / "tier-a-eligibility-first-25.jsonl.gz", eligibility_rows)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    retro = subparsers.add_parser("retrospective")
    retro.add_argument("--frozen-root", type=pathlib.Path, required=True)
    retro.add_argument("--output", type=pathlib.Path, required=True)
    prospect = subparsers.add_parser("prospective")
    prospect.add_argument("--candidates", type=pathlib.Path, required=True)
    prospect.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    prospect.add_argument("--observer-health", type=pathlib.Path, required=True)
    prospect.add_argument("--env", type=pathlib.Path, required=True)
    prospect.add_argument("--sample-start-utc", required=True)
    prospect.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    result = retrospective(args) if args.command == "retrospective" else prospective(args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
