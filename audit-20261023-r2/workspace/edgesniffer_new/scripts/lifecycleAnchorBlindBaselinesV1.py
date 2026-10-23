#!/usr/bin/env python3
"""Run frozen blind executable baselines at causal lifecycle anchors."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pathlib
import statistics
from typing import Any

from buildPumpExactAlphaResearchV1 import NETWORK_FEE
from executableOpportunityCensusV1 import group_events, state_dict
from sandboxPumpExactFollowThroughAtlasV1 import SOL_QUOTES, collect_partition, load_prefix


SCHEMA = "LIFECYCLE_ANCHOR_BLIND_BASELINES_V1"
LAMPORTS = 1_000_000_000
ANCHOR_THRESHOLDS = {
    "EXTERNAL_GROSS_BUY_GE_1_SOL": 1.0,
    "EXTERNAL_GROSS_BUY_GE_2_SOL": 2.0,
    "EXTERNAL_GROSS_BUY_GE_5_SOL": 5.0,
}


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = int(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def distribution(values: list[float]) -> dict[str, Any]:
    return {
        "N": len(values),
        "min": min(values) if values else None,
        "P10": percentile(values, 0.10),
        "P25": percentile(values, 0.25),
        "median": statistics.median(values) if values else None,
        "P75": percentile(values, 0.75),
        "P90": percentile(values, 0.90),
        "max": max(values) if values else None,
    }


def reconstruct_anchor_metadata(events: list[Any], create_ms: int) -> dict[str, dict[str, Any]]:
    rows = [row for row in events if row.event.quote_mint in SOL_QUOTES and not row.event.mayhem_mode]
    groups = group_events(rows)
    output: dict[str, dict[str, Any]] = {}
    cumulative_external_gross = 0
    external_buy_events = 0
    unique_external_buyers: set[str] = set()
    for group in groups:
        before_gross = cumulative_external_gross
        before_events = external_buy_events
        group_external_gross = 0
        for row in group["rows"]:
            event = row.event
            if event.is_buy and event.user != event.creator:
                amount = int(event.immediate_quote_amount)
                group_external_gross += amount
                cumulative_external_gross += amount
                external_buy_events += 1
                unique_external_buyers.add(event.user)
        for anchor, threshold_sol in ANCHOR_THRESHOLDS.items():
            threshold = int(threshold_sol * LAMPORTS)
            if anchor not in output and before_gross < threshold <= cumulative_external_gross:
                output[anchor] = {
                    "decisionUnixMs": group["arrivalMs"],
                    "decisionUtc": dt.datetime.fromtimestamp(group["arrivalMs"] / 1000, tz=dt.timezone.utc).isoformat(),
                    "mintAgeMs": group["arrivalMs"] - create_ms,
                    "triggerSignature": group["signature"],
                    "triggerSlot": group["slot"],
                    "thresholdSol": threshold_sol,
                    "cumulativeExternalGrossBuySolBefore": before_gross / LAMPORTS,
                    "crossingTransactionExternalGrossBuySol": group_external_gross / LAMPORTS,
                    "actualCumulativeExternalGrossBuySol": cumulative_external_gross / LAMPORTS,
                    "overshootSol": (cumulative_external_gross - threshold) / LAMPORTS,
                    "overshootPctOfThreshold": (cumulative_external_gross - threshold) * 100 / threshold,
                    "externalBuyEventCount": external_buy_events,
                    "uniqueExternalBuyerCount": len(unique_external_buyers),
                    "decisionState": state_dict(group["postState"]),
                }
        if "EXTERNAL_BUY_EVENT_5" not in output and before_events < 5 <= external_buy_events:
            output["EXTERNAL_BUY_EVENT_5"] = {
                "decisionUnixMs": group["arrivalMs"],
                "decisionUtc": dt.datetime.fromtimestamp(group["arrivalMs"] / 1000, tz=dt.timezone.utc).isoformat(),
                "mintAgeMs": group["arrivalMs"] - create_ms,
                "triggerSignature": group["signature"],
                "triggerSlot": group["slot"],
                "thresholdBuyEventCount": 5,
                "externalBuyEventCountBefore": before_events,
                "actualExternalBuyEventCount": external_buy_events,
                "buyEventOvershoot": external_buy_events - 5,
                "actualCumulativeExternalGrossBuySol": cumulative_external_gross / LAMPORTS,
                "crossingTransactionExternalGrossBuySol": group_external_gross / LAMPORTS,
                "uniqueExternalBuyerCount": len(unique_external_buyers),
                "decisionState": state_dict(group["postState"]),
            }
    return output


def load_parent_partition(path: pathlib.Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    rows = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        header = json.loads(next(handle))["header"]
        for line in handle:
            if line.strip():
                row = json.loads(line)
                rows[row["mint"]] = row
    return header, rows


def anchor_state(parent: dict[str, Any], label: str) -> dict[str, Any] | None:
    matches = [state for state in parent.get("eventStates", []) if label in state.get("labels", [])]
    if len(matches) > 1:
        raise RuntimeError(f"DUPLICATE_PARENT_ANCHOR:{parent['mint']}:{label}")
    return matches[0] if matches else None


def write_partition(path: pathlib.Path, header: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"header": header}, sort_keys=True, separators=(",", ":")) + "\n")
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def iter_rows(output: pathlib.Path):
    for path in sorted((output / "partitions").glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            next(handle)
            for line in handle:
                if line.strip():
                    yield json.loads(line)


def adjusted_result(row: dict[str, Any], scenario: str, size: float, cost: float, policy: str) -> dict[str, Any] | None:
    cell = row["cells"].get(f"{scenario}|{size:.1f}")
    if not cell or cell.get("status") != "EXACT":
        return None
    policy_row = cell["policies"].get(policy, {})
    if policy_row.get("status") != "EXACT":
        return None
    capital = int(round(size * LAMPORTS)) + NETWORK_FEE
    cost_return_pct = cost * LAMPORTS * 100 / capital
    return {
        "mint": row["mint"],
        "ordinal": row["ordinal"],
        "decisionUnixMs": row["decisionUnixMs"],
        "exitUnixMs": int(policy_row["landingAtMs"]),
        "returnPct": float(policy_row["returnPct"]) - cost_return_pct,
        "pnlSol": float(policy_row["pnlSol"]) - cost,
        "outcome": policy_row["outcome"],
        "triggerRoiPct": policy_row.get("triggerRoiPct"),
        "baseMfePct": cell.get("baseMfePct"),
        "baseMaePct": cell.get("baseMaePct"),
        "counterfactualDiverged": int(cell.get("counterfactualFailedTransactions") or 0) > 0,
    }


def select_max_open1(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    accepted = []
    busy_until = -1
    rejected = 0
    for row in sorted(results, key=lambda item: (item["decisionUnixMs"], item["mint"])):
        if row["decisionUnixMs"] < busy_until:
            rejected += 1
            continue
        accepted.append(row)
        busy_until = row["exitUnixMs"]
    return accepted, rejected


def result_metric(results: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [row["pnlSol"] for row in results]
    returns = [row["returnPct"] for row in results]
    gains = sum(value for value in pnls if value > 0)
    losses = -sum(value for value in pnls if value < 0)
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in sorted(results, key=lambda item: (item["decisionUnixMs"], item["mint"])):
        cumulative += row["pnlSol"]
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    loss_values = sorted((-value for value in pnls if value < 0), reverse=True)
    outcomes = collections.Counter(row["outcome"] for row in results)
    return {
        "N": len(results),
        "wins": sum(value > 0 for value in pnls),
        "losses": sum(value < 0 for value in pnls),
        "flat": sum(value == 0 for value in pnls),
        "winRate": sum(value > 0 for value in pnls) / len(pnls) if pnls else None,
        "profitFactor": gains / losses if losses else (math.inf if gains else None),
        "expectancyReturnPct": statistics.mean(returns) if returns else None,
        "expectancySol": statistics.mean(pnls) if pnls else None,
        "normalizedPnlSol": sum(pnls),
        "medianReturnPct": statistics.median(returns) if returns else None,
        "returnP10Pct": percentile(returns, 0.10),
        "returnP25Pct": percentile(returns, 0.25),
        "returnP75Pct": percentile(returns, 0.75),
        "returnP90Pct": percentile(returns, 0.90),
        "worstReturnPct": min(returns) if returns else None,
        "bestReturnPct": max(returns) if returns else None,
        "maxDrawdownSol": max_drawdown,
        "largestLossShare": loss_values[0] / losses if losses and loss_values else None,
        "top5LossShare": sum(loss_values[:5]) / losses if losses else None,
        "outcomes": dict(outcomes),
        "divergentN": sum(row["counterfactualDiverged"] for row in results),
        "divergentRate": sum(row["counterfactualDiverged"] for row in results) / len(results) if results else None,
    }


def opportunity_metric(rows: list[dict[str, Any]], scenario: str, size: float, cost: float, policy: str) -> dict[str, Any]:
    exact = []
    opportunity = []
    policy_positive = []
    policy_gte10 = []
    divergence = []
    for row in rows:
        cell = row["cells"].get(f"{scenario}|{size:.1f}")
        if not cell or cell.get("status") != "EXACT":
            continue
        exact.append(row["mint"])
        opportunity.append(bool(cell["opportunity"][f"{cost:.4f}"]["thresholds"]["10"]["reached"]))
        divergence.append(int(cell.get("counterfactualFailedTransactions") or 0) > 0)
        result = adjusted_result(row, scenario, size, cost, policy)
        policy_positive.append(bool(result and result["returnPct"] > 0))
        policy_gte10.append(bool(result and result["returnPct"] >= 10))
    n = len(exact)
    return {
        "exactN": n,
        "opportunityGte10N": sum(opportunity),
        "opportunityGte10Rate": sum(opportunity) / n if n else None,
        "policyPositiveN": sum(policy_positive),
        "policyPositiveRate": sum(policy_positive) / n if n else None,
        "policyGte10N": sum(policy_gte10),
        "policyGte10Rate": sum(policy_gte10) / n if n else None,
        "divergentN": sum(divergence),
        "divergentRate": sum(divergence) / n if n else None,
    }


def evaluate_configuration(rows: list[dict[str, Any]], scenario: str, size: float, cost: float, policy: str) -> dict[str, Any]:
    results = [value for row in rows if (value := adjusted_result(row, scenario, size, cost, policy)) is not None]
    max_open, rejected = select_max_open1(results)
    nondivergent = [row for row in results if not row["counterfactualDiverged"]]
    return {
        "scenario": scenario,
        "sizeSol": size,
        "standardizedDeliveryCostSol": cost,
        "policy": policy,
        "status": "UNAVAILABLE_NOT_IMPUTED" if not results and scenario == "P75" else "EXACT",
        "unconstrained": result_metric(results),
        "maxOpen1": {**result_metric(max_open), "rejectedWhileOpen": rejected},
        "nonDivergentSensitivityOnly": result_metric(nondivergent),
        "opportunityGap": opportunity_metric(rows, scenario, size, cost, policy),
    }


def pairwise_overlap(anchor_sets: dict[str, set[str]]) -> list[dict[str, Any]]:
    anchors = sorted(anchor_sets)
    rows = []
    for left_index, left in enumerate(anchors):
        for right in anchors[left_index + 1:]:
            intersection = anchor_sets[left] & anchor_sets[right]
            union = anchor_sets[left] | anchor_sets[right]
            rows.append({
                "left": left,
                "right": right,
                "leftN": len(anchor_sets[left]),
                "rightN": len(anchor_sets[right]),
                "intersectionN": len(intersection),
                "jaccard": len(intersection) / len(union) if union else None,
            })
    return rows


def summarize(output: pathlib.Path, contract: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    rows = list(iter_rows(output))
    anchors = [item["id"] for item in contract["anchors"]]
    by_anchor = {anchor: [row for row in rows if row["anchor"] == anchor] for anchor in anchors}
    primary_policy = contract["policies"]["primary"]
    diagnostics = contract["policies"]["diagnostic"]
    primary_cost = float(contract["economics"]["primaryStandardizedDeliveryCostSol"])
    costs = [float(value) for value in contract["economics"]["standardizedDeliveryCostSweepSol"]]
    anchor_reports = {}
    eligibility_sets = {anchor: {row["mint"] for row in values} for anchor, values in by_anchor.items()}
    opportunity_sets = collections.defaultdict(set)
    positive_sets = collections.defaultdict(set)

    for anchor, values in by_anchor.items():
        for row in values:
            cell = row["cells"].get("P50|0.5")
            if cell and cell.get("status") == "EXACT":
                if cell["opportunity"][f"{primary_cost:.4f}"]["thresholds"]["10"]["reached"]:
                    opportunity_sets[anchor].add(row["mint"])
                result = adjusted_result(row, "P50", 0.5, primary_cost, primary_policy)
                if result and result["returnPct"] > 0:
                    positive_sets[anchor].add(row["mint"])
        report = {
            "eligibleUniqueMints": len(eligibility_sets[anchor]),
            "primary": evaluate_configuration(values, "P50", 0.5, primary_cost, primary_policy),
            "diagnosticPolicies": [evaluate_configuration(values, "P50", 0.5, primary_cost, policy) for policy in diagnostics],
            "deliveryCostSweep": [evaluate_configuration(values, "P50", 0.5, cost, primary_policy) for cost in costs],
            "latencyRobustness": [evaluate_configuration(values, scenario, 0.5, primary_cost, primary_policy) for scenario in ("P50", "P75", "P90")],
            "oneSolRobustness": evaluate_configuration(values, "P50", 1.0, primary_cost, primary_policy),
        }
        if anchor in ANCHOR_THRESHOLDS:
            report["thresholdOvershoot"] = distribution([float(row["anchorMetadata"]["overshootSol"]) for row in values])
            report["actualCumulativeExternalGrossBuySol"] = distribution([float(row["anchorMetadata"]["actualCumulativeExternalGrossBuySol"]) for row in values])
        else:
            report["buyEventOvershoot"] = distribution([float(row["anchorMetadata"]["buyEventOvershoot"]) for row in values])
            report["uniqueExternalBuyersAtFifthEvent"] = distribution([float(row["anchorMetadata"]["uniqueExternalBuyerCount"]) for row in values])
        anchor_reports[anchor] = report

    ordinal_counts = collections.Counter(int(row["ordinal"]) for row in rows)
    mint_anchor_counts = collections.Counter((row["mint"], row["anchor"]) for row in rows)
    return {
        "schema": SCHEMA + "_REPORT",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "contractSha256": identity["contractSha256"],
        "parentCensus": contract["parentCensus"],
        "population": {
            "discoveryOrdinalStart": 1,
            "discoveryOrdinalEnd": identity["boundaryOrdinal"],
            "anchorRows": len(rows),
            "uniqueMintsAcrossAnchors": len({row["mint"] for row in rows}),
            "anchorEligibleCounts": {anchor: len(values) for anchor, values in eligibility_sets.items()},
        },
        "anchors": anchor_reports,
        "overlap": {
            "eligibility": pairwise_overlap(eligibility_sets),
            "opportunityGte10": pairwise_overlap(opportunity_sets),
            "primaryPolicyPositive": pairwise_overlap(positive_sets),
            "allFourEligibilityIntersectionN": len(set.intersection(*(eligibility_sets[anchor] for anchor in anchors))),
            "warning": "Anchor populations overlap and must not be summed as independent evidence.",
        },
        "integrity": {
            "duplicateMintAnchorRows": [f"{mint}|{anchor}" for (mint, anchor), count in mint_anchor_counts.items() if count != 1],
            "ordinalsOutsideFrozenBoundary": sorted(ordinal for ordinal in ordinal_counts if not 1 <= ordinal <= identity["boundaryOrdinal"]),
            "parentStateMismatches": sum(not row["parentStateParityPass"] for row in rows),
            "validationRowsInspected": 0,
            "holdoutRowsInspected": 0,
            "fingerprintsSearched": False,
            "featureSeparabilitySearched": False,
            "bestAnchorSelected": False,
            "liveChanges": 0,
        },
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# LIFECYCLE_ANCHOR_BLIND_BASELINES_V1",
        "",
        f"Contract SHA256: `{report['contractSha256']}`",
        "",
        "Primary frozen view: 0.5 SOL, P50, TP20/SL20/120s, 0.001 SOL standardized delivery overlay.",
        "",
        "| Anchor | N | WR | PF | Exp % | PnL SOL | +10% opportunity | Policy positive | Policy >=10% | MAX_OPEN1 PF |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for anchor, row in report["anchors"].items():
        primary = row["primary"]
        metric = primary["unconstrained"]
        gap = primary["opportunityGap"]
        max_open = primary["maxOpen1"]
        lines.append(
            f"| {anchor} | {metric['N']} | {(metric['winRate'] or 0):.2%} | {(metric['profitFactor'] or 0):.3f} | "
            f"{(metric['expectancyReturnPct'] or 0):.3f} | {metric['normalizedPnlSol']:.3f} | "
            f"{(gap['opportunityGte10Rate'] or 0):.2%} | {(gap['policyPositiveRate'] or 0):.2%} | "
            f"{(gap['policyGte10Rate'] or 0):.2%} | {(max_open['profitFactor'] or 0):.3f} |"
        )
    lines += [
        "",
        "The four anchors are separate overlapping lanes. No best anchor was selected, no fingerprint search was run, and no validation or holdout rows were accessed.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=pathlib.Path)
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("parent_census_root", type=pathlib.Path)
    parser.add_argument("contract", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema") != "LIFECYCLE_ANCHOR_BLIND_BASELINES_V1_CONTRACT":
        raise RuntimeError("CONTRACT_SCHEMA_MISMATCH")
    if sha256_file(args.parent_census_root / "contract.json") != contract["parentCensus"]["contractSha256"]:
        raise RuntimeError("PARENT_CONTRACT_HASH_MISMATCH")
    if sha256_file(args.parent_census_root / "final-report.json") != contract["parentCensus"]["finalReportSha256"]:
        raise RuntimeError("PARENT_REPORT_HASH_MISMATCH")
    parent_identity = json.loads((args.parent_census_root / "run-identity.json").read_text())
    if parent_identity["discoveryPrefixSha256"] != contract["parentCensus"]["discoveryPrefixSha256"]:
        raise RuntimeError("PARENT_PREFIX_HASH_MISMATCH")

    contract_sha = sha256_file(args.contract)
    boundary = int(contract["population"]["discoveryOrdinalEnd"])
    prefix, ordinals = load_prefix(args.research_root, boundary)
    identity = {
        "schema": SCHEMA + "_RUN_IDENTITY",
        "contractSha256": contract_sha,
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "parentCensusContractSha256": contract["parentCensus"]["contractSha256"],
        "parentCensusFinalReportSha256": contract["parentCensus"]["finalReportSha256"],
        "discoveryPrefixSha256": parent_identity["discoveryPrefixSha256"],
        "boundaryOrdinal": boundary,
    }
    identity_path = args.output / "run-identity.json"
    if identity_path.exists():
        if json.loads(identity_path.read_text()) != identity:
            raise RuntimeError("IMMUTABLE_RUN_IDENTITY_MISMATCH")
    else:
        args.output.mkdir(parents=True, exist_ok=True)
        identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (args.output / "contract.json").write_bytes(args.contract.read_bytes())

    selected = set(ordinals)
    anchors = {item["id"]: item["parentLabel"] for item in contract["anchors"]}
    manifests = []
    total_anchor_rows = 0
    reports = []
    for path in sorted((args.evidence_root / "readiness-v1" / "partitions").glob("*-v4.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        targets = {row["mint"] for row in report.get("mints", []) if row.get("exactAdmitted") and row.get("mint") in selected}
        if targets:
            reports.append((report, targets))

    for report, targets in reports:
        key = report["partitionKey"].replace("/", "-")
        destination = args.output / "partitions" / f"{key}.jsonl.gz"
        parent_path = args.parent_census_root / "partitions" / f"{key}.jsonl.gz"
        parent_header, parent_rows = load_parent_partition(parent_path)
        source_sha = str(report.get("sourceReconciliationManifestSha256") or "")
        if destination.exists():
            with gzip.open(destination, "rt", encoding="utf-8") as handle:
                header = json.loads(next(handle))["header"]
            if header.get("contractSha256") != contract_sha or header.get("sourceSha256") != source_sha:
                raise RuntimeError(f"IMMUTABLE_PARTITION_MISMATCH:{key}")
            total_anchor_rows += int(header["rowCount"])
            manifests.append(header | {"path": str(destination), "sha256": sha256_file(destination), "reused": True})
            continue
        events_by_mint, counters = collect_partition(args.evidence_root, report, targets)
        create_by_mint = {row["mint"]: int(row.get("createReceivedAtUnixNs") or 0) // 1_000_000 for row in report.get("mints", []) if row.get("mint") in targets}
        rows = []
        for mint in sorted(targets, key=lambda value: ordinals[value]):
            parent = parent_rows.get(mint)
            if not parent or parent.get("status") != "EXACT_EVALUABLE":
                continue
            metadata = reconstruct_anchor_metadata(events_by_mint.get(mint, []), create_by_mint[mint])
            for anchor, label in anchors.items():
                state = anchor_state(parent, label)
                raw_anchor = metadata.get(anchor)
                if state is None or raw_anchor is None:
                    continue
                parity = (
                    state["decisionUnixMs"] == raw_anchor["decisionUnixMs"]
                    and state["triggerSignature"] == raw_anchor["triggerSignature"]
                    and state["decisionState"] == raw_anchor["decisionState"]
                )
                rows.append({
                    "schema": SCHEMA + "_ANCHOR_ROW",
                    "mint": mint,
                    "ordinal": ordinals[mint],
                    "anchor": anchor,
                    "parentLabel": label,
                    "decisionUnixMs": state["decisionUnixMs"],
                    "decisionUtc": state["decisionUtc"],
                    "mintAgeMs": state["mintAgeMs"],
                    "triggerSignature": state["triggerSignature"],
                    "triggerSlot": state["triggerSlot"],
                    "anchorMetadata": raw_anchor,
                    "parentStateParityPass": parity,
                    "parentEvidenceIds": state["evidenceIds"],
                    "cells": state["cells"],
                })
        if any(not row["parentStateParityPass"] for row in rows):
            raise RuntimeError(f"PARENT_STATE_PARITY_MISMATCH:{key}")
        header = {
            "schema": SCHEMA + "_PARTITION",
            "contractSha256": contract_sha,
            "partitionKey": report["partitionKey"],
            "sourceSha256": source_sha,
            "parentPartitionSha256": sha256_file(parent_path),
            "parentContractSha256": parent_header["contractSha256"],
            "targetMintCount": len(targets),
            "rowCount": len(rows),
            "collectionCounters": counters,
        }
        write_partition(destination, header, rows)
        total_anchor_rows += len(rows)
        manifests.append(header | {"path": str(destination), "sha256": sha256_file(destination), "reused": False})
        print(json.dumps({"partition": report["partitionKey"], "targets": len(targets), "anchorRows": len(rows)}, sort_keys=True), flush=True)

    report = summarize(args.output, contract, identity)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (args.output / "report.json").write_text(payload, encoding="utf-8")
    (args.output / "report.md").write_text(markdown(report), encoding="utf-8")
    provenance = {
        **identity,
        "reportSha256": hashlib.sha256(payload.encode()).hexdigest(),
        "partitions": manifests,
        "anchorRows": total_anchor_rows,
        "validationRowsInspected": 0,
        "holdoutRowsInspected": 0,
    }
    (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "COMPLETE", "anchorRows": total_anchor_rows, "output": str(args.output)}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
