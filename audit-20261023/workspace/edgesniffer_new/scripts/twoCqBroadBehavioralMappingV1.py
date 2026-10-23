#!/usr/bin/env python3
"""Outcome-blind descriptive mapping for the broad 2CQ teacher corpus.

This script intentionally performs no outcome join, score fitting, threshold
search, or validation-row analysis.  It reports only fields that the corpus
actually stores and marks unavailable route features as unavailable.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import os
import pathlib
import sqlite3
from typing import Any, Iterable


SCHEMA = "2CQ_BROAD_BEHAVIORAL_MAPPING_V1"
FIREWALLED_ROLE = "POST_DEVELOPMENT_VALIDATION_FIREWALLED"
ROUTES = ["PUMP_BONDING_CURVE", "PUMP_CPI", "PUMPSWAP", "OTHER_DEX", "JUPITER_OR_ROUTER"]


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def quantile(values: Iterable[float], p: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * p
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summary(values: Iterable[float]) -> dict[str, float | int | None]:
    ordered = [float(value) for value in values]
    return {
        "n": len(ordered),
        "p50": quantile(ordered, 0.50),
        "p75": quantile(ordered, 0.75),
        "p90": quantile(ordered, 0.90),
        "min": min(ordered) if ordered else None,
        "max": max(ordered) if ordered else None,
    }


def parse(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


def selected_state(choice: dict[str, Any]) -> dict[str, Any] | None:
    mint = choice.get("mint")
    boundary = (choice.get("decisionBoundary") or {}).get("arrivalUnixNs")
    candidates = [state for state in choice.get("states") or [] if state.get("mint") == mint]
    if boundary is not None:
        candidates = [state for state in candidates if int(state.get("stateReceivedAtUnixNs") or 0) <= int(boundary)]
    return max(candidates, key=lambda state: int(state.get("stateReceivedAtUnixNs") or 0), default=None)


def route_report(rows: list[dict[str, Any]], route: str) -> dict[str, Any]:
    subset = [row for row in rows if row["route"] == route]
    tiers = collections.Counter(row["tier"] for row in subset)
    status = collections.Counter(row["strictStatus"] for row in subset)
    mint_counts = collections.Counter(row["mint"] for row in subset)
    ordered = sorted(subset, key=lambda row: (row["blockTimeMs"], row["slot"], row["transactionIndex"], row["signature"]))
    gaps = [(right["blockTimeMs"] - left["blockTimeMs"]) / 1000 for left, right in zip(ordered, ordered[1:])]
    quotes = collections.Counter()
    selected_features: dict[str, list[float]] = collections.defaultdict(list)
    selected_ages: list[float] = []
    acquisition_classes = collections.Counter()
    selected_state_n = 0
    for row in subset:
        economic = row["detail"].get("economicFamily") or {}
        quotes.update(str(value) for value in economic.get("quoteMints") or [])
        choice = row["choice"]
        if choice:
            acquisition_classes[str(choice.get("acquisitionClass") or "UNKNOWN")] += 1
        state = selected_state(choice)
        if state is None:
            continue
        selected_state_n += 1
        boundary = choice.get("decisionBoundary") or {}
        created = state.get("createReceivedAtUnixNs")
        arrival = boundary.get("arrivalUnixNs")
        if created is not None and arrival is not None and int(arrival) >= int(created):
            selected_ages.append((int(arrival) - int(created)) / 1_000_000)
        for key, value in (state.get("features") or {}).items():
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                selected_features[str(key)].append(float(value))
    return {
        "route": route,
        "totalN": len(subset),
        "uniqueMints": len(mint_counts),
        "repeatedMintActions": sum(count for count in mint_counts.values() if count > 1),
        "repeatedMintN": sum(1 for count in mint_counts.values() if count > 1),
        "evidenceTierN": {f"TIER_{tier}": count for tier, count in sorted(tiers.items())},
        "strictStatusN": dict(sorted(status.items())),
        "quoteAssets": dict(quotes.most_common()),
        "interarrivalSeconds": summary(gaps),
        "burstShareAtMost5Seconds": (sum(gap <= 5 for gap in gaps) / len(gaps)) if gaps else None,
        "burstShareAtMost60Seconds": (sum(gap <= 60 for gap in gaps) / len(gaps)) if gaps else None,
        "acquisitionClasses": dict(acquisition_classes),
        "selectedPreEntryStateN": selected_state_n,
        "selectedTokenAgeMs": summary(selected_ages),
        "selectedFeatureSummaries": {
            key: summary(values)
            for key, values in sorted(selected_features.items())
            if key in {
                "curve_progress", "total_external_buy_sol", "total_external_sell_sol", "total_external_net_sol",
                "gross_buy_sol_5000ms", "gross_sell_sol_5000ms", "net_flow_sol_5000ms",
                "unique_buyers_total", "unique_sellers_total", "creator_participated", "lastExternalAgeMs",
            }
        },
        "unavailable": [value for value in [
            "selected pre-entry state and lifecycle-flow features" if not selected_state_n else None,
            "creator/deployer identity linkage (not present as a canonical corpus field)",
            "outcomes/PnL (intentionally absent)",
        ] if value],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    connection = sqlite3.connect(f"file:{args.corpus}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    all_rows = []
    for row in connection.execute("SELECT * FROM teacher_actions ORDER BY block_time_ms,slot,transaction_index,signature"):
        all_rows.append({
            "signature": row["signature"], "mint": row["mint"], "route": row["route"], "economicFamily": row["economic_family"],
            "blockTimeMs": int(row["block_time_ms"]), "slot": int(row["slot"]), "transactionIndex": int(row["transaction_index"]),
            "strictStatus": row["strict_status"], "strictRole": row["strict_role"], "tier": int(row["evidence_tier"]),
            "detail": parse(row["detail_json"]), "choice": parse(row["choice_json"]),
        })
    connection.close()
    analysis_rows = [row for row in all_rows if row["strictRole"] != FIREWALLED_ROLE]
    reports = {route: route_report(analysis_rows, route) for route in ROUTES}
    tier4 = [row for row in analysis_rows if row["tier"] == 4]
    broad = [row for row in analysis_rows if row["tier"] < 4]
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": now_utc(),
        "totalCorpusActions": len(all_rows),
        "analysisActionsExcludingFirewalledValidation": len(analysis_rows),
        "firewalledValidationRowsExcludedFromAnalysis": len(all_rows) - len(analysis_rows),
        "routeReports": reports,
        "evidenceTierCoverage": {f"TIER_{tier}": sum(row["tier"] == tier for row in analysis_rows) for tier in range(1, 5)},
        "tier4VsBroad": {
            "tier4N": len(tier4),
            "broadTier1to3N": len(broad),
            "tier4RouteDistribution": dict(collections.Counter(row["route"] for row in tier4)),
            "broadRouteDistribution": dict(collections.Counter(row["route"] for row in broad)),
            "representativeness": "Tier-4 is exclusively SOL-Pump-family in this corpus; it is representative only of the fully instrumented SOL-Pump subset, not the cross-route teacher population.",
        },
        "scientificFirewall": {
            "outcomesRead": 0,
            "validationRowsRead": 0,
            "submissions": 0,
            "firewalledValidationRowsExcludedFromAnalysis": len(all_rows) - len(analysis_rows),
            "policyFittingPerformed": False,
        },
        "limitations": [
            "No outcome or PnL data was accessed.",
            "PumpSwap, Other DEX, and router rows lack comparable selected pre-entry state vectors in the present corpus.",
            "Route labels describe currently supported canonical classifications; subvenues inside OTHER_DEX require adapter enrichment before route-specific mechanism claims.",
        ],
    }
    atomic_json(args.output / "report.json", report)
    atomic_json(args.output / "manifest.json", {
        "schema": SCHEMA + "_MANIFEST",
        "generatedAtUtc": now_utc(),
        "corpus": str(args.corpus),
        "analysis": "OUTCOME_BLIND_DESCRIPTIVE_ONLY",
        "firewalledValidationRowsExcluded": report["firewalledValidationRowsExcludedFromAnalysis"],
    })
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
