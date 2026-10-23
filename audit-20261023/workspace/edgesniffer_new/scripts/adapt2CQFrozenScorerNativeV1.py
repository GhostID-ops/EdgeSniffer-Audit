#!/usr/bin/env python3
"""Adapt native Pump observations to the preserved 2CQ frozen scorer.

This adapter is intentionally fail-closed.  It does not substitute first-seen
time for launch time, current state for launch state, or wallet identity for
creator identity.  It emits exact-score signals and same-period controls only
when the complete causal feature lineage is present.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import twoCqCausalEntryTimingFingerprintV1 as timing
from materialize2CQEventDrivenIndependentWindowsV1 import side_of, sort_key, timestamp
from runIndependentStrategyLabV1 import state as parse_state
from runIndependentStrategyLabV1 import route_of, state_evidence_rejection


WINDOW_MS = 300_000
PUMP_ROUTES = {"PUMP_CURVE", "PUMP_BONDING_CURVE_TOP_LEVEL"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def load_enrichment(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        mint = str(row.get("mint") or "")
        if mint:
            result[mint] = row
    return result


def load_create_registry(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT mint, signature, slot, realtime_receipt_ns, canonical_order_status, "
            "realtime_status, decoder_status FROM create_registry"
        ).fetchall()
        return {str(row["mint"]): dict(row) for row in rows}
    finally:
        connection.close()


def state_pair(row: dict[str, Any]) -> tuple[Any, Any] | None:
    if state_evidence_rejection(row):
        return None
    canonical = row.get("canonical_state") if isinstance(row.get("canonical_state"), dict) else {}
    pre = parse_state(canonical.get("pre_state") or row.get("pre_state"))
    post = parse_state(canonical.get("post_state") or row.get("post_state") or row.get("state"))
    return (pre, post) if pre is not None and post is not None else None


def wallet_of(row: dict[str, Any]) -> str:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    return str(row.get("wallet") or raw.get("wallet") or "")


def signature_of(row: dict[str, Any]) -> str:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    return str(row.get("transaction_signature") or raw.get("signature") or row.get("observation_id") or "")


def instruction_index(row: dict[str, Any]) -> int:
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    try:
        return int(raw.get("instructionIndex") if raw.get("instructionIndex") is not None else 0)
    except (TypeError, ValueError):
        return 0


def transaction_index(row: dict[str, Any]) -> int:
    try:
        return int(row.get("source_transaction_index") or row.get("sourceTransactionIndex") or 0)
    except (TypeError, ValueError):
        return 0


def source_event(row: dict[str, Any], creator: str) -> timing.Event | None:
    pair = state_pair(row)
    observed = timestamp(row)
    if pair is None or observed is None:
        return None
    return timing.Event(
        mint=str(row.get("mint") or ""), signature=signature_of(row), slot=int(row.get("source_slot") or 0),
        transaction_index=transaction_index(row), instruction_index=instruction_index(row),
        observed_ms=observed, evidence_id=str(row.get("observation_id") or ""),
        is_buy=side_of(row) == "buy", user=wallet_of(row), creator=creator,
        quote_lamports=int(float(row.get("quote_lamports") or 0)), token_raw=0,
        pre_state=pair[0], post_state=pair[1],
    )


def metadata_for(mint: str, enrichment: dict[str, dict[str, Any]], registry: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    supplied = dict(registry.get(mint) or {})
    supplied.update(enrichment.get(mint) or {})
    reasons: list[str] = []
    create_ms = supplied.get("create_receive_ts_ms")
    if create_ms is None and supplied.get("realtime_receipt_ns") is not None:
        create_ms = int(supplied["realtime_receipt_ns"]) // 1_000_000
    if create_ms is None:
        reasons.append("missing_exact_create_receive_clock")
    else:
        supplied["create_receive_ts_ms"] = int(create_ms)
    if not isinstance(supplied.get("creator"), str) or not supplied.get("creator"):
        reasons.append("missing_creator_identity")
    initial = parse_state(supplied.get("initial_state"))
    if initial is None:
        reasons.append("missing_exact_launch_reserve_state")
    else:
        supplied["initial_state"] = initial
    return supplied, reasons


def scorer_logit(features: dict[str, float], scorer: dict[str, Any]) -> float:
    model = scorer["timingModel"]
    value = float(model["intercept"])
    for name in model["featureNames"]:
        value += float(model["coefficients"][name]) * (
            (float(features[name]) - float(model["means"][name])) / max(1e-12, float(model["scales"][name]))
        )
    return value


def sigmoid(value: float) -> float:
    """Stable logistic transform for the frozen model's linear predictor."""
    if value >= 0:
        inverse = math.exp(-value)
        return 1.0 / (1.0 + inverse)
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def scorer_scores(features: dict[str, float], scorer: dict[str, Any]) -> tuple[float, float]:
    """Return (linear predictor, probability) in the frozen scorer's domains."""
    logit = scorer_logit(features, scorer)
    return logit, sigmoid(logit)


def post_event_boundary(event: timing.Event, prior: list[timing.Event]) -> tuple[timing.Event, list[timing.Event]]:
    """Represent the preserved scorer's POST_EXTERNAL_EVENT boundary.

    The historical scorer constructs features from the state immediately after
    the triggering external event and includes that event in prior history.
    The native adapter previously passed the event as current/pre-state and
    omitted it from history, which was a different feature definition.
    """
    snapshot = timing.Event(
        mint=event.mint, signature="POST_EVENT_SNAPSHOT", slot=event.slot,
        transaction_index=event.transaction_index, instruction_index=event.instruction_index,
        observed_ms=event.observed_ms, evidence_id=event.evidence_id,
        is_buy=False, user="SNAPSHOT", creator=event.creator, quote_lamports=0,
        token_raw=0, pre_state=event.post_state, post_state=event.post_state,
    )
    return snapshot, [*prior, event]


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    scorer = json.loads(args.scorer.read_text(encoding="utf-8"))
    enrichment = load_enrichment(args.enrichment)
    registry = load_create_registry(args.create_registry)
    rows = sorted(read_jsonl(args.input), key=sort_key)
    histories: dict[str, list[timing.Event]] = defaultdict(list)
    last_score: dict[str, float] = {}
    last_signal_ms: dict[str, int] = {}
    signals: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []
    missing: Counter[str] = Counter()
    metadata_by_mint: dict[str, dict[str, Any]] = {}
    metadata_reasons_by_mint: dict[str, list[str]] = {}
    for row in rows:
        mint = str(row.get("mint") or "")
        if not mint or mint in metadata_by_mint or mint in metadata_reasons_by_mint:
            continue
        metadata, reasons = metadata_for(mint, enrichment, registry)
        if reasons:
            metadata_reasons_by_mint[mint] = reasons
        else:
            metadata_by_mint[mint] = metadata
    for reasons in metadata_reasons_by_mint.values():
        for reason in reasons:
            missing[reason] += 1
    feature_complete_rows = 0
    candidate_count = 0
    control_count = 0
    threshold = float(scorer["timingThreshold"])
    feature_names = tuple(scorer["timingModel"]["featureNames"])
    decision_boundary = str(getattr(args, "decision_boundary", "post_event"))

    for row in rows:
        mint = str(row.get("mint") or "")
        route = route_of(row)
        if not mint or route not in PUMP_ROUTES:
            if mint and route is None:
                missing["missing_exact_pump_route"] += 1
            continue
        metadata = metadata_by_mint.get(mint)
        if metadata is None:
            continue
        creator = str(metadata["creator"])
        event = source_event(row, creator)
        if event is None:
            missing["missing_exact_native_event_state"] += 1
            continue
        prior = histories[mint]
        if side_of(row) != "buy":
            histories[mint].append(event)
            continue
        try:
            feature_event, feature_prior = (post_event_boundary(event, prior)
                                            if decision_boundary == "post_event"
                                            else (event, prior))
            features = timing.state_features(
                feature_event, feature_prior, int(metadata["create_receive_ts_ms"]), metadata["initial_state"],
                excluded_wallets=set(),
            )
            if any(name not in features or not math.isfinite(float(features[name])) for name in feature_names):
                raise ValueError("feature_vector_incomplete")
        except Exception as error:
            missing[f"feature_build:{type(error).__name__}"] += 1
            histories[mint].append(event)
            continue
        feature_complete_rows += 1
        score_logit, score_probability = scorer_scores(features, scorer)
        receive_ms = int(event.observed_ms)
        crossing = score_probability >= threshold and last_score.get(mint, -float("inf")) < threshold
        record = {
            "schema": "EDGESNIFFER_2CQ_NATIVE_FROZEN_SCORER_EVENT_V2",
            "record_type": "INDEPENDENT_EVENT_DRIVEN_SCORE_EVENT",
            "mint": mint, "route": route,
            "signal_receive_ts_ms": receive_ms, "signal_signature": event.signature,
            "signal_wallet": event.user, "teacher_wallet_match": False,
            "score": score_probability, "score_probability": score_probability,
            "score_logit": score_logit, "score_domain": "probability",
            "threshold": threshold, "crossing": crossing,
            "decision_boundary": decision_boundary,
            "features": features, "entry_state": {
                "virtual_sol": event.post_state.virtual_sol, "virtual_token": event.post_state.virtual_token,
                "real_sol": event.post_state.real_sol, "real_token": event.post_state.real_token,
            },
            "fee_basis_points": row.get("fee_basis_points"),
            "protocol_fee_basis_points": row.get("protocol_fee_basis_points"),
            "creator_fee_basis_points": row.get("creator_fee_basis_points"),
            "lineage": {"source_receive_clock": True, "create_receive_ts_ms": metadata["create_receive_ts_ms"],
                         "initial_state_source": metadata.get("initial_state_source", "enrichment"),
                         "creator_source": metadata.get("creator_source", "enrichment"),
                         "teacher_label_used_for_admission": False},
        }
        if crossing and receive_ms - last_signal_ms.get(mint, -10**18) >= WINDOW_MS:
            signals.append(record)
            candidate_count += 1
            last_signal_ms[mint] = receive_ms
        elif not crossing:
            record["record_type"] = "INDEPENDENT_EVENT_DRIVEN_CONTROL_EVENT"
            record["control_group"] = receive_ms // WINDOW_MS
            controls.append(record)
            control_count += 1
        last_score[mint] = score_probability
        histories[mint].append(event)

    args.signals_output.parent.mkdir(parents=True, exist_ok=True)
    with args.signals_output.open("w", encoding="utf-8") as handle:
        for row in signals:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    with args.controls_output.open("w", encoding="utf-8") as handle:
        for row in controls:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    report = {
        "schema": "EDGESNIFFER_2CQ_NATIVE_FROZEN_SCORER_ADAPTER_V2",
        "source_sha256": sha256_file(args.input),
        "scorer_sha256": sha256_file(args.scorer),
        "create_registry_sha256": sha256_file(args.create_registry) if args.create_registry else None,
        "input_rows": len(rows), "feature_complete_rows": feature_complete_rows,
        "unique_mints": len({str(row.get("mint") or "") for row in rows if row.get("mint")}),
        "exact_lineage_mints": len(metadata_by_mint),
        "incomplete_lineage_mints": len(metadata_reasons_by_mint),
        "exact_candidate_signals": candidate_count, "same_period_control_events": control_count,
        "missing_reason_counts": dict(missing),
        "threshold": threshold, "threshold_domain": "probability",
        "decision_boundary": decision_boundary,
        "model_scope": "timing_model_benchmark_only",
        "composite_policy_implemented": False,
        "cross_mint_selection_applied": False,
        "preserved_scorer_equivalence": "timing score domain and post-event boundary",
        "signals_output": str(args.signals_output), "controls_output": str(args.controls_output),
        "exact_trigger_ready": bool(feature_complete_rows and candidate_count),
        "policy_frozen": False, "promotion_blocked": True, "read_only": True, "trading_enabled": False,
        "notes": [
            "No proxy launch time, launch state, creator identity, or teacher label is used.",
            "Controls are non-crossing, same-period native Pump buys from the same sorted source.",
            "The preserved timing model is evaluated in probability space; score_logit is retained for auditability.",
            "The default post_event boundary matches the preserved scorer's POST_EXTERNAL_EVENT training semantics.",
            "This adapter does not claim composite cross-mint selection parity; it is a timing-model benchmark only.",
            "A fresh prospective cohort is not justified until exact feature coverage is non-zero and controls are present.",
        ],
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--scorer", type=Path, required=True)
    parser.add_argument("--create-registry", type=Path)
    parser.add_argument("--enrichment", type=Path)
    parser.add_argument("--signals-output", type=Path, required=True)
    parser.add_argument("--controls-output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--decision-boundary", choices=("post_event", "pre_event"), default="post_event")
    args = parser.parse_args()
    print(json.dumps(materialize(args), sort_keys=True))


if __name__ == "__main__":
    main()
