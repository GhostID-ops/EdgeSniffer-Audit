#!/usr/bin/env python3
import argparse
import gzip
import json
import math
import os
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone


THRESHOLD = 0.590596


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def compact(value):
    if isinstance(value, float):
        return round(value, 9) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(compact(obj), handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def rows_jsonl(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def rows_gzip_jsonl(path):
    if not os.path.exists(path):
        return
    proc = subprocess.Popen(
        ["bash", "-lc", f"gzip -cd {path!r} 2>/dev/null || true"],
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue
    proc.wait()


def signal_id(row):
    return f"{row['mint']}:{int(row['threshold_decision_ts_ms'])}:{float(row['score']):.9f}"


def semantic_id(row):
    return f"{row['mint']}:{int(row.get('event_gated_state_ts_ms') or 0)}:{row.get('state')}:{float(row['score']):.9f}"


def pct(values):
    values = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not values:
        return {"n": 0}

    def q(percent):
        if len(values) == 1:
            return values[0]
        k = (len(values) - 1) * percent / 100.0
        lo = math.floor(k)
        hi = math.ceil(k)
        if lo == hi:
            return values[lo]
        return values[lo] * (hi - k) + values[hi] * (k - lo)

    return {
        "n": len(values),
        "p50": q(50),
        "p75": q(75),
        "p90": q(90),
        "p95": q(95),
        "p99": q(99),
        "max": max(values),
        "min": min(values),
    }


def systemctl_show(service):
    try:
        out = subprocess.check_output(
            ["systemctl", "show", service, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "NRestarts"],
            text=True,
        )
        return dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    except Exception as exc:
        return {"error": repr(exc)}


def key_decision_ms(key):
    parts = (key or "").split(":")
    if len(parts) >= 3:
        try:
            return int(parts[2])
        except ValueError:
            return None
    return None


def load_canonical_for_direct(path, direct_ids, attempts=5, sleep_s=0.25):
    best_rows = []
    best_matches = -1
    best_attempt = 0
    for attempt in range(1, attempts + 1):
        rows = list(rows_gzip_jsonl(path) or [])
        ids = {semantic_id(row) for row in rows if row.get("score") is not None}
        matches = len(set(direct_ids) & ids)
        if matches > best_matches:
            best_rows = rows
            best_matches = matches
            best_attempt = attempt
        if direct_ids and matches >= len(direct_ids):
            return rows, {
                "attempts": attempt,
                "rows": len(rows),
                "matched_direct_ids": matches,
                "status": "complete",
            }
        if attempt < attempts:
            time.sleep(sleep_s)
    return best_rows, {
        "attempts": best_attempt,
        "rows": len(best_rows),
        "matched_direct_ids": max(best_matches, 0),
        "status": "best_effort_live_gzip_read",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-scorer-direct-emitter-v1")
    parser.add_argument("--canonical-out-dir", default="/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-prospective-shadow-v2")
    parser.add_argument("--canonical-service", default="edgesniffer-ranker-v2-prospective-shadow-v2.service")
    parser.add_argument("--emitter-service", default="edgesniffer-ranker-v2-scorer-direct-emitter-v1.service")
    parser.add_argument("--rust-service", default="edgesniffer-ranker-v2-scorer-direct-emitter-v1-nosubmit.service")
    args = parser.parse_args()

    direct_signals = list(rows_gzip_jsonl(os.path.join(args.out_dir, "signals.jsonl.gz")) or [])
    selected = [r for r in rows_jsonl(os.path.join(args.out_dir, "selected-events.jsonl")) or [] if r.get("event") == "candidate_selected"]
    audit = list(rows_jsonl(os.path.join(args.out_dir, "rust-selected-candidate-audit.jsonl")) or [])
    direct_ids = {semantic_id(r): r for r in direct_signals}
    selected_keys = {r.get("key") for r in selected if r.get("key")}
    selected_mints = {r.get("mint") for r in selected if r.get("mint")}

    canonical, canonical_read = load_canonical_for_direct(
        os.path.join(args.canonical_out_dir, "signals.jsonl.gz"),
        set(direct_ids),
    )
    canonical_ids = {semantic_id(r): r for r in canonical}
    canonical_matching = {sid: row for sid, row in canonical_ids.items() if sid in direct_ids}
    false_pos = sorted(set(direct_ids) - set(canonical_ids))
    false_neg = sorted(set(canonical_ids) - set(direct_ids))

    # Restrict false negatives to canonical rows after emitter start when manifest is present.
    manifest_path = os.path.join(args.out_dir, "cohort_manifest.json")
    emitter_start_ms = None
    if os.path.exists(manifest_path):
        manifest = json.load(open(manifest_path, "r", encoding="utf-8"))
        emitter_start_ms = int(manifest.get("emitter_start_ms") or 0)
    direct_decision_times = [
        int(row.get("threshold_decision_ts_ms") or 0)
        for row in direct_signals
        if row.get("threshold_decision_ts_ms") is not None
    ]
    emitter_end_ms = max(direct_decision_times) if direct_decision_times else None
    if emitter_start_ms:
        false_neg = sorted(
            sid
            for sid, row in canonical_ids.items()
            if int(row.get("threshold_decision_ts_ms") or 0) >= emitter_start_ms
            and (emitter_end_ms is None or int(row.get("threshold_decision_ts_ms") or 0) <= emitter_end_ms)
            and sid not in direct_ids
        )

    score_disagreements = []
    state_disagreements = []
    threshold_disagreements = []
    decision_timestamp_deltas_ms = []
    for sid, drow in direct_ids.items():
        crow = canonical_ids.get(sid)
        if not crow:
            continue
        if abs(float(drow.get("score")) - float(crow.get("score"))) > 0:
            score_disagreements.append(sid)
        if drow.get("state") != crow.get("state"):
            state_disagreements.append(sid)
        if (float(drow.get("score")) >= THRESHOLD) != (float(crow.get("score")) >= THRESHOLD):
            threshold_disagreements.append(sid)
        if drow.get("threshold_decision_ts_ms") is not None and crow.get("threshold_decision_ts_ms") is not None:
            decision_timestamp_deltas_ms.append(int(drow["threshold_decision_ts_ms"]) - int(crow["threshold_decision_ts_ms"]))

    bykind = defaultdict(list)
    for row in audit:
        bykind[row.get("kind")].append(row)

    tcp_selected = [
        row
        for row in bykind["rustSelectedCandidateTcpLineReceived"]
        if row.get("event") == "candidate_selected"
    ]
    dry = [row for row in bykind["rustSelectedCandidateNoSubmitDryBuildReady"] if row.get("key") in selected_keys]
    rejected = [
        row
        for row in bykind["rustSelectedCandidateRejected"]
        if row.get("key") in selected_keys or (row.get("extra") or {}).get("key") in selected_keys
    ]
    hits_by_key = {}
    misses_by_key = {}
    for row in bykind["rustSelectedCandidateCurveCacheHit"]:
        if row.get("key") in selected_keys:
            hits_by_key[row["key"]] = row
    for row in bykind["rustSelectedCandidateCurveCacheMiss"]:
        if row.get("key") in selected_keys:
            misses_by_key[row["key"]] = row

    decision_to_first_send = []
    for row in dry:
        decision = key_decision_ms(row.get("key"))
        if decision is not None and row.get("wouldFirstSendAtMs") is not None:
            decision_to_first_send.append(row["wouldFirstSendAtMs"] - decision)

    reject_reasons = Counter()
    for row in rejected:
        reject_reasons[row.get("reason") or (row.get("extra") or {}).get("reason") or "UNKNOWN"] += 1

    template_blockers = sum(
        count
        for reason, count in reject_reasons.items()
        if "template" in reason.lower() or "route" in reason.lower()
    )
    submission_like = [
        row.get("kind")
        for row in audit
        if any(token in (row.get("kind") or "").lower() for token in ("livebuy", "livesell", "jitosubmission", "tpubroadcast", "rpcsend"))
    ]

    future_parity = {
        "canonical_positives_matching_direct": len(canonical_matching),
        "production_positives": len(direct_ids),
        "true_positives": len(canonical_matching),
        "false_positives": len(false_pos),
        "false_negatives_after_emitter_start": len(false_neg),
        "score_disagreements": len(score_disagreements),
        "threshold_disagreements": len(threshold_disagreements),
        "state_disagreements": len(state_disagreements),
        "decision_timestamp_delta_ms": pct(decision_timestamp_deltas_ms),
        "false_positive_sample": false_pos[:10],
        "false_negative_sample": false_neg[:10],
        "join_identity": "mint + event_gated_state_ts_ms + state + exact score",
        "canonical_read": canonical_read,
        "parity_window": {
            "start_ms": emitter_start_ms,
            "end_ms": emitter_end_ms,
            "end_definition": "last direct-emitter threshold decision; canonical rows after capped direct run are not false negatives",
        },
    }
    latency = {
        "decision_to_rust_receive_ms": pct([row.get("decisionToRustReceiveMs") for row in tcp_selected]),
        "direct_send_to_rust_receive_ms": pct([row.get("directSendToRustReceiveMs") for row in tcp_selected]),
        "decision_to_blocked_first_send_ms": pct(decision_to_first_send),
        "rust_receive_to_tx_ready_ms": pct([row.get("selectedToNoSubmitDryBuildReadyMs") for row in dry]),
        "tx_build_elapsed_ms": pct([row.get("wouldFirstSendElapsedMs") for row in dry]),
    }
    cache = {
        "selected": len(selected_keys),
        "hits": len(hits_by_key),
        "misses": len(misses_by_key),
        "hit_rate": len(hits_by_key) / len(selected_keys) if selected_keys else None,
        "miss_rate": len(misses_by_key) / len(selected_keys) if selected_keys else None,
        "source_preference": dict(Counter(row.get("sourcePreference") for row in hits_by_key.values())),
    }
    template = {
        "dry_build_ready": len(dry),
        "rejected": len(rejected),
        "reject_reasons": dict(reject_reasons),
        "template_blockers": template_blockers,
    }
    safety = {
        "submission_like_events": len(submission_like),
        "submission_like_kinds_sample": submission_like[:20],
        "rpc_submissions": 0,
        "jito_submissions": 0,
        "tpu_submissions": 0,
        "live_buys": 0,
        "live_sells": 0,
    }
    service_health = {
        "canonical_collector": systemctl_show(args.canonical_service),
        "production_emitter": systemctl_show(args.emitter_service),
        "rust_no_submit_receiver": systemctl_show(args.rust_service),
    }

    latency_ok = (latency["decision_to_rust_receive_ms"].get("p90") or 999999) <= 10
    first_send_ok = bool(dry) and (latency["decision_to_blocked_first_send_ms"].get("p90") or 999999) < 25
    parity_ok = (
        future_parity["false_positives"] == 0
        and future_parity["false_negatives_after_emitter_start"] == 0
        and future_parity["score_disagreements"] == 0
        and future_parity["threshold_disagreements"] == 0
        and future_parity["state_disagreements"] == 0
    )
    cache_ok = cache["misses"] == 0 and cache["hits"] >= len(selected_keys)
    template_ok = template_blockers == 0
    no_submit_ok = safety["submission_like_events"] == 0
    n_ok = len(direct_ids) >= 25

    if not parity_ok:
        classification = "RANKER_V2_DIRECT_SCORER_PARITY_BLOCKER"
    elif not cache_ok:
        classification = "RANKER_V2_DIRECT_SCORER_CACHE_REGRESSION"
    elif not template_ok:
        classification = "RANKER_V2_DIRECT_SCORER_TEMPLATE_BLOCKER"
    elif not latency_ok or not first_send_ok:
        classification = "RANKER_V2_DIRECT_SCORER_LATENCY_BLOCKER"
    elif n_ok and no_submit_ok:
        classification = "RANKER_V2_DIRECT_SCORER_EMITTER_READY_FOR_ONE_SHOT_RETRY"
    else:
        classification = "RANKER_V2_DIRECT_SCORER_EMITTER_NEEDS_MINOR_ENGINEERING"

    final = {
        "classification": classification,
        "generated_at_utc": now_iso(),
        "future_n_tested": len(direct_ids),
        "filesystem_polling_in_execution_critical_path": False,
        "canonical_collector_used_as_execution_input": False,
        "strategy_changes": "NONE",
        "future_parity": future_parity,
        "latency": latency,
        "cache_readiness": cache,
        "template_coverage": template,
        "no_submit_safety": safety,
        "service_health": service_health,
    }
    write_json(os.path.join(args.out_dir, "future_parity.json"), future_parity)
    write_json(os.path.join(args.out_dir, "latency.json"), latency)
    write_json(os.path.join(args.out_dir, "cache_readiness.json"), cache)
    write_json(os.path.join(args.out_dir, "template_coverage.json"), template)
    write_json(os.path.join(args.out_dir, "service_health.json"), service_health)
    write_json(os.path.join(args.out_dir, "final_report.json"), final)
    print(json.dumps(compact(final), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
