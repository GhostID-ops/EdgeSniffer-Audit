#!/usr/bin/env python3
import argparse
import gzip
import hashlib
import importlib.util
import json
import math
import os
import struct
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone


STATIC_THRESHOLD = 0.590596
ENTRY_DELAY_MS = 25
SELLABLE_P50_MS = 964
SELLABLE_P75_MS = 1476
SELLABLE_P90_MS = 1989
TP = 20.0
SL = -20.0
HOLD_MS = 120_000
PROBLEM_MINT = "57XUWbdeMWTuYnJmPWmo7CNaoNf52g3byDyHEWCfpump"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def num(value, default=None):
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def read_jsonl_any(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def canonical_json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_obj(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def f64(value):
    value = float(value)
    bits = struct.unpack(">Q", struct.pack(">d", value))[0]
    return {
        "decimal_repr": repr(value),
        "float_hex": value.hex(),
        "bits_hex_be": f"{bits:016x}",
    }


def from_f64(obj):
    return float.fromhex(obj["float_hex"])


def metrics(rows, key="pnl"):
    vals = [num(r.get(key)) for r in rows if num(r.get(key)) is not None]
    pos = [v for v in vals if v > 0]
    neg = [v for v in vals if v < 0]
    gp = sum(pos)
    gl = abs(sum(neg))
    outcomes = Counter(r.get("outcome") or "UNKNOWN" for r in rows)
    return {
        "n": len(vals),
        "wins": len(pos),
        "losses": len(neg),
        "wr_pct": len(pos) * 100.0 / len(vals) if vals else None,
        "pf": gp / gl if gl else (float("inf") if gp else None),
        "expectancy_pct": sum(vals) / len(vals) if vals else None,
        "pnl_at_1sol": sum(vals) / 100.0,
        "tp": outcomes.get("TP", 0),
        "sl": outcomes.get("SL", 0),
        "max_hold": outcomes.get("max_hold", 0) + outcomes.get("MAX_HOLD", 0) + outcomes.get("HOLD", 0),
    }


def q(values, pct):
    xs = sorted(v for v in values if finite(v))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * pct
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def load_candidate_rows(forensics, path):
    rows = []
    for i, raw in enumerate(read_jsonl_any(path), 1):
        row = forensics.normalize_row(raw, i)
        if row.get("mint") and row.get("ts"):
            rows.append(row)
    return sorted(rows, key=lambda r: (r["ts"], r["_row_id"]))


def build_lossless_spec(forensics, stats, weights, args):
    weights_ordered = []
    for name, weight in weights.items():
        weights_ordered.append({
            "feature": name,
            "weight_f64": f64(weight),
        })

    normalization_ordered = []
    for name in forensics.FEATURES:
        st = stats.get(name)
        if not st:
            continue
        normalization_ordered.append({
            "feature": name,
            "center_f64": f64(st["center"]),
            "scale_f64": f64(st["scale"]),
            "corr_f64": f64(st["corr"]),
            "coverage": int(st["coverage"]),
        })

    return {
        "classification": "CANONICAL_LOSSLESS_RANKER_V2_SCORE_SPEC",
        "supersedes": {
            "path": args.rounded_spec,
            "reason": "SUPERSEDED_PRECISION_LOSS",
        },
        "source_of_truth": {
            "description": "Exact deterministic V2 scorer construction path used by executableAlphaRankingEngineV2FullUniverseReplay.py: normalize original V1 candidate outcomes, chronological 50% train slice, train_ranker(), then score_with_contribs(). This is not derived from the rounded frozen_score_spec.json.",
            "v2_replay_script": args.v2_script,
            "forensics_script": args.forensics_script,
            "candidate_outcomes": args.candidate_outcomes,
        },
        "do_not_retrain": True,
        "feature_order": list(forensics.FEATURES),
        "weighted_feature_order": [x["feature"] for x in weights_ordered],
        "weights_lossless": weights_ordered,
        "normalization_constants_lossless": normalization_ordered,
        "intercept_f64": f64(0.0),
        "threshold": {
            "literal": "0.590596",
            "value_f64": f64(STATIC_THRESHOLD),
            "comparator": "score >= threshold",
            "epsilon": "NONE",
            "score_rounding_before_compare": "NONE",
        },
        "feature_transforms": "z=(value-train_median)/train_iqr_or_p90_p10, clipped to [-5.0,5.0]",
        "missing_value_handling": "skip missing/non-finite weighted feature; score is null only if no weighted features are available",
        "score_formula": "iterate weighted_feature_order deterministically; contribution = weight * clamp((raw-center)/scale, -5, 5); score = sum(contributions)",
        "selection_semantics": "first chronological eligible observation per mint where score >= 0.590596; tie-break by timestamp ascending, score descending, snapshot_kind ascending",
        "entry_benchmark": {
            "entry_delay_ms": ENTRY_DELAY_MS,
            "sellable_p50_ms": SELLABLE_P50_MS,
            "primary_sellable_p75_ms": SELLABLE_P75_MS,
            "sellable_p90_ms": SELLABLE_P90_MS,
            "tp_pct": TP,
            "sl_pct": SL,
            "max_hold_ms": HOLD_MS,
        },
        "concurrency_reporting": ["RAW", "MAX_OPEN1", "MAX_OPEN2", "MAX_OPEN3", "MAX_OPEN5"],
    }


def canonical_runtime(spec):
    stats = {}
    for item in spec["normalization_constants_lossless"]:
        stats[item["feature"]] = {
            "center": from_f64(item["center_f64"]),
            "scale": from_f64(item["scale_f64"]),
            "corr": from_f64(item["corr_f64"]),
            "coverage": int(item["coverage"]),
        }
    weights = {item["feature"]: from_f64(item["weight_f64"]) for item in spec["weights_lossless"]}
    ordered = [(item["feature"], weights[item["feature"]]) for item in spec["weights_lossless"]]
    return stats, weights, ordered


def canonical_score(row, runtime):
    stats, _weights, ordered = runtime
    score = 0.0
    used = 0
    contribs = {}
    norms = {}
    for name, weight in ordered:
        value = row.get(name)
        if not finite(value):
            continue
        st = stats[name]
        z = max(-5.0, min(5.0, (float(value) - st["center"]) / st["scale"]))
        c = weight * z
        norms[name] = z
        contribs[name] = c
        score += c
        used += 1
    return (score if used else None), norms, contribs


def first_per_mint(rows):
    chosen = {}
    for row in sorted(rows, key=lambda r: (r["ts"], -num(r.get("score"), -1e99), r.get("snapshot_kind") or "")):
        chosen.setdefault(row["mint"], row)
    return sorted(chosen.values(), key=lambda r: r["ts"])


def by_split(rows):
    out = defaultdict(list)
    for row in rows:
        out[row.get("split")].append(row)
    return out


def update_neighbors(below, above, row, score):
    item = {
        "mint": row.get("mint"),
        "ts": row.get("ts"),
        "snapshot_kind": row.get("snapshot_kind"),
        "score": score,
        "distance": abs(score - STATIC_THRESHOLD),
    }
    if score < STATIC_THRESHOLD:
        below.append(item)
        below.sort(key=lambda r: r["distance"])
        del below[50:]
    else:
        above.append(item)
        above.sort(key=lambda r: r["distance"])
        del above[50:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2-dir", required=True)
    ap.add_argument("--candidate-outcomes", required=True)
    ap.add_argument("--forensics-script", required=True)
    ap.add_argument("--v2-script", required=True)
    ap.add_argument("--rounded-spec", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    started = time.time()

    forensics = load_module("static_forensics_v1", args.forensics_script)
    candidate_rows = load_candidate_rows(forensics, args.candidate_outcomes)
    train = candidate_rows[: int(len(candidate_rows) * 0.50)]
    stats, weights = forensics.train_ranker(train)
    spec = build_lossless_spec(forensics, stats, weights, args)
    spec_hash = sha256_obj(spec)
    canon_runtime = canonical_runtime(spec)

    # Candidate-source parity, including the previous failure fixture.
    cand_diffs = []
    cand_exact = 0
    cand_disagreements = 0
    previous_failure = None
    for row in candidate_rows:
        original_score, _, _ = forensics.score_with_contribs(row, stats, weights)
        canon_score, _, _ = canonical_score(row, canon_runtime)
        if not (finite(original_score) and finite(canon_score)):
            continue
        diff = abs(original_score - canon_score)
        cand_diffs.append(diff)
        cand_exact += 1 if original_score == canon_score else 0
        if (original_score >= STATIC_THRESHOLD) != (canon_score >= STATIC_THRESHOLD):
            cand_disagreements += 1
        if row.get("mint") == PROBLEM_MINT:
            previous_failure = {
                "mint": row.get("mint"),
                "ts": row.get("ts"),
                "row_id": row.get("_row_id"),
                "snapshot_kind": row.get("snapshot_kind"),
                "canonical_score": canon_score,
                "original_score": original_score,
                "threshold": STATIC_THRESHOLD,
                "canonical_decision": canon_score >= STATIC_THRESHOLD,
                "original_decision": original_score >= STATIC_THRESHOLD,
                "outcome": row.get("outcome"),
                "pnl_pct": row.get("pnl"),
            }

    # Full-universe parity against original scorer over the full scoreable feature table.
    score_path = os.path.join(args.v2_dir, "full_universe_scores.jsonl.gz")
    selected_source_path = os.path.join(args.v2_dir, "selected_trades.jsonl.gz")
    full_diffs = []
    full_exact = 0
    compared = 0
    pass_pass = fail_fail = orig_pass_canon_fail = orig_fail_canon_pass = 0
    original_selected = []
    canonical_selected = []
    below = []
    above = []
    stored_pass = 0
    stored_vs_canon_disagreements = 0
    with gzip.open(score_path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            features = row.get("features") or {}
            shell = {name: features.get(name) for name in forensics.FEATURES}
            original_score, _, _ = forensics.score_with_contribs(shell, stats, weights)
            canon_score, _, _ = canonical_score(shell, canon_runtime)
            if not (finite(original_score) and finite(canon_score)):
                continue
            compared += 1
            diff = abs(original_score - canon_score)
            full_diffs.append(diff)
            full_exact += 1 if original_score == canon_score else 0
            orig_pass = original_score >= STATIC_THRESHOLD
            canon_pass = canon_score >= STATIC_THRESHOLD
            stored_pass_bool = finite(row.get("rank_score")) and row["rank_score"] >= STATIC_THRESHOLD
            stored_pass += 1 if stored_pass_bool else 0
            stored_vs_canon_disagreements += 1 if stored_pass_bool != canon_pass else 0
            if orig_pass and canon_pass:
                pass_pass += 1
            elif not orig_pass and not canon_pass:
                fail_fail += 1
            elif orig_pass and not canon_pass:
                orig_pass_canon_fail += 1
            else:
                orig_fail_canon_pass += 1
            update_neighbors(below, above, row, original_score)
            if orig_pass:
                original_selected.append({
                    "mint": row["mint"],
                    "ts": int(row["ts"]),
                    "snapshot_kind": row.get("snapshot_kind"),
                    "score": original_score,
                    "split": row.get("split"),
                })
            if canon_pass:
                canonical_selected.append({
                    "mint": row["mint"],
                    "ts": int(row["ts"]),
                    "snapshot_kind": row.get("snapshot_kind"),
                    "score": canon_score,
                    "split": row.get("split"),
                })

    original_primary = first_per_mint(original_selected)
    canonical_primary = first_per_mint(canonical_selected)
    source_selected = list(read_jsonl_any(selected_source_path))
    source_primary = first_per_mint([{
        "mint": r["mint"],
        "ts": int(r["ts"]),
        "snapshot_kind": r.get("snapshot_kind"),
        "score": r.get("rank_score"),
        "split": r.get("split"),
        "outcome": r.get("outcome"),
        "pnl": r.get("pnl"),
    } for r in source_selected])
    source_by_key = {(r["mint"], int(r["ts"]), r.get("snapshot_kind")): r for r in source_selected}
    canon_enriched = []
    missing_econ_keys = []
    for row in canonical_primary:
        key = (row["mint"], int(row["ts"]), row.get("snapshot_kind"))
        src = source_by_key.get(key)
        if not src:
            missing_econ_keys.append(key)
            continue
        enriched = dict(row)
        enriched.update({"outcome": src.get("outcome"), "pnl": src.get("pnl")})
        canon_enriched.append(enriched)

    def manifest_rows(rows):
        return [{
            "mint": r["mint"],
            "ts": int(r["ts"]),
            "snapshot_kind": r.get("snapshot_kind"),
            "split": r.get("split"),
            "score": r.get("score"),
        } for r in rows]

    primary_ledger = manifest_rows(canonical_primary)
    primary_ledger_path = os.path.join(args.out_dir, "primary_trigger_ledger.jsonl.gz")
    with gzip.open(primary_ledger_path, "wt", encoding="utf-8") as out:
        for row in primary_ledger:
            out.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    def keyset(rows):
        return {(r["mint"], int(r["ts"]), r.get("snapshot_kind")) for r in rows}

    trigger_manifest = {
        "observation_level": {
            "original_count": len(original_selected),
            "canonical_count": len(canonical_selected),
            "source_v2_stored_count": len(source_selected),
            "original_vs_canonical_key_diff": len(keyset(original_selected) ^ keyset(canonical_selected)),
            "source_v2_stored_vs_canonical_key_diff": len(keyset(source_selected) ^ keyset(canonical_selected)),
        },
        "earliest_trigger_per_mint": {
            "original_count": len(original_primary),
            "canonical_count": len(canonical_primary),
            "source_v2_stored_count": len(source_primary),
            "original_vs_canonical_key_diff": len(keyset(original_primary) ^ keyset(canonical_primary)),
            "source_v2_stored_vs_canonical_key_diff": len(keyset(source_primary) ^ keyset(canonical_primary)),
            "by_split": {split: len(rows) for split, rows in sorted(by_split(canonical_primary).items())},
        },
        "selection_semantics": spec["selection_semantics"],
        "ledger_path": primary_ledger_path,
        "ledger_sha256": sha256_file(primary_ledger_path),
    }

    parity_report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "candidate_source_parity": {
            "rows_compared": len(cand_diffs),
            "exact_score_matches": cand_exact,
            "max_abs_score_difference": max(cand_diffs) if cand_diffs else None,
            "median_score_difference": q(cand_diffs, 0.50),
            "p99_score_difference": q(cand_diffs, 0.99),
            "threshold_disagreements": cand_disagreements,
            "previous_failure_fixture": previous_failure,
        },
        "full_universe_parity": {
            "rows_compared": compared,
            "exact_score_matches": full_exact,
            "max_abs_score_difference": max(full_diffs) if full_diffs else None,
            "median_score_difference": q(full_diffs, 0.50),
            "p99_score_difference": q(full_diffs, 0.99),
            "pass_pass": pass_pass,
            "fail_fail": fail_fail,
            "original_pass_canonical_fail": orig_pass_canon_fail,
            "original_fail_canonical_pass": orig_fail_canon_pass,
            "threshold_disagreements": orig_pass_canon_fail + orig_fail_canon_pass,
            "stored_v2_pass_count": stored_pass,
            "stored_v2_vs_canonical_threshold_disagreements": stored_vs_canon_disagreements,
        },
    }

    threshold_neighbor = {
        "nearest_50_below": below,
        "nearest_50_above": above,
        "canonical_parity": {
            "below_checked": len(below),
            "above_checked": len(above),
            "threshold_disagreements": 0 if parity_report["full_universe_parity"]["threshold_disagreements"] == 0 else "see parity_report",
        },
    }

    economics = {
        "source_population_missing_economics_keys": len(missing_econ_keys),
        "canonical_primary_by_split": {split: metrics(rows) for split, rows in sorted(by_split(canon_enriched).items())},
        "expected_counts": {"train": 964, "validation": 428, "final_test": 766},
        "expected_pf_approx": {"train": 2.25, "validation": 3.07, "final_test": 4.65},
    }

    feature_manifest = {
        "feature_order": spec["feature_order"],
        "weighted_feature_order": spec["weighted_feature_order"],
        "features": [{
            "feature": name,
            "source_fields": [name],
            "window_semantics": (
                f"backward {name.split('.')[0][1:]}ms window ending at decision timestamp"
                if name.startswith("w") and "." in name else
                "current decision-state value or backward wallet relationship ending at decision timestamp"
            ),
            "units": "feature-specific numeric scalar",
            "missing_behavior": spec["missing_value_handling"],
            "transform": spec["feature_transforms"],
            "information_time": "<= decision timestamp",
        } for name in spec["feature_order"]],
    }

    threshold_spec = spec["threshold"]
    freeze_manifest = {
        "classification": None,
        "created_at_utc": parity_report["created_at_utc"],
        "read_only_artifact_generation": True,
        "no_live_or_production_changes": True,
        "previous_rounded_artifact": {
            "path": args.rounded_spec,
            "status": "SUPERSEDED_PRECISION_LOSS",
            "sha256": sha256_file(args.rounded_spec),
        },
        "source_hashes": {
            "v2_replay_script": sha256_file(args.v2_script),
            "forensics_script": sha256_file(args.forensics_script),
            "candidate_outcomes": sha256_file(args.candidate_outcomes),
            "full_universe_scores": sha256_file(score_path),
            "selected_trades": sha256_file(selected_source_path),
        },
        "artifact_hashes": {},
        "final_decision": None,
    }

    blockers = []
    if parity_report["full_universe_parity"]["threshold_disagreements"] != 0:
        blockers.append("full universe threshold disagreement")
    if parity_report["candidate_source_parity"]["threshold_disagreements"] != 0:
        blockers.append("candidate source threshold disagreement")
    if trigger_manifest["observation_level"]["canonical_count"] != 3889:
        blockers.append("observation-level crossing count mismatch")
    if trigger_manifest["earliest_trigger_per_mint"]["canonical_count"] != 2158:
        blockers.append("earliest-trigger-per-mint count mismatch")
    if trigger_manifest["observation_level"]["source_v2_stored_vs_canonical_key_diff"] != 0:
        blockers.append("canonical selected observations differ from V2 source ledger")
    if trigger_manifest["earliest_trigger_per_mint"]["source_v2_stored_vs_canonical_key_diff"] != 0:
        blockers.append("canonical primary ledger differs from V2 source ledger")
    if not previous_failure or not previous_failure["canonical_decision"]:
        blockers.append("previous failure fixture not corrected")
    if missing_econ_keys:
        blockers.append("canonical primary economics missing source rows")

    decision = "RANKER_V2_CANONICAL_FREEZE_PARITY_PASS" if not blockers else "RANKER_V2_CANONICAL_FREEZE_PARITY_BLOCKED"
    freeze_manifest["classification"] = decision
    freeze_manifest["final_decision"] = decision
    freeze_manifest["blockers"] = blockers

    artifacts = {
        "canonical_score_spec.json": spec,
        "feature_manifest.json": feature_manifest,
        "threshold_spec.json": threshold_spec,
        "parity_report.json": parity_report,
        "threshold_neighbor_parity.json": threshold_neighbor,
        "primary_trigger_manifest.json": trigger_manifest,
        "primary_economics_parity.json": economics,
    }
    paths = {}
    for name, obj in artifacts.items():
        path = os.path.join(args.out_dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, indent=2, sort_keys=True)
        paths[name] = path
        freeze_manifest["artifact_hashes"][name] = sha256_file(path)
    freeze_manifest["artifact_hashes"]["primary_trigger_ledger.jsonl.gz"] = sha256_file(primary_ledger_path)
    freeze_manifest["canonical_score_spec_object_sha256"] = spec_hash

    freeze_path = os.path.join(args.out_dir, "freeze_manifest.json")
    with open(freeze_path, "w", encoding="utf-8") as handle:
        json.dump(freeze_manifest, handle, indent=2, sort_keys=True)
    paths["freeze_manifest.json"] = freeze_path
    with open(os.path.join(args.out_dir, "canonical_score_spec.sha256"), "w", encoding="utf-8") as handle:
        handle.write(freeze_manifest["artifact_hashes"]["canonical_score_spec.json"] + "  canonical_score_spec.json\n")

    result = {
        "decision": decision,
        "blockers": blockers,
        "out_dir": args.out_dir,
        "canonical_score_spec_sha256": freeze_manifest["artifact_hashes"]["canonical_score_spec.json"],
        "canonical_score_spec_object_sha256": spec_hash,
        "parity": parity_report,
        "trigger_manifest": trigger_manifest,
        "economics": economics,
        "runtime_sec": time.time() - started,
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
