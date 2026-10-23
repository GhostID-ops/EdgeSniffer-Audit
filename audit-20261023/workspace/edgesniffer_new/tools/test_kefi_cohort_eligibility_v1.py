import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[1]
REGISTRY = ROOT / "scripts" / "kefi_scientific_epoch_registry_v1.py"
GUARD = ROOT / "scripts" / "kefi_cohort_eligibility_v1.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ns(iso: str) -> int:
    import datetime as dt
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1_000_000_000)


def test_guard_enforces_epoch_and_boundary_policy(tmp_path):
    epochs, guard = load(REGISTRY, "epochs"), load(GUARD, "guard")
    def add(epoch_id, kind, status, start, end=""):
        epochs.append(tmp_path, {"epoch_id":epoch_id,"kind":kind,"status":status,"start_utc":start,"end_utc":end,"reason":"test"})
    add("h1", "H1_OPENED", "DEVELOPMENT_ONLY", "2026-10-10T00:00:00Z", "2026-10-10T01:00:00Z")
    add("h2", "H2_QUARANTINED", "QUARANTINED", "2026-10-10T02:00:00Z", "2026-10-10T03:00:00Z")
    add("ops", "OPERATIONAL", "OPERATIONAL_ONLY", "2026-10-10T04:00:00Z", "2026-10-10T05:00:00Z")
    add("future", "FUTURE_UNOPENED", "UNOPENED", "2026-10-10T06:00:00Z")
    for at, boundary, expected in [("2026-10-10T00:30:00Z", "2026-10-09T00:00:00Z", "H1_OPENED"),
                                   ("2026-10-10T02:30:00Z", "2026-10-09T00:00:00Z", "H2_QUARANTINED"),
                                   ("2026-10-10T04:30:00Z", "2026-10-09T00:00:00Z", "OPERATIONAL"),
                                   ("2026-10-10T05:30:00Z", "2026-10-10T06:00:00Z", "PRE_BOUNDARY")]:
        try: guard.assert_eligible(REGISTRY, tmp_path, boundary, ns(at))
        except RuntimeError as error: assert expected in str(error)
        else: raise AssertionError("ineligible evidence was accepted")
    guard.assert_eligible(REGISTRY, tmp_path, "2026-10-10T06:00:00Z", ns("2026-10-10T06:30:00Z"))
