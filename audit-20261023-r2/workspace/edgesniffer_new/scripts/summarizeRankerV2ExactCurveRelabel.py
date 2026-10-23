#!/usr/bin/env python3
"""Render a decision-grade report from frozen exact-curve relabel artifacts."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


PRIMARY_ENTRY_MS = 25
PRIMARY_SELLABLE_MS = 1476
EXPECTED_EVALUATOR_SHA256 = (
    "badf8d417a155931498422300ffadb5ba1b3e3574bbcad9fd62d52161144ff10"
)
POPULATIONS = [
    "train",
    "validation",
    "final_test",
    "full_prospective",
    "recent_prospective",
    "last_24h",
    "n25_v2_window",
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl_gz(path: Path) -> Iterable[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float) and math.isinf(value):
        return "inf"
    return f"{float(value):.{digits}f}"


def memberships(row: dict[str, Any]) -> list[str]:
    if "memberships" in row:
        return list(row["memberships"])
    if row.get("population") == "prospective":
        return list(row.get("tags", []))
    return [row["split"]]


def old_metrics(values: list[float]) -> dict[str, Any]:
    positive = sum(value for value in values if value > 0)
    negative = -sum(value for value in values if value < 0)
    return {
        "n": len(values),
        "wr": 100 * sum(value > 0 for value in values) / len(values) if values else None,
        "pf": positive / negative if negative else (math.inf if positive else None),
        "expectancy": sum(values) / len(values) if values else None,
    }


def primary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row["entry_delay_ms"] == PRIMARY_ENTRY_MS
        and row["first_sellable_delay_ms"] == PRIMARY_SELLABLE_MS
    ]


def row_for(
    rows: list[dict[str, Any]], population: str, size: float
) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in rows
            if row["population"] == population and row["size_sol"] == size
        ),
        None,
    )


def metric_cells(row: dict[str, Any]) -> str:
    return (
        f"{row['total_signals']} | {row['exactly_evaluable']} | "
        f"{row['excluded_inexact']} | {row['unresolved']} | {row['wins']} | "
        f"{row['losses']} | {fmt(row['win_rate_pct'], 2)}% | "
        f"{fmt(row['profit_factor'])} | {fmt(row['expectancy_pct'])}% | "
        f"{fmt(row['total_pnl_sol'], 6)} | {fmt(row['max_drawdown_sol'], 6)} | "
        f"{row['tp']} | {row['sl']} | {row['max_hold']}"
    )


def make_report(base: Path) -> tuple[str, dict[str, Any]]:
    manifest = load_json(base / "manifest.json")
    matrix = load_json(base / "matrix-all-signals.json")
    max_open = load_json(base / "matrix-max-open1.json")
    comparison = load_json(base / "old-model-comparison.json")
    attribution = load_json(base / "capacity-attribution.json")
    robustness = load_json(base / "robustness.json")

    if manifest["evaluator_sha256"] != EXPECTED_EVALUATOR_SHA256:
        raise RuntimeError("unexpected evaluator hash")
    evaluator_path = Path(manifest["evaluator_path"])
    if sha256(evaluator_path) != EXPECTED_EVALUATOR_SHA256:
        raise RuntimeError("evaluator artifact no longer matches the frozen hash")

    primary_rows = primary(matrix)
    primary_max_open = primary(max_open)
    exact_primary_ids = {
        row["signal_id"]
        for row in load_jsonl_gz(base / "exact-outcomes.jsonl.gz")
        if row["size_lamports"] == 10_000_000
        and row["entry_delay_ms"] == PRIMARY_ENTRY_MS
        and row["first_sellable_delay_ms"] == PRIMARY_SELLABLE_MS
    }

    all_old: dict[str, list[float]] = defaultdict(list)
    admitted_old: dict[str, list[float]] = defaultdict(list)
    for signal in load_jsonl_gz(base / "frozen-signals.jsonl.gz"):
        value = signal.get("old_return_pct")
        if value is None:
            continue
        for population in memberships(signal):
            all_old[population].append(float(value))
            if signal["signal_id"] in exact_primary_ids:
                admitted_old[population].append(float(value))

    exclusions: dict[str, Counter[str]] = defaultdict(Counter)
    all_exclusions: dict[tuple[str, int, int, int], Counter[str]] = defaultdict(Counter)
    for row in load_jsonl_gz(base / "exclusions.jsonl.gz"):
        for population in row["memberships"]:
            all_exclusions[
                (
                    population,
                    row["size_lamports"],
                    row["entry_delay_ms"],
                    row["first_sellable_delay_ms"],
                )
            ][row["reason"]] += 1
        if (
            row["size_lamports"] == 10_000_000
            and row["entry_delay_ms"] == PRIMARY_ENTRY_MS
            and row["first_sellable_delay_ms"] == PRIMARY_SELLABLE_MS
        ):
            for population in row["memberships"]:
                exclusions[population][row["reason"]] += 1

    full_primary = {
        row["size_sol"]: row
        for row in primary_rows
        if row["population"] == "full_prospective"
    }
    recent_primary = {
        row["size_sol"]: row
        for row in primary_rows
        if row["population"] == "recent_prospective"
    }
    max_open_full = {
        row["size_sol"]: row
        for row in primary_max_open
        if row["population"] == "full_prospective"
    }

    classification = "RANKER_V2_MC_PROXY_ALPHA_NOT_EXECUTABLE"
    verdict = {
        "classification": classification,
        "evaluator_sha256": EXPECTED_EVALUATOR_SHA256,
        "primary_entry_delay_ms": PRIMARY_ENTRY_MS,
        "primary_first_sellable_delay_ms": PRIMARY_SELLABLE_MS,
        "exact_historical_train_n": row_for(primary_rows, "train", 0.01)["resolved"],
        "exact_historical_validation_n": row_for(primary_rows, "validation", 0.01)["resolved"],
        "full_prospective_exact_n_0p01": full_primary[0.01]["resolved"],
        "full_prospective_exact_coverage_pct_0p01": (
            100 * full_primary[0.01]["resolved"] / full_primary[0.01]["total_signals"]
        ),
        "full_prospective": {
            str(size): {
                "n": row["resolved"],
                "pf": row["profit_factor"],
                "expectancy_pct": row["expectancy_pct"],
                "pnl_sol": row["total_pnl_sol"],
            }
            for size, row in full_primary.items()
        },
        "recent_prospective": {
            str(size): {
                "n": row["resolved"],
                "pf": row["profit_factor"],
                "expectancy_pct": row["expectancy_pct"],
                "pnl_sol": row["total_pnl_sol"],
            }
            for size, row in recent_primary.items()
        },
        "max_open1_full_prospective": {
            str(size): {
                "n": row["resolved"],
                "pf": row["profit_factor"],
                "expectancy_pct": row["expectancy_pct"],
                "pnl_sol": row["total_pnl_sol"],
            }
            for size, row in max_open_full.items()
        },
        "qualification": (
            "Historical train/validation exact replay is unavailable; the classification "
            "is based on exact prospective evidence and same-row old-vs-exact comparisons."
        ),
    }

    lines = [
        "# Ranker V2 Exact-Curve Relabel: Final Report",
        "",
        f"**Classification: `{classification}`**",
        "",
        "The frozen Ranker V2 signal does not retain executable alpha in the exact-admitted "
        "prospective sample at any tested size. This is not a whole-history exact relabel: "
        "train and validation curve history is unavailable, and prospective exact coverage "
        "is incomplete. Missing evidence was excluded rather than approximated.",
        "",
        "## Frozen Scope",
        "",
        f"- Evaluator: `{manifest['evaluator']}`",
        f"- Evaluator SHA-256: `{manifest['evaluator_sha256']}`",
        f"- Ranker threshold: `{manifest['threshold']}`",
        f"- Historical trigger SHA-256: `{manifest['population']['historical']['trigger_sha256']}`",
        f"- Historical economics SHA-256: `{manifest['population']['historical']['economics_sha256']}`",
        f"- Frozen signals: {manifest['population']['total_signals']} across "
        f"{manifest['population']['unique_mints']} unique mints",
        f"- Prospective snapshot: {manifest['population']['prospective']['unique_signals']} unique signals",
        "- Strategy: TP +20%, SL -20%, true max hold 120s",
        "- Entry delays: 25/50/100ms; first-sellable: 250/500/1000/1476ms",
        "- Sizes: 0.01/0.10/0.50/1.00 SOL; nonlinear curve replay at every size",
        "- No MC fallback, no threshold/feature/state tuning, and no live changes",
        "",
        "## Direct Answers",
        "",
        "1. **0.01 SOL:** No retained exact alpha. Full prospective PF 0.085, expectancy "
        "-6.695%, N=236 exact.",
        "2. **0.10 SOL:** No retained exact alpha. PF 0.085, expectancy -6.749%, N=232.",
        "3. **0.50 SOL:** No retained exact alpha. PF 0.079, expectancy -6.932%, N=223.",
        "4. **1.00 SOL:** No retained exact alpha. PF 0.075, expectancy -7.022%, N=213.",
        "5. **Best grid cell, descriptive only:** full prospective 100ms entry/1000ms "
        "sellability is least negative; no tested timing cell is positive.",
        "6. **MC-proxy error:** on the same 0.01-SOL admitted rows, proxy PF 1.525 / "
        "+1.590% became exact PF 0.089 / -7.197%.",
        "7. **Recent prospective:** negative at every size and every tested timing combination.",
        "8. **MAX_OPEN1:** destroys rather than preserves the observed exact economics; at "
        "0.01 SOL, PF 0.062 and expectancy -5.949% on N=116 exact accepted outcomes.",
        "9. **Capacity:** no practical positive capacity limit exists for this frozen signal. "
        "It is already negative at 0.01 SOL; larger sizes add impact and more flow changes.",
        "10. **Next status:** do not resume live Ranker V2 or promote it under exact economics. "
        "A future-only no-submit exact evaluator may continue only as evidence collection, not tuning.",
        "",
        "## Coverage",
        "",
        "| Population | Frozen signals | Exact @0.01 | Coverage | Principal limitation |",
        "|---|---:|---:|---:|---|",
    ]
    for population in POPULATIONS:
        row = row_for(primary_rows, population, 0.01)
        assert row is not None
        reason = exclusions[population].most_common(1)
        reason_text = f"{reason[0][0]} ({reason[0][1]})" if reason else "none"
        lines.append(
            f"| {population} | {row['total_signals']} | {row['resolved']} | "
            f"{100 * row['resolved'] / row['total_signals']:.2f}% | {reason_text} |"
        )

    lines += [
        "",
        "Train and validation have zero exact outcomes because the retained finalized curve "
        "history cannot reach their causal entry anchors. The evaluator did not substitute "
        "MC-ratio returns. Final-test evidence is only N=7-8 and is descriptive.",
        "",
        "## Primary Exact Economics",
        "",
        "Primary case: 25ms entry delay and 1476ms first-sellable.",
        "",
        "| Population | Size | Total | Exact | Excluded | Unresolved | W | L | WR | PF | E | PnL SOL | Max DD | TP | SL | Hold |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for population in POPULATIONS:
        for size in (0.01, 0.1, 0.5, 1.0):
            row = row_for(primary_rows, population, size)
            assert row is not None
            lines.append(f"| {population} | {size:.2f} | {metric_cells(row)} |")

    lines += [
        "",
        "## Same-Row Old Proxy vs Exact",
        "",
        "Only rows carrying both old and exact outcomes are compared. Delta is exact minus old.",
        "",
        "| Population | Size | N | Old PF | Exact PF | Delta PF | Old E | Exact E | Delta E | Old PnL | Exact PnL |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison:
        old = row["old_mc_proxy"]
        exact = row["exact"]
        lines.append(
            f"| {row['population']} | {row['size_sol']:.2f} | {row['matched_n']} | "
            f"{fmt(old['profit_factor'])} | {fmt(exact['profit_factor'])} | "
            f"{fmt((exact['profit_factor'] - old['profit_factor']) if exact['profit_factor'] is not None and old['profit_factor'] is not None else None)} | "
            f"{fmt(old['expectancy_pct'])}% | {fmt(exact['expectancy_pct'])}% | "
            f"{fmt(exact['expectancy_pct'] - old['expectancy_pct'])}% | "
            f"{fmt(old['total_pnl_sol'], 6)} | {fmt(exact['total_pnl_sol'], 6)} |"
        )

    lines += [
        "",
        "### Coverage-Bias Check",
        "",
        "| Population | Old available N | Old PF all | Old E all | Old N exact-admitted | Old PF admitted | Old E admitted |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for population in POPULATIONS:
        all_metric = old_metrics(all_old[population])
        admitted_metric = old_metrics(admitted_old[population])
        lines.append(
            f"| {population} | {all_metric['n']} | {fmt(all_metric['pf'])} | "
            f"{fmt(all_metric['expectancy'])}% | {admitted_metric['n']} | "
            f"{fmt(admitted_metric['pf'])} | {fmt(admitted_metric['expectancy'])}% |"
        )
    lines += [
        "",
        "Exact-admitted prospective rows are weaker under the old proxy than the whole "
        "prospective population, so missingness is not random. Even so, the matched admitted "
        "rows were positive under the proxy and sharply negative under exact replay. That is "
        "direct model-error evidence, while the whole-population conclusion remains coverage-limited.",
        "",
        "## Size and Capacity",
        "",
        "Full prospective, primary timing:",
        "",
        "| Size | N | PF | E | PnL | Entry impact mean/P50/P75/P90 | Exit impact mean/P50/P75/P90 | Fee burden mean | External status flips |",
        "|---:|---:|---:|---:|---:|---|---|---:|---:|",
    ]
    for size, row in sorted(full_primary.items()):
        entry = row["entry_impact_pct"]
        exit_impact = row["exit_impact_pct"]
        fee = row["fee_burden_pct"]
        lines.append(
            f"| {size:.2f} | {row['resolved']} | {fmt(row['profit_factor'])} | "
            f"{fmt(row['expectancy_pct'])}% | {fmt(row['total_pnl_sol'], 6)} | "
            f"{fmt(entry['mean'])}/{fmt(entry['p50'])}/{fmt(entry['p75'])}/{fmt(entry['p90'])}% | "
            f"{fmt(exit_impact['mean'])}/{fmt(exit_impact['p50'])}/{fmt(exit_impact['p75'])}/{fmt(exit_impact['p90'])}% | "
            f"{fmt(fee['mean'])}% | {row['external_status_flips']}/{row['external_transactions']} "
            f"({100 * row['external_status_flip_fraction']:.2f}%) |"
        )

    lines += [
        "",
        "Ablations are nonadditive because changing our trade changes later transaction "
        "success and reserve state. At 0.01 SOL, removing own fees improves expectancy by "
        "about 2.39 percentage points, removing entry impact by about 0.43 points, removing "
        "exit impact by about 0.07 points, and forcing external transactions successful by "
        "about 0.36 points. None restores positive expectancy. At 1 SOL, mean entry/exit "
        "impact rises to 3.08%/2.92%, and 9.63% of replayed external transaction outcomes flip.",
        "",
        "## MAX_OPEN1",
        "",
        "Primary timing, chronological actual hold lifecycle:",
        "",
        "| Population | Size | Accepted | Capacity rejected | Exact accepted | Inexact accepted | PF | E | PnL |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for population in POPULATIONS:
        for size in (0.01, 0.1, 0.5, 1.0):
            row = row_for(primary_max_open, population, size)
            assert row is not None
            lines.append(
                f"| {population} | {size:.2f} | {row['accepted']} | "
                f"{row['capacity_rejected']} | {row['resolved']} | {row['accepted_inexact']} | "
                f"{fmt(row['profit_factor'])} | {fmt(row['expectancy_pct'])}% | "
                f"{fmt(row['total_pnl_sol'], 6)} |"
            )

    lines += [
        "",
        "MAX_OPEN1 does not rescue the signal. Full prospective 0.01 SOL falls to PF 0.062 "
        "and -5.949% expectancy; all tested sizes remain negative.",
        "",
        "## Robustness",
        "",
        "| Population | Size | Baseline PF/E | Remove top 5 PF/E | Remove top 1% PF/E | Missing as SL20 PF/E |",
        "|---|---:|---|---|---|---|",
    ]
    for row in robustness:
        if row["population"] not in ("final_test", "full_prospective", "recent_prospective", "last_24h"):
            continue
        baseline = row["baseline"]
        top5 = row["remove_top_5_winners"]
        top1 = row["remove_top_1pct_winners"]
        worst = row["missing_as_policy_sl20"]
        lines.append(
            f"| {row['population']} | {row['size_sol']:.2f} | "
            f"{fmt(baseline['profit_factor'])}/{fmt(baseline['expectancy_pct'])}% | "
            f"{fmt(top5['profit_factor'])}/{fmt(top5['expectancy_pct'])}% | "
            f"{fmt(top1['profit_factor'])}/{fmt(top1['expectancy_pct'])}% | "
            f"{fmt(worst['profit_factor'])}/{fmt(worst['expectancy_pct'])}% |"
        )

    lines += [
        "",
        "All four full-prospective chronological quartiles are negative at every size. At "
        "0.01 SOL their expectancies are -8.90%, -11.22%, -4.43%, and -2.23%. The last "
        "quartile improves, but does not cross zero. Removing winners only worsens an already "
        "negative result.",
        "",
        "## Primary Exclusions",
        "",
        "25ms/1476ms at 0.01 SOL:",
        "",
        "| Population | Reason | Count |",
        "|---|---|---:|",
    ]
    for population in POPULATIONS:
        if not exclusions[population]:
            lines.append(f"| {population} | none | 0 |")
        for reason, count in exclusions[population].most_common():
            lines.append(f"| {population} | {reason} | {count} |")

    lines += [
        "",
        "## Timing Grid Summary",
        "",
        "No tested full-prospective or recent-prospective timing cell is positive. The "
        "least-negative full-prospective cell is 100ms/1000ms; this is descriptive and was "
        "not selected as a new policy. The exhaustive frozen matrix follows.",
        "",
        "## Exhaustive Exact Matrix",
        "",
        "Every requested size x entry-delay x first-sellable combination is listed below.",
    ]
    for population in POPULATIONS:
        lines += [
            "",
            f"### {population}",
            "",
            "| Size | Entry ms | Sellable ms | Total | Exact | Excluded | Unresolved | W | L | WR | PF | E | PnL | DD | TP | SL | Hold | Entry impact mean/P50/P75/P90 | Exit impact mean/P50/P75/P90 | Fee mean | External flips | Exclusion reasons |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---|",
        ]
        rows = sorted(
            (row for row in matrix if row["population"] == population),
            key=lambda row: (
                row["size_sol"],
                row["entry_delay_ms"],
                row["first_sellable_delay_ms"],
            ),
        )
        for row in rows:
            entry = row["entry_impact_pct"]
            exit_impact = row["exit_impact_pct"]
            fee = row["fee_burden_pct"]
            reasons = all_exclusions[
                (
                    population,
                    row["size_lamports"],
                    row["entry_delay_ms"],
                    row["first_sellable_delay_ms"],
                )
            ]
            reason_text = "; ".join(
                f"{reason}:{count}" for reason, count in reasons.most_common()
            ) or "none"
            lines.append(
                f"| {row['size_sol']:.2f} | {row['entry_delay_ms']} | "
                f"{row['first_sellable_delay_ms']} | {metric_cells(row)} | "
                f"{fmt(entry['mean'])}/{fmt(entry['p50'])}/{fmt(entry['p75'])}/{fmt(entry['p90'])}% | "
                f"{fmt(exit_impact['mean'])}/{fmt(exit_impact['p50'])}/{fmt(exit_impact['p75'])}/{fmt(exit_impact['p90'])}% | "
                f"{fmt(fee['mean'])}% | {row['external_status_flips']}/{row['external_transactions']} "
                f"({fmt(100 * row['external_status_flip_fraction'], 2) if row['external_status_flip_fraction'] is not None else 'N/A'}%) | "
                f"{reason_text} |"
            )

    lines += [
        "",
        "## Evidence Integrity",
        "",
        f"- Matched canonical trade observations: {manifest['corpus']['scan']['matched_trade_observations']}",
        f"- Complete curve-history windows: {manifest['curve_history']['complete']}/{manifest['curve_history']['signals']}",
        f"- Additional finalized transaction details fetched: {manifest['rpc_evidence']['curve_history']['transactions_fetched_now']}",
        f"- Curve-history observations added: {manifest['curve_history_augmentation']['observations_added']}",
        f"- Transaction cache: {manifest['rpc_evidence']['final_cache']['transactions']} signatures, "
        f"{manifest['rpc_evidence']['final_cache']['transactions_available']} available transactions",
        "- Failed/dropped transactions mutate zero state; successful transaction groups replay atomically",
        "- Unsupported/inexact cases are explicit exclusions; no silent MC-proxy fallback",
        "- Live Ranker remained paused and N25-v3 remained preserved",
        "",
        "## Final Interpretation",
        "",
        "The exact evidence answers the production question more harshly than the retired "
        "proxy. The frozen Ranker V2 score selected a same-row prospective cohort that looked "
        "positive under MC-ratio economics, but that cohort became deeply negative when our "
        "exact buy, fees, causal subsequent flow, first-sellable boundary, exact inventory, "
        "and exact sell were replayed. The effect exists at dust size, so 0.5-1 SOL capacity "
        "is not the primary failure; larger size simply worsens an already non-executable edge.",
        "",
        "Because historical exact evidence is unavailable and prospective missingness is "
        "non-random, this is not proof that every frozen signal would lose. It is enough to "
        "reject promotion: the available exact evidence does not support executable alpha, "
        "MAX_OPEN1 does not preserve it, and no frozen timing/size cell rescues it.",
        "",
        f"**Final classification: `{classification}`**",
    ]
    return "\n".join(lines) + "\n", verdict


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    report, verdict = make_report(args.output_dir)
    report_path = args.output_dir / "FINAL_REPORT.md"
    verdict_path = args.output_dir / "final-verdict.json"
    report_path.write_text(report, encoding="utf-8")
    verdict_path.write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    artifacts = sorted(
        path
        for path in args.output_dir.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    )
    checksum_lines = [f"{sha256(path)}  {path.name}" for path in artifacts]
    (args.output_dir / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    print(json.dumps(verdict, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
