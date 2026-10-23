#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,statistics
from collections import Counter
from pathlib import Path

def q(xs):
 xs=sorted(xs)
 if not xs:return {}
 def at(p):return xs[min(len(xs)-1,int((len(xs)-1)*p))]
 return {'min':xs[0],'p25':at(.25),'median':at(.5),'p75':at(.75),'p90':at(.9),'max':xs[-1]}
def jsonl(p):
 with p.open(encoding='utf-8') as f:
  for l in f:
   if l.strip():yield json.loads(l)
def profile(rs):
 return {'base_amount':q([r['base_amount'] for r in rs]),'pre_pool_base':q([r['pre_pool_base'] for r in rs]),'pre_pool_quote':q([r['pre_pool_quote'] for r in rs]),'pool_quote_delta':q([r['pool_quote_delta'] for r in rs]),'amm_fee_bps':q([r['lp_fee_bps']+r['protocol_fee_bps']+r['coin_creator_bps'] for r in rs])}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--selected',required=True,type=Path);ap.add_argument('--controls',required=True,type=Path);ap.add_argument('--output',required=True,type=Path);n=ap.parse_args()
 s=list(jsonl(n.selected));c=list(jsonl(n.controls));d={'schema':'KEFI_CONTEXT_COMPARISON_V1','selected_count':len(s),'control_count':len(c),'control_bias':'CONTROLS COME FROM SELECTED-CENTERED WINDOWS; NOT VALIDATION','selected_profile':profile(s),'control_profile':profile(c),'interpretation':'Exploratory contextual contrast only; no predictive threshold or performance verdict.'};n.output.write_text(json.dumps(d,indent=2,sort_keys=True)+'\n',encoding='utf-8');print(json.dumps(d,sort_keys=True))
if __name__=='__main__':main()
