#!/usr/bin/env python3
"""Audit a sealed kEFi paper result for concentration and reconstruction bias.

This tool never changes the entry rule or reruns model selection. It joins the
sealed paper records to their frozen opportunity identities and reports whether
the observed result is broad across source partitions and mints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


SCHEMA = "KEFI_CAPTURE_FIRST_ECONOMIC_STABILITY_AUDIT_V1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def stable_pick(identity: tuple[str, int]) -> int:
    return int(hashlib.sha256("|".join(map(str, identity)).encode()).hexdigest()[:16], 16)


def aggregate(records: list[dict]) -> dict:
    if not records:
        return {"n": 0, "total_pnl_lamports": None, "mean_return_bps": None, "median_return_bps": None,
                "win_rate": None, "profit_factor": None, "maximum_drawdown_lamports": None,
                "top_1_pnl_share": None, "top_5_pnl_share": None}
    returns = [record["return_bps"] for record in records]
    pnl = [record["pnl_lamports"] for record in sorted(records, key=lambda value: value["entry_time_ns"])]
    gains = sum(value for value in pnl if value > 0)
    losses = -sum(value for value in pnl if value < 0)
    equity = peak = drawdown = 0
    for value in pnl:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    positive = sorted((value for value in pnl if value > 0), reverse=True)
    return {
        "n": len(records),
        "total_pnl_lamports": sum(pnl),
        "mean_return_bps": sum(returns) / len(returns),
        "median_return_bps": median(returns),
        "win_rate": sum(value > 0 for value in returns) / len(returns),
        "profit_factor": gains / losses if losses else None,
        "maximum_drawdown_lamports": drawdown,
        "top_1_pnl_share": positive[0] / gains if gains else None,
        "top_5_pnl_share": sum(positive[:5]) / gains if gains else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--opportunities", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--expected-result-sha256", required=True)
    parser.add_argument("--match-field", default="crowded_impulse_match")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    result_sha = sha256(args.result)
    if result_sha != args.expected_result_sha256:
        raise SystemExit("FAIL_CLOSED: result hash mismatch")
    result = json.loads(args.result.read_text())
    if result.get("status") != "COMPLETE_STRICT_RECONSTRUCTION":
        raise SystemExit("FAIL_CLOSED: paper result is not complete")

    opportunities: dict[tuple[str, int], dict] = {}
    with args.opportunities.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            identity = (str(row["candidate_signature"]), int(row.get("event_index", 0)))
            if identity in opportunities:
                raise SystemExit(f"FAIL_CLOSED: duplicate opportunity {identity}")
            opportunities[identity] = row

    joined: list[dict] = []
    for record in result["records"]:
        identity = (str(record["id"][0]), int(record["id"][1]))
        opportunity = opportunities.get(identity)
        if opportunity is None:
            raise SystemExit(f"FAIL_CLOSED: paper record not in frozen opportunity set {identity}")
        if args.match_field not in opportunity:
            raise SystemExit(f"FAIL_CLOSED: opportunity lacks match field {args.match_field}")
        if bool(record["match"]) != bool(opportunity[args.match_field]):
            raise SystemExit(f"FAIL_CLOSED: match identity disagreement {identity}")
        exit_120 = record["exits"]["120"]
        joined.append({
            "identity": identity,
            "match": bool(record["match"]),
            "source_partition": str(opportunity.get("source_partition")),
            "mint": str(opportunity.get("mint")),
            "entry_time_ns": int(record["entry_time_ns"]),
            "return_bps": int(exit_120["return_bps"]),
            "pnl_lamports": int(exit_120["quote_out_lamports"]) - int(record["entry_notional_lamports"]),
        })

    matches = [record for record in joined if record["match"]]
    accepted_non_matches = [record for record in joined if not record["match"]]
    controls = sorted(accepted_non_matches, key=lambda record: stable_pick(record["identity"]))[:len(matches)]
    if len(controls) != len(matches):
        raise SystemExit("FAIL_CLOSED: deterministic control cardinality mismatch")

    total_matches = sum(bool(row["crowded_impulse_match"]) for row in opportunities.values())
    total_non_matches = len(opportunities) - total_matches
    by_partition_match: dict[str, list[dict]] = defaultdict(list)
    by_partition_control: dict[str, list[dict]] = defaultdict(list)
    by_mint_match: dict[str, list[dict]] = defaultdict(list)
    for record in matches:
        by_partition_match[record["source_partition"]].append(record)
        by_mint_match[record["mint"]].append(record)
    for record in controls:
        by_partition_control[record["source_partition"]].append(record)

    partition_rows = []
    for partition in sorted(set(by_partition_match) | set(by_partition_control)):
        match_metrics = aggregate(by_partition_match[partition])
        control_metrics = aggregate(by_partition_control[partition])
        partition_rows.append({"source_partition": partition, "matches": match_metrics, "controls": control_metrics})
    mint_rows = [
        {"mint": mint, "matches": aggregate(records)}
        for mint, records in by_mint_match.items()
    ]
    mint_rows.sort(key=lambda row: (-row["matches"]["n"], row["mint"]))

    output = {
        "schema": SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "result_sha256": result_sha,
        "opportunity_sha256": sha256(args.opportunities),
        "match_field": args.match_field,
        "identity_join_pass": True,
        "global": {"matches": aggregate(matches), "deterministic_controls": aggregate(controls)},
        "reconstruction_acceptance": {
            "all_opportunities": len(opportunities),
            "frozen_matches": total_matches,
            "frozen_non_matches": total_non_matches,
            "accepted_matches": len(matches),
            "accepted_non_matches": len(accepted_non_matches),
            "match_acceptance_rate": len(matches) / total_matches if total_matches else None,
            "non_match_acceptance_rate": len(accepted_non_matches) / total_non_matches if total_non_matches else None,
            "aggregate_fail_closed_rejections": result.get("rejections", {}),
            "note": "The sealed economics result retains aggregate rejection reasons only; it cannot attribute a rejection reason to a match class without a new pre-outcome replay artifact.",
        },
        "partition_stability": partition_rows,
        "mint_concentration": {
            "distinct_mints_with_accepted_matches": len(by_mint_match),
            "top_25_mints_by_match_count": mint_rows[:25],
        },
        "scientific_guards": {
            "selection_rule_modified": False,
            "outcome_dependent_filtering": False,
            "execution_enabled": False,
            "interpretation": "Descriptive audit of a sealed paper result only. It does not promote a live strategy.",
        },
    }
    atomic_json(args.out, output)
    print(json.dumps({
        "matches": len(matches),
        "controls": len(controls),
        "distinct_match_mints": len(by_mint_match),
        "partitions": len(partition_rows),
        "match_acceptance_rate": output["reconstruction_acceptance"]["match_acceptance_rate"],
        "non_match_acceptance_rate": output["reconstruction_acceptance"]["non_match_acceptance_rate"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
