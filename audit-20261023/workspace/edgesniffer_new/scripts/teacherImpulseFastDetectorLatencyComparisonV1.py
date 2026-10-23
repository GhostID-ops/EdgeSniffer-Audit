#!/usr/bin/env python3
import argparse
import glob
import json
import math
import os


BASE = "/var/lib/edgesniffer-teacher-impulse-parity-v1"
OLD_BASE = os.path.join(BASE, "research", "top3-flow-prospective-v1")
FAST_BASE = os.path.join(BASE, "research", "fast-flow-detector-v1")
OUT = os.path.join(BASE, "research", "fast-flow-detector-v1", "latency-comparison-report.json")


RULE_MAP = {
    "gross_buy_1s": ("grossbuy_1s_ge5_prospective_v1", "grossbuy_1s_ge5_fastdetector_v1"),
    "gross_buy_2s": ("grossbuy_2s_ge5_prospective_v1", "grossbuy_2s_ge5_fastdetector_v1"),
    "bc50_gross_buy_5s": ("bc50_ge2_grossbuy5s_ge5_prospective_v1", "bc50_ge2_grossbuy5s_ge5_fastdetector_v1"),
}


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


def read_rows(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def total_latencies(rows):
    return [(row.get("latencyComponentsMs") or {}).get("ruleTruthToWouldSendTotal") for row in rows]


def matched_by_mint(old_rows, fast_rows):
    old = {}
    for row in old_rows:
        mint = row.get("mint")
        ts = row.get("ruleTruthAtMs") or row.get("qualificationAtMs")
        if mint and finite(ts):
            old.setdefault(mint, row)
    fast = {}
    for row in fast_rows:
        mint = row.get("mint")
        ts = row.get("ruleTruthAtMs") or row.get("qualificationAtMs")
        if mint and finite(ts):
            fast.setdefault(mint, row)
    shared = sorted(set(old) & set(fast))
    deltas = []
    for mint in shared:
        o = (old[mint].get("latencyComponentsMs") or {}).get("ruleTruthToWouldSendTotal")
        f = (fast[mint].get("latencyComponentsMs") or {}).get("ruleTruthToWouldSendTotal")
        if finite(o) and finite(f):
            deltas.append(o - f)
    return {"sharedMints": len(shared), "oldMinusFastMs": dist(deltas)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-base", default=OLD_BASE)
    parser.add_argument("--fast-base", default=FAST_BASE)
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    report = {"rules": {}, "oldBase": args.old_base, "fastBase": args.fast_base}
    for label, (old_lane, fast_lane) in RULE_MAP.items():
        old_rows = read_rows(os.path.join(args.old_base, old_lane, "qualifications.jsonl"))
        fast_rows = read_rows(os.path.join(args.fast_base, fast_lane, "qualifications.jsonl"))
        report["rules"][label] = {
            "oldLane": old_lane,
            "fastLane": fast_lane,
            "old": {"qualified": len(old_rows), "latency": dist(total_latencies(old_rows))},
            "fast": {"qualified": len(fast_rows), "latency": dist(total_latencies(fast_rows))},
            "matched": matched_by_mint(old_rows, fast_rows),
        }

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    print(json.dumps({"report": args.out, "rules": len(report["rules"])}, indent=2))


if __name__ == "__main__":
    main()
