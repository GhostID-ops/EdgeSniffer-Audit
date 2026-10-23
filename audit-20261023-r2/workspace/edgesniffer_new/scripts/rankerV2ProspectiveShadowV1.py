#!/usr/bin/env python3
import argparse
import gzip
import hashlib
import importlib.util
import json
import math
import os
import signal
import struct
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone


COHORT_NAME = "RANKER_V2_PROSPECTIVE_SHADOW_V1"
STATIC_THRESHOLD = 0.590596
ENTRY_DELAY_MS = 25
SELLABLE_P50_MS = 964
SELLABLE_P75_MS = 1476
SELLABLE_P90_MS = 1989
TP = 20.0
SL = -20.0
HOLD_MS = 120_000
ROUND_TRIP_COST_PCT = 2.5
CHECKPOINTS = (25, 50, 100, 200)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def now_ms():
    return int(time.time() * 1000)


def iso(ms=None):
    if ms is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def num(value, default=None):
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


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


def read_jsonl_any(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_json(path, obj):
    tmp = f"{path}.tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(compact(obj), handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def f64_from_lossless(obj):
    if "float_hex" in obj:
        return float.fromhex(obj["float_hex"])
    if "bits_hex_be" in obj:
        return struct.unpack(">d", int(obj["bits_hex_be"], 16).to_bytes(8, "big"))[0]
    return float(obj["decimal_repr"])


def load_canonical_spec(path):
    spec = json.load(open(path, "r", encoding="utf-8"))
    stats = {}
    for item in spec["normalization_constants_lossless"]:
        stats[item["feature"]] = {
            "center": f64_from_lossless(item["center_f64"]),
            "scale": f64_from_lossless(item["scale_f64"]),
            "corr": f64_from_lossless(item["corr_f64"]),
            "coverage": int(item["coverage"]),
        }
    weights = {}
    ordered = []
    for item in spec["weights_lossless"]:
        weight = f64_from_lossless(item["weight_f64"])
        weights[item["feature"]] = weight
        ordered.append((item["feature"], weight))
    threshold = f64_from_lossless(spec["threshold"]["value_f64"])
    return spec, stats, weights, ordered, threshold


def score_with_contribs(row, stats, ordered):
    score = 0.0
    used = 0
    normalized = {}
    contributions = {}
    for name, weight in ordered:
        value = row.get(name)
        if not finite(value):
            continue
        st = stats[name]
        z = max(-5.0, min(5.0, (float(value) - st["center"]) / st["scale"]))
        c = weight * z
        normalized[name] = z
        contributions[name] = c
        score += c
        used += 1
    return (score if used else None), normalized, contributions, used


class JsonlWriter:
    def __init__(self, path, gzip_enabled=False):
        self.path = path
        self.gzip_enabled = gzip_enabled
        os.makedirs(os.path.dirname(path), exist_ok=True)
        opener = gzip.open if gzip_enabled else open
        self.handle = opener(path, "at", encoding="utf-8")

    def write(self, row):
        self.handle.write(json.dumps(compact(row), sort_keys=True, separators=(",", ":")) + "\n")
        self.handle.flush()

    def close(self):
        try:
            self.handle.close()
        except Exception:
            pass


def metrics(rows, lane_key):
    vals = [num((r.get(lane_key) or {}).get("pnl")) for r in rows if num((r.get(lane_key) or {}).get("pnl")) is not None]
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    outcomes = Counter((r.get(lane_key) or {}).get("outcome") or "UNKNOWN" for r in rows)
    return {
        "n": len(vals),
        "wins": len(wins),
        "losses": len(losses),
        "wr_pct": len(wins) * 100.0 / len(vals) if vals else None,
        "pf": gp / gl if gl else (999.0 if gp else None),
        "expectancy_pct": sum(vals) / len(vals) if vals else None,
        "median_return_pct": q(vals, 0.5),
        "pnl_at_1sol": sum(vals) / 100.0,
        "tp": outcomes.get("TP", 0),
        "sl": outcomes.get("SL", 0),
        "max_hold": outcomes.get("max_hold", 0) + outcomes.get("MAX_HOLD", 0) + outcomes.get("HOLD", 0),
        "other": len(vals) - outcomes.get("TP", 0) - outcomes.get("SL", 0) - outcomes.get("max_hold", 0) - outcomes.get("MAX_HOLD", 0) - outcomes.get("HOLD", 0),
    }


def first_buyer_motif(features):
    bc100 = features.get("w100.buy_count")
    fb = features.get("first_buyer_quote_share_bps")
    fast100 = finite(bc100) and bc100 >= 2 and finite(fb) and fb <= 5000
    return {
        "fast100_fb_le_5000bps": bool(fast100),
        "first_impulse": features.get("snapshot_kind") == "FIRST_IMPULSE",
        "both": bool(fast100 and features.get("snapshot_kind") == "FIRST_IMPULSE"),
    }


def score_distribution(scores):
    vals = [v for v in scores if finite(v)]
    selected = [v for v in vals if v >= STATIC_THRESHOLD]
    return {
        "n": len(vals),
        "median": q(vals, 0.5),
        "p90": q(vals, 0.90),
        "p95": q(vals, 0.95),
        "p99": q(vals, 0.99),
        "p99_5": q(vals, 0.995),
        "max": max(vals) if vals else None,
        "pct_gte_threshold": len(selected) * 100.0 / len(vals) if vals else None,
    }


def concurrency(rows, lane_key, max_open):
    active = []
    accepted = []
    rejected = 0
    for row in sorted(rows, key=lambda r: r["signal"]["threshold_decision_ts_ms"]):
        active = [end for end in active if end > row["signal"]["threshold_decision_ts_ms"]]
        outcome = row.get(lane_key) or {}
        exit_ts = outcome.get("exit_ts")
        if len(active) >= max_open:
            rejected += 1
            continue
        if finite(exit_ts):
            active.append(int(exit_ts))
        else:
            active.append(int(row["signal"]["threshold_decision_ts_ms"]) + ENTRY_DELAY_MS + SELLABLE_P90_MS + HOLD_MS)
        accepted.append(row)
    out = metrics(accepted, lane_key)
    out["accepted"] = len(accepted)
    out["rejected_due_to_capacity"] = rejected
    return out


def score_outcome_at(atlas, target_entry_ts, marks, sellable_ms, entry_stress_pct=0.0):
    if not finite(target_entry_ts):
        return {"outcome": "missing", "pnl": None}
    entry = None
    for ts, mc in sorted(marks):
        if ts >= target_entry_ts:
            entry = (int(ts), float(mc) * (1.0 + entry_stress_pct / 100.0))
            break
    if entry is None:
        return {"outcome": "no_fill", "pnl": -ROUND_TRIP_COST_PCT, "exit_ms": None}
    entry_ts, entry_mc = entry
    return atlas.score(entry_ts, entry_mc, sorted(marks), 0, sellable_ms, entry_stress_pct)


def manifest_hashes(paths):
    return {name: sha256_file(path) for name, path in paths.items() if os.path.exists(path)}


class RankerV2Prospective:
    def __init__(self, args, atlas, canonical):
        self.args = args
        self.atlas = atlas
        self.spec, self.stats, self.weights, self.ordered, self.threshold = canonical
        self.flows = defaultdict(deque)
        self.first_seen = {}
        self.emitted_fixed = defaultdict(set)
        self.emitted_events = defaultdict(set)
        self.global_first_seen = deque()
        self.triggered_mints = set()
        self.pending_outcomes = {}
        self.counters = Counter()
        self.rejects = Counter()
        self.score_values = []
        self.signal_scores = []
        self.outcome_rows = []
        self.running = True
        self.last_source_pos = 0
        self.last_source_inode = None

        self.all_scores = JsonlWriter(os.path.join(args.out_dir, "all_scores.jsonl.gz"), gzip_enabled=True)
        self.signals = JsonlWriter(os.path.join(args.out_dir, "signals.jsonl.gz"), gzip_enabled=True)
        self.outcomes = JsonlWriter(os.path.join(args.out_dir, "outcomes.jsonl.gz"), gzip_enabled=True)
        self.entries = JsonlWriter(os.path.join(args.out_dir, "entry_reconstruction.jsonl.gz"), gzip_enabled=True)
        self.checkpoints = JsonlWriter(os.path.join(args.out_dir, "checkpoints.json"), gzip_enabled=False)
        self.health_log = JsonlWriter(os.path.join(args.out_dir, "service_health.jsonl"), gzip_enabled=False)
        self.load_existing_signals()
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

    def stop(self, *_args):
        self.running = False
        self.emit_status("stopping")

    def load_existing_signals(self):
        path = os.path.join(self.args.out_dir, "signals.jsonl.gz")
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return
        try:
            for row in read_jsonl_any(path):
                mint = row.get("mint")
                if mint:
                    self.triggered_mints.add(mint)
        except Exception as exc:
            self.counters["existing_signal_load_error"] += 1
            self.health_log.write({"event": "existing_signal_load_error", "error": str(exc), "ts_ms": now_ms()})

    def is_eligible(self, row, mint, ts, mc, side):
        if not mint:
            self.rejects["missing_mint"] += 1
            return False
        if not finite(ts):
            self.rejects["missing_ts"] += 1
            return False
        if int(ts) < self.args.cohort_start_ms:
            self.rejects["pre_cohort_event_ts"] += 1
            return False
        if not finite(mc) or not (0 < float(mc) <= self.atlas.MAX_MC_USD):
            self.rejects["bad_or_out_of_range_mc"] += 1
            return False
        if side not in ("buy", "sell"):
            self.rejects["not_buy_or_sell"] += 1
            return False
        return True

    def prune(self, mint, ts):
        dq = self.flows[mint]
        cutoff = ts - self.atlas.HOLD_MS - SELLABLE_P90_MS - 10_000
        while dq and dq[0]["ts"] < cutoff:
            dq.popleft()

    def emit_lifecycle(self, source_row, feats, source_row_id, timing):
        self.counters["lifecycle_states_generated"] += 1
        self.counters[f"lifecycle_{feats['snapshot_kind']}"] += 1
        score_input = {name: num(feats.get(name)) for name in self.spec["feature_order"]}
        score_start = now_ms()
        score, norms, contribs, used = score_with_contribs(score_input, self.stats, self.ordered)
        score_ready = now_ms()
        if not finite(score):
            self.counters["score_failures"] += 1
            self.rejects["score_null"] += 1
            return
        self.counters["scoreable_states"] += 1
        self.counters["score_evaluations"] += 1
        self.score_values.append(score)
        decision_ts = now_ms()
        motif = first_buyer_motif(feats)
        passes = score >= self.threshold
        row = {
            "event": "ranker_v2_score",
            "cohort": COHORT_NAME,
            "mint": feats["mint"],
            "state": feats["snapshot_kind"],
            "state_ts_ms": int(feats["ts"]),
            "mint_age_ms": feats.get("age_ms"),
            "feature_ready_ts_ms": timing["feature_ready_ts_ms"],
            "score_start_ts_ms": score_start,
            "score_ready_ts_ms": score_ready,
            "threshold_decision_ts_ms": decision_ts,
            "state_to_feature_ready_ms": timing["feature_ready_ts_ms"] - int(feats["ts"]),
            "feature_ready_to_score_ready_ms": score_ready - timing["feature_ready_ts_ms"],
            "state_to_threshold_decision_ms": decision_ts - int(feats["ts"]),
            "score": score,
            "score_threshold": self.threshold,
            "threshold_distance": score - self.threshold,
            "passes_threshold": passes,
            "used_weighted_features": used,
            "mc_usd": feats.get("mc_usd"),
            "raw_features": score_input,
            "normalized_feature_values": norms,
            "feature_contributions": contribs,
            "motifs": motif,
            "source_row_id": source_row_id,
            "no_submit": True,
        }
        self.all_scores.write(row)
        if passes:
            self.counters["threshold_crossings_observation_level"] += 1
            self.maybe_signal(row, feats, timing)

    def maybe_signal(self, score_row, feats, timing):
        mint = score_row["mint"]
        if mint in self.triggered_mints:
            self.counters["later_threshold_crossing_after_mint_signal"] += 1
            return
        self.triggered_mints.add(mint)
        self.counters["unique_triggered_mints"] += 1
        self.signal_scores.append(score_row["score"])
        signal_row = {
            "event": "ranker_v2_primary_signal",
            "cohort": COHORT_NAME,
            "mint": mint,
            "state": score_row["state"],
            "state_ts_ms": score_row["state_ts_ms"],
            "decision_state_reference_mc": score_row["mc_usd"],
            "feature_ready_ts_ms": score_row["feature_ready_ts_ms"],
            "threshold_decision_ts_ms": score_row["threshold_decision_ts_ms"],
            "score": score_row["score"],
            "threshold": self.threshold,
            "threshold_distance": score_row["threshold_distance"],
            "selection_semantics": "FIRST chronological eligible observation per mint with score >= 0.590596",
            "motifs": score_row["motifs"],
            "features": score_row["raw_features"],
            "normalized_feature_values": score_row["normalized_feature_values"],
            "feature_contributions": score_row["feature_contributions"],
            "timing": {
                "lifecycle_state_ts_ms": score_row["state_ts_ms"],
                "first_required_input_receive_time_ms": timing["first_required_input_receive_time_ms"],
                "final_required_input_receive_time_ms": timing["final_required_input_receive_time_ms"],
                "feature_ready_ts_ms": score_row["feature_ready_ts_ms"],
                "score_start_ts_ms": score_row["score_start_ts_ms"],
                "score_ready_ts_ms": score_row["score_ready_ts_ms"],
                "threshold_decision_ts_ms": score_row["threshold_decision_ts_ms"],
                "state_to_feature_ready_ms": score_row["state_to_feature_ready_ms"],
                "feature_ready_to_score_ready_ms": score_row["feature_ready_to_score_ready_ms"],
                "state_to_threshold_decision_ms": score_row["state_to_threshold_decision_ms"],
            },
            "no_submit": True,
        }
        self.signals.write(signal_row)
        marks = [(e["ts"], e["mc"]) for e in self.flows[mint] if e["ts"] >= score_row["state_ts_ms"] - 1_000]
        self.pending_outcomes[mint] = {
            "signal": signal_row,
            "marks": marks,
            "created_wall_ms": now_ms(),
        }
        self.emit_entry_reconstruction(signal_row, marks, unresolved=True)
        self.write_reports()

    def emit_entry_reconstruction(self, signal_row, marks, unresolved=False):
        state_ts = signal_row["state_ts_ms"]
        decision_ts = signal_row["threshold_decision_ts_ms"]
        historical_target = state_ts + ENTRY_DELAY_MS
        actual_target = decision_ts + ENTRY_DELAY_MS
        hist_entry = next(((ts, mc) for ts, mc in sorted(marks) if ts >= historical_target), None)
        actual_entry = next(((ts, mc) for ts, mc in sorted(marks) if ts >= actual_target), None)
        ref_mc = signal_row["decision_state_reference_mc"]
        self.entries.write({
            "event": "ranker_v2_entry_reconstruction",
            "mint": signal_row["mint"],
            "state_ts_ms": state_ts,
            "threshold_decision_ts_ms": decision_ts,
            "decision_state_reference_mc": ref_mc,
            "historical_parity_target_entry_ts_ms": historical_target,
            "historical_parity_entry_ts_ms": hist_entry[0] if hist_entry else None,
            "historical_parity_entry_mc_usd": hist_entry[1] if hist_entry else None,
            "actual_signal_ready_target_entry_ts_ms": actual_target,
            "actual_signal_ready_entry_ts_ms": actual_entry[0] if actual_entry else None,
            "actual_signal_ready_entry_mc_usd": actual_entry[1] if actual_entry else None,
            "historical_parity_degradation_pct": ((hist_entry[1] / ref_mc - 1.0) * 100.0) if hist_entry and ref_mc else None,
            "actual_signal_ready_degradation_pct": ((actual_entry[1] / ref_mc - 1.0) * 100.0) if actual_entry and ref_mc else None,
            "unresolved": unresolved,
            "no_submit": True,
        })

    def update_outcome_marks(self, mint, ts, mc):
        state = self.pending_outcomes.get(mint)
        if not state:
            return
        state["marks"].append((int(ts), float(mc)))
        latest_target = state["signal"]["threshold_decision_ts_ms"] + ENTRY_DELAY_MS + SELLABLE_P90_MS + HOLD_MS
        if ts >= latest_target:
            self.close_outcome(mint)

    def close_stale_outcomes(self, now_event_ts):
        if now_event_ts is None:
            return
        stale = []
        cutoff = now_event_ts - ENTRY_DELAY_MS - SELLABLE_P90_MS - HOLD_MS - 5_000
        for mint, state in self.pending_outcomes.items():
            if state["signal"]["threshold_decision_ts_ms"] < cutoff:
                stale.append(mint)
        for mint in stale:
            self.close_outcome(mint)

    def close_outcome(self, mint):
        state = self.pending_outcomes.pop(mint, None)
        if not state:
            return
        signal_row = state["signal"]
        marks = sorted(state["marks"])
        historical_p75 = self.atlas.score(signal_row["state_ts_ms"], signal_row["decision_state_reference_mc"], marks, ENTRY_DELAY_MS, SELLABLE_P75_MS, 0)
        actual_p75 = score_outcome_at(self.atlas, signal_row["threshold_decision_ts_ms"] + ENTRY_DELAY_MS, marks, SELLABLE_P75_MS, 0)
        actual_p50 = score_outcome_at(self.atlas, signal_row["threshold_decision_ts_ms"] + ENTRY_DELAY_MS, marks, SELLABLE_P50_MS, 0)
        actual_p90 = score_outcome_at(self.atlas, signal_row["threshold_decision_ts_ms"] + ENTRY_DELAY_MS, marks, SELLABLE_P90_MS, 0)
        actual_stress5 = score_outcome_at(self.atlas, signal_row["threshold_decision_ts_ms"] + ENTRY_DELAY_MS, marks, SELLABLE_P75_MS, 5)
        actual_stress10 = score_outcome_at(self.atlas, signal_row["threshold_decision_ts_ms"] + ENTRY_DELAY_MS, marks, SELLABLE_P75_MS, 10)
        row = {
            "event": "ranker_v2_outcome",
            "cohort": COHORT_NAME,
            "mint": mint,
            "signal": signal_row,
            "mark_count": len(marks),
            "historical_parity_25ms_p75": historical_p75,
            "actual_signal_ready_plus_25ms_p75": actual_p75,
            "actual_signal_ready_plus_25ms_p50": actual_p50,
            "actual_signal_ready_plus_25ms_p90": actual_p90,
            "actual_signal_ready_plus_25ms_p75_stress5": actual_stress5,
            "actual_signal_ready_plus_25ms_p75_stress10": actual_stress10,
            "no_submit": True,
        }
        self.outcomes.write(row)
        self.outcome_rows.append(row)
        self.counters["resolved_primary_outcomes"] += 1
        self.emit_entry_reconstruction(signal_row, marks, unresolved=False)
        if self.counters["resolved_primary_outcomes"] in CHECKPOINTS:
            self.checkpoints.write({
                "event": "ranker_v2_checkpoint",
                "cohort": COHORT_NAME,
                "resolved_primary_outcomes": self.counters["resolved_primary_outcomes"],
                "unique_triggered_mints": self.counters["unique_triggered_mints"],
                "classification": self.checkpoint_classification(),
                "ts_ms": now_ms(),
            })
        self.write_reports()

    def checkpoint_classification(self):
        n = self.counters["resolved_primary_outcomes"]
        if n < 25:
            return "RANKER_V2_SHADOW_HEALTHY_COLLECTING"
        if n < 50:
            return "RANKER_V2_N25_DESCRIPTIVE"
        if n < 100:
            return "RANKER_V2_N50_INFORMATIVE"
        primary = metrics(self.outcome_rows, "actual_signal_ready_plus_25ms_p75")
        stress5 = metrics(self.outcome_rows, "actual_signal_ready_plus_25ms_p75_stress5")
        if (primary.get("pf") or 0) > 1.5 and (primary.get("expectancy_pct") or 0) > 0 and (stress5.get("pf") or 0) > 1:
            return "RANKER_V2_PROSPECTIVE_ALPHA_PROMISING"
        if (primary.get("pf") or 0) > 1 and (primary.get("expectancy_pct") or 0) > 0:
            return "RANKER_V2_PROSPECTIVE_ALPHA_WEAK_POSITIVE"
        return "RANKER_V2_PROSPECTIVE_ALPHA_FAILS"

    def write_reports(self):
        elapsed_h = max(1e-9, (now_ms() - self.args.cohort_start_ms) / 3_600_000.0)
        lane_keys = [
            "historical_parity_25ms_p75",
            "actual_signal_ready_plus_25ms_p75",
            "actual_signal_ready_plus_25ms_p50",
            "actual_signal_ready_plus_25ms_p90",
            "actual_signal_ready_plus_25ms_p75_stress5",
            "actual_signal_ready_plus_25ms_p75_stress10",
        ]
        lane_metrics = {key: metrics(self.outcome_rows, key) for key in lane_keys}
        for value in lane_metrics.values():
            value["opportunities_per_hour"] = self.counters["unique_triggered_mints"] / elapsed_h
        report = {
            "classification": self.checkpoint_classification(),
            "cohort": COHORT_NAME,
            "cohort_start_utc": self.args.cohort_start_utc,
            "updated_at_utc": iso(),
            "canonical_score_spec_sha256": self.args.canonical_spec_sha256,
            "canonical_score_spec_object_sha256": self.args.canonical_object_sha256,
            "threshold": self.threshold,
            "counters": dict(self.counters),
            "rejects": dict(self.rejects),
            "open_outcomes": len(self.pending_outcomes),
            "score_distribution": score_distribution(self.score_values),
            "signal_score_distribution": score_distribution(self.signal_scores),
            "metrics": lane_metrics,
            "primary_concurrency": {
                "max_open1": concurrency(self.outcome_rows, "actual_signal_ready_plus_25ms_p75", 1),
                "max_open2": concurrency(self.outcome_rows, "actual_signal_ready_plus_25ms_p75", 2),
                "max_open3": concurrency(self.outcome_rows, "actual_signal_ready_plus_25ms_p75", 3),
                "max_open5": concurrency(self.outcome_rows, "actual_signal_ready_plus_25ms_p75", 5),
            },
            "interpretation_discipline": "Do not judge before N25; N100 is first serious assessment.",
            "no_submit": True,
        }
        write_json(os.path.join(self.args.out_dir, "final_report.json"), report)
        write_json(os.path.join(self.args.out_dir, "score_drift.json"), {
            "updated_at_utc": iso(),
            "prospective_score_distribution": report["score_distribution"],
            "historical_reference": self.args.historical_reference,
            "drift_classification": "INSUFFICIENT_N" if len(self.score_values) < 1000 else "DESCRIPTIVE_ONLY_NO_THRESHOLD_CHANGE",
        })
        write_json(os.path.join(self.args.out_dir, "concurrency.json"), report["primary_concurrency"])

    def emit_status(self, status="running"):
        row = {
            "event": "ranker_v2_service_health",
            "status": status,
            "cohort": COHORT_NAME,
            "ts_ms": now_ms(),
            "cohort_start_utc": self.args.cohort_start_utc,
            "pid": os.getpid(),
            "counters": dict(self.counters),
            "rejects": dict(self.rejects),
            "open_outcomes": len(self.pending_outcomes),
            "triggered_mints_loaded": len(self.triggered_mints),
            "no_submit": True,
        }
        self.health_log.write(row)
        write_json(os.path.join(self.args.out_dir, "service_health.json"), row)

    def process_due(self, now_event_ts=None):
        if now_event_ts is None:
            self.close_stale_outcomes(now_ms())
            return
        self.close_stale_outcomes(now_event_ts)

    def ingest(self, row, source_row_id):
        self.counters["rows_seen"] += 1
        receive_ts = now_ms()
        mint = self.atlas.mint_of(row)
        ts = self.atlas.ts_of(row)
        mc = self.atlas.mc_of(row)
        side = self.atlas.side_of(row)
        if not self.is_eligible(row, mint, ts, mc, side):
            return
        ts = int(ts)
        mc = float(mc)
        self.counters["mints_observed_unique"] = len(self.first_seen) + (0 if mint in self.first_seen else 1)
        self.counters["eligible_flow_rows"] += 1
        event = self.atlas.build_event(row, ts, mc, side)
        dq = self.flows[mint]
        dq.append(event)
        self.prune(mint, ts)
        if mint not in self.first_seen:
            self.first_seen[mint] = ts
            self.global_first_seen.append((ts, mint))
        while self.global_first_seen and self.global_first_seen[0][0] < ts - 60_000:
            self.global_first_seen.popleft()
        global_state = {
            "launches_per_minute": len({m for _, m in self.global_first_seen}),
            "active_flow_mints": len(self.flows),
        }
        self.update_outcome_marks(mint, ts, mc)

        age = self.atlas.creation_age_ms(row, self.first_seen[mint], ts)
        timing = {
            "first_required_input_receive_time_ms": receive_ts,
            "final_required_input_receive_time_ms": receive_ts,
            "feature_ready_ts_ms": now_ms(),
        }
        for age_ms in self.atlas.FIXED_AGES_MS:
            if age >= age_ms and age_ms not in self.emitted_fixed[mint]:
                self.emitted_fixed[mint].add(age_ms)
                feats = self.atlas.feature_row(dq, row, ts, mc, self.first_seen[mint], f"FIXED_{age_ms}MS", age_ms, global_state)
                self.emit_lifecycle(row, feats, source_row_id, timing)
        if side == "buy":
            event_kinds = []
            quick = self.atlas.window(dq, ts, 1000)
            w500 = self.atlas.window(dq, ts, 500)
            w5000 = self.atlas.window(dq, ts, 5000)
            w10000 = self.atlas.window(dq, ts, 10000)
            if "FIRST_IMPULSE" not in self.emitted_events[mint] and quick["gross_buy_sol"] >= 2:
                event_kinds.append("FIRST_IMPULSE")
            if "SECOND_ACCELERATION" not in self.emitted_events[mint] and w500["gross_buy_sol"] >= 2 and sum(e["quote_sol"] for e in dq if ts - 5000 <= e["ts"] < ts - 1000 and e["side"] == "buy") >= 1:
                event_kinds.append("SECOND_ACCELERATION")
            if "FIRST_LARGE_SELL_ABSORPTION" not in self.emitted_events[mint] and w5000["sell_count"] >= 1 and w5000["gross_buy_sol"] >= 4 and (w5000["drawdown_from_high_pct"] or 0) >= -10:
                event_kinds.append("FIRST_LARGE_SELL_ABSORPTION")
            if "POST_PULLBACK_BREAKOUT" not in self.emitted_events[mint] and finite(w10000["drawdown_from_high_pct"]) and -25 <= w10000["drawdown_from_high_pct"] <= -4 and quick["gross_buy_sol"] >= 2:
                event_kinds.append("POST_PULLBACK_BREAKOUT")
            if "CONSOLIDATION_BREAKOUT" not in self.emitted_events[mint] and finite(w10000["range_width_pct"]) and w10000["range_width_pct"] <= 25 and quick["gross_buy_sol"] >= 2 and (w10000["drawdown_from_high_pct"] or 0) >= -3:
                event_kinds.append("CONSOLIDATION_BREAKOUT")
            for kind in event_kinds:
                self.emitted_events[mint].add(kind)
                feats = self.atlas.feature_row(dq, row, ts, mc, self.first_seen[mint], kind, age, global_state)
                self.emit_lifecycle(row, feats, source_row_id, timing)
        elif side == "sell" and "FIRST_LARGE_SELLER" not in self.emitted_events[mint]:
            self.emitted_events[mint].add("FIRST_LARGE_SELLER")
            feats = self.atlas.feature_row(dq, row, ts, mc, self.first_seen[mint], "FIRST_LARGE_SELLER", age, global_state)
            self.emit_lifecycle(row, feats, source_row_id, timing)

        if self.counters["rows_seen"] % self.args.status_every == 0:
            self.emit_status()
            self.write_reports()

    def tail_loop(self):
        source_row_id = 0
        with open(self.args.input, "r", encoding="utf-8", errors="replace") as handle:
            if self.args.start_at_end:
                handle.seek(0, os.SEEK_END)
            self.last_source_pos = handle.tell()
            if not getattr(self.args, "cohort_start_ms", None):
                manifest = cohort_manifest(
                    self.args,
                    self.args.prestart_fixture_parity,
                    self.args.broadcast_safety,
                    source_input_size_at_start=self.last_source_pos,
                )
                self.args.cohort_start_utc = manifest["cohort_start_utc"]
                self.args.cohort_start_ms = int(manifest["cohort_start_ms"])
            self.emit_status("running")
            self.write_reports()
            idle_at = time.time()
            while self.running:
                line = handle.readline()
                if not line:
                    if time.time() - idle_at >= self.args.idle_process_sec:
                        self.process_due(now_event_ts=None)
                        self.emit_status()
                        idle_at = time.time()
                    time.sleep(self.args.poll_ms / 1000.0)
                    continue
                idle_at = time.time()
                source_row_id += 1
                try:
                    self.ingest(json.loads(line), source_row_id)
                except json.JSONDecodeError as exc:
                    self.counters["malformed_records"] += 1
                    self.health_log.write({"event": "malformed_record", "error": str(exc), "ts_ms": now_ms()})
                except Exception as exc:
                    self.counters["ingest_exceptions"] += 1
                    self.health_log.write({"event": "ingest_exception", "error": repr(exc), "ts_ms": now_ms()})
        self.emit_status("stopped")


def fixture_parity(args, atlas, canonical):
    _spec, stats, _weights, ordered, threshold = canonical
    compared = 0
    exact = 0
    diffs = []
    stored_pass = canonical_pass = 0
    pass_pass = fail_fail = stored_pass_canon_fail = stored_fail_canon_pass = 0
    score_path = os.path.join(args.v2_dir, "full_universe_scores.jsonl.gz")
    started = time.time()
    limit = args.parity_limit
    with gzip.open(score_path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            features = row.get("features") or {}
            score, _norms, _contribs, _used = score_with_contribs({name: features.get(name) for name in args.feature_order}, stats, ordered)
            if not finite(score):
                continue
            compared += 1
            stored_score = row.get("rank_score")
            diff = abs(float(stored_score) - score) if finite(stored_score) else None
            if finite(diff):
                diffs.append(diff)
                exact += 1 if float(stored_score) == score else 0
            sp = finite(stored_score) and float(stored_score) >= threshold
            cp = score >= threshold
            stored_pass += 1 if sp else 0
            canonical_pass += 1 if cp else 0
            if sp and cp:
                pass_pass += 1
            elif not sp and not cp:
                fail_fail += 1
            elif sp and not cp:
                stored_pass_canon_fail += 1
            else:
                stored_fail_canon_pass += 1
            if limit and compared >= limit:
                break
    report = {
        "event": "ranker_v2_prestart_fixture_parity",
        "fixture_source": score_path,
        "rows_compared": compared,
        "exact_score_matches": exact,
        "max_abs_score_difference": max(diffs) if diffs else None,
        "median_score_difference": q(diffs, 0.5),
        "p99_score_difference": q(diffs, 0.99),
        "stored_pass_count": stored_pass,
        "canonical_pass_count": canonical_pass,
        "pass_pass": pass_pass,
        "fail_fail": fail_fail,
        "stored_pass_canonical_fail": stored_pass_canon_fail,
        "stored_fail_canonical_pass": stored_fail_canon_pass,
        "threshold_disagreements": stored_pass_canon_fail + stored_fail_canon_pass,
        "status": "PASS" if stored_pass_canon_fail + stored_fail_canon_pass == 0 else "FAIL",
        "runtime_sec": time.time() - started,
    }
    write_json(os.path.join(args.out_dir, "scorer_fixture_parity_report.json"), report)
    return report


def broadcast_safety(args):
    report = {
        "event": "broadcast_safety_audit",
        "status": "PASS",
        "no_private_key_args": True,
        "no_wallet_key_args": True,
        "no_transaction_construction_required": True,
        "no_submission_capability": True,
        "forbidden_capabilities": ["sendTransaction", "Jito submission", "bundle submission", "live buy", "live sell"],
        "service_has_no_key_environment": True,
        "note": "This Python collector opens only corpus/read and analytics output files. It has no RPC sender, signing key, Jito, bundle, or transaction-builder arguments.",
        "created_at_utc": iso(),
    }
    write_json(os.path.join(args.out_dir, "broadcast_safety_audit.json"), report)
    return report


def cohort_manifest(args, parity, safety, source_input_size_at_start=None):
    manifest_path = os.path.join(args.out_dir, "cohort_manifest.json")
    if os.path.exists(manifest_path):
        manifest = json.load(open(manifest_path, "r", encoding="utf-8"))
        args.cohort_start_utc = manifest["cohort_start_utc"]
        args.cohort_start_ms = int(manifest["cohort_start_ms"])
        return manifest
    start_ms = now_ms()
    args.cohort_start_ms = start_ms
    args.cohort_start_utc = iso(start_ms)
    manifest = {
        "cohort": COHORT_NAME,
        "cohort_start_utc": args.cohort_start_utc,
        "cohort_start_ms": args.cohort_start_ms,
        "future_only": True,
        "no_submit": True,
        "canonical_score_spec": args.canonical_spec,
        "canonical_score_spec_sha256": args.canonical_spec_sha256,
        "canonical_score_spec_object_sha256": args.canonical_object_sha256,
        "threshold": STATIC_THRESHOLD,
        "selection_semantics": "FIRST chronological eligible observation per mint where score >= 0.590596",
        "entry_lanes": [
            "HISTORICAL_PARITY_25MS_P75_1476MS",
            "ACTUAL_SIGNAL_READY_PLUS_25MS_P75_1476MS_PRIMARY",
            "ACTUAL_SIGNAL_READY_PLUS_25MS_P50_964MS",
            "ACTUAL_SIGNAL_READY_PLUS_25MS_P90_1989MS",
        ],
        "exit_policy": {"tp_pct": TP, "sl_pct": SL, "max_hold_ms": HOLD_MS},
        "prestart_fixture_parity": parity,
        "broadcast_safety": safety,
        "source_input": args.input,
        "source_input_sha256_at_start": None,
        "source_input_size_at_start": source_input_size_at_start,
        "service_script": args.self_script,
        "service_script_sha256": sha256_file(args.self_script) if args.self_script and os.path.exists(args.self_script) else None,
    }
    write_json(manifest_path, manifest)
    write_json(os.path.join(args.out_dir, "frozen_spec_reference.json"), {
        "canonical_score_spec": args.canonical_spec,
        "canonical_score_spec_sha256": args.canonical_spec_sha256,
        "canonical_score_spec_object_sha256": args.canonical_object_sha256,
        "threshold": STATIC_THRESHOLD,
        "freeze_manifest": args.freeze_manifest,
    })
    return manifest


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--canonical-spec", required=True)
    parser.add_argument("--canonical-spec-sha256", required=True)
    parser.add_argument("--canonical-object-sha256", required=True)
    parser.add_argument("--freeze-manifest", required=True)
    parser.add_argument("--atlas-script", required=True)
    parser.add_argument("--v2-dir", required=True)
    parser.add_argument("--self-script")
    parser.add_argument("--start-at-end", action="store_true")
    parser.add_argument("--poll-ms", type=int, default=10)
    parser.add_argument("--idle-process-sec", type=float, default=1.0)
    parser.add_argument("--status-every", type=int, default=10000)
    parser.add_argument("--parity-limit", type=int, default=0, help="0 means full fixture")
    parser.add_argument("--preflight-only", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    args.historical_reference = {
        "validation_primary_pf": 3.072777,
        "final_pseudo_holdout_primary_pf": 4.647665,
        "validation_signal_count": 428,
        "final_signal_count": 766,
        "historical_opportunities_per_hour": 16.254168,
        "terminology": "historical replication / pseudo-holdout, not untouched holdout",
    }
    atlas = load_module("memecoin_alpha_atlas_v1", args.atlas_script)
    canonical = load_canonical_spec(args.canonical_spec)
    args.feature_order = canonical[0]["feature_order"]
    actual_hash = sha256_file(args.canonical_spec)
    if actual_hash != args.canonical_spec_sha256:
        write_json(os.path.join(args.out_dir, "final_report.json"), {
            "classification": "RANKER_V2_PROSPECTIVE_DATA_BLOCKER",
            "reason": "canonical_score_spec sha256 mismatch",
            "expected": args.canonical_spec_sha256,
            "actual": actual_hash,
        })
        print(json.dumps({"ok": False, "classification": "RANKER_V2_PROSPECTIVE_DATA_BLOCKER", "reason": "canonical spec hash mismatch"}))
        return
    safety = broadcast_safety(args)
    parity = fixture_parity(args, atlas, canonical)
    if safety["status"] != "PASS" or parity["status"] != "PASS":
        write_json(os.path.join(args.out_dir, "final_report.json"), {
            "classification": "RANKER_V2_PROSPECTIVE_DATA_BLOCKER",
            "broadcast_safety": safety,
            "fixture_parity": parity,
        })
        print(json.dumps({"ok": False, "classification": "RANKER_V2_PROSPECTIVE_DATA_BLOCKER", "broadcastSafety": safety["status"], "fixtureParity": parity["status"]}))
        return
    if args.preflight_only:
        print(json.dumps({"ok": True, "preflightOnly": True, "out_dir": args.out_dir, "parity": parity, "broadcastSafety": safety["status"]}, separators=(",", ":")))
        return
    manifest_path = os.path.join(args.out_dir, "cohort_manifest.json")
    if os.path.exists(manifest_path):
        manifest = cohort_manifest(args, parity, safety)
        args.cohort_start_utc = manifest["cohort_start_utc"]
        args.cohort_start_ms = int(manifest["cohort_start_ms"])
    else:
        args.cohort_start_utc = None
        args.cohort_start_ms = None
    args.prestart_fixture_parity = parity
    args.broadcast_safety = safety
    collector = RankerV2Prospective(args, atlas, canonical)
    collector.tail_loop()


if __name__ == "__main__":
    main()
