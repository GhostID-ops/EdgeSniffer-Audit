import subprocess
import sys
from pathlib import Path


RUNNER = Path(__file__).parents[1] / "scripts" / "kefi_scientific_materializer_runner_v1.py"


def test_runner_requires_all_scientific_dependencies(tmp_path):
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--materializer", str(tmp_path / "missing"),
         "--root", str(tmp_path / "root"), "--out", str(tmp_path / "out"),
         "--decoder", str(tmp_path / "decoder"), "--registry-script", str(tmp_path / "registry.py"),
         "--registry-root", str(tmp_path / "registry"), "--boundary-utc", "2026-10-12T00:00:00Z"],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "FAIL_CLOSED" in result.stderr
