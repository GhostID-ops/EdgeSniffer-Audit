#!/usr/bin/env python3
import json, math, statistics, hashlib
from pathlib import Path

ROOT=Path('/var/lib/8ycb-canonical')
ECO=ROOT/'economics'
DATA=ECO/'corrected-canonical-economic-history-v1.jsonl'
AUDIT=ECO/'quote-semantic-audit-v1.jsonl'
ART=ECO/'canonical-economic-artifact.jsonl'
OUT=ECO/'corrected-canonical-economic-history-report-v1.json'

def rows(p):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

def roi(x):
    return None if not x else x.get('net_roi_pct')

def stats(values):
    v=[float(x) for x in values if x is not None]
    if not v: return {'n':0,'wins':0,'win_rate_pct':None,'expectancy_pct':None,'profit_factor':None,'normalized_pnl_sol':None,'max_drawdown_sol':None}
    pos=sum(x for x in v if x>0); neg=sum(x for x in v if x<0)
    cur=peak=dd=0.0
    for x in v:
        cur += x/100.0*0.1
        peak=max(peak,cur); dd=max(dd,peak-cur)
    return {'n':len(v),'wins':sum(x>0 for x in v),'win_rate_pct':100.0*sum(x>0 for x in v)/len(v),'expectancy_pct':statistics.mean(v),'profit_factor':(pos/abs(neg) if neg else None),'normalized_pnl_sol':sum(x/100.0*0.1 for x in v),'max_drawdown_sol':dd}

def pctl(v,p):
    v=sorted(float(x) for x in v)
    if not v:return None
    if len(v)==1:return v[0]
    k=(len(v)-1)*p; f=math.floor(k); c=math.ceil(k)
    return v[f] if f==c else v[f]+(v[c]-v[f])*(k-f)

def main():
    data=rows(DATA); audit={r['canonical_ordinal']:r for r in rows(AUDIT)}
    arts={r['canonical_ordinal']:r for r in rows(ART)}
    by={r['canonical_ordinal']:r for r in data}
    # For #143–148 there is no legacy V4 artifact; the already-correct
    # canonical result is the unchanged comparison baseline.
    def legacy(n):
        r=by[n]; x=roi(r.get('legacy_primary'))
        if x is not None:return x
        a=arts.get(n,{}).get('primary')
        return roi(a)
    def corrected(n): return roi(by[n].get('corrected_primary'))
    def group(lo,hi):
        ns=list(range(lo,hi+1)); lv=[legacy(n) for n in ns]; cv=[corrected(n) for n in ns]
        return {'range':f'{lo}-{hi}','total':len(ns),'legacy':stats(lv),'corrected_known':stats(cv),'corrected_missing':[n for n,x in zip(ns,cv) if x is None]}
    groups={'full':group(1,148),'1_100':group(1,100),'101_plus':group(101,148),'last_50':group(99,148),'last_20':group(129,148),'last_10':group(139,148)}
    common=[(n,legacy(n),corrected(n)) for n in range(1,149) if legacy(n) is not None and corrected(n) is not None]
    affected=[(n,legacy(n),corrected(n)) for n in range(1,149) if audit[n].get('legacy_used_max_cost_instead') and corrected(n) is not None]
    deltas=[c-l for _,l,c in common]
    flips={'losses_flipped_to_wins':sum(l<=0 and c>0 for _,l,c in common),'wins_flipped_to_losses':sum(l>0 and c<=0 for _,l,c in common),'unchanged_classifications':sum((l>0)==(c>0) for _,l,c in common)}
    no_legacy=[n for n in range(1,149) if audit[n].get('evidence_status')=='COMPLETE' and audit[n].get('legacy_v4_input_lamports') is None]
    matched_not_affected=[n for n,r in audit.items() if r.get('legacy_matches_executed_quote') is True]
    result={
      'dataset':str(DATA),'dataset_sha256':hashlib.sha256(DATA.read_bytes()).hexdigest(),'total_canonical_launches':148,
      'total_economically_finalized_legacy_or_canonical':148,'corrected_known_count':sum(corrected(n) is not None for n in range(1,149)),
      'corrected_missing_count':sum(corrected(n) is None for n in range(1,149)),'corrected_missing_ordinals':[n for n in range(1,149) if corrected(n) is None],
      'legacy_quote_error_count':sum(r.get('legacy_used_max_cost_instead') is True for r in audit.values()),
      'complete_quote_evidence_count':sum(r.get('evidence_status')=='COMPLETE' for r in audit.values()),
      'insufficient_quote_evidence_count':sum(r.get('evidence_status')!='COMPLETE' for r in audit.values()),
      'groups':groups,'common_corrected_comparison_n':len(common),'affected_corrected_comparison_n':len(affected),
      'roi_delta_all_common_pct':{'mean':statistics.mean(deltas) if deltas else None,'median':statistics.median(deltas) if deltas else None,'p10':pctl(deltas,.10),'p90':pctl(deltas,.90)},
      'roi_delta_affected_pct':{'mean':statistics.mean([c-l for _,l,c in affected]) if affected else None,'median':statistics.median([c-l for _,l,c in affected]) if affected else None,'p10':pctl([c-l for _,l,c in affected],.10),'p90':pctl([c-l for _,l,c in affected],.90)},
      **flips,
      'affected_ordinals':[n for n,r in audit.items() if r.get('legacy_used_max_cost_instead') is True],
      'not_affected_ordinals':sorted(matched_not_affected+no_legacy),'not_affected_count':len(matched_not_affected)+len(no_legacy),
      'legacy_v4_no_baseline_ordinals':no_legacy,
      'insufficient_quote_ordinals':[n for n,r in audit.items() if r.get('evidence_status')!='COMPLETE'],
      'control_results_143_148':{str(n):{'corrected_roi_pct':corrected(n),'exit_reason':by[n].get('corrected_primary',{}).get('exit_reason')} for n in range(143,149)}
    }
    OUT.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    print(json.dumps(result,sort_keys=True,indent=2))
if __name__=='__main__':main()
