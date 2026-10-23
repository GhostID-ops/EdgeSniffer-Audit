import json, subprocess, sys, tempfile
from pathlib import Path

SCRIPT=Path(__file__).with_name("evidence_lake_precise_pin_plan_v1.py")

def test_plan_retains_broad_pin_until_dynamic_coverage_exists():
  with tempfile.TemporaryDirectory() as raw:
    r=Path(raw); src=r/'source'; work=r/'work'; p=src/'source-spool/partitions/2026/10/08/00'; p.mkdir(parents=True)
    (p/'manifest.json').write_text(json.dumps({'temporalHourClosed':True}))
    q=src/'source-spool/partitions/2026/10/08/01'; q.mkdir(parents=True); (q/'manifest.json').write_text(json.dumps({'temporalHourClosed':True}))
    for path in [work/'out/kefi-market-controls-v1/state.json',work/'out/kefi-market-controls-catchup-v1/state.json']:
      path.parent.mkdir(parents=True,exist_ok=True); path.write_text('{"processed":[]}')
    out=r/'plan.json'
    x=subprocess.run([sys.executable,str(SCRIPT),'--source-root',str(src),'--work-root',str(work),'--output',str(out)],text=True,capture_output=True)
    assert x.returncode==0 and json.loads(x.stdout)['status']=='RETAIN_BROAD_PIN'
