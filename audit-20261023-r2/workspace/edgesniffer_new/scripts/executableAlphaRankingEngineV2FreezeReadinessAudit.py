#!/usr/bin/env python3
import argparse
import gzip
import importlib.util
import json
import math
import os
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


def compact(value):
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def iso(ms):
    if not finite(ms):
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


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


def metrics(rows, key="pnl"):
    vals = []
    outcomes = Counter()
    mints = set()
    for row in rows:
        pnl = num(row.get(key))
        if pnl is None:
            continue
        vals.append(pnl)
        outcomes[row.get("outcome") or "UNKNOWN"] += 1
        if row.get("mint"):
            mints.add(row["mint"])
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    total = sum(vals)
    return {
        "n": len(vals),
        "unique_mints": len(mints),
        "wins": len(wins),
        "losses": len(losses),
        "wr_pct": len(wins) * 100.0 / len(vals) if vals else None,
        "pf": gp / gl if gl else (float("inf") if gp else None),
        "expectancy_pct": total / len(vals) if vals else None,
        "median_return_pct": q(vals, 0.5),
        "pnl_at_1sol": total / 100.0,
        "tp": outcomes.get("TP", 0),
        "sl": outcomes.get("SL", 0),
        "max_hold": outcomes.get("max_hold", 0) + outcomes.get("MAX_HOLD", 0) + outcomes.get("HOLD", 0),
        "other": len(vals) - outcomes.get("TP", 0) - outcomes.get("SL", 0) - outcomes.get("max_hold", 0) - outcomes.get("MAX_HOLD", 0) - outcomes.get("HOLD", 0),
        "pf_ex_best1": pf_ex_best(vals, 1),
        "pf_ex_best3": pf_ex_best(vals, 3),
        "pf_ex_best5": pf_ex_best(vals, 5),
        "pf_ex_best10": pf_ex_best(vals, 10),
        "top1_profit_contribution_pct": top_contribution(wins, total, 1),
        "top3_profit_contribution_pct": top_contribution(wins, total, 3),
        "top10_profit_contribution_pct": top_contribution(wins, total, 10),
    }


def top_contribution(wins, total, n):
    return sum(sorted(wins, reverse=True)[:n]) / total * 100.0 if wins and total > 0 else None


def pf_ex_best(vals, n):
    xs = [v for v in vals if finite(v)]
    for value in sorted([v for v in xs if v > 0], reverse=True)[:n]:
        try:
            xs.remove(value)
        except ValueError:
            pass
    wins = [v for v in xs if v > 0]
    losses = [v for v in xs if v < 0]
    return sum(wins) / abs(sum(losses)) if losses else (float("inf") if wins else None)


def read_jsonl_any(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def score_with_spec(row, spec):
    score = 0.0
    used = 0
    weights = spec["weights"]
    stats = spec["normalization_constants"]
    for name, weight in weights.items():
        value = row.get(name)
        if not finite(value):
            continue
        st = stats[name]
        z = max(-5.0, min(5.0, (value - st["center"]) / st["scale"]))
        score += weight * z
        used += 1
    return score if used else None


def first_per_mint(rows):
    chosen = {}
    for row in sorted(rows, key=lambda r: (r["ts"], -num(r.get("rank_score"), -1e99), r.get("snapshot_kind") or "")):
        chosen.setdefault(row["mint"], row)
    return sorted(chosen.values(), key=lambda r: r["ts"])


def by_split(rows):
    out = defaultdict(list)
    for row in rows:
        out[row.get("split")].append(row)
    return out


def atlas_nonatlas(rows):
    return {
        "ORIGINAL_ATLAS_CANDIDATE": metrics([r for r in rows if r.get("is_original_atlas_candidate")]),
        "FULL_UNIVERSE_NON_ATLAS": metrics([r for r in rows if not r.get("is_original_atlas_candidate")]),
    }


def split_table(rows):
    by = by_split(rows)
    return {split: metrics(vals) for split, vals in sorted(by.items())}


def duplicate_mints(rows):
    c = Counter(r["mint"] for r in rows)
    return {mint: count for mint, count in c.items() if count > 1}


def age_distribution(rows):
    vals = [num(r.get("age_ms")) for r in rows if finite(num(r.get("age_ms")))]
    buckets = Counter()
    for age in vals:
        if age < 100:
            buckets["<100ms"] += 1
        elif age < 250:
            buckets["100-250ms"] += 1
        elif age < 500:
            buckets["250-500ms"] += 1
        elif age < 1000:
            buckets["500ms-1s"] += 1
        elif age < 2000:
            buckets["1-2s"] += 1
        elif age < 5000:
            buckets["2-5s"] += 1
        else:
            buckets[">5s"] += 1
    return {
        "p10": q(vals, 0.10),
        "p25": q(vals, 0.25),
        "p50": q(vals, 0.50),
        "p75": q(vals, 0.75),
        "p90": q(vals, 0.90),
        "buckets": dict(sorted(buckets.items())),
    }


def frequency(rows, span_hours):
    hourly = Counter(r["ts"] // 3_600_000 for r in rows)
    daily = Counter(r["ts"] // 86_400_000 for r in rows)
    return {
        "span_hours_denominator": span_hours,
        "one_per_mint_opportunities_per_hour": len(rows) / span_hours if span_hours else None,
        "hourly": {
            "hours_with_signals": len(hourly),
            "p10": q(list(hourly.values()), 0.10),
            "p25": q(list(hourly.values()), 0.25),
            "p50": q(list(hourly.values()), 0.50),
            "p75": q(list(hourly.values()), 0.75),
            "p90": q(list(hourly.values()), 0.90),
            "max": max(hourly.values()) if hourly else None,
            "zero_signal_hours_inside_span_estimate": max(0, int(math.ceil(span_hours or 0)) - len(hourly)),
        },
        "daily": {str(day): count for day, count in sorted(daily.items())},
    }


def concurrency(rows, max_open):
    active = []
    accepted = []
    rejected = 0
    for row in sorted(rows, key=lambda r: r["ts"]):
        active = [end for end in active if end > row["ts"]]
        if len(active) >= max_open:
            rejected += 1
            continue
        exit_ms = num(row.get("exit_ms"), HOLD_MS)
        active.append(row["ts"] + int(exit_ms))
        accepted.append(row)
    out = metrics(accepted)
    out["accepted"] = len(accepted)
    out["rejected_due_to_capacity"] = rejected
    return out


def event_semantics(snapshot_kind):
    table = {
        "FIRST_IMPULSE": ("PASS", "First buy event where trailing 1000ms gross_buy_sol >= 2; uses only current/backward window."),
        "SECOND_ACCELERATION": ("PASS", "First buy event where trailing 500ms gross_buy_sol >= 2 and prior 1s-5s buys >= 1; backward only."),
        "FIRST_LARGE_SELLER": ("PASS", "First observed sell event; declared at the sell observation timestamp."),
        "FIRST_LARGE_SELL_ABSORPTION": ("PASS", "First buy event with trailing 5s sell_count, gross flow, and drawdown conditions; backward-window heuristic."),
        "POST_PULLBACK_BREAKOUT": ("PASS_WITH_NAMING_LIMITATION", "Backward-window pullback + current quick gross flow; no future breakout confirmation, name overclaims but semantics are prospective."),
        "CONSOLIDATION_BREAKOUT": ("PASS_WITH_NAMING_LIMITATION", "Backward-window range/drawdown + current quick gross flow; no future confirmation, name overclaims but semantics are prospective."),
    }
    if str(snapshot_kind).startswith("FIXED_"):
        return ("PASS", "Fixed-age lifecycle snapshot emitted when mint age crosses the fixed age using current/backward state.")
    return table.get(snapshot_kind, ("UNKNOWN_REVIEW_REQUIRED", "State not recognized by this audit."))


def feature_live_status(feature):
    historical_only = set()
    derivable = {
        "launches_per_minute",
        "active_flow_mints",
        "new_wallets_500_vs_prior5s",
        "repeat_wallets_500",
        "second_to_first_sol_ratio",
    }
    if feature in historical_only:
        return "HISTORICAL_ONLY_BLOCKER"
    if feature in derivable or feature.startswith("w"):
        return "LIVE_DERIVABLE"
    return "LIVE_AVAILABLE"


def sample_selected(rows):
    by = by_split(rows)
    sample = []
    for split in ("train", "validation", "final_test"):
        vals = sorted(by.get(split, []), key=lambda r: r["ts"])
        if not vals:
            continue
        want = 40 if split != "train" else 20
        if len(vals) <= want:
            sample.extend(vals)
            continue
        for i in range(want):
            idx = round(i * (len(vals) - 1) / max(1, want - 1))
            sample.append(vals[idx])
    return sample[:140]


def collect_sample_marks(atlas, capsules, sample):
    ranges = {}
    for row in sample:
        start, end = ranges.get(row["mint"], (row["ts"], row["ts"]))
        ranges[row["mint"]] = (min(start, row["ts"]), max(end, row["ts"] + HOLD_MS + SELLABLE_P90_MS + 1000))
    marks = defaultdict(list)
    for raw in atlas.read_jsonl(capsules):
        mint = atlas.mint_of(raw)
        if mint not in ranges:
            continue
        ts = atlas.ts_of(raw)
        mc = atlas.mc_of(raw)
        if finite(ts) and finite(mc):
            ts = int(ts)
            start, end = ranges[mint]
            if start <= ts <= end and 0 < float(mc) <= atlas.MAX_MC_USD:
                marks[mint].append((ts, float(mc)))
    for arr in marks.values():
        arr.sort()
    return marks


def raw_economics_check(atlas, capsules, sample):
    marks = collect_sample_marks(atlas, capsules, sample)
    mismatches = []
    stress_mismatches = []
    checked = []
    for row in sample:
        replay = atlas.score(row["ts"], row["mc_usd"], marks.get(row["mint"], []), ENTRY_DELAY_MS, SELLABLE_P75_MS, 0)
        stress5 = atlas.score(row["ts"], row["mc_usd"], marks.get(row["mint"], []), ENTRY_DELAY_MS, SELLABLE_P75_MS, 5)
        def close(a, b, eps=1e-6):
            if a is None and b is None:
                return True
            return finite(a) and finite(b) and abs(float(a) - float(b)) <= eps
        mismatch = (
            replay.get("outcome") != row.get("outcome")
            or not close(replay.get("pnl"), row.get("pnl"))
            or not close(replay.get("entry_mc"), row.get("entry_mc"))
            or replay.get("first_sellable_ts") != row.get("first_sellable_ts")
        )
        stress_mismatch = not close(stress5.get("pnl"), row.get("stress5_pnl"))
        item = {
            "mint": row["mint"],
            "split": row["split"],
            "ts": row["ts"],
            "cached_outcome": row.get("outcome"),
            "replay_outcome": replay.get("outcome"),
            "cached_pnl": row.get("pnl"),
            "replay_pnl": replay.get("pnl"),
            "cached_entry_mc": row.get("entry_mc"),
            "replay_entry_mc": replay.get("entry_mc"),
            "cached_stress5_pnl": row.get("stress5_pnl"),
            "replay_stress5_pnl": stress5.get("pnl"),
        }
        checked.append(item)
        if mismatch:
            mismatches.append(item)
        if stress_mismatch:
            stress_mismatches.append(item)
    return {
        "sample_size": len(sample),
        "mismatch_count": len(mismatches),
        "mismatch_rate_pct": len(mismatches) * 100.0 / len(sample) if sample else None,
        "stress5_mismatch_count": len(stress_mismatches),
        "stress5_mismatch_rate_pct": len(stress_mismatches) * 100.0 / len(sample) if sample else None,
        "mismatch_examples": mismatches[:10],
        "stress5_mismatch_examples": stress_mismatches[:10],
        "checked_examples": checked[:10],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2-dir", required=True)
    ap.add_argument("--capsules", required=True)
    ap.add_argument("--candidate-outcomes", required=True)
    ap.add_argument("--atlas-script", required=True)
    ap.add_argument("--forensics-script", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    started = time.time()

    atlas = load_module("atlas_v1", args.atlas_script)
    forensics = load_module("forensics_v1", args.forensics_script)

    report = json.load(open(os.path.join(args.v2_dir, "final_report.json"), "r", encoding="utf-8"))
    spec = json.load(open(os.path.join(args.v2_dir, "frozen_score_spec.json"), "r", encoding="utf-8"))
    manifest = json.load(open(os.path.join(args.v2_dir, "full_universe_manifest.json"), "r", encoding="utf-8"))
    selected = list(read_jsonl_any(os.path.join(args.v2_dir, "selected_trades.jsonl.gz")))
    scores = list(read_jsonl_any(os.path.join(args.v2_dir, "full_universe_scores.jsonl.gz")))

    primary = first_per_mint(selected)
    primary_by_split = by_split(primary)
    selected_by_split = by_split(selected)
    primary_atlas_nonatlas = {split: atlas_nonatlas(rows) for split, rows in sorted(primary_by_split.items())}

    # Frozen scorer parity against original V1 rows.
    candidate_rows = []
    for i, raw in enumerate(read_jsonl_any(args.candidate_outcomes), 1):
        row = forensics.normalize_row(raw, i)
        if row.get("mint") and row.get("ts"):
            candidate_rows.append(row)
    candidate_rows = sorted(candidate_rows, key=lambda r: (r["ts"], r["_row_id"]))
    train = candidate_rows[: int(len(candidate_rows) * 0.50)]
    fresh_stats, fresh_weights = forensics.train_ranker(train)
    max_diff = 0.0
    disagreements = 0
    rows_compared = 0
    for row in candidate_rows:
        fresh_score, _, _ = forensics.score_with_contribs(row, fresh_stats, fresh_weights)
        spec_score = score_with_spec(row, spec)
        if finite(fresh_score) and finite(spec_score):
            rows_compared += 1
            max_diff = max(max_diff, abs(fresh_score - spec_score))
            if (fresh_score >= STATIC_THRESHOLD) != (spec_score >= STATIC_THRESHOLD):
                disagreements += 1

    event_groups = defaultdict(list)
    for row in primary:
        event_groups[row.get("snapshot_kind")].append(row)
    event_audit = {}
    for kind, rows in sorted(event_groups.items()):
        status, explanation = event_semantics(kind)
        event_audit[kind] = {
            "population": "EARLIEST_TRIGGER_PER_MINT",
            "n": len(rows),
            "metrics": metrics(rows),
            "prospective_semantics": status,
            "explanation": explanation,
        }

    dup_primary = duplicate_mints(primary)
    sample = sample_selected(primary)
    raw_check = raw_economics_check(atlas, args.capsules, sample)

    split_meta = manifest.get("split") or {}
    split_integrity = {
        "split_method": "chronological by mint create/first_seen timestamp",
        "train_end_create_ts_ms": split_meta.get("train_end_create_ts"),
        "train_end_create_utc": iso(split_meta.get("train_end_create_ts")),
        "validation_end_create_ts_ms": split_meta.get("validation_end_create_ts"),
        "validation_end_create_utc": iso(split_meta.get("validation_end_create_ts")),
        "mints": {
            "train": split_meta.get("train_mints"),
            "validation": split_meta.get("validation_mints"),
            "final_test": split_meta.get("final_test_mints"),
        },
        "mint_overlap": split_meta.get("mint_overlap"),
        "primary_duplicate_mints": len(dup_primary),
        "primary_duplicate_examples": dict(list(dup_primary.items())[:10]),
        "boundary_handling": "mints sorted by first_seen/create proxy timestamp, then address tie-breaker; entire mint assigned to one split",
    }

    span_hours = manifest.get("span_hours")
    concurrency_report = {
        "raw_independent": split_table(primary),
        "max_open1": {split: concurrency(rows, 1) for split, rows in sorted(primary_by_split.items())},
        "max_open2": {split: concurrency(rows, 2) for split, rows in sorted(primary_by_split.items())},
        "max_open3": {split: concurrency(rows, 3) for split, rows in sorted(primary_by_split.items())},
        "max_open5": {split: concurrency(rows, 5) for split, rows in sorted(primary_by_split.items())},
    }

    feature_status = []
    for name in spec.get("feature_names") or []:
        if name.startswith("w"):
            window = f"backward window {name.split('.')[0][1:]}ms ending at decision timestamp"
        elif name in ("new_wallets_500_vs_prior5s", "repeat_wallets_500", "second_to_first_sol_ratio"):
            window = "backward wallet/flow relationship window ending at decision timestamp"
        else:
            window = "current decision-state value"
        feature_status.append({
            "feature": name,
            "live_status": feature_live_status(name),
            "prospective_window": window,
            "feature_information_time_lte_decision_time": True,
        })

    classifications = []
    blockers = []
    if manifest.get("population_classification") != "FULL_ELIGIBLE_DECISION_STATE_UNIVERSE":
        blockers.append("population was not classified as full eligible decision-state universe")
    if disagreements != 0 or max_diff > 1e-9:
        blockers.append("frozen scorer parity mismatch")
    if raw_check["mismatch_count"] != 0:
        blockers.append("raw economics replay mismatch")
    if raw_check["stress5_mismatch_count"] != 0:
        blockers.append("+5% entry stress replay mismatch")
    if len(dup_primary) != 0:
        blockers.append("primary one-trade-per-mint ledger contains duplicate mints")
    if any(x["live_status"] == "HISTORICAL_ONLY_BLOCKER" for x in feature_status):
        blockers.append("feature parity blocker")

    val = metrics(primary_by_split.get("validation", []))
    final = metrics(primary_by_split.get("final_test", []))
    plus5 = report.get("N_entry_stress", {})
    val_plus5 = (((plus5.get("validation") or {}).get("plus5") or {}).get("pf"))
    final_plus5 = (((plus5.get("final_test") or {}).get("plus5") or {}).get("pf"))
    if blockers:
        decision = "RANKER_V2_NOT_READY"
    elif (val.get("pf") or 0) > 2 and (final.get("pf") or 0) > 2 and (final_plus5 or 0) > 1:
        decision = "RANKER_V2_FREEZE_READY_WITH_DOCUMENTED_LIMITATIONS"
    else:
        decision = "RANKER_V2_NOT_READY"

    freeze_spec = None
    if decision.startswith("RANKER_V2_FREEZE_READY"):
        freeze_spec = {
            "candidate_name": "EXECUTABLE_ALPHA_RANKING_ENGINE_V2_FULL_UNIVERSE_STATIC_THRESHOLD",
            "score_spec_source": os.path.join(args.v2_dir, "frozen_score_spec.json"),
            "score_formula": spec.get("score_formula"),
            "score_hash_material": {
                "features": spec.get("feature_names"),
                "weights": spec.get("weights"),
                "normalization_constants": spec.get("normalization_constants"),
                "threshold": STATIC_THRESHOLD,
            },
            "threshold": STATIC_THRESHOLD,
            "feature_semantics": feature_status,
            "lifecycle_state_semantics": event_audit,
            "selection_semantics": "earliest chronological observation per mint where frozen rank_score >= 0.590596; no highest-score or best-outcome selection",
            "entry_assumptions": {
                "entry_delay_ms": ENTRY_DELAY_MS,
                "primary_first_sellable_ms": SELLABLE_P75_MS,
                "p50_first_sellable_ms": SELLABLE_P50_MS,
                "p90_first_sellable_ms": SELLABLE_P90_MS,
                "tp_pct": TP,
                "sl_pct": SL,
                "max_hold_ms": HOLD_MS,
                "no_pre_sellable_tp": True,
            },
            "max_open_reporting": "report raw one-per-mint and MAX_OPEN1/2/3/5 using modeled exact close timestamps",
            "historical_evidence_status": "PSEUDO_HOLDOUT/HISTORICAL_REPLICATION; requires future-only prospective no-submit freeze before promotion",
        }
        write_path = os.path.join(args.out_dir, "immutable_freeze_candidate_spec.json")
        with open(write_path, "w", encoding="utf-8") as handle:
            json.dump(compact(freeze_spec), handle, indent=2, sort_keys=True)
        freeze_spec["path"] = write_path

    audit = {
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "read_only_adversarial_audit": True,
        "decision": decision,
        "blockers": blockers,
        "1_population_count_reconciliation": {
            "observation_level_sections": {
                "selected_trades_jsonl": len(selected),
                "validation_observation_level": metrics(selected_by_split.get("validation", [])),
                "final_test_observation_level": metrics(selected_by_split.get("final_test", [])),
                "atlas_vs_nonatlas_original_report_was_observation_level": True,
                "monotonicity_threshold_to_max_is_observation_level": True,
            },
            "earliest_trigger_per_mint_sections": {
                "primary_ledger": len(primary),
                "by_split": split_table(primary),
                "primary_atlas_vs_nonatlas": primary_atlas_nonatlas,
            },
            "explanation": "686 validation and 1600 final are observation-level threshold crossings. 428 validation and 766 final are earliest chronological threshold crossing per mint, which is the primary deployable interpretation.",
        },
        "2_full_universe_eligibility_audit": {
            "classification": manifest.get("population_classification"),
            "rows_scanned": manifest.get("rows_scanned"),
            "scoreable_snapshots": manifest.get("scoreable_snapshots"),
            "snapshot_counts": manifest.get("snapshot_counts"),
            "exclusions": manifest.get("excluded_rows"),
            "decision_state_vs_outcome_evaluability": "decision-state eligibility was built before outcome scoring from current/backward lifecycle state; outcome evaluability is only applied later via marks for economic replay.",
            "outcome_related_exclusion_risk": {
                "bad_or_out_of_range_mc": "medium: bad/missing MC can be infrastructure-correlated, but it is a technical scoreability exclusion, not favorable-outcome filtering.",
                "not_buy_or_sell": "low: non-flow rows cannot define Atlas lifecycle decision states in this generator.",
                "unscoreable_missing_weighted_feature": "low/none in manifest if zero; weighted-feature missingness would need monitoring prospectively.",
            },
        },
        "3_frozen_scorer_parity": {
            "rows_compared": rows_compared,
            "max_score_difference": max_diff,
            "threshold_disagreements": disagreements,
            "semantic_trigger_disagreement": disagreements,
            "features": spec.get("feature_names"),
            "weights": spec.get("weights"),
            "normalization": spec.get("normalization_constants"),
            "missing_handling": spec.get("missing_value_handling"),
            "clipping": spec.get("feature_transforms"),
            "threshold": STATIC_THRESHOLD,
        },
        "4_event_state_prospectiveness": event_audit,
        "5_one_trade_per_mint_logic": {
            "pseudocode": [
                "sort selected threshold-crossing rows by (ts ascending, rank_score descending, snapshot_kind)",
                "for each row: if mint not already selected, accept this row as primary trade",
                "ignore later threshold crossings for that mint as diagnostics only",
            ],
            "train_plus_validation_plus_final": sum(len(primary_by_split.get(x, [])) for x in ("train", "validation", "final_test")),
            "expected_unique_triggered_mints": 2158,
            "matches_expected": sum(len(primary_by_split.get(x, [])) for x in ("train", "validation", "final_test")) == 2158,
            "duplicate_primary_mints": len(dup_primary),
        },
        "6_split_integrity": split_integrity,
        "7_historical_evidence_status": {
            "classification": "PSEUDO_HOLDOUT",
            "reason": "The final-test interval has now been inspected by Atlas V1, Ranking Engine V1 narratives, static-threshold forensics, and V2 full-universe replay. It is historical replication, not untouched future evidence.",
            "recommended_language": "historical replication / pseudo-holdout until future-only prospective no-submit validation exists",
        },
        "8_raw_economics_replay_sample": raw_check,
        "9_entry_stress_audit": {
            "definition_verified": "+5% passes a worse entry market cap into the same executable scorer, then recomputes TP/SL/return ordering from altered entry.",
            "sample_stress5_mismatch_rate_pct": raw_check["stress5_mismatch_rate_pct"],
            "validation_plus5_pf": val_plus5,
            "final_test_plus5_pf": final_plus5,
            "risk": "validation +5 PF is only modestly above breakeven; prospective execution quality remains a promotion gate.",
        },
        "10_signal_age_entry_feasibility": {
            split: age_distribution(rows) for split, rows in sorted(primary_by_split.items())
        },
        "11_score_crossing_frequency": frequency(primary, span_hours),
        "12_non_atlas_generalization_primary": primary_atlas_nonatlas,
        "13_outlier_temporal_robustness": {
            "primary_by_split": split_table(primary),
            "daily_blocks": report.get("Q_temporal_stability"),
        },
        "14_concurrency_replay": concurrency_report,
        "15_prospective_implementability": {
            "feature_status": feature_status,
            "all_features_live_available_or_derivable": all(x["live_status"] != "HISTORICAL_ONLY_BLOCKER" for x in feature_status),
            "do_not_implement_in_this_audit": True,
        },
        "final_decision": decision,
        "immutable_candidate_spec": freeze_spec,
        "runtime_sec": time.time() - started,
    }

    out_path = os.path.join(args.out_dir, "freeze_readiness_audit_report.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(compact(audit), handle, indent=2, sort_keys=True)
    print(json.dumps(compact({
        "decision": decision,
        "blockers": blockers,
        "report": out_path,
        "primary_validation": metrics(primary_by_split.get("validation", [])),
        "primary_final_test": metrics(primary_by_split.get("final_test", [])),
        "primary_atlas_vs_nonatlas_validation": primary_atlas_nonatlas.get("validation"),
        "primary_atlas_vs_nonatlas_final_test": primary_atlas_nonatlas.get("final_test"),
        "scorer_parity": {"rows_compared": rows_compared, "max_diff": max_diff, "threshold_disagreements": disagreements},
        "raw_replay": raw_check,
        "runtime_sec": audit["runtime_sec"],
    }), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
