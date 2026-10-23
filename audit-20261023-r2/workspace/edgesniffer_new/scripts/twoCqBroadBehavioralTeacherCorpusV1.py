#!/usr/bin/env python3
"""Publish an outcome-blind, evidence-tiered corpus of canonical 2CQ actions.

This is intentionally a read-only projection of the prospective symmetric
materializer's canonical action store.  It preserves weak observations with
explicit evidence flags, but never promotes them into the frozen development
or sealed validation cohorts.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
from typing import Any


SCHEMA = "2CQ_BROAD_BEHAVIORAL_TEACHER_CORPUS_V1"
OUTCOME_KEYS = {"outcome", "outcomes", "pnl", "roi", "return", "profit", "loss", "exit"}
EXACT_REASONS = {"EXACT_RECONCILIATION_NOT_READY", "EXACT_RECONCILIATION_EXHAUSTED"}
REALTIME_BLOCKERS = {
    "CANONICAL_ACQUISITION_WITHOUT_REALTIME_CANDIDATE",
    "PAUSED_INVALID_COLLECTION_INTERVAL",
    "GLOBAL_REALTIME_CONTINUITY_NOT_PROVEN_FOR_120S_UNIVERSE",
}


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def strip_outcomes(value: Any) -> Any:
    if isinstance(value, list):
        return [strip_outcomes(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        str(key): strip_outcomes(item)
        for key, item in value.items()
        if not any(token in str(key).lower() for token in OUTCOME_KEYS)
    }


def parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def order_after(left: tuple[int, int, int, str], right: tuple[int, int, int, str]) -> bool:
    return left > right


def freeze_membership(path: pathlib.Path) -> tuple[set[str], tuple[int, int, int, str] | None, str | None]:
    if not path.exists():
        return set(), None, None
    freeze = json.loads(path.read_text(encoding="utf-8"))
    signature_set = {str(value) for value in freeze.get("signatures") or []}
    raw_boundary = freeze.get("boundaryOrder") or freeze.get("developmentBoundaryOrder")
    if not isinstance(raw_boundary, (list, tuple)) or len(raw_boundary) != 4:
        return signature_set, None, sha256_file(path)
    return (
        signature_set,
        (int(raw_boundary[0]), int(raw_boundary[1]), int(raw_boundary[2]), str(raw_boundary[3])),
        sha256_file(path),
    )


def derive_boundary_from_frozen_members(
    connection: sqlite3.Connection, frozen: set[str],
) -> tuple[int, int, int, str] | None:
    """Use the immutable development membership when its freeze has no order field."""
    if not frozen:
        return None
    orders: list[tuple[int, int, int, str]] = []
    for signature in frozen:
        row = connection.execute(
            "SELECT block_time_ms,slot,transaction_index,signature FROM current_decisions WHERE signature=?",
            (signature,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"FROZEN_DEVELOPMENT_MEMBER_MISSING:{signature}")
        orders.append((int(row[0]), int(row[1]), int(row[2]), str(row[3])))
    return max(orders)


def strict_role(row: sqlite3.Row, frozen: set[str], boundary: tuple[int, int, int, str] | None) -> str:
    if row["signature"] in frozen:
        return "FROZEN_DEVELOPMENT"
    if row["status"] != "ONLINE_ELIGIBLE":
        return "NOT_STRICT_ELIGIBLE"
    if boundary is None:
        return "STRICT_ELIGIBLE_FIREWALLED_UNRESOLVED_BOUNDARY"
    order = (int(row["block_time_ms"]), int(row["slot"]), int(row["transaction_index"]), str(row["signature"]))
    return "POST_DEVELOPMENT_VALIDATION_FIREWALLED" if order_after(order, boundary) else "RETROACTIVE_EXCLUDED"


def evidence(row: sqlite3.Row, reasons: list[str], choice: dict[str, Any]) -> tuple[int, dict[str, bool]]:
    reason_set = set(reasons)
    exact_complete = not bool(reason_set & EXACT_REASONS)
    realtime = exact_complete and not bool(reason_set & REALTIME_BLOCKERS)
    causal_state = exact_complete and bool(choice.get("states"))
    alternatives = causal_state and int(choice.get("controlN") or 0) > 0 and "NO_CONTEMPORANEOUS_ALTERNATIVE" not in reason_set
    strict = str(row["status"]) == "ONLINE_ELIGIBLE"
    flags = {
        "canonicalTeacherAcquisition": True,
        "finalizedRouteClassified": True,
        "exactCausalPartitionComplete": exact_complete,
        "realtimeCausalTiming": realtime,
        "causalPreEntryState": causal_state,
        "contemporaneousAlternativeSet": alternatives,
        "strictSymmetricEligible": strict,
    }
    tier = 0
    for requirement in ("finalizedRouteClassified", "exactCausalPartitionComplete", "realtimeCausalTiming", "strictSymmetricEligible"):
        if flags[requirement]:
            tier += 1
        else:
            break
    return tier, flags


def init_destination(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS teacher_actions (
          signature TEXT PRIMARY KEY,
          mint TEXT NOT NULL,
          route TEXT NOT NULL,
          economic_family TEXT NOT NULL,
          block_time_ms INTEGER NOT NULL,
          slot INTEGER NOT NULL,
          transaction_index INTEGER NOT NULL,
          strict_status TEXT NOT NULL,
          strict_role TEXT NOT NULL,
          strict_reasons_json TEXT NOT NULL,
          evidence_tier INTEGER NOT NULL,
          evidence_flags_json TEXT NOT NULL,
          detail_json TEXT NOT NULL,
          choice_json TEXT NOT NULL,
          source_generation_sha TEXT NOT NULL,
          source_version_sha TEXT NOT NULL,
          source_updated_utc TEXT NOT NULL,
          corpus_updated_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS teacher_actions_route_order_idx
          ON teacher_actions(route, block_time_ms, slot, transaction_index);
        CREATE INDEX IF NOT EXISTS teacher_actions_tier_idx
          ON teacher_actions(evidence_tier, strict_role);
        """
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-state", type=pathlib.Path, required=True)
    parser.add_argument("--development-freeze", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()

    source_sha = sha256_file(args.source_state)
    frozen, boundary, freeze_sha = freeze_membership(args.development_freeze)
    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / "teacher-actions.sqlite3"
    source = sqlite3.connect(f"file:{args.source_state}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    if boundary is None:
        boundary = derive_boundary_from_frozen_members(source, frozen)
    target = sqlite3.connect(destination)
    init_destination(target)
    changed = 0
    for row in source.execute("SELECT * FROM current_decisions ORDER BY block_time_ms,slot,transaction_index,signature"):
        role = strict_role(row, frozen, boundary)
        existing = target.execute(
            "SELECT source_version_sha,source_updated_utc,strict_role FROM teacher_actions WHERE signature=?", (row["signature"],)
        ).fetchone()
        if existing == (row["version_sha"], row["updated_utc"], role):
            continue
        reasons = [str(item) for item in json.loads(row["reasons_json"])]
        choice = strip_outcomes(parse_json(row["choice_json"]))
        detail = strip_outcomes(parse_json(row["detail_json"]))
        tier, flags = evidence(row, reasons, choice)
        target.execute(
            """INSERT INTO teacher_actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(signature) DO UPDATE SET
                 mint=excluded.mint,route=excluded.route,economic_family=excluded.economic_family,
                 block_time_ms=excluded.block_time_ms,slot=excluded.slot,transaction_index=excluded.transaction_index,
                 strict_status=excluded.strict_status,strict_role=excluded.strict_role,
                 strict_reasons_json=excluded.strict_reasons_json,evidence_tier=excluded.evidence_tier,
                 evidence_flags_json=excluded.evidence_flags_json,detail_json=excluded.detail_json,
                 choice_json=excluded.choice_json,source_generation_sha=excluded.source_generation_sha,
                 source_version_sha=excluded.source_version_sha,source_updated_utc=excluded.source_updated_utc,
                 corpus_updated_utc=excluded.corpus_updated_utc""",
            (
                row["signature"], row["mint"], row["route"], row["economic_family"], row["block_time_ms"], row["slot"],
                row["transaction_index"], row["status"], role, canonical(reasons), tier,
                canonical(flags), canonical(detail), canonical(choice), row["generation_sha"], row["version_sha"], row["updated_utc"], now_utc(),
            ),
        )
        changed += 1
    target.commit()
    total = int(target.execute("SELECT COUNT(*) FROM teacher_actions").fetchone()[0])
    unique_mints = int(target.execute("SELECT COUNT(DISTINCT mint) FROM teacher_actions").fetchone()[0])
    route_distribution = dict(target.execute("SELECT route,COUNT(*) FROM teacher_actions GROUP BY route ORDER BY route"))
    tier_distribution = {f"TIER_{tier}": count for tier, count in target.execute("SELECT evidence_tier,COUNT(*) FROM teacher_actions GROUP BY evidence_tier ORDER BY evidence_tier")}
    role_distribution = dict(target.execute("SELECT strict_role,COUNT(*) FROM teacher_actions GROUP BY strict_role ORDER BY strict_role"))
    reason_distribution = collections.Counter()
    for (raw,) in target.execute("SELECT strict_reasons_json FROM teacher_actions"):
        reason_distribution.update(json.loads(raw))
    census = {
        "schema": SCHEMA + "_CENSUS",
        "generatedAtUtc": now_utc(),
        "totalTeacherActions": total,
        "uniqueMints": unique_mints,
        "routeDistribution": route_distribution,
        "evidenceTierDistribution": tier_distribution,
        "strictRoleDistribution": role_distribution,
        "strictSymmetricEligible": int(target.execute("SELECT COUNT(*) FROM teacher_actions WHERE strict_status='ONLINE_ELIGIBLE'").fetchone()[0]),
        "realtimeTimingAvailable": int(target.execute("SELECT COUNT(*) FROM teacher_actions WHERE json_extract(evidence_flags_json,'$.realtimeCausalTiming')=1").fetchone()[0]),
        "causalPreEntryStateAvailable": int(target.execute("SELECT COUNT(*) FROM teacher_actions WHERE json_extract(evidence_flags_json,'$.causalPreEntryState')=1").fetchone()[0]),
        "alternativeSetAvailable": int(target.execute("SELECT COUNT(*) FROM teacher_actions WHERE json_extract(evidence_flags_json,'$.contemporaneousAlternativeSet')=1").fetchone()[0]),
        "majorExclusionReasons": dict(reason_distribution.most_common(12)),
        "sourceStateSha256": source_sha,
        "developmentFreezeSha256": freeze_sha,
        "developmentSignaturesN": len(frozen),
        "developmentBoundaryOrder": list(boundary) if boundary is not None else None,
        "outcomeBlind": True,
        "outcomesRead": 0,
        "validationRowsRead": 0,
        "submissions": 0,
        "officialSolPumpCohortModified": False,
        "changedRowsThisRun": changed,
    }
    atomic_json(args.output / "census.json", census)
    manifest = {
        "schema": SCHEMA + "_MANIFEST",
        "generatedAtUtc": now_utc(),
        "teacherActionsPath": str(destination),
        "teacherActionsSha256": sha256_file(destination),
        "censusSha256": sha256_file(args.output / "census.json"),
        "sourceState": str(args.source_state),
        "sourceStateSha256": source_sha,
        "developmentFreeze": str(args.development_freeze),
        "developmentFreezeSha256": freeze_sha,
        "outcomeBlind": True,
        "firewall": "READ_ONLY_SOURCE_PROJECTION; NO_VALIDATION_OUTCOME_OR_SUBMISSION_ACCESS",
    }
    atomic_json(args.output / "manifest.json", manifest)
    source.close()
    target.close()
    print(json.dumps(census, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
