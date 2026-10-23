#!/usr/bin/env python3
"""Discovery-only Pump curve surface for the canonical new-wallet census."""
from __future__ import annotations
import argparse, json, statistics
from collections import defaultdict
from pathlib import Path
import exactCurveEconomicsV1 as curve

SIZES=(100_000_000,500_000_000,1_000_000_000); HORIZONS=(5,10,15,30,60,120,300); WSOL=next(iter(curve.SOL_QUOTE_MINTS))
def state(row): return curve.CurveState(**{k:int(v) for k,v in row.items() if k in {'virtual_sol','virtual_token','real_sol','real_token'}})
def summarize(values, attempted):
    p=[x for x in values if x is not None]; wins=[x for x in p if x>0]; losses=[x for x in p if x<0]; gross_win=sum(wins); gross_loss=-sum(losses)
    return {'attempted':attempted,'actionable':len(p),'pnl_sol':sum(p)/1e9,'roi_pct_mean':(statistics.mean(p)/1e7 if p else None),'win_rate_pct':(len(wins)*100/len(p) if p else None),'profit_factor':(gross_win/gross_loss if gross_loss else None),'max_loss_sol':(min(p)/1e9 if p else None)}
def main():
    p=argparse.ArgumentParser(); p.add_argument('--events',type=Path,required=True); p.add_argument('--out',type=Path,required=True); a=p.parse_args()
    rows=[json.loads(x) for x in a.events.read_text().splitlines() if x.strip()]; eligible=[r for r in rows if r.get('underlying_venue')=='PUMP_BONDING_CURVE' and r.get('quote_mint') in curve.SOL_QUOTE_MINTS and r.get('side')=='BUY']
    groups=defaultdict(list)
    for r in rows:
        if r.get('underlying_venue')=='PUMP_BONDING_CURVE' and r.get('quote_mint') in curve.SOL_QUOTE_MINTS: groups[(r.get('route'),r.get('mint'))].append(r)
    for g in groups.values(): g.sort(key=lambda r:(int(r.get('block_time') or 0),int(r.get('event_index') or 0)))
    surface={}
    for route in sorted({r.get('route') for r in eligible}):
        buys=[r for r in eligible if r.get('route')==route]; surface[route]={}
        for size in SIZES:
            sk=str(size/1e9); surface[route][sk]={}
            for horizon in HORIZONS:
                vals=[]; failures=0
                for buy in buys:
                    g=groups[(buy.get('route'),buy.get('mint'))]; t=int(buy.get('block_time') or 0); entry=next((r for r in g if int(r.get('block_time') or 0)>t),None)
                    if not entry: failures+=1; vals.append(None); continue
                    try:
                        ev=curve.Fees(protocol_bps=int(entry['fees']['fee_bps']),creator_bps=int(entry['fees']['creator_bps']),lp_bps=0); q=curve.quote_exact_quote_in_buy(state(entry['pre_state']),size,ev); mark=next((r for r in g if int(r.get('block_time') or 0)>=int(entry.get('block_time') or 0)+horizon),None)
                        if not mark: failures+=1; vals.append(None); continue
                        sell=curve.quote_exact_token_sell(state(mark['post_state']),q.token_out,0,ev); vals.append(int(sell.immediate_quote_out)-size)
                    except Exception: failures+=1; vals.append(None)
                surface[route][sk][str(horizon)]={**summarize(vals,len(buys)),'failed_or_missing':failures}
    out={'schema':'WALLET_PUMP_CURVE_ECONOMICS_SURFACE_DISCOVERY_V1','read_only':True,'outcome_source':'canonical_historical_discovery','policy_status':'hypotheses_only; no policy selected or frozen','timing_basis':'block_time_seconds; not live receive-clock parity','notionals_sol':[.1,.5,1.0],'fixed_exit_seconds':list(HORIZONS),'eligible_buy_events':len(eligible),'routes':surface}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n'); print(json.dumps({'status':'WALLET_PUMP_CURVE_SURFACE_COMPLETE','eligible_buy_events':len(eligible),'routes':list(surface)}))
if __name__=='__main__': main()
