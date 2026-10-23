import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
GUARD = ROOT / "scripts" / "kefi_full_meta_capacity_guard_v1.py"


def module():
    spec = importlib.util.spec_from_file_location("kefi_guard", GUARD)
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_unknown_or_failed_stop_is_not_a_verified_pause(monkeypatch):
    guard = module()

    def run(command, **_):
        if "stop" in command:
            return Result(1, stderr="Unit does not exist")
        return Result(0, "LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\n")

    monkeypatch.setattr(guard.subprocess, "run", run)
    assert guard.verified_stop(["systemctl"], "missing.service")["verifiedStopped"] is False


def test_stop_failure_with_otherwise_perfect_unit_state_is_not_verified(monkeypatch):
    guard = module()

    def run(command, **_):
        if "stop" in command:
            return Result(1, stderr="permission denied")
        return Result(0, "LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\n")

    monkeypatch.setattr(guard.subprocess, "run", run)
    assert guard.verified_stop(["systemctl"], "collector.service")["verifiedStopped"] is False


def test_active_or_nonzero_pid_is_not_verified(monkeypatch):
    guard = module()

    def run(command, **_):
        if "stop" in command:
            return Result(0)
        return Result(0, "LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=123\n")

    monkeypatch.setattr(guard.subprocess, "run", run)
    assert guard.verified_stop(["systemctl"], "collector.service")["verifiedStopped"] is False


def test_show_failure_is_not_verified(monkeypatch):
    guard = module()

    def run(command, **_):
        return Result(0) if "stop" in command else Result(1, stderr="dbus error")

    monkeypatch.setattr(guard.subprocess, "run", run)
    assert guard.verified_stop(["systemctl"], "collector.service")["verifiedStopped"] is False


def test_missing_hot_root_becomes_an_emergency_input(monkeypatch, tmp_path):
    guard = module()

    def raise_missing(_):
        raise FileNotFoundError("mount disappeared")

    monkeypatch.setattr(guard, "volume", raise_missing)
    measured, error = guard.safe_volume(tmp_path / "missing-hot-root")
    assert measured is None
    assert error["reason"] == "STATVFS_FAILED"


def test_statvfs_failure_reaches_verified_collector_stop(monkeypatch, tmp_path):
    guard = module()
    calls = []
    writes = []
    monkeypatch.setattr(guard, "safe_volume", lambda path: (
        (None, {"path": str(path), "reason": "STATVFS_FAILED"})
        if "hot" in str(path) else ({"path": str(path), "freeGiB": 500}, None)
    ))
    monkeypatch.setattr(guard, "mounted_source", lambda _: {"findmntReturncode": 0, "source": "/dev/vdc1"})
    monkeypatch.setattr(guard, "verified_stop", lambda _, unit: calls.append(unit) or {
        "unit": unit, "verifiedStopped": True
    })
    monkeypatch.setattr(guard, "atomic", lambda _, value: writes.append(value))
    monkeypatch.setattr(sys, "argv", ["guard", "--hot-root", str(tmp_path / "hot"),
        "--host-mount", str(tmp_path), "--state", str(tmp_path / "state.json"),
        "--collector-unit", "collector.service", "--materializer-timer-unit", "timer.unit",
        "--materializer-service-unit", "service.unit", "--hot-mount", str(tmp_path),
        "--expected-hot-source", "/dev/vdc1", "--systemd-scope", "system"])
    assert guard.main() == 0
    assert calls == ["timer.unit", "service.unit", "collector.service"]
    assert writes[-1]["status"] == "EMERGENCY_PAUSED_VERIFIED"
    assert writes[-1]["paused"] is True


def test_warning_requires_timer_and_service_to_stop(monkeypatch, tmp_path):
    guard = module()
    calls = []
    writes = []
    monkeypatch.setattr(guard, "safe_volume", lambda path: ({"path": str(path), "freeGiB": 80}, None))
    monkeypatch.setattr(guard, "mounted_source", lambda _: {"findmntReturncode": 0, "source": "/dev/vdc1"})

    def stop(_, unit):
        calls.append(unit)
        return {"unit": unit, "verifiedStopped": unit != "service.unit"}

    monkeypatch.setattr(guard, "verified_stop", stop)
    monkeypatch.setattr(guard, "atomic", lambda _, value: writes.append(value))
    monkeypatch.setattr(sys, "argv", ["guard", "--hot-root", str(tmp_path / "hot"),
        "--host-mount", str(tmp_path), "--state", str(tmp_path / "state.json"),
        "--collector-unit", "collector.service", "--materializer-timer-unit", "timer.unit",
        "--materializer-service-unit", "service.unit", "--hot-mount", str(tmp_path),
        "--expected-hot-source", "/dev/vdc1", "--systemd-scope", "system",
        "--warning-free-gib", "100", "--emergency-free-gib", "70"])
    assert guard.main() == 0
    assert calls == ["timer.unit", "service.unit"]
    assert writes[-1]["status"] == "WARNING_MATERIALIZER_STOP_FAILED"
    assert writes[-1]["paused"] is False


def test_loaded_inactive_unit_requires_successful_stop_return_code(monkeypatch):
    guard = module()

    def run(command, **_):
        if "stop" in command:
            return Result(0)
        return Result(0, "LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\n")

    monkeypatch.setattr(guard.subprocess, "run", run)
    assert guard.verified_stop(["systemctl"], "collector.service")["verifiedStopped"] is True
