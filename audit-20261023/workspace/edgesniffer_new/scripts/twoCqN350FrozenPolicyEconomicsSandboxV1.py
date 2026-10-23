#!/usr/bin/env python3
"""Non-authoritative exact-economics sandbox for the frozen N350 WHAT policy."""

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
import sqlite3
import statistics
from typing import Any

import exactCurveEconomicsV1 as exact_engine
import twoCqSymmetricCrossSectionalPolicyV1 as policy_v1
from buildPumpExactAlphaResearchV1 import (
    NETWORK_FEE,
    pair_events,
    retain_pump_trade_arrival,
    retain_target_arrival,
)
from executableOpportunityCensusV1 import evaluate_cell, group_events
from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "2CQ_N350_FROZEN_POLICY_ECONOMICS_SANDBOX_V1"
CONTRACT_SCHEMA = SCHEMA + "_CONTRACT"
DEVELOPMENT_N = 350
LAMPORTS = 1_000_000_000


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def load_frozen_inputs(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise RuntimeError("CONTRACT_SCHEMA_MISMATCH")
    if contract.get("classification") != "NON_AUTHORITATIVE_DEVELOPMENT_SANDBOX":
        raise RuntimeError("SANDBOX_CLASSIFICATION_MISSING")
    if contract["population"].get("validationRowsRead") != 0:
        raise RuntimeError("VALIDATION_ACCESS_FORBIDDEN")
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    if sha256_file(args.policy) != contract["frozenInputs"]["crossSectionalPolicySha256"]:
        raise RuntimeError("POLICY_HASH_MISMATCH")
    if policy.get("policyCountFit") != 1 or policy.get("outcomesRead") != 0:
        raise RuntimeError("FROZEN_POLICY_INVALID")
    if hashlib.sha256(canonical(policy["crossMintModel"])).hexdigest() != contract["frozenInputs"]["crossSectionalModelSha256"]:
        raise RuntimeError("CROSS_SECTIONAL_MODEL_HASH_MISMATCH")
    engine_path = pathlib.Path(exact_engine.__file__).resolve()
    if sha256_file(engine_path) != contract["frozenInputs"]["exactCurveEngineSha256"]:
        raise RuntimeError("EXACT_CURVE_ENGINE_HASH_MISMATCH")
    rows = policy_v1.read_examples(args.development)
    if sha256_file(args.development) != contract["frozenInputs"]["developmentChoiceSetsSha256"]:
        raise RuntimeError("DEVELOPMENT_HASH_MISMATCH")
    if len(rows) != DEVELOPMENT_N:
        raise RuntimeError(f"DEVELOPMENT_NOT_N350:{len(rows)}")
    policy_v1.validate_examples(rows, 1, DEVELOPMENT_N)
    return contract, rows, policy


def score_state(state: dict[str, Any], model: dict[str, Any]) -> float:
    score = float(model["intercept"])
    for name in model["featureNames"]:
        scale = float(model["scales"][name])
        normalized = (float(state["features"][name]) - float(model["means"][name])) / scale
        score += float(model["coefficients"][name]) * normalized
    if not math.isfinite(score):
        raise RuntimeError("NONFINITE_FROZEN_SCORE")
    return score


def choose_states(row: dict[str, Any], policy: dict[str, Any], seed: str) -> dict[str, dict[str, Any]]:
    states = row["states"]
    teacher = next(state for state in states if state["label"])
    scored = [(score_state(state, policy["crossMintModel"]), state) for state in states]
    best = max(score for score, _ in scored)
    winners = [state for score, state in scored if score == best]
    if len(winners) != 1:
        raise RuntimeError(f"EXACT_TOP_SCORE_TIE:{row['eligibleOrdinal']}")
    ordered = sorted(states, key=lambda state: (str(state["mint"]), str(state["stateSignature"])))
    digest = hashlib.sha256(f"{seed}:{row['episodeId']}".encode()).digest()
    blind = ordered[int.from_bytes(digest[:8], "big") % len(ordered)]
    return {
        "TEACHER_SELECTED": teacher,
        "FROZEN_MODEL_TOP1": winners[0],
        "DETERMINISTIC_BLIND_ONE_PER_CHOICE_SET": blind,
    }


def load_source_manifest(
    state_path: pathlib.Path, rows: list[dict[str, Any]], evidence_root: pathlib.Path,
) -> tuple[list[pathlib.Path], dict[str, Any]]:
    signatures = [row["episodeId"] for row in rows]
    connection = sqlite3.connect(f"file:{state_path}?mode=ro", uri=True)
    try:
        database_rows = connection.execute(
            "SELECT signature,detail_json FROM current_decisions "
            "WHERE status='ONLINE_ELIGIBLE' AND choice_json IS NOT NULL "
            "ORDER BY block_time_ms,slot,transaction_index,signature LIMIT 350"
        ).fetchall()
    finally:
        connection.close()
    if [row[0] for row in database_rows] != signatures:
        raise RuntimeError("DEVELOPMENT_DATABASE_MEMBERSHIP_CHANGED")
    declarations: dict[str, dict[str, Any]] = {}
    generation_hashes = set()
    for _, raw_detail in database_rows:
        detail = json.loads(raw_detail)
        generation_hashes.add(str(detail.get("reconciliationGenerationSha256") or ""))
        for reconciliation in detail.get("reconciliation") or []:
            if reconciliation.get("exactReplayComplete") is not True:
                raise RuntimeError("SOURCE_RECONCILIATION_NOT_EXACT")
            for item in reconciliation.get("inputEvidence") or []:
                prior = declarations.get(item["path"])
                if prior is not None and prior.get("sha256") != item.get("sha256"):
                    raise RuntimeError("SOURCE_HASH_DECLARATION_CONFLICT")
                declarations[item["path"]] = item
    paths = []
    for relative, item in sorted(declarations.items()):
        path = evidence_root / relative
        if not path.exists() and path.name == "evidence.frames.partial":
            path = path.with_name("evidence.frames.zst")
        if not path.exists():
            raise RuntimeError(f"SOURCE_EVIDENCE_MISSING:{relative}")
        if int(item.get("size") or path.stat().st_size) != path.stat().st_size:
            raise RuntimeError(f"SOURCE_EVIDENCE_SIZE_CHANGED:{relative}")
        paths.append(path)
    return paths, {
        "pathN": len(paths),
        "totalBytes": sum(path.stat().st_size for path in paths),
        "reconciliationGenerationSha256": sorted(generation_hashes),
        "declaredEvidence": [
            {"path": relative, "size": int(item.get("size") or 0), "sha256": item.get("sha256")}
            for relative, item in sorted(declarations.items())
        ],
    }


def collect_events(paths: list[pathlib.Path], targets: set[str]) -> tuple[dict[str, list[Any]], dict[str, int]]:
    arrivals: dict[str, int] = {}
    arrival_evidence: dict[str, str] = {}
    by_mint: dict[str, list[Any]] = collections.defaultdict(list)
    seen_finalized = set()
    counters = collections.Counter()
    for path in paths:
        counters["sourcePathsRead"] += 1
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            kind = metadata.get("kind")
            signature = str(metadata.get("signature") or "")
            if kind == "transaction" and signature:
                observed_ms = int(metadata.get("receivedAtUnixNs") or 0) // 1_000_000
                evidence_id = str(metadata.get("evidenceId") or "")
                kinds = set(metadata.get("pumpInstructionKinds") or [])
                retain_pump_trade_arrival(arrivals, arrival_evidence, signature, observed_ms, evidence_id, kinds)
                discovered = targets.intersection(metadata.get("discoveredMints") or [])
                if discovered:
                    retain_target_arrival(arrivals, arrival_evidence, signature, observed_ms, evidence_id, discovered)
                    counters["targetArrivalFrames"] += 1
            elif kind == "rpc_finalized_transaction" and signature and targets.intersection(metadata.get("discoveredMints") or []):
                counters["targetFinalizedFrames"] += 1
                if signature in seen_finalized:
                    continue
                seen_finalized.add(signature)
                arrival = arrivals.get(signature)
                if arrival is None:
                    counters["missingRealtimeArrival"] += 1
                    continue
                try:
                    transaction = json.loads(raw).get("result")
                    if not transaction or (transaction.get("meta") or {}).get("err") is not None:
                        counters["failedOrMissingFinalized"] += 1
                        continue
                    decoded = pair_events(
                        transaction,
                        signature,
                        arrival_evidence.get(signature, ""),
                        str(metadata.get("evidenceId") or ""),
                        arrival,
                        int(metadata.get("slot") or 0),
                        int(metadata.get("transactionIndex") or 0),
                        targets,
                    )
                except Exception:
                    counters["decodeFailures"] += 1
                    continue
                for event in decoded:
                    if event.event.mint in targets:
                        by_mint[event.event.mint].append(event)
                        counters["decodedPumpEvents"] += 1
    for mint in list(by_mint):
        deduped = {(event.signature, event.instruction_index): event for event in by_mint[mint]}
        by_mint[mint] = sorted(
            deduped.values(),
            key=lambda event: (event.arrival_ms, event.slot, event.tx_index, event.instruction_index),
        )
    counters["targetMintsWithEvents"] = len(by_mint)
    return by_mint, dict(counters)


def exact_cell(
    row: dict[str, Any], state: dict[str, Any], events: list[Any], size: float,
    scenario: str, latency: dict[str, Any], overlay_sol: float,
) -> dict[str, Any]:
    if not events:
        return {"status": "EXCLUDED", "reason": "NO_EXACT_EVENTS"}
    matching = [
        event for event in events
        if event.signature == state["stateSignature"]
        and event.instruction_index == int(state["stateInstructionIndex"])
    ]
    if len(matching) != 1:
        return {"status": "EXCLUDED", "reason": "STATE_EVENT_NOT_UNIQUELY_RECONSTRUCTED"}
    groups = group_events(events)
    group_index = next(
        (index for index, group in enumerate(groups) if group["signature"] == state["stateSignature"]),
        None,
    )
    if group_index is None:
        return {"status": "EXCLUDED", "reason": "STATE_GROUP_NOT_FOUND"}
    matched = matching[0]
    if groups[group_index]["rows"][-1].instruction_index != matched.instruction_index:
        return {"status": "EXCLUDED", "reason": "STATE_INSTRUCTION_NOT_GROUP_TERMINAL"}
    decision_ms = int(row["decisionBoundary"]["arrivalUnixNs"]) // 1_000_000
    decision = {
        "decisionMs": decision_ms,
        "baseState": matched.event.post_state,
        "feeEvent": matched.event,
        "futureIndex": group_index + 1,
    }
    cell = evaluate_cell(
        decision,
        groups,
        size,
        scenario,
        int(latency["entryOffsetMs"]),
        int(latency["firstSellableOffsetMs"]),
        int(latency["sellLandingOffsetMs"]),
        [overlay_sol],
        [10.0],
    )
    if cell.get("status") != "EXACT":
        return {"status": "EXCLUDED", "reason": cell.get("reason") or cell.get("status")}
    policy = cell["policies"]["TP20_SL20_MAX120"]
    if policy.get("status") != "EXACT":
        return {"status": "EXCLUDED", "reason": policy.get("reason") or policy.get("status")}
    capital_sol = size + NETWORK_FEE / LAMPORTS
    adjusted_pnl = float(policy["pnlSol"]) - overlay_sol
    adjusted_return = adjusted_pnl * 100 / capital_sol
    return {
        "status": "EXACT",
        "mint": state["mint"],
        "decisionUnixMs": decision_ms,
        "entryUnixMs": cell["entryMs"],
        "firstSellableUnixMs": cell["firstSellableMs"],
        "exitTriggerUnixMs": policy["triggerAtMs"],
        "exitLandingUnixMs": policy["landingAtMs"],
        "outcome": policy["outcome"],
        "returnPctBeforeDeliveryOverlay": policy["returnPct"],
        "pnlSolBeforeDeliveryOverlay": policy["pnlSol"],
        "returnPct": adjusted_return,
        "pnlSol": adjusted_pnl,
        "tokensReceivedRaw": cell["tokensReceivedRaw"],
        "movementBeforeEntryInventoryDeteriorationPct": cell["movementBeforeEntryInventoryDeteriorationPct"],
        "counterfactualAttemptedTransactions": cell["counterfactualAttemptedTransactions"],
        "counterfactualFailedTransactions": cell["counterfactualFailedTransactions"],
        "counterfactualDivergenceRate": cell["counterfactualDivergenceRate"],
    }


def metrics(rows: list[dict[str, Any]], requested_n: int | None = None) -> dict[str, Any]:
    exact = [row for row in rows if row.get("status") == "EXACT"]
    pnls = [float(row["pnlSol"]) for row in exact]
    rois = [float(row["returnPct"]) for row in exact]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value <= 0]
    ordered_wins = sorted(wins, reverse=True)
    return {
        "requestedN": len(rows) if requested_n is None else requested_n,
        "exactEvaluableN": len(exact),
        "excludedN": (len(rows) if requested_n is None else requested_n) - len(exact),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": len(wins) / len(exact) if exact else None,
        "profitFactor": sum(wins) / abs(sum(losses)) if losses else (None if not wins else math.inf),
        "expectancyRoiPct": statistics.fmean(rois) if rois else None,
        "totalExactPnlSol": sum(pnls),
        "medianRoiPct": statistics.median(rois) if rois else None,
        "p25RoiPct": quantile(rois, 0.25),
        "p75RoiPct": quantile(rois, 0.75),
        "worstRoiPct": min(rois) if rois else None,
        "bestRoiPct": max(rois) if rois else None,
        "tpCount": sum(row["outcome"] == "TP" for row in exact),
        "slCount": sum(row["outcome"] == "SL" for row in exact),
        "maxHoldCount": sum(row["outcome"] == "MAX_HOLD" for row in exact),
        "meanCounterfactualDivergenceRate": statistics.fmean(
            float(row["counterfactualDivergenceRate"]) for row in exact
        ) if exact else None,
        "topWinnerShareOfGrossProfit": (
            ordered_wins[0] / sum(wins) if ordered_wins and sum(wins) else None
        ),
        "top3WinnerShareOfGrossProfit": (
            sum(ordered_wins[:3]) / sum(wins) if ordered_wins and sum(wins) else None
        ),
    }


def max_open_one(rows: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = []
    rejected = 0
    flat_at = -1
    for row in sorted(rows, key=lambda item: (int(item.get("entryUnixMs") or 0), int(item["eligibleOrdinal"]))):
        if row.get("status") != "EXACT":
            continue
        if int(row["entryUnixMs"]) < flat_at:
            rejected += 1
            continue
        accepted.append(row)
        flat_at = int(row["exitLandingUnixMs"])
    return {"accepted": len(accepted), "rejectedDueToOpenPosition": rejected, "metrics": metrics(accepted)}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 2CQ N350 Frozen Policy Economics Sandbox V1",
        "",
        "`NON_AUTHORITATIVE_DEVELOPMENT_SANDBOX`",
        "",
        "> This report is conditional on the teacher's decision boundary and cannot alter the frozen WHAT policy, the sealed N150, or any promotion gate.",
        "",
        "## Scope",
        "",
        f"- Development ordinals: 1-350",
        f"- Validation rows read: {report['integrity']['validationRowsRead']}",
        f"- Outcomes read from validation: 0",
        f"- Model refits: {report['integrity']['modelRefits']}",
        f"- Trade submissions: {report['integrity']['submissions']}",
        "",
        "## Results",
        "",
        "| Population | Scenario | Size | Exact N | WR | PF | Expectancy | PnL SOL | Median ROI | Worst ROI |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, value in report["metrics"].items():
        role, scenario, size = key.split("|")
        def f(number: Any, suffix: str = "") -> str:
            return "n/a" if number is None else f"{number:.3f}{suffix}"
        lines.append(
            f"| {role} | {scenario} | {size} | {value['exactEvaluableN']} | "
            f"{f(None if value['winRate'] is None else 100*value['winRate'], '%')} | "
            f"{f(value['profitFactor'])} | {f(value['expectancyRoiPct'], '%')} | "
            f"{f(value['totalExactPnlSol'])} | {f(value['medianRoiPct'], '%')} | {f(value['worstRoiPct'], '%')} |"
        )
    lines.extend([
        "",
        "## Interpretation Boundary",
        "",
        "This is a descriptive development-only side note. It is not an autonomous strategy backtest because the decision clock is conditioned on 2CQ's action boundary. It cannot be used to tune, promote, reject, or reinterpret the frozen policy before its one-time sealed N150 validation.",
        "",
    ])
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    contract, development, policy = load_frozen_inputs(args)
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "report.json").exists():
        raise RuntimeError("IMMUTABLE_SANDBOX_OUTPUT_ALREADY_COMPLETE")
    paths, source_manifest = load_source_manifest(args.state, development, args.evidence_root)
    identity = {
        "schema": SCHEMA + "_RUN_IDENTITY",
        "classification": "NON_AUTHORITATIVE_DEVELOPMENT_SANDBOX",
        "contractSha256": sha256_file(args.contract),
        "developmentSha256": sha256_file(args.development),
        "policySha256": sha256_file(args.policy),
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "developmentOrdinals": [1, 350],
        "validationRowsRead": 0,
        "outcomesReadBeforeIdentityFreeze": 0,
    }
    identity_path = args.output / "run-identity.json"
    if identity_path.exists():
        if json.loads(identity_path.read_text(encoding="utf-8")) != identity:
            raise RuntimeError("IMMUTABLE_RUN_IDENTITY_MISMATCH")
    else:
        atomic_json(identity_path, identity)
    contract_copy = args.output / "contract.json"
    if contract_copy.exists():
        if sha256_file(contract_copy) != sha256_file(args.contract):
            raise RuntimeError("IMMUTABLE_CONTRACT_COPY_MISMATCH")
    else:
        contract_copy.write_bytes(args.contract.read_bytes())

    selections = []
    targets = set()
    same_model_teacher = 0
    for row in development:
        chosen = choose_states(row, policy, contract["frozenInputs"]["blindBaselineSeed"])
        same_model_teacher += chosen["FROZEN_MODEL_TOP1"]["mint"] == chosen["TEACHER_SELECTED"]["mint"]
        for role, state in chosen.items():
            targets.add(state["mint"])
            selections.append({"row": row, "role": role, "state": state})

    events_by_mint, collection_counters = collect_events(paths, targets)
    result_rows = []
    exclusion_reasons = collections.Counter()
    for selection in selections:
        row, role, state = selection["row"], selection["role"], selection["state"]
        for scenario, latency in contract["latencyScenarios"].items():
            for size in contract["economics"]["sizesSol"]:
                result = exact_cell(
                    row, state, events_by_mint.get(state["mint"], []), float(size),
                    scenario, latency, float(contract["economics"]["roundTripDeliveryOverlaySol"]),
                )
                result.update({
                    "eligibleOrdinal": int(row["eligibleOrdinal"]),
                    "episodeId": row["episodeId"],
                    "role": role,
                    "scenario": scenario,
                    "sizeSol": float(size),
                    "teacherMint": row["mint"],
                    "selectedMint": state["mint"],
                })
                if result["status"] != "EXACT":
                    exclusion_reasons[result.get("reason") or "UNKNOWN"] += 1
                result_rows.append(result)

    rows_path = args.output / "rows.jsonl.gz"
    rows_temporary = rows_path.with_suffix(rows_path.suffix + ".tmp")
    with gzip.open(rows_temporary, "wt", encoding="utf-8") as stream:
        for row in result_rows:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(rows_temporary, rows_path)

    aggregate_metrics = {}
    max_open = {}
    for role in contract["populations"]:
        for scenario in contract["latencyScenarios"]:
            for size in contract["economics"]["sizesSol"]:
                subset = [
                    row for row in result_rows
                    if row["role"] == role and row["scenario"] == scenario and row["sizeSol"] == float(size)
                ]
                key = f"{role}|{scenario}|{float(size):.1f}"
                aggregate_metrics[key] = metrics(subset, DEVELOPMENT_N)
                max_open[key] = max_open_one(subset)

    matched_metrics = {}
    for scenario in contract["latencyScenarios"]:
        for size in contract["economics"]["sizesSol"]:
            cell_rows = [
                row for row in result_rows
                if row["scenario"] == scenario and row["sizeSol"] == float(size)
            ]
            exact_by_role = {
                role: {row["eligibleOrdinal"] for row in cell_rows if row["role"] == role and row["status"] == "EXACT"}
                for role in contract["populations"]
            }
            common = set.intersection(*exact_by_role.values())
            matched_metrics[f"{scenario}|{float(size):.1f}"] = {
                "matchedEpisodeN": len(common),
                "byPopulation": {
                    role: metrics([
                        row for row in cell_rows if row["role"] == role and row["eligibleOrdinal"] in common
                    ], len(common))
                    for role in contract["populations"]
                },
            }

    report = {
        "schema": SCHEMA + "_REPORT",
        "classification": "NON_AUTHORITATIVE_DEVELOPMENT_SANDBOX",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "identity": identity,
        "population": {
            "developmentN": DEVELOPMENT_N,
            "uniqueSelectedMintsAcrossPopulations": len(targets),
            "modelTeacherSameMintN": same_model_teacher,
            "modelTeacherSameMintRate": same_model_teacher / DEVELOPMENT_N,
        },
        "sourceEvidence": source_manifest,
        "collectionCounters": collection_counters,
        "metrics": aggregate_metrics,
        "matchedEpisodeMetrics": matched_metrics,
        "maxOpen1": max_open,
        "exclusionsByReasonAcrossCells": dict(exclusion_reasons),
        "integrity": {
            "developmentOrdinalsRead": [1, 350],
            "validationRowsRead": 0,
            "modelRefits": 0,
            "thresholdSearches": 0,
            "exitPolicySearches": 0,
            "submissions": 0,
            "authoritativePolicyModified": False,
            "authoritativeResearchInfluencePermitted": False,
            "autonomousTimingClaim": False,
        },
        "interpretation": contract["interpretation"],
    }
    atomic_json(args.output / "report.json", report)
    (args.output / "REPORT.md").write_text(render_markdown(report), encoding="utf-8")
    atomic_json(args.output / "manifest.json", {
        "schema": SCHEMA + "_MANIFEST",
        "contractSha256": sha256_file(args.output / "contract.json"),
        "runIdentitySha256": sha256_file(args.output / "run-identity.json"),
        "rowsSha256": sha256_file(rows_path),
        "reportSha256": sha256_file(args.output / "report.json"),
        "markdownSha256": sha256_file(args.output / "REPORT.md"),
        "classification": "NON_AUTHORITATIVE_DEVELOPMENT_SANDBOX",
        "validationRowsRead": 0,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--development", type=pathlib.Path, required=True)
    parser.add_argument("--policy", type=pathlib.Path, required=True)
    parser.add_argument("--state", type=pathlib.Path, required=True)
    parser.add_argument("--evidence-root", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    report = run(args)
    print(json.dumps({
        "classification": report["classification"],
        "population": report["population"],
        "metrics": report["metrics"],
        "integrity": report["integrity"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
