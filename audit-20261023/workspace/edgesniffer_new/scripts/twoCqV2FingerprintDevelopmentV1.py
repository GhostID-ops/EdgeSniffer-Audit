#!/usr/bin/env python3
"""Outcome-blind development study for the frozen prospective 2CQ V2 cohort.

The only readable allocation bucket is FINGERPRINT_V2_DEVELOPMENT_N350.  The
script refuses every other bucket before opening any referenced evidence.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import pathlib
import sqlite3
import statistics
from typing import Any

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqFullMetaCanonicalStatePlaneV1 as state_plane
import twoCqProspectiveDatasetAllocationV1 as allocation
import twoCqProspectiveSymmetricChoiceMaterializerV1 as materializer

BUCKET = "FINGERPRINT_V2_DEVELOPMENT_N350"
ORDINALS = list(range(151, 501))
SCHEMA = "2CQ_FINGERPRINT_V2_DEVELOPMENT_STUDY_V1"
FORBIDDEN = ("pnl", "profit", "outcome", "return", "roi", "sell", "exit")
COMPACT_FEATURES = (
    "log_age_ms", "curve_progress", "log_market_cap_sol", "net_flow_sol_1000ms",
    "buy_sell_imbalance_1000ms", "unique_buyers_1000ms", "buyer_hhi_1000ms",
    "log_price_change_1000ms",
)


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def outcome_blind(value: Any, where: str = "root") -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            low = str(k).lower()
            if low in {"outcomeblind", "outcome_blind"}:
                if v is not True:
                    raise RuntimeError(f"OUTCOME_BLIND_ATTESTATION_INVALID:{where}.{k}")
                continue
            if low in {"pnlread", "pnl_read", "economicoutcomesread", "economic_outcomes_read"}:
                if int(v or 0) != 0:
                    raise RuntimeError(f"OUTCOME_ACCESS_TAINT:{where}.{k}")
                continue
            if any(token in low for token in FORBIDDEN):
                raise RuntimeError(f"FORBIDDEN_FIELD:{where}.{k}")
            outcome_blind(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            outcome_blind(v, f"{where}[{i}]")


def load_entries(root: pathlib.Path) -> tuple[list[dict[str, Any]], list[str]]:
    directory = root / "manifests" / BUCKET / "batches"
    entries, hashes, previous = [], [], None
    for sequence, path in enumerate(sorted(directory.glob("*.json")), 1):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("bucket") != BUCKET or payload.get("sequence") != sequence:
            raise RuntimeError(f"DEVELOPMENT_MANIFEST_INVALID:{path}")
        if payload.get("previousManifestSha256") != previous:
            raise RuntimeError(f"DEVELOPMENT_MANIFEST_CHAIN_INVALID:{path}")
        previous = sha256(path)
        hashes.append(previous)
        entries.extend(payload.get("entries") or [])
    ordinals = [int(x.get("ordinal") or 0) for x in entries]
    if ordinals != ORDINALS:
        raise RuntimeError("DEVELOPMENT_ORDINALS_NOT_EXACT_151_500")
    if any(x.get("assignment", {}).get("bucket") != BUCKET for x in entries):
        raise RuntimeError("NON_DEVELOPMENT_ENTRY_ATTEMPTED")
    if len({(x["signature"], x["mint"]) for x in entries}) != 350:
        raise RuntimeError("DEVELOPMENT_DUPLICATE_IDENTITY")
    if [(int(x["realtimeReceiptUnixNs"]), x["signature"], x["mint"]) for x in entries] != sorted(
        (int(x["realtimeReceiptUnixNs"]), x["signature"], x["mint"]) for x in entries
    ):
        raise RuntimeError("DEVELOPMENT_CHRONOLOGY_INVALID")
    for x in entries:
        for path_key, hash_key in (("eligibilityArtifactReference", "eligibilityArtifactSha256"),
                                   ("full120sUniverseReference", "full120sUniverseSha256"),
                                   ("windowPromotionManifestReference", "windowPromotionManifestSha256")):
            p = pathlib.Path(x[path_key])
            if not p.exists() or sha256(p) != x[hash_key]:
                raise RuntimeError(f"DEVELOPMENT_EVIDENCE_HASH_INVALID:{x['ordinal']}:{path_key}")
    outcome_blind(entries)
    return entries, hashes


def selection(connection: sqlite3.Connection, entry: dict[str, Any]) -> dict[str, Any]:
    row = connection.execute("SELECT slot,tx_index FROM transactions WHERE signature=?", (entry["signature"],)).fetchone()
    if row is None or int(row[0]) != int(entry["canonicalSlot"]):
        raise RuntimeError(f"CANONICAL_ORDER_MISSING:{entry['ordinal']}")
    return {"signature": entry["signature"], "mint": entry["mint"],
            "entryReceivedAtUnixNs": int(entry["realtimeReceiptUnixNs"]), "entrySlot": int(row[0]),
            "entryTransactionIndex": int(row[1]), "route": "PUMP_BONDING_CURVE",
            "acquisitionClass": "CANONICAL_SUCCESSFUL_PUMP_ACQUISITION"}


def reconstruct(connection: sqlite3.Connection, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Use bounded chronological slices. A candidate CREATE is at most 120
    # seconds old, so each slice needs only MAX_AGE_NS lookback; build_choice
    # independently enforces the exact receipt/order boundary for every row.
    # This is equivalent to per-entry windows without an unbounded state load.
    slice_ns = 30 * 60 * 1_000_000_000
    groups: list[list[dict[str, Any]]] = []
    for entry in entries:
        key = int(entry["realtimeReceiptUnixNs"]) // slice_ns
        if not groups or int(groups[-1][0]["realtimeReceiptUnixNs"]) // slice_ns != key:
            groups.append([entry])
        else:
            groups[-1].append(entry)
    out = []
    for group in groups:
        events, creates, incomplete = state_plane.load_state(connection, [int(x["realtimeReceiptUnixNs"]) for x in group])
        for entry in group:
            choice, reasons, _ = materializer.build_choice(selection(connection, entry), events, creates, incomplete)
            if choice is None:
                raise RuntimeError(f"FROZEN_RECONSTRUCTION_DIVERGED:{entry['ordinal']}:{','.join(reasons)}")
            choice["eligibleOrdinal"] = int(entry["ordinal"])
            out.append(choice)
    materializer.assert_outcome_blind(out)
    return out


def fit(rows: list[dict[str, Any]], names: tuple[str, ...]):
    x, y, w, _ = timing.flatten(rows, names)
    means, scales = timing.standardizer(x)
    model = timing.fit_logistic(timing.transform(x, means, scales), y, w)
    return means, scales, model


def score_rows(rows, names, fitted):
    means, scales, model = fitted
    return timing.ranking_metrics(rows, names, means, scales, model)


def fold_report(rows: list[dict[str, Any]], names: tuple[str, ...]) -> dict[str, Any]:
    # Five expanding chronological folds. Each test fold remains strictly later
    # than its training observations and therefore cannot borrow future states.
    n, reports = len(rows), []
    edges = [0, 70, 140, 210, 280, 350]
    for i in range(1, 5):
        train, test = rows[:edges[i]], rows[edges[i]:edges[i + 1]]
        fitted = fit(train, names)
        metric = score_rows(test, names, fitted)
        seen = {x["mint"] for x in train}
        novel = [x for x in test if x["mint"] not in seen]
        reports.append({"fold": i, "trainOrdinals": [train[0]["eligibleOrdinal"], train[-1]["eligibleOrdinal"]],
                        "testOrdinals": [test[0]["eligibleOrdinal"], test[-1]["eligibleOrdinal"]],
                        "metrics": metric, "novelMintN": len(novel),
                        "novelMintMetrics": score_rows(novel, names, fitted) if novel else None})
    keys = ("actualRankedFirstPct", "meanReciprocalRank", "mintBalancedPairwiseConcordance", "actualRankedTop3Pct")
    summary = {k: statistics.fmean(x["metrics"][k] for x in reports) for k in keys}
    summary["foldTop1Range"] = [min(x["metrics"]["actualRankedFirstPct"] for x in reports), max(x["metrics"]["actualRankedFirstPct"] for x in reports)]
    return {"folds": reports, "meanOutOfSample": summary}


def univariate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for name in timing.FULL_FEATURES:
        selected, controls = [], []
        for ep in rows:
            for state in ep["states"]:
                (selected if state["label"] else controls).append(float(state["features"][name]))
        # Pairwise concordance direction is selected > control; also calculate reverse
        gt = sum((a > b) + 0.5 * (a == b) for a in selected for b in controls) / (len(selected) * len(controls))
        result.append({"feature": name, "selectedMean": statistics.fmean(selected), "controlMean": statistics.fmean(controls),
                       "pairwiseAuc": max(gt, 1 - gt), "direction": "higher" if gt >= .5 else "lower"})
    return sorted(result, key=lambda x: x["pairwiseAuc"], reverse=True)


def calibration(rows, names, fitted) -> dict[str, Any]:
    means, scales, model = fitted
    x, y, _, _ = timing.flatten(rows, names)
    scores = timing.predict(timing.transform(x, means, scales), model)
    bins = []
    for lo, hi in ((0,.2),(.2,.4),(.4,.6),(.6,.8),(.8,1.000001)):
        ids = [i for i, s in enumerate(scores) if lo <= s < hi]
        if ids: bins.append({"range": [lo, min(hi,1)], "n": len(ids), "meanScore": statistics.fmean(scores[i] for i in ids), "observedSelectedRate": statistics.fmean(y[i] for i in ids)})
    return {"brier": statistics.fmean((s-l)**2 for s,l in zip(scores,y)), "bins": bins}


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise RuntimeError("DEVELOPMENT_STUDY_OUTPUT_ALREADY_EXISTS")
    entries, batch_hashes = load_entries(args.allocation_root)
    conn = sqlite3.connect(f"file:{args.state_db}?mode=ro", uri=True, timeout=60)
    try:
        rows = reconstruct(conn, entries)
    finally:
        conn.close()
    if [x["eligibleOrdinal"] for x in rows] != ORDINALS:
        raise RuntimeError("DEVELOPMENT_RECONSTRUCTED_ORDER_INVALID")
    full = fold_report(rows, timing.FULL_FEATURES)
    compact = fold_report(rows, COMPACT_FEATURES)
    # Predeclared parsimony rule: compact wins only when its chronological mean
    # Top-1 is within two percentage points of the full model.
    chosen_name, names = ("COMPACT_CAUSAL_LOGISTIC_V1", COMPACT_FEATURES) if compact["meanOutOfSample"]["actualRankedFirstPct"] >= full["meanOutOfSample"]["actualRankedFirstPct"] - 2.0 else ("FULL_CAUSAL_LOGISTIC_V1", timing.FULL_FEATURES)
    final = fit(rows, names)
    coefficients = sorted(({"feature": k, "standardizedCoefficient": v} for k,v in zip(names, final[2][1])), key=lambda x: abs(x["standardizedCoefficient"]), reverse=True)
    result = {"schema": SCHEMA, "outcomeBlind": True, "pnlRead": 0, "submissions": 0,
              "developmentBucket": BUCKET, "ordinals": [151,500], "n": len(rows),
              "developmentManifestSetSha256": allocation.current_set_hash(BUCKET, batch_hashes), "manifestBatchHashes": batch_hashes,
              "featureSet": list(names), "modelFamily": chosen_name,
              "model": {"means": dict(zip(names,final[0])), "scales": dict(zip(names,final[1])), "intercept": final[2][0], "coefficients": dict(zip(names,final[2][1]))},
              "modelSelection": {"rule": "compact within 2.0 pp chronological OOS Top-1 of full", "full": full["meanOutOfSample"], "compact": compact["meanOutOfSample"]},
              "candidateFamilies": {"fullLogistic": full, "compactLogistic": compact},
              "finalDevelopmentMetrics": score_rows(rows,names,final), "calibration": calibration(rows,names,final),
              "individualFeatureSeparation": univariate(rows), "coefficientsByMagnitude": coefficients,
              "autonomousReplay": {"status":"NOT_ESTABLISHABLE_FROM_FROZEN_DEVELOPMENT_WINDOWS", "reason":"The retained causal state is promoted around teacher entries and alternatives, not a complete trigger-free market-time control stream. Ranking at teacher times is selection imitation, not autonomous firing."},
              "sizeSignal": {"status":"UNAVAILABLE", "reason":"Frozen symmetric choice schema has no causally isolated teacher entry-size feature."},
              "economicInventory": {"selectionInput":"complete", "fixedSizeEntry":"available after fingerprint freeze if separate trajectory evidence is certified", "confidenceSizing":"available after fingerprint freeze", "fullOrPartialTeacherSells":"not established by this selection-only dataset", "postEntryMFE_MAE":"requires separately certified post-entry trajectory tape", "outcomeOptimization":"not performed"},
              "limitations":["Development-only model selection has no external generalization claim until untouched N150 holdout is separately authorized.","Autonomous firing frequency and false-positive burden are not inferable from teacher-centered retained windows.","Repeated mint dependencies are addressed descriptively with novel-mint fold slices; full mint-cluster holdout is sample-limited."],
              "holdoutAccessed":0}
    # Inputs were recursively checked above.  The result contains feature names
    # such as gross_sell_sol_250ms; those are causal pre-entry features, not
    # outcome fields, so do not apply key-name taint matching to this schema.
    if result["pnlRead"] != 0 or result["outcomeBlind"] is not True or result["holdoutAccessed"] != 0:
        raise RuntimeError("DEVELOPMENT_OUTPUT_SAFETY_INVALID")
    atomic_json(args.output, result)
    args.output.chmod(0o444)
    return result


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--allocation-root", type=pathlib.Path, required=True)
    p.add_argument("--state-db", type=pathlib.Path, required=True)
    p.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(p.parse_args()), indent=2, sort_keys=True))

if __name__ == "__main__": main()
