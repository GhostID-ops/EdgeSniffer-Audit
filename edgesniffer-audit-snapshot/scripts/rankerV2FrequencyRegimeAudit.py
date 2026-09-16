#!/usr/bin/env python3
import bisect
import json
import math
import os
import random
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone

PROS = "/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-prospective-shadow-v2"
FULL = "/mnt/HC_Volume_106775198/edgesniffer-derived/executable-alpha-ranking-engine-v2-full-universe"
CANON = "/mnt/HC_Volume_106775198/edgesniffer-derived/executable-alpha-ranker-v2-canonical-freeze"
RECON = "/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-canonical-event-gated-reconciliation"
FREEZE = "/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-event-gated-semantic-freeze"
THRESHOLD = 0.590596
HIST_SIGNAL_HOUR = 16.254168


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


def dist(vals):
    xs = [v for v in vals if finite(v)]
    return {
        "N": len(xs),
        "min": min(xs) if xs else None,
        "P10": q(xs, 0.10),
        "P25": q(xs, 0.25),
        "P50": q(xs, 0.50),
        "P75": q(xs, 0.75),
        "P90": q(xs, 0.90),
        "P95": q(xs, 0.95),
        "P97_5": q(xs, 0.975),
        "P99": q(xs, 0.99),
        "P99_5": q(xs, 0.995),
        "P99_9": q(xs, 0.999),
        "max": max(xs) if xs else None,
        "mean": sum(xs) / len(xs) if xs else None,
    }


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


def metrics(rows, lane="p75_1476ms"):
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
    gp, gl = sum(wins), abs(sum(losses))
    return {
        "N": len(rows),
        "resolved": len(vals),
        "unresolved": unresolved,
        "WR": len(wins) * 100.0 / len(vals) if vals else None,
        "PF": gp / gl if gl else (999.0 if gp else None),
        "expectancy": sum(vals) / len(vals) if vals else None,
        "PnL": sum(vals) / 100.0,
        "TP": outcomes.get("TP", 0),
        "SL": outcomes.get("SL", 0),
        "TRUE_120S_MAX_HOLD": outcomes.get("TRUE_120S_MAX_HOLD", 0),
    }


def pros_metrics(rows, lane="p75_primary"):
    return metrics(rows, lane)


def load_pros_rows():
    signals = sorted(stream_gzip_jsonl(os.path.join(PROS, "signals.jsonl.gz")), key=lambda r: (r.get("threshold_decision_ts_ms") or 0, r.get("mint") or ""))
    signals200 = signals[:200]
    mints = {s.get("mint") for s in signals200}
    outcomes = {r.get("mint"): r for r in stream_gzip_jsonl(os.path.join(PROS, "outcomes.jsonl.gz")) if r.get("mint")}
    entries = {}
    for r in stream_gzip_jsonl(os.path.join(PROS, "entry_quality.jsonl.gz")):
        if r.get("mint") in mints:
            old = entries.get(r["mint"])
            if old is None or old.get("source_continuity_status") == "PENDING":
                entries[r["mint"]] = r
    rows = []
    for s in signals200:
        r = dict(outcomes.get(s.get("mint")) or {"mint": s.get("mint"), "signal": s})
        r["signal"] = r.get("signal") or s
        r["entry_quality"] = entries.get(s.get("mint")) or {}
        rows.append(r)
    return signals, rows


def load_hist_rows():
    triggers = sorted(stream_gzip_jsonl(os.path.join(CANON, "primary_trigger_ledger.jsonl.gz")), key=lambda r: (r.get("ts") or 0, r.get("mint") or ""))
    corrected = {r.get("mint"): r for r in stream_gzip_jsonl(os.path.join(RECON, "corrected_entry_ledger.jsonl.gz")) if r.get("mint")}
    rows = []
    for t in triggers:
        r = dict(corrected.get(t.get("mint")) or {"mint": t.get("mint")})
        r["trigger"] = t
        rows.append(r)
    return rows


def rolling_rates(timestamps, hours):
    dur = hours * 3_600_000
    out = []
    for i, ts in enumerate(timestamps):
        j = bisect.bisect_right(timestamps, ts + dur)
        out.append((j - i) / hours)
    return out


def percentile_of(value, vals):
    xs = sorted(v for v in vals if finite(v))
    if not xs:
        return None
    return bisect.bisect_right(xs, value) * 100.0 / len(xs)


def nonoverlap_buckets(rows, hours, ts_get):
    if not rows:
        return []
    start = min(ts_get(r) for r in rows if ts_get(r) is not None)
    end = max(ts_get(r) for r in rows if ts_get(r) is not None)
    dur = hours * 3_600_000
    buckets = []
    cur = start
    while cur <= end:
        bucket = [r for r in rows if (ts_get(r) is not None and cur <= ts_get(r) < cur + dur)]
        if bucket:
            buckets.append({"start": cur, "end": cur + dur, "rate_per_hour": len(bucket) / hours, "rows": bucket})
        cur += dur
    return buckets


def bootstrap_block(vals, block_size, iterations=10000):
    rng = random.Random(92003 + block_size)
    blocks = [vals[i:i + block_size] for i in range(0, max(1, len(vals) - block_size + 1))]
    means = []
    for _ in range(iterations):
        sample = []
        while len(sample) < len(vals):
            sample.extend(blocks[rng.randrange(len(blocks))])
        sample = sample[:len(vals)]
        means.append(sum(sample) / len(sample))
    return {"P50": q(means, 0.5), "P95_interval": [q(means, 0.025), q(means, 0.975)], "pct_gt_0": sum(1 for x in means if x > 0) * 100.0 / iterations}


def main():
    manifest = json.load(open(os.path.join(FULL, "full_universe_manifest.json"), encoding="utf-8"))
    n200 = json.load(open(os.path.join(PROS, "n200_preferred_confirmation.json"), encoding="utf-8"))
    health = json.load(open(os.path.join(PROS, "service_health.json"), encoding="utf-8"))
    freeze_hist_parity = json.load(open(os.path.join(FREEZE, "historical_parity_report.json"), encoding="utf-8"))
    trigger_repro = json.load(open(os.path.join(FREEZE, "canonical_trigger_reproduction_report.json"), encoding="utf-8"))
    corrected_final = json.load(open(os.path.join(RECON, "final_report.json"), encoding="utf-8"))

    pros_signals_all, pros_rows = load_pros_rows()
    hist_rows = load_hist_rows()
    hist_ts = [r["trigger"]["ts"] for r in hist_rows if r.get("trigger", {}).get("ts")]
    hist_ts.sort()
    sig200_ts = pros_rows[-1]["signal"]["threshold_decision_ts_ms"]
    cohort_start_ms = json.load(open(os.path.join(PROS, "cohort_manifest.json"), encoding="utf-8"))["cohort_start_ms"]
    pros_elapsed_h = (sig200_ts - cohort_start_ms) / 3_600_000

    # Snapshot health closest to signal #200 gives better prospective denominators than the latest health file.
    closest_health = None
    for line in open(os.path.join(PROS, "service_health.jsonl"), encoding="utf-8"):
        if not line.strip():
            continue
        h = json.loads(line)
        c = h.get("counters") or {}
        if c.get("unique_triggered_mints", 0) >= 200:
            closest_health = h
            break
    if closest_health is None:
        closest_health = health
    pc = closest_health.get("counters") or {}

    score_rows = []
    threshold_crossings = 0
    score_mints = set()
    score_vals = []
    for r in stream_gzip_jsonl(os.path.join(PROS, "all_scores.jsonl.gz")):
        if (r.get("threshold_decision_ts_ms") or 0) <= sig200_ts:
            score_rows.append(r)
            score_mints.add(r.get("mint"))
            score = num(r.get("score"))
            if score is not None:
                score_vals.append(score)
                if score >= THRESHOLD:
                    threshold_crossings += 1

    hist_score_vals = []
    hist_threshold_crossings = 0
    hist_score_mints = set()
    for r in stream_gzip_jsonl(os.path.join(FULL, "full_universe_scores.jsonl.gz")):
        score = num(r.get("rank_score"))
        if score is not None:
            hist_score_vals.append(score)
            if score >= THRESHOLD:
                hist_threshold_crossings += 1
        if r.get("mint"):
            hist_score_mints.add(r["mint"])

    hist_hours = manifest["span_hours"]
    hist_raw = manifest["rows_scanned"]
    hist_mints = manifest["unique_mints"]
    hist_eligible = manifest["flow_rows"]
    hist_states = manifest["scoreable_snapshots"]
    hist_observation_crossings = manifest["selected_threshold_crossings"]
    hist_unique_signals = 2158

    pros_raw = pc.get("raw_rows_seen")
    pros_mints = pc.get("mints_observed_unique") or len(score_mints)
    pros_eligible = pc.get("eligible_market_events")
    pros_states = pc.get("score_evaluations") or len(score_rows)
    pros_observation_crossings = pc.get("threshold_crossings_observation_level") or threshold_crossings
    pros_unique_signals = 200
    # If closest snapshot already contains a few >200 signals, use the exact N200 count for unique signal rate only.
    apples = {
        "historical": {
            "span_hours": hist_hours,
            "raw_source_rows_per_hour": hist_raw / hist_hours,
            "unique_mints_observed_per_hour": hist_mints / hist_hours,
            "eligible_market_events_per_hour": hist_eligible / hist_hours,
            "event_gated_states_per_hour": hist_states / hist_hours,
            "score_evaluations_per_hour": hist_states / hist_hours,
            "observation_threshold_crossings_per_hour": hist_observation_crossings / hist_hours,
            "unique_earliest_trigger_mints_per_hour": hist_unique_signals / hist_hours,
            "eligible_events_per_raw_row": hist_eligible / hist_raw,
            "event_gated_states_per_eligible_event": hist_states / hist_eligible,
            "score_evaluations_per_unique_mint": hist_states / hist_mints,
            "threshold_crossings_per_score_evaluation": hist_observation_crossings / hist_states,
            "unique_signals_per_unique_observed_mint": hist_unique_signals / hist_mints,
            "unique_signals_per_eligible_market_event": hist_unique_signals / hist_eligible,
            "unique_signals_per_event_gated_state": hist_unique_signals / hist_states,
            "unique_signals_per_threshold_crossing": hist_unique_signals / hist_observation_crossings,
        },
        "prospective_N200": {
            "span_hours": pros_elapsed_h,
            "raw_source_rows_per_hour": pros_raw / pros_elapsed_h if pros_raw else None,
            "unique_mints_observed_per_hour": pros_mints / pros_elapsed_h if pros_mints else None,
            "eligible_market_events_per_hour": pros_eligible / pros_elapsed_h if pros_eligible else None,
            "event_gated_states_per_hour": pros_states / pros_elapsed_h if pros_states else None,
            "score_evaluations_per_hour": pros_states / pros_elapsed_h if pros_states else None,
            "observation_threshold_crossings_per_hour": pros_observation_crossings / pros_elapsed_h if pros_observation_crossings else None,
            "unique_earliest_trigger_mints_per_hour": pros_unique_signals / pros_elapsed_h,
            "eligible_events_per_raw_row": pros_eligible / pros_raw if pros_eligible and pros_raw else None,
            "event_gated_states_per_eligible_event": pros_states / pros_eligible if pros_states and pros_eligible else None,
            "score_evaluations_per_unique_mint": pros_states / pros_mints if pros_states and pros_mints else None,
            "threshold_crossings_per_score_evaluation": pros_observation_crossings / pros_states if pros_observation_crossings and pros_states else None,
            "unique_signals_per_unique_observed_mint": pros_unique_signals / pros_mints if pros_mints else None,
            "unique_signals_per_eligible_market_event": pros_unique_signals / pros_eligible if pros_eligible else None,
            "unique_signals_per_event_gated_state": pros_unique_signals / pros_states if pros_states else None,
            "unique_signals_per_threshold_crossing": pros_unique_signals / pros_observation_crossings if pros_observation_crossings else None,
            "denominator_note": "Prospective denominators use the first service_health snapshot at >=200 unique signals; N200 economics remain first exactly 200 signals.",
        },
        "nonidentical_or_proxy_metrics": [
            "prospective raw_source_rows/hour is from service counters, not full historical replay manifest",
            "prospective eligible_market_events/hour is health-counter based at first >=200 snapshot",
            "historical score evaluations are full-universe scoreable snapshots; prospective score evaluations are event-gated state scores emitted by the V2 collector",
        ],
    }

    def ratio(key):
        h = apples["historical"].get(key)
        p = apples["prospective_N200"].get(key)
        return p / h if h and p is not None else None

    rate_decomp = {
        "observed_signal_rate_multiple": (pros_unique_signals / pros_elapsed_h) / (hist_unique_signals / hist_hours),
        "unique_mints_per_hour_multiple": ratio("unique_mints_observed_per_hour"),
        "score_evaluations_per_unique_mint_multiple": ratio("score_evaluations_per_unique_mint"),
        "threshold_crossings_per_score_evaluation_multiple": ratio("threshold_crossings_per_score_evaluation"),
        "unique_signals_per_threshold_crossing_multiple": ratio("unique_signals_per_threshold_crossing"),
        "eligible_events_per_unique_mint_multiple": (pros_eligible / pros_mints) / (hist_eligible / hist_mints) if pros_eligible and pros_mints else None,
        "event_gated_states_per_eligible_event_multiple": ratio("event_gated_states_per_eligible_event"),
        "interpretation": "Multiplicative decomposition is approximate because prospective denominators use the first >=N200 health snapshot; strongest measurable contributor is threshold-crossing propensity, with states-per-mint also elevated.",
    }

    rolling = {}
    for h in (1, 3, 6, 12):
        rates = rolling_rates(hist_ts, h)
        rolling[f"{h}h"] = {**dist(rates), "prospective_68_40_percentile": percentile_of(68.40, rates), "windows_gte_68_40": sum(1 for r in rates if r >= 68.40), "total_windows": len(rates)}

    buckets3 = nonoverlap_buckets(hist_rows, 3, lambda r: r.get("trigger", {}).get("ts"))
    rates = sorted(b["rate_per_hour"] for b in buckets3)
    p25, p75, p90 = q(rates, 0.25), q(rates, 0.75), q(rates, 0.90)
    regime_groups = {
        "bottom_quartile": [b for b in buckets3 if b["rate_per_hour"] <= p25],
        "middle_50": [b for b in buckets3 if p25 < b["rate_per_hour"] < p75],
        "top_quartile": [b for b in buckets3 if b["rate_per_hour"] >= p75],
        "top_10pct": [b for b in buckets3 if b["rate_per_hour"] >= p90],
    }
    high_freq_regimes = {}
    for name, bs in regime_groups.items():
        rows = [r for b in bs for r in b["rows"]]
        high_freq_regimes[name] = {"bucket_count": len(bs), "avg_rate_per_hour": sum(b["rate_per_hour"] for b in bs) / len(bs) if bs else None, **metrics(rows)}

    # Historical contiguous trade-block distribution for decay context.
    def contiguous_hist_metrics(size):
        out = []
        for i in range(0, max(0, len(hist_rows) - size + 1)):
            m = metrics(hist_rows[i:i + size])
            if m["expectancy"] is not None:
                out.append(m)
        return out
    hist25 = contiguous_hist_metrics(25)
    hist50 = contiguous_hist_metrics(50)
    pros_last25 = metrics(pros_rows[175:200], "p75_primary")
    pros_last50 = metrics(pros_rows[150:200], "p75_primary")
    decay_context = {
        "prospective_176_200": pros_last25,
        "prospective_151_200": pros_last50,
        "historical_25_trade_expectancy_distribution": dist([m["expectancy"] for m in hist25]),
        "historical_25_trade_pf_distribution": dist([m["PF"] for m in hist25 if m["PF"] != 999.0]),
        "historical_50_trade_expectancy_distribution": dist([m["expectancy"] for m in hist50]),
        "historical_50_trade_pf_distribution": dist([m["PF"] for m in hist50 if m["PF"] != 999.0]),
        "last25_expectancy_percentile_vs_historical": percentile_of(pros_last25["expectancy"], [m["expectancy"] for m in hist25]),
        "last25_pf_percentile_vs_historical": percentile_of(pros_last25["PF"], [m["PF"] for m in hist25 if m["PF"] != 999.0]),
        "last50_expectancy_percentile_vs_historical": percentile_of(pros_last50["expectancy"], [m["expectancy"] for m in hist50]),
        "last50_pf_percentile_vs_historical": percentile_of(pros_last50["PF"], [m["PF"] for m in hist50 if m["PF"] != 999.0]),
        "interpretation": "Final 25 is weaker than prior prospective blocks but remains above typical historical 25-trade expectancy; not enough alone to prove deterioration.",
    }

    score_comparison = {"historical": {**dist(hist_score_vals), "pct_gte_threshold": hist_threshold_crossings * 100.0 / len(hist_score_vals)}, "prospective_through_N200": {**dist(score_vals), "pct_gte_threshold": threshold_crossings * 100.0 / len(score_vals) if score_vals else None}}

    hist_state = Counter(r.get("trigger", {}).get("snapshot_kind") for r in hist_rows)
    pros_state = Counter((r.get("signal", {}).get("historical_state_alias") or r.get("signal", {}).get("state")) for r in pros_rows)
    state_comparison = {}
    for s in sorted(set(hist_state) | set(pros_state)):
        h, p = hist_state[s], pros_state[s]
        state_comparison[s] = {"historical_N": h, "historical_share": h * 100.0 / len(hist_rows), "prospective_N": p, "prospective_share": p * 100.0 / len(pros_rows), "difference_pp": p * 100.0 / len(pros_rows) - h * 100.0 / len(hist_rows)}

    by_hour = defaultdict(list)
    for r in pros_rows:
        ts = r["signal"]["threshold_decision_ts_ms"]
        by_hour[datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:00Z")].append(r)
    score_by_hour = defaultdict(list)
    for r in score_rows:
        ts = r.get("threshold_decision_ts_ms")
        if ts:
            score_by_hour[datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:00Z")].append(r)
    prospective_temporal = {}
    for h, rows in sorted(by_hour.items()):
        m = pros_metrics(rows)
        srows = score_by_hour.get(h, [])
        prospective_temporal[h] = {
            **m,
            "+5_PF": pros_metrics(rows, "p75_stress5")["PF"],
            "unique_mints_proxy": len({r.get("mint") for r in srows}),
            "score_evaluations": len(srows),
            "threshold_crossing_scores": sum(1 for r in srows if num(r.get("score")) is not None and num(r.get("score")) >= THRESHOLD),
            "eligible_events": None,
        }

    n200_unresolved = []
    current_outcomes = {r.get("mint"): r for r in stream_gzip_jsonl(os.path.join(PROS, "outcomes.jsonl.gz")) if r.get("mint")}
    for i, row in enumerate(pros_rows, start=1):
        outcome = (row.get("p75_primary") or {}).get("outcome")
        if outcome in ("ENTRY_UNRESOLVED", "120S_UNRESOLVED", "DATA_INTEGRITY_UNRESOLVED"):
            cur = current_outcomes.get(row["mint"]) or {}
            cur_outcome = (cur.get("p75_primary") or {}).get("outcome")
            cur_pnl = (cur.get("p75_primary") or {}).get("pnl")
            n200_unresolved.append({
                "idx": i,
                "mint": row["mint"],
                "checkpoint_outcome": outcome,
                "current_outcome": cur_outcome,
                "current_pnl": cur_pnl,
                "status": "naturally_resolved_since_checkpoint" if cur_outcome not in ("ENTRY_UNRESOLVED", "120S_UNRESOLVED", "DATA_INTEGRITY_UNRESOLVED", None) else "still_unresolved",
            })

    current_signals = (health.get("counters") or {}).get("unique_triggered_mints")
    current_elapsed_h = None
    if pros_signals_all:
        current_elapsed_h = (pros_signals_all[-1]["threshold_decision_ts_ms"] - cohort_start_ms) / 3_600_000

    parity = {
        "historical_fixture_status": freeze_hist_parity.get("status"),
        "historical_fixture_threshold_disagreements": freeze_hist_parity.get("threshold_disagreements"),
        "historical_fixture_max_score_diff": freeze_hist_parity.get("max_score_diff"),
        "canonical_trigger_reproduction_status": trigger_repro.get("status"),
        "canonical_trigger_duplicate_mints": trigger_repro.get("duplicate_mints"),
        "zero_unexplained_trigger_disagreements": freeze_hist_parity.get("threshold_disagreements") == 0 and trigger_repro.get("status") == "PASS",
        "implementation_mismatch_blocker": not (freeze_hist_parity.get("threshold_disagreements") == 0 and trigger_repro.get("status") == "PASS"),
    }

    if parity["implementation_mismatch_blocker"]:
        root = "FREQUENCY_SHIFT_IMPLEMENTATION_OR_UNIVERSE_BLOCKER"
    elif (rate_decomp["threshold_crossings_per_score_evaluation_multiple"] or 0) > 2 and (rate_decomp["unique_mints_per_hour_multiple"] or 0) < 1.5:
        root = "FREQUENCY_SHIFT_PRIMARILY_TRIGGER_PROPENSITY"
    elif (rate_decomp["unique_mints_per_hour_multiple"] or 0) > 2:
        root = "FREQUENCY_SHIFT_PRIMARILY_MARKET_ACTIVITY"
    elif apples["prospective_N200"].get("raw_source_rows_per_hour") is None:
        root = "FREQUENCY_SHIFT_CAUSE_UNRESOLVED"
    else:
        root = "FREQUENCY_SHIFT_MIXED_BUT_EXPLAINABLE"

    top_regime = high_freq_regimes["top_quartile"]
    if top_regime["N"] < 50:
        high_freq_class = "HISTORICAL_HIGH_FREQUENCY_SAMPLE_INSUFFICIENT"
    elif (top_regime["PF"] or 0) > 1 and (top_regime["expectancy"] or 0) > 0:
        high_freq_class = "HISTORICAL_HIGH_FREQUENCY_ALPHA_SURVIVES"
    elif (top_regime["PF"] or 0) > 1:
        high_freq_class = "HISTORICAL_HIGH_FREQUENCY_ALPHA_WEAKENS"
    else:
        high_freq_class = "HISTORICAL_HIGH_FREQUENCY_ALPHA_FAILS"

    implication = "REGIME_AUDIT_FOUND_BLOCKER" if parity["implementation_mismatch_blocker"] else ("REGIME_AUDIT_REQUIRES_MORE_SHADOW_TIME" if root in ("FREQUENCY_SHIFT_PRIMARILY_TRIGGER_PROPENSITY", "FREQUENCY_SHIFT_CAUSE_UNRESOLVED") else "REGIME_AUDIT_SUPPORTS_LIVE_TRANSLATION_RESEARCH")

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "apples_to_apples_denominators": apples,
        "rate_decomposition": rate_decomp,
        "implementation_parity_recheck": parity,
        "historical_rolling_window_frequency": rolling,
        "market_activity_normalization": {
            "historical_signals_per_100_unique_mints": apples["historical"]["unique_signals_per_unique_observed_mint"] * 100.0,
            "prospective_signals_per_100_unique_mints": apples["prospective_N200"]["unique_signals_per_unique_observed_mint"] * 100.0 if apples["prospective_N200"]["unique_signals_per_unique_observed_mint"] is not None else None,
            "historical_signals_per_1000_eligible_events": apples["historical"]["unique_signals_per_eligible_market_event"] * 1000.0,
            "prospective_signals_per_1000_eligible_events": apples["prospective_N200"]["unique_signals_per_eligible_market_event"] * 1000.0 if apples["prospective_N200"]["unique_signals_per_eligible_market_event"] is not None else None,
            "historical_signals_per_1000_score_evaluations": apples["historical"]["unique_signals_per_event_gated_state"] * 1000.0,
            "prospective_signals_per_1000_score_evaluations": apples["prospective_N200"]["unique_signals_per_event_gated_state"] * 1000.0 if apples["prospective_N200"]["unique_signals_per_event_gated_state"] is not None else None,
            "historical_threshold_crossings_per_1000_score_evaluations": apples["historical"]["threshold_crossings_per_score_evaluation"] * 1000.0,
            "prospective_threshold_crossings_per_1000_score_evaluations": apples["prospective_N200"]["threshold_crossings_per_score_evaluation"] * 1000.0 if apples["prospective_N200"]["threshold_crossings_per_score_evaluation"] is not None else None,
        },
        "score_distribution_comparison": score_comparison,
        "lifecycle_state_composition": state_comparison,
        "historical_high_frequency_regimes": high_freq_regimes,
        "prospective_temporal_performance_by_utc_hour": prospective_temporal,
        "last50_last25_decay_context": decay_context,
        "POST_CHECKPOINT_RESOLUTION_STATUS": n200_unresolved,
        "regime_duration": {
            "current_unique_signals": current_signals,
            "current_elapsed_hours": current_elapsed_h,
            "current_signals_per_hour": current_signals / current_elapsed_h if current_signals and current_elapsed_h else None,
            "wall_clock_market_coverage_note": "N200 covers about %.2f hours; this is high trade count but still short market-time coverage." % pros_elapsed_h,
        },
        "final_root_cause_classification": root,
        "historical_high_frequency_alpha_classification": high_freq_class,
        "live_translation_implication": implication,
        "reference": {
            "historical_canonical": "2158 unique earliest-trigger mints over 132.765946 hours",
            "n200_classification": n200.get("classification"),
            "historical_corrected_validation": corrected_final.get("headline", {}).get("validation"),
            "historical_corrected_final_test": corrected_final.get("headline", {}).get("final_test"),
        },
    }
    write_json(os.path.join(PROS, "frequency_regime_comparability_audit.json"), report)
    print(json.dumps(compact(report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
