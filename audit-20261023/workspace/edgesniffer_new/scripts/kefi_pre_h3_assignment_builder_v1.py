#!/usr/bin/env python3
"""Deterministically build PRE_H3_ACTIONABILITY_QUALIFICATION_V1 assignments.

This is deliberately an upstream publisher, not an evaluator or boundary
creator.  It admits all frozen-fingerprint matches before delayed entry,
tradeability, or horizon availability is derived; binds every input and tool
identity; and writes the JSONL consumed by the frozen evaluator.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import importlib.util
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Iterable


SCHEMA = "KEFI_PRE_H3_ASSIGNMENT_BUILDER_V1"
EXPECTED_FINGERPRINT_SHA256 = "db08899d92f4d69d8c0234cfff9cbd05bd27dfb1365aed88d08b85f88cfc1b4a"
WARMUP_NS = 120_000_000_000
ENTRY_DELAY_NS = 1_000_000_000
HORIZON_NS = 120_000_000_000
CHRONOLOGICAL_BLOCKS = 8
MAX_CONTROLS = 4
EXPECTED_FROZEN_SCORER_SHA256 = "ae1771faeda813547ab8a0294c1384402580c1f1028c5672a570b613c801cff4"
FROZEN_FEATURE_NAMES = (
    "log_pre_pool_quote", "log_pre_pool_base", "amm_fee_bps", "observed_pool_age_seconds",
    "seconds_since_previous_observed_buy", "prior_pre_pool_quote_log_change", "prior_buy_count_5s",
    "prior_buy_count_20s", "prior_buy_count_60s", "prior_buy_quote_5s", "prior_buy_quote_20s",
    "prior_buy_quote_60s", "no_prior_observed_buy",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(canonical(value)); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"FAIL_CLOSED: cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_utc_ns(value: str) -> int:
    if not value.endswith("Z"):
        raise RuntimeError("FAIL_CLOSED: UTC boundary must end with Z")
    main, dot, fraction = value[:-1].partition(".")
    from datetime import datetime, timezone
    seconds = int(datetime.fromisoformat(main + "+00:00").replace(tzinfo=timezone.utc).timestamp())
    nanos = int((fraction + "000000000")[:9]) if dot else 0
    return seconds * 1_000_000_000 + nanos


def event_id(mechanics, row: dict) -> str:
    return "|".join(map(str, mechanics.event_key(row)))


def validate_fingerprint_schema(model: dict) -> None:
    required = ("features", "coefficients", "intercept", "standardization_means", "standardization_scales", "threshold")
    if any(field not in model for field in required) or model.get("schema") != "KEFI_RICH_PREENTRY_FINGERPRINT_V2":
        raise RuntimeError("FAIL_CLOSED: frozen fingerprint missing scoring contract")
    if tuple(model["features"]) != FROZEN_FEATURE_NAMES:
        raise RuntimeError("FAIL_CLOSED: frozen fingerprint feature-name/order mismatch")
    if set(model["coefficients"]) != set(FROZEN_FEATURE_NAMES):
        raise RuntimeError("FAIL_CLOSED: frozen fingerprint coefficient schema mismatch")
    if not (len(model["features"]) == len(model["standardization_means"]) == len(model["standardization_scales"]) == len(FROZEN_FEATURE_NAMES)):
        raise RuntimeError("FAIL_CLOSED: frozen fingerprint feature shape mismatch")
    for scale in model["standardization_scales"]:
        if float(scale) == 0:
            raise RuntimeError("FAIL_CLOSED: frozen fingerprint zero scale")


def read_rows(paths: Iterable[Path], mechanics) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise RuntimeError(f"FAIL_CLOSED: invalid canonical JSONL {path}:{line_number}") from error
    return mechanics.sorted_unique(rows)


def hard_wall_before(rows: list[dict], signal: dict, until_ns: int, mechanics) -> bool:
    stamp, instance, epoch, *_ = mechanics.availability_key(signal)
    for row in rows:
        received, row_instance, row_epoch, *_ = mechanics.availability_key(row)
        if received < stamp:
            continue
        if received >= until_ns:
            return False
        if row_instance != instance or row_epoch != epoch:
            return True
    return False


def actionability(rows: list[dict], target: dict, mechanics) -> dict:
    entry = mechanics.delayed_entry_state(rows, target, ENTRY_DELAY_NS)
    if entry["status"] != "TRADEABLE":
        deadline = int(target["received_at_unix_ns"]) + ENTRY_DELAY_NS
        reason = str(entry.get("reason", ""))
        outcome = "HARD_WALL_INCOMPLETE_HORIZON" if hard_wall_before(rows, target, deadline, mechanics) else (
            "NO_POST_DELAY_ENTRY_STATE" if reason == "NO_SUPPORTED_POST_DELAY_ENTRY_STATE" else f"POST_DELAY_NON_TRADEABLE_{reason}")
        return {"complete_actionable": False, "outcome_reason": outcome, "entry": entry, "mark": None}
    entry_row = next(row for row in rows if event_id(mechanics, row) == "|".join(map(str, entry["entry_event_key"])))
    mark = mechanics.observed_exit_mark(rows, entry_row, HORIZON_NS)
    if mark["status"] == "MARK_AVAILABLE":
        return {"complete_actionable": True, "outcome_reason": "COMPLETE_ACTIONABLE_TRAJECTORY", "entry": entry, "mark": mark}
    deadline = int(entry_row["received_at_unix_ns"]) + HORIZON_NS
    reason = str(mark.get("reason", ""))
    outcome = "HARD_WALL_INCOMPLETE_HORIZON" if hard_wall_before(rows, entry_row, deadline, mechanics) else (
        "NO_120S_MARK" if reason == "NO_SUPPORTED_EXIT_MARK" else f"POST_ENTRY_NON_TRADEABLE_{reason}")
    return {"complete_actionable": False, "outcome_reason": outcome, "entry": entry, "mark": mark}


class ActionabilityIndex:
    """Exact indexed form of the frozen delayed-entry/mark scan.

    The baseline mechanics scans every row for each target.  This index uses
    the same canonical row order and the same *first* row at/after each
    deadline, but scopes lookup to the already-required source/epoch/pool/mint
    stream.  It changes cost, never selection or semantics.
    """
    def __init__(self, rows: list[dict], mechanics):
        self.mechanics = mechanics
        self.streams: dict[tuple[str, int, str, str], tuple[list[int], list[dict]]] = {}
        grouped: dict[tuple[str, int, str, str], list[dict]] = defaultdict(list)
        for row in mechanics.sorted_unique(rows):
            _, instance, epoch, *_ = mechanics.availability_key(row)
            grouped[(instance, epoch, str(row["pool"]), str(row["mint"]))].append(row)
        for key, stream in grouped.items():
            self.streams[key] = ([int(row["received_at_unix_ns"]) for row in stream], stream)

    def first_at_or_after(self, row: dict, deadline_ns: int) -> dict | None:
        _, instance, epoch, *_ = self.mechanics.availability_key(row)
        values = self.streams.get((instance, epoch, str(row["pool"]), str(row["mint"])))
        if values is None:
            return None
        stamps, stream = values
        index = bisect.bisect_left(stamps, deadline_ns)
        return stream[index] if index < len(stream) else None

    def actionability(self, target: dict) -> dict:
        entry_candidate = self.first_at_or_after(target, int(target["received_at_unix_ns"]) + ENTRY_DELAY_NS)
        if entry_candidate is None:
            return {"complete_actionable": False, "outcome_reason": "NO_POST_DELAY_ENTRY_STATE", "entry": {"status": "NON_TRADEABLE", "reason": "NO_SUPPORTED_POST_DELAY_ENTRY_STATE"}, "mark": None}
        entry_state = self.mechanics.tradeable(entry_candidate)
        if not entry_state["tradeable"]:
            return {"complete_actionable": False, "outcome_reason": f"POST_DELAY_NON_TRADEABLE_{entry_state['reason']}", "entry": {"status": "NON_TRADEABLE", "reason": entry_state["reason"]}, "mark": None}
        entry = {"status": "TRADEABLE", "entry_available_at_unix_ns": int(entry_candidate["received_at_unix_ns"]),
                 "entry_event_key": entry_state["event_key"], "pre_state": entry_state["pre_state"]}
        mark_candidate = self.first_at_or_after(entry_candidate, int(entry_candidate["received_at_unix_ns"]) + HORIZON_NS)
        if mark_candidate is None:
            return {"complete_actionable": False, "outcome_reason": "NO_120S_MARK", "entry": entry,
                    "mark": {"status": "NON_TRADEABLE", "reason": "NO_SUPPORTED_EXIT_MARK"}}
        mark_state = self.mechanics.tradeable(mark_candidate)
        if not mark_state["tradeable"]:
            return {"complete_actionable": False, "outcome_reason": f"POST_ENTRY_NON_TRADEABLE_{mark_state['reason']}", "entry": entry,
                    "mark": {"status": "NON_TRADEABLE", "reason": mark_state["reason"]}}
        return {"complete_actionable": True, "outcome_reason": "COMPLETE_ACTIONABLE_TRAJECTORY", "entry": entry,
                "mark": {"status": "MARK_AVAILABLE", "mark_available_at_unix_ns": int(mark_candidate["received_at_unix_ns"]),
                         "mark_event_key": mark_state["event_key"], "pre_state": mark_state["pre_state"]}}


def fingerprint_candidates(rows: list[dict], model: dict, boundary_ns: int, mechanics, frozen_scorer) -> tuple[list[dict], list[dict]]:
    """Run the original frozen scorer's state machine, names, and score code.

    State is scoped to a source instance/continuity epoch so no source break can
    leak pre-break context into a future qualification observation.
    """
    validate_fingerprint_schema(model)
    states: dict[tuple[str, int, str], object] = defaultdict(frozen_scorer.PoolState)
    matches, non_matches = [], []
    for row in rows:
        received, instance, epoch, *_ = mechanics.availability_key(row)
        state = states[(instance, epoch, str(row.get("pool", "")))]
        features = list(state.snapshot(received, row))
        supported = mechanics.tradeable(row)["tradeable"] and str(row.get("side", "")).upper() == "BUY"
        eligible = supported and not bool(row.get("is_tracked_teacher")) and received >= boundary_ns + WARMUP_NS
        if eligible:
            enriched = {**row, "_frozen_feature_vector": features,
                        "frozen_fingerprint_score": frozen_scorer.score(features, model)}
            (matches if enriched["frozen_fingerprint_score"] >= float(model["threshold"]) else non_matches).append(enriched)
        state.observe(received, row)
    return matches, non_matches


def unique_targets(matches: list[dict], mechanics) -> list[dict]:
    retained: dict[tuple[str, int, str, int], dict] = {}
    for row in matches:
        received, instance, epoch, *_ = mechanics.availability_key(row)
        retained.setdefault((instance, epoch, str(row["pool"]), received // 20_000_000_000), row)
    return sorted(retained.values(), key=mechanics.availability_key)


def chronological_block(index: int, total: int) -> str:
    if total <= 0:
        raise RuntimeError("FAIL_CLOSED: no targets")
    return f"block-{(index * CHRONOLOGICAL_BLOCKS) // total + 1:02d}"


def assert_single_continuous_source(rows: list[dict], mechanics) -> tuple[str, int]:
    epochs = {(str(row["source_instance_id"]), int(row["continuity_epoch"])) for row in rows}
    if len(epochs) != 1:
        raise RuntimeError("FAIL_CLOSED: qualification source-instance/continuity break")
    return next(iter(epochs))


def build_assignments(rows: list[dict], model: dict, boundary_ns: int, mechanics, frozen_scorer, *, control_seed: str) -> list[dict]:
    # Assignment identity must not depend on JSONL enumeration order after a
    # restart; canonical source order is the only permitted input order.
    rows = mechanics.sorted_unique(rows)
    assert_single_continuous_source(rows, mechanics)
    complete_first_seen = mechanics.observed_pool_first_seen(rows)
    actionability_index = ActionabilityIndex(rows, mechanics)
    matches, non_matches = fingerprint_candidates(rows, model, boundary_ns, mechanics, frozen_scorer)
    targets = unique_targets(matches, mechanics)
    controls = mechanics.deterministic_controls(targets, non_matches, bucket_ns=mechanics.CONTROL_BUCKET_NS,
                                                per_target=MAX_CONTROLS, seed=control_seed,
                                                first_seen=complete_first_seen)
    assignments = []
    for index, target in enumerate(targets):
        target_outcome = actionability_index.actionability(target)
        selected_controls = []
        for control in controls.get(mechanics.event_key(target), []):
            control_outcome = actionability_index.actionability(control)
            selected_controls.append({
                "control_id": event_id(mechanics, control),
                "complete_actionable": control_outcome["complete_actionable"],
                "outcome_reason": control_outcome["outcome_reason"],
            })
        assignments.append({
            "schema": SCHEMA,
            "target_id": event_id(mechanics, target),
            "target_event_key": list(mechanics.event_key(target)),
            "pool": str(target["pool"]), "mint": str(target["mint"]),
            "cluster_id": mechanics.cluster_key(target, mechanics.CONTROL_BUCKET_NS),
            "chronological_block": chronological_block(index, len(targets)),
            "target_complete_actionable": target_outcome["complete_actionable"],
            "target_outcome_reason": target_outcome["outcome_reason"],
            "controls": selected_controls,
            "control_count": len(selected_controls),
            "fingerprint_score": target["frozen_fingerprint_score"],
            "pnl_accessed": False,
        })
    return assignments


def validate_canonical_input(manifest_path: Path, rows_path: Path) -> dict:
    """Require the canonical index export; receipt concatenation is forbidden."""
    manifest = json.loads(manifest_path.read_text())
    expected_schema = "KEFI_INCREMENTAL_EVENT_INDEX_V2_QUALIFICATION_INPUT_V1"
    if manifest.get("schema") != expected_schema:
        raise RuntimeError("FAIL_CLOSED: invalid canonical qualification manifest")
    if Path(str(manifest.get("canonical_events_path", ""))).resolve() != rows_path.resolve():
        raise RuntimeError("FAIL_CLOSED: canonical input path not bound by manifest")
    if not rows_path.is_file() or sha256(rows_path) != str(manifest.get("canonical_events_sha256", "")):
        raise RuntimeError("FAIL_CLOSED: canonical input hash mismatch")
    provenance = Path(str(manifest.get("window_exposures_path", "")))
    if not provenance.is_file() or sha256(provenance) != str(manifest.get("window_exposures_sha256", "")):
        raise RuntimeError("FAIL_CLOSED: canonical exposure provenance hash mismatch")
    if manifest.get("input_policy") != "canonical intrinsic events exactly once; window multiplicity is provenance only":
        raise RuntimeError("FAIL_CLOSED: canonical input policy mismatch")
    return {"manifest_path": str(manifest_path), "manifest_sha256": sha256(manifest_path),
            "canonical_events_path": str(rows_path), "canonical_events_sha256": sha256(rows_path),
            "window_exposures_path": str(provenance), "window_exposures_sha256": sha256(provenance),
            "canonical_events": int(manifest.get("canonical_events", -1)),
            "window_exposures": int(manifest.get("window_exposures", -1))}


def utc_from_ns(value: int) -> str:
    from datetime import datetime, timezone
    seconds, nanos = divmod(value, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{nanos:09d}Z"


def assert_epoch_eligibility(rows: list[dict], registry_script: Path, registry_root: Path,
                             boundary_utc: str) -> None:
    registry = load_module(registry_script, "kefi_preh3_epoch_registry")
    for row in rows:
        registry.eligible(registry_root, utc_from_ns(int(row["received_at_unix_ns"])), boundary_utc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fingerprint", type=Path, required=True)
    parser.add_argument("--boundary-receipt", type=Path, required=True)
    parser.add_argument("--canonical-input", type=Path, required=True)
    parser.add_argument("--canonical-manifest", type=Path, required=True)
    parser.add_argument("--registry-script", type=Path, required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--frozen-scorer", type=Path, required=True)
    parser.add_argument("--mechanics", type=Path, required=True)
    parser.add_argument("--evaluator", type=Path, required=True)
    parser.add_argument("--out-assignments", type=Path, required=True)
    parser.add_argument("--out-manifest", type=Path, required=True)
    parser.add_argument("--control-seed", required=True)
    args = parser.parse_args()
    if sha256(args.fingerprint) != EXPECTED_FINGERPRINT_SHA256:
        raise SystemExit("FAIL_CLOSED: frozen fingerprint hash mismatch")
    boundary = json.loads(args.boundary_receipt.read_text())
    if boundary.get("status") not in {"ACTIVE", "FROZEN"} or not boundary.get("boundaryUtc"):
        raise SystemExit("FAIL_CLOSED: invalid scientific boundary receipt")
    mechanics = load_module(args.mechanics, "kefi_preh3_mechanics")
    if sha256(args.frozen_scorer) != EXPECTED_FROZEN_SCORER_SHA256:
        raise SystemExit("FAIL_CLOSED: frozen strict-preentry scorer hash mismatch")
    frozen_scorer = load_module(args.frozen_scorer, "kefi_frozen_strict_preentry_scorer")
    canonical_input = validate_canonical_input(args.canonical_manifest, args.canonical_input)
    model = json.loads(args.fingerprint.read_text())
    if model.get("status") != "FROZEN_FOR_PROSPECTIVE_OUTCOME_BLIND_SHADOW" or not model.get("strict_preentry"):
        raise SystemExit("FAIL_CLOSED: invalid frozen fingerprint artifact")
    validate_fingerprint_schema(model)
    rows = read_rows([args.canonical_input], mechanics)
    assert_epoch_eligibility(rows, args.registry_script, args.registry_root, str(boundary["boundaryUtc"]))
    source_instance_id, continuity_epoch = assert_single_continuous_source(rows, mechanics)
    assignments = build_assignments(rows, model, parse_utc_ns(str(boundary["boundaryUtc"])), mechanics,
                                    frozen_scorer, control_seed=args.control_seed)
    if not assignments:
        raise SystemExit("FAIL_CLOSED: no pre-entry eligible fingerprint targets")
    args.out_assignments.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out_assignments.with_suffix(args.out_assignments.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for assignment in assignments:
            handle.write(json.dumps(assignment, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, args.out_assignments)
    manifest = {
        "schema": SCHEMA, "status": "ASSIGNMENTS_BUILT_NOT_EVALUATED", "outcomes_evaluated": False,
        "pnl_accessed": False, "boundary_receipt_path": str(args.boundary_receipt),
        "boundary_receipt_sha256": sha256(args.boundary_receipt), "boundary_utc": boundary["boundaryUtc"],
        "fingerprint_path": str(args.fingerprint), "fingerprint_sha256": sha256(args.fingerprint),
        "canonical_input": canonical_input, "mechanics_path": str(args.mechanics), "mechanics_sha256": sha256(args.mechanics),
        "frozen_scorer_path": str(args.frozen_scorer), "frozen_scorer_sha256": sha256(args.frozen_scorer),
        "frozen_feature_names": list(FROZEN_FEATURE_NAMES),
        "epoch_registry_path": str(args.registry_root), "epoch_registry_script": str(args.registry_script),
        "epoch_registry_sha256": sha256(args.registry_script),
        "assignment_builder_path": str(Path(__file__)), "assignment_builder_sha256": sha256(Path(__file__)),
        "evaluator_path": str(args.evaluator), "evaluator_sha256": sha256(args.evaluator),
        "control_seed_sha256": hashlib.sha256(args.control_seed.encode()).hexdigest(),
        "source_instance_id": source_instance_id, "continuity_epoch": continuity_epoch,
        "source_continuity_policy": "exactly one source_instance_id/continuity_epoch; any break invalidates the cohort",
        "pool_first_seen_policy": "complete ordered scientific row stream before eligibility/warm-up/filtering",
        "assignments_path": str(args.out_assignments), "assignments_sha256": sha256(args.out_assignments),
        "targets_all": len(assignments), "targets_with_controls": sum(bool(a["controls"]) for a in assignments),
        "chronological_blocks": sorted({a["chronological_block"] for a in assignments}),
    }
    atomic(args.out_manifest, manifest)
    print(json.dumps({"status": manifest["status"], "assignments": len(assignments),
                      "assignments_sha256": manifest["assignments_sha256"], "manifest_sha256": sha256(args.out_manifest)}, sort_keys=True))


if __name__ == "__main__":
    main()
