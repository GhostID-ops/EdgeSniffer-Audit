import json,subprocess,sys,tempfile
from pathlib import Path
SCRIPT=Path(__file__).with_name('evidence_lake_live_dependency_snapshot_v1.py')
def test_snapshot_identifies_open_and_next_raw_pair():
 with tempfile.TemporaryDirectory() as raw:
  r=Path(raw); s=r/'src'; w=r/'work'; base=s/'source-spool/partitions/2026/10/08'
  for hour in ('00','01','02'):
   p=base/hour;p.mkdir(parents=True);(p/'manifest.json').write_text('{"temporalHourClosed":true}')
  (base/'03').mkdir();(base/'03'/'evidence.frames.partial').write_bytes(b'x')
  for p in [w/'out/kefi-market-controls-v1/state.json',w/'out/kefi-market-controls-catchup-v1/state.json']:
   p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{"processed":["2026/10/08/00__2026/10/08/01"]}')
  out=r/'snapshot.json'; x=subprocess.run([sys.executable,str(SCRIPT),'--source-root',str(s),'--work-root',str(w),'--output',str(out)],text=True,capture_output=True)
  assert x.returncode==0 and json.loads(x.stdout)['next_pair']['pair']=='2026/10/08/01__2026/10/08/02'
