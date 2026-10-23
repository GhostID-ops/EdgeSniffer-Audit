#!/usr/bin/env python3
"""Non-authoritative, outcome-blind peek at current 2CQ choice fingerprints."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import sqlite3
import statistics
from typing import Any


SCHEMA = "2CQ_PRELIMINARY_CHOICE_FINGERPRINT_PEEK_V1"
FORBIDDEN_PARTS = ("pnl", "roi", "profit", "future", "outcome", "exit")


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_identity_sha(rows: list[dict[str, Any]]) -> str:
    identities = [
        {
            "signature": row["signature"],
            "mint": row["mint"],
            "choice": row["choice"],
        }
        for row in rows
    ]
    raw = json.dumps(identities, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def assert_blind(value: Any, location: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"outcomesread", "outcomes_read", "validationrowsread", "validation_rows_read", "submissions"}:
                if int(item or 0) != 0:
                    raise RuntimeError(f"BLINDING_VIOLATION:{location}.{key}")
                continue
            if any(part in lowered for part in FORBIDDEN_PARTS):
                if item in (False, 0, None, ""):
                    continue
                raise RuntimeError(f"FORBIDDEN_FIELD:{location}.{key}")
            assert_blind(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_blind(item, f"{location}[{index}]")


def load_choices(db_path: pathlib.Path, sql: str) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        raw_rows = list(connection.execute(sql))
    finally:
        connection.close()
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        choice = json.loads(raw["choice_json"])
        assert_blind(choice)
        states = choice.get("states") or []
        if len(states) < 2 or sum(bool(state.get("label")) for state in states) != 1:
            raise RuntimeError(f"INVALID_CHOICE_SET:{raw['signature']}")
        rows.append({
            "signature": raw["signature"],
            "mint": raw["mint"],
            "lane": raw["lane"] if "lane" in raw.keys() else "SOL_PUMP_LAUNCHED_120S",
            "boundary": int(raw["boundary"]),
            "choice": choice,
        })
    rows.sort(key=lambda row: (row["boundary"], row["signature"]))
    return rows


def value(state: dict[str, Any], feature: str) -> float:
    raw = (state.get("features") or {}).get(feature)
    if raw is None or isinstance(raw, bool):
        raise RuntimeError(f"FEATURE_MISSING_OR_INVALID:{feature}")
    number = float(raw)
    if not math.isfinite(number):
        raise RuntimeError(f"FEATURE_NONFINITE:{feature}")
    return number


def percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * q
    lo = int(math.floor(position))
    hi = int(math.ceil(position))
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] * (hi - position) + sorted_values[hi] * (position - lo)


def univariate(rows: list[dict[str, Any]], feature: str, blocks: int = 3) -> dict[str, Any]:
    selected_values: list[float] = []
    control_medians: list[float] = []
    selected_percentiles: list[float] = []
    block_percentiles: list[list[float]] = [[] for _ in range(blocks)]
    for index, row in enumerate(rows):
        states = row["choice"]["states"]
        selected = next(state for state in states if state["label"])
        controls = [state for state in states if not state["label"]]
        selected_value = value(selected, feature)
        control_values = sorted(value(state, feature) for state in controls)
        all_values = sorted(control_values + [selected_value])
        less = sum(candidate < selected_value for candidate in all_values)
        equal = sum(candidate == selected_value for candidate in all_values)
        rank_pct = (less + 0.5 * equal) / len(all_values)
        selected_values.append(selected_value)
        control_medians.append(float(statistics.median(control_values)))
        selected_percentiles.append(rank_pct)
        block = min(blocks - 1, index * blocks // max(1, len(rows)))
        block_percentiles[block].append(rank_pct)
    mean_rank = statistics.fmean(selected_percentiles)
    direction = "HIGH" if mean_rank >= 0.5 else "LOW"
    block_means = [statistics.fmean(values) if values else None for values in block_percentiles]
    stable = all(
        block_mean is not None and ((block_mean >= 0.5) == (direction == "HIGH"))
        for block_mean in block_means
    )
    paired = [selected - control for selected, control in zip(selected_values, control_medians)]
    return {
        "feature": feature,
        "preferredDirection": direction,
        "episodeN": len(rows),
        "selectedMedian": statistics.median(selected_values),
        "controlMedianOfMedians": statistics.median(control_medians),
        "pairedMedianDifference": statistics.median(paired),
        "selectedMeanWithinChoicePercentile": mean_rank,
        "distanceFromChance": abs(mean_rank - 0.5),
        "selectedAboveControlMedianPct": 100 * sum(a > b for a, b in zip(selected_values, control_medians)) / len(rows),
        "selectedBelowControlMedianPct": 100 * sum(a < b for a, b in zip(selected_values, control_medians)) / len(rows),
        "chronologyBlockMeanPercentiles": block_means,
        "stableDirectionAcrossBlocks": stable,
    }


def summarize_univariates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    common = set((rows[0]["choice"]["states"][0].get("features") or {}).keys())
    for row in rows:
        for state in row["choice"]["states"]:
            common &= set((state.get("features") or {}).keys())
    summaries = [univariate(rows, feature) for feature in sorted(common)]
    summaries.sort(key=lambda item: (item["stableDirectionAcrossBlocks"], item["distanceFromChance"]), reverse=True)
    return summaries


def model_score(state: dict[str, Any], model: dict[str, Any]) -> float:
    total = float(model["intercept"])
    for feature in model["featureNames"]:
        transformed = (value(state, feature) - float(model["means"][feature])) / float(model["scales"][feature])
        total += float(model["coefficients"][feature]) * transformed
    return total


def ranking_metrics(rows: list[dict[str, Any]], model: dict[str, Any]) -> dict[str, Any]:
    ranks: list[int] = []
    choice_sizes: list[int] = []
    margins: list[float] = []
    per_row: list[dict[str, Any]] = []
    for row in rows:
        scored = [(model_score(state, model), bool(state["label"])) for state in row["choice"]["states"]]
        selected_score = next(score for score, label in scored if label)
        control_scores = [score for score, label in scored if not label]
        rank = 1 + sum(score > selected_score for score in control_scores)
        margin = selected_score - max(control_scores)
        ranks.append(rank)
        choice_sizes.append(len(scored))
        margins.append(margin)
        per_row.append({"signature": row["signature"], "mint": row["mint"], "rank": rank, "choiceN": len(scored), "marginToBestControl": margin})
    def pct(predicate: Any) -> float:
        return 100 * sum(predicate(rank) for rank in ranks) / len(ranks)
    return {
        "N": len(rows),
        "meanChoiceSetN": statistics.fmean(choice_sizes),
        "chanceTop1Pct": 100 * statistics.fmean(1 / size for size in choice_sizes),
        "top1Pct": pct(lambda rank: rank == 1),
        "top3Pct": pct(lambda rank: rank <= 3),
        "meanReciprocalRank": statistics.fmean(1 / rank for rank in ranks),
        "medianMarginToBestControl": statistics.median(margins),
        "rows": per_row,
    }


def split_metrics(rows: list[dict[str, Any]], model: dict[str, Any]) -> list[dict[str, Any]]:
    cuts = [0, len(rows) // 2, len(rows)]
    output = []
    for index in range(2):
        subset = rows[cuts[index]:cuts[index + 1]]
        metrics = ranking_metrics(subset, model)
        metrics.pop("rows")
        output.append({"block": index + 1, **metrics})
    return output


def render_markdown(report: dict[str, Any]) -> str:
    sol = report["solPump"]
    features = {item["feature"]: item for item in sol["topUnivariate"]}
    age = features["log_age_ms"]
    recency = features["log_ms_since_last_external"]
    halves = sol["chronologicalHalves"]
    lines = [
        "# 2CQ Preliminary Choice Fingerprint Peek V1",
        "",
        "NON-AUTHORITATIVE, outcome-blind, development-only hypothesis peek.",
        "",
        "## SOL-Pump forward check",
        "",
        f"- Fair decisions: {sol['N']}",
        f"- Frozen historical model Top-1: {sol['frozenModelForward']['top1Pct']:.2f}% (chance {sol['frozenModelForward']['chanceTop1Pct']:.2f}%)",
        f"- Top-3: {sol['frozenModelForward']['top3Pct']:.2f}%",
        f"- MRR: {sol['frozenModelForward']['meanReciprocalRank']:.3f}",
        f"- Chronological halves Top-1: {halves[0]['top1Pct']:.2f}% / {halves[1]['top1Pct']:.2f}%",
        "",
        "## Preliminary SOL-Pump fingerprint",
        "",
        f"- Very young: selected median {math.expm1(age['selectedMedian']):.0f}ms from CREATE versus "
        f"{math.expm1(age['controlMedianOfMedians']):.0f}ms for the contemporaneous control median.",
        f"- Immediate continuation: selected median {math.expm1(recency['selectedMedian']):.0f}ms since the latest "
        f"external event versus {math.expm1(recency['controlMedianOfMedians']):.0f}ms for controls.",
        f"- Fast multi-buy impulse: median {features['buy_count_250ms']['selectedMedian']:.0f} buys and "
        f"{features['gross_buy_sol_250ms']['selectedMedian']:.2f} SOL gross buy flow in 250ms.",
        f"- Sustained first-second flow: median {features['gross_buy_sol_1000ms']['selectedMedian']:.2f} SOL gross "
        f"and {features['net_flow_sol_1000ms']['selectedMedian']:.2f} SOL net buy flow in 1s.",
        f"- Meaningful participants: median {features['unique_buyers_1000ms']['selectedMedian']:.0f} unique buyers, "
        f"mean buy {features['mean_buy_sol_1000ms']['selectedMedian']:.2f} SOL, largest buy "
        f"{features['largest_buy_sol_1000ms']['selectedMedian']:.2f} SOL.",
        "",
        "This describes what 2CQ chose, not whether following those choices is profitable after execution.",
        "",
        "| Feature | Direction | Selected median | Control median | Mean choice percentile | Stable blocks |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in sol["topUnivariate"][:10]:
        lines.append(
            f"| {item['feature']} | {item['preferredDirection']} | {item['selectedMedian']:.4f} | "
            f"{item['controlMedianOfMedians']:.4f} | {item['selectedMeanWithinChoicePercentile']:.3f} | "
            f"{'yes' if item['stableDirectionAcrossBlocks'] else 'no'} |"
        )
    lines += ["", "## Route siblings", ""]
    for lane, payload in report["routeSiblings"].items():
        lines.append(f"### {lane}")
        lines.append(f"N={payload['N']}. {payload['classification']}")
        if payload.get("topUnivariate"):
            for item in payload["topUnivariate"][:5]:
                lines.append(
                    f"- {item['feature']}: {item['preferredDirection']}, mean within-choice percentile "
                    f"{item['selectedMeanWithinChoicePercentile']:.3f}, stable blocks="
                    f"{'yes' if item['stableDirectionAcrossBlocks'] else 'no'}"
                )
        lines.append("")
    lines += [
        "## Integrity",
        "",
        "- Outcome/PnL fields read: 0",
        "- Validation rows read: 0",
        "- Submissions: 0",
        "- Policies modified: 0",
        "- Routes pooled: no",
        "- Current sealed validation consumed: no",
        "- This report may generate hypotheses but may not freeze or promote a strategy",
        "",
        f"Verdict: `{report['verdict']}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sol-db", type=pathlib.Path, required=True)
    parser.add_argument("--route-db", type=pathlib.Path, required=True)
    parser.add_argument("--frozen-policy", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()

    sol_rows = load_choices(
        args.sol_db,
        "SELECT signature, mint, block_time_ms AS boundary, choice_json "
        "FROM current_decisions WHERE status='ONLINE_ELIGIBLE' AND choice_json IS NOT NULL "
        "ORDER BY block_time_ms, slot, transaction_index, signature",
    )
    route_rows = load_choices(
        args.route_db,
        "SELECT signature, mint, lane, boundary_ns AS boundary, choice_json "
        "FROM decisions WHERE status='ELIGIBLE' AND choice_json IS NOT NULL "
        "ORDER BY boundary_ns, slot, tx_index, signature",
    )
    policy = json.loads(args.frozen_policy.read_text(encoding="utf-8"))
    assert_blind(policy)
    model = policy["crossMintModel"]
    forward = ranking_metrics(sol_rows, model)
    forward_rows = forward.pop("rows")
    sol_univariate = summarize_univariates(sol_rows)

    route_lanes: dict[str, Any] = {}
    for lane in sorted({row["lane"] for row in route_rows}):
        lane_rows = [row for row in route_rows if row["lane"] == lane]
        if len(lane_rows) < 10:
            route_lanes[lane] = {"N": len(lane_rows), "classification": "TINY_N_NO_FINGERPRINT", "topUnivariate": []}
            continue
        route_lanes[lane] = {
            "N": len(lane_rows),
            "classification": "NON_AUTHORITATIVE_DESCRIPTIVE_HYPOTHESIS_ONLY",
            "topUnivariate": summarize_univariates(lane_rows)[:10],
            "choiceRowIdentitySha256": row_identity_sha(lane_rows),
        }

    report = {
        "schema": SCHEMA,
        "generatedAtUtc": now_utc(),
        "scope": "NON_AUTHORITATIVE_SANDBOX_PEEK_OUTCOME_BLIND",
        "solPump": {
            "N": len(sol_rows),
            "choiceRowIdentitySha256": row_identity_sha(sol_rows),
            "frozenPolicyPath": str(args.frozen_policy),
            "frozenPolicySha256": sha256_file(args.frozen_policy),
            "frozenModelForward": forward,
            "chronologicalHalves": split_metrics(sol_rows, model),
            "topUnivariate": sol_univariate[:20],
            "rankingRows": forward_rows,
        },
        "routeSiblings": route_lanes,
        "integrity": {
            "outcomesRead": 0,
            "validationRowsRead": 0,
            "submissions": 0,
            "modelsFit": 0,
            "thresholdsTuned": 0,
            "policiesModified": 0,
            "routesPooled": False,
        },
        "verdict": "PRELIMINARY_TEACHER_CHOICE_FINGERPRINT_VISIBLE_NOT_ALPHA_PROOF",
    }
    atomic_json(args.output / "report.json", report)
    (args.output / "report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
