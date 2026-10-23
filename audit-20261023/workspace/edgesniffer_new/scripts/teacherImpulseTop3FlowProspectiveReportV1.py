#!/usr/bin/env python3
import argparse
import glob
import json
import math
import os


LANE_SEMANTICS = {
    "net1s_ge_5_prospective_v1": "legacy_partial_net_known_sell_zero_when_missing",
    "net2s_ge_5_prospective_v1": "legacy_partial_net_known_sell_zero_when_missing",
    "bc50_ge2_net5s_ge5_prospective_v1": "legacy_partial_net_known_sell_zero_when_missing",
    "grossbuy_1s_ge5_prospective_v1": "gross_buy_pressure",
    "grossbuy_2s_ge5_prospective_v1": "gross_buy_pressure",
    "bc50_ge2_grossbuy5s_ge5_prospective_v1": "gross_buy_pressure",
    "truenet_1s_ge5_prospective_v1": "true_net_only_when_sell_quote_point_in_time_safe",
    "truenet_2s_ge5_prospective_v1": "true_net_only_when_sell_quote_point_in_time_safe",
    "bc50_ge2_truenet5s_ge5_prospective_v1": "true_net_only_when_sell_quote_point_in_time_safe",
}


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


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


def summarize(rows, pnl_key=None, outcome_key="outcome"):
    pnls = [
        float(
            row.get(pnl_key)
            if pnl_key and finite(row.get(pnl_key))
            else (
                row.get("sellFixV11ExpectedPnlPct")
                if finite(row.get("sellFixV11ExpectedPnlPct"))
                else row.get("feeAwarePnlPct", 0.0)
            )
        )
        for row in rows
    ]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    return {
        "n": len(rows),
        "wrPct": (len(wins) * 100.0 / len(pnls)) if pnls else None,
        "pf": (gross_profit / gross_loss) if gross_loss else ("inf" if gross_profit else 0.0),
        "expectancyPct": (sum(pnls) / len(pnls)) if pnls else None,
        "normalizedPnlAt1Sol": sum(pnls) / 100.0,
        "tp": sum(1 for row in rows if row.get(outcome_key) == "tp"),
        "sl": sum(1 for row in rows if row.get(outcome_key) == "sl"),
        "maxHold": sum(1 for row in rows if row.get(outcome_key) == "max_hold"),
        "noFill": sum(1 for row in rows if row.get(outcome_key) == "no_fill"),
    }


def quantile(values, pct):
    xs = sorted(value for value in values if finite(value))
    if not xs:
        return None
    idx = min(len(xs) - 1, max(0, int((len(xs) - 1) * pct)))
    return xs[idx]


def distribution(values):
    xs = [value for value in values if finite(value)]
    if not xs:
        return {"n": 0, "min": None, "median": None, "p75": None, "p90": None, "p95": None, "worst": None}
    return {
        "n": len(xs),
        "min": min(xs),
        "median": quantile(xs, 0.50),
        "p75": quantile(xs, 0.75),
        "p90": quantile(xs, 0.90),
        "p95": quantile(xs, 0.95),
        "worst": max(xs),
    }


def loss_streak(rows):
    longest = 0
    current = 0
    for row in rows:
        pnl = row.get("sellFixV11ExpectedPnlPct")
        if not finite(pnl):
            pnl = row.get("feeAwarePnlPct")
        if finite(pnl) and pnl < 0:
            current += 1
            longest = max(longest, current)
        elif finite(pnl):
            current = 0
    return longest


def max_drawdown(rows):
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for row in rows:
        pnl = row.get("sellFixV11ExpectedPnlPct")
        if not finite(pnl):
            pnl = row.get("feeAwarePnlPct")
        if not finite(pnl):
            continue
        equity += pnl / 100.0
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def rolling(rows, n):
    return summarize(rows[-n:]) if len(rows) >= n else None


def would_send_summary(rows):
    scored = [
        row for row in rows
        if row.get("wouldSendParityOutcome") is not None
        or finite(row.get("wouldSendParitySellFixV11ExpectedPnlPct"))
    ]
    return {
        "closedWithWouldSendView": len(scored),
        "summary": summarize(
            scored,
            pnl_key="wouldSendParitySellFixV11ExpectedPnlPct",
            outcome_key="wouldSendParityOutcome",
        ),
        "entryDeteriorationPct": distribution(
            (row.get("wouldSendParityEntry") or {}).get("entryDeteriorationPct")
            for row in scored
        ),
        "exitMs": distribution(row.get("wouldSendParityExitMs") for row in scored),
    }


def latency_summary(rows):
    components = {}
    for key in [
        "ruleTruthToFeatureReady",
        "featureReadyToQualification",
        "qualificationToSelected",
        "selectedToAuthoritativeReady",
        "authoritativeReadyToWouldSend",
        "ruleTruthToWouldSendTotal",
    ]:
        components[key] = distribution(
            (row.get("latencyComponentsMs") or {}).get(key)
            for row in rows
        )
    return components


def durability_summary(rows):
    winners = [row for row in rows if row.get("outcome") == "tp"]
    return {
        "winners": len(winners),
        "firstExecutableTpMs": distribution(row.get("exitMs") for row in winners),
        "tpDurationMs": distribution(row.get("tpDurationMs") for row in winners),
        "peakExecutablePnlPct": distribution(row.get("maxRoiPct") for row in winners),
        "tpPersistGe250ms": sum(1 for row in winners if finite(row.get("tpDurationMs")) and row["tpDurationMs"] >= 250),
        "tpPersistGe500ms": sum(1 for row in winners if finite(row.get("tpDurationMs")) and row["tpDurationMs"] >= 500),
        "tpPersistGe1000ms": sum(1 for row in winners if finite(row.get("tpDurationMs")) and row["tpDurationMs"] >= 1000),
        "tpPersistGe2000ms": sum(1 for row in winners if finite(row.get("tpDurationMs")) and row["tpDurationMs"] >= 2000),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-out-dir",
        default="/var/lib/edgesniffer-teacher-impulse-parity-v1/research/top3-flow-prospective-v1",
    )
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    report = {
        "baseOutDir": args.base_out_dir,
        "checkpoints": [50, 100, 250, 500, 1000],
        "lanes": {},
        "overlap": {},
        "noSubmit": True,
        "note": "Analytics-only prospective scoreboard. Uses SellFix V1.1 expected PnL when present.",
    }

    lane_qual_mints = {}
    for lane_dir in sorted(glob.glob(os.path.join(args.base_out_dir, "*"))):
        if not os.path.isdir(lane_dir):
            continue
        lane = os.path.basename(lane_dir)
        quals = list(read_jsonl(os.path.join(lane_dir, "qualifications.jsonl")) or [])
        outcomes = list(read_jsonl(os.path.join(lane_dir, "outcomes.jsonl")) or [])
        lane_qual_mints[lane] = {row.get("mint") for row in quals if row.get("mint")}
        report["lanes"][lane] = {
            "semantics": LANE_SEMANTICS.get(lane, "unknown"),
            "qualified": len(quals),
            "closed": len(outcomes),
            "openEstimate": max(0, len(quals) - len(outcomes)),
            "allClosed": summarize(outcomes),
            "wouldSendParity": would_send_summary(outcomes),
            "latency": latency_summary(quals),
            "winnerDurability": durability_summary(outcomes),
            "maxDrawdownSolAt1Sol": max_drawdown(outcomes),
            "longestLossStreak": loss_streak(outcomes),
            "rolling": {
                "last50": rolling(outcomes, 50),
                "last100": rolling(outcomes, 100),
                "last250": rolling(outcomes, 250),
            },
            "checkpoints": {
                str(n): summarize(outcomes[:n])
                for n in report["checkpoints"]
                if len(outcomes) >= n
            },
            "latestQualification": quals[-1] if quals else None,
            "latestOutcome": outcomes[-1] if outcomes else None,
        }

    lanes = sorted(lane_qual_mints)
    all_mints = set().union(*(lane_qual_mints[lane] for lane in lanes)) if lanes else set()
    for mint in all_mints:
        key = "+".join(lane for lane in lanes if mint in lane_qual_mints[lane])
        report["overlap"][key] = report["overlap"].get(key, 0) + 1

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
