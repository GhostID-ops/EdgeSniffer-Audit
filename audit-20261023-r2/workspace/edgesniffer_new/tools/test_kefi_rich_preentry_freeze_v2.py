import importlib.util
from pathlib import Path
PATH=Path(__file__).with_name('kefi_rich_preentry_freeze_v2.py'); SPEC=importlib.util.spec_from_file_location('freeze',PATH); MOD=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MOD)
def test_sha_stable(tmp_path):
 p=tmp_path/'x';p.write_text('x');assert MOD.sha(p)==MOD.sha(p)
