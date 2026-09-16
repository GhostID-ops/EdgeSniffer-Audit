#!/usr/bin/env python3
"""Outcome-blind multi-archetype research views over the prospective 2CQ tape.

The frozen LAUNCHED_120S cohort remains authoritative and untouched.  This
module emits an all-route decision tape and a separate all-age SOL Pump choice
population whose candidates had recent causal activity before the teacher
action.  It never reads outcomes or validation rows.
"""

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
from typing import Any, Iterable

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqDecisionChoiceSetV1 as choice_legacy
import twoCqDecisionSystemDecompositionV1 as decision
import twoCqIndexedCausalStatePlaneV1 as state_plane
import twoCqProspectiveSymmetricChoiceMaterializerV1 as legacy
import twoCqProspectiveSymmetricChoiceMaterializerV2 as launched


SCHEMA = "2CQ_MULTI_ARCHETYPE_RESEARCH_V1"
IMPLEMENTATION_VERSION = "1.1.0"
ACTIVE_WINDOW_MS = 5_000
ACTIVE_WINDOW_NS = ACTIVE_WINDOW_MS * 1_000_000
SUPPORTED_SOL_FAMILIES = {"SOL_PUMP_CURVE", "SOL_PUMP_CPI"}
ACTIVE_FEATURES = tuple(
    name for name in timing.FULL_FEATURES
    if name not in {
        "log_age_ms",
        "curve_progress",
        "distance_from_launch_log_price",
        "total_external_buy_sol",
        "total_external_sell_sol",
        "total_external_net_sol",
        "unique_buyers_total",
        "unique_sellers_total",
    }
)


ROUTE_CAPABILITIES = {
    "SOL_PUMP_CURVE": {
        "acquisitionClassification": True,
        "causalState": True,
        "symmetricControls": True,
        "exactEconomics": True,
        "localExecution": True,
        "researchStatus": "SUPPORTED",
    },
    "SOL_PUMP_CPI": {
        "acquisitionClassification": True,
        "causalState": True,
        "symmetricControls": True,
        "exactEconomics": True,
        "localExecution": True,
        "researchStatus": "SUPPORTED",
    },
    "NON_SOL_PUMP_V2": {
        "acquisitionClassification": True,
        "causalState": True,
        "symmetricControls": True,
        "exactEconomics": True,
        "localExecution": False,
        "researchStatus": "QUOTE_MINT_SCOPED_STATE_PLANE_COLLECTING",
    },
    "PUMPSWAP": {
        "acquisitionClassification": True,
        "causalState": True,
        "symmetricControls": True,
        "exactEconomics": True,
        "localExecution": True,
        "researchStatus": "TEACHER_SELECTED_POOL_HISTORY_COLLECTING",
    },
    "PUMPSWAP_CPI": {
        "acquisitionClassification": True,
        "causalState": True,
        "symmetricControls": True,
        "exactEconomics": True,
        "localExecution": True,
        "researchStatus": "TEACHER_SELECTED_POOL_HISTORY_COLLECTING",
    },
    "ROUTER": {
        "acquisitionClassification": True,
        "causalState": False,
        "symmetricControls": False,
        "exactEconomics": False,
        "localExecution": False,
        "researchStatus": "UNDERLYING_VENUE_RESOLUTION_ACTIVE_ADAPTER_DEPENDENT",
    },
    "OTHER_DEX": {
        "acquisitionClassification": True,
        "causalState": False,
        "symmetricControls": False,
        "exactEconomics": False,
        "localExecution": False,
        "researchStatus": "LAUNCHLAB_EXACT_OTHER_VENUES_FAIL_CLOSED",
    },
    "AMBIGUOUS_PUMP_FAMILY": {
        "acquisitionClassification": False,
        "causalState": False,
        "symmetricControls": False,
        "exactEconomics": False,
        "localExecution": False,
        "researchStatus": "FAIL_CLOSED",
    },
}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
    os.replace(temporary, path)


def write_jsonl_gz(path: pathlib.Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            for row in rows:
                stream.write(canonical(row) + b"\n")
    os.replace(temporary, path)


def load_candidates(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open("rt", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            signature = str(row.get("signature") or "")
            if signature and str(row.get("source")) == "allenhark-subscribe-entries":
                rows.setdefault(signature, row)
    return rows


def active_features(
    state: Any,
    boundary_ns: int,
    mint: str,
    creator: str,
    prior: list[decision.CausalEvent],
) -> dict[str, float]:
    """Use only state and flow available in the bounded active window."""
    first_ns = min(row.received_ns for row in prior)
    features = decision.state_features(state, boundary_ns, mint, creator, prior, first_ns)
    return {name: float(features[name]) for name in ACTIVE_FEATURES}


def build_active_sol_choice(
    selected: dict[str, Any],
    events: dict[str, list[decision.CausalEvent]],
    incomplete: dict[str, list[tuple[tuple[int, int, int, str], str]]],
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    boundary_ns = int(selected["entryReceivedAtUnixNs"])
    boundary_order = (
        int(selected["entrySlot"]), int(selected["entryTransactionIndex"]), -1, ""
    )
    selected_mint = str(selected["mint"])
    choices: list[dict[str, Any]] = []
    exclusions: collections.Counter[str] = collections.Counter()
    selected_exclusions: set[str] = set()

    for mint, all_events in events.items():
        prior = [
            row for row in all_events
            if launched.causal_before(row, boundary_ns, boundary_order)
            and row.received_ns >= boundary_ns - legacy.MAX_AGE_NS
        ]
        external = [row for row in prior if row.user != decision.WALLET]
        if not external or boundary_ns - external[-1].received_ns > ACTIVE_WINDOW_NS:
            continue
        missing = {
            reason for order, reason in incomplete.get(mint, [])
            if order < boundary_order and reason != "ALLENHARK_TRADE_ARRIVAL_MISSING"
        }
        if missing:
            exclusions.update(missing)
            if mint == selected_mint:
                selected_exclusions.update(missing)
            continue
        if not launched.is_sol_curve_sequence(prior):
            exclusions["NON_SOL_OR_UNSUPPORTED_CURVE_STATE"] += 1
            if mint == selected_mint:
                selected_exclusions.add("NON_SOL_OR_UNSUPPORTED_CURVE_STATE")
            continue
        if not legacy.reserve_continuous(prior):
            exclusions["ACTIVE_WINDOW_RESERVE_CONTINUITY_INCOMPLETE"] += 1
            if mint == selected_mint:
                selected_exclusions.add("ACTIVE_WINDOW_RESERVE_CONTINUITY_INCOMPLETE")
            continue
        if choice_legacy.teacher_exposure_raw(prior) > 1:
            exclusions["TEACHER_ALREADY_EXPOSED"] += 1
            if mint == selected_mint:
                selected_exclusions.add("TEACHER_ALREADY_EXPOSED")
            continue
        last = prior[-1]
        if int(last.post_state.real_token) <= 0:
            exclusions["CURVE_NOT_EXECUTABLE"] += 1
            if mint == selected_mint:
                selected_exclusions.add("CURVE_NOT_EXECUTABLE")
            continue
        choices.append({
            "label": mint == selected_mint,
            "mint": mint,
            "economicFamily": "SOL_PUMP_ACTIVE_STATE",
            "stateSignature": last.signature,
            "stateSlot": last.slot,
            "stateTransactionIndex": last.transaction_index,
            "stateInstructionIndex": last.instruction_index,
            "stateReceivedAtUnixNs": last.received_ns,
            "lastExternalAgeMs": (boundary_ns - external[-1].received_ns) / 1_000_000,
            "features": active_features(
                last.post_state, boundary_ns, mint, last.creator, prior
            ),
        })

    chosen = [row for row in choices if row["label"]]
    reasons: list[str] = []
    if len(chosen) != 1:
        reasons.append("SELECTED_MINT_ABSENT_FROM_ACTIVE_SOL_UNIVERSE")
    if len(choices) <= 1:
        reasons.append("NO_CONTEMPORANEOUS_ACTIVE_SOL_ALTERNATIVE")
    detail = {
        "activeWindowMs": ACTIVE_WINDOW_MS,
        "eligibleCandidateN": len(choices),
        "controlN": max(0, len(choices) - len(chosen)),
        "candidateExclusions": dict(sorted(exclusions.items())),
        "selectedExclusionReasons": sorted(selected_exclusions),
    }
    if reasons:
        return None, reasons, detail
    return {
        "schema": SCHEMA + "_ACTIVE_SOL_CHOICE",
        "episodeId": selected["signature"],
        "signature": selected["signature"],
        "mint": selected_mint,
        "route": selected["route"],
        "economicFamily": selected["economicFamily"],
        "universe": f"ACTIVE_SOL_PUMP_{ACTIVE_WINDOW_MS}MS",
        "decisionBoundary": {
            "arrivalUnixNs": boundary_ns,
            "slot": selected["entrySlot"],
            "transactionIndex": selected["entryTransactionIndex"],
            "source": "allenhark-subscribe-entries:capture-first",
        },
        "controlN": len(choices) - 1,
        "states": choices,
        "evidenceSummary": detail,
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "submissions": 0,
    }, [], detail


def model_feasibility(
    rows: list[dict[str, Any]],
    feature_names: tuple[str, ...],
    population: str,
) -> dict[str, Any]:
    """Tiny-N descriptive split; this is never a frozen policy."""
    if len(rows) < 25:
        return {
            "classification": "TOO_FEW_INDEPENDENT_DECISIONS",
            "N": len(rows),
            "minimumN": 25,
            "population": population,
        }
    split = max(20, math.floor(len(rows) * 0.70))
    split = min(split, len(rows) - 5)
    train = rows[:split]
    train_mints = {row["mint"] for row in train}
    assessment = [row for row in rows[split:] if row["mint"] not in train_mints]
    if len(assessment) < 5:
        return {
            "classification": "TOO_FEW_CHRONOLOGICAL_ASSESSMENT_DECISIONS",
            "N": len(rows), "trainN": len(train), "assessmentN": len(assessment),
            "population": population,
        }
    capped = [decision.fit_control_sample(row) for row in train]
    report, _ = timing.evaluate_model(capped, assessment, feature_names)
    ranking = report["validation"]["withinMint"]
    return {
        "classification": "TINY_N_DEVELOPMENT_ONLY_DESCRIPTIVE",
        "population": population,
        "N": len(rows),
        "trainN": len(train),
        "assessmentN": len(assessment),
        "featureN": len(feature_names),
        "rocAuc": report["validation"]["rocAuc"],
        "top1Pct": ranking["actualRankedFirstPct"],
        "top3Pct": ranking["actualRankedTop3Pct"],
        "chanceTop1Pct": ranking["chanceRankedFirstPct"],
        "meanReciprocalRank": ranking["meanReciprocalRank"],
        "topCoefficients": report["coefficients"][:10],
        "notFrozen": True,
        "validationRowsRead": 0,
        "outcomesRead": 0,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{args.v2_state}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    rows = list(source.execute(
        "SELECT * FROM current_decisions ORDER BY block_time_ms,slot,transaction_index,signature"
    ))
    source.close()
    source_identity = hashlib.sha256(canonical([
        [str(row["signature"]), str(row["generation_sha"]), str(row["status"]), str(row["reasons_json"])]
        for row in rows
    ])).hexdigest()
    current_report = output / "report.json"
    if current_report.exists():
        previous = json.loads(current_report.read_text(encoding="utf-8"))
        if (
            previous.get("sourceDecisionIdentitySha256") == source_identity
            and previous.get("implementationVersion") == IMPLEMENTATION_VERSION
        ):
            return previous
    candidates = load_candidates(args.candidates)

    launched_rows = [
        json.loads(str(row["choice_json"]))
        for row in rows
        if str(row["status"]) == "ONLINE_ELIGIBLE" and row["choice_json"]
    ]
    launched_rows.sort(key=lambda row: (
        int(row["decisionBoundary"]["arrivalUnixNs"]), str(row["signature"])
    ))

    tape = []
    route_counts: collections.Counter[str] = collections.Counter()
    for row in rows:
        family = str(row["economic_family"])
        route_counts[family] += 1
        tape.append({
            "schema": SCHEMA + "_DECISION_TAPE",
            "signature": str(row["signature"]),
            "mint": str(row["mint"]),
            "blockTimeMs": int(row["block_time_ms"]),
            "slot": int(row["slot"]),
            "transactionIndex": int(row["transaction_index"]),
            "route": str(row["route"]),
            "economicFamily": family,
            "familyCapability": ROUTE_CAPABILITIES.get(family, ROUTE_CAPABILITIES["AMBIGUOUS_PUMP_FAMILY"]),
            "sourceV2Status": str(row["status"]),
            "sourceV2Reasons": json.loads(str(row["reasons_json"])),
            "sourceGenerationSha256": str(row["generation_sha"]),
            "outcomesRead": 0,
            "submissions": 0,
        })
    write_jsonl_gz(output / "all-route-decision-tape.jsonl.gz", tape)

    eligible_source = []
    selected_by_signature: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = str(row["economic_family"])
        signature = str(row["signature"])
        candidate = candidates.get(signature)
        if family not in SUPPORTED_SOL_FAMILIES or row["status"] == "DEFERRED" or candidate is None:
            continue
        reasons = set(json.loads(str(row["reasons_json"])))
        if reasons.intersection({
            "PAUSED_INVALID_COLLECTION_INTERVAL",
            "GLOBAL_REALTIME_CONTINUITY_NOT_PROVEN_FOR_120S_UNIVERSE",
            "CANONICAL_ACQUISITION_WITHOUT_REALTIME_CANDIDATE",
        }):
            continue
        selected_by_signature[signature] = {
            "signature": signature,
            "mint": str(row["mint"]),
            "route": str(row["route"]),
            "economicFamily": family,
            "entryReceivedAtUnixNs": int(candidate["receivedAtUnixNs"]),
            "entrySlot": int(candidate["slot"]),
            "entryTransactionIndex": int(row["transaction_index"]),
            "generationSha256": str(row["generation_sha"]),
        }
        eligible_source.append(row)

    boundaries = [row["entryReceivedAtUnixNs"] for row in selected_by_signature.values()]
    plane = sqlite3.connect(args.state_plane)
    try:
        events, _, incomplete, plane_evidence = state_plane.materialize_universe(
            plane, args.compact_arrivals, boundaries
        )
    finally:
        plane.close()

    active_rows = []
    active_exclusions: collections.Counter[str] = collections.Counter()
    for row in eligible_source:
        selected = selected_by_signature[str(row["signature"])]
        choice, reasons, detail = build_active_sol_choice(selected, events, incomplete)
        if choice is None:
            active_exclusions.update(reasons)
            active_exclusions.update(detail["selectedExclusionReasons"])
            continue
        choice["sourceProof"] = {
            "sourceV2GenerationSha256": selected["generationSha256"],
            "statePlaneSha256": sha256_file(args.state_plane),
            "sourceContractSha256": sha256_file(args.v2_contract),
        }
        active_rows.append(choice)
    active_rows.sort(key=lambda row: (
        int(row["decisionBoundary"]["arrivalUnixNs"]), row["signature"]
    ))
    for ordinal, row in enumerate(active_rows, 1):
        row["developmentOrdinal"] = ordinal
        row["split"] = "DEVELOPMENT_ONLY_UNTIL_SEPARATE_FREEZE"
    write_jsonl_gz(output / "active-sol-pump-5000ms-choice-sets.jsonl.gz", active_rows)

    launched_feasibility = model_feasibility(
        launched_rows, tuple(timing.FULL_FEATURES), "LAUNCHED_120S"
    )
    feasibility = model_feasibility(
        active_rows, ACTIVE_FEATURES, f"ACTIVE_SOL_PUMP_{ACTIVE_WINDOW_MS}MS"
    )
    report = {
        "schema": SCHEMA + "_REPORT",
        "implementationVersion": IMPLEMENTATION_VERSION,
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "purpose": "linked route census plus all-age recent-active SOL Pump choice feasibility",
        "sourceDecisionIdentitySha256": source_identity,
        "sourceDecisionN": len(rows),
        "routeCounts": dict(sorted(route_counts.items())),
        "routeCapabilities": ROUTE_CAPABILITIES,
        "activeSol": {
            "sourceMatureCompatibleN": len(eligible_source),
            "eligibleN": len(active_rows),
            "exclusions": dict(sorted(active_exclusions.items())),
            "windowMs": ACTIVE_WINDOW_MS,
            "requiresRealtimeCreate": False,
            "featureNames": list(ACTIVE_FEATURES),
            "statePlaneEvidence": plane_evidence,
        },
        "launched120": {
            "eligibleN": len(launched_rows),
            "fingerprintFeasibility": launched_feasibility,
        },
        "fingerprintFeasibility": feasibility,
        "integrity": {
            "currentLaunched120CohortModified": False,
            "validationRowsRead": 0,
            "outcomesRead": 0,
            "submissions": 0,
            "liveTradingModified": False,
        },
    }
    atomic_json(output / "report.json", report)
    atomic_json(output / "route-capability-matrix.json", {
        "schema": SCHEMA + "_ROUTE_CAPABILITY_MATRIX",
        "capabilities": ROUTE_CAPABILITIES,
    })
    atomic_json(output / "provenance.json", {
        "schema": SCHEMA + "_PROVENANCE",
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "v2StateSha256": sha256_file(args.v2_state),
        "v2ContractSha256": sha256_file(args.v2_contract),
        "statePlaneSha256": sha256_file(args.state_plane),
        "artifacts": {
            name: sha256_file(output / name)
            for name in (
                "all-route-decision-tape.jsonl.gz",
                "active-sol-pump-5000ms-choice-sets.jsonl.gz",
                "report.json",
                "route-capability-matrix.json",
            )
        },
        "validationRowsRead": 0,
        "outcomesRead": 0,
        "submissions": 0,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v2-state", type=pathlib.Path, required=True)
    parser.add_argument("--v2-contract", type=pathlib.Path, required=True)
    parser.add_argument("--state-plane", type=pathlib.Path, required=True)
    parser.add_argument("--candidates", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
