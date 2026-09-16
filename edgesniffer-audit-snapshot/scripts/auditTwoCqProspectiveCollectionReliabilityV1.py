#!/usr/bin/env python3
"""Outcome-blind reliability audit for the fixed post-coverage 2CQ window."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
from typing import Any

from reconcilePumpExactEvidenceLedgerV1 import iter_frames
from twoCqProspectiveTierAEntryCensusV1 import (
    PUMP_PROGRAM,
    WALLET,
    evaluate_candidate,
    parse_candidate,
    readiness_reports,
    scan_report,
)


SCHEMA = "2CQ_PROSPECTIVE_COLLECTION_RELIABILITY_REPAIR_V1_AUDIT"


def unix_ms(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def candidate_rows(detector: pathlib.Path, start_ms: int) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with detector.open("rb") as stream:
        for line in stream:
            try:
                raw = json.loads(line)
                parsed = parse_candidate(raw, start_ms)
            except Exception:
                continue
            if parsed is not None:
                rows.setdefault(parsed["signature"], parsed)
    return sorted(rows.values(), key=lambda row: (row["observedUnixMs"], row["signature"]))


def evidence_paths(exact_root: pathlib.Path, start_ms: int, end_ms: int) -> list[pathlib.Path]:
    start = dt.datetime.fromtimestamp(start_ms / 1000, dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = dt.datetime.fromtimestamp(end_ms / 1000, dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    paths = []
    current = start
    while current <= end:
        directory = exact_root / "partitions" / current.strftime("%Y/%m/%d/%H")
        for name in ("evidence.frames.zst", "evidence.frames.partial"):
            path = directory / name
            if path.exists():
                paths.append(path)
        current += dt.timedelta(hours=1)
    return paths


def scan_signatures(paths: list[pathlib.Path], signatures: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = collections.defaultdict(lambda: {
        "rawTransactionN": 0,
        "rpcFinalizedN": 0,
        "rpcFinalizedSuccessfulN": 0,
        "rpcFinalizedFailedN": 0,
        "rpcFinalizedPayloads": [],
        "rawSlots": [],
    })
    frame_n = 0
    for path in paths:
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            frame_n += 1
            signature = str(metadata.get("signature") or "")
            if signature not in signatures:
                continue
            row = evidence[signature]
            if metadata.get("kind") == "transaction":
                row["rawTransactionN"] += 1
                row["rawSlots"].append(metadata.get("slot"))
            elif metadata.get("kind") == "rpc_finalized_transaction":
                row["rpcFinalizedN"] += 1
                if metadata.get("successful") is True:
                    row["rpcFinalizedSuccessfulN"] += 1
                elif metadata.get("failed") is True:
                    row["rpcFinalizedFailedN"] += 1
                try:
                    row["rpcFinalizedPayloads"].append(json.loads(raw).get("result"))
                except Exception:
                    row["rpcFinalizedPayloads"].append(None)
    return dict(evidence), {"paths": [str(path) for path in paths], "frameN": frame_n}


def matching_finalized_buy(candidate: dict[str, Any], evidence: dict[str, Any]) -> bool:
    for transaction in evidence.get("rpcFinalizedPayloads") or []:
        if not transaction or (transaction.get("meta") or {}).get("err") is not None:
            continue
        pre = (transaction.get("meta") or {}).get("preTokenBalances") or []
        post = (transaction.get("meta") or {}).get("postTokenBalances") or []
        pre_amount = sum(
            int((row.get("uiTokenAmount") or {}).get("amount") or 0)
            for row in pre if row.get("mint") == candidate["mint"] and row.get("owner") == WALLET
        )
        post_amount = sum(
            int((row.get("uiTokenAmount") or {}).get("amount") or 0)
            for row in post if row.get("mint") == candidate["mint"] and row.get("owner") == WALLET
        )
        if post_amount > pre_amount:
            return True
    return False


def classify_funnel(
    candidates: list[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    collector_start_ms: int,
) -> tuple[dict[str, str], dict[str, int]]:
    classes: dict[str, str] = {}
    counts: collections.Counter[str] = collections.Counter()
    for candidate in candidates:
        signature = candidate["signature"]
        found = evidence.get(signature)
        if not found or found["rawTransactionN"] == 0:
            reason = (
                "NO_RAW_SIGNATURE_BEFORE_EXACT_COLLECTOR_INSTANCE"
                if candidate["observedUnixMs"] < collector_start_ms
                else "NO_RAW_SIGNATURE_DURING_HEALTHY_EXACT_STREAM"
            )
        elif found["rpcFinalizedN"] == 0:
            reason = "RAW_SIGNATURE_JOINED_FINALITY_RECONCILIATION_MISSING"
        elif found["rpcFinalizedFailedN"] > 0 and found["rpcFinalizedSuccessfulN"] == 0:
            reason = "FINALIZED_FAILED_TRANSACTION"
        elif matching_finalized_buy(candidate, found):
            reason = "FINALIZED_SUCCESSFUL_EXACT_BUY"
        else:
            reason = "FINALIZED_SUCCESS_NO_MATCHING_WALLET_MINT_ACQUISITION"
        classes[signature] = reason
        counts[reason] += 1
    return classes, dict(sorted(counts.items()))


def tier_a_replay(
    candidates: list[dict[str, Any]],
    classes: dict[str, str],
    exact_root: pathlib.Path,
) -> dict[str, Any]:
    reports, mint_readiness = readiness_reports(exact_root)
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    ineligible: collections.Counter[str] = collections.Counter()
    for candidate in candidates:
        if classes[candidate["signature"]] != "FINALIZED_SUCCESSFUL_EXACT_BUY":
            continue
        readiness = mint_readiness.get(candidate["mint"])
        if readiness is None:
            ineligible["NO_EXACT_MINT_READINESS"] += 1
            continue
        report = reports.get(readiness["partitionKey"])
        if report is None or report.get("sourceExactReplayComplete") is not True:
            ineligible["PARTITION_NOT_EXACT_COMPLETE"] += 1
            continue
        if readiness.get("exactAdmitted") is not True:
            ineligible["MINT_NOT_EXACT_ADMITTED"] += 1
            continue
        grouped[readiness["partitionKey"]].append(candidate)

    nominal = sum(len(group) for group in grouped.values())
    valid = 0
    eligibility_reasons: collections.Counter[str] = collections.Counter()
    for partition, group in sorted(grouped.items()):
        targets = {row["mint"] for row in group}
        events, transactions, _ = scan_report(exact_root, reports[partition], targets)
        for candidate in group:
            row, reasons = evaluate_candidate(
                candidate,
                events.get(candidate["mint"], []),
                transactions,
                valid + 1,
                mint_readiness[candidate["mint"]],
            )
            if row is not None:
                valid += 1
            else:
                eligibility_reasons.update(set(reasons))
    return {
        "nominalTierA": nominal,
        "validTierAWithEarlierControl": valid,
        "preNominalExclusions": dict(sorted(ineligible.items())),
        "nominalEligibilityExclusionsNonExclusive": dict(sorted(eligibility_reasons.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detector", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--coverage-report", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    coverage = json.loads(args.coverage_report.read_text(encoding="utf-8"))
    start_ms = unix_ms(coverage["generatedAtUtc"])
    candidates = candidate_rows(args.detector, start_ms)
    end_ms = max((row["observedUnixMs"] for row in candidates), default=start_ms)
    paths = evidence_paths(args.exact_root, start_ms, end_ms)
    evidence, scan = scan_signatures(paths, {row["signature"] for row in candidates})
    health = json.loads((args.exact_root / "health.json").read_text(encoding="utf-8"))
    collector_start_ms = unix_ms(health["startupTimeUtc"])
    classes, counts = classify_funnel(candidates, evidence, collector_start_ms)
    tier_a = tier_a_replay(candidates, classes, args.exact_root)
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "window": {
            "startUtcInclusive": coverage["generatedAtUtc"],
            "endUtcInclusive": dt.datetime.fromtimestamp(end_ms / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        "rawUniqueEntries": len(candidates),
        "funnelClassification": counts,
        "tierA": tier_a,
        "exactCollector": {
            "startupTimeUtc": health.get("startupTimeUtc"),
            "processInstanceUuid": health.get("processInstanceUuid"),
            "evidenceFlowHealthy": health.get("evidenceFlowHealthy"),
            "streamConnected": health.get("streamConnected"),
        },
        "scan": scan,
        "outcomesRead": 0,
        "pnlRead": 0,
        "fingerprintPerformanceRead": 0,
        "servicesModified": 0,
        "rows": [
            {
                "signature": row["signature"],
                "mint": row["mint"],
                "observedAtUtc": row["observedAtUtc"],
                "sourceSlot": row["sourceSlot"],
                "classification": classes[row["signature"]],
            }
            for row in candidates
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
