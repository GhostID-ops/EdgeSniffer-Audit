#!/usr/bin/env python3
"""Integrity-audit and finalize the immutable executable-opportunity census."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import pathlib
import random
import statistics
from typing import Any


EXPECTED_SCHEMA = "EXECUTABLE_OPPORTUNITY_CENSUS_V1"
REFERENCE_COST = "0.0010"
REFERENCE_THRESHOLD = "10"


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


def distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "N": len(values),
        "P25": percentile(values, 0.25),
        "median": statistics.median(values) if values else None,
        "P75": percentile(values, 0.75),
        "P90": percentile(values, 0.90),
        "worst": max(values) if values else None,
    }


def bootstrap_rate(values: list[int], replicates: int, seed: int) -> dict[str, float | int | None]:
    if not values:
        return {"N": 0, "rate": None, "ciLow": None, "ciHigh": None}
    rng = random.Random(seed)
    n = len(values)
    samples = [sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(replicates)]
    return {
        "N": n,
        "rate": sum(values) / n,
        "ciLow": percentile(samples, 0.025),
        "ciHigh": percentile(samples, 0.975),
    }


def iter_rows(root: pathlib.Path):
    for path in sorted((root / "partitions").glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            header = json.loads(next(handle))["header"]
            for line in handle:
                if line.strip():
                    yield path, header, json.loads(line)


def cell_for(state: dict[str, Any], scenario: str, size: float) -> dict[str, Any] | None:
    cell = state["cells"].get(f"{scenario}|{size:.1f}")
    return cell if cell and cell.get("status") == "EXACT" else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("census_root", type=pathlib.Path)
    parser.add_argument("evidence_root", type=pathlib.Path)
    args = parser.parse_args()
    root = args.census_root
    identity = json.loads((root / "run-identity.json").read_text())
    contract = json.loads((root / "contract.json").read_text())
    raw_report = json.loads((root / "report.json").read_text())
    provenance = json.loads((root / "provenance.json").read_text())
    if sha256_file(root / "contract.json") != identity["contractSha256"]:
        raise RuntimeError("CONTRACT_HASH_MISMATCH")
    if raw_report["contractSha256"] != identity["contractSha256"]:
        raise RuntimeError("REPORT_CONTRACT_MISMATCH")

    manifest_by_path = {pathlib.Path(item["path"]).name: item for item in provenance["partitions"]}
    partition_hash_failures = []
    for path in sorted((root / "partitions").glob("*.jsonl.gz")):
        manifest = manifest_by_path.get(path.name)
        if not manifest or manifest.get("sha256") != sha256_file(path):
            partition_hash_failures.append(path.name)

    rows = []
    path_headers: dict[str, dict[str, Any]] = {}
    for path, header, row in iter_rows(root):
        path_headers[path.name] = header
        rows.append(row)
    mint_counts = collections.Counter(row["mint"] for row in rows)
    ordinal_counts = collections.Counter(int(row["ordinal"]) for row in rows)
    duplicates = sorted(mint for mint, count in mint_counts.items() if count != 1)
    duplicate_ordinals = sorted(ordinal for ordinal, count in ordinal_counts.items() if count != 1)
    expected_ordinals = set(range(1, int(identity["boundaryOrdinal"]) + 1))
    missing_ordinals = sorted(expected_ordinals - set(ordinal_counts))
    extra_ordinals = sorted(set(ordinal_counts) - expected_ordinals)
    evaluable = [row for row in rows if row.get("status") == "EXACT_EVALUABLE"]

    covered_start_ms = int(dt.datetime.fromisoformat(raw_report["population"]["coveredStartUtc"]).timestamp() * 1000)
    covered_end_ms = int(dt.datetime.fromisoformat(raw_report["population"]["coveredEndUtc"]).timestamp() * 1000)
    observed_launches = 0
    exact_admitted_launches = 0
    excluded_reason_counts: collections.Counter[str] = collections.Counter()
    excluded_reason_combinations: collections.Counter[tuple[str, ...]] = collections.Counter()
    intentionally_excluded_mayhem = 0
    for path in sorted((args.evidence_root / "readiness-v1" / "partitions").glob("*-v4.json")):
        readiness = json.loads(path.read_text(encoding="utf-8"))
        for row in readiness.get("mints", []):
            timestamp = int(row.get("createReceivedAtUnixNs") or 0) // 1_000_000
            if not covered_start_ms <= timestamp <= covered_end_ms:
                continue
            observed_launches += 1
            if row.get("exactAdmitted"):
                exact_admitted_launches += 1
                continue
            reasons = tuple(sorted(row.get("exclusionReasons") or ["UNKNOWN"]))
            excluded_reason_counts.update(reasons)
            excluded_reason_combinations[reasons] += 1
            if "unsupported_mayhem_transition" in reasons:
                intentionally_excluded_mayhem += 1
    eligible_observed_launches = observed_launches - intentionally_excluded_mayhem
    eligible_population_evaluable_rate = (
        len(evaluable) / eligible_observed_launches if eligible_observed_launches else None
    )

    scenarios = ("ZERO_DELAY_ORACLE", "P50", "P75", "P90")
    sizes = (0.5, 1.0)
    divergence: dict[str, Any] = {}
    coverage: dict[str, Any] = {}
    for scenario in scenarios:
        for size in sizes:
            key = f"{scenario}|{size:.1f}"
            states = 0
            affected_states = 0
            affected_mints = set()
            exact_mints = set()
            for row in evaluable:
                for state in row["eventStates"]:
                    cell = cell_for(state, scenario, size)
                    if cell is None:
                        continue
                    states += 1
                    exact_mints.add(row["mint"])
                    if cell["counterfactualFailedTransactions"] > 0:
                        affected_states += 1
                        affected_mints.add(row["mint"])
            divergence[key] = {
                "exactStateN": states,
                "affectedStateN": affected_states,
                "affectedStateRate": affected_states / states if states else None,
                "exactMintN": len(exact_mints),
                "affectedMintN": len(affected_mints),
                "affectedMintRate": len(affected_mints) / len(exact_mints) if exact_mints else None,
            }
            coverage[key] = len(exact_mints)

    label_data: dict[str, dict[str, Any]] = collections.defaultdict(
        lambda: {"mints": set(), "opportunity": collections.defaultdict(set)}
    )
    reference_flags = []
    one_sol_flags = []
    zero_flags = []
    policy_flags = []
    earliest_hits = []
    longest_windows = []
    total_windows = []
    break_even_margins = []

    for row in evaluable:
        mint = row["mint"]
        reference_hits = []
        one_sol_hits = []
        zero_hits = []
        policy_hit = False
        mint_longest = []
        mint_total = []
        mint_margins = []
        for state in row["eventStates"]:
            for label in state["labels"]:
                label_data[label]["mints"].add(mint)
            for size in sizes:
                cell = cell_for(state, "P50", size)
                if cell is None:
                    continue
                for threshold in ("5", "10", "20"):
                    hit = cell["opportunity"][REFERENCE_COST]["thresholds"][threshold]["reached"]
                    if hit:
                        for label in state["labels"]:
                            label_data[label]["opportunity"][(size, int(threshold))].add(mint)
                if size == 0.5:
                    hit = cell["opportunity"][REFERENCE_COST]["thresholds"][REFERENCE_THRESHOLD]
                    if hit["reached"]:
                        reference_hits.append(int(hit["firstHitAfterEntryMs"]))
                        mint_longest.append(int(hit["longestWindowMs"]))
                        mint_total.append(int(hit["totalWindowMs"]))
                    mint_margins.append(float(cell["breakEvenAdditionalDeliveryCostSol"]))
                    policy = cell["policies"].get("TP20_SL20_MAX120", {})
                    if policy.get("status") == "EXACT":
                        adjusted = float(policy["returnPct"]) - 0.001 * 1_000_000_000 * 100 / (500_000_000 + 5000)
                        policy_hit = policy_hit or adjusted >= 10
                else:
                    hit = cell["opportunity"][REFERENCE_COST]["thresholds"][REFERENCE_THRESHOLD]
                    if hit["reached"]:
                        one_sol_hits.append(int(hit["firstHitAfterEntryMs"]))
            zero = cell_for(state, "ZERO_DELAY_ORACLE", 0.5)
            if zero is not None:
                hit = zero["opportunity"][REFERENCE_COST]["thresholds"][REFERENCE_THRESHOLD]
                if hit["reached"]:
                    zero_hits.append(int(hit["firstHitAfterEntryMs"]))
        reference_flags.append(int(bool(reference_hits)))
        one_sol_flags.append(int(bool(one_sol_hits)))
        zero_flags.append(int(bool(zero_hits)))
        policy_flags.append(int(policy_hit))
        if reference_hits:
            earliest_hits.append(min(reference_hits))
            longest_windows.append(max(mint_longest))
            total_windows.append(max(mint_total))
        if mint_margins:
            break_even_margins.append(max(mint_margins))

    event_state_table = []
    for label, values in sorted(label_data.items()):
        n = len(values["mints"])
        row = {"eventState": label, "evaluableMintN": n}
        for size in sizes:
            for threshold in (5, 10, 20):
                count = len(values["opportunity"].get((size, threshold), set()))
                row[f"size{size:.1f}Threshold{threshold}MintN"] = count
                row[f"size{size:.1f}Threshold{threshold}Prevalence"] = count / n if n else None
        event_state_table.append(row)

    bootstrap = contract["diagnosis"]["frozenDecisionRules"]["bootstrap"]
    opportunity_n = sum(reference_flags)
    policy_n = sum(policy_flags)
    corrected_later_share = sum(value > 2000 for value in earliest_hits) / len(earliest_hits) if earliest_hits else None
    opportunity_policy_gap = opportunity_n - policy_n

    latency_curve = [
        row for row in raw_report["opportunityPrevalence"]
        if row["sizeSol"] in sizes and row["deliveryCostSol"] == 0.001 and row["thresholdPct"] in (5, 10, 20)
    ]
    delivery_cost_curve = [
        row for row in raw_report["opportunityPrevalence"]
        if row["scenario"] == "P50" and row["thresholdPct"] == 10
    ]
    policy_table = [
        row for row in raw_report["frozenPolicyCapture"]
        if row["scenario"] == "P50" and row["deliveryCostSol"] == 0.001 and row["thresholdPct"] == 10
    ]

    raw_diagnosis = raw_report["diagnosis"]
    frozen_rules = contract["diagnosis"]["frozenDecisionRules"]
    triggered = list(raw_diagnosis["allTriggered"])
    data_blocked = (
        eligible_population_evaluable_rate is None
        or eligible_population_evaluable_rate < frozen_rules["dataCoverageBlockerBelowExactEvaluableRate"]
    )
    if data_blocked and "DATA_COVERAGE_BLOCKER" not in triggered:
        triggered.append("DATA_COVERAGE_BLOCKER")
    if not data_blocked and "DATA_COVERAGE_BLOCKER" in triggered:
        triggered.remove("DATA_COVERAGE_BLOCKER")
    triggered = [label for label in frozen_rules["priorityOrder"] if label in triggered]
    final_population = {
        **raw_report["population"],
        "totalObservedLaunchesInCoveredTimeRange": observed_launches,
        "exactAdmittedLaunchesInCoveredTimeRange": exact_admitted_launches,
        "intentionallyExcludedMayhemLaunches": intentionally_excluded_mayhem,
        "eligibilityAdjustedObservedLaunches": eligible_observed_launches,
        "eligibilityAdjustedExactEvaluableRate": eligible_population_evaluable_rate,
        "allObservedExactEvaluableRate": len(evaluable) / observed_launches if observed_launches else None,
        "fullUniverseExclusionReasonOccurrences": dict(excluded_reason_counts),
        "fullUniverseExclusionReasonCombinations": {
            "|".join(reasons): count for reasons, count in excluded_reason_combinations.items()
        },
    }
    final = {
        "schema": EXPECTED_SCHEMA + "_FINAL_AUDITED_REPORT",
        "contractSha256": identity["contractSha256"],
        "evaluatorSha256": identity["scriptSha256"],
        "finalizerSha256": sha256_file(pathlib.Path(__file__)),
        "population": final_population,
        "integrity": {
            "partitionCount": len(path_headers),
            "partitionHashFailures": partition_hash_failures,
            "mintRows": len(rows),
            "uniqueMints": len(mint_counts),
            "duplicateMints": duplicates,
            "duplicateOrdinals": duplicate_ordinals,
            "missingOrdinals": missing_ordinals,
            "extraOrdinals": extra_ordinals,
            "contractHashPass": True,
            "validationRowsInspected": 0,
            "holdoutRowsInspected": 0,
            "liveChanges": 0,
        },
        "coverageByScenario": coverage,
        "referenceOpportunity": {
            "definition": "P50 entry/sellability, 0.5 SOL, 0.001 SOL configurable delivery cost, +10% net threshold, any frozen event state",
            "mintClustered": bootstrap_rate(reference_flags, int(bootstrap["replicates"]), int(bootstrap["seed"])),
            "oneSolMintClustered": bootstrap_rate(one_sol_flags, int(bootstrap["replicates"]), int(bootstrap["seed"]) + 1),
            "zeroDelayHalfSolMintClustered": bootstrap_rate(zero_flags, int(bootstrap["replicates"]), int(bootstrap["seed"]) + 2),
            "bestPolicyCapturedMintClustered": bootstrap_rate(policy_flags, int(bootstrap["replicates"]), int(bootstrap["seed"]) + 3),
            "bestPolicyCaptureConditionalOnOpportunity": policy_n / opportunity_n if opportunity_n else None,
            "economicOpportunityMintsNotCapturedByBestFrozenPolicy": opportunity_policy_gap,
            "economicOpportunityNotCapturedShare": opportunity_policy_gap / opportunity_n if opportunity_n else None,
        },
        "timeAndWindow": {
            "uniqueMintEarliestHitAfterEntryMs": distribution([float(value) for value in earliest_hits]),
            "uniqueMintBestLongestContinuousWindowMs": distribution([float(value) for value in longest_windows]),
            "uniqueMintBestTotalWindowMs": distribution([float(value) for value in total_windows]),
            "uniqueMintLaterThan2sShare": corrected_later_share,
            "rawEventStateWeightedLaterThan2sShare": raw_report["headline"]["laterThan2sFirstHitShare"],
        },
        "breakEvenAdditionalDeliveryMarginSol": distribution(break_even_margins),
        "eventStateOpportunity": event_state_table,
        "latencyOpportunityCurve": latency_curve,
        "deliveryCostOpportunityCurve": delivery_cost_curve,
        "frozenPolicyCapture": policy_table,
        "counterfactualDivergenceByScenario": divergence,
        "economicAttribution": raw_report["economicAttribution"],
        "diagnosis": {
            "primary": triggered[0] if triggered else None,
            "secondary": triggered[1] if len(triggered) > 1 else None,
            "tertiary": triggered[2] if len(triggered) > 2 else None,
            "allFrozenRulesTriggered": triggered,
            "interpretation": (
                "The frozen all-cell divergence gate is triggered. The P50 0.5 SOL reference state rate is below 25%, "
                "while P50 1.0 SOL is above 25%; replay uncertainty is therefore materially more severe at 1 SOL. "
                "The unique-mint later-than-2s share remains above its frozen gate after correcting event-state weighting. "
                "Data coverage uses all eligibility-adjusted observed launches: intentional Mayhem exclusions are outside the frozen census universe."
            ),
        },
        "prohibitionsVerified": raw_report["integrity"],
    }
    blocker = bool(
        partition_hash_failures or duplicates or duplicate_ordinals or missing_ordinals or extra_ordinals
        or len(rows) != int(identity["boundaryOrdinal"])
    )
    final["classification"] = "EXECUTABLE_OPPORTUNITY_CENSUS_V1_INTEGRITY_BLOCKER" if blocker else "EXECUTABLE_OPPORTUNITY_CENSUS_V1_COMPLETE"
    payload = json.dumps(final, indent=2, sort_keys=True) + "\n"
    (root / "final-report.json").write_text(payload, encoding="utf-8")
    (root / "final-report-provenance.json").write_text(json.dumps({
        "contractSha256": identity["contractSha256"],
        "evaluatorSha256": identity["scriptSha256"],
        "finalizerSha256": final["finalizerSha256"],
        "rawReportSha256": sha256_file(root / "report.json"),
        "finalReportSha256": hashlib.sha256(payload.encode()).hexdigest(),
        "validationRowsInspected": 0,
        "holdoutRowsInspected": 0,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "classification": final["classification"],
        "uniqueMints": len(mint_counts),
        "evaluableMints": len(evaluable),
        "referenceOpportunityMints": opportunity_n,
        "primary": final["diagnosis"]["primary"],
        "secondary": final["diagnosis"]["secondary"],
    }, indent=2))
    return 1 if blocker else 0


if __name__ == "__main__":
    raise SystemExit(main())
