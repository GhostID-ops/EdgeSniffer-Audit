#!/usr/bin/env python3
import argparse
import glob
import json
import math
import os
from collections import Counter


BASE = "/var/lib/edgesniffer-teacher-impulse-parity-v1"
PROSPECTIVE_BASE = os.path.join(BASE, "research", "top3-flow-prospective-v1")
OUT = os.path.join(BASE, "research", "signal-pipeline-forensics-v1", "report.json")


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def q(values, pct):
    xs = sorted(value for value in values if finite(value))
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, int((len(xs) - 1) * pct)))]


def dist(values):
    xs = [value for value in values if finite(value)]
    return {
        "n": len(xs),
        "min": min(xs) if xs else None,
        "median": q(xs, 0.50),
        "p75": q(xs, 0.75),
        "p90": q(xs, 0.90),
        "p95": q(xs, 0.95),
        "worst": max(xs) if xs else None,
    }


def read_jsonl(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def lane_rows(base):
    rows = {}
    for lane_dir in sorted(glob.glob(os.path.join(base, "*_prospective_v1"))):
        lane = os.path.basename(lane_dir)
        rows[lane] = list(read_jsonl(os.path.join(lane_dir, "qualifications.jsonl")) or [])
    return rows


def component_summary(rows):
    components = [
        "ruleTruthToFeatureReady",
        "featureReadyToQualification",
        "qualificationToSelected",
        "selectedToAuthoritativeReady",
        "authoritativeReadyToWouldSend",
        "ruleTruthToWouldSendTotal",
    ]
    out = {}
    for component in components:
        out[component] = dist((row.get("latencyComponentsMs") or {}).get(component) for row in rows)
    # Additional fields requested by the audit. Some collapse to zero because the
    # current observer is downstream of the capsule file, not upstream gRPC.
    out["sourceEventToLocalObservation"] = dist(
        (row.get("flowEventObservedAtMs") - row.get("ruleTruthAtMs"))
        if finite(row.get("flowEventObservedAtMs")) and finite(row.get("ruleTruthAtMs"))
        else None
        for row in rows
    )
    out["localObservationToRollingFeatureUpdate"] = {
        "n": len(rows),
        "note": "Current Python observer updates rolling state synchronously while reading each candidate-capsule row; no separate timestamp exists.",
    }
    out["featureUpdateToRuleTruthDetection"] = {
        "n": len(rows),
        "note": "Rule truth is evaluated synchronously in maybe_qualify() after rolling update; no separate timestamp exists.",
    }
    return out


def dominant_components(summary):
    ranked = []
    for name, stats in summary.items():
        if not isinstance(stats, dict) or not finite(stats.get("median")):
            continue
        if name == "ruleTruthToWouldSendTotal":
            continue
        ranked.append({
            "component": name,
            "median": stats.get("median"),
            "p90": stats.get("p90"),
            "p95": stats.get("p95"),
        })
    ranked.sort(key=lambda row: ((row["p90"] or 0), (row["median"] or 0)), reverse=True)
    return ranked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=PROSPECTIVE_BASE)
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    rows_by_lane = lane_rows(args.base)
    report = {
        "clockSemantics": {
            "verdict": "CLOCKS_COMPARABLE_WITH_CAVEAT",
            "caveat": "All recorded timestamps are Unix epoch milliseconds, but ruleTruthAtMs in this file-based observer is candidate_ready_ts_ms from the capsule, not the original exchange/on-chain occurrence time. It is compatible for pipeline measurement after the source feed, but not a pure network-delivery measurement.",
            "fields": {
                "ruleTruthAtMs": "candidate_ready_ts_ms/local receive epoch ms from candidate-capsule row; generated upstream by source feed/capsule pipeline.",
                "flowEventObservedAtMs": "local_receive_ts_ms or decode_complete_ts_ms epoch ms from candidate-capsule row.",
                "flowFeatureReadyAtMs": "candidate_ready_ts_ms/decode_complete_ts_ms epoch ms; feature update happens synchronously after Python file read.",
                "prospectiveQualificationAtMs": "Python observer wall-clock epoch ms at qualification write time.",
                "candidateSelectedAtMs": "same as prospectiveQualificationAtMs in no-submit observer.",
                "authoritativeStateReadyAtMs": "canonical_state.observed_at_ms if present; otherwise feature-ready timestamp.",
                "shadowWouldFirstSendAtMs": "max(candidateSelectedAtMs, authoritativeStateReadyAtMs) + deterministic empirical latency sample.",
            },
        },
        "implementationForensics": {
            "currentObserverMode": "file-tail polling",
            "capsuleEmitterPollMs": 25,
            "prospectiveObserverPollMs": 25,
            "rollingWindowComplexity": "Per event, per mint deque is retained, but each window call filters/scans that mint deque for 25/50/75/100/150/250/500/1000/2000/5000ms windows. This is per-mint small-window O(k), not O(1).",
            "dominantExpectedCause": "Two file-tail polling stages plus Python scheduling/JSON serialization dominate the measured 30-40ms.",
        },
        "lanes": {},
    }
    for lane, rows in rows_by_lane.items():
        summary = component_summary(rows)
        report["lanes"][lane] = {
            "qualified": len(rows),
            "components": summary,
            "dominantComponents": dominant_components(summary),
            "pollingVsEventDriven": "periodic file-tail polling; rules are evaluated per row only after the capsule file is observed by Python.",
        }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    print(json.dumps({"report": args.out, "lanes": len(report["lanes"])}, indent=2))


if __name__ == "__main__":
    main()
