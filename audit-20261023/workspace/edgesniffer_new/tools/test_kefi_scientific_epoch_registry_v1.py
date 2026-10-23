import importlib.util
from pathlib import Path

import pytest


REGISTRY = Path(__file__).parents[1] / "scripts" / "kefi_scientific_epoch_registry_v1.py"


def module():
    spec = importlib.util.spec_from_file_location("kefi_epochs", REGISTRY)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def record(epoch_id: str) -> dict:
    return {
        "epoch_id": epoch_id,
        "kind": "OPERATIONAL",
        "status": "OPERATIONAL_ONLY",
        "start_utc": "2026-10-12T00:00:00Z",
        "end_utc": "",
        "reason": "test",
    }


def test_registry_is_hash_chained_and_append_only(tmp_path):
    epochs = module()
    first_payload = record("one"); first_payload["end_utc"] = "2026-10-12T00:00:01Z"
    second_payload = record("two"); second_payload["start_utc"] = "2026-10-12T00:00:02Z"
    first = epochs.append(tmp_path, first_payload)
    second = epochs.append(tmp_path, second_payload)
    assert second["previous_record_sha256"] == first["record_sha256"]
    epochs.verify(tmp_path)
    assert (tmp_path / "records" / "one.json").read_text()


def test_duplicate_or_tampered_epoch_fails_closed(tmp_path):
    epochs = module()
    epochs.append(tmp_path, record("one"))
    try:
        epochs.append(tmp_path, record("one"))
    except RuntimeError as error:
        assert "epoch already exists" in str(error)
    else:
        raise AssertionError("duplicate epoch was accepted")
    journal = tmp_path / "epochs.jsonl"
    journal.write_text(journal.read_text().replace("OPERATIONAL_ONLY", "UNOPENED"))
    try:
        epochs.verify(tmp_path)
    except RuntimeError as error:
        assert "FAIL_CLOSED" in str(error)
    else:
        raise AssertionError("tampered journal was accepted")


def test_future_cohort_eligibility_rejects_quarantine_and_allows_unopened(tmp_path):
    epochs = module()
    def add(epoch_id, kind, status, start, end):
        epochs.append(tmp_path, {"epoch_id": epoch_id, "kind": kind, "status": status,
            "start_utc": start, "end_utc": end, "reason": "test"})
    add("h1", "H1_OPENED", "DEVELOPMENT_ONLY", "2026-10-10T00:00:00Z", "2026-10-10T01:00:00Z")
    add("h2", "H2_QUARANTINED", "QUARANTINED", "2026-10-10T02:00:00Z", "2026-10-10T03:00:00Z")
    add("ops", "OPERATIONAL", "OPERATIONAL_ONLY", "2026-10-10T04:00:00Z", "2026-10-10T05:00:00Z")
    add("future", "FUTURE_UNOPENED", "UNOPENED", "2026-10-10T06:00:00Z", "")
    cases = [
        ("2026-10-10T00:30:00Z", "2026-10-09T00:00:00Z", "INELIGIBLE_EPOCH_H1_OPENED"),
        ("2026-10-10T02:30:00Z", "2026-10-09T00:00:00Z", "INELIGIBLE_EPOCH_H2_QUARANTINED"),
        ("2026-10-10T04:30:00Z", "2026-10-09T00:00:00Z", "INELIGIBLE_EPOCH_OPERATIONAL"),
        ("2026-10-10T05:30:00Z", "2026-10-10T06:00:00Z", "INELIGIBLE_PRE_BOUNDARY"),
    ]
    for at, boundary, expected in cases:
        try:
            epochs.eligible(tmp_path, at, boundary)
        except RuntimeError as error:
            assert expected in str(error)
        else:
            raise AssertionError(expected + " was accepted")
    epochs.eligible(tmp_path, "2026-10-10T06:30:00Z", "2026-10-10T06:00:00Z")


def test_exact_fractional_timestamp_boundary_is_not_pre_boundary(tmp_path):
    epochs = module()
    epochs.append(tmp_path, {"epoch_id": "future", "kind": "FUTURE_UNOPENED", "status": "UNOPENED",
                             "start_utc": "2026-10-10T06:00:00Z", "end_utc": "", "reason": "test"})
    epochs.eligible(tmp_path, "2026-10-10T06:00:00.000000Z", "2026-10-10T06:00:00Z")


def test_eligibility_requires_exactly_one_future_unopened_epoch(tmp_path):
    epochs = module()
    def add(epoch_id, kind, status, start, end=""):
        epochs.append(tmp_path, {"epoch_id": epoch_id, "kind": kind, "status": status,
                                 "start_utc": start, "end_utc": end, "reason": "test"})
    add("future-a", "FUTURE_UNOPENED", "UNOPENED", "2026-10-10T00:00:00Z", "2026-10-10T01:00:00Z")
    for at, boundary, expected in [("2026-10-09T23:59:59Z", "2026-10-10T00:00:00Z", "PRE_BOUNDARY"),
                                   ("2026-10-10T01:30:00Z", "2026-10-09T00:00:00Z", "GAP")]:
        try: epochs.eligible(tmp_path, at, boundary)
        except RuntimeError as error: assert expected in str(error)
        else: raise AssertionError("invalid membership accepted")
    with pytest.raises(RuntimeError, match="overlapping epoch membership"):
        add("future-b", "FUTURE_UNOPENED", "UNOPENED", "2026-10-10T00:30:00Z", "2026-10-10T02:00:00Z")


def test_invalid_kind_status_combination_fails_closed(tmp_path):
    epochs = module()
    try:
        epochs.append(tmp_path, {"epoch_id": "bad", "kind": "FUTURE_UNOPENED", "status": "QUARANTINED",
                                 "start_utc": "2026-10-10T00:00:00Z", "end_utc": "", "reason": "test"})
    except RuntimeError as error:
        assert "kind/status" in str(error)
    else:
        raise AssertionError("invalid kind/status accepted")


def test_voided_cohort_is_immutably_closed_before_a_fresh_future_epoch(tmp_path):
    epochs = module()
    first = epochs.append(tmp_path, {"epoch_id": "voided", "kind": "FUTURE_UNOPENED", "status": "UNOPENED",
                                     "start_utc": "2026-10-10T00:00:00Z", "end_utc": "", "reason": "test"})
    closure = epochs.void_epoch(tmp_path, "voided", "2026-10-10T01:00:00Z", "hard wall")
    assert closure["target_record_sha256"] == first["record_sha256"]
    epochs.append(tmp_path, {"epoch_id": "fresh", "kind": "FUTURE_UNOPENED", "status": "UNOPENED",
                             "start_utc": "2026-10-10T02:00:00Z", "end_utc": "", "reason": "fresh"})
    epochs.eligible(tmp_path, "2026-10-10T02:01:00Z", "2026-10-10T02:00:00Z")
    with pytest.raises(RuntimeError, match="VOID"):
        epochs.eligible(tmp_path, "2026-10-10T00:30:00Z", "2026-10-10T00:00:00Z")
    with pytest.raises(RuntimeError, match="GAP"):
        epochs.eligible(tmp_path, "2026-10-10T01:30:00Z", "2026-10-10T00:00:00Z")
    epochs.verify(tmp_path)
