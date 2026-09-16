#!/usr/bin/env python3
"""Complete characterization of the frozen Pump exact-alpha V1 N5000 universe.

This harness does not add features or thresholds. Candidate generation is a
literal reproduction of scanPumpExactAlphaFingerprintsV1.py:
  * numeric V1 fields except mintAgeMs
  * unique-value quantiles q=.1..9
  * ge/le one-feature rules with min N=2% of checkpoint
  * 2-way combinations from the scanner's top 12 one-feature bases
  * 3-way combinations from its top 8 bases, three distinct features, N>=300

It adds reporting only: factor shapes, chronology, sizes, outliers, nearby
stability, cohort deduplication, and balanced ranking.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import io
import itertools
import json
import math
import pathlib
import statistics
from typing import Any, Iterable


N = 5_000
PRIMARY = "size_0.50_entry_25_sellable_500"
SIZE_KEYS = {
    "0.01": "size_0.01_entry_25_sellable_500",
    "0.10": "size_0.10_entry_25_sellable_500",
    "0.50": PRIMARY,
    "1.00": "size_1.00_entry_25_sellable_500",
}
FIXED_STATES = (
    "FIXED_25MS",
    "FIXED_50MS",
    "FIXED_100MS",
    "FIXED_250MS",
    "FIXED_500MS",
    "FIXED_1000MS",
    "FIXED_2000MS",
    "FIXED_5000MS",
    "FIXED_10000MS",
)
BLOCKS = (
    ("A_1_1000", 1, 1000),
    ("B_1001_2000", 1001, 2000),
    ("C_2001_3000", 2001, 3000),
    ("D_3001_4000", 3001, 4000),
    ("E_4001_5000", 4001, 5000),
    ("EARLY_1_3000", 1, 3000),
    ("LATER_3001_5000", 3001, 5000),
)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def write_json(path: pathlib.Path, value: Any) -> None:
    if path.exists():
        raise RuntimeError(f"IMMUTABLE_OUTPUT_EXISTS:{path}")
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_jsonl_gz(path: pathlib.Path, rows: Iterable[dict[str, Any]]) -> None:
    if path.exists():
        raise RuntimeError(f"IMMUTABLE_OUTPUT_EXISTS:{path}")
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            with io.TextIOWrapper(zipped, encoding="utf-8") as text:
                for row in rows:
                    text.write(json.dumps(row, sort_keys=True) + "\n")


def load_prefix(path: pathlib.Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != N or [row["ordinal"] for row in rows] != list(
        range(1, N + 1)
    ):
        raise RuntimeError("N5000_PREFIX_INTEGRITY")
    return rows


def load_rows(
    research_root: pathlib.Path, prefix_mints: set[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((research_root / "rows-v2").glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("mint") not in prefix_mints:
                    continue
                if row.get("labels", {}).get(PRIMARY, {}).get("status") != "EXACT":
                    continue
                row["labels"] = {
                    key: value
                    for key, value in row["labels"].items()
                    if key in set(SIZE_KEYS.values())
                }
                rows.append(row)
    return rows


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def quantiles(values: Iterable[float]) -> list[float]:
    unique = sorted(set(values))
    if len(unique) < 5:
        return unique
    return sorted(
        {
            unique[min(len(unique) - 1, int((len(unique) - 1) * fraction))]
            for fraction in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
        }
    )


def quantile_value(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def metric(rows: Iterable[dict[str, Any]], key: str) -> dict[str, Any]:
    rows = list(rows)
    if len(rows) != len({row["mint"] for row in rows}):
        raise RuntimeError("PSEUDOREPLICATION_BLOCKER")
    labels = [
        row["labels"][key]
        for row in rows
        if row.get("labels", {}).get(key, {}).get("status") == "EXACT"
    ]
    returns = [float(cell["returnPct"]) for cell in labels]
    pnls = [float(cell["pnlSol"]) for cell in labels]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    loss_sum = sum(losses)
    return {
        "matchUniqueMintN": len(rows),
        "passN": len(labels),
        "executableShareOfMatches": len(labels) / len(rows) if rows else None,
        "TP": sum(cell.get("outcome") == "TP" for cell in labels),
        "SL": sum(cell.get("outcome") == "SL" for cell in labels),
        "MAX_HOLD": sum(cell.get("outcome") == "MAX_HOLD" for cell in labels),
        "WR": len(wins) / len(labels) if labels else None,
        "PF": (
            sum(wins) / abs(loss_sum)
            if losses and loss_sum
            else None
        ),
        "expectancyReturnPct": statistics.mean(returns) if returns else None,
        "totalPnlSol": sum(pnls),
        "medianRoiPct": statistics.median(returns) if returns else None,
        "worstRoiPct": min(returns) if returns else None,
        "bestRoiPct": max(returns) if returns else None,
    }


def is_positive(value: dict[str, Any]) -> bool:
    return (
        isinstance(value.get("PF"), (int, float))
        and value["PF"] > 1
        and isinstance(value.get("expectancyReturnPct"), (int, float))
        and value["expectancyReturnPct"] > 0
    )


def clauses_key(state: str, clauses: list[list[Any]]) -> str:
    canonical = sorted(
        [[str(key), str(op), float(threshold)] for key, op, threshold in clauses]
    )
    return json.dumps([state, canonical], separators=(",", ":"))


def cohort_hash(mints: set[str]) -> str:
    return hashlib.sha256("\n".join(sorted(mints)).encode()).hexdigest()


def selected_rows(
    state_rows: dict[str, dict[str, dict[str, Any]]],
    state: str,
    clauses: list[list[Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    available = state_rows.get(state, {})
    chosen: list[dict[str, Any]] = []
    mints: set[str] = set()
    for mint, row in available.items():
        passed = True
        for key, op, threshold in clauses:
            value = row["features"].get(key)
            if not finite(value):
                passed = False
                break
            if op == "ge" and value < threshold:
                passed = False
                break
            if op == "le" and value > threshold:
                passed = False
                break
        if passed:
            chosen.append(row)
            mints.add(mint)
    chosen.sort(key=lambda row: (row["decisionUnixMs"], row["mint"]))
    return chosen, mints


def generate_universe(
    state_rows: dict[str, dict[str, dict[str, Any]]]
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], list[float]], list[dict[str, Any]]]:
    min_n = max(50, int(N * 0.02))
    one: list[dict[str, Any]] = []
    thresholds: dict[tuple[str, str], list[float]] = {}
    for state, by_mint in state_rows.items():
        rows = list(by_mint.values())
        keys = sorted(
            {
                key
                for row in rows
                for key, value in row["features"].items()
                if finite(value) and key != "mintAgeMs"
            }
        )
        for key in keys:
            values = [
                float(row["features"][key])
                for row in rows
                if finite(row["features"].get(key))
            ]
            q_values = quantiles(values)
            thresholds[(state, key)] = q_values
            for threshold in q_values:
                for op in ("ge", "le"):
                    selected, mints = selected_rows(
                        state_rows, state, [[key, op, threshold]]
                    )
                    if len(selected) < min_n:
                        continue
                    primary = metric(selected, PRIMARY)
                    one.append(
                        {
                            "kind": "ONE_FEATURE",
                            "state": state,
                            "clauses": [[key, op, threshold]],
                            "mints": mints,
                            "scannerScore": (primary["PF"] or 0)
                            * math.sqrt(len(selected)),
                        }
                    )
    one.sort(key=lambda row: row["scannerScore"], reverse=True)
    candidates = list(one)
    base = one[:20]
    for left, right in itertools.combinations(base[:12], 2):
        if left["state"] != right["state"]:
            continue
        clauses = left["clauses"] + right["clauses"]
        selected, mints = selected_rows(state_rows, left["state"], clauses)
        if len(selected) >= min_n:
            primary = metric(selected, PRIMARY)
            candidates.append(
                {
                    "kind": "TWO_FEATURE",
                    "state": left["state"],
                    "clauses": clauses,
                    "mints": mints,
                    "scannerScore": (primary["PF"] or 0)
                    * math.sqrt(len(selected)),
                }
            )
    for group in itertools.combinations(base[:8], 3):
        if len({item["state"] for item in group}) != 1:
            continue
        clauses = sum((item["clauses"] for item in group), [])
        if len({item[0] for item in clauses}) < 3:
            continue
        state = group[0]["state"]
        selected, mints = selected_rows(state_rows, state, clauses)
        if len(selected) >= max(300, min_n):
            primary = metric(selected, PRIMARY)
            candidates.append(
                {
                    "kind": "THREE_FEATURE",
                    "state": state,
                    "clauses": clauses,
                    "mints": mints,
                    "scannerScore": (primary["PF"] or 0)
                    * math.sqrt(len(selected)),
                }
            )
    candidates.sort(key=lambda row: row["scannerScore"], reverse=True)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        identity = clauses_key(candidate["state"], candidate["clauses"])
        if identity in seen:
            continue
        seen.add(identity)
        candidate["candidateId"] = "C-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        candidate["cohortSha256"] = cohort_hash(candidate["mints"])
        deduped.append(candidate)
    return deduped, thresholds, base


def corr(values: list[float | None]) -> float | None:
    pairs = [(index, value) for index, value in enumerate(values) if value is not None]
    if len(pairs) < 3:
        return None
    xs = [float(index) for index, _ in pairs]
    ys = [float(value) for _, value in pairs]
    x_mean = statistics.mean(xs)
    y_mean = statistics.mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - x_mean) ** 2 for x in xs)
        * sum((y - y_mean) ** 2 for y in ys)
    )
    return numerator / denominator if denominator else None


def classify_shape(
    economics_05: list[dict[str, Any]], economics_10: list[dict[str, Any]]
) -> tuple[str, str | None, dict[str, Any]]:
    e05 = [row.get("expectancyReturnPct") for row in economics_05]
    e10 = [row.get("expectancyReturnPct") for row in economics_10]
    c05, c10 = corr(e05), corr(e10)
    direction = None
    coherent = False
    if c05 is not None and c10 is not None and c05 * c10 > 0:
        direction = "HIGHER_BETTER" if c05 > 0 else "LOWER_BETTER"
        coherent = abs(c05) >= 0.65 and abs(c10) >= 0.65
    joint_positive = [
        bool(is_positive(left) and is_positive(right))
        for left, right in zip(economics_05, economics_10)
    ]
    positive_indices = [index for index, value in enumerate(joint_positive) if value]
    knife_edge = (
        len(positive_indices) == 1
        and positive_indices[0] not in (0, len(joint_positive) - 1)
    )
    classification = (
        "MONOTONIC_OR_COHERENT"
        if coherent
        else "KNIFE_EDGE"
        if knife_edge
        else "IRREGULAR"
    )
    return classification, direction, {
        "correlationLowToHigh0p5": c05,
        "correlationLowToHigh1p0": c10,
        "jointPositiveBuckets": positive_indices,
    }


def factor_shapes(
    state_rows: dict[str, dict[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for state, by_mint in state_rows.items():
        rows = list(by_mint.values())
        keys = sorted(
            {
                key
                for row in rows
                for key, value in row["features"].items()
                if finite(value) and key != "mintAgeMs"
            }
        )
        for key in keys:
            ordered = sorted(
                [row for row in rows if finite(row["features"].get(key))],
                key=lambda row: (row["features"][key], row["mint"]),
            )
            buckets = []
            econ05: list[dict[str, Any]] = []
            econ10: list[dict[str, Any]] = []
            for index in range(5):
                part = ordered[len(ordered) * index // 5 : len(ordered) * (index + 1) // 5]
                m05 = metric(part, SIZE_KEYS["0.50"])
                m10 = metric(part, SIZE_KEYS["1.00"])
                econ05.append(m05)
                econ10.append(m10)
                buckets.append(
                    {
                        "bucket": index + 1,
                        "range": {
                            "min": part[0]["features"][key] if part else None,
                            "max": part[-1]["features"][key] if part else None,
                        },
                        "uniqueMintN": len(part),
                        "economics": {"0.50": m05, "1.00": m10},
                    }
                )
            classification, direction, diagnostics = classify_shape(econ05, econ10)
            output.append(
                {
                    "state": state,
                    "feature": key,
                    "numericUniqueMintN": len(ordered),
                    "classification": classification,
                    "direction": direction,
                    "diagnostics": diagnostics,
                    "bucketsLowToHigh": buckets,
                }
            )
    return output


def outlier_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    exact = [
        row
        for row in rows
        if row.get("labels", {}).get(key, {}).get("status") == "EXACT"
    ]
    ordered = sorted(
        exact, key=lambda row: float(row["labels"][key]["pnlSol"]), reverse=True
    )
    total = sum(float(row["labels"][key]["pnlSol"]) for row in ordered)

    def top_share(count: int) -> float | None:
        if total <= 0:
            return None
        return sum(
            float(row["labels"][key]["pnlSol"]) for row in ordered[:count]
        ) / total

    after1 = metric(ordered[1:], key)
    after3 = metric(ordered[3:], key)
    after5 = metric(ordered[5:], key)
    return {
        "bestTradeShareOfTotalPnl": top_share(1),
        "best3ShareOfTotalPnl": top_share(3),
        "best5ShareOfTotalPnl": top_share(5),
        "afterRemovingBestTrade": after1,
        "afterRemovingBest3": after3,
        "afterRemovingBest5": after5,
    }


def capacity_flag(economics: dict[str, dict[str, Any]]) -> str:
    positive = {size: is_positive(value) for size, value in economics.items()}
    if positive["0.10"] and not positive["0.50"]:
        return "TINY_SIZE_ONLY"
    if positive["0.50"] and not positive["1.00"]:
        return "COLLAPSES_AT_1_SOL"
    if positive["0.10"] and positive["0.50"] and positive["1.00"]:
        pf05 = economics["0.50"].get("PF") or 0
        pf10 = economics["1.00"].get("PF") or 0
        if pf10 < pf05 * 0.8:
            return "CAPACITY_LIMITED"
        return "SIZE_ROBUST"
    return "NO_SIZE_ROBUST_ALPHA"


def chronological(
    rows: list[dict[str, Any]], ordinal_by_mint: dict[str, int]
) -> tuple[dict[str, Any], str, int]:
    result: dict[str, Any] = {}
    for name, start, end in BLOCKS:
        part = [
            row
            for row in rows
            if start <= ordinal_by_mint[row["mint"]] <= end
        ]
        result[name] = {
            "matchingUniqueN": len(part),
            "0.50": metric(part, SIZE_KEYS["0.50"]),
            "1.00": metric(part, SIZE_KEYS["1.00"]),
        }
    five = [result[name] for name, _, _ in BLOCKS[:5]]
    positive_blocks = sum(
        is_positive(block["0.50"]) and is_positive(block["1.00"])
        for block in five
    )
    early = result["EARLY_1_3000"]
    later = result["LATER_3001_5000"]
    early_positive = is_positive(early["0.50"]) and is_positive(early["1.00"])
    later_positive = is_positive(later["0.50"]) and is_positive(later["1.00"])
    if positive_blocks == 5 and early_positive and later_positive:
        classification = "CHRONOLOGICALLY_STRONG"
    elif positive_blocks >= 4 and later_positive:
        classification = "MOSTLY_CONSISTENT"
    elif early_positive and not later_positive:
        classification = "EARLY_ONLY"
    elif later_positive and not early_positive:
        classification = "LATE_ONLY"
    elif positive_blocks >= 2:
        classification = "REGIME_SENSITIVE"
    else:
        classification = "UNSTABLE"
    return result, classification, positive_blocks


def nearby_stability(
    candidate: dict[str, Any],
    state_rows: dict[str, dict[str, dict[str, Any]]],
    thresholds: dict[tuple[str, str], list[float]],
) -> dict[str, Any]:
    base_rows, _ = selected_rows(
        state_rows, candidate["state"], candidate["clauses"]
    )
    base_econ = {
        "0.50": metric(base_rows, SIZE_KEYS["0.50"]),
        "1.00": metric(base_rows, SIZE_KEYS["1.00"]),
    }
    variants: list[dict[str, Any]] = []
    for clause_index, (key, op, threshold) in enumerate(candidate["clauses"]):
        values = thresholds.get((candidate["state"], key), [])
        if threshold not in values:
            continue
        index = values.index(threshold)
        for neighbor_index in (index - 1, index + 1):
            if not 0 <= neighbor_index < len(values):
                continue
            clauses = [list(item) for item in candidate["clauses"]]
            clauses[clause_index][2] = values[neighbor_index]
            rows, _ = selected_rows(state_rows, candidate["state"], clauses)
            if len(rows) < 100:
                continue
            econ = {
                "0.50": metric(rows, SIZE_KEYS["0.50"]),
                "1.00": metric(rows, SIZE_KEYS["1.00"]),
            }
            variants.append(
                {
                    "kind": "NEARBY_THRESHOLD",
                    "changedClauseIndex": clause_index,
                    "state": candidate["state"],
                    "clauses": clauses,
                    "matchingUniqueN": len(rows),
                    "economics": econ,
                    "positiveBoth": is_positive(econ["0.50"])
                    and is_positive(econ["1.00"]),
                }
            )
    if candidate["state"] in FIXED_STATES:
        state_index = FIXED_STATES.index(candidate["state"])
        for neighbor_index in (state_index - 1, state_index + 1):
            if not 0 <= neighbor_index < len(FIXED_STATES):
                continue
            state = FIXED_STATES[neighbor_index]
            rows, _ = selected_rows(state_rows, state, candidate["clauses"])
            if len(rows) < 100:
                continue
            econ = {
                "0.50": metric(rows, SIZE_KEYS["0.50"]),
                "1.00": metric(rows, SIZE_KEYS["1.00"]),
            }
            variants.append(
                {
                    "kind": "NEARBY_CHECKPOINT",
                    "state": state,
                    "clauses": candidate["clauses"],
                    "matchingUniqueN": len(rows),
                    "economics": econ,
                    "positiveBoth": is_positive(econ["0.50"])
                    and is_positive(econ["1.00"]),
                }
            )
    survival = (
        sum(item["positiveBoth"] for item in variants) / len(variants)
        if variants
        else None
    )
    base_positive = is_positive(base_econ["0.50"]) and is_positive(
        base_econ["1.00"]
    )
    if not base_positive:
        classification = "BRITTLE"
    elif survival is None:
        classification = "MODERATELY_STABLE"
    elif survival >= 0.75:
        classification = "STABLE"
    elif survival >= 0.5:
        classification = "MODERATELY_STABLE"
    else:
        classification = "BRITTLE"
    return {
        "classification": classification,
        "nearbyVariantN": len(variants),
        "positiveBothShare": survival,
        "variants": variants,
    }


def selectivity(rows: list[dict[str, Any]], coverage_hours: float) -> dict[str, Any]:
    ages = sorted(float(row["features"]["mintAgeMs"]) for row in rows)
    median = statistics.median(ages) if ages else None
    if not ages:
        window = None
    elif median < 100:
        window = "BEFORE_100MS"
    elif median < 250:
        window = "100_250MS"
    elif median < 500:
        window = "250_500MS"
    else:
        window = "LATER"
    return {
        "matchesPer1000Launches": len(rows) / 5,
        "matchesPerHour": len(rows) / coverage_hours if coverage_hours else None,
        "decisionAgeMs": {
            "p25": quantile_value(ages, 0.25),
            "p50": quantile_value(ages, 0.50),
            "p75": quantile_value(ages, 0.75),
            "median": median,
        },
        "timingClass": window,
    }


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def balanced_score(
    economics: dict[str, dict[str, Any]],
    chronology: dict[str, Any],
    positive_blocks: int,
    stability: dict[str, Any],
    outliers: dict[str, Any],
    match_n: int,
) -> tuple[float, dict[str, Any]]:
    exp_values = [
        economics[size].get("expectancyReturnPct")
        for size in ("0.50", "1.00")
    ]
    pf_values = [economics[size].get("PF") for size in ("0.50", "1.00")]
    min_exp = min(value if value is not None else -100 for value in exp_values)
    min_pf = min(value if value is not None else 0 for value in pf_values)
    later = chronology["LATER_3001_5000"]
    later_positive = is_positive(later["0.50"]) and is_positive(later["1.00"])
    stability_share = stability.get("positiveBothShare")
    stability_value = stability_share if stability_share is not None else 0.5
    outlier_survives = all(
        is_positive(outliers[size]["afterRemovingBest3"])
        for size in ("0.50", "1.00")
    )
    size_robust = capacity_flag(economics) == "SIZE_ROBUST"
    components = {
        "minimumPrimaryExpectancy": 30 * clamp(min_exp / 2.0, -2, 2),
        "minimumPrimaryPfMargin": 15 * clamp((min_pf - 1) / 0.25, -2, 2),
        "positiveChronologicalBlocks": 15 * positive_blocks / 5,
        "laterPositiveBoth": 10 if later_positive else 0,
        "independentN": 10 * math.log1p(match_n) / math.log1p(N),
        "nearbyStability": 10 * stability_value,
        "outlierResistance": 5 if outlier_survives else 0,
        "sizeRobustness": 5 if size_robust else 0,
    }
    return sum(components.values()), components


def deep_candidate(
    candidate: dict[str, Any],
    state_rows: dict[str, dict[str, dict[str, Any]]],
    thresholds: dict[tuple[str, str], list[float]],
    ordinal_by_mint: dict[str, int],
    coverage_hours: float,
    shape_by_state_feature: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    rows, mints = selected_rows(
        state_rows, candidate["state"], candidate["clauses"]
    )
    economics = {
        size: metric(rows, key) for size, key in SIZE_KEYS.items()
    }
    blocks, chrono_class, positive_blocks = chronological(rows, ordinal_by_mint)
    outliers = {
        size: outlier_metrics(rows, SIZE_KEYS[size]) for size in ("0.50", "1.00")
    }
    outlier_driven = any(
        not is_positive(outliers[size]["afterRemovingBest3"])
        for size in ("0.50", "1.00")
    )
    stability = nearby_stability(candidate, state_rows, thresholds)
    score, components = balanced_score(
        economics,
        blocks,
        positive_blocks,
        stability,
        outliers,
        len(rows),
    )
    aggregate_both = is_positive(economics["0.50"]) and is_positive(
        economics["1.00"]
    )
    later = blocks["LATER_3001_5000"]
    later_both = is_positive(later["0.50"]) and is_positive(later["1.00"])
    serious = (
        aggregate_both
        and later_both
        and positive_blocks >= 3
        and not outlier_driven
        and stability["classification"] != "BRITTLE"
        and capacity_flag(economics) == "SIZE_ROBUST"
    )
    feature_shapes = [
        {
            "feature": key,
            "shape": (
                shape_by_state_feature.get((candidate["state"], key), {}).get(
                    "classification"
                )
            ),
            "direction": (
                shape_by_state_feature.get((candidate["state"], key), {}).get(
                    "direction"
                )
            ),
        }
        for key, _, _ in candidate["clauses"]
    ]
    return {
        "candidateId": candidate["candidateId"],
        "kind": candidate["kind"],
        "state": candidate["state"],
        "clauses": candidate["clauses"],
        "cohortSha256": cohort_hash(mints),
        "matchingUniqueMintN": len(rows),
        "frequencyOfN5000": len(rows) / N,
        "scannerScore": candidate["scannerScore"],
        "balancedScore": score,
        "balancedScoreComponents": components,
        "economics": economics,
        "capacityClassification": capacity_flag(economics),
        "chronology": blocks,
        "chronologyClassification": chrono_class,
        "positiveBothPrimary1kBlocks": positive_blocks,
        "outlier": outliers,
        "outlierClassification": "OUTLIER_DRIVEN"
        if outlier_driven
        else "OUTLIER_RESILIENT",
        "nearbyStability": stability,
        "selectivity": selectivity(rows, coverage_hours),
        "featureShapes": feature_shapes,
        "aggregatePositiveBothPrimarySizes": aggregate_both,
        "later3001To5000PositiveBothPrimarySizes": later_both,
        "seriousLead": serious,
    }


def jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def compact_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"chronology", "outlier", "nearbyStability"}
    } | {
        "chronologyClassification": row["chronologyClassification"],
        "positiveBothPrimary1kBlocks": row["positiveBothPrimary1kBlocks"],
        "later3001To5000": row["chronology"]["LATER_3001_5000"],
        "outlierClassification": row["outlierClassification"],
        "outlier": row["outlier"],
        "nearbyStability": row["nearbyStability"],
    }


def frozen_rules(
    path: pathlib.Path,
    state_rows: dict[str, dict[str, dict[str, Any]]],
    ordinal_by_mint: dict[str, int],
    coverage_hours: float,
) -> dict[str, Any]:
    source = json.loads(path.read_text(encoding="utf-8"))
    results = []
    for name, definition in sorted(source["rules"].items()):
        rows, mints = selected_rows(
            state_rows, definition["state"], definition["clauses"]
        )
        economics = {
            size: metric(rows, key) for size, key in SIZE_KEYS.items()
        }
        blocks, classification, positive_blocks = chronological(
            rows, ordinal_by_mint
        )
        outlier = {
            size: outlier_metrics(rows, SIZE_KEYS[size])
            for size in ("0.50", "1.00")
        }
        results.append(
            {
                "name": name,
                "state": definition["state"],
                "clauses": definition["clauses"],
                "matchingUniqueMintN": len(rows),
                "cohortSha256": cohort_hash(mints),
                "selectivity": selectivity(rows, coverage_hours),
                "economics": economics,
                "chronology": blocks,
                "chronologyClassification": classification,
                "positiveBothPrimary1kBlocks": positive_blocks,
                "outlier": outlier,
                "recovered": (
                    is_positive(economics["0.50"])
                    and is_positive(economics["1.00"])
                    and is_positive(blocks["LATER_3001_5000"]["0.50"])
                    and is_positive(blocks["LATER_3001_5000"]["1.00"])
                ),
            }
        )
    return {
        "sourcePath": str(path),
        "sourceSha256": sha256_file(path),
        "retuned": False,
        "results": results,
    }


def scanner_parity(
    scanner_report: dict[str, Any],
    deep_by_identity: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    comparisons = []
    for frozen in scanner_report.get("top10") or []:
        identity = clauses_key(frozen["state"], frozen["clauses"])
        ours = deep_by_identity.get(identity)
        frozen_econ = frozen["economics"]["normal0p5"]
        ours_econ = ours["economics"]["0.50"] if ours else {}
        comparisons.append(
            {
                "state": frozen["state"],
                "clauses": frozen["clauses"],
                "present": ours is not None,
                "frozenN": frozen_econ.get("N"),
                "ourN": ours_econ.get("passN"),
                "frozenPF": frozen_econ.get("PF"),
                "ourPF": ours_econ.get("PF"),
                "metricMatch": (
                    ours is not None
                    and frozen_econ.get("N") == ours_econ.get("passN")
                    and math.isclose(
                        float(frozen_econ.get("PF") or 0),
                        float(ours_econ.get("PF") or 0),
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                ),
            }
        )
    return {
        "allFrozenTop10PresentAndMetricMatched": all(
            item["present"] and item["metricMatch"] for item in comparisons
        ),
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("integrity_dir", type=pathlib.Path)
    parser.add_argument("scanner_report", type=pathlib.Path)
    parser.add_argument("frozen_rules", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError(f"IMMUTABLE_OUTPUT_EXISTS:{args.output}")
    args.output.mkdir(parents=True)

    gate = json.loads((args.integrity_dir / "integrity-gate.json").read_text())
    if gate.get("classification") != "PASS":
        raise RuntimeError("INTEGRITY_GATE_NOT_PASS")
    prefix = load_prefix(args.integrity_dir / "n5000-prefix.jsonl")
    prefix_mints = {row["mint"] for row in prefix}
    ordinal_by_mint = {row["mint"]: row["ordinal"] for row in prefix}
    rows = load_rows(args.research_root, prefix_mints)
    state_rows: dict[str, dict[str, dict[str, Any]]] = collections.defaultdict(dict)
    for row in sorted(rows, key=lambda item: (item["decisionUnixMs"], item["mint"])):
        state_rows[row["observationState"]].setdefault(row["mint"], row)

    shapes = factor_shapes(state_rows)
    shape_map = {(row["state"], row["feature"]): row for row in shapes}
    candidates, thresholds, scanner_base = generate_universe(state_rows)
    coverage_hours = (
        max(row["createReceivedAtUnixNs"] for row in prefix)
        - min(row["createReceivedAtUnixNs"] for row in prefix)
    ) / 1e9 / 3600

    deep_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        deep_rows.append(
            deep_candidate(
                candidate,
                state_rows,
                thresholds,
                ordinal_by_mint,
                coverage_hours,
                shape_map,
            )
        )
    deep_rows.sort(key=lambda row: row["balancedScore"], reverse=True)
    deep_by_identity = {
        clauses_key(row["state"], row["clauses"]): row for row in deep_rows
    }

    distinct: list[dict[str, Any]] = []
    candidate_mints = {
        candidate["candidateId"]: candidate["mints"] for candidate in candidates
    }
    collapsed = []
    for row in deep_rows:
        mints = candidate_mints[row["candidateId"]]
        duplicate = None
        for kept in distinct:
            overlap = jaccard(mints, candidate_mints[kept["candidateId"]])
            if overlap >= 0.90:
                duplicate = (kept["candidateId"], overlap)
                break
        if duplicate:
            collapsed.append(
                {
                    "candidateId": row["candidateId"],
                    "collapsedInto": duplicate[0],
                    "jaccard": duplicate[1],
                }
            )
            continue
        distinct.append(row)
        if len(distinct) >= 15:
            break

    overlaps = []
    for left, right in itertools.combinations(distinct, 2):
        overlaps.append(
            {
                "left": left["candidateId"],
                "right": right["candidateId"],
                "jaccard": jaccard(
                    candidate_mints[left["candidateId"]],
                    candidate_mints[right["candidateId"]],
                ),
            }
        )
    serious = [row for row in deep_rows if row["seriousLead"]]
    promising = [
        row
        for row in deep_rows
        if row["aggregatePositiveBothPrimarySizes"]
        and row["later3001To5000PositiveBothPrimarySizes"]
        and row["positiveBothPrimary1kBlocks"] >= 2
        and row["outlierClassification"] != "OUTLIER_DRIVEN"
    ]
    single_ranked = [
        row for row in deep_rows if row["kind"] == "ONE_FEATURE"
    ][:50]
    old_rules = frozen_rules(
        args.frozen_rules, state_rows, ordinal_by_mint, coverage_hours
    )
    scanner_report = json.loads(args.scanner_report.read_text(encoding="utf-8"))
    if scanner_report.get("checkpointPrefixSha256") != gate["prefix"][
        "checkpointPrefixSha256"
    ]:
        raise RuntimeError("SCANNER_PREFIX_HASH_MISMATCH")
    parity = scanner_parity(scanner_report, deep_by_identity)
    if not parity["allFrozenTop10PresentAndMetricMatched"]:
        raise RuntimeError("FROZEN_SCANNER_PARITY_FAILURE")

    write_jsonl_gz(args.output / "factor-shapes.jsonl.gz", shapes)
    write_jsonl_gz(args.output / "complete-candidates.jsonl.gz", deep_rows)
    write_json(
        args.output / "distinct-candidates.json",
        {
            "schema": "PUMP_EXACT_ALPHA_N5000_DISTINCT_CANDIDATES_V1",
            "dedupeRule": "greedy balanced-rank; collapse Jaccard cohort overlap >=0.90",
            "top15": [compact_candidate(row) for row in distinct],
            "overlap": overlaps,
            "collapsedExamples": collapsed[:100],
        },
    )
    write_json(
        args.output / "chronological-blocks.json",
        {
            "schema": "PUMP_EXACT_ALPHA_N5000_CHRONOLOGY_V1",
            "blocks": [
                {"name": name, "startOrdinal": start, "endOrdinal": end}
                for name, start, end in BLOCKS
            ],
            "top15": [
                {
                    "candidateId": row["candidateId"],
                    "state": row["state"],
                    "clauses": row["clauses"],
                    "classification": row["chronologyClassification"],
                    "positiveBothPrimary1kBlocks": row[
                        "positiveBothPrimary1kBlocks"
                    ],
                    "chronology": row["chronology"],
                }
                for row in distinct
            ],
        },
    )
    write_json(args.output / "n3000-frozen-rules-at-n5000.json", old_rules)

    verdict = (
        "N5000_STRONG_SELECTIVE_ALPHA_FOUND"
        if serious
        else "N5000_PROMISING_ALPHA_LEADS_NEED_MORE_N"
        if promising
        else "N5000_WEAK_OR_REGIME_SENSITIVE_LEADS_ONLY"
        if any(row["aggregatePositiveBothPrimarySizes"] for row in deep_rows)
        else "N5000_NO_CONVINCING_ALPHA_IN_FROZEN_V1"
    )
    summary = {
        "schema": "PUMP_EXACT_ALPHA_N5000_COMPLETE_ANALYSIS_V1",
        "generatedAtUtc": dt.datetime.now(dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "verdict": verdict,
        "checkpointN": N,
        "prefixSha256": gate["prefix"]["checkpointPrefixSha256"],
        "integrityGateSha256": sha256_file(
            args.integrity_dir / "integrity-gate.json"
        ),
        "frozenScanner": {
            "path": str(args.scanner_report),
            "artifactSha256": sha256_file(args.scanner_report),
            "sourceFingerprintSha256": scanner_report.get(
                "sourceFingerprintSha256"
            ),
            "scannerCodeSha256": gate["semanticFreeze"]["codeHashes"][
                "scanPumpExactAlphaFingerprintsV1.py"
            ]["actual"],
            "parity": parity,
        },
        "population": {
            "exactAdmittedPrefixMints": N,
            "materializedMints": gate["accounting"]["materializedUniqueMints"],
            "accountedExcludedMints": gate["accounting"][
                "accountedExcludedMints"
            ],
            "primaryExecutableMints": scanner_report["population"][
                "primaryExecutableMints"
            ],
            "opportunityRows": len(rows),
            "coverageSpanHours": coverage_hours,
            "states": {
                state: len(by_mint) for state, by_mint in state_rows.items()
            },
        },
        "frozenUniverse": {
            "oneFeatureCandidateN": sum(
                row["kind"] == "ONE_FEATURE" for row in candidates
            ),
            "twoFeatureCandidateN": sum(
                row["kind"] == "TWO_FEATURE" for row in candidates
            ),
            "threeFeatureCandidateN": sum(
                row["kind"] == "THREE_FEATURE" for row in candidates
            ),
            "totalCandidateN": len(candidates),
            "factorShapeN": len(shapes),
            "scannerBaseTop20": [
                {
                    "state": row["state"],
                    "clauses": row["clauses"],
                    "scannerScore": row["scannerScore"],
                    "matchingUniqueMintN": len(row["mints"]),
                }
                for row in scanner_base
            ],
        },
        "balancedScoreFormula": {
            "minimumPrimaryExpectancy": "30*clip(min(E0.5,E1.0)/2pct,-2,2)",
            "minimumPrimaryPfMargin": "15*clip((min(PF0.5,PF1.0)-1)/0.25,-2,2)",
            "positiveChronologicalBlocks": "15*positive_both_1k_blocks/5",
            "laterPositiveBoth": "10 if ordinals 3001-5000 positive at both sizes",
            "independentN": "10*log1p(N)/log1p(5000)",
            "nearbyStability": "10*positive_both_neighbor_share",
            "outlierResistance": "5 if positive both after best 3 removed",
            "sizeRobustness": "5 if 0.1,0.5,1.0 all positive and 1.0 PF >=80% of 0.5 PF",
        },
        "top15Distinct": [compact_candidate(row) for row in distinct],
        "top5SeriousLeads": [compact_candidate(row) for row in serious[:5]],
        "top3MostCredible": [compact_candidate(row) for row in serious[:3]],
        "top5PromisingButNotSerious": [
            compact_candidate(row) for row in promising[:5]
        ],
        "topSingleFeatureViews": [
            compact_candidate(row) for row in single_ranked
        ],
        "n3000FrozenRules": old_rules,
        "outcomeRowsAboveOrdinal5000RetainedOrReported": 0,
        "validationRowsInspected": 0,
        "holdoutRowsInspected": 0,
        "newFeaturesAdded": 0,
        "thresholdsRetuned": False,
        "liveOrShadowActions": 0,
    }
    write_json(args.output / "analysis-summary.json", summary)
    write_json(
        args.output / "code-identity.json",
        {
            "analysisHarness": {
                "path": str(pathlib.Path(__file__)),
                "sha256": sha256_file(pathlib.Path(__file__)),
            },
            "frozenScannerCodeSha256": summary["frozenScanner"][
                "scannerCodeSha256"
            ],
            "correctedFreezeSha256": gate["semanticFreeze"]["sha256"],
            "checkpointPrefixSha256": summary["prefixSha256"],
            "frozenScannerArtifactSha256": summary["frozenScanner"][
                "artifactSha256"
            ],
        },
    )
    checksums = []
    for path in sorted(args.output.iterdir()):
        if path.name == "SHA256SUMS":
            continue
        checksums.append(f"{sha256_file(path)}  {path.name}")
    (args.output / "SHA256SUMS").write_text(
        "\n".join(checksums) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "verdict": verdict,
                "candidateN": len(candidates),
                "factorShapeN": len(shapes),
                "seriousLeadN": len(serious),
                "promisingLeadN": len(promising),
                "top15": [
                    {
                        "candidateId": row["candidateId"],
                        "kind": row["kind"],
                        "state": row["state"],
                        "clauses": row["clauses"],
                        "N": row["matchingUniqueMintN"],
                        "PF0p5": row["economics"]["0.50"]["PF"],
                        "E0p5Pct": row["economics"]["0.50"][
                            "expectancyReturnPct"
                        ],
                        "PF1p0": row["economics"]["1.00"]["PF"],
                        "E1p0Pct": row["economics"]["1.00"][
                            "expectancyReturnPct"
                        ],
                        "laterPositiveBoth": row[
                            "later3001To5000PositiveBothPrimarySizes"
                        ],
                        "positiveBlocks": row[
                            "positiveBothPrimary1kBlocks"
                        ],
                        "chronology": row["chronologyClassification"],
                        "stability": row["nearbyStability"]["classification"],
                        "outlier": row["outlierClassification"],
                    }
                    for row in distinct
                ],
                "output": str(args.output),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
