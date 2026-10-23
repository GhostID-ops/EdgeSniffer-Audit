import importlib.util
from pathlib import Path


PATH = Path(__file__).with_name("kefi_rich_preentry_receipt_v1.py")
SPEC = importlib.util.spec_from_file_location("receipt", PATH)
MOD = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MOD)


def test_sha_is_stable(tmp_path):
    path = tmp_path / "artifact.json"
    path.write_text('{"x":1}\n')
    assert MOD.sha(path) == MOD.sha(path)
