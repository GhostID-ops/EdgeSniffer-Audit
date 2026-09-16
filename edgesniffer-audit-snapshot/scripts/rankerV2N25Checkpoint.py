#!/usr/bin/env python3
import gzip
import json
import math
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone


OUT = "/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-prospective-shadow-v2"
FREEZE = "/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-event-gated-semantic-freeze"
THRESHOLD = 0.590596
COHORT_START_UTC = "2026-09-03T14:26:22.198000Z"


def finite(v):
    return isinstance(v, (int, float)) and math.isfinite(v)


def num(v, default=None):
    try:
        out = float(v)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def q(vals, pct):
    xs = sorted(v for v in vals if finite(v))
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


def compact(v):
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 9)
    if isinstance(v, dict):
        return {k: compact(x) for k, x in v.items()}
    if isinstance(v, list):
        return [compact(x) for x in v]
    return v


def stream_gzip_jsonl(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    proc = subprocess.Popen(["gzip", "-cd", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    assert proc.stdout is not None
    for line in proc.stdout:
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue
    proc.wait()


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(compact(obj), f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def metrics(rows, lane):
    vals = []
    outcomes = Counter()
    unresolved = 0
    for r in rows:
        obj = (r.get(lane) or {})
        outcome = obj.get("outcome")
        pnl = num(obj.get("pnl"))
        if outcome in ("ENTRY_UNRESOLVED", "120S_UNRESOLVED", "DATA_INTEGRITY_UNRESOLVED") or pnl is None:
            unresolved += 1
            continue
        vals.append(pnl)
        outcomes[outcome] += 1
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    return {
        "total_N": len(rows),
        "resolved_N": len(vals),
        "unresolved_N": unresolved,
        "wins": len(wins),
        "losses": len(losses),
        "WR": len(wins) * 100.0 / len(vals) if vals else None,
        "PF": gp / gl if gl else (999.0 if gp else None),
        "expectancy": sum(vals) / len(vals) if vals else None,
        "median_return": q(vals, 0.5),
        "normalized_PnL_at_1SOL": sum(vals) / 100.0,
        "TP": outcomes.get("TP", 0),
        "SL": outcomes.get("SL", 0),
        "TRUE_120S_MAX_HOLD": outcomes.get("TRUE_120S_MAX_HOLD", 0),
    }


def metrics_unresolved_zero(rows, lane):
    vals = []
    outcomes = Counter()
    unresolved = 0
    for r in rows:
        obj = (r.get(lane) or {})
        outcome = obj.get("outcome")
        pnl = num(obj.get("pnl"))
        if outcome in ("ENTRY_UNRESOLVED", "120S_UNRESOLVED", "DATA_INTEGRITY_UNRESOLVED") or pnl is None:
            vals.append(0.0)
            unresolved += 1
            continue
        vals.append(pnl)
        outcomes[outcome] += 1
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    return {
        "total_N": len(rows),
        "resolved_N": len(rows) - unresolved,
        "unresolved_as_0_N": unresolved,
        "wins": len(wins),
        "losses": len(losses),
        "WR": len(wins) * 100.0 / len(vals) if vals else None,
        "PF": gp / gl if gl else (999.0 if gp else None),
        "expectancy": sum(vals) / len(vals) if vals else None,
        "median_return": q(vals, 0.5),
        "normalized_PnL_at_1SOL": sum(vals) / 100.0,
        "TP": outcomes.get("TP", 0),
        "SL": outcomes.get("SL", 0),
        "TRUE_120S_MAX_HOLD": outcomes.get("TRUE_120S_MAX_HOLD", 0),
    }


def capacity(rows, max_open):
    active = []
    accepted = []
    rejected = 0
    for row in sorted(rows, key=lambda r: r["signal"]["threshold_decision_ts_ms"]):
        ts = row["signal"]["threshold_decision_ts_ms"]
        active = [end for end in active if end > ts]
        if len(active) >= max_open:
            rejected += 1
            continue
        accepted.append(row)
        out = row.get("p75_primary") or {}
        active.append(out.get("exit_ts") or (ts + 120_025))
    out = metrics(accepted, "p75_primary")
    return {"accepted_N": len(accepted), "rejected_due_to_capacity": rejected, **out}


def sha(path):
    if not os.path.exists(path):
        return None
    h = __import__("hashlib").sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    manifest = json.load(open(os.path.join(OUT, "cohort_manifest.json")))
    health = json.load(open(os.path.join(OUT, "service_health.json")))
    freeze = json.load(open(os.path.join(FREEZE, "freeze_manifest.json")))
    signals = list(stream_gzip_jsonl(os.path.join(OUT, "signals.jsonl.gz")))
    signals.sort(key=lambda r: (r.get("threshold_decision_ts_ms") or 0, r.get("mint") or ""))
    first25 = signals[:25]
    first25_mints = {r["mint"] for r in first25}
    outcomes_all = list(stream_gzip_jsonl(os.path.join(OUT, "outcomes.jsonl.gz")))
    outcomes_by_mint = {r.get("mint"): r for r in outcomes_all}
    rows = []
    for sig in first25:
        row = outcomes_by_mint.get(sig["mint"])
        if row is None:
            row = {"mint": sig["mint"], "signal": sig}
        rows.append(row)

    entry_rows_by_mint = {}
    for er in stream_gzip_jsonl(os.path.join(OUT, "entry_quality.jsonl.gz")):
        if er.get("mint") in first25_mints:
            entry_rows_by_mint[er["mint"]] = er

    signal25_ts = first25[-1]["threshold_decision_ts_ms"] if len(first25) == 25 else None
    score_rows = []
    for r in stream_gzip_jsonl(os.path.join(OUT, "all_scores.jsonl.gz")):
        if signal25_ts is not None and (r.get("threshold_decision_ts_ms") or 0) <= signal25_ts:
            score_rows.append(r)
    score_vals = [num(r.get("score")) for r in score_rows if num(r.get("score")) is not None]
    triggered_score_vals = [num(r.get("score")) for r in first25 if num(r.get("score")) is not None]
    dist = lambda vals: {
        "N": len(vals),
        "min": min(vals) if vals else None,
        "median": q(vals, 0.5),
        "mean": sum(vals) / len(vals) if vals else None,
        "P75": q(vals, 0.75),
        "P90": q(vals, 0.90),
        "P95": q(vals, 0.95),
        "P99": q(vals, 0.99),
        "P99_5": q(vals, 0.995),
        "max": max(vals) if vals else None,
    }

    state_counts = Counter(sig.get("state") or sig.get("historical_state_alias") for sig in first25)
    lat_state_to_decision = [num(s.get("event_state_to_threshold_decision_ms")) for s in first25]
    lat_feature_to_score = [num(s.get("feature_ready_to_score_ready_ms")) for s in first25]
    lat_input_to_decision = [
        num(s.get("threshold_decision_ts_ms")) - num(s.get("final_required_input_received_ts_ms"))
        for s in first25
        if num(s.get("threshold_decision_ts_ms")) is not None and num(s.get("final_required_input_received_ts_ms")) is not None
    ]
    lat_summary = lambda vals: {"P50": q(vals, 0.5), "P75": q(vals, 0.75), "P90": q(vals, 0.90), "max": max([v for v in vals if finite(v)], default=None)}

    movement = []
    movement_after = {str(ms): [] for ms in (25, 50, 100, 250, 500)}
    for sig in first25:
        er = entry_rows_by_mint.get(sig["mint"])
        if not er:
            continue
        mv = num(er.get("movement_state_to_entry_pct"))
        if mv is not None:
            movement.append(mv)
        for ms, obj in (er.get("descriptive_movement_after_decision_ms") or {}).items():
            if ms in movement_after:
                v = num((obj or {}).get("pct_from_state"))
                if v is not None:
                    movement_after[ms].append(v)

    resolved_only = metrics(rows, "p75_primary")
    unresolved_zero = metrics_unresolved_zero(rows, "p75_primary")
    stress = {
        "NORMAL": metrics(rows, "p75_primary"),
        "PLUS_5_PERCENT_WORSE_ENTRY": metrics(rows, "p75_stress5"),
        "PLUS_10_PERCENT_WORSE_ENTRY": metrics(rows, "p75_stress10"),
    }
    sellability = {
        "P50_964ms": metrics(rows, "p50"),
        "P75_1476ms_PRIMARY": metrics(rows, "p75_primary"),
        "P90_1989ms": metrics(rows, "p90"),
    }
    concurrency = {
        "RAW": {"accepted_N": len(rows), "rejected_due_to_capacity": 0, **metrics(rows, "p75_primary")},
        "MAX_OPEN1": capacity(rows, 1),
        "MAX_OPEN2": capacity(rows, 2),
        "MAX_OPEN3": capacity(rows, 3),
        "MAX_OPEN5": capacity(rows, 5),
    }

    vals = [num((r.get("p75_primary") or {}).get("pnl")) for r in rows if num((r.get("p75_primary") or {}).get("pnl")) is not None]
    wins = sorted([v for v in vals if v > 0], reverse=True)
    total_pnl = sum(vals)
    pf_after_top1 = None
    if wins:
        tmp = vals[:]
        tmp.remove(wins[0])
        gp = sum(v for v in tmp if v > 0)
        gl = abs(sum(v for v in tmp if v < 0))
        pf_after_top1 = gp / gl if gl else (999.0 if gp else None)
    temporal_1_12 = metrics(rows[:12], "p75_primary")
    temporal_13_25 = metrics(rows[12:25], "p75_primary")

    integrity_blocker = False
    integrity_notes = []
    if len(first25) < 25:
        integrity_blocker = True
        integrity_notes.append("fewer than 25 signals available")
    if len(first25_mints) != len(first25):
        integrity_blocker = True
        integrity_notes.append("duplicate mints in first25")
    if any(num(s.get("score")) is None or num(s.get("score")) < THRESHOLD for s in first25):
        integrity_blocker = True
        integrity_notes.append("score below threshold in first25")
    if manifest.get("cohort_start_utc") != COHORT_START_UTC:
        integrity_blocker = True
        integrity_notes.append("cohort start mismatch")
    malformed = health.get("completed_malformed_records") or 0
    scoring_failures = (health.get("counters") or {}).get("score_failures", 0)
    feature_failures = (health.get("rejects") or {}).get("feature_failures", 0)
    if malformed or scoring_failures or feature_failures:
        integrity_notes.append("nonzero malformed/scoring/feature failure counters present")

    if integrity_blocker:
        classification = "RANKER_V2_N25_DATA_INTEGRITY_BLOCKER"
    elif (resolved_only.get("PF") or 0) <= 1 or (resolved_only.get("expectancy") or 0) <= 0:
        classification = "RANKER_V2_N25_DESCRIPTIVE_ECONOMICS_WEAK"
    else:
        classification = "RANKER_V2_N25_DESCRIPTIVE_HEALTHY"

    report = {
        "classification": classification,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cohort_integrity": {
            "N": len(first25),
            "resolved": resolved_only["resolved_N"],
            "unresolved_open": 25 - resolved_only["resolved_N"] if len(first25) >= 25 else None,
            "duplicate_mints": len(first25) - len(first25_mints),
            "all_after_cohort_start": all((s.get("threshold_decision_ts_ms") or 0) >= manifest.get("cohort_start_ms", 0) for s in first25),
            "all_score_gte_threshold": all((num(s.get("score")) or -999) >= THRESHOLD for s in first25),
            "scorer_hash": manifest.get("canonical_spec_sha256"),
            "semantic_contract_hashes": freeze.get("contract_hashes"),
            "service_pid": health.get("pid"),
            "NRestarts": health.get("n_restarts"),
            "submission_count": health.get("submission_count"),
            "no_submit": health.get("no_submit"),
        },
        "primary_economics": {
            "resolved_only": resolved_only,
            "unresolved_as_zero": unresolved_zero,
        },
        "entry_stress": stress,
        "sellability_sensitivity": sellability,
        "concurrency": concurrency,
        "signal_score_distribution": {
            **dist(triggered_score_vals),
            "mean_distance_above_threshold": sum(v - THRESHOLD for v in triggered_score_vals) / len(triggered_score_vals) if triggered_score_vals else None,
            "median_distance_above_threshold": q([v - THRESHOLD for v in triggered_score_vals], 0.5),
        },
        "state_distribution": {
            state: {"N": n, "pct_of_N25": n * 100.0 / len(first25) if first25 else None}
            for state, n in state_counts.items()
        },
        "decision_latency": {
            "event_gated_state_to_threshold_decision_ms": lat_summary(lat_state_to_decision),
            "feature_ready_to_score_ready_ms": lat_summary(lat_feature_to_score),
            "final_required_input_receive_to_threshold_decision_ms": lat_summary(lat_input_to_decision),
        },
        "entry_movement": {
            "state_to_intended_entry_pct": {
                "P25": q(movement, 0.25),
                "P50": q(movement, 0.5),
                "P75": q(movement, 0.75),
                "P90": q(movement, 0.9),
                "max_adverse_move": max(movement) if movement else None,
                "max_favorable_move": min(movement) if movement else None,
            },
            "descriptive_after_decision_pct": {
                ms: {"P50": q(vals, 0.5), "P75": q(vals, 0.75), "P90": q(vals, 0.9), "N": len(vals)}
                for ms, vals in movement_after.items()
            },
        },
        "unresolved_data_quality": {
            "ENTRY_UNRESOLVED": sum(1 for r in rows if (r.get("p75_primary") or {}).get("outcome") == "ENTRY_UNRESOLVED"),
            "120S_UNRESOLVED": sum(1 for r in rows if (r.get("p75_primary") or {}).get("outcome") == "120S_UNRESOLVED"),
            "DATA_INTEGRITY_UNRESOLVED": sum(1 for r in rows if (r.get("p75_primary") or {}).get("outcome") == "DATA_INTEGRITY_UNRESOLVED"),
            "completed_malformed_records": malformed,
            "partial_records_successfully_buffered_current": health.get("partial_buffered_records"),
            "dropped_rows": (health.get("rejects") or {}).get("dropped_rows", 0),
            "ingestion_gaps": (health.get("rejects") or {}).get("ingestion_gaps", 0),
            "reconnects": (health.get("rejects") or {}).get("reconnects", 0),
            "ordering_errors": (health.get("rejects") or {}).get("ordering_errors", 0),
            "feature_failures": feature_failures,
            "scoring_failures": scoring_failures,
            "duplicate_trigger_attempts": (health.get("counters") or {}).get("later_threshold_crossing_after_mint_signal", 0),
            "integrity_notes": integrity_notes,
            "could_false_admit_signal": integrity_blocker,
            "could_suppress_signal": bool(scoring_failures or feature_failures),
            "could_change_outcome": integrity_blocker,
        },
        "score_population_through_signal_25": {
            **dist(score_vals),
            "pct_gte_threshold": sum(1 for v in score_vals if v >= THRESHOLD) * 100.0 / len(score_vals) if score_vals else None,
            "drift_classification": "POSSIBLE_DRIFT" if len(score_vals) < 1000 else "NO_OBVIOUS_DRIFT",
        },
        "temporal_descriptive_split": {
            "signals_1_to_12": temporal_1_12,
            "signals_13_to_25": temporal_13_25,
        },
        "outlier_dependence": {
            "largest_winner_contribution_pct": wins[0] / total_pnl * 100.0 if wins and total_pnl else None,
            "top3_winners_contribution_pct": sum(wins[:3]) / total_pnl * 100.0 if len(wins) >= 3 and total_pnl else None,
            "largest_loser": min(vals) if vals else None,
            "PF_after_removing_top1_winner": pf_after_top1,
            "note": "Descriptive only; N25 is too small for robustness verdict.",
        },
        "current_collector_status": {
            "current_unique_signals": (health.get("counters") or {}).get("unique_triggered_mints", 0),
            "resolved": health.get("resolved_primary_outcomes"),
            "open": health.get("open_outcomes"),
            "score_evaluations": (health.get("counters") or {}).get("score_evaluations", 0),
            "eligible_events": (health.get("counters") or {}).get("eligible_market_events", 0),
            "service": "edgesniffer-ranker-v2-prospective-shadow-v2.service",
            "PID": health.get("pid"),
            "NRestarts": health.get("n_restarts"),
        },
        "first25_trigger_table": [
            {
                "idx": i + 1,
                "mint": s.get("mint"),
                "state": s.get("state"),
                "historical_state_alias": s.get("historical_state_alias"),
                "threshold_decision_ts_ms": s.get("threshold_decision_ts_ms"),
                "score": s.get("score"),
                "distance": num(s.get("score"), 0) - THRESHOLD if num(s.get("score")) is not None else None,
                "primary_outcome": (outcomes_by_mint.get(s.get("mint")) or {}).get("p75_primary", {}).get("outcome"),
                "primary_pnl": (outcomes_by_mint.get(s.get("mint")) or {}).get("p75_primary", {}).get("pnl"),
            }
            for i, s in enumerate(first25)
        ],
    }
    write_json(os.path.join(OUT, "n25_descriptive_checkpoint.json"), report)
    print(json.dumps(compact(report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
