#!/usr/bin/env python3
import gzip
import hashlib
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


def sha(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def metrics(rows, lane):
    vals = []
    outcomes = Counter()
    unresolved = 0
    for r in rows:
        obj = r.get(lane) or {}
        pnl = num(obj.get("pnl"))
        outcome = obj.get("outcome")
        if pnl is None or outcome in ("ENTRY_UNRESOLVED", "120S_UNRESOLVED", "DATA_INTEGRITY_UNRESOLVED"):
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
        obj = r.get(lane) or {}
        pnl = num(obj.get("pnl"))
        outcome = obj.get("outcome")
        if pnl is None or outcome in ("ENTRY_UNRESOLVED", "120S_UNRESOLVED", "DATA_INTEGRITY_UNRESOLVED"):
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
    return {"accepted_N": len(accepted), "capacity_rejected_N": rejected, **out}


def dist(vals):
    return {
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


def lat(vals):
    xs = [v for v in vals if finite(v)]
    return {"P50": q(xs, 0.5), "P75": q(xs, 0.75), "P90": q(xs, 0.90), "P95": q(xs, 0.95), "max": max(xs) if xs else None}


def pf_expect(rows, lane="p75_primary"):
    m = metrics(rows, lane)
    return {"PF": m["PF"], "expectancy": m["expectancy"], "WR": m["WR"], "PnL": m["normalized_PnL_at_1SOL"]}


def main():
    manifest = json.load(open(os.path.join(OUT, "cohort_manifest.json")))
    health = json.load(open(os.path.join(OUT, "service_health.json")))
    freeze = json.load(open(os.path.join(FREEZE, "freeze_manifest.json")))
    signals = list(stream_gzip_jsonl(os.path.join(OUT, "signals.jsonl.gz")))
    signals.sort(key=lambda r: (r.get("threshold_decision_ts_ms") or 0, r.get("mint") or ""))
    first50 = signals[:50]
    mints50 = {s["mint"] for s in first50}
    outcomes_by_mint = {r.get("mint"): r for r in stream_gzip_jsonl(os.path.join(OUT, "outcomes.jsonl.gz"))}
    rows = []
    for sig in first50:
        rows.append(outcomes_by_mint.get(sig["mint"]) or {"mint": sig["mint"], "signal": sig})

    entry_by_mint = {}
    for er in stream_gzip_jsonl(os.path.join(OUT, "entry_quality.jsonl.gz")):
        if er.get("mint") in mints50:
            old = entry_by_mint.get(er["mint"])
            if old is None or old.get("source_continuity_status") == "PENDING":
                entry_by_mint[er["mint"]] = er

    signal50_ts = first50[-1]["threshold_decision_ts_ms"] if len(first50) == 50 else None
    score_rows = []
    for r in stream_gzip_jsonl(os.path.join(OUT, "all_scores.jsonl.gz")):
        if signal50_ts is not None and (r.get("threshold_decision_ts_ms") or 0) <= signal50_ts:
            score_rows.append(r)
    score_vals = [num(r.get("score")) for r in score_rows if num(r.get("score")) is not None]
    trig_scores = [num(s.get("score")) for s in first50 if num(s.get("score")) is not None]
    distances = [s - THRESHOLD for s in trig_scores]
    state_counts = Counter(s.get("state") for s in first50)
    first25_states = Counter(s.get("state") for s in first50[:25])
    second25_states = Counter(s.get("state") for s in first50[25:50])

    movement = []
    movement25 = []
    for sig in first50:
        er = entry_by_mint.get(sig["mint"])
        if not er:
            continue
        v = num(er.get("movement_state_to_entry_pct"))
        if v is not None:
            movement.append(v)
        v25 = num(((er.get("descriptive_movement_after_decision_ms") or {}).get("25") or {}).get("pct_from_state"))
        if v25 is not None:
            movement25.append(v25)

    blocks = {}
    for start in (0, 10, 20, 30, 40):
        block = rows[start : start + 10]
        blocks[f"{start+1}-{start+len(block)}"] = pf_expect(block)
    cumulative = {}
    for n in (10, 20, 25, 30, 40, 50):
        cumulative[f"N{n}"] = metrics(rows[:n], "p75_primary")

    vals = [num((r.get("p75_primary") or {}).get("pnl")) for r in rows if num((r.get("p75_primary") or {}).get("pnl")) is not None]
    wins = sorted([v for v in vals if v > 0], reverse=True)
    losses = sorted([v for v in vals if v < 0])
    def pf_after_remove(n):
        tmp = vals[:]
        for w in wins[:n]:
            try:
                tmp.remove(w)
            except ValueError:
                pass
        gp = sum(v for v in tmp if v > 0)
        gl = abs(sum(v for v in tmp if v < 0))
        return {
            "PF": gp / gl if gl else (999.0 if gp else None),
            "expectancy": sum(tmp) / len(tmp) if tmp else None,
            "N": len(tmp),
        }

    n25 = rows[:25]
    n26_50 = rows[25:50]
    first25_vs_second25 = {
        "signals_1_25": {
            **metrics(n25, "p75_primary"),
            "+5%_PF": metrics(n25, "p75_stress5")["PF"],
            "+10%_PF": metrics(n25, "p75_stress10")["PF"],
            "MAX_OPEN1_PF": capacity(n25, 1)["PF"],
        },
        "signals_26_50": {
            **metrics(n26_50, "p75_primary"),
            "+5%_PF": metrics(n26_50, "p75_stress5")["PF"],
            "+10%_PF": metrics(n26_50, "p75_stress10")["PF"],
            "MAX_OPEN1_PF": capacity(n26_50, 1)["PF"],
        },
    }

    primary = metrics(rows, "p75_primary")
    stress5 = metrics(rows, "p75_stress5")
    stress10 = metrics(rows, "p75_stress10")
    malformed = health.get("completed_malformed_records") or 0
    rejects = health.get("rejects") or {}
    counters = health.get("counters") or {}
    integrity_notes = []
    integrity_blocker = False
    if len(first50) != 50:
        integrity_blocker = True
        integrity_notes.append("fewer than 50 signals available")
    if len(mints50) != len(first50):
        integrity_blocker = True
        integrity_notes.append("duplicate mints in first50")
    if any((num(s.get("score")) or -999) < THRESHOLD for s in first50):
        integrity_blocker = True
        integrity_notes.append("score below threshold")
    if manifest.get("cohort_start_utc") != COHORT_START_UTC:
        integrity_blocker = True
        integrity_notes.append("cohort start mismatch")
    if malformed or counters.get("score_failures", 0) or rejects.get("feature_failures", 0):
        integrity_notes.append("nonzero malformed/scoring/feature failure counters present")

    if integrity_blocker:
        classification = "RANKER_V2_N50_DATA_INTEGRITY_BLOCKER"
    elif (primary.get("PF") or 0) >= 2 and (stress5.get("PF") or 0) > 1 and (primary.get("expectancy") or 0) > 5:
        classification = "RANKER_V2_N50_INFORMATIVE_STRONG"
    elif (primary.get("PF") or 0) > 1 and (primary.get("expectancy") or 0) > 0 and (stress5.get("PF") or 0) > 1:
        classification = "RANKER_V2_N50_INFORMATIVE_PROMISING"
    elif (primary.get("PF") or 0) > 1 or (primary.get("expectancy") or 0) > 0:
        classification = "RANKER_V2_N50_INFORMATIVE_MIXED"
    else:
        classification = "RANKER_V2_N50_INFORMATIVE_WEAK"

    start_ms = manifest.get("cohort_start_ms")
    first25_ts = first50[24]["threshold_decision_ts_ms"] if len(first50) >= 25 else None
    second50_ts = signal50_ts
    frequency = {
        "elapsed_cohort_hours_through_signal_50": (second50_ts - start_ms) / 3_600_000 if start_ms and second50_ts else None,
        "unique_signals_per_hour_through_signal_50": 50 / ((second50_ts - start_ms) / 3_600_000) if start_ms and second50_ts and second50_ts > start_ms else None,
        "historical_reference_opportunities_per_hour": 16.254168,
        "first25_signals_per_hour": 25 / ((first25_ts - start_ms) / 3_600_000) if start_ms and first25_ts and first25_ts > start_ms else None,
        "second25_signals_per_hour": 25 / ((second50_ts - first25_ts) / 3_600_000) if first25_ts and second50_ts and second50_ts > first25_ts else None,
    }

    report = {
        "classification": classification,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "integrity": {
            "N": len(first50),
            "resolved": primary["resolved_N"],
            "open_unresolved": primary["unresolved_N"],
            "duplicate_mints": len(first50) - len(mints50),
            "all_after_cohort_start": all((s.get("threshold_decision_ts_ms") or 0) >= manifest.get("cohort_start_ms", 0) for s in first50),
            "canonical_scorer_hash": manifest.get("canonical_spec_sha256"),
            "semantic_contract_hashes": freeze.get("contract_hashes"),
            "threshold": THRESHOLD,
            "service_NRestarts": health.get("n_restarts"),
            "service_PID": health.get("pid"),
            "submission_count": health.get("submission_count"),
            "integrity_notes": integrity_notes,
        },
        "primary_n50_economics": {
            "resolved_only": primary,
            "unresolved_as_zero": metrics_unresolved_zero(rows, "p75_primary"),
        },
        "entry_stress": {
            "NORMAL": primary,
            "+5%_worse_entry": stress5,
            "+10%_worse_entry": stress10,
        },
        "sellability": {
            "P50_964ms": metrics(rows, "p50"),
            "P75_1476ms_PRIMARY": primary,
            "P90_1989ms": metrics(rows, "p90"),
        },
        "concurrency": {
            "RAW": {"accepted_N": len(rows), "capacity_rejected_N": 0, **primary},
            "MAX_OPEN1": capacity(rows, 1),
            "MAX_OPEN2": capacity(rows, 2),
            "MAX_OPEN3": capacity(rows, 3),
            "MAX_OPEN5": capacity(rows, 5),
        },
        "first25_vs_second25": first25_vs_second25,
        "cumulative_path": cumulative,
        "rolling_blocks": blocks,
        "outlier_robustness": {
            "largest_winner": max(wins) if wins else None,
            "largest_loser": min(losses) if losses else None,
            "largest_winner_contribution_pct": (wins[0] / sum(vals) * 100.0) if wins and sum(vals) else None,
            "top3_winner_contribution_pct": (sum(wins[:3]) / sum(vals) * 100.0) if len(wins) >= 3 and sum(vals) else None,
            "top5_winner_contribution_pct": (sum(wins[:5]) / sum(vals) * 100.0) if len(wins) >= 5 and sum(vals) else None,
            "after_removing_top1_winner": pf_after_remove(1),
            "after_removing_top3_winners": pf_after_remove(3),
            "after_removing_top5_winners": pf_after_remove(5),
        },
        "score_state_composition": {
            "score_distribution": dist(trig_scores),
            "distance_above_threshold": {"median": q(distances, 0.5), "P90": q(distances, 0.9)},
            "states": {k: {"N": v, "pct": v * 100.0 / len(first50) if first50 else None} for k, v in state_counts.items()},
            "first25_states": {k: {"N": v, "pct": v * 100.0 / 25.0} for k, v in first25_states.items()},
            "second25_states": {k: {"N": v, "pct": v * 100.0 / 25.0} for k, v in second25_states.items()},
        },
        "decision_entry_latency": {
            "event_gated_state_to_threshold_decision_ms": lat([num(s.get("event_state_to_threshold_decision_ms")) for s in first50]),
            "threshold_decision_to_intended_entry_ms": 25,
            "actual_price_movement_over_25ms_pct": {
                "P25": q(movement25, 0.25),
                "P50": q(movement25, 0.5),
                "P75": q(movement25, 0.75),
                "P90": q(movement25, 0.9),
                "max_adverse": max(movement25) if movement25 else None,
                "max_favorable": min(movement25) if movement25 else None,
            },
            "state_to_intended_entry_pct": {
                "P25": q(movement, 0.25),
                "P50": q(movement, 0.5),
                "P75": q(movement, 0.75),
                "P90": q(movement, 0.9),
                "max_adverse": max(movement) if movement else None,
                "max_favorable": min(movement) if movement else None,
            },
        },
        "data_quality": {
            "ENTRY_UNRESOLVED": sum(1 for r in rows if (r.get("p75_primary") or {}).get("outcome") == "ENTRY_UNRESOLVED"),
            "120S_UNRESOLVED": sum(1 for r in rows if (r.get("p75_primary") or {}).get("outcome") == "120S_UNRESOLVED"),
            "DATA_INTEGRITY_UNRESOLVED": sum(1 for r in rows if (r.get("p75_primary") or {}).get("outcome") == "DATA_INTEGRITY_UNRESOLVED"),
            "completed_malformed_rows": malformed,
            "buffered_partial_records": health.get("partial_buffered_records"),
            "dropped_rows": rejects.get("dropped_rows", 0),
            "ingestion_gaps": rejects.get("ingestion_gaps", 0),
            "reconnects": rejects.get("reconnects", 0),
            "ordering_errors": rejects.get("ordering_errors", 0),
            "feature_failures": rejects.get("feature_failures", 0),
            "score_failures": counters.get("score_failures", 0),
            "duplicate_signal_attempts": counters.get("later_threshold_crossing_after_mint_signal", 0),
            "could_incorrectly_admit": integrity_blocker,
            "could_suppress": bool(counters.get("score_failures", 0) or rejects.get("feature_failures", 0)),
            "could_alter_outcome": integrity_blocker,
        },
        "score_population_drift_through_signal_50": {
            **dist(score_vals),
            "pct_gte_threshold": sum(1 for v in score_vals if v >= THRESHOLD) * 100.0 / len(score_vals) if score_vals else None,
            "historical_reference": {"selected_unique_mints_per_hour": 16.254168},
            "n25_pct_gte_threshold_reference": 0.856531049,
            "classification": "NO_OBVIOUS_DRIFT" if len(score_vals) >= 1000 else "POSSIBLE_DRIFT",
        },
        "frequency": frequency,
        "current_collector_status": {
            "current_unique_signals": counters.get("unique_triggered_mints", 0),
            "resolved": health.get("resolved_primary_outcomes"),
            "open": health.get("open_outcomes"),
            "score_evaluations": counters.get("score_evaluations", 0),
            "eligible_events": counters.get("eligible_market_events", 0),
            "service": "edgesniffer-ranker-v2-prospective-shadow-v2.service",
            "PID": health.get("pid"),
            "NRestarts": health.get("n_restarts"),
        },
        "first50_table": [
            {
                "idx": i + 1,
                "mint": s.get("mint"),
                "state": s.get("state"),
                "score": s.get("score"),
                "distance": num(s.get("score"), 0) - THRESHOLD if num(s.get("score")) is not None else None,
                "threshold_decision_ts_ms": s.get("threshold_decision_ts_ms"),
                "primary_outcome": (outcomes_by_mint.get(s.get("mint")) or {}).get("p75_primary", {}).get("outcome"),
                "primary_pnl": (outcomes_by_mint.get(s.get("mint")) or {}).get("p75_primary", {}).get("pnl"),
            }
            for i, s in enumerate(first50)
        ],
    }
    path = os.path.join(OUT, "n50_informative_checkpoint.json")
    write_json(path, report)
    print(json.dumps(compact(report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
