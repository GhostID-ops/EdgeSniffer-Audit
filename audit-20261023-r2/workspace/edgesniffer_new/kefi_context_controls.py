#!/usr/bin/env python3
"""Build a clearly-labeled exploratory control set from replayed context.

This is not the final independent candidate universe: it is limited to the
already-replayed 120-second contexts around selected entries. It is useful for
pipeline validation while the full closed-partition candidate scan is built.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def rows(p):
    with p.open(encoding="utf-8") as f:
        for line in f:
            if line.strip(): yield json.loads(line)

def sha256(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--entries",required=True,type=Path); ap.add_argument("--replay",required=True,type=Path,nargs="+"); ap.add_argument("--output",required=True,type=Path); ns=ap.parse_args()
    selected={r["signature"] for r in rows(ns.entries)}; seen=set(); out=[]
    for p in ns.replay:
        for r in rows(p):
            if r.get("side") != "BUY" or r.get("signature") in selected: continue
            key=(r.get("signature"),r.get("event_index"))
            if key in seen: continue
            seen.add(key)
            out.append({"candidate_signature":r["signature"],"event_index":r["event_index"],"mint":r.get("target_mint"),"pool":r.get("target_pool"),"source_frame_sha256":r.get("source_frame_sha256"),"slot":r.get("slot"),"received_at_unix_ns":r.get("received_at_unix_ns"),"window_offset_ns":r.get("window_offset_ns"),"base_amount":r.get("base_amount"),"pre_pool_base":r.get("pre_pool_base"),"pre_pool_quote":r.get("pre_pool_quote"),"virtual_quote_reserves":r.get("virtual_quote_reserves"),"pool_quote_delta":r.get("pool_quote_delta"),"lp_fee_bps":r.get("lp_fee_bps"),"protocol_fee_bps":r.get("protocol_fee_bps"),"coin_creator_bps":r.get("coin_creator_bps")})
    out.sort(key=lambda r:(r["received_at_unix_ns"],r["slot"],r["candidate_signature"],r["event_index"]))
    ns.output.parent.mkdir(parents=True,exist_ok=True)
    with ns.output.open("w",encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r,separators=(",",":"))+"\n")
    summary={"schema":"KEFI_CONTEXT_CONTROLS_V1","control_rows":len(out),"distinct_candidate_signatures":len(seen),"selected_signatures_excluded":len(selected),"independent_full_universe":False,"entry_sha256":sha256(ns.entries),"replay_sha256":[sha256(p) for p in ns.replay]}
    sp=ns.output.with_suffix(".summary.json"); sp.write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(summary,sort_keys=True))

if __name__=="__main__": main()
