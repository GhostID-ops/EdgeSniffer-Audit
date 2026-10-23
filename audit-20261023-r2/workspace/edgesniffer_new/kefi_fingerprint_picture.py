#!/usr/bin/env python3
"""Descriptive, outcome-blind kEFi fingerprint picture.

No threshold selection, outcome lookup, or PnL access. This summarizes the
observed entry behavior and the already-reconstructed structural exits.
"""
from __future__ import annotations
import argparse, hashlib, json, statistics
from collections import Counter
from pathlib import Path

def jsonl(p):
    with p.open(encoding="utf-8") as f:
        for line in f:
            if line.strip(): yield json.loads(line)

def sha256(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()

def quantiles(v):
    v=sorted(v)
    if not v: return {}
    def q(x): return v[min(len(v)-1, int((len(v)-1)*x))]
    return {"min":v[0],"p25":q(.25),"median":q(.5),"p75":q(.75),"p90":q(.9),"max":v[-1]}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--entries",required=True,type=Path)
    ap.add_argument("--exits",required=True,type=Path)
    ap.add_argument("--output",required=True,type=Path)
    ns=ap.parse_args()
    entries=list(jsonl(ns.entries)); exits=json.loads(ns.exits.read_text(encoding="utf-8"))
    bases=[r["base_amount"] for r in entries]
    reserves=[r["pre_pool_base"] for r in entries]
    quote_reserves=[r["pre_pool_quote"] for r in entries]
    quote_deltas=[r["pool_quote_delta"] for r in entries]
    fee_bps=[sum(r.get(k,0) for k in ("lp_fee_bps","protocol_fee_bps","coin_creator_bps")) for r in entries]
    mints=Counter(r["selected_mint"] for r in entries)
    pools=Counter(r["pool"] for r in entries)
    sides=Counter(r.get("side") for r in entries)
    exit_rows=[v["best_exit"]["net_quote_out"] for v in exits["results"].values() if v.get("best_exit")]
    return_bps=[v["best_exit"]["net_return_bps"] for v in exits["results"].values() if v.get("best_exit") and v["best_exit"].get("net_return_bps") is not None]
    report={
      "schema":"KEFI_FINGERPRINT_PICTURE_V1",
      "outcome_blind":True,"economic_outcomes_read":False,
      "threshold_selection_performed":False,"fresh_validation_performed":False,
      "entries":len(entries),"distinct_mints":len(mints),"distinct_pools":len(pools),
      "entry_sides":dict(sides),
      "recurring_entry_behavior":{
        "base_amount":quantiles(bases),"pre_pool_base":quantiles(reserves),
        "pre_pool_quote":quantiles(quote_reserves),"pool_quote_delta":quantiles(quote_deltas),
        "recorded_amm_fee_bps":quantiles(fee_bps),
      },
      "most_repeated_mints":mints.most_common(10),
      "most_repeated_pools":pools.most_common(10),
      "reconstructed_exit_summary":{
        "valid_candidates":exits["entries_with_valid_exit_candidates"],
        "without_valid_candidates":exits["entries_without_valid_exit_candidates"],
        "best_net_quote_out":quantiles(exit_rows),
        "best_exit_vs_recorded_entry_bps":quantiles(return_bps),
      },
      "comparison_status":"NO_PREDECLARED_NONMATCH_COHORT",
      "interpretation":"Descriptive entry fingerprint only; no predictive or profitability claim.",
      "entry_sha256":sha256(ns.entries),"exit_analysis_sha256":sha256(ns.exits),
    }
    ns.output.parent.mkdir(parents=True,exist_ok=True)
    ns.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("entries","distinct_mints","distinct_pools","comparison_status")},sort_keys=True))

if __name__=="__main__": main()
