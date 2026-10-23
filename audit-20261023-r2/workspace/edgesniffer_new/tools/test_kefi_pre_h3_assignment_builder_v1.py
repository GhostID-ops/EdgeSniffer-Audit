import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
GOLDEN = ROOT / "tools" / "fixtures" / "kefi_frozen_strict_preentry_golden_v1.json"
SCRIPT = ROOT / "scripts" / "kefi_pre_h3_assignment_builder_v1.py"
MECHANICS = ROOT / "scripts" / "kefi_h3_pumpswap_mechanics_v1.py"
FROZEN_SCORER = ROOT / "scripts" / "kefi_capture_first_frozen_opportunities_v1.py"
spec = importlib.util.spec_from_file_location("builder", SCRIPT); assert spec and spec.loader
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
spec = importlib.util.spec_from_file_location("mechanics", MECHANICS); assert spec and spec.loader
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
spec = importlib.util.spec_from_file_location("frozen_scorer", FROZEN_SCORER); assert spec and spec.loader
s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)


def row(seq, stamp, *, pool, mint, quote, tracked=False):
    return {
        "source_instance_id": "instance-a", "continuity_epoch": 1,
        "durable_output_sequence": seq, "event_index": 0, "source_frame_sha256": f"frame-{seq}",
        "received_at_unix_ns": stamp, "exact_transition_validated": True,
        "quote_mint": m.WSOL, "mint": mint, "amount_mode": "EXACT_BASE", "side": "BUY",
        "pool": pool, "pool_base_raw": 90, "pool_quote_raw": quote, "base_amount": 10,
        "pool_quote_delta": 10, "lp_fee_bps": 10, "protocol_fee_bps": 20,
        "coin_creator_bps": 30, "cashback_bps": 0, "buyback_bps": 0,
        "user_quote_amount": 1, "is_tracked_teacher": tracked,
    }


def model():
    # The first frozen feature distinguishes target/control while both remain
    # in the same pre-event liquidity stratum; this keeps the fixture honest.
    features = list(b.FROZEN_FEATURE_NAMES)
    return {
        "schema": "KEFI_RICH_PREENTRY_FINGERPRINT_V2", "status": "FROZEN_FOR_PROSPECTIVE_OUTCOME_BLIND_SHADOW", "strict_preentry": True,
        "features": features, "coefficients": {name: (1.0 if index == 0 else 0.0) for index, name in enumerate(features)},
        "intercept": -5.0, "standardization_means": [0.0] * 13,
        "standardization_scales": [1.0] * 13, "threshold": 0.5,
    }


def canonical_rows():
    ns = 1_000_000_000
    # First observed states establish equal pool-age buckets before warm-up.
    rows = [
        row(1, 1 * ns, pool="target-pool", mint="target-warm", quote=200, tracked=True),
        row(2, 1 * ns + 1, pool="control-pool", mint="control-warm", quote=120, tracked=True),
        # Both candidates are in availability bucket 6 after a 120-second warm-up.
        row(3, 130 * ns, pool="target-pool", mint="target", quote=200),
        row(4, 131 * ns, pool="control-pool", mint="control", quote=120),
        # State evidence for delayed entry and the 120-second mark is not itself a candidate.
        row(5, 132 * ns, pool="target-pool", mint="target", quote=200, tracked=True),
        row(6, 133 * ns, pool="control-pool", mint="control", quote=120, tracked=True),
        row(7, 253 * ns, pool="target-pool", mint="target", quote=200, tracked=True),
        row(8, 254 * ns, pool="control-pool", mint="control", quote=120, tracked=True),
    ]
    return rows


def test_assignments_are_restart_deterministic_and_keep_zero_controls():
    rows = canonical_rows()
    first = b.build_assignments(rows, model(), 0, m, s, control_seed="frozen-seed")
    second = b.build_assignments(list(reversed(rows)), model(), 0, m, s, control_seed="frozen-seed")
    assert first == second
    assert len(first) == 1
    assert first[0]["target_complete_actionable"] is True
    assert first[0]["controls"] and first[0]["controls"][0]["complete_actionable"] is True

    # A target is not dropped just because no compatible fingerprint non-match exists.
    zero_control = b.build_assignments(rows, {**model(), "threshold": 0.0}, 0, m, s, control_seed="frozen-seed")
    assert zero_control and all(not item["controls"] for item in zero_control)


def test_indexed_actionability_is_identical_to_frozen_full_scan():
    rows = canonical_rows()
    indexed = b.ActionabilityIndex(rows, m)
    for item in rows:
        assert indexed.actionability(item) == b.actionability(rows, item, m)


def test_post_entry_or_pnl_fields_never_change_target_or_control_identity():
    original = canonical_rows()
    altered = copy.deepcopy(original)
    for index, item in enumerate(altered):
        item["post_entry_pnl"] = 999_999 - index
        item["later_mark"] = {"return": -999, "timestamp": 9_999_999_999}
        item["future_outcome_label"] = bool(index % 2)
    before = b.build_assignments(original, model(), 0, m, s, control_seed="frozen-seed")
    after = b.build_assignments(altered, model(), 0, m, s, control_seed="frozen-seed")
    identity = lambda items: [(item["target_id"], [control["control_id"] for control in item["controls"]]) for item in items]
    assert identity(before) == identity(after)


def test_complete_scientific_stream_first_seen_blocks_age_mismatched_control():
    ns = 1_000_000_000
    # These two rows are ineligible (teacher/warm-up evidence), but their
    # source order is still scientifically relevant to observed pool age.
    target_seen = row(1, 120 * ns, pool="target-pool", mint="target-warm", quote=200, tracked=True)
    control_seen = row(2, 240 * ns, pool="control-pool", mint="control-warm", quote=120, tracked=True)
    target = row(3, 250 * ns, pool="target-pool", mint="target", quote=200)
    control = row(4, 251 * ns, pool="control-pool", mint="control", quote=120)
    rows = [target_seen, control_seen, target, control]
    complete_first_seen = m.observed_pool_first_seen(rows)
    no_context = m.deterministic_controls([target], [control], bucket_ns=m.CONTROL_BUCKET_NS,
                                          per_target=4, seed="fixed")
    with_context = m.deterministic_controls([target], [control], bucket_ns=m.CONTROL_BUCKET_NS,
                                            per_target=4, seed="fixed", first_seen=complete_first_seen)
    assert no_context[m.event_key(target)]  # candidate-only first-seen would be wrong
    assert with_context[m.event_key(target)] == []


def test_golden_frozen_strict_preentry_feature_vectors_scores_and_identities():
    fixture = json.loads(GOLDEN.read_text())
    assert tuple(fixture["feature_names"]) == b.FROZEN_FEATURE_NAMES
    model_fixture, rows = fixture["model"], fixture["rows"]
    matches, non_matches = b.fingerprint_candidates(rows, model_fixture, fixture["boundary_unix_ns"], m, s)
    candidates = [*matches, *non_matches]
    expected = fixture["expected"]
    assert [b.event_id(m, row) for row in matches] == expected["match_event_ids"]
    assert [b.event_id(m, row) for row in non_matches] == expected["non_match_event_ids"]
    for row in candidates:
        identity = b.event_id(m, row)
        assert row["_frozen_feature_vector"] == pytest.approx(expected["candidate_feature_vectors"][identity])
        assert row["frozen_fingerprint_score"] == pytest.approx(expected["scores"][identity])

    # Independently replay the canonical frozen scorer to prove builder output
    # is not merely self-consistent.
    states = {}
    direct = {}
    for row in m.sorted_unique(rows):
        key = (row["source_instance_id"], row["continuity_epoch"], row["pool"])
        state = states.setdefault(key, s.PoolState())
        features = list(state.snapshot(row["received_at_unix_ns"], row))
        if not row["is_tracked_teacher"]:
            direct[b.event_id(m, row)] = (features, s.score(features, model_fixture))
        state.observe(row["received_at_unix_ns"], row)
    for row in candidates:
        features, score = direct[b.event_id(m, row)]
        assert row["_frozen_feature_vector"] == features
        assert row["frozen_fingerprint_score"] == score


def test_frozen_feature_name_order_is_mandatory_not_just_feature_count():
    malformed = model()
    malformed["features"] = list(reversed(malformed["features"]))
    try:
        b.validate_fingerprint_schema(malformed)
    except RuntimeError as error:
        assert str(error) == "FAIL_CLOSED: frozen fingerprint feature-name/order mismatch"
    else:
        raise AssertionError("same-length reordered feature schema was accepted")


def test_source_instance_or_epoch_break_invalidates_entire_qualification():
    rows = canonical_rows()
    rows[-1]["continuity_epoch"] = 2
    try:
        b.build_assignments(rows, model(), 0, m, s, control_seed="frozen-seed")
    except RuntimeError as error:
        assert str(error) == "FAIL_CLOSED: qualification source-instance/continuity break"
    else:
        raise AssertionError("cross-epoch assignments were accepted")


def test_atomic_jsonl_output_has_stable_content_across_rerun(tmp_path):
    assignments = b.build_assignments(canonical_rows(), model(), 0, m, s, control_seed="frozen-seed")
    path = tmp_path / "assignments.jsonl"
    for _ in range(2):
        path.write_text("".join(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n" for item in assignments))
        assert b.sha256(path) == b.sha256(path)


def test_end_to_end_canonical_registry_restart_is_byte_identical(tmp_path, monkeypatch):
    rows_path = tmp_path / "canonical.jsonl"
    rows_path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in canonical_rows()))
    fingerprint = tmp_path / "frozen-fingerprint.json"
    fingerprint.write_text(json.dumps(model(), sort_keys=True))
    boundary = tmp_path / "boundary.json"
    boundary.write_text(json.dumps({"status": "ACTIVE", "boundaryUtc": "1970-01-01T00:00:00.000000000Z"}))
    exposures = tmp_path / "window-exposures.jsonl"; exposures.write_text("")
    canonical_manifest = tmp_path / "canonical-manifest.json"
    canonical_manifest.write_text(json.dumps({
        "schema": "KEFI_INCREMENTAL_EVENT_INDEX_V2_QUALIFICATION_INPUT_V1",
        "canonical_events_path": str(rows_path), "canonical_events_sha256": b.sha256(rows_path),
        "window_exposures_path": str(exposures), "window_exposures_sha256": b.sha256(exposures),
        "canonical_events": len(canonical_rows()), "window_exposures": 0,
        "input_policy": "canonical intrinsic events exactly once; window multiplicity is provenance only",
    }))
    registry_root = tmp_path / "registry"
    registry = ROOT / "scripts" / "kefi_scientific_epoch_registry_v1.py"
    registry_module = importlib.util.spec_from_file_location("registry", registry); assert registry_module and registry_module.loader
    epochs = importlib.util.module_from_spec(registry_module); registry_module.loader.exec_module(epochs)
    epochs.append(registry_root, {"epoch_id": "future", "kind": "FUTURE_UNOPENED", "status": "UNOPENED",
                                  "start_utc": "1970-01-01T00:00:00Z", "end_utc": "", "reason": "test"})
    assignments, manifest = tmp_path / "assignments.jsonl", tmp_path / "manifest.json"
    old_expected = b.EXPECTED_FINGERPRINT_SHA256
    b.EXPECTED_FINGERPRINT_SHA256 = b.sha256(fingerprint)
    try:
        argv = ["builder", "--fingerprint", str(fingerprint), "--boundary-receipt", str(boundary),
                "--canonical-input", str(rows_path), "--canonical-manifest", str(canonical_manifest), "--registry-script", str(registry), "--registry-root", str(registry_root),
                "--mechanics", str(MECHANICS), "--frozen-scorer", str(FROZEN_SCORER), "--evaluator", str(ROOT / "scripts" / "kefi_pre_h3_actionability_evaluator_v1.py"),
                "--out-assignments", str(assignments), "--out-manifest", str(manifest), "--control-seed", "test-seed"]
        monkeypatch.setattr(sys, "argv", argv); b.main(); first = b.sha256(assignments)
        monkeypatch.setattr(sys, "argv", argv); b.main(); second = b.sha256(assignments)
    finally:
        b.EXPECTED_FINGERPRINT_SHA256 = old_expected
    assert first == second
    output = [json.loads(line) for line in assignments.read_text().splitlines()]
    assert output and output[0]["target_id"] and output[0]["controls"]
