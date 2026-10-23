#!/usr/bin/env python3
import json
import math
import os
import random
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
    proc = subprocess.Popen(
        ["gzip", "-cd", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
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


def metrics(rows, lane="p75_primary"):
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


def metrics_unresolved_zero(rows, lane="p75_primary"):
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
    for row in sorted(rows, key=lambda r: ((r.get("signal") or {}).get("threshold_decision_ts_ms") or 0)):
        ts = (row.get("signal") or {}).get("threshold_decision_ts_ms") or 0
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
    xs = [v for v in vals if finite(v)]
    return {
        "N": len(xs),
        "min": min(xs) if xs else None,
        "P10": q(xs, 0.10),
        "P25": q(xs, 0.25),
        "P50": q(xs, 0.50),
        "median": q(xs, 0.50),
        "mean": sum(xs) / len(xs) if xs else None,
        "P75": q(xs, 0.75),
        "P90": q(xs, 0.90),
        "P95": q(xs, 0.95),
        "P99": q(xs, 0.99),
        "P99_5": q(xs, 0.995),
        "max": max(xs) if xs else None,
    }


def lat(vals):
    xs = [v for v in vals if finite(v)]
    return {"P50": q(xs, 0.50), "P75": q(xs, 0.75), "P90": q(xs, 0.90), "P95": q(xs, 0.95), "max": max(xs) if xs else None}


def pf_exp(rows, lane="p75_primary"):
    m = metrics(rows, lane)
    return {"WR": m["WR"], "PF": m["PF"], "expectancy": m["expectancy"], "PnL": m["normalized_PnL_at_1SOL"]}


def remove_top_winners(vals, n):
    tmp = list(vals)
    for w in sorted([v for v in vals if v > 0], reverse=True)[:n]:
        try:
            tmp.remove(w)
        except ValueError:
            pass
    wins = [v for v in tmp if v > 0]
    losses = [v for v in tmp if v < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    return {
        "N": len(tmp),
        "PF": gp / gl if gl else (999.0 if gp else None),
        "expectancy": sum(tmp) / len(tmp) if tmp else None,
        "PnL": sum(tmp) / 100.0,
    }


def bootstrap(vals, iterations=10000):
    if not vals:
        return {}
    rng = random.Random(590596)
    means = []
    pfs = []
    n = len(vals)
    for _ in range(iterations):
        sample = [vals[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
        gp = sum(v for v in sample if v > 0)
        gl = abs(sum(v for v in sample if v < 0))
        pfs.append(gp / gl if gl else (999.0 if gp else 0.0))
    return {
        "observed_expectancy": sum(vals) / n,
        "bootstrap_median": q(means, 0.50),
        "interval_80": [q(means, 0.10), q(means, 0.90)],
        "interval_90": [q(means, 0.05), q(means, 0.95)],
        "interval_95": [q(means, 0.025), q(means, 0.975)],
        "pct_expectancy_gt_0": sum(1 for v in means if v > 0) * 100.0 / iterations,
        "pct_pf_gt_1": sum(1 for v in pfs if v > 1) * 100.0 / iterations,
    }


def summarize_groups(rows, key_fn, lane="p75_primary"):
    groups = {}
    for r in rows:
        groups.setdefault(key_fn(r), []).append(r)
    return {k: {**metrics(v, lane), "share_pct": len(v) * 100.0 / len(rows) if rows else None} for k, v in sorted(groups.items(), key=lambda x: str(x[0]))}


def descriptive_by_outcome(rows):
    winners = [r for r in rows if num((r.get("p75_primary") or {}).get("pnl")) is not None and num((r.get("p75_primary") or {}).get("pnl")) > 0]
    losers = [r for r in rows if num((r.get("p75_primary") or {}).get("pnl")) is not None and num((r.get("p75_primary") or {}).get("pnl")) < 0]

    def values(rs, getter):
        out = []
        for r in rs:
            v = getter(r)
            if finite(v):
                out.append(v)
        return dist(out)

    def snapshot(rs):
        return {
            "score": values(rs, lambda r: num((r.get("signal") or {}).get("score"))),
            "mint_age_ms": values(rs, lambda r: num((r.get("signal") or {}).get("mint_age_ms"))),
            "mc_usd": values(rs, lambda r: num((r.get("signal") or {}).get("mc_usd"))),
            "entry_movement_25ms_pct": values(rs, lambda r: num(((r.get("entry_quality") or {}).get("descriptive_movement_after_decision_ms") or {}).get("25", {}).get("pct_from_state"))),
            "max_favorable_excursion": values(rs, lambda r: num((r.get("p75_primary") or {}).get("max_return_after_sellable"))),
            "max_adverse_excursion": values(rs, lambda r: num((r.get("p75_primary") or {}).get("min_return_after_sellable"))),
            "outcomes": dict(Counter((r.get("p75_primary") or {}).get("outcome") for r in rs)),
            "states": dict(Counter((r.get("signal") or {}).get("state") for r in rs)),
        }

    return {"winners": snapshot(winners), "losers": snapshot(losers)}


def main():
    manifest = json.load(open(os.path.join(OUT, "cohort_manifest.json"), encoding="utf-8"))
    health = json.load(open(os.path.join(OUT, "service_health.json"), encoding="utf-8"))
    freeze = json.load(open(os.path.join(FREEZE, "freeze_manifest.json"), encoding="utf-8"))

    signals = list(stream_gzip_jsonl(os.path.join(OUT, "signals.jsonl.gz")))
    signals.sort(key=lambda r: (r.get("threshold_decision_ts_ms") or 0, r.get("mint") or ""))
    n100_signals = signals[:100]
    n100_mints = {s.get("mint") for s in n100_signals}

    outcomes_by_mint = {}
    for r in stream_gzip_jsonl(os.path.join(OUT, "outcomes.jsonl.gz")):
        if r.get("mint"):
            outcomes_by_mint[r["mint"]] = r

    entry_by_mint = {}
    for r in stream_gzip_jsonl(os.path.join(OUT, "entry_quality.jsonl.gz")):
        mint = r.get("mint")
        if mint in n100_mints:
            old = entry_by_mint.get(mint)
            if old is None or old.get("source_continuity_status") == "PENDING":
                entry_by_mint[mint] = r

    rows = []
    for sig in n100_signals:
        row = outcomes_by_mint.get(sig.get("mint")) or {"mint": sig.get("mint"), "signal": sig}
        row["signal"] = row.get("signal") or sig
        row["entry_quality"] = entry_by_mint.get(sig.get("mint")) or {}
        rows.append(row)

    signal100_ts = n100_signals[-1]["threshold_decision_ts_ms"] if len(n100_signals) >= 100 else None
    score_rows = []
    for r in stream_gzip_jsonl(os.path.join(OUT, "all_scores.jsonl.gz")):
        if signal100_ts is not None and (r.get("threshold_decision_ts_ms") or 0) <= signal100_ts:
            score_rows.append(r)

    score_vals = [num(r.get("score")) for r in score_rows if num(r.get("score")) is not None]
    trig_scores = [num((r.get("signal") or {}).get("score")) for r in rows if num((r.get("signal") or {}).get("score")) is not None]
    pnl_vals = [num((r.get("p75_primary") or {}).get("pnl")) for r in rows if num((r.get("p75_primary") or {}).get("pnl")) is not None]

    primary = metrics(rows, "p75_primary")
    stress5 = metrics(rows, "p75_stress5")
    stress10 = metrics(rows, "p75_stress10")
    p50 = metrics(rows, "p50")
    p90 = metrics(rows, "p90")

    sellability_changes = {
        "p50_vs_p75_changed_outcome": sum(1 for r in rows if (r.get("p50") or {}).get("outcome") != (r.get("p75_primary") or {}).get("outcome")),
        "p75_vs_p90_changed_outcome": sum(1 for r in rows if (r.get("p75_primary") or {}).get("outcome") != (r.get("p90") or {}).get("outcome")),
    }

    first50 = rows[:50]
    second50 = rows[50:100]
    first50_vs_second50 = {
        "signals_1_50": {
            **metrics(first50, "p75_primary"),
            "+5%": metrics(first50, "p75_stress5"),
            "+10%": metrics(first50, "p75_stress10"),
            "MAX_OPEN1": capacity(first50, 1),
        },
        "signals_51_100": {
            **metrics(second50, "p75_primary"),
            "+5%": metrics(second50, "p75_stress5"),
            "+10%": metrics(second50, "p75_stress10"),
            "MAX_OPEN1": capacity(second50, 1),
        },
    }

    four_blocks = {}
    for start, end in ((0, 25), (25, 50), (50, 75), (75, 100)):
        block = rows[start:end]
        four_blocks[f"{start+1}-{end}"] = {**pf_exp(block), "+5%_PF": metrics(block, "p75_stress5")["PF"]}

    cumulative_path = {}
    for n in (10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90, 100):
        cumulative_path[f"N{n}"] = metrics(rows[:n], "p75_primary")

    concurrency = {
        "RAW": {"accepted_N": len(rows), "capacity_rejected_N": 0, **primary},
        "MAX_OPEN1": capacity(rows, 1),
        "MAX_OPEN2": capacity(rows, 2),
        "MAX_OPEN3": capacity(rows, 3),
        "MAX_OPEN5": capacity(rows, 5),
        "MAX_OPEN1_signals_1_50": capacity(first50, 1),
        "MAX_OPEN1_signals_51_100": capacity(second50, 1),
    }

    wins = sorted([v for v in pnl_vals if v > 0], reverse=True)
    total_pnl_pct = sum(pnl_vals)
    outlier = {
        "largest_winner": max(wins) if wins else None,
        "largest_loser": min([v for v in pnl_vals if v < 0], default=None),
        "top1_winner_contribution_pct": (sum(wins[:1]) / total_pnl_pct * 100.0) if total_pnl_pct else None,
        "top3_winner_contribution_pct": (sum(wins[:3]) / total_pnl_pct * 100.0) if total_pnl_pct else None,
        "top5_winner_contribution_pct": (sum(wins[:5]) / total_pnl_pct * 100.0) if total_pnl_pct else None,
        "top10_winner_contribution_pct": (sum(wins[:10]) / total_pnl_pct * 100.0) if total_pnl_pct else None,
        "after_removing_top1_winner": remove_top_winners(pnl_vals, 1),
        "after_removing_top3_winners": remove_top_winners(pnl_vals, 3),
        "after_removing_top5_winners": remove_top_winners(pnl_vals, 5),
        "after_removing_top10_winners": remove_top_winners(pnl_vals, 10),
    }

    state_counts = Counter((r.get("signal") or {}).get("state") for r in rows)
    first50_state_counts = Counter((r.get("signal") or {}).get("state") for r in first50)
    second50_state_counts = Counter((r.get("signal") or {}).get("state") for r in second50)
    state_comp = {
        "N100": {k: {"N": v, "share_pct": v * 100.0 / len(rows)} for k, v in sorted(state_counts.items())},
        "signals_1_50": {k: {"N": v, "share_pct": v * 100.0 / 50.0} for k, v in sorted(first50_state_counts.items())},
        "signals_51_100": {k: {"N": v, "share_pct": v * 100.0 / 50.0} for k, v in sorted(second50_state_counts.items())},
    }

    movement25 = []
    for r in rows:
        v = num(((r.get("entry_quality") or {}).get("descriptive_movement_after_decision_ms") or {}).get("25", {}).get("pct_from_state"))
        if v is not None:
            movement25.append(v)
    movement = {
        "P10": q(movement25, 0.10),
        "P25": q(movement25, 0.25),
        "P50": q(movement25, 0.50),
        "P75": q(movement25, 0.75),
        "P90": q(movement25, 0.90),
        "P95": q(movement25, 0.95),
        "max_favorable": min(movement25) if movement25 else None,
        "max_adverse": max(movement25) if movement25 else None,
        "pct_adverse_gt_1": sum(1 for v in movement25 if v > 1) * 100.0 / len(movement25) if movement25 else None,
        "pct_adverse_gt_2": sum(1 for v in movement25 if v > 2) * 100.0 / len(movement25) if movement25 else None,
        "pct_adverse_gt_5": sum(1 for v in movement25 if v > 5) * 100.0 / len(movement25) if movement25 else None,
        "pct_adverse_gt_10": sum(1 for v in movement25 if v > 10) * 100.0 / len(movement25) if movement25 else None,
    }

    create_to_state = [num((r.get("signal") or {}).get("mint_age_ms")) for r in rows]
    state_to_decision = [num((r.get("signal") or {}).get("event_state_to_threshold_decision_ms")) for r in rows]
    final_input_to_decision = []
    for r in rows:
        s = r.get("signal") or {}
        a = num(s.get("threshold_decision_ts_ms"))
        b = num(s.get("final_required_input_received_ts_ms"))
        if a is not None and b is not None:
            final_input_to_decision.append(a - b)

    latency = {
        "create_to_event_gated_state_ms_from_mint_age": lat(create_to_state),
        "event_gated_state_to_threshold_decision_ms": lat(state_to_decision),
        "final_required_input_receive_to_threshold_decision_ms": lat(final_input_to_decision),
        "threshold_decision_latency_1_50": lat([num((r.get("signal") or {}).get("event_state_to_threshold_decision_ms")) for r in first50]),
        "threshold_decision_latency_51_100": lat([num((r.get("signal") or {}).get("event_state_to_threshold_decision_ms")) for r in second50]),
    }

    malformed = health.get("completed_malformed_records") or 0
    rejects = health.get("rejects") or {}
    counters = health.get("counters") or {}
    integrity_notes = []
    integrity_blocker = False
    if len(rows) != 100:
        integrity_blocker = True
        integrity_notes.append("fewer_than_100_signals")
    if len(n100_mints) != len(rows):
        integrity_blocker = True
        integrity_notes.append("duplicate_mints_in_n100")
    if any((num((r.get("signal") or {}).get("score")) or -999) < THRESHOLD for r in rows):
        integrity_blocker = True
        integrity_notes.append("score_below_threshold")
    if manifest.get("cohort_start_utc") != COHORT_START_UTC:
        integrity_blocker = True
        integrity_notes.append("cohort_start_mismatch")

    data_quality = {
        "ENTRY_UNRESOLVED": sum(1 for r in rows if (r.get("p75_primary") or {}).get("outcome") == "ENTRY_UNRESOLVED"),
        "120S_UNRESOLVED": sum(1 for r in rows if (r.get("p75_primary") or {}).get("outcome") == "120S_UNRESOLVED"),
        "DATA_INTEGRITY_UNRESOLVED": sum(1 for r in rows if (r.get("p75_primary") or {}).get("outcome") == "DATA_INTEGRITY_UNRESOLVED"),
        "completed_malformed_rows": malformed,
        "buffered_partial_records": health.get("partial_buffered_records"),
        "dropped_rows": rejects.get("dropped_rows", 0),
        "ingestion_gaps": rejects.get("ingestion_gaps", 0),
        "reconnects": rejects.get("reconnects", 0),
        "timestamp_order_errors": rejects.get("ordering_errors", 0),
        "feature_failures": rejects.get("feature_failures", 0),
        "score_failures": counters.get("score_failures", 0),
        "duplicate_primary_trigger_attempts": counters.get("later_threshold_crossing_after_mint_signal", 0),
        "service_restarts": health.get("n_restarts"),
        "submission_count": health.get("submission_count"),
        "could_falsely_admit": integrity_blocker,
        "could_suppress": bool(counters.get("score_failures", 0) or rejects.get("feature_failures", 0)),
        "could_materially_alter_outcome": integrity_blocker,
    }

    score_drift = {
        **dist(score_vals),
        "pct_gte_threshold": sum(1 for v in score_vals if v >= THRESHOLD) * 100.0 / len(score_vals) if score_vals else None,
        "historical_frozen_population": {"selected_unique_mints_per_hour": 16.254168},
        "n25_pct_gte_threshold_reference": 0.856531049,
        "n50_pct_gte_threshold_reference": 1.08398232,
    }
    pct = score_drift.get("pct_gte_threshold")
    score_drift["classification"] = "MATERIAL_DRIFT" if pct is not None and pct > 3.0 else ("POSSIBLE_DRIFT" if pct is not None and pct > 2.0 else "NO_OBVIOUS_DRIFT")

    start_ms = manifest.get("cohort_start_ms")
    sig50_ts = rows[49]["signal"]["threshold_decision_ts_ms"] if len(rows) >= 50 else None
    sig100_ts = signal100_ts
    spacing = []
    prev = None
    for s in n100_signals:
        ts = s.get("threshold_decision_ts_ms")
        if prev is not None and ts is not None:
            spacing.append((ts - prev) / 1000.0)
        prev = ts
    frequency = {
        "elapsed_cohort_hours_through_signal_100": (sig100_ts - start_ms) / 3_600_000 if start_ms and sig100_ts else None,
        "signals_per_hour_through_100": 100 / ((sig100_ts - start_ms) / 3_600_000) if start_ms and sig100_ts and sig100_ts > start_ms else None,
        "historical_reference_rate": 16.254168,
        "first50_signals_per_hour": 50 / ((sig50_ts - start_ms) / 3_600_000) if start_ms and sig50_ts and sig50_ts > start_ms else None,
        "second50_signals_per_hour": 50 / ((sig100_ts - sig50_ts) / 3_600_000) if sig50_ts and sig100_ts and sig100_ts > sig50_ts else None,
        "signal_spacing_seconds": {"P25": q(spacing, 0.25), "P50": q(spacing, 0.50), "P75": q(spacing, 0.75), "P90": q(spacing, 0.90)},
    }

    boot = bootstrap(pnl_vals)

    if integrity_blocker:
        classification = "RANKER_V2_N100_PROSPECTIVE_DATA_BLOCKER"
    elif (primary.get("PF") or 0) >= 2.0 and (primary.get("expectancy") or 0) > 5.0 and (stress5.get("PF") or 0) > 1.0 and (concurrency["MAX_OPEN1"].get("PF") or 0) > 1.0 and (first50_vs_second50["signals_51_100"].get("PF") or 0) > 1.0 and score_drift["classification"] != "MATERIAL_DRIFT":
        classification = "RANKER_V2_N100_PROSPECTIVE_ALPHA_STRONG"
    elif (primary.get("PF") or 0) > 1.0 and (primary.get("expectancy") or 0) > 0 and (stress5.get("PF") or 0) > 1.0:
        classification = "RANKER_V2_N100_PROSPECTIVE_ALPHA_PROMISING"
    elif (primary.get("PF") or 0) > 1.0 or (primary.get("expectancy") or 0) > 0:
        classification = "RANKER_V2_N100_PROSPECTIVE_ALPHA_MIXED"
    else:
        classification = "RANKER_V2_N100_PROSPECTIVE_ALPHA_FAILS"

    report = {
        "classification": classification,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "canonical_n100_cohort": {
            "signals": len(rows),
            "unique_mints": len(n100_mints),
            "duplicates": len(rows) - len(n100_mints),
            "resolved": primary["resolved_N"],
            "open_unresolved": primary["unresolved_N"],
            "scorer_hash": manifest.get("canonical_spec_sha256"),
            "semantic_contract_hashes": freeze.get("contract_hashes"),
            "threshold": THRESHOLD,
            "service_PID": health.get("pid"),
            "NRestarts": health.get("n_restarts"),
            "submission_count": health.get("submission_count"),
            "integrity_notes": integrity_notes,
        },
        "primary_n100_economics": {
            "resolved_only": primary,
            "unresolved_as_zero": metrics_unresolved_zero(rows, "p75_primary"),
        },
        "entry_stress": {
            "NORMAL": primary,
            "+5%_worse_entry": stress5,
            "+10%_worse_entry": stress10,
            "normal_to_5pct_expectancy_degradation": primary["expectancy"] - stress5["expectancy"] if primary["expectancy"] is not None and stress5["expectancy"] is not None else None,
            "normal_to_10pct_expectancy_degradation": primary["expectancy"] - stress10["expectancy"] if primary["expectancy"] is not None and stress10["expectancy"] is not None else None,
        },
        "sellability_sensitivity": {
            "P50_964ms": p50,
            "P75_1476ms_PRIMARY": primary,
            "P90_1989ms": p90,
            "classification_changes": sellability_changes,
        },
        "first50_vs_second50": first50_vs_second50,
        "four_chronological_blocks": four_blocks,
        "cumulative_path": cumulative_path,
        "max_open_concurrency": concurrency,
        "outlier_dependence": outlier,
        "loss_characterization": descriptive_by_outcome(rows),
        "trigger_score_distribution": {
            **dist(trig_scores),
            "median_distance_over_threshold": q([v - THRESHOLD for v in trig_scores], 0.50),
        },
        "all_score_distribution_through_signal_100": score_drift,
        "event_gated_state_composition": state_comp,
        "signal_timing": latency,
        "real_entry_movement_25ms": movement,
        "data_integrity": data_quality,
        "opportunity_frequency": frequency,
        "expectancy_bootstrap": boot,
        "stress_robustness_by_half": {
            "signals_1_50": {
                "NORMAL": pf_exp(first50, "p75_primary"),
                "+5%": pf_exp(first50, "p75_stress5"),
                "+10%": pf_exp(first50, "p75_stress10"),
            },
            "signals_51_100": {
                "NORMAL": pf_exp(second50, "p75_primary"),
                "+5%": pf_exp(second50, "p75_stress5"),
                "+10%": pf_exp(second50, "p75_stress10"),
            },
        },
        "current_collector_status": {
            "current_unique_signals": counters.get("unique_triggered_mints", 0),
            "resolved": health.get("resolved_primary_outcomes"),
            "open": health.get("open_outcomes"),
            "score_evaluations": counters.get("score_evaluations", 0),
            "eligible_market_events": counters.get("eligible_market_events", 0),
            "service_PID": health.get("pid"),
            "NRestarts": health.get("n_restarts"),
        },
    }

    write_json(os.path.join(OUT, "n100_serious_prospective_assessment.json"), report)
    print(json.dumps(compact(report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
