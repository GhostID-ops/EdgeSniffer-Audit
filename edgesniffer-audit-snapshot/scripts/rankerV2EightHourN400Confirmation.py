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
HIST_AUDIT = os.path.join(OUT, "frequency_regime_comparability_audit.json")
THRESHOLD = 0.590596
START_UTC = "2026-09-03T14:26:22.198000+00:00"


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


def metrics(rows, lane="p75_primary"):
    vals = []
    out = Counter()
    unresolved = 0
    for r in rows:
        o = r.get(lane) or {}
        pnl = num(o.get("pnl"))
        outcome = o.get("outcome")
        if pnl is None or outcome in ("ENTRY_UNRESOLVED", "120S_UNRESOLVED", "DATA_INTEGRITY_UNRESOLVED"):
            unresolved += 1
            continue
        vals.append(pnl)
        out[outcome] += 1
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
        "median_return": q(vals, 0.5),
        "normalized_PnL": sum(vals) / 100.0,
        "TP": out.get("TP", 0),
        "SL": out.get("SL", 0),
        "TRUE_120S_MAX_HOLD": out.get("TRUE_120S_MAX_HOLD", 0),
    }


def vals(rows, lane="p75_primary"):
    out = []
    for r in rows:
        o = r.get(lane) or {}
        pnl = num(o.get("pnl"))
        outcome = o.get("outcome")
        if pnl is not None and outcome not in ("ENTRY_UNRESOLVED", "120S_UNRESOLVED", "DATA_INTEGRITY_UNRESOLVED"):
            out.append(pnl)
    return out


def capacity(rows, max_open=1):
    active = []
    accepted = []
    rejected = 0
    for row in sorted(rows, key=lambda r: ((r.get("signal") or {}).get("threshold_decision_ts_ms") or 0)):
        ts = (row.get("signal") or {}).get("threshold_decision_ts_ms") or 0
        active = [e for e in active if e > ts]
        if len(active) >= max_open:
            rejected += 1
            continue
        accepted.append(row)
        active.append((row.get("p75_primary") or {}).get("exit_ts") or ts + 120_025)
    return {"accepted": len(accepted), "capacity_rejected": rejected, **metrics(accepted)}


def brief(rows):
    m = metrics(rows)
    s5 = metrics(rows, "p75_stress5")
    s10 = metrics(rows, "p75_stress10")
    c1 = capacity(rows)
    return {
        **m,
        "+5_PF": s5["PF"],
        "+5_expectancy": s5["expectancy"],
        "+5_PnL": s5["normalized_PnL"],
        "+10_PF": s10["PF"],
        "+10_expectancy": s10["expectancy"],
        "+10_PnL": s10["normalized_PnL"],
        "MAX_OPEN1_PF": c1["PF"],
        "MAX_OPEN1_expectancy": c1["expectancy"],
        "MAX_OPEN1_accepted": c1["accepted"],
    }


def state_comp(rows):
    c = Counter((r.get("signal") or {}).get("historical_state_alias") or (r.get("signal") or {}).get("state") for r in rows)
    n = len(rows)
    return {k: {"N": v, "share": v * 100.0 / n if n else None} for k, v in sorted(c.items())}


def state_delta(a, b):
    ca, cb = state_comp(a), state_comp(b)
    out = {}
    for k in sorted(set(ca) | set(cb)):
        out[k] = {
            "first_share": (ca.get(k) or {}).get("share", 0.0),
            "second_share": (cb.get(k) or {}).get("share", 0.0),
            "delta_pp": (cb.get(k) or {}).get("share", 0.0) - (ca.get(k) or {}).get("share", 0.0),
            "first_N": (ca.get(k) or {}).get("N", 0),
            "second_N": (cb.get(k) or {}).get("N", 0),
        }
    return out


def dist(vals_):
    xs = [v for v in vals_ if finite(v)]
    return {"N": len(xs), "P50": q(xs, 0.5), "P75": q(xs, 0.75), "P90": q(xs, 0.9), "P95": q(xs, 0.95), "P99": q(xs, 0.99), "max": max(xs) if xs else None}


def movement(rows):
    xs = []
    for r in rows:
        v = num((((r.get("entry_quality") or {}).get("descriptive_movement_after_decision_ms") or {}).get("25") or {}).get("pct_from_state"))
        if v is not None:
            xs.append(v)
    return {
        **dist(xs),
        "max_adverse": max(xs) if xs else None,
        "pct_adverse_gt_1": sum(1 for x in xs if x > 1) * 100.0 / len(xs) if xs else None,
        "pct_adverse_gt_2": sum(1 for x in xs if x > 2) * 100.0 / len(xs) if xs else None,
        "pct_adverse_gt_5": sum(1 for x in xs if x > 5) * 100.0 / len(xs) if xs else None,
        "pct_adverse_gt_10": sum(1 for x in xs if x > 10) * 100.0 / len(xs) if xs else None,
    }


def equity_stats(pnls):
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    max_streak = 0
    for p in pnls:
        cum += p / 100.0
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        if p <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return {"max_drawdown": max_dd, "longest_non_positive_streak": max_streak}


def remove_top(pnls, n):
    tmp = list(pnls)
    for w in sorted([v for v in pnls if v > 0], reverse=True)[:n]:
        try:
            tmp.remove(w)
        except ValueError:
            pass
    wins, losses = [v for v in tmp if v > 0], [v for v in tmp if v < 0]
    gp, gl = sum(wins), abs(sum(losses))
    return {"N": len(tmp), "PF": gp / gl if gl else (999.0 if gp else None), "expectancy": sum(tmp) / len(tmp) if tmp else None}


def bootstrap(pnls, block=None, iters=5000):
    rng = random.Random(4001476 + (block or 0))
    if not pnls:
        return {}
    means = []
    if block:
        blocks = [pnls[i:i + block] for i in range(max(1, len(pnls) - block + 1))]
        for _ in range(iters):
            sample = []
            while len(sample) < len(pnls):
                sample.extend(blocks[rng.randrange(len(blocks))])
            sample = sample[:len(pnls)]
            means.append(sum(sample) / len(sample))
    else:
        for _ in range(iters):
            sample = [pnls[rng.randrange(len(pnls))] for _ in pnls]
            means.append(sum(sample) / len(sample))
    return {"interval95": [q(means, 0.025), q(means, 0.975)], "pct_expectancy_gt_0": sum(1 for x in means if x > 0) * 100.0 / iters}


def main():
    now = datetime.now(timezone.utc)
    start_dt = datetime.fromisoformat(START_UTC)
    start_ms = int(start_dt.timestamp() * 1000)
    end8_ms = start_ms + 8 * 3_600_000
    manifest = json.load(open(os.path.join(OUT, "cohort_manifest.json"), encoding="utf-8"))
    health = json.load(open(os.path.join(OUT, "service_health.json"), encoding="utf-8"))
    freeze = json.load(open(os.path.join(FREEZE, "freeze_manifest.json"), encoding="utf-8"))

    signals_all_raw = sorted(stream_gzip_jsonl(os.path.join(OUT, "signals.jsonl.gz")), key=lambda r: (r.get("threshold_decision_ts_ms") or 0, r.get("mint") or ""))
    signals_all = []
    seen = set()
    for s in signals_all_raw:
        if s.get("mint") in seen:
            continue
        seen.add(s.get("mint"))
        signals_all.append(s)
    elapsed = (now - start_dt).total_seconds() / 3600
    if elapsed < 8.0 or len(signals_all) < 400:
        print(json.dumps({"status": "RANKER_V2_TIME_DIVERSITY_CHECKPOINT_NOT_READY", "current_utc": now.isoformat().replace("+00:00", "Z"), "elapsed_hours": elapsed, "unique_signals": len(signals_all)}, indent=2))
        return

    first400_signals = signals_all[:400]
    first8h_signals = [s for s in signals_all if start_ms <= (s.get("threshold_decision_ts_ms") or 0) <= end8_ms]
    mints = {s.get("mint") for s in first400_signals} | {s.get("mint") for s in first8h_signals}

    outcomes = {r.get("mint"): r for r in stream_gzip_jsonl(os.path.join(OUT, "outcomes.jsonl.gz")) if r.get("mint")}
    entries = {}
    for r in stream_gzip_jsonl(os.path.join(OUT, "entry_quality.jsonl.gz")):
        if r.get("mint") in mints:
            old = entries.get(r["mint"])
            if old is None or old.get("source_continuity_status") == "PENDING":
                entries[r["mint"]] = r

    def rows_for(sigs):
        rows = []
        for s in sigs:
            r = dict(outcomes.get(s["mint"]) or {"mint": s["mint"], "signal": s})
            r["signal"] = r.get("signal") or s
            r["entry_quality"] = entries.get(s["mint"]) or {}
            rows.append(r)
        return rows

    first400 = rows_for(first400_signals)
    first8h = rows_for(first8h_signals)
    h04 = [r for r in first8h if (r["signal"]["threshold_decision_ts_ms"] < start_ms + 4 * 3_600_000)]
    h48 = [r for r in first8h if (start_ms + 4 * 3_600_000 <= r["signal"]["threshold_decision_ts_ms"] <= end8_ms)]

    def time_block(h0, h1):
        a, b = start_ms + h0 * 3_600_000, start_ms + h1 * 3_600_000
        rs = [r for r in first8h if a <= r["signal"]["threshold_decision_ts_ms"] < b]
        return {**brief(rs), "signal_rate": len(rs) / (h1 - h0)}

    n_blocks = {}
    for i in range(0, 400, 100):
        rs = first400[i:i + 100]
        n_blocks[f"{i + 1}-{i + 100}"] = brief(rs)
    two_hour_blocks = {f"H{a}-{b}": time_block(a, b) for a, b in ((0, 2), (2, 4), (4, 6), (6, 8))}
    recent = {f"last_{n}": brief(first400[-n:]) for n in (25, 50, 100)}

    # Hourly score/threshold props and service-counter deltas from health snapshots.
    scores_by_hour = defaultdict(list)
    threshold_by_hour = Counter()
    score_mints_by_hour = defaultdict(set)
    for r in stream_gzip_jsonl(os.path.join(OUT, "all_scores.jsonl.gz")):
        ts = r.get("threshold_decision_ts_ms") or 0
        if start_ms <= ts < end8_ms:
            h = int((ts - start_ms) // 3_600_000)
            sc = num(r.get("score"))
            if sc is not None:
                scores_by_hour[h].append(sc)
                if sc >= THRESHOLD:
                    threshold_by_hour[h] += 1
            if r.get("mint"):
                score_mints_by_hour[h].add(r["mint"])

    hourly_signals = defaultdict(list)
    for r in first8h:
        h = int((r["signal"]["threshold_decision_ts_ms"] - start_ms) // 3_600_000)
        hourly_signals[h].append(r)

    hourly = {}
    for h in range(8):
        scs = scores_by_hour[h]
        hourly[f"hour_{h + 1}"] = {
            "unique_signals": len(hourly_signals[h]),
            "mints_observed_proxy": len(score_mints_by_hour[h]),
            "eligible_events": None,
            "score_evaluations": len(scs),
            "threshold_crossings": threshold_by_hour[h],
            "pct_score_evals_gte_threshold": threshold_by_hour[h] * 100.0 / len(scs) if scs else None,
        }

    score_vals = [x for xs in scores_by_hour.values() for x in xs]
    hist_pct = 0.348491379
    pct_gte = sum(1 for x in score_vals if x >= THRESHOLD) * 100.0 / len(score_vals) if score_vals else None
    if pct_gte is None:
        drift = "NO_MATERIAL_DRIFT"
    elif pct_gte > hist_pct * 6 and brief(h48)["PF"] and brief(h48)["PF"] < 1:
        drift = "MATERIAL_DRIFT_WITH_ECONOMIC_DETERIORATION"
    elif pct_gte > hist_pct * 6:
        drift = "MATERIAL_DRIFT_REQUIRES_MORE_SHADOW"
    elif pct_gte > hist_pct * 2:
        drift = "DESCRIPTIVE_REGIME_DRIFT_ALPHA_SURVIVES"
    else:
        drift = "NO_MATERIAL_DRIFT"

    pnls400 = vals(first400)
    wins = sorted([v for v in pnls400 if v > 0], reverse=True)
    total = sum(pnls400)
    robustness = {
        **equity_stats(pnls400),
        "largest_winner_contribution_pct": sum(wins[:1]) * 100.0 / total if total else None,
        "top5_winner_contribution_pct": sum(wins[:5]) * 100.0 / total if total else None,
        "top10_winner_contribution_pct": sum(wins[:10]) * 100.0 / total if total else None,
        "PF_after_removing_top5": remove_top(pnls400, 5)["PF"],
        "PF_after_removing_top10": remove_top(pnls400, 10)["PF"],
        "IID_bootstrap": bootstrap(pnls400),
        "block5_bootstrap": bootstrap(pnls400, 5),
        "block10_bootstrap": bootstrap(pnls400, 10),
        "block20_bootstrap": bootstrap(pnls400, 20),
    }

    data_integrity = {
        "duplicate_mints_first400": len(first400_signals) - len({s.get("mint") for s in first400_signals}),
        "malformed_records": health.get("completed_malformed_records"),
        "dropped_records": (health.get("rejects") or {}).get("dropped_rows", 0),
        "source_gaps": (health.get("rejects") or {}).get("ingestion_gaps", 0),
        "reconnects": (health.get("rejects") or {}).get("reconnects", 0),
        "feature_failures": (health.get("rejects") or {}).get("feature_failures", 0),
        "score_failures": (health.get("counters") or {}).get("score_failures", 0),
        "service_restarts": health.get("n_restarts"),
        "submissions": health.get("submission_count"),
        "ENTRY_UNRESOLVED_first400": sum(1 for r in first400 if (r.get("p75_primary") or {}).get("outcome") == "ENTRY_UNRESOLVED"),
        "120S_UNRESOLVED_first400": sum(1 for r in first400 if (r.get("p75_primary") or {}).get("outcome") == "120S_UNRESOLVED"),
        "DATA_INTEGRITY_UNRESOLVED_first400": sum(1 for r in first400 if (r.get("p75_primary") or {}).get("outcome") == "DATA_INTEGRITY_UNRESOLVED"),
        "ENTRY_UNRESOLVED_first8h": sum(1 for r in first8h if (r.get("p75_primary") or {}).get("outcome") == "ENTRY_UNRESOLVED"),
        "120S_UNRESOLVED_first8h": sum(1 for r in first8h if (r.get("p75_primary") or {}).get("outcome") == "120S_UNRESOLVED"),
        "DATA_INTEGRITY_UNRESOLVED_first8h": sum(1 for r in first8h if (r.get("p75_primary") or {}).get("outcome") == "DATA_INTEGRITY_UNRESOLVED"),
    }
    blocker = any(data_integrity[k] for k in ("duplicate_mints_first400", "malformed_records", "dropped_records", "source_gaps", "reconnects", "feature_failures", "score_failures", "service_restarts", "submissions"))

    first400_b = brief(first400)
    first8_b = brief(first8h)
    h48_b = brief(h48)
    if blocker:
        cls = "RANKER_V2_8H_N400_DATA_BLOCKER"
        nxt = "RANKER_V2_TIME_DIVERSITY_BLOCKER"
    elif first400_b["PF"] and first400_b["PF"] > 3 and first400_b["+5_PF"] and first400_b["+5_PF"] > 1 and h48_b["PF"] and h48_b["PF"] > 1 and drift != "MATERIAL_DRIFT_WITH_ECONOMIC_DETERIORATION":
        cls = "RANKER_V2_8H_N400_ALPHA_CONFIRMED_PROMISING"
        nxt = "RANKER_V2_LIVE_TRANSLATION_RESEARCH_READY"
    elif first400_b["PF"] and first400_b["PF"] > 1:
        cls = "RANKER_V2_8H_N400_ALPHA_MIXED"
        nxt = "RANKER_V2_MORE_SHADOW_TIME_REQUIRED"
    else:
        cls = "RANKER_V2_8H_N400_ALPHA_WEAKENING"
        nxt = "RANKER_V2_MORE_SHADOW_TIME_REQUIRED"

    report = {
        "overall_classification": cls,
        "production_next_step_classification": nxt,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "integrity": {
            "scorer_hash": manifest.get("canonical_spec_sha256"),
            "semantic_contract_hashes": (json.load(open(os.path.join(FREEZE, "freeze_manifest.json"), encoding="utf-8"))).get("contract_hashes"),
            "threshold": manifest.get("threshold"),
            "data_integrity": data_integrity,
            "signal_affecting_issue": blocker,
        },
        "first400": first400_b,
        "first400_sellability": {"P50": metrics(first400, "p50"), "P75": metrics(first400), "P90": metrics(first400, "p90")},
        "first8h": first8_b,
        "time_diversity": {"hours_0_4": {**brief(h04), "signals_per_hour": len(h04) / 4}, "hours_4_8": {**brief(h48), "signals_per_hour": len(h48) / 4}},
        "trade_count_diversity": {"signals_1_200": brief(first400[:200]), "signals_201_400": brief(first400[200:400])},
        "n100_blocks": n_blocks,
        "two_hour_time_blocks": two_hour_blocks,
        "recent_windows_at_gate": recent,
        "frequency_regime": {"hourly": hourly, "trigger_propensity": "NORMALIZING" if len(hourly_signals[7]) < len(hourly_signals[0]) else "STILL_ELEVATED"},
        "state_composition": {"hours_0_4_vs_4_8": state_delta(h04, h48), "signals_1_200_vs_201_400": state_delta(first400[:200], first400[200:400])},
        "state_economics_descriptive_only": {k: brief(v) for k, v in defaultdict(list, {k: [r for r in first8h if ((r.get("signal") or {}).get("historical_state_alias") or (r.get("signal") or {}).get("state")) == k] for k in state_comp(first8h)}).items()},
        "state_filter_warning": "NO STATE FILTERS MAY BE CREATED FROM THIS CHECKPOINT",
        "max_open1": {"first400": capacity(first400), "first8h": capacity(first8h), "hours_0_4": capacity(h04), "hours_4_8": capacity(h48), "signals_1_200": capacity(first400[:200]), "signals_201_400": capacity(first400[200:400])},
        "robustness_first400": robustness,
        "drift": {
            "classification": drift,
            "pct_scores_gte_threshold_first8h": pct_gte,
            "score_eval_N_first8h": len(score_vals),
            "score_P50": q(score_vals, 0.5),
            "score_P95": q(score_vals, 0.95),
            "score_P99": q(score_vals, 0.99),
        },
        "entry_movement": {"first8h": movement(first8h), "hours_0_4": movement(h04), "hours_4_8": movement(h48)},
        "equity_path": {
            "cumulative_pnl_by_hour": {f"{h}h": metrics([r for r in first8h if r["signal"]["threshold_decision_ts_ms"] < start_ms + h * 3_600_000])["normalized_PnL"] for h in range(1, 9)},
            **equity_stats(vals(first8h)),
        },
        "historical_comparison": {
            "validation": {"PF": 3.43, "expectancy": 7.63, "+5_PF": 1.05},
            "final": {"PF": 5.06, "expectancy": 11.01, "+5_PF": 1.86},
            "first400": {"PF": first400_b["PF"], "expectancy": first400_b["expectancy"], "+5_PF": first400_b["+5_PF"]},
            "first8h": {"PF": first8_b["PF"], "expectancy": first8_b["expectancy"], "+5_PF": first8_b["+5_PF"]},
        },
        "current_collector": {
            "current_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "total_current_unique_signals": (health.get("counters") or {}).get("unique_triggered_mints"),
            "resolved": health.get("resolved_primary_outcomes"),
            "open": health.get("open_outcomes"),
            "PID": health.get("pid"),
            "NRestarts": health.get("n_restarts"),
            "submission_count": health.get("submission_count"),
        },
    }
    write_json(os.path.join(OUT, "eight_hour_n400_time_diversity_confirmation.json"), report)
    print(json.dumps(compact(report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
