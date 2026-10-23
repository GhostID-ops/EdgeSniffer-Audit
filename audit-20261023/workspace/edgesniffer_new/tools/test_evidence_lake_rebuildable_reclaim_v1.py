import json,subprocess,sys,tempfile
from pathlib import Path
SCRIPT=Path(__file__).with_name('evidence_lake_rebuildable_reclaim_v1.py')
def test_requires_exact_approved_target_and_writes_receipt():
 with tempfile.TemporaryDirectory() as raw:
  root=Path(raw); target=root/'lab'/'rust';target.mkdir(parents=True);(target/'x').write_bytes(b'x')
  r=subprocess.run([sys.executable,str(SCRIPT),'--path',str(target),'--expected-path',str(target),'--receipt-dir',str(root/'receipts'),'--apply'],text=True,capture_output=True)
  assert r.returncode==0 and not target.exists() and Path(json.loads(r.stdout)['receipt']).is_file()
def test_refuses_noncache_namespace():
 with tempfile.TemporaryDirectory() as raw:
  target=Path(raw)/'evidence';target.mkdir();(target/'x').write_text('x')
  r=subprocess.run([sys.executable,str(SCRIPT),'--path',str(target),'--expected-path',str(target),'--receipt-dir',str(Path(raw)/'r'),'--apply'],text=True,capture_output=True)
  assert r.returncode==2 and target.exists()
