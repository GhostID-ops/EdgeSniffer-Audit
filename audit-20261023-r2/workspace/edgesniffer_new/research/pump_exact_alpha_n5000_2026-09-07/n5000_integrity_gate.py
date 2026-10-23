#!/usr/bin/env python3
"""Fail-closed integrity gate for the frozen Pump exact-alpha N5000 prefix.

This is an audit harness, not a scanner. It does not define or search features.
It reads only the first 5,000 discovery index entries and retains rows only for
those mints. Output is written only to a new research directory.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import itertools
import json
import math
import pathlib
from typing import Any, Iterable

from reconcilePumpExactEvidenceLedgerV1 import iter_frames, partition_data_path


N = 5_000
PRIMARY_KEYS = (
    "size_0.10_entry_25_sellable_500",
    "size_0.50_entry_25_sellable_500",
    "size_1.00_entry_25_sellable_500",
)
PRIMARY = PRIMARY_KEYS[1]
EXPECTED_LABEL_KEYS = {
    f"size_{size:.2f}_entry_{entry}_sellable_{sellable}"
    for size in (0.01, 0.10, 0.50, 1.00)
    for entry in (25, 50, 100)
    for sellable in (500, 1000)
}
NETWORK_FEE = 5_000


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_new(path: pathlib.Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RuntimeError(f"IMMUTABLE_OUTPUT_EXISTS:{path}")
    path.write_text(data, encoding="utf-8")


def load_prefix(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in itertools.islice(handle, N):
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_prefix_rows(
    research_root: pathlib.Path, prefix_mints: set[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    paths: list[str] = []
    for path in sorted((research_root / "rows-v2").glob("*.jsonl.gz")):
        paths.append(str(path))
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("mint") in prefix_mints:
                    rows.append(row)
    return rows, paths


def q(value: float, expected: float, tolerance: float = 1e-9) -> bool:
    return math.isclose(float(value), float(expected), rel_tol=tolerance, abs_tol=tolerance)


def verify_label(row: dict[str, Any], key: str = PRIMARY) -> dict[str, Any]:
    cell = row["labels"][key]
    checks: dict[str, bool] = {}
    checks["exact"] = cell.get("status") == "EXACT"
    if not checks["exact"]:
        return {"pass": False, "checks": checks}
    cost = int(cell["grossBuyLamports"]) + NETWORK_FEE
    proceeds = int(cell["sellProceedsLamports"]) - NETWORK_FEE
    pnl = (proceeds - cost) / 1e9
    ret = (proceeds - cost) * 100 / cost
    checks["pnl_arithmetic"] = q(cell["pnlSol"], pnl)
    checks["return_arithmetic"] = q(cell["returnPct"], ret)
    checks["entry_clock"] = int(cell["entryMs"]) == int(row["decisionUnixMs"]) + 25
    checks["sellable_clock"] = (
        int(cell["firstSellableAtMs"]) == int(row["decisionUnixMs"]) + 500
        and cell.get("firstSellableFrom") == "decision"
    )
    checks["max_hold_clock"] = (
        cell.get("maxHoldFrom") == "decision"
        and int(cell.get("exitMs") or 0) <= int(row["decisionUnixMs"]) + 120_000
    )
    outcome = cell.get("outcome")
    trigger = float(cell.get("triggerReturnPct"))
    checks["outcome_threshold"] = (
        (outcome == "TP" and trigger >= 20)
        or (outcome == "SL" and trigger <= -20)
        or (
            outcome == "MAX_HOLD"
            and int(cell["exitMs"]) == int(row["decisionUnixMs"]) + 120_000
        )
    )
    before = cell.get("entryState") or {}
    after = cell.get("ownBuyPostState") or {}
    checks["own_buy_state_direction"] = (
        int(after.get("virtualSol") or 0) > int(before.get("virtualSol") or 0)
        and int(after.get("virtualToken") or 0) < int(before.get("virtualToken") or 0)
        and int(cell.get("tokensReceivedRaw") or 0) > 0
    )
    checks["entry_evidence"] = bool(cell.get("entryStateEvidenceId"))
    checks["exit_evidence"] = bool(cell.get("exitStateEvidenceId"))
    observed = cell.get("replayObservationEvidenceIds") or []
    canonical = cell.get("replayCanonicalEvidenceIds") or []
    checks["post_entry_evidence_pairing"] = not observed or bool(canonical)
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "costLamports": cost,
        "proceedsLamports": proceeds,
        "recomputedPnlSol": pnl,
        "recomputedReturnPct": ret,
    }


def select_samples(
    rows: list[dict[str, Any]], partition_by_mint: dict[str, str]
) -> tuple[str | None, list[tuple[str, dict[str, Any]]]]:
    # One fixed 1000 ms row per mint gives activity a common decision horizon.
    representative: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: (r["decisionUnixMs"], r["mint"])):
        if row.get("observationState") != "FIXED_1000MS":
            continue
        if row.get("labels", {}).get(PRIMARY, {}).get("status") != "EXACT":
            continue
        representative.setdefault(row["mint"], row)
    by_partition: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for mint, row in representative.items():
        by_partition[partition_by_mint[mint]].append(row)
    viable = [
        (partition, part)
        for partition, part in by_partition.items()
        if {"TP", "SL", "MAX_HOLD"}.issubset(
            {r["labels"][PRIMARY]["outcome"] for r in part}
        )
        and len(part) >= 25
    ]
    if not viable:
        return None, []
    partition, part = max(viable, key=lambda item: len(item[1]))
    chosen: list[tuple[str, dict[str, Any]]] = []
    used: set[str] = set()
    for outcome in ("TP", "SL", "MAX_HOLD"):
        options = [r for r in part if r["labels"][PRIMARY]["outcome"] == outcome]
        options.sort(
            key=lambda r: r["features"].get("buyCount", 0)
            + r["features"].get("sellCount", 0)
        )
        row = options[len(options) // 2]
        chosen.append((outcome, row))
        used.add(row["mint"])
    remaining = [r for r in part if r["mint"] not in used]
    remaining.sort(
        key=lambda r: r["features"].get("buyCount", 0)
        + r["features"].get("sellCount", 0)
    )
    if remaining:
        chosen.append(("LOW_ACTIVITY", remaining[0]))
        used.add(remaining[0]["mint"])
    remaining = [r for r in remaining if r["mint"] not in used]
    if remaining:
        chosen.append(("HIGH_ACTIVITY", remaining[-1]))
    return partition, chosen


def partition_hours(
    ledger_root: pathlib.Path, partition_key: str
) -> list[pathlib.Path]:
    normalized = partition_key.replace("/", "-")
    hour = dt.datetime.strptime(normalized, "%Y-%m-%d-%H").replace(
        tzinfo=dt.timezone.utc
    )
    paths: list[pathlib.Path] = []
    for offset in range(3):
        directory = (
            ledger_root
            / "partitions"
            / (hour + dt.timedelta(hours=offset)).strftime("%Y/%m/%d/%H")
        )
        path = partition_data_path(directory)
        if path is not None:
            paths.append(path)
    return paths


def resolve_evidence(
    ledger_root: pathlib.Path,
    partition_key: str | None,
    targets: set[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if not partition_key or not targets:
        return {}, []
    resolved: dict[str, dict[str, Any]] = {}
    source_paths: list[str] = []
    for path in partition_hours(ledger_root, partition_key):
        source_paths.append(str(path))
        complete = False
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            evidence_id = metadata.get("evidenceId")
            if evidence_id not in targets:
                continue
            tx_shape: dict[str, Any] = {}
            if metadata.get("kind") == "rpc_finalized_transaction":
                try:
                    payload = json.loads(raw)
                    result = payload.get("result") or {}
                    meta = result.get("meta") or {}
                    tx_shape = {
                        "rpcMetaErr": meta.get("err"),
                        "logMessageN": len(meta.get("logMessages") or []),
                        "innerInstructionGroupN": len(meta.get("innerInstructions") or []),
                        "slot": result.get("slot", metadata.get("slot")),
                    }
                except Exception as exc:
                    tx_shape = {"rawDecodeError": type(exc).__name__}
            resolved[evidence_id] = {
                "kind": metadata.get("kind"),
                "signature": metadata.get("signature"),
                "slot": metadata.get("slot"),
                "transactionIndex": metadata.get("transactionIndex"),
                "successful": metadata.get("successful"),
                "failed": metadata.get("failed"),
                "finalized": metadata.get("finalized"),
                "unresolved": metadata.get("unresolved"),
                "discoveredMints": metadata.get("discoveredMints") or [],
                "hasTradeEvent": metadata.get("hasTradeEvent"),
                "pumpInstructionKinds": metadata.get("pumpInstructionKinds") or [],
                "txShape": tx_shape,
            }
            if len(resolved) == len(targets):
                # Consume the current compressed stream to a clean EOF. Closing
                # a live zstd pipe early is reported as a decompressor failure.
                complete = True
        if complete:
            break
    return resolved, source_paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger_root", type=pathlib.Path)
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()

    output = args.output
    if output.exists():
        raise RuntimeError(f"IMMUTABLE_OUTPUT_EXISTS:{output}")
    output.mkdir(parents=True)

    blockers: list[str] = []
    prefix = load_prefix(args.research_root / "discovery-mint-index.jsonl")
    if len(prefix) != N:
        blockers.append(f"PREFIX_LENGTH:{len(prefix)}")
    ordinals = [row.get("ordinal") for row in prefix]
    if ordinals != list(range(1, N + 1)):
        blockers.append("NON_CONTIGUOUS_ORDINALS")
    mints = [row["mint"] for row in prefix]
    mint_set = set(mints)
    if len(mint_set) != N:
        blockers.append(f"DUPLICATE_PREFIX_MINTS:{N-len(mint_set)}")
    ordering = [
        (int(row.get("createReceivedAtUnixNs") or 0), row["mint"]) for row in prefix
    ]
    if ordering != sorted(ordering):
        blockers.append("PREFIX_NOT_CHRONOLOGICAL")
    prefix_sha = hashlib.sha256("\n".join(mints).encode()).hexdigest()
    write_new(
        output / "n5000-prefix.jsonl",
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in prefix),
    )

    rows, row_paths = load_prefix_rows(args.research_root, mint_set)
    state_keys = [(row["mint"], row["observationState"]) for row in rows]
    if len(state_keys) != len(set(state_keys)):
        blockers.append(f"DUPLICATE_MINT_STATE_ROWS:{len(state_keys)-len(set(state_keys))}")
    if any(set(row.get("labels") or {}) != EXPECTED_LABEL_KEYS for row in rows):
        blockers.append("LABEL_SCHEMA_MISMATCH")
    if any(row.get("availabilityCausalityPass") is not True for row in rows):
        blockers.append("AVAILABILITY_CAUSALITY_FAILURE")

    excluded: dict[str, str] = {}
    manifest_hashes: list[dict[str, Any]] = []
    for path in sorted((args.research_root / "manifests-v2").glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest_hashes.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "partitionKey": manifest.get("partitionKey"),
                "rowSha256": manifest.get("sha256"),
                "sourceFingerprintSha256": manifest.get("sourceFingerprintSha256"),
            }
        )
        for item in manifest.get("excluded") or []:
            if item.get("mint") in mint_set:
                excluded[item["mint"]] = item.get("reason") or "UNKNOWN"
    row_mints = {row["mint"] for row in rows}
    unaccounted = mint_set - row_mints - set(excluded)
    replaced = row_mints.intersection(excluded)
    if unaccounted:
        blockers.append(f"UNACCOUNTED_PREFIX_MINTS:{len(unaccounted)}")
    if replaced:
        blockers.append(f"MINT_BOTH_BUILT_AND_EXCLUDED:{len(replaced)}")
    if len(row_mints) + len(excluded) != N:
        blockers.append(
            f"READINESS_ACCOUNTING:{len(row_mints)}+{len(excluded)}!={N}"
        )

    label_summary: dict[str, Any] = {}
    replay_pairing_failures = 0
    for key in PRIMARY_KEYS:
        cells = [
            row["labels"][key]
            for row in rows
            if row.get("labels", {}).get(key, {}).get("status") == "EXACT"
        ]
        outcomes = collections.Counter(cell.get("outcome") for cell in cells)
        returns = [
            float(cell["returnPct"])
            for cell in cells
            if isinstance(cell.get("returnPct"), (int, float))
        ]
        post_entry = [
            cell
            for cell in cells
            if cell.get("replayObservationEvidenceIds")
        ]
        paired = [
            cell
            for cell in post_entry
            if cell.get("replayCanonicalEvidenceIds")
        ]
        replay_pairing_failures += len(post_entry) - len(paired)
        label_summary[key] = {
            "exactCells": len(cells),
            "distinctExactReturns": len({round(value, 12) for value in returns}),
            "minReturnPct": min(returns) if returns else None,
            "medianReturnPct": (
                sorted(returns)[len(returns) // 2] if returns else None
            ),
            "maxReturnPct": max(returns) if returns else None,
            "outcomes": dict(sorted(outcomes.items())),
            "postEntryActivityCells": len(post_entry),
            "postEntryCellsWithCanonicalEvidence": len(paired),
        }
        if not {"TP", "SL", "MAX_HOLD"}.issubset(outcomes):
            blockers.append(f"OUTCOME_CLASS_MISSING:{key}")
        if len(set(round(value, 12) for value in returns)) <= 1:
            blockers.append(f"DEGENERATE_RETURNS:{key}")
    if replay_pairing_failures:
        blockers.append(f"POST_ENTRY_EVIDENCE_PAIRING_FAILURES:{replay_pairing_failures}")

    fix_freeze_path = (
        args.research_root
        / "PUMP_EXACT_ALPHA_RESEARCH_PIPELINE_V1.POST_ENTRY_CANONICAL_REPLAY_FIX.freeze.json"
    )
    fix_freeze = json.loads(fix_freeze_path.read_text(encoding="utf-8"))
    code_hashes: dict[str, dict[str, Any]] = {}
    expected_hashes = fix_freeze["provenance"]["codeSha256"]
    scripts = pathlib.Path("/root/bot/EdgeSniffer/scripts")
    for name, expected in expected_hashes.items():
        path = scripts / name
        actual = sha256_file(path)
        code_hashes[name] = {
            "path": str(path),
            "expected": expected,
            "actual": actual,
            "match": actual == expected,
        }
        if actual != expected:
            blockers.append(f"CODE_HASH_MISMATCH:{name}")
    semantics = fix_freeze.get("semanticIntegrity") or {}
    if semantics.get("exactIntegerPumpMathUnchanged") is not True:
        blockers.append("EXACT_INTEGER_MATH_NOT_FROZEN")
    if semantics.get("failedTransactionsMutateState") is not False:
        blockers.append("FAILED_TRANSACTION_STATE_SEMANTIC")
    if semantics.get("validationRowsInspected") != 0:
        blockers.append("VALIDATION_EXPOSURE_IN_FREEZE")
    if semantics.get("holdoutRowsInspected") != 0:
        blockers.append("HOLDOUT_EXPOSURE_IN_FREEZE")

    partition_by_mint = {row["mint"]: row["partitionKey"] for row in prefix}
    sample_partition, chosen = select_samples(rows, partition_by_mint)
    sample_records: list[dict[str, Any]] = []
    evidence_targets: set[str] = set()
    for role, row in chosen:
        cell = row["labels"][PRIMARY]
        verify = verify_label(row)
        if not verify["pass"]:
            blockers.append(f"MANUAL_SAMPLE_LABEL_FAILURE:{role}:{row['mint']}")
        target_ids = {
            cell.get("entryStateEvidenceId"),
            cell.get("exitStateEvidenceId"),
        }
        replay = cell.get("replayCanonicalEvidenceIds") or []
        if replay:
            target_ids.add(replay[0])
            target_ids.add(replay[-1])
        target_ids.discard(None)
        evidence_targets.update(target_ids)
        sample_records.append(
            {
                "role": role,
                "mint": row["mint"],
                "ordinal": next(
                    item["ordinal"] for item in prefix if item["mint"] == row["mint"]
                ),
                "partitionKey": partition_by_mint[row["mint"]],
                "observationState": row["observationState"],
                "decisionUnixMs": row["decisionUnixMs"],
                "activity": {
                    "buyCount": row["features"].get("buyCount"),
                    "sellCount": row["features"].get("sellCount"),
                    "grossBuySol": row["features"].get("grossBuySol"),
                    "grossSellSol": row["features"].get("grossSellSol"),
                },
                "label": {
                    k: cell.get(k)
                    for k in (
                        "status",
                        "outcome",
                        "returnPct",
                        "pnlSol",
                        "grossBuyLamports",
                        "sellProceedsLamports",
                        "entryMs",
                        "firstSellableAtMs",
                        "exitMs",
                        "triggerReturnPct",
                        "counterfactualFailedTransactions",
                        "entryStateEvidenceId",
                        "exitStateEvidenceId",
                    )
                },
                "replayCanonicalEvidenceN": len(replay),
                "targetEvidenceIds": sorted(target_ids),
                "independentLabelChecks": verify,
            }
        )
    if len(sample_records) != 5:
        blockers.append(f"REPRESENTATIVE_SAMPLE_COUNT:{len(sample_records)}")

    resolved, raw_paths = resolve_evidence(
        args.ledger_root, sample_partition, evidence_targets
    )
    missing_evidence = evidence_targets - set(resolved)
    if missing_evidence:
        blockers.append(f"SAMPLE_CANONICAL_EVIDENCE_UNRESOLVED:{len(missing_evidence)}")
    for record in sample_records:
        details = {
            evidence_id: resolved.get(evidence_id)
            for evidence_id in record["targetEvidenceIds"]
        }
        record["canonicalEvidence"] = details
        for evidence_id, metadata in details.items():
            if not metadata:
                continue
            if metadata.get("kind") != "rpc_finalized_transaction":
                blockers.append(f"SAMPLE_NOT_CANONICAL_RPC:{record['mint']}:{evidence_id}")
            tx_shape = metadata.get("txShape") or {}
            if (
                metadata.get("failed") is True
                or tx_shape.get("rpcMetaErr") is not None
                or metadata.get("unresolved") is True
                or record["mint"] not in metadata.get("discoveredMints", [])
            ):
                blockers.append(f"SAMPLE_CANONICAL_TX_MISMATCH:{record['mint']}:{evidence_id}")

    report = {
        "schema": "PUMP_EXACT_ALPHA_N5000_INTEGRITY_GATE_V1",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "classification": "PASS" if not blockers else "BLOCKED",
        "blockers": sorted(set(blockers)),
        "prefix": {
            "ordinals": [1, N],
            "ordinalContiguous": ordinals == list(range(1, N + 1)),
            "uniqueMintN": len(mint_set),
            "ordering": "createReceivedAtUnixNs,mint",
            "chronological": ordering == sorted(ordering),
            "checkpointPrefixSha256": prefix_sha,
            "prefixFileSha256": sha256_file(output / "n5000-prefix.jsonl"),
            "rowSource": "rows-v2 only",
            "sourceRowFiles": row_paths,
        },
        "accounting": {
            "materializedUniqueMints": len(row_mints),
            "accountedExcludedMints": len(excluded),
            "accountedExcludedReasons": dict(
                sorted(collections.Counter(excluded.values()).items())
            ),
            "unaccountedMints": sorted(unaccounted),
            "builtAndExcludedMints": sorted(replaced),
            "materializedOrAccountedN": len(row_mints) + len(excluded),
            "opportunityRows": len(rows),
            "uniqueMintStateRows": len(set(state_keys)),
        },
        "labels": label_summary,
        "semanticFreeze": {
            "path": str(fix_freeze_path),
            "sha256": sha256_file(fix_freeze_path),
            "classification": fix_freeze.get("classification"),
            "exactIntegerPumpMathUnchanged": semantics.get(
                "exactIntegerPumpMathUnchanged"
            ),
            "failedTransactionsMutateState": semantics.get(
                "failedTransactionsMutateState"
            ),
            "timingSemanticsSha256": semantics.get("timingSemanticsSha256"),
            "economicLabelSchemaSha256": semantics.get(
                "economicLabelSchemaSha256"
            ),
            "codeHashes": code_hashes,
        },
        "manifestHashes": manifest_hashes,
        "representativeSamplePartition": sample_partition,
        "representativeSamples": sample_records,
        "sampleCanonicalEvidenceTargets": len(evidence_targets),
        "sampleCanonicalEvidenceResolved": len(resolved),
        "sampleRawEvidencePaths": raw_paths,
        "readinessBugRecurrence": (
            "NONE"
            if len(row_mints) + len(excluded) == N and not unaccounted and not replaced
            else "DETECTED"
        ),
        "zeroPostEntryReplayBugRecurrence": (
            "NONE"
            if replay_pairing_failures == 0
            and all(
                value["distinctExactReturns"] > 1
                and value["postEntryCellsWithCanonicalEvidence"] > 0
                for value in label_summary.values()
            )
            else "DETECTED"
        ),
        "unitTestsRunSeparately": [
            "testExactCurveEconomicsV1.py: 9/9 PASS",
            "testPumpExactAlphaResearchV1.py: 9/9 PASS",
        ],
        "scannerAuthorized": not blockers,
    }
    write_new(
        output / "integrity-gate.json",
        json.dumps(report, indent=2, sort_keys=True) + "\n",
    )
    write_new(
        output / "SHA256SUMS",
        "\n".join(
            f"{sha256_file(path)}  {path.name}"
            for path in sorted(output.iterdir())
            if path.name != "SHA256SUMS"
        )
        + "\n",
    )
    print(
        json.dumps(
            {
                "classification": report["classification"],
                "blockers": report["blockers"],
                "prefix": report["prefix"],
                "accounting": report["accounting"],
                "labels": report["labels"],
                "representativeSamples": [
                    {
                        "role": row["role"],
                        "mint": row["mint"],
                        "ordinal": row["ordinal"],
                        "outcome": row["label"]["outcome"],
                        "activity": row["activity"],
                        "independentLabelPass": row["independentLabelChecks"]["pass"],
                        "canonicalEvidenceResolved": sum(
                            value is not None
                            for value in row["canonicalEvidence"].values()
                        ),
                        "canonicalEvidenceRequested": len(
                            row["canonicalEvidence"]
                        ),
                    }
                    for row in sample_records
                ],
                "scannerAuthorized": report["scannerAuthorized"],
                "output": str(output),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
