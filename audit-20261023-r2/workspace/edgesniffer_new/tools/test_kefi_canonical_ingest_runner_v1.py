import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "kefi_canonical_ingest_runner_v1.py"


def test_source_health_rejects_stale_or_gap(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("runner", SCRIPT); assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
    class Done: returncode = 0
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: Done())
    health = tmp_path / "health.json"
    health.write_text('{"latestReceivedAtUtc":"2000-01-01T00:00:00Z","continuityGaps":0,"receiveErrors":0,"reconnects":0}')
    assert runner.source_is_fresh(tmp_path, "collector")[0] is False
    health.write_text('{"latestReceivedAtUtc":"2999-01-01T00:00:00Z","continuityGaps":1,"receiveErrors":0,"reconnects":0}')
    assert runner.source_is_fresh(tmp_path, "collector")[0] is False
