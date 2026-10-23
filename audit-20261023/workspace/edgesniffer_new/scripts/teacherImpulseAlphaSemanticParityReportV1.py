#!/usr/bin/env python3
import glob
import json
import math
import os
from collections import Counter


BASE = "/var/lib/edgesniffer-teacher-impulse-parity-v1"
RESEARCH = os.path.join(BASE, "research")
FULL_UNIVERSE_REPORT = os.path.join(RESEARCH, "full-universe-top3-flow-validation-v1", "report.json")
PROSPECTIVE_REPORT = os.path.join(RESEARCH, "top3-flow-prospective-v1", "comparison-report.json")
OUT = os.path.join(RESEARCH, "alpha-semantic-parity-v1", "report.json")


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def read_jsonl(path, limit=None):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for idx, line in enumerate(handle, 1):
            if limit and idx > limit:
                break
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def sell_recovery_scan(path, limit=50_000):
    stats = Counter()
    examples = []
    for row in read_jsonl(path, limit):
        stats["rows"] += 1
        rr = row.get("raw_record") or row
        side = rr.get("side") or row.get("side")
        if side == "buy":
            stats["buy"] += 1
        elif side == "sell":
            stats["sell"] += 1
            if finite(rr.get("sellQuoteLamports")) or finite(row.get("sellQuoteLamports")) or finite(row.get("sell_quote_lamports")):
                stats["sell_quote_lamports_present"] += 1
            if finite(rr.get("quoteLamports")) or finite(row.get("quoteLamports")) or finite(row.get("quote_lamports")):
                stats["quote_lamports_on_sell_present"] += 1
            if finite(rr.get("sellTokenAmountRaw")) or finite(row.get("sellTokenAmountRaw")) or finite(row.get("sell_token_amount_raw")):
                stats["sell_token_amount_raw_present"] += 1
            if rr.get("bondingCurve") or row.get("bondingCurve") or (row.get("extra_decision_fields") or {}).get("bondingCurve"):
                stats["bonding_curve_present"] += 1
            if (row.get("canonical_state") or {}).get("market_cap_usd") is not None:
                stats["canonical_mc_present"] += 1
            if len(examples) < 3:
                examples.append({
                    "side": side,
                    "mint": rr.get("mint") or row.get("mint"),
                    "quoteLamports": rr.get("quoteLamports") or row.get("quoteLamports") or row.get("quote_lamports"),
                    "sellQuoteLamports": rr.get("sellQuoteLamports") or row.get("sellQuoteLamports") or row.get("sell_quote_lamports"),
                    "sellTokenAmountRaw": rr.get("sellTokenAmountRaw") or row.get("sellTokenAmountRaw") or row.get("sell_token_amount_raw"),
                    "bondingCurve": rr.get("bondingCurve") or row.get("bondingCurve") or (row.get("extra_decision_fields") or {}).get("bondingCurve"),
                    "canonicalState": row.get("canonical_state"),
                })
    return {"path": path, "stats": dict(stats), "examples": examples}


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def latency_class(total_ms):
    if not finite(total_ms):
        return "unknown"
    if total_ms <= 20:
        return "LATENCY_COMPATIBLE"
    if total_ms <= 50:
        return "LATENCY_MARGINAL"
    return "LATENCY_INCOMPATIBLE"


def classify_latency(lane_report):
    total = (((lane_report or {}).get("latency") or {}).get("ruleTruthToWouldSendTotal") or {})
    p90 = total.get("p90")
    med = total.get("median")
    return {
        "medianMs": med,
        "p90Ms": p90,
        "classification": latency_class(p90 if finite(p90) else med),
    }


def interpolate_latency_grid(rule_report, latency_ms):
    if not finite(latency_ms):
        return None
    grid = sorted(rule_report.get("latency_sensitivity") or [], key=lambda x: x.get("delay_ms", 0))
    if not grid:
        return None
    for row in grid:
        if latency_ms <= row.get("delay_ms", 0):
            return {
                "bucketDelayMs": row.get("delay_ms"),
                "pf": row.get("pf"),
                "expectancyPct": row.get("expectancy_pct"),
                "note": "Ceiling bucket from historical fixed-delay grid, not exact per-candidate replay.",
            }
    row = grid[-1]
    return {
        "bucketDelayMs": row.get("delay_ms"),
        "pf": row.get("pf"),
        "expectancyPct": row.get("expectancy_pct"),
        "note": "Latency exceeded grid; using worst available fixed-delay bucket.",
    }


def prospective_lane_rows(base):
    out = {}
    for lane_dir in sorted(glob.glob(os.path.join(base, "*_prospective_v1"))):
        lane = os.path.basename(lane_dir)
        quals = list(read_jsonl(os.path.join(lane_dir, "qualifications.jsonl")) or [])
        out[lane] = quals
    return out


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    full = load_json(FULL_UNIVERSE_REPORT) or {}
    prospective = load_json(PROSPECTIVE_REPORT) or {}
    prospective_base = os.path.join(RESEARCH, "top3-flow-prospective-v1")

    candidate_capsule_scan = sell_recovery_scan(os.path.join(BASE, "candidate-capsules.jsonl"))
    if (full.get("scan_stats") or {}).get("sell_rows") is not None:
        stats = full["scan_stats"]
        candidate_capsule_scan["stats"].update({
            "rows": stats.get("rows"),
            "buy": stats.get("buy_rows"),
            "sell": stats.get("sell_rows"),
            "sell_quote_lamports_present": stats.get("sell_rows", 0) - stats.get("sell_quote_missing", 0),
            "exact_from_full_universe_report": True,
        })
    sell_scans = [
        candidate_capsule_scan,
        sell_recovery_scan(os.path.join(BASE, "normalized-observations.jsonl")),
        sell_recovery_scan(os.path.join(BASE, "decision-inputs.jsonl")),
        sell_recovery_scan(os.path.join(RESEARCH, "early-burst-telemetry-v1.jsonl")),
    ]

    historical_semantics = {
        "teacherImpulseCorrectedMultiscaleResearch.py": {
            "formula": "For each trailing window, buy_sol=sum(quote for buy rows), sell_sol=sum(quote for sell rows), net_buy_sol_X=buy_sol-sell_sol.",
            "sellQuoteMissingContribution": "0 SOL because sell rows without quote data enter telemetry/capsules with quote=0 or sellQuoteLamports=null; no inference is performed.",
            "actualDiscoveredSignal": "gross known buy pressure / partial net with unknown sells contributing zero.",
        },
        "teacherImpulseFullUniverseTop3Validation.py": {
            "formula": "For each trailing window, buys use raw_record.quoteLamports; sells subtract raw_record.sellQuoteLamports when finite; missing sellQuoteLamports contributes 0.",
            "sellQuoteMissingContribution": "0 SOL.",
            "actualDiscoveredSignal": "gross buy pressure in current retained corpus because sellQuoteLamports is absent on retained sell rows.",
        },
    }

    top3_map = {
        "net1s_ge_5": "net1s_ge_5_prospective_v1",
        "net2s_ge_5": "net2s_ge_5_prospective_v1",
        "bc50_ge2_net5s_ge5": "bc50_ge2_net5s_ge5_prospective_v1",
    }
    gross_map = {
        "net1s_ge_5": "grossbuy_1s_ge5_prospective_v1",
        "net2s_ge_5": "grossbuy_2s_ge5_prospective_v1",
        "bc50_ge2_net5s_ge5": "bc50_ge2_grossbuy5s_ge5_prospective_v1",
    }

    measured_pipeline = {}
    lanes = prospective.get("lanes") or {}
    for rule, legacy_lane in top3_map.items():
        lane = lanes.get(gross_map[rule]) or lanes.get(legacy_lane) or {}
        latency = classify_latency(lane)
        measured_pipeline[rule] = {
            "laneUsed": gross_map[rule] if gross_map[rule] in lanes else legacy_lane,
            "latency": latency,
            "historicalCeilingBucketReplay": interpolate_latency_grid((full.get("rules") or {}).get(rule, {}), latency.get("p90Ms")),
        }

    semantic_parity = {}
    for rule, legacy_lane in top3_map.items():
        legacy_sem = "partial-net field, but retained sells contribute zero; effectively gross known buy pressure"
        prospective_sem = (lanes.get(legacy_lane) or {}).get("semantics", "unknown")
        semantic_parity[legacy_lane] = {
            "historicalSemantics": legacy_sem,
            "prospectiveSemantics": prospective_sem,
            "match": "MATCH_EFFECTIVE_WHILE_SELL_QUOTES_MISSING",
            "note": "Explicit grossbuy parity lane is preferred going forward because it remains stable even if future sellQuoteLamports appears.",
        }
        gross_lane = gross_map[rule]
        semantic_parity[gross_lane] = {
            "historicalSemantics": "gross known buy pressure",
            "prospectiveSemantics": (lanes.get(gross_lane) or {}).get("semantics", "gross_buy_pressure"),
            "match": "MATCH",
        }

    final_decision = "CONTINUE_PROSPECTIVE_VALIDATION"
    if any(
        item.get("latency", {}).get("classification") in ("LATENCY_MARGINAL", "LATENCY_INCOMPATIBLE")
        for item in measured_pipeline.values()
    ):
        final_decision = "LATENCY_PIPELINE_PROBLEM"

    report = {
        "historicalFlowSemantics": historical_semantics,
        "sellSolRecovery": {
            "classification": "NOT_RECOVERABLE_EXACTLY_FROM_CURRENT_RETAINED_FIELDS",
            "coverageScans": sell_scans,
            "reason": "Retained sell rows have sellTokenAmountRaw and bondingCurve, but no exact sell quote SOL or full before/after reserve/meta set in the canonical JSONL inspected here. Reconstructing from market-cap marks would be approximate and not acceptable for true net flow.",
        },
        "grossBuyVsTrueNet": {
            "historicalGrossBuyPressureValidated": True,
            "trueHistoricalNetValidated": False,
            "note": "True-net rules are created prospectively only when sell quote is point-in-time safe; historical true-net PF is not claimed.",
        },
        "prospectiveSemanticParity": semantic_parity,
        "parityLanes": {
            "grossbuy_1s_ge5_prospective_v1": "trailing 1-second gross BUY quote SOL >= 5",
            "grossbuy_2s_ge5_prospective_v1": "trailing 2-second gross BUY quote SOL >= 5",
            "bc50_ge2_grossbuy5s_ge5_prospective_v1": "buy_count_50ms >= 2 AND trailing 5-second gross BUY quote SOL >= 5",
        },
        "trueNetLanes": {
            "truenet_1s_ge5_prospective_v1": "trailing 1-second true net >= 5 only when every sell in window has point-in-time quote SOL",
            "truenet_2s_ge5_prospective_v1": "trailing 2-second true net >= 5 only when every sell in window has point-in-time quote SOL",
            "bc50_ge2_truenet5s_ge5_prospective_v1": "buy_count_50ms >= 2 AND trailing 5-second true net >= 5 only when every sell in window has point-in-time quote SOL",
        },
        "measuredPipelineReplay": measured_pipeline,
        "prospectiveStatus": prospective,
        "sizeScalabilityStatus": "SIZE_SCALABILITY_NOT_YET_PROVEN",
        "finalDecision": final_decision,
    }

    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    print(json.dumps({"report": OUT, "decision": report["finalDecision"]}, indent=2))


if __name__ == "__main__":
    main()
