#!/usr/bin/env python3
import json
import math
import os
import random
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone

OUT = "/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-prospective-shadow-v2"
FREEZE = "/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-event-gated-semantic-freeze"
THRESHOLD = 0.590596
COHORT_START_UTC = "2026-09-03T14:26:22.198000Z"


def finite(v):
    return isinstance(v, (int, float)) and math.isfinite(v)


def num(v, default=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def q(vals, pct):
    xs = sorted(v for v in vals if finite(v))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * pct
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def compact(v):
    if isinstance(v, float):
        if not math.isfinite(v):
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


def metrics(rows, lane="p75_primary", unresolved_zero=False):
    vals = []
    outcomes = Counter()
    unresolved = 0
    for r in rows:
        obj = r.get(lane) or {}
        pnl = num(obj.get("pnl"))
        outcome = obj.get("outcome")
        if pnl is None or outcome in ("ENTRY_UNRESOLVED", "120S_UNRESOLVED", "DATA_INTEGRITY_UNRESOLVED"):
            unresolved += 1
            if unresolved_zero:
                vals.append(0.0)
            continue
        vals.append(pnl)
        outcomes[outcome] += 1
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gp, gl = sum(wins), abs(sum(losses))
    return {
        "total_N": len(rows),
        "resolved_N": len(rows) - unresolved if unresolved_zero else len(vals),
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


def returns(rows, lane="p75_primary", unresolved_zero=False):
    vals = []
    for r in rows:
        obj = r.get(lane) or {}
        pnl = num(obj.get("pnl"))
        outcome = obj.get("outcome")
        if pnl is None or outcome in ("ENTRY_UNRESOLVED", "120S_UNRESOLVED", "DATA_INTEGRITY_UNRESOLVED"):
            if unresolved_zero:
                vals.append(0.0)
            continue
        else:
            vals.append(pnl)
    return vals


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
    return {"accepted_N": len(accepted), "capacity_rejected_N": rejected, **metrics(accepted)}


def dist(vals):
    xs = [v for v in vals if finite(v)]
    return {
        "N": len(xs), "min": min(xs) if xs else None, "P10": q(xs, 0.10), "P25": q(xs, 0.25),
        "P50": q(xs, 0.50), "median": q(xs, 0.50), "mean": sum(xs) / len(xs) if xs else None,
        "P75": q(xs, 0.75), "P90": q(xs, 0.90), "P95": q(xs, 0.95), "P99": q(xs, 0.99),
        "P99_5": q(xs, 0.995), "P99_9": q(xs, 0.999), "max": max(xs) if xs else None,
    }


def lat(vals):
    xs = [v for v in vals if finite(v)]
    return {"P50": q(xs, 0.50), "P75": q(xs, 0.75), "P90": q(xs, 0.90), "P95": q(xs, 0.95), "P99": q(xs, 0.99), "max": max(xs) if xs else None}


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
    wins, losses = [v for v in tmp if v > 0], [v for v in tmp if v < 0]
    gp, gl = sum(wins), abs(sum(losses))
    return {"N": len(tmp), "PF": gp / gl if gl else (999.0 if gp else None), "expectancy": sum(tmp) / len(tmp) if tmp else None, "PnL": sum(tmp) / 100.0}


def iid_bootstrap(vals, iterations=10000):
    rng = random.Random(590596)
    means = []
    n = len(vals)
    for _ in range(iterations):
        sample = [vals[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    return {"observed_expectancy": sum(vals) / n if n else None, "bootstrap_median": q(means, 0.50), "interval_80": [q(means, 0.10), q(means, 0.90)], "interval_90": [q(means, 0.05), q(means, 0.95)], "interval_95": [q(means, 0.025), q(means, 0.975)], "pct_expectancy_gt_0": sum(1 for v in means if v > 0) * 100.0 / iterations if means else None}


def block_bootstrap(vals, block_size, iterations=10000):
    rng = random.Random(590596 + block_size)
    if not vals:
        return {}
    blocks = [vals[i:i + block_size] for i in range(0, len(vals) - block_size + 1)]
    if not blocks:
        blocks = [vals]
    means = []
    while len(means) < iterations:
        sample = []
        while len(sample) < len(vals):
            sample.extend(blocks[rng.randrange(len(blocks))])
        sample = sample[:len(vals)]
        means.append(sum(sample) / len(sample))
    return {"block_size": block_size, "observed_expectancy": sum(vals) / len(vals), "bootstrap_median": q(means, 0.50), "interval_80": [q(means, 0.10), q(means, 0.90)], "interval_90": [q(means, 0.05), q(means, 0.95)], "interval_95": [q(means, 0.025), q(means, 0.975)], "pct_expectancy_gt_0": sum(1 for v in means if v > 0) * 100.0 / iterations}


def block_summary(rows, block_size):
    out = {}
    for i in range(0, len(rows), block_size):
        chunk = rows[i:i + block_size]
        out[f"{i + 1}-{i + len(chunk)}"] = pf_exp(chunk)
    return out


def cumulative_drawdown(vals):
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    path = []
    for v in vals:
        cum += v / 100.0
        peak = max(peak, cum)
        dd = peak - cum
        max_dd = max(max_dd, dd)
        path.append(cum)
    return {"max_cumulative_drawdown_SOL": max_dd, "final_cumulative_SOL": cum, "path_SOL": path}


def state_comp(rows):
    c = Counter((r.get("signal") or {}).get("state") for r in rows)
    n = len(rows)
    return {k: {"N": v, "share_pct": v * 100.0 / n if n else None} for k, v in sorted(c.items())}


def main():
    manifest = json.load(open(os.path.join(OUT, "cohort_manifest.json"), encoding="utf-8"))
    health = json.load(open(os.path.join(OUT, "service_health.json"), encoding="utf-8"))
    freeze = json.load(open(os.path.join(FREEZE, "freeze_manifest.json"), encoding="utf-8"))

    signals_all = sorted(stream_gzip_jsonl(os.path.join(OUT, "signals.jsonl.gz")), key=lambda r: (r.get("threshold_decision_ts_ms") or 0, r.get("mint") or ""))
    if len({s.get("mint") for s in signals_all}) < 200:
        print(f"RANKER_V2_N200_NOT_REACHED {len({s.get('mint') for s in signals_all})}")
        return
    signals = signals_all[:200]
    mints = {s.get("mint") for s in signals}

    outcomes_by_mint = {r.get("mint"): r for r in stream_gzip_jsonl(os.path.join(OUT, "outcomes.jsonl.gz")) if r.get("mint")}
    entry_by_mint = {}
    for r in stream_gzip_jsonl(os.path.join(OUT, "entry_quality.jsonl.gz")):
        mint = r.get("mint")
        if mint in mints:
            old = entry_by_mint.get(mint)
            if old is None or old.get("source_continuity_status") == "PENDING":
                entry_by_mint[mint] = r

    rows = []
    for s in signals:
        r = dict(outcomes_by_mint.get(s.get("mint")) or {"mint": s.get("mint"), "signal": s})
        r["signal"] = r.get("signal") or s
        r["entry_quality"] = entry_by_mint.get(s.get("mint")) or {}
        rows.append(r)

    signal200_ts = signals[-1]["threshold_decision_ts_ms"]
    score_rows = [r for r in stream_gzip_jsonl(os.path.join(OUT, "all_scores.jsonl.gz")) if (r.get("threshold_decision_ts_ms") or 0) <= signal200_ts]
    score_vals = [num(r.get("score")) for r in score_rows if num(r.get("score")) is not None]
    score_mints = {r.get("mint") for r in score_rows if r.get("mint")}

    primary = metrics(rows)
    stress5 = metrics(rows, "p75_stress5")
    stress10 = metrics(rows, "p75_stress10")
    vals = returns(rows)

    first100, second100 = rows[:100], rows[100:200]
    first_vs_second = {
        "signals_1_100": {**metrics(first100), "+5": metrics(first100, "p75_stress5"), "+10": metrics(first100, "p75_stress10"), "MAX_OPEN1": capacity(first100, 1)},
        "signals_101_200": {**metrics(second100), "+5": metrics(second100, "p75_stress5"), "+10": metrics(second100, "p75_stress10"), "MAX_OPEN1": capacity(second100, 1)},
    }

    four50 = {}
    for i in range(0, 200, 50):
        chunk = rows[i:i + 50]
        four50[f"{i + 1}-{i + 50}"] = {**pf_exp(chunk), "+5_PF": metrics(chunk, "p75_stress5")["PF"], "+5_expectancy": metrics(chunk, "p75_stress5")["expectancy"], "+10_PF": metrics(chunk, "p75_stress10")["PF"], "MAX_OPEN1_PF": capacity(chunk, 1)["PF"]}

    eight25 = block_summary(rows, 25)
    cumulative = {f"N{n}": metrics(rows[:n]) for n in (25, 50, 75, 100, 125, 150, 175, 200)}
    drawdown = cumulative_drawdown(vals)

    concurrency = {f"MAX_OPEN{i}": capacity(rows, i) for i in (1, 2, 3, 5)}
    concurrency["RAW"] = {"accepted_N": len(rows), "capacity_rejected_N": 0, **primary}
    concurrency["MAX_OPEN1_1_100"] = capacity(first100, 1)
    concurrency["MAX_OPEN1_101_200"] = capacity(second100, 1)
    raw_pnl = primary["normalized_PnL_at_1SOL"] or 0.0
    mo1_pnl = concurrency["MAX_OPEN1"]["normalized_PnL_at_1SOL"] or 0.0
    concurrency["MAX_OPEN1_raw_alpha_rejected_pct"] = (raw_pnl - mo1_pnl) * 100.0 / raw_pnl if raw_pnl else None

    wins = sorted([v for v in vals if v > 0], reverse=True)
    total_pnl = sum(vals)
    outlier = {
        "largest_winner": max(wins) if wins else None,
        "largest_loser": min([v for v in vals if v < 0], default=None),
        "top1_winner_contribution_pct": sum(wins[:1]) * 100.0 / total_pnl if total_pnl else None,
        "top3_winner_contribution_pct": sum(wins[:3]) * 100.0 / total_pnl if total_pnl else None,
        "top5_winner_contribution_pct": sum(wins[:5]) * 100.0 / total_pnl if total_pnl else None,
        "top10_winner_contribution_pct": sum(wins[:10]) * 100.0 / total_pnl if total_pnl else None,
        "top20_winner_contribution_pct": sum(wins[:20]) * 100.0 / total_pnl if total_pnl else None,
        "remove_top1": remove_top_winners(vals, 1),
        "remove_top5": remove_top_winners(vals, 5),
        "remove_top10": remove_top_winners(vals, 10),
        "remove_top20": remove_top_winners(vals, 20),
    }

    by_hour = defaultdict(list)
    for r in rows:
        ts = (r.get("signal") or {}).get("threshold_decision_ts_ms")
        if ts:
            hour = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:00Z")
            by_hour[hour].append(r)
    clustering = {
        "contiguous_10_signal_blocks": block_summary(rows, 10),
        "contiguous_20_signal_blocks": block_summary(rows, 20),
        "utc_hour_blocks": {h: pf_exp(rs) for h, rs in sorted(by_hour.items())},
    }

    sellability = {
        "P50_964ms": metrics(rows, "p50"),
        "P75_1476ms_PRIMARY": primary,
        "P90_1989ms": metrics(rows, "p90"),
        "p50_vs_p90_classification_changes": sum(1 for r in rows if (r.get("p50") or {}).get("outcome") != (r.get("p90") or {}).get("outcome")),
    }

    trig_scores = [num((r.get("signal") or {}).get("score")) for r in rows if num((r.get("signal") or {}).get("score")) is not None]
    score_drift = {
        **dist(score_vals),
        "pct_gte_threshold": sum(1 for v in score_vals if v >= THRESHOLD) * 100.0 / len(score_vals) if score_vals else None,
        "historical_reference": {"opportunities_per_hour": 16.254168},
        "N50_pct_gte_threshold": 1.08398232,
        "N100_pct_gte_threshold": 1.455835576,
    }
    pct = score_drift.get("pct_gte_threshold")
    score_drift["classification"] = "MATERIAL_DRIFT" if pct is not None and pct > 3.0 else ("POSSIBLE_DRIFT" if pct is not None and pct > 2.0 else "NO_OBVIOUS_DRIFT")

    state_composition = {"N1_100": state_comp(first100), "N101_200": state_comp(second100), "N200": state_comp(rows)}
    states_changed = state_composition["N1_100"] != state_composition["N101_200"]

    def sigvals(rs, field):
        return [num((r.get("signal") or {}).get(field)) for r in rs if num((r.get("signal") or {}).get(field)) is not None]
    def final_input_lat(rs):
        out = []
        for r in rs:
            s = r.get("signal") or {}
            a, b = num(s.get("threshold_decision_ts_ms")), num(s.get("final_required_input_received_ts_ms"))
            if a is not None and b is not None:
                out.append(a - b)
        return out
    latency = {
        "event_state_to_threshold_decision_ms": lat(sigvals(rows, "event_state_to_threshold_decision_ms")),
        "final_required_input_to_threshold_decision_ms": lat(final_input_lat(rows)),
        "event_state_to_threshold_decision_1_100": lat(sigvals(first100, "event_state_to_threshold_decision_ms")),
        "event_state_to_threshold_decision_101_200": lat(sigvals(second100, "event_state_to_threshold_decision_ms")),
        "final_required_input_to_threshold_decision_1_100": lat(final_input_lat(first100)),
        "final_required_input_to_threshold_decision_101_200": lat(final_input_lat(second100)),
    }

    def movement(rs):
        vals2 = []
        for r in rs:
            v = num(((r.get("entry_quality") or {}).get("descriptive_movement_after_decision_ms") or {}).get("25", {}).get("pct_from_state"))
            if v is not None:
                vals2.append(v)
        return {
            "P10": q(vals2, 0.10), "P25": q(vals2, 0.25), "P50": q(vals2, 0.50), "P75": q(vals2, 0.75), "P90": q(vals2, 0.90), "P95": q(vals2, 0.95), "P99": q(vals2, 0.99),
            "max_favorable": min(vals2) if vals2 else None, "max_adverse": max(vals2) if vals2 else None,
            "pct_adverse_gt_1": sum(1 for v in vals2 if v > 1) * 100.0 / len(vals2) if vals2 else None,
            "pct_adverse_gt_2": sum(1 for v in vals2 if v > 2) * 100.0 / len(vals2) if vals2 else None,
            "pct_adverse_gt_5": sum(1 for v in vals2 if v > 5) * 100.0 / len(vals2) if vals2 else None,
            "pct_adverse_gt_10": sum(1 for v in vals2 if v > 10) * 100.0 / len(vals2) if vals2 else None,
        }
    entry_movement = {"N200": movement(rows), "N1_100": movement(first100), "N101_200": movement(second100)}

    counters = health.get("counters") or {}
    rejects = health.get("rejects") or {}
    malformed = health.get("completed_malformed_records") or 0
    integrity_blocker = False
    notes = []
    if len(rows) != 200:
        integrity_blocker = True; notes.append("not_exactly_200_signals")
    if len(mints) != 200:
        integrity_blocker = True; notes.append("duplicate_mints_in_n200")
    if any((num((r.get("signal") or {}).get("score")) or -999) < THRESHOLD for r in rows):
        integrity_blocker = True; notes.append("score_below_threshold")
    if manifest.get("cohort_start_utc") != COHORT_START_UTC:
        integrity_blocker = True; notes.append("cohort_start_mismatch")
    if any(((r.get("signal") or {}).get("threshold_decision_ts_ms") or 0) <= (manifest.get("cohort_start_ms") or 0) for r in rows):
        integrity_blocker = True; notes.append("signal_not_after_cohort_start")

    data_integrity = {
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
        "duplicate_primary_signal_attempts": counters.get("later_threshold_crossing_after_mint_signal", 0),
        "service_restarts": health.get("n_restarts"),
        "submission_count": health.get("submission_count"),
        "could_falsely_admit": integrity_blocker,
        "could_suppress_signal": bool(counters.get("score_failures", 0) or rejects.get("feature_failures", 0)),
        "could_alter_outcome": integrity_blocker,
        "notes": notes,
    }

    start_ms = manifest.get("cohort_start_ms")
    elapsed_h = (signal200_ts - start_ms) / 3_600_000 if start_ms else None
    sig100_ts = signals[99]["threshold_decision_ts_ms"]
    spacing = [(signals[i]["threshold_decision_ts_ms"] - signals[i - 1]["threshold_decision_ts_ms"]) / 1000.0 for i in range(1, len(signals))]
    launches_vals = [num(((r.get("signal") or {}).get("raw_features") or {}).get("launches_per_minute")) for r in rows if num(((r.get("signal") or {}).get("raw_features") or {}).get("launches_per_minute")) is not None]
    signals_by_hour = Counter(datetime.fromtimestamp(s["threshold_decision_ts_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:00Z") for s in signals)
    frequency = {
        "elapsed_hours_through_signal_200": elapsed_h,
        "N200_signals_per_hour": 200 / elapsed_h if elapsed_h else None,
        "historical_reference_signals_per_hour": 16.254168,
        "frequency_multiple_vs_historical": (200 / elapsed_h) / 16.254168 if elapsed_h else None,
        "first100_signals_per_hour": 100 / ((sig100_ts - start_ms) / 3_600_000) if start_ms and sig100_ts > start_ms else None,
        "second100_signals_per_hour": 100 / ((signal200_ts - sig100_ts) / 3_600_000) if signal200_ts > sig100_ts else None,
        "signals_by_utc_hour": dict(sorted(signals_by_hour.items())),
        "score_evaluations_per_hour_proxy": len(score_rows) / elapsed_h if elapsed_h else None,
        "unique_scored_mints_per_hour_proxy": len(score_mints) / elapsed_h if elapsed_h else None,
        "launches_per_minute_distribution_at_triggers": dist(launches_vals),
        "signal_spacing_seconds": {"P25": q(spacing, 0.25), "P50": q(spacing, 0.50), "P75": q(spacing, 0.75), "P90": q(spacing, 0.90)},
    }
    frequency["is_materially_higher_than_historical"] = (frequency["frequency_multiple_vs_historical"] or 0) > 1.5
    frequency["classification"] = "MATERIAL_FREQUENCY_REGIME_SHIFT" if (frequency["frequency_multiple_vs_historical"] or 0) > 3.0 else ("FREQUENCY_ELEVATED_BUT_EXPLAINABLE" if (frequency["frequency_multiple_vs_historical"] or 0) > 1.5 else "FREQUENCY_WITHIN_HISTORICAL_RANGE")

    historical_vs_prospective = {
        "historical_corrected_validation": {"PF": 3.43, "expectancy": 7.63, "+5_PF": 1.05},
        "historical_corrected_final": {"PF": 5.06, "expectancy": 11.01, "+5_PF": 1.86},
        "prospective_N200": {"PF": primary["PF"], "expectancy": primary["expectancy"], "+5_PF": stress5["PF"]},
        "note": "Prospective frequency/regime differs; higher PF is descriptive and not proof that historical estimate was conservative.",
    }

    live_ready = (
        not integrity_blocker
        and primary["unresolved_N"] == 0
        and (primary["PF"] or 0) > 2
        and (primary["expectancy"] or 0) > 5
        and (stress5["PF"] or 0) > 1
        and (stress10["PF"] or 0) > 1
        and (first_vs_second["signals_101_200"]["PF"] or 0) > 1
        and score_drift["classification"] != "MATERIAL_DRIFT"
    )
    final_class = "RANKER_V2_N200_PROSPECTIVE_DATA_BLOCKER" if integrity_blocker else ("RANKER_V2_N200_PROSPECTIVE_ALPHA_CONFIRMED_STRONG" if live_ready else ("RANKER_V2_N200_PROSPECTIVE_ALPHA_CONFIRMED_PROMISING" if (primary["PF"] or 0) > 1 and (stress5["PF"] or 0) > 1 else ("RANKER_V2_N200_PROSPECTIVE_ALPHA_MIXED" if (primary["PF"] or 0) > 1 else "RANKER_V2_N200_PROSPECTIVE_ALPHA_FAILS")))

    report = {
        "classification": final_class,
        "live_translation_readiness": "LIVE_TRANSLATION_RESEARCH_READY" if live_ready else "LIVE_TRANSLATION_RESEARCH_NOT_READY",
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cohort_integrity": {
            "signals": len(rows), "unique_mints": len(mints), "duplicate_primary_mints": len(rows) - len(mints),
            "first_threshold_crossing_per_mint_only": True,
            "all_signals_after_cohort_start": all((r["signal"].get("threshold_decision_ts_ms") or 0) > (manifest.get("cohort_start_ms") or 0) for r in rows),
            "threshold": manifest.get("threshold"), "expected_threshold": THRESHOLD,
            "canonical_scorer_hash": manifest.get("canonical_spec_sha256"),
            "semantic_contract_hashes": freeze.get("contract_hashes"),
            "resolved": primary["resolved_N"], "unresolved_open": primary["unresolved_N"],
            "PID": health.get("pid"), "NRestarts": health.get("n_restarts"), "submissions": health.get("submission_count"),
            "signal_affecting_integrity_issue": integrity_blocker, "notes": notes,
        },
        "primary_n200_economics": {"resolved_only": primary, "unresolved_as_zero": metrics(rows, unresolved_zero=True)},
        "entry_stress": {"NORMAL": primary, "+5_worse_entry": stress5, "+10_worse_entry": stress10, "normal_to_5_expectancy_degradation": primary["expectancy"] - stress5["expectancy"] if primary["expectancy"] is not None and stress5["expectancy"] is not None else None, "normal_to_10_expectancy_degradation": primary["expectancy"] - stress10["expectancy"] if primary["expectancy"] is not None and stress10["expectancy"] is not None else None},
        "n1_100_vs_n101_200": first_vs_second,
        "four_50_signal_blocks": four50,
        "eight_25_signal_blocks": eight25,
        "cumulative_path": cumulative,
        "cumulative_drawdown": drawdown,
        "concurrency_capacity": concurrency,
        "sellability_robustness": sellability,
        "outlier_robustness": outlier,
        "signal_dependence_clustering": clustering,
        "bootstrap": {"IID": iid_bootstrap(vals), "block_5": block_bootstrap(vals, 5), "block_10": block_bootstrap(vals, 10), "block_20": block_bootstrap(vals, 20)},
        "signal_frequency_regime": frequency,
        "score_drift": score_drift,
        "event_gated_state_composition": {**state_composition, "composition_changed_between_halves": states_changed},
        "actual_decision_latency": latency,
        "entry_movement_25ms": entry_movement,
        "data_integrity": data_integrity,
        "historical_vs_prospective": historical_vs_prospective,
        "current_collector": {
            "current_unique_signals": counters.get("unique_triggered_mints", 0),
            "resolved": health.get("resolved_primary_outcomes"),
            "open": health.get("open_outcomes"),
            "score_evaluations": counters.get("score_evaluations", 0),
            "eligible_events": counters.get("eligible_market_events", 0),
            "service_PID": health.get("pid"),
            "NRestarts": health.get("n_restarts"),
        },
    }
    write_json(os.path.join(OUT, "n200_preferred_confirmation.json"), report)
    print(json.dumps(compact(report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
