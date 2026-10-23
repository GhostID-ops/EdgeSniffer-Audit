import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dedup", ROOT / "scripts/dedupKefi13ArmProspectiveObservationBatchV1.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def test_identical_overlap_is_dropped_and_conflict_fails():
    manifest = {"partitions": []}
    row = {"observation_id": "a", "state": {"x": 1}}
    fresh, report = module.run(manifest, [row, row])
    assert len(fresh) == 1
    assert report["identical_overlaps_dropped"] == 1
    changed = {"observation_id": "a", "state": {"x": 2}}
    fresh, report = module.run(manifest, [row, changed])
    assert report["status"] == "FAIL_CONFLICTING_DUPLICATE"
    assert report["conflicts"]


def test_equivalent_overlap_window_is_dropped():
    manifest = {"partitions": []}
    row = {
        "observation_id": "a",
        "state": {"raw_evidence": {"event_signature": "sig", "window_id": "w1", "window_manifest_sha256": "m1"}},
    }
    duplicate = {
        "observation_id": "a",
        "state": {"raw_evidence": {"event_signature": "sig", "window_id": "w2", "window_manifest_sha256": "m2"}},
    }
    fresh, report = module.run(manifest, [row, duplicate])
    assert len(fresh) == 1
    assert report["identical_overlaps_dropped"] == 1
    assert report["status"] == "PASS"
