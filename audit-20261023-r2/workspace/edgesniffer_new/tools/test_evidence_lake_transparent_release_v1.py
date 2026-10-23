import hashlib, json, subprocess, sys, tempfile
from pathlib import Path

SCRIPT=Path(__file__).with_name('evidence_lake_transparent_release_v1.py')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run(local,archive,receipt,audit,out,mode):
 return subprocess.run([sys.executable,str(SCRIPT),'--local-payload',str(local),'--archive-payload',str(archive),'--archive-receipt',str(receipt),'--dependency-audit',str(audit),'--release-receipt-dir',str(out),'--mode',mode],text=True,capture_output=True)
def test_release_requires_proven_audit_and_preserves_path_contents():
 with tempfile.TemporaryDirectory() as raw:
  r=Path(raw); local=r/'frames.zst'; archive=r/'archive.zst'; local.write_bytes(b'raw'); archive.write_bytes(b'raw')
  receipt=r/'archive.json'; receipt.write_text(json.dumps({'status':'NFS_VERIFIED'})); audit=r/'audit.json'; audit.write_text(json.dumps({'outcome':'DEPENDENCY_NONE_PROVEN'}))
  assert run(local,archive,receipt,audit,r/'out','VERIFY').returncode==0
  released=run(local,archive,receipt,audit,r/'out','RELEASE'); assert released.returncode==0
  assert local.is_symlink() and local.read_bytes()==b'raw'
def test_release_fails_for_unknown_audit():
 with tempfile.TemporaryDirectory() as raw:
  r=Path(raw); local=r/'frames.zst'; archive=r/'archive.zst'; local.write_bytes(b'raw'); archive.write_bytes(b'raw')
  receipt=r/'archive.json'; receipt.write_text(json.dumps({'status':'NFS_VERIFIED'})); audit=r/'audit.json'; audit.write_text(json.dumps({'outcome':'DEPENDENCY_UNKNOWN'}))
  assert run(local,archive,receipt,audit,r/'out','VERIFY').returncode==2 and not local.is_symlink()
