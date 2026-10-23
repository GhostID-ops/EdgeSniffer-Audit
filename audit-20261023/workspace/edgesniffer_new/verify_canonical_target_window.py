#!/usr/bin/env python3
"""Prove retained-source coverage for canonical #143-147 without estimation."""
from __future__ import annotations
import json, re, sys
from pathlib import Path

SOURCE = Path('/mnt/HC_Volume_106775198/edgesniffer-research/2cq-yellowstone-full-meta-prospective-v1')
ROOT = Path('/var/lib/8ycb-canonical')
FLOOR = '20260922T180000Z'
# #147's canonical block time is 19:41:38Z; 20:00Z is a conservative
# closed-source cutoff beyond its full 120-second frozen horizon. Current
# .partial/promoted files after this cutoff are not needed to prove history.
CUTOFF = '20260922T200000Z'
TARGETS = {143,144,145,146,147}

def main():
    state = json.loads((ROOT/'pump-tape/source-scan-state.json').read_text()) if (ROOT/'pump-tape/source-scan-state.json').exists() else {}
    ledger = [json.loads(x) for x in (ROOT/'launch-identity-ledger.jsonl').read_text().splitlines() if x.strip()]
    target_rows = [r for r in ledger if int(r.get('canonical_ordinal',0)) in TARGETS]
    files = [p for p in SOURCE.rglob('evidence.frames*') if p.is_file()]
    relevant=[]
    for p in files:
        s=str(p)
        if '/partitions/2026/09/22/' in s:
            hour=int(re.search(r'/22/(\d\d)/',s).group(1))
            if 18 <= hour < 20: relevant.append(p)
        elif '/promoted-windows/' in s:
            m=re.search(r'auto-(\d{8}T\d{6}Z)-',s)
            if m and FLOOR <= m.group(1) < CUTOFF: relevant.append(p)
    missing=[]
    for p in relevant:
        st=p.stat(); marker={'size':st.st_size,'mtime_ns':st.st_mtime_ns}
        if state.get(str(p)) != marker: missing.append(str(p))
    health=json.loads((ROOT/'pump-tape/health.json').read_text()) if (ROOT/'pump-tape/health.json').exists() else {}
    max_slot=int(health.get('max_source_slot') or 0)
    max_target=max(int(r.get('slot') or 0) for r in target_rows)
    tape_rows=[json.loads(x) for x in (ROOT/'pump-tape/evidence.jsonl').read_text().splitlines() if x.strip()] if (ROOT/'pump-tape/evidence.jsonl').exists() else []
    counts={n:sum(int(r.get('canonical_ordinal',-1))==n for r in tape_rows) for n in TARGETS}
    complete=not missing and max_slot >= max_target+1000 and all(counts[n] > 0 for n in TARGETS if n in TARGETS)
    out={'schema':'8ycb-canonical-target-window-coverage-v1','status':'PASS' if complete else 'PENDING','floor_utc':'2026-09-22T18:00:00Z','target_ordinals':sorted(TARGETS),'relevant_source_file_count':len(relevant),'unscanned_relevant_source_files':missing,'max_source_slot':max_slot,'max_target_slot':max_target,'tape_rows_by_ordinal':counts,'no_interpolation':True}
    path=ROOT/'pump-tape/target-window-coverage.json'; path.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
    print(json.dumps(out,sort_keys=True))
    return 0 if complete else 2
if __name__=='__main__': sys.exit(main())
