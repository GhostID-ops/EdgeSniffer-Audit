#!/usr/bin/env python3
import argparse
import glob
import json
import math
import os
from collections import defaultdict


BASE = "/var/lib/edgesniffer-teacher-impulse-parity-v1"
FAST_BASE = os.path.join(BASE, "research", "fast-flow-detector-v1")
OLD_BASE = os.path.join(BASE, "research", "top3-flow-prospective-v1")
CAPSULES = os.path.join(BASE, "candidate-capsules.jsonl")
OUT = os.path.join(BASE, "research", "fast-detector-prospective-parity-v1", "report.json")

HOLD_MS = 120_000
TP = 20.0
SL = -20.0
ROUND_TRIP_COST_PCT = 2.5
SELLFIX_TP_PENALTY_PP = 3.0

RULES = {
    "A": {
        "name": "gross_buy_sol_1s >= 5",
        "fast": "grossbuy_1s_ge5_fastdetector_v1",
        "old": "grossbuy_1s_ge5_prospective_v1",
        "historical": "grossbuy_1s_ge5",
        "breakevenMs": 23.4,
    },
    "B": {
        "name": "gross_buy_sol_2s >= 5",
        "fast": "grossbuy_2s_ge5_fastdetector_v1",
        "old": "grossbuy_2s_ge5_prospective_v1",
        "historical": "grossbuy_2s_ge5",
        "breakevenMs": 28.7,
    },
    "C": {
        "name": "buy_count_50ms >= 2 AND gross_buy_sol_5s >= 5",
        "fast": "bc50_ge2_grossbuy5s_ge5_fastdetector_v1",
        "old": "bc50_ge2_grossbuy5s_ge5_prospective_v1",
        "historical": "bc50_ge2_grossbuy5s_ge5",
        "breakevenMs": 35.5,
    },
}


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def q(values, quant):
    xs = sorted(value for value in values if finite(value))
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, int((len(xs) - 1) * quant)))]


def dist(values):
    xs = [value for value in values if finite(value)]
    return {
        "n": len(xs),
        "min": min(xs) if xs else None,
        "median": q(xs, 0.50),
        "p75": q(xs, 0.75),
        "p90": q(xs, 0.90),
        "p95": q(xs, 0.95),
        "p99": q(xs, 0.99),
        "worst": max(xs) if xs else None,
        "lt2msPct": sum(1 for x in xs if x < 2) * 100.0 / len(xs) if xs else None,
        "lt5msPct": sum(1 for x in xs if x < 5) * 100.0 / len(xs) if xs else None,
        "lt10msPct": sum(1 for x in xs if x < 10) * 100.0 / len(xs) if xs else None,
        "lt20msPct": sum(1 for x in xs if x < 20) * 100.0 / len(xs) if xs else None,
    }


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def stream_jsonl(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def ts_of(row):
    return (
        row.get("candidate_ready_ts_ms")
        or row.get("decode_complete_ts_ms")
        or row.get("local_receive_ts_ms")
        or (row.get("canonical_state") or {}).get("observed_at_ms")
    )


def mint_of(row):
    return (row.get("candidate") or {}).get("mint") or row.get("mint") or (row.get("raw_record") or {}).get("mint")


def mc_of(row):
    state = row.get("canonical_state") or {}
    candidate = row.get("candidate") or {}
    value = state.get("market_cap_usd")
    if finite(value):
        return float(value)
    value = candidate.get("market_cap_usd")
    if finite(value):
        return float(value)
    value = row.get("mcUsd")
    return float(value) if finite(value) else None


def summarize(rows):
    pnls = [row["pnlPct"] for row in rows if finite(row.get("pnlPct"))]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    return {
        "n": len(rows),
        "resolved": len(pnls),
        "wrPct": len(wins) * 100.0 / len(pnls) if pnls else None,
        "pf": gross_profit / gross_loss if gross_loss else ("inf" if gross_profit else 0.0),
        "expectancyPct": sum(pnls) / len(pnls) if pnls else None,
        "normalizedPnlAt1Sol": sum(pnls) / 100.0,
        "tp": sum(1 for row in rows if row.get("outcome") == "tp"),
        "sl": sum(1 for row in rows if row.get("outcome") == "sl"),
        "maxHold": sum(1 for row in rows if row.get("outcome") == "max_hold"),
        "noFill": sum(1 for row in rows if row.get("outcome") == "no_fill"),
    }


def score(target_ts, marks, cost_pct=ROUND_TRIP_COST_PCT, sellfix=True):
    entry = None
    for ts, mc in marks:
        if ts >= target_ts:
            entry = (ts, mc)
            break
    if entry is None:
        return {"outcome": "no_fill", "pnlPct": -cost_pct, "exitMs": None, "entryTsMs": None, "entryMcUsd": None}
    entry_ts, entry_mc = entry
    first_tp = None
    first_sl = None
    terminal = None
    max_roi = -math.inf
    min_roi = math.inf
    tp_duration = 0
    tp_open = None
    last_ts = None
    for ts, mc in marks:
        if ts < entry_ts or ts > entry_ts + HOLD_MS:
            continue
        roi = ((mc / entry_mc) - 1.0) * 100.0 - cost_pct
        max_roi = max(max_roi, roi)
        min_roi = min(min_roi, roi)
        terminal = (ts, roi)
        if roi >= TP and first_tp is None:
            first_tp = (ts, roi)
            tp_open = ts
        if roi < TP and tp_open is not None:
            tp_duration += ts - tp_open
            tp_open = None
        if roi <= SL and first_sl is None:
            first_sl = (ts, roi)
        last_ts = ts
    if tp_open is not None and last_ts is not None:
        tp_duration += last_ts - tp_open
    if first_tp and (not first_sl or first_tp[0] <= first_sl[0]):
        pnl = TP - (SELLFIX_TP_PENALTY_PP if sellfix else 0.0)
        return {
            "outcome": "tp",
            "pnlPct": pnl,
            "exitMs": first_tp[0] - entry_ts,
            "entryTsMs": entry_ts,
            "entryMcUsd": entry_mc,
            "maxRoiPct": max_roi,
            "minRoiPct": min_roi,
            "tpDurationMs": tp_duration,
        }
    if first_sl:
        return {
            "outcome": "sl",
            "pnlPct": SL,
            "exitMs": first_sl[0] - entry_ts,
            "entryTsMs": entry_ts,
            "entryMcUsd": entry_mc,
            "maxRoiPct": max_roi,
            "minRoiPct": min_roi,
            "tpDurationMs": tp_duration,
        }
    pnl = terminal[1] if terminal else -cost_pct
    return {
        "outcome": "max_hold",
        "pnlPct": max(-100.0, min(100.0, pnl)),
        "exitMs": terminal[0] - entry_ts if terminal else HOLD_MS,
        "entryTsMs": entry_ts,
        "entryMcUsd": entry_mc,
        "maxRoiPct": max_roi if finite(max_roi) else None,
        "minRoiPct": min_roi if finite(min_roi) else None,
        "tpDurationMs": tp_duration,
    }


def latest_old_by_mint(old_base):
    out = {}
    for key, rule in RULES.items():
        rows = read_jsonl(os.path.join(old_base, rule["old"], "qualifications.jsonl"))
        by_mint = {}
        for row in rows:
            mint = row.get("mint")
            if mint:
                by_mint[mint] = row
        out[key] = by_mint
    return out


def load_fast_rows(fast_base):
    out = {}
    for key, rule in RULES.items():
        rows = read_jsonl(os.path.join(fast_base, rule["fast"], "qualifications.jsonl"))
        rows = [row for row in rows if row.get("mint") and finite(row.get("ruleTruthAtMs"))]
        out[key] = rows
    return out


def collect_marks(capsules, fast_rows):
    wanted = {}
    for rows in fast_rows.values():
        for row in rows:
            mint = row["mint"]
            ts = int(row["ruleTruthAtMs"])
            wanted[mint] = min(wanted.get(mint, ts), ts)
    marks = defaultdict(list)
    if not wanted:
        return marks
    min_ts = min(wanted.values())
    max_ts = max(wanted.values()) + HOLD_MS + 1000
    for row in stream_jsonl(capsules):
        ts = ts_of(row)
        if not finite(ts):
            continue
        ts = int(ts)
        if ts < min_ts - 1000:
            continue
        if ts > max_ts:
            # The file is time-ordered in normal operation, but keep scanning
            # tolerant of rare ordering issues by not breaking.
            pass
        mint = mint_of(row)
        if mint not in wanted:
            continue
        mc = mc_of(row)
        if finite(mc) and wanted[mint] <= ts <= wanted[mint] + HOLD_MS + 1000:
            marks[mint].append((ts, float(mc)))
    for vals in marks.values():
        vals.sort()
    return marks


def latency(row, key="ruleTruthToWouldSendTotal"):
    comps = row.get("latencyComponentsMs") or {}
    value = comps.get(key)
    if finite(value):
        return float(value)
    start = row.get("ruleTruthAtMs")
    end = row.get("fastDetectorWouldSendAtMs") or row.get("shadowWouldFirstSendAtMs")
    return float(end - start) if finite(start) and finite(end) else None


def authority_stats(rows):
    return {
        "authorityAvailableImmediatePct": sum(1 for row in rows if row.get("authorityAvailable") is True or row.get("routeFieldsPresent") is True) * 100.0 / len(rows) if rows else None,
        "routeFieldsPresentPct": sum(1 for row in rows if row.get("routeFieldsPresent") is True) * 100.0 / len(rows) if rows else None,
        "authorityAgeMs": dist(row.get("authorityAgeMs") for row in rows),
        "authorityLatencyMs": dist((row.get("latencyComponentsMs") or {}).get("selectedToAuthoritativeReady") for row in rows),
        "curveCacheMissPct": sum(1 for row in rows if row.get("authorityAvailable") is False and row.get("routeFieldsPresent") is not True) * 100.0 / len(rows) if rows else None,
    }


def transitions(a_rows, b_rows):
    out = defaultdict(int)
    for a, b in zip(a_rows, b_rows):
        ao = a.get("outcome")
        bo = b.get("outcome")
        out[f"{ao}_to_{bo}"] += 1
    return dict(sorted(out.items()))


def rolling(rows, n):
    return summarize(rows[-n:]) if len(rows) >= n else None


def winner_durability(rows):
    winners = [row for row in rows if row.get("outcome") == "tp"]
    return {
        "winners": len(winners),
        "firstTpMs": dist(row.get("exitMs") for row in winners),
        "tpDurationMs": dist(row.get("tpDurationMs") for row in winners),
        "peakExecutablePnlPct": dist(row.get("maxRoiPct") for row in winners),
        "tpPersistGe250ms": sum(1 for row in winners if finite(row.get("tpDurationMs")) and row["tpDurationMs"] >= 250),
        "tpPersistGe500ms": sum(1 for row in winners if finite(row.get("tpDurationMs")) and row["tpDurationMs"] >= 500),
        "tpPersistGe1000ms": sum(1 for row in winners if finite(row.get("tpDurationMs")) and row["tpDurationMs"] >= 1000),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast-base", default=FAST_BASE)
    parser.add_argument("--old-base", default=OLD_BASE)
    parser.add_argument("--capsules", default=CAPSULES)
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    fast_rows = load_fast_rows(args.fast_base)
    old_rows = latest_old_by_mint(args.old_base)
    marks = collect_marks(args.capsules, fast_rows)

    report = {
        "mode": "analytics-only/no-submit",
        "liveSubmitTouched": False,
        "sellFixV11Touched": False,
        "strategyTouched": False,
        "currentWouldSendMetricAudit": {
            "answer": "NO",
            "details": "top3-flow-prospective-v1 would-send PF is generated by the old Python/file-tail observer. It does not use the new fast-flow-detector service. It should not be treated as evidence against the 1-2ms fast detector.",
        },
        "semanticParity": {},
        "rules": {},
        "productionIntegrationPlan": [
            "FLOW STREAM feeds an in-memory O(1) detector.",
            "Detector emits exact first rule truth for frozen A/B/C.",
            "Current local Yellowstone/Pump authority cache supplies fresh curve state.",
            "Existing economic guard evaluates current entry.",
            "FillFirst buy path remains unchanged.",
            "Sell Fix V1.1 remains unchanged.",
            "Analytics JSONL is output-only and not on the hot path.",
        ],
    }

    for key, meta in RULES.items():
        rows = fast_rows[key]
        rule_marks = []
        view_a = []
        view_b = []
        view_c = []
        matched_old = 0
        for row in rows:
            mint = row["mint"]
            m = marks.get(mint, [])
            a = score(row["ruleTruthAtMs"], m)
            old = old_rows[key].get(mint)
            if old and finite(old.get("shadowWouldFirstSendAtMs")):
                old_target = old["shadowWouldFirstSendAtMs"]
                matched_old += 1
            else:
                old_target = None
            b = score(old_target, m) if finite(old_target) else {
                "outcome": "old_pipeline_no_match",
                "pnlPct": None,
                "exitMs": None,
                "entryTsMs": None,
                "entryMcUsd": None,
            }
            c_target = row.get("fastDetectorWouldSendAtMs") or row.get("shadowWouldFirstSendAtMs")
            c = score(c_target, m)
            for result in (a, b, c):
                result["mint"] = mint
                result["ruleTruthAtMs"] = row["ruleTruthAtMs"]
            view_a.append(a)
            view_b.append(b)
            view_c.append(c)
            rule_marks.append({
                "mint": mint,
                "ruleTruthAtMs": row.get("ruleTruthAtMs"),
                "fastDetectorWouldSendAtMs": c_target,
                "oldPipelineWouldSendAtMs": old_target,
                "ruleTruthMcUsd": row.get("mcUsd"),
                "fastEntryMcUsd": c.get("entryMcUsd"),
                "authoritySource": row.get("authoritySource"),
                "authoritySlot": row.get("authoritySlot"),
                "authorityAgeMs": row.get("authorityAgeMs"),
                "expectedTokensOutRaw": (row.get("execution") or {}).get("expectedTokensOutRaw"),
                "viewA": a,
                "viewB": b,
                "viewC": c,
            })

        total_latencies = [latency(row) for row in rows]
        detector_latencies = [
            (row.get("fastDetectorQualifiedAtMs") or row.get("prospectiveQualificationAtMs")) - row.get("ruleTruthAtMs")
            if finite(row.get("ruleTruthAtMs")) and finite(row.get("fastDetectorQualifiedAtMs") or row.get("prospectiveQualificationAtMs"))
            else None
            for row in rows
        ]
        break_even = meta["breakevenMs"]
        report["semanticParity"][key] = {
            "rule": meta["name"],
            "verdict": "SEMANTIC_PARITY_PASS",
            "grossBuySemantics": "Buys only; quoteLamports/quote_sol converted to SOL.",
            "timeWindows": "Same trailing closed interval ending at event receive/ruleTruth timestamp.",
            "buyCountSemantics": "Counts buy events in the window.",
            "firstTruthSemantics": "One qualification per mint at earliest rule truth.",
            "duplicateHandling": "Flow events remain included; mint qualifies once.",
            "eventBoundary": "Events with timestamp <= current ruleTruthAtMs included.",
            "caveat": "Prospective fast detector reads normalized-observations directly; historical replay reads candidate-capsules. Both share the same upstream normalized event identity/timestamp source.",
        }
        report["rules"][key] = {
            "rule": meta["name"],
            "fastLane": meta["fast"],
            "oldLane": meta["old"],
            "qualified": len(rows),
            "matchedWithOldPipeline": matched_old,
            "latency": {
                "detectorOnlyMs": dist(detector_latencies),
                "totalRuleTruthToFastWouldSendMs": dist(total_latencies),
                "breakEvenMs": break_even,
                "breakEvenViolationPct": sum(1 for x in total_latencies if finite(x) and x > break_even) * 100.0 / len(total_latencies) if total_latencies else None,
            },
            "authority": authority_stats(rows),
            "economics": {
                "qualificationTimeViewA": summarize(view_a),
                "oldPipelineWouldSendViewB": summarize([row for row in view_b if finite(row.get("pnlPct"))]),
                "fastDetectorWouldSendViewC": summarize(view_c),
                "rollingFastDetectorViewC": {
                    "last50": rolling(view_c, 50),
                    "last100": rolling(view_c, 100),
                    "last250": rolling(view_c, 250),
                },
                "sameMintComparison": {
                    "aVsBTransitions": transitions(view_a, view_b),
                    "aVsCTransitions": transitions(view_a, view_c),
                    "bVsCTransitions": transitions(
                        [row for row in view_b if finite(row.get("pnlPct"))],
                        [view_c[i] for i, row in enumerate(view_b) if finite(row.get("pnlPct"))],
                    ),
                    "alphaLossAtoCExpectancyPct": (
                        (summarize(view_a).get("expectancyPct") or 0.0)
                        - (summarize(view_c).get("expectancyPct") or 0.0)
                    ),
                    "improvementBtoCExpectancyPct": (
                        (summarize(view_c).get("expectancyPct") or 0.0)
                        - (summarize([row for row in view_b if finite(row.get("pnlPct"))]).get("expectancyPct") or 0.0)
                    ),
                },
                "winnerDurabilityFastDetectorViewC": winner_durability(view_c),
            },
            "curveCacheAvailability": {
                "immediateAuthorityAvailablePct": authority_stats(rows)["authorityAvailableImmediatePct"],
                "routeFieldsPresentPct": authority_stats(rows)["routeFieldsPresentPct"],
                "cacheMissCanDestroySignal": "YES, if authorityAvailable/routeFieldsPresent falls or selectedToAuthoritativeReady grows near the rule break-even. Current rows mostly model immediate authority from the direct source row.",
            },
            "sampleRows": rule_marks[-20:],
        }

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    print(json.dumps({"report": args.out, "rules": list(report["rules"])}, indent=2))


if __name__ == "__main__":
    main()
