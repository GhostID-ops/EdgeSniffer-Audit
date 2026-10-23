#!/usr/bin/env python3
"""Combine the completed 2CQ development-only decomposition reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp = Path(handle.name)
    os.replace(temp, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metric(model: dict[str, Any]) -> dict[str, float | int]:
    assessment = model["validation"]
    ranked = assessment["withinMint"]
    return {
        "n": assessment["positiveN"],
        "rocAuc": assessment["rocAuc"],
        "top1Pct": ranked["actualRankedFirstPct"],
        "top3Pct": ranked["actualRankedTop3Pct"],
        "chanceTop1Pct": ranked["chanceRankedFirstPct"],
        "mrr": ranked["meanReciprocalRank"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()

    development_path = args.directory / "report.json"
    cross_path = args.directory / "cross-sectional-report.json"
    development = json.loads(development_path.read_text())
    cross = json.loads(cross_path.read_text())

    timing = {
        name: metric(model)
        for name, model in development["entryTiming"]["models"].items()
    }
    selection = {
        universe: {
            "trainN": payload["trainN"],
            "assessmentN": payload["assessmentN"],
            "meanChoiceSetN": payload["meanChoiceSetN"],
            "missingChosen": cross["choiceSet"]["missingChosenByUniverse"][universe],
            "models": {
                name: metric(model) for name, model in payload["models"].items()
            },
        }
        for universe, payload in cross["universes"].items()
    }

    result = {
        "schema": "2CQ_DECISION_SYSTEM_DECOMPOSITION_V1_FINAL",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "developmentN": development["population"]["developmentN"],
        "population": development["population"],
        "entryTimingAssessment": timing,
        "convictionAssessment": development["conviction"],
        "crossSectionalAssessment": selection,
        "deferredActions": development["deferredActions"],
        "integrity": {
            **development["integrity"],
            **cross["integrity"],
            "developmentReportSha256": sha256(development_path),
            "crossSectionalReportSha256": sha256(cross_path),
        },
        "evidence": cross["evidence"],
        "globalReconciliationExceptions": cross["globalReconciliationExceptions"],
        "verdict": "2CQ_DECISION_SYSTEM_DECOMPOSITION_V1_DEVELOPMENT_COMPLETE",
    }
    atomic_write(args.directory / "final-report.json", json.dumps(result, indent=2, sort_keys=True) + "\n")

    lines = [
        "# 2CQ Decision System Decomposition V1",
        "",
        "Development-only, outcome-blind result. Official validation remains sealed.",
        "",
        "## Population",
        "",
        f"- Development entries: {result['developmentN']}",
        f"- Timing-identifiable entries: {development['population']['timingIdentifiableN']}",
        f"- Unique mints: {development['population']['uniqueMints']}",
        "",
        "## Same-mint timing (internal chronological assessment)",
        "",
        "| Model | N | ROC AUC | Top-1 | Top-3 | Chance Top-1 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, values in timing.items():
        lines.append(
            f"| {name} | {values['n']} | {values['rocAuc']:.3f} | "
            f"{values['top1Pct']:.2f}% | {values['top3Pct']:.2f}% | "
            f"{values['chanceTop1Pct']:.2f}% |"
        )
    lines += [
        "",
        "## Cross-sectional choice (internal chronological assessment)",
        "",
        "| Universe | Model | N | Mean choices | ROC AUC | Top-1 | Chance Top-1 | Missing chosen |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for universe, payload in selection.items():
        for name, values in payload["models"].items():
            lines.append(
                f"| {universe} | {name} | {values['n']} | {payload['meanChoiceSetN']:.2f} | "
                f"{values['rocAuc']:.3f} | {values['top1Pct']:.2f}% | "
                f"{values['chanceTop1Pct']:.2f}% | {payload['missingChosen']} |"
            )
    conviction = development["conviction"]
    lines += [
        "",
        "## Conviction / size",
        "",
        f"- Assessment N: {conviction['validationN']}",
        f"- Exact tier accuracy: {conviction['exactAccuracyPct']:.2f}%",
        f"- Majority baseline: {conviction['majorityBaselineAccuracyPct']:.2f}%",
        f"- Expected-tier Spearman: {conviction['spearmanExpectedTier']:.3f}",
        f"- Tier MAE: {conviction['meanAbsoluteTierError']:.3f}",
        "",
        "## Integrity",
        "",
        "- Validation rows read: 0",
        "- Outcome labels read: 0",
        "- Submissions: 0",
        "- Teacher entry excluded from features: yes",
        "- Cross-sectional states are strictly pre-teacher-boundary: yes",
        "",
        "## Scope",
        "",
        "ADD_WHILE_EXPOSED, PARTIAL_EXIT, and FINAL_FLATTEN remain deferred because the frozen V3 tape is an independent-entry census.",
        "",
        f"Verdict: `{result['verdict']}`",
    ]
    atomic_write(args.directory / "final-report.md", "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
