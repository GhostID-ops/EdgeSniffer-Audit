#!/usr/bin/env python3
import argparse
import json
import math
import os
from collections import Counter, defaultdict


BASE = "/var/lib/edgesniffer-teacher-impulse-parity-v1"
PROD_BASE = os.path.join(BASE, "research", "production-candidate-path-v1")
PROD_EVENTS = os.path.join(PROD_BASE, "rule-a-production-path-events.jsonl")
OBSERVATIONS = os.path.join(BASE, "normalized-observations.jsonl")
CAPSULES = os.path.join(BASE, "candidate-capsules.jsonl")
FAST_A = os.path.join(
    BASE,
    "research",
    "fast-flow-detector-v1",
    "grossbuy_1s_ge5_fastdetector_v1",
    "qualifications.jsonl",
)
FAST_B = os.path.join(
    BASE,
    "research",
    "fast-flow-detector-v1",
    "grossbuy_2s_ge5_fastdetector_v1",
    "qualifications.jsonl",
)
FAST_C = os.path.join(
    BASE,
    "research",
    "fast-flow-detector-v1",
    "bc50_ge2_grossbuy5s_ge5_fastdetector_v1",
    "qualifications.jsonl",
)
OUT = os.path.join(PROD_BASE, "report.json")


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
        "worst": max(xs) if xs else None,
    }


def ms_delta(end, start):
    if not finite(end) or not finite(start):
        return None
    # Separate audit events can land in the same millisecond with opposite
    # rounding edges; for report distributions, negative elapsed time is noise.
    return max(0, end - start)


def read_jsonl(path):
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


def iter_jsonl_tail(path, tail_mb):
    if not os.path.exists(path):
        return
    start = 0
    if tail_mb and tail_mb > 0:
        size = os.path.getsize(path)
        start = max(0, size - int(tail_mb * 1024 * 1024))
    with open(path, "rb") as handle:
        if start:
            handle.seek(start)
            handle.readline()
        for raw in handle:
            if not raw.strip():
                continue
            try:
                yield json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                continue


def ts(row):
    return (
        row.get("atMs")
        or row.get("candidateQualifiedAtMs")
        or row.get("ruleTruthAtMs")
        or row.get("candidate_ready_ts_ms")
        or row.get("decode_complete_ts_ms")
        or row.get("local_receive_ts_ms")
    )


def mint(row):
    return row.get("mint") or (row.get("candidate") or {}).get("mint")


def key(row):
    return row.get("key") or mint(row)


def get_path(row, *path):
    cur = row
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def observation_mc(row):
    value = get_path(row, "canonical_state", "market_cap_usd")
    if finite(value):
        return float(value)
    value = get_path(row, "candidate", "market_cap_usd")
    if finite(value):
        return float(value)
    value = row.get("marketCapUsd") or row.get("mcUsd")
    return float(value) if finite(value) else None


def pnl_from_mc(entry_mc, current_mc, cost_pct):
    if not finite(entry_mc) or entry_mc <= 0 or not finite(current_mc):
        return None
    return ((current_mc / entry_mc) - 1.0) * 100.0 - cost_pct


def score_marks(entry_ts, entry_mc, marks, tp_pct=20.0, sl_pct=-20.0, max_hold_ms=120_000, cost_pct=0.0):
    if not finite(entry_ts) or not finite(entry_mc) or entry_mc <= 0:
        return None
    scoped = [(t, mc) for t, mc in marks if finite(t) and finite(mc) and t >= entry_ts]
    if not scoped:
        return None
    peak = None
    trough = None
    first_tp_ts = None
    last_tp_ts = None
    for t, mc in scoped:
        if t > entry_ts + max_hold_ms:
            break
        pnl = pnl_from_mc(entry_mc, mc, cost_pct)
        if pnl is None:
            continue
        peak = pnl if peak is None else max(peak, pnl)
        trough = pnl if trough is None else min(trough, pnl)
        if pnl >= tp_pct:
            if first_tp_ts is None:
                first_tp_ts = t
            last_tp_ts = t
            return {
                "outcome": "tp",
                "pnlPct": tp_pct,
                "exitMs": t - entry_ts,
                "maxRoiPct": peak,
                "minRoiPct": trough,
                "firstExecutableTpMs": t - entry_ts,
                "tpDurationMs": 0,
            }
        if pnl <= sl_pct:
            return {
                "outcome": "sl",
                "pnlPct": sl_pct,
                "exitMs": t - entry_ts,
                "maxRoiPct": peak,
                "minRoiPct": trough,
                "firstExecutableTpMs": None,
                "tpDurationMs": None,
            }
    horizon = entry_ts + max_hold_ms
    horizon_marks = [item for item in scoped if item[0] <= horizon]
    if not horizon_marks:
        return None
    last_t, last_mc = horizon_marks[-1]
    if last_t < horizon and scoped[-1][0] < horizon:
        return None
    pnl = pnl_from_mc(entry_mc, last_mc, cost_pct)
    if pnl is None:
        return None
    return {
        "outcome": "max_hold",
        "pnlPct": pnl,
        "exitMs": min(max_hold_ms, last_t - entry_ts),
        "maxRoiPct": peak,
        "minRoiPct": trough,
        "firstExecutableTpMs": (first_tp_ts - entry_ts) if first_tp_ts else None,
        "tpDurationMs": ((last_tp_ts - first_tp_ts) if first_tp_ts and last_tp_ts else None),
    }


def summarize_pnl_rows(rows, pnl_key="pnlPct", outcome_key="outcome"):
    pnls = [float(row[pnl_key]) for row in rows if finite(row.get(pnl_key))]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    return {
        "n": len(pnls),
        "wrPct": (len(wins) * 100.0 / len(pnls)) if pnls else None,
        "pf": (gross_profit / gross_loss) if gross_loss else ("inf" if gross_profit else 0.0),
        "expectancyPct": (sum(pnls) / len(pnls)) if pnls else None,
        "normalizedPnlAt1Sol": sum(pnls) / 100.0,
        "tp": sum(1 for row in rows if row.get(outcome_key) == "tp"),
        "sl": sum(1 for row in rows if row.get(outcome_key) == "sl"),
        "maxHold": sum(1 for row in rows if row.get(outcome_key) == "max_hold"),
    }


def max_drawdown_sol(rows):
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for row in rows:
        pnl = row.get("pnlPct")
        if not finite(pnl):
            continue
        equity += pnl / 100.0
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def longest_loss_streak(rows):
    longest = 0
    current = 0
    for row in rows:
        pnl = row.get("pnlPct")
        if finite(pnl) and pnl < 0:
            current += 1
            longest = max(longest, current)
        elif finite(pnl):
            current = 0
    return longest


def contribution(rows, top_n):
    pnls = sorted((row.get("pnlPct") for row in rows if finite(row.get("pnlPct"))), reverse=True)
    total = sum(pnls)
    if not pnls or total == 0:
        return None
    return sum(pnls[:top_n]) / total


def fast_detector_provenance(rows, start_boundary):
    row_ts = [ts(row) for row in rows if ts(row)]
    forward = [
        row
        for row in rows
        if start_boundary and ts(row) and ts(row) >= start_boundary
    ]
    raw_keys = [key(row) for row in rows if key(row)]
    raw_mints = [mint(row) for row in rows if mint(row)]
    forward_mints = [mint(row) for row in forward if mint(row)]
    return {
        "rawRows": len(rows),
        "rawUniqueKeys": len(set(raw_keys)),
        "rawUniqueMints": len(set(raw_mints)),
        "rawDuplicateRowsRemoved": len(raw_keys) - len(set(raw_keys)),
        "firstQualificationAtMs": min(row_ts) if row_ts else None,
        "lastQualificationAtMs": max(row_ts) if row_ts else None,
        "forwardOnlyRowsAfterProductionPathStart": len(forward),
        "forwardOnlyUniqueMintsAfterProductionPathStart": len(set(forward_mints)),
        "containsRowsBeforeProductionPathStart": (
            any(value < start_boundary for value in row_ts)
            if start_boundary and row_ts
            else None
        ),
        "classification": (
            "CONTAINS_BACKFILL_OR_OLDER_FORWARD_ROWS"
            if start_boundary and any(value < start_boundary for value in row_ts)
            else "TRUE_PROSPECTIVE_AFTER_BOUNDARY"
        ),
    }


def find_entry_at_or_after(marks, at_ms):
    if not finite(at_ms):
        return None
    for mark_ts, mark_mc in marks:
        if mark_ts >= at_ms:
            return mark_ts, mark_mc
    return None


def score_signal_cohort(rows, marks_by_mint, entry_ts_field, entry_mc_field=None):
    scored = []
    for row in rows:
        row_mint = mint(row) or get_path(row, "extra", "mint")
        entry_ts = row.get(entry_ts_field)
        marks = sorted(marks_by_mint.get(row_mint, []))
        entry_mark = find_entry_at_or_after(marks, entry_ts)
        if entry_mark:
            score_entry_ts, score_entry_mc = entry_mark
        else:
            score_entry_ts, score_entry_mc = entry_ts, row.get(entry_mc_field) if entry_mc_field else None
        outcome = score_marks(score_entry_ts, score_entry_mc, marks)
        if outcome is None:
            scored.append({
                "mint": row_mint,
                "status": "open_or_unscorable",
                "entryTsMs": score_entry_ts,
                "entryMcUsd": score_entry_mc,
                "marks": len(marks),
            })
            continue
        scored.append({
            "mint": row_mint,
            "status": "resolved",
            "entryTsMs": score_entry_ts,
            "entryMcUsd": score_entry_mc,
            **outcome,
        })
    return scored


def bucket_counts(values):
    xs = [value for value in values if finite(value)]
    return {
        "n": len(xs),
        "lte2ms": sum(1 for value in xs if value <= 2),
        "lte5ms": sum(1 for value in xs if value <= 5),
        "lte10ms": sum(1 for value in xs if value <= 10),
        "lte20ms": sum(1 for value in xs if value <= 20),
        "lte23p4ms": sum(1 for value in xs if value <= 23.4),
        "gt23p4ms": sum(1 for value in xs if value > 23.4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--production-events", default=PROD_EVENTS)
    parser.add_argument("--fast-a", default=FAST_A)
    parser.add_argument("--fast-b", default=FAST_B)
    parser.add_argument("--fast-c", default=FAST_C)
    parser.add_argument("--observations", default=OBSERVATIONS)
    parser.add_argument(
        "--marks",
        default=CAPSULES,
        help="Point-in-time market-cap mark stream; candidate capsules carry canonical_state MC.",
    )
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--tp-pct", type=float, default=20.0)
    parser.add_argument("--sl-pct", type=float, default=-20.0)
    parser.add_argument("--max-hold-ms", type=int, default=120_000)
    parser.add_argument("--sellfix-tp-penalty-pp", type=float, default=3.0)
    parser.add_argument("--observations-tail-mb", type=int, default=256)
    parser.add_argument("--marks-tail-mb", type=int, default=1024)
    parser.add_argument(
        "--start-at-ms",
        type=int,
        default=None,
        help="Pinned forward cohort boundary. Use this to preserve a cohort across no-submit telemetry restarts.",
    )
    parser.add_argument(
        "--all-starts",
        action="store_true",
        help="Include all events instead of isolating the latest sniper start.",
    )
    args = parser.parse_args()

    raw_prod = read_jsonl(args.production_events)
    starts = [
        row
        for row in raw_prod
        if row.get("kind") == "rustSelectedCandidateSniperStarted" and ts(row)
    ]
    latest_start = max((ts(row) for row in starts), default=None)
    start_boundary = args.start_at_ms if args.start_at_ms is not None else (None if args.all_starts else latest_start)
    prod = [
        row
        for row in raw_prod
        if start_boundary is None or (ts(row) and ts(row) >= start_boundary)
    ]
    fast_a = read_jsonl(args.fast_a)
    fast_b = read_jsonl(args.fast_b)
    fast_c = read_jsonl(args.fast_c)

    by_kind = Counter(row.get("kind", "unknown") for row in prod)
    rejects = Counter(
        row.get("reason", "unknown")
        for row in prod
        if row.get("kind") in {"teacherImpulseProductionRuleARejected", "rustSelectedCandidateRejected"}
    )

    first_ts = min((ts(row) for row in prod if ts(row)), default=None)
    last_ts = max((ts(row) for row in prod if ts(row)), default=None)

    qualified = [row for row in prod if row.get("kind") == "teacherImpulseProductionRuleACandidateQualified"]
    authority = [row for row in prod if row.get("kind") == "teacherImpulseProductionRuleAAuthorityReady"]
    buy_start = [row for row in prod if row.get("kind") == "rustSelectedCandidateBuyStart"]
    dry_ready = [row for row in prod if row.get("kind") == "rustSelectedCandidateNoSubmitDryBuildReady"]
    shadow = [row for row in prod if row.get("kind") == "teacherImpulseProductionRuleShadowQualified"]
    authority_miss_resolved = [
        row for row in prod if row.get("kind") == "teacherImpulseProductionRuleAAuthorityMissResolved"
    ]

    qualified_by_key = {key(row): row for row in qualified}
    authority_by_key = {key(row): row for row in authority}
    buy_start_by_key = {key(row): row for row in buy_start}
    dry_by_key = {key(row): row for row in dry_ready}

    actual_send_like = []
    for row in prod:
        kind = row.get("kind", "")
        sender = row.get("senderPath")
        if row.get("submitEnabled") is True:
            actual_send_like.append(row)
        elif kind in {"rustSelectedCandidateBuyResult", "rustSelectedCandidateBuyFinality"}:
            actual_send_like.append(row)
        elif sender and sender != "dry-build-no-submit":
            actual_send_like.append(row)

    latency_rows = []
    for k, dry in dry_by_key.items():
        qual = qualified_by_key.get(k, {})
        auth = authority_by_key.get(k, {})
        start = buy_start_by_key.get(k, {})
        rule_truth_at = qual.get("ruleTruthAtMs")
        qualified_at = qual.get("candidateQualifiedAtMs")
        authority_ready_at = auth.get("atMs") or start.get("authoritativeStateReadyAtMs")
        final_guard_at = start.get("finalEconomicGuardAtMs")
        buy_start_at = start.get("atMs")
        would_send_at = dry.get("wouldFirstSendAtMs")
        latency_rows.append(
            {
                "key": k,
                "mint": dry.get("mint") or qual.get("mint"),
                "ruleTruthToQualifiedMs": (
                    ms_delta(qualified_at, rule_truth_at)
                ),
                "qualifiedToAuthorityMs": (
                    ms_delta(authority_ready_at, qualified_at)
                ),
                "authorityToGuardMs": (
                    ms_delta(final_guard_at, authority_ready_at)
                ),
                "guardToBuyStartMs": start.get("finalEconomicGuardToBuildMs"),
                "buyStartToWouldSendMs": (
                    ms_delta(would_send_at, buy_start_at)
                ),
                "ruleTruthToWouldSendMs": (
                    ms_delta(would_send_at, rule_truth_at)
                ),
                "selectedToWouldSendMs": dry.get("wouldFirstSendElapsedMs"),
                "buildMs": dry.get("buildMs"),
                "signMs": dry.get("signMs"),
                "serializeMs": dry.get("serializeMs"),
                "txBytes": dry.get("txBytes"),
                "reserveSource": dry.get("reserveSource"),
                "preSignReserveSource": dry.get("preSignReserveSource"),
                "entryMarketCapUsd": qual.get("entryMarketCapUsd"),
                "finalEconomicGuardEntryMarketCapUsd": dry.get("finalEconomicGuardEntryMarketCapUsd"),
            }
        )

    authority_blocked_rows = [
        row
        for row in prod
        if row.get("kind") == "teacherImpulseProductionRuleARejected"
        and row.get("reason") == "fill_first_send_time_curve_cache_miss"
    ]
    other_guard_blocked_rows = [
        row
        for row in prod
        if row.get("kind") in {"teacherImpulseProductionRuleARejected", "rustSelectedCandidateRejected"}
        and row.get("reason") != "fill_first_send_time_curve_cache_miss"
    ]
    dry_mints = {mint(row) for row in dry_ready if mint(row)}
    all_truth_mints = set(dry_mints)
    all_truth_mints.update(mint(row) or get_path(row, "extra", "mint") for row in authority_blocked_rows)
    all_truth_mints.update(mint(row) or get_path(row, "extra", "mint") for row in other_guard_blocked_rows)
    all_truth_mints = {value for value in all_truth_mints if value}
    marks_by_mint = defaultdict(list)
    latest_mark_ts = None
    if all_truth_mints:
        for row in iter_jsonl_tail(args.marks, args.marks_tail_mb):
            row_ts = ts(row)
            if start_boundary and row_ts and row_ts < start_boundary:
                continue
            latest_mark_ts = max(latest_mark_ts or row_ts or 0, row_ts or 0)
            row_mint = mint(row) or get_path(row, "candidate", "mint")
            if row_mint not in all_truth_mints:
                continue
            mc = observation_mc(row)
            if row_ts and finite(mc):
                marks_by_mint[row_mint].append((row_ts, mc))
        if not marks_by_mint and args.observations:
            # Fallback for older corpora where normalized observations included
            # canonical state; this is analytics only and never in the hot path.
            for row in iter_jsonl_tail(args.observations, args.observations_tail_mb):
                row_ts = ts(row)
                if start_boundary and row_ts and row_ts < start_boundary:
                    continue
                latest_mark_ts = max(latest_mark_ts or row_ts or 0, row_ts or 0)
                row_mint = mint(row) or get_path(row, "candidate", "mint")
                if row_mint not in all_truth_mints:
                    continue
                mc = observation_mc(row)
                if row_ts and finite(mc):
                    marks_by_mint[row_mint].append((row_ts, mc))

    economics_rows = []
    for dry in dry_ready:
        row_mint = mint(dry)
        would_send_at = dry.get("wouldFirstSendAtMs")
        entry_mc = dry.get("finalEconomicGuardEntryMarketCapUsd") or dry.get("entryMarketCapUsd")
        marks = sorted(marks_by_mint.get(row_mint, []))
        entry_mark = next(
            ((mark_ts, mark_mc) for mark_ts, mark_mc in marks if mark_ts >= would_send_at),
            None,
        ) if finite(would_send_at) else None
        if entry_mark:
            entry_ts, executable_entry_mc = entry_mark
        else:
            entry_ts, executable_entry_mc = would_send_at, entry_mc
        scored = score_marks(
            entry_ts,
            executable_entry_mc,
            marks,
            args.tp_pct,
            args.sl_pct,
            args.max_hold_ms,
            0.0,
        )
        if scored is None:
            economics_rows.append({
                "key": key(dry),
                "mint": row_mint,
                "status": "open_or_unscorable",
                "wouldFirstSendAtMs": would_send_at,
                "entryMcUsd": executable_entry_mc,
                "marks": len(marks),
            })
            continue
        sellfix_pnl = scored.get("pnlPct")
        if scored.get("outcome") == "tp" and finite(sellfix_pnl):
            sellfix_pnl -= args.sellfix_tp_penalty_pp
        economics_rows.append({
            "key": key(dry),
            "mint": row_mint,
            "status": "resolved",
            "wouldFirstSendAtMs": would_send_at,
            "entryTsMs": entry_ts,
            "entryMcUsd": executable_entry_mc,
            "guardEntryMcUsd": entry_mc,
            "entryDeteriorationPct": (
                ((executable_entry_mc / entry_mc) - 1.0) * 100.0
                if finite(executable_entry_mc) and finite(entry_mc) and entry_mc
                else None
            ),
            "outcome": scored.get("outcome"),
            "pnlPct": scored.get("pnlPct"),
            "sellFixV11ExpectedPnlPct": sellfix_pnl,
            "exitMs": scored.get("exitMs"),
            "firstExecutableTpMs": scored.get("firstExecutableTpMs"),
            "tpDurationMs": scored.get("tpDurationMs"),
            "maxRoiPct": scored.get("maxRoiPct"),
            "minRoiPct": scored.get("minRoiPct"),
            "observedMarks": len(marks),
        })

    resolved_economics = [row for row in economics_rows if row.get("status") == "resolved"]
    capacity_rows = []
    occupied_until = -1
    for row in sorted(resolved_economics, key=lambda item: item.get("wouldFirstSendAtMs") or 0):
        start_at = row.get("wouldFirstSendAtMs")
        exit_ms = row.get("exitMs")
        if not finite(start_at) or not finite(exit_ms):
            continue
        if start_at < occupied_until:
            blocked = dict(row)
            blocked["capacityDecision"] = "blocked"
            capacity_rows.append(blocked)
            continue
        taken = dict(row)
        taken["capacityDecision"] = "taken"
        capacity_rows.append(taken)
        occupied_until = start_at + max(0, exit_ms)
    capacity_taken = [row for row in capacity_rows if row.get("capacityDecision") == "taken"]
    capacity_blocked = [row for row in capacity_rows if row.get("capacityDecision") == "blocked"]

    signal_rows = []
    for row in dry_ready:
        signal_rows.append({
            "mint": mint(row),
            "ruleTruthAtMs": qualified_by_key.get(key(row), {}).get("ruleTruthAtMs") or row.get("wouldFirstSendAtMs"),
            "entryMarketCapUsd": row.get("finalEconomicGuardEntryMarketCapUsd") or row.get("entryMarketCapUsd"),
        })
    for row in authority_blocked_rows:
        extra = row.get("extra") or {}
        forensics = extra.get("authorityForensics") or {}
        rule_truth = (
            forensics.get("ruleTruthAtMs")
            or (row.get("atMs") - extra.get("authorityWaitMs") if finite(row.get("atMs")) and finite(extra.get("authorityWaitMs")) else row.get("atMs"))
        )
        signal_rows.append({
            "mint": extra.get("mint") or mint(row),
            "ruleTruthAtMs": rule_truth,
            "entryMarketCapUsd": None,
        })
    for row in other_guard_blocked_rows:
        extra = row.get("extra") or {}
        signal_rows.append({
            "mint": extra.get("mint") or mint(row),
            "ruleTruthAtMs": row.get("atMs"),
            "entryMarketCapUsd": None,
        })
    signal_scored = score_signal_cohort(signal_rows, marks_by_mint, "ruleTruthAtMs", "entryMarketCapUsd")
    authority_scored = score_signal_cohort([
        {
            "mint": (row.get("extra") or {}).get("mint") or mint(row),
            "ruleTruthAtMs": (
                ((row.get("extra") or {}).get("authorityForensics") or {}).get("ruleTruthAtMs")
                or (row.get("atMs") - (row.get("extra") or {}).get("authorityWaitMs") if finite(row.get("atMs")) and finite((row.get("extra") or {}).get("authorityWaitMs")) else row.get("atMs"))
            ),
        }
        for row in authority_blocked_rows
    ], marks_by_mint, "ruleTruthAtMs")
    other_guard_scored = score_signal_cohort([
        {"mint": (row.get("extra") or {}).get("mint") or mint(row), "ruleTruthAtMs": row.get("atMs")}
        for row in other_guard_blocked_rows
    ], marks_by_mint, "ruleTruthAtMs")

    miss_classes = Counter()
    miss_context_rows = []
    for row in authority_blocked_rows:
        extra = row.get("extra") or {}
        forensics = extra.get("authorityForensics") or {}
        miss_class = forensics.get("missClass") or "UNCLASSIFIED_PRE_FORENSICS_PATCH"
        miss_classes[miss_class] += 1
        miss_context_rows.append({
            "mint": extra.get("mint"),
            "bondingCurve": extra.get("bondingCurve"),
            "atMs": row.get("atMs"),
            "authorityWaitMs": extra.get("authorityWaitMs"),
            "missClass": miss_class,
            "firstObservedAtMs": forensics.get("firstObservedAtMs"),
            "firstPumpEventAtMs": forensics.get("firstPumpEventAtMs"),
            "firstBuyFlowAtMs": forensics.get("firstBuyFlowAtMs"),
            "watchedAtMs": forensics.get("watchedAtMs"),
            "watchToRuleMs": forensics.get("watchToRuleMs"),
            "firstObservedToRuleMs": forensics.get("firstObservedToRuleMs"),
            "latestYellowstoneAgeMs": forensics.get("latestYellowstoneAgeMs"),
        })
    resolved_delay_values = [
        row.get("ruleTruthToFirstFreshAuthorityMs") for row in authority_miss_resolved
    ]

    prod_mints = {mint(row) for row in qualified if mint(row)}
    prod_truth_mints = {
        mint(row) or get_path(row, "extra", "mint")
        for row in prod
        if row.get("kind") in {"teacherImpulseProductionRuleACandidateQualified", "teacherImpulseProductionRuleARejected"}
        and (mint(row) or get_path(row, "extra", "mint"))
    }
    fast_forward = [
        row for row in fast_a if start_boundary and ts(row) and ts(row) >= start_boundary
    ]
    fast_mints = {mint(row) for row in fast_forward if mint(row)}
    both = prod_mints & fast_mints

    authority_waits = [row.get("waitedMs") for row in authority]
    authority_ages = [row.get("ageMs") for row in authority]
    latency_summary = {
        "ruleTruthToQualifiedMs": dist([row["ruleTruthToQualifiedMs"] for row in latency_rows]),
        "qualifiedToAuthorityMs": dist([row["qualifiedToAuthorityMs"] for row in latency_rows]),
        "authorityToGuardMs": dist([row["authorityToGuardMs"] for row in latency_rows]),
        "guardToBuyStartMs": dist([row["guardToBuyStartMs"] for row in latency_rows]),
        "buyStartToWouldSendMs": dist([row["buyStartToWouldSendMs"] for row in latency_rows]),
        "selectedToWouldSendMs": dist([row["selectedToWouldSendMs"] for row in latency_rows]),
        "ruleTruthToWouldSendMs": dist([row["ruleTruthToWouldSendMs"] for row in latency_rows]),
        "buildMs": dist([row["buildMs"] for row in latency_rows]),
        "signMs": dist([row["signMs"] for row in latency_rows]),
        "serializeMs": dist([row["serializeMs"] for row in latency_rows]),
    }

    report = {
        "schemaVersion": "teacher-impulse-production-candidate-path-v1-report",
        "productionEventsFile": args.production_events,
        "fastDetectorRuleAFile": args.fast_a,
        "fastDetectorRuleBFile": args.fast_b,
        "fastDetectorRuleCFile": args.fast_c,
        "markSourceFile": args.marks,
        "cohort": {
            "productionCandidatePathV1StartTimestamp": start_boundary,
            "latestServiceStartAtMs": latest_start,
            "latestStartOnly": not args.all_starts,
            "firstEventAtMs": first_ts,
            "lastEventAtMs": last_ts,
            "productionEvents": len(prod),
            "rawProductionEvents": len(raw_prod),
            "serviceStartsSeen": len(starts),
            "pinnedStartBoundary": args.start_at_ms is not None,
        },
        "ruleSemantics": {
            "A": "trailing 1000ms gross BUY quote SOL >= 5.0",
            "B_shadow": "trailing 2000ms gross BUY quote SOL >= 5.0",
            "C_shadow": "trailing 50ms buy count >= 2 AND trailing 5000ms gross BUY quote SOL >= 5.0",
            "windowBoundary": "inclusive: now_ms - event_observed_at_ms <= window_ms",
            "dedupe": "per mint signature+instructionIndex+side",
            "firstTruth": "one Rule A production qualification per mint lifecycle",
        },
        "prospectiveCohortProvenance": {
            "productionPath": {
                "serviceStartsSeen": len(starts),
                "latestServiceStartAtMs": latest_start,
                "forwardBoundaryAtMs": start_boundary,
                "newProductionPathRowsAfterBoundary": len(prod),
                "classification": "TRUE_PROSPECTIVE_AFTER_BOUNDARY" if start_boundary else "NO_BOUNDARY",
            },
            "fastDetectorA": fast_detector_provenance(fast_a, start_boundary),
            "fastDetectorB": fast_detector_provenance(fast_b, start_boundary),
            "fastDetectorC": fast_detector_provenance(fast_c, start_boundary),
            "note": "Rows before the new production path boundary are preserved but excluded from the production-path forward cohort.",
        },
        "counts": {
            "byKind": dict(by_kind),
            "ruleAQualified": len(qualified),
            "ruleAAuthorityReady": len(authority),
            "ruleADryBuildReady": len(dry_ready),
            "ruleBShadowQualified": sum(1 for row in shadow if row.get("rule") == "B"),
            "ruleCShadowQualified": sum(1 for row in shadow if row.get("rule") == "C"),
            "authorityMissResolved": len(authority_miss_resolved),
            "actualSendLikeEvents": len(actual_send_like),
        },
        "rejects": dict(rejects),
        "noSubmitProof": {
            "actualSendLikeEvents": len(actual_send_like),
            "actualSendCountRequired": 0,
            "pass": len(actual_send_like) == 0,
            "dryBuildSenderPaths": dict(Counter(row.get("senderPath") for row in dry_ready)),
        },
        "authority": {
            "yellowstoneReady": len(authority),
            "immediateReadyPct": (
                sum(1 for value in authority_waits if finite(value) and value == 0) * 100.0 / len(authority_waits)
                if authority_waits
                else None
            ),
            "waitMs": dist(authority_waits),
            "ageMs": dist(authority_ages),
            "sources": dict(Counter(row.get("authoritySource") for row in authority)),
        },
        "latency": latency_summary,
        "semanticParityWithFastDetectorAfterProductionStart": {
            "fastDetectorForwardRows": len(fast_forward),
            "fastDetectorForwardUniqueMints": len(fast_mints),
            "productionQualifiedUniqueMints": len(prod_mints),
            "productionRuleATruthUniqueMints": len(prod_truth_mints),
            "bothUniqueMints": len(both),
            "fastOnlyUniqueMints": len(fast_mints - prod_mints),
            "productionOnlyUniqueMints": len(prod_mints - fast_mints),
            "parityPctOfProduction": (len(both) * 100.0 / len(prod_mints) if prod_mints else None),
            "parityPctOfFastDetectorForward": (len(both) * 100.0 / len(fast_mints) if fast_mints else None),
            "preSafetyBothUniqueMints": len(prod_truth_mints & fast_mints),
            "preSafetyFastOnlyUniqueMints": len(fast_mints - prod_truth_mints),
            "preSafetyProductionOnlyUniqueMints": len(prod_truth_mints - fast_mints),
            "preSafetyParityPctOfFastDetectorForward": (
                len(prod_truth_mints & fast_mints) * 100.0 / len(fast_mints)
                if fast_mints else None
            ),
        },
        "duplicateMintCohortAudit": {
            "ruleAQualifiedRawRows": len(qualified),
            "ruleAQualifiedUniqueMints": len(prod_mints),
            "ruleAQualifiedDuplicateRowsRemoved": len(qualified) - len(prod_mints),
            "ruleADryBuildRawRows": len(dry_ready),
            "ruleADryBuildUniqueMints": len(dry_mints),
            "ruleADryBuildDuplicateRowsRemoved": len(dry_ready) - len(dry_mints),
            "requalificationSemantics": "one Rule A first-truth per mint lifecycle; duplicate mint truths are rejected as duplicate_rule_a_mint",
        },
        "ruleAProductionFunnel": {
            "ruleATruth": len(prod_truth_mints),
            "authorityReady": len(authority),
            "candidateQualified": len(qualified),
            "dryAccepted": sum(1 for row in prod if row.get("kind") == "rustSelectedCandidateDryAccepted"),
            "wouldSendDryBuildReady": len(dry_ready),
            "rejected": sum(1 for row in prod if row.get("kind") == "teacherImpulseProductionRuleARejected"),
            "rejects": dict(rejects),
        },
        "authorityMissForensics": {
            "classes": dict(miss_classes),
            "resolvedLater": len(authority_miss_resolved),
            "arrivalDelayMs": dist(resolved_delay_values),
            "arrivalDelayBuckets": bucket_counts(resolved_delay_values),
            "unresolvedOrPrePatch": len(authority_blocked_rows) - len(authority_miss_resolved),
            "latestResolvedRows": authority_miss_resolved[-10:],
            "latestMissContextRows": miss_context_rows[-10:],
            "note": "Older misses before the forensics telemetry patch remain UNCLASSIFIED_PRE_FORENSICS_PATCH; new misses include root-cause class and later-arrival timing.",
        },
        "authorityBlockedCounterfactualEconomics": {
            "label": "SIGNAL_COUNTERFACTUAL_ONLY",
            "resolved": sum(1 for row in authority_scored if row.get("status") == "resolved"),
            "openOrUnscorable": sum(1 for row in authority_scored if row.get("status") != "resolved"),
            "summary": summarize_pnl_rows([row for row in authority_scored if row.get("status") == "resolved"]),
        },
        "otherGuardBlockedCounterfactualEconomics": {
            "label": "SIGNAL_COUNTERFACTUAL_ONLY",
            "resolved": sum(1 for row in other_guard_scored if row.get("status") == "resolved"),
            "openOrUnscorable": sum(1 for row in other_guard_scored if row.get("status") != "resolved"),
            "summary": summarize_pnl_rows([row for row in other_guard_scored if row.get("status") == "resolved"]),
        },
        "level1SignalEconomics": {
            "label": "ALL_RULE_A_SIGNALS_COUNTERFACTUAL_FROM_RULE_TRUTH",
            "resolved": sum(1 for row in signal_scored if row.get("status") == "resolved"),
            "openOrUnscorable": sum(1 for row in signal_scored if row.get("status") != "resolved"),
            "summary": summarize_pnl_rows([row for row in signal_scored if row.get("status") == "resolved"]),
        },
        "executableVsMissedWinners": {
            "totalSignalLevelWinners": sum(1 for row in signal_scored if row.get("status") == "resolved" and finite(row.get("pnlPct")) and row["pnlPct"] > 0),
            "executableWinners": sum(1 for row in resolved_economics if finite(row.get("pnlPct")) and row["pnlPct"] > 0),
            "authorityBlockedWinners": sum(1 for row in authority_scored if row.get("status") == "resolved" and finite(row.get("pnlPct")) and row["pnlPct"] > 0),
            "totalSignalLevelLosers": sum(1 for row in signal_scored if row.get("status") == "resolved" and finite(row.get("pnlPct")) and row["pnlPct"] < 0),
            "authorityBlockedLosers": sum(1 for row in authority_scored if row.get("status") == "resolved" and finite(row.get("pnlPct")) and row["pnlPct"] < 0),
        },
        "ruleAProductionPathEconomics": {
            "scoring": "from wouldFirstSendAtMs using observed market-cap marks; TP/SL/max-hold; SellFix V1.1 expected subtracts TP penalty",
            "tpPct": args.tp_pct,
            "slPct": args.sl_pct,
            "maxHoldMs": args.max_hold_ms,
            "latestMarkTsMs": latest_mark_ts,
            "resolved": len(resolved_economics),
            "openOrUnscorable": sum(1 for row in economics_rows if row.get("status") != "resolved"),
            "cleanExecutable": summarize_pnl_rows(resolved_economics, "pnlPct"),
            "sellFixV11Expected": summarize_pnl_rows(resolved_economics, "sellFixV11ExpectedPnlPct"),
            "entryDeteriorationPct": dist(row.get("entryDeteriorationPct") for row in resolved_economics),
            "maxDrawdownSolAt1Sol": max_drawdown_sol(resolved_economics),
            "longestLossStreak": longest_loss_streak(resolved_economics),
            "top1Contribution": contribution(resolved_economics, 1),
            "top3Contribution": contribution(resolved_economics, 3),
            "latestRows": economics_rows[-10:],
        },
        "maxOpen1Capacity": {
            "resolvedWouldSendCandidates": len(resolved_economics),
            "taken": len(capacity_taken),
            "capacityBlocked": len(capacity_blocked),
            "blockedWinners": sum(1 for row in capacity_blocked if finite(row.get("pnlPct")) and row["pnlPct"] > 0),
            "blockedLosers": sum(1 for row in capacity_blocked if finite(row.get("pnlPct")) and row["pnlPct"] < 0),
            "takenSummary": summarize_pnl_rows(capacity_taken, "sellFixV11ExpectedPnlPct"),
        },
        "winnerDurability": {
            "winners": sum(1 for row in resolved_economics if row.get("outcome") == "tp"),
            "firstExecutableTpMs": dist(row.get("firstExecutableTpMs") for row in resolved_economics if row.get("outcome") == "tp"),
            "tpDurationMs": dist(row.get("tpDurationMs") for row in resolved_economics if row.get("outcome") == "tp"),
            "peakExecutablePnlPct": dist(row.get("maxRoiPct") for row in resolved_economics if row.get("outcome") == "tp"),
        },
        "ruleBCShadowOverlap": {
            "ruleBQualifiedMints": len({mint(row) for row in shadow if row.get("rule") == "B" and mint(row)}),
            "ruleCQualifiedMints": len({mint(row) for row in shadow if row.get("rule") == "C" and mint(row)}),
            "ruleAWouldSendAndB": sum(
                1 for row in dry_ready
                if (mint(row) in {mint(s) for s in shadow if s.get("rule") == "B" and mint(s)})
            ),
            "ruleAWouldSendAndC": sum(
                1 for row in dry_ready
                if (mint(row) in {mint(s) for s in shadow if s.get("rule") == "C" and mint(s)})
            ),
        },
        "sampleRows": {
            "latestRuleAQualified": qualified[-5:],
            "latestDryBuildReady": dry_ready[-5:],
            "latestRejects": [
                row
                for row in prod
                if row.get("kind") in {"teacherImpulseProductionRuleARejected", "rustSelectedCandidateRejected"}
            ][-10:],
        },
        "verdict": (
            "CONTINUE_PRODUCTION_PATH_SHADOW"
            if len(actual_send_like) == 0
            else "RULE_A_PRODUCTION_PARITY_FAILED"
        ),
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
