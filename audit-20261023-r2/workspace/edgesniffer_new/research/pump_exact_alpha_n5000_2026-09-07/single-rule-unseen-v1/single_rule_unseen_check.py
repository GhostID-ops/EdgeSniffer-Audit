#!/usr/bin/env python3
"""Evaluate one preregistered Pump exact-alpha rule on frozen ordinals 5001-END.

This is deliberately not a scanner. It contains exactly one state, feature,
operator and threshold, and it never evaluates any alternative rule.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import math
import pathlib
from typing import Any, Iterable


START = 5001
STATE = "FIXED_25MS"
FEATURE = "firstBuyerShareBps"
OPERATOR = "le"
THRESHOLD = 360.40437631955825
LABEL_INCLUDED_TOTAL_DELIVERY_SOL = 0.000010
SIZE_KEYS = {
    "0.50": "size_0.50_entry_25_sellable_500",
    "1.00": "size_1.00_entry_25_sellable_500",
}
TOTAL_COST_STRESSES_SOL = (0.005, 0.010, 0.020)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_new(path: pathlib.Path, value: Any) -> None:
    if path.exists():
        raise RuntimeError(f"IMMUTABLE_OUTPUT_EXISTS:{path}")
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def metric(
    rows: Iterable[dict[str, Any]],
    label_key: str,
    *,
    total_delivery_cost_sol: float | None = None,
) -> dict[str, Any]:
    rows = list(rows)
    cells = [
        row["labels"][label_key]
        for row in rows
        if row.get("labels", {}).get(label_key, {}).get("status") == "EXACT"
    ]
    incremental_cost_sol = 0.0
    if total_delivery_cost_sol is not None:
        incremental_cost_sol = max(
            0.0, total_delivery_cost_sol - LABEL_INCLUDED_TOTAL_DELIVERY_SOL
        )
    adjusted_pnls = [float(cell["pnlSol"]) - incremental_cost_sol for cell in cells]
    adjusted_returns = [
        pnl * 1_000_000_000 * 100
        / (int(cell["grossBuyLamports"]) + 5_000)
        for pnl, cell in zip(adjusted_pnls, cells)
    ]
    wins = [value for value in adjusted_pnls if value > 0]
    losses = [value for value in adjusted_pnls if value <= 0]
    loss_sum = sum(losses)
    return {
        "matchingUniqueMintN": len(rows),
        "passN": len(cells),
        "passRate": len(cells) / len(rows) if rows else None,
        "PF": sum(wins) / abs(loss_sum) if loss_sum < 0 else None,
        "expectancyReturnPct": (
            sum(adjusted_returns) / len(adjusted_returns)
            if adjusted_returns
            else None
        ),
        "totalPnlSol": sum(adjusted_pnls),
        "meanPnlSolPerTrade": (
            sum(adjusted_pnls) / len(adjusted_pnls) if adjusted_pnls else None
        ),
        "totalDeliveryCostSolPerTrade": total_delivery_cost_sol,
        "incrementalCostBeyondLabelSolPerTrade": incremental_cost_sol,
    }


def positive(value: dict[str, Any]) -> bool:
    return (
        isinstance(value.get("PF"), (int, float))
        and value["PF"] > 1
        and isinstance(value.get("expectancyReturnPct"), (int, float))
        and value["expectancyReturnPct"] > 0
        and isinstance(value.get("totalPnlSol"), (int, float))
        and value["totalPnlSol"] > 0
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("frozen_readiness", type=pathlib.Path)
    parser.add_argument("cost_policy", type=pathlib.Path)
    parser.add_argument("end", type=int)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError(f"IMMUTABLE_OUTPUT_EXISTS:{args.output}")
    args.output.mkdir(parents=True)

    readiness = json.loads(args.frozen_readiness.read_text(encoding="utf-8"))
    if int(readiness["exactAdmittedN"]) != args.end:
        raise RuntimeError("FROZEN_END_MISMATCH")
    if not 5000 < args.end < 10001:
        raise RuntimeError("END_TOUCHES_VALIDATION_OR_HOLDOUT")

    index_path = args.research_root / "discovery-mint-index.jsonl"
    with index_path.open(encoding="utf-8") as handle:
        prefix = [
            json.loads(line)
            for line in itertools.islice(handle, args.end)
            if line.strip()
        ]
    if len(prefix) != args.end:
        raise RuntimeError(f"INDEX_SHORT:{len(prefix)}:{args.end}")
    if [row.get("ordinal") for row in prefix] != list(range(1, args.end + 1)):
        raise RuntimeError("INDEX_ORDINALS_NONCONTIGUOUS")
    if len({row["mint"] for row in prefix}) != args.end:
        raise RuntimeError("INDEX_MINTS_NOT_UNIQUE")
    unseen = prefix[START - 1 : args.end]
    unseen_mints = {row["mint"] for row in unseen}
    ordinal_by_mint = {row["mint"]: int(row["ordinal"]) for row in unseen}
    mid = (START + args.end - 1) // 2
    halves = (("FIRST_HALF", START, mid), ("SECOND_HALF", mid + 1, args.end))

    fixed_rows: dict[str, dict[str, Any]] = {}
    row_files: list[dict[str, str]] = []
    for path in sorted((args.research_root / "rows-v2").glob("*.jsonl.gz")):
        retained_from_file = False
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                mint = row.get("mint")
                if mint not in unseen_mints or row.get("observationState") != STATE:
                    continue
                if mint in fixed_rows:
                    raise RuntimeError(f"DUPLICATE_FIXED_STATE:{mint}")
                fixed_rows[mint] = row
                retained_from_file = True
        if retained_from_file:
            row_files.append({"path": str(path), "sha256": sha256_file(path)})

    matched = []
    for mint, row in fixed_rows.items():
        value = row.get("features", {}).get(FEATURE)
        if isinstance(value, (int, float)) and math.isfinite(value) and value <= THRESHOLD:
            matched.append(row)
    matched.sort(key=lambda row: ordinal_by_mint[row["mint"]])
    if len(matched) != len({row["mint"] for row in matched}):
        raise RuntimeError("PSEUDOREPLICATION")

    baseline = {
        size: metric(matched, key) for size, key in SIZE_KEYS.items()
    }
    chronology: dict[str, Any] = {}
    for name, low, high in halves:
        part = [row for row in matched if low <= ordinal_by_mint[row["mint"]] <= high]
        chronology[name] = {
            "ordinals": [low, high],
            "matchingUniqueMintN": len(part),
            "economics": {
                size: metric(part, key) for size, key in SIZE_KEYS.items()
            },
        }

    break_even = {}
    for size, item in baseline.items():
        current_mean = item["meanPnlSolPerTrade"]
        break_even[size] = {
            "additionalCostBeyondCurrentLabelSolPerTrade": current_mean,
            "totalExecutionDeliveryCostSolPerTradeIncludingLabelBaseFees": (
                current_mean + LABEL_INCLUDED_TOTAL_DELIVERY_SOL
                if current_mean is not None
                else None
            ),
        }

    policy = json.loads(args.cost_policy.read_text(encoding="utf-8"))
    computed = policy["computedPolicy"]
    policy_costs = {
        "CURRENT_IRREVERSIBLE_DELIVERY_POLICY": (
            computed["roundTripIrreversibleDeliveryCostLamports"] / 1e9
        ),
        "CURRENT_FIRST_TRADE_CASH_COST_INCLUDING_165B_ATA": (
            computed["firstTradeRoundTripCashCostIncludingAtaLamports"] / 1e9
        ),
    }
    stress = {}
    for name, cost in policy_costs.items():
        stress[name] = {
            "totalDeliveryCostSolPerTrade": cost,
            "economics": {
                size: metric(matched, key, total_delivery_cost_sol=cost)
                for size, key in SIZE_KEYS.items()
            },
        }
    for cost in TOTAL_COST_STRESSES_SOL:
        name = f"TOTAL_COST_{cost:.3f}_SOL"
        stress[name] = {
            "totalDeliveryCostSolPerTrade": cost,
            "economics": {
                size: metric(matched, key, total_delivery_cost_sol=cost)
                for size, key in SIZE_KEYS.items()
            },
        }

    enough = (
        len(matched) >= 30
        and all(block["matchingUniqueMintN"] >= 10 for block in chronology.values())
    )
    persists = all(positive(value) for value in baseline.values()) and all(
        positive(value)
        for block in chronology.values()
        for value in block["economics"].values()
    )
    verdict = (
        "TOO_FEW_MATCHES" if not enough else "PERSISTS_UNSEEN" if persists else "FAILS_UNSEEN"
    )

    result = {
        "schema": "PUMP_EXACT_ALPHA_SINGLE_FROZEN_RULE_UNSEEN_V1",
        "verdict": verdict,
        "verdictRuleFrozenBeforeOutcomeRead": {
            "TOO_FEW_MATCHES": "matching N <30 or either chronological half matching N <10",
            "PERSISTS_UNSEEN": "adequate N and PF>1, expectancy>0, PnL>0 at both sizes in aggregate and both halves",
            "FAILS_UNSEEN": "adequate N but persistence condition fails",
        },
        "rule": {
            "state": STATE,
            "clauses": [[FEATURE, OPERATOR, THRESHOLD]],
            "ruleN": 1,
            "searched": False,
            "ranked": False,
            "retuned": False,
        },
        "frozenRange": {
            "startOrdinal": START,
            "endOrdinal": args.end,
            "launchN": len(unseen),
            "frozenReadinessGeneratedAtUtc": readiness.get("generatedAtUtc"),
            "frozenReadinessSha256": sha256_file(args.frozen_readiness),
            "prefixThroughEndMintSha256": hashlib.sha256(
                "\n".join(row["mint"] for row in prefix).encode()
            ).hexdigest(),
            "unseenMintSha256": hashlib.sha256(
                "\n".join(row["mint"] for row in unseen).encode()
            ).hexdigest(),
        },
        "accounting": {
            "fixed25MaterializedN": len(fixed_rows),
            "fixed25UnmaterializedN": len(unseen) - len(fixed_rows),
            "matchingUniqueMintN": len(matched),
            "matchedOrdinals": [ordinal_by_mint[row["mint"]] for row in matched],
        },
        "baselineExactLabels": baseline,
        "chronologicalHalves": chronology,
        "breakEvenDeliveryCost": break_even,
        "labelIncludedTotalDeliveryCostSolPerTrade": LABEL_INCLUDED_TOTAL_DELIVERY_SOL,
        "labelCostCoverage": policy["labelCoverageConclusion"],
        "productionCostPolicySha256": sha256_file(args.cost_policy),
        "costAdjusted": stress,
        "sourceRows": row_files,
        "noTouch": {
            "otherRulesEvaluated": 0,
            "scannerRuns": 0,
            "newFeatures": 0,
            "retuning": False,
            "validationRowsInspected": 0,
            "holdoutRowsInspected": 0,
            "serviceChanges": 0,
        },
    }
    write_new(args.output / "single-rule-result.json", result)
    write_new(
        args.output / "code-identity.json",
        {
            "analysisHarnessPath": str(pathlib.Path(__file__)),
            "analysisHarnessSha256": sha256_file(pathlib.Path(__file__)),
            "productionCostPolicySha256": sha256_file(args.cost_policy),
            "frozenReadinessSha256": sha256_file(args.frozen_readiness),
            "discoveryMintIndexSha256AtEvaluation": sha256_file(index_path),
        },
    )
    checksums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(args.output.iterdir())
        if path.name != "SHA256SUMS"
    ]
    (args.output / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
