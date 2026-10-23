#!/usr/bin/env python3
"""Read-only proposed precise-pin coverage proof for the live kEfi lane.

It never mutates the catalog.  Its output says whether the broad source-root
pin can be considered for atomic replacement; a human/second tool must still
perform that replacement after the proof is reviewed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def utcnow(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
def digest(p: Path):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""): h.update(b)
    return h.hexdigest()
def canon(v): return (json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=True)+"\n").encode()
def epoch(key): return datetime.strptime(key,"%Y/%m/%d/%H").replace(tzinfo=timezone.utc).timestamp()

def read_json(p): return json.loads(p.read_text())

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-root",required=True); ap.add_argument("--work-root",required=True)
    ap.add_argument("--output",required=True); args=ap.parse_args()
    root=Path(args.source_root); work=Path(args.work_root)
    partitions=[]
    for m in sorted((root/"source-spool/partitions").rglob("manifest.json")):
        try:
            row=read_json(m)
            if row.get("temporalHourClosed") is True:
                partitions.append((str(m.parent.relative_to(root/"source-spool/partitions")),m.parent,row))
        except (OSError,json.JSONDecodeError):
            pass
    normal_state=read_json(work/"out/kefi-market-controls-v1/state.json")
    catchup_state=read_json(work/"out/kefi-market-controls-catchup-v1/state.json")
    processed=set(normal_state.get("processed",[]))|set(catchup_state.get("processed",[]))
    pending_pairs=[]
    for (a,_,_),(b,_,_) in zip(partitions,partitions[1:]):
        key=f"{a}__{b}"
        if key not in processed: pending_pairs.append({"pair":key,"partitions":[a,b]})
    # A control worker's source scanner needs all manifests, but only the first
    # pending pair needs immediate raw-frame reads.  The rest remain retained
    # by policy until their own audit proves archival safety.
    immediate=set(p for pair in pending_pairs[:1] for p in pair["partitions"])
    live_partition_paths=[str(path) for key,path,_ in partitions if key in immediate]
    metadata_paths=[str(root/"source-spool/state"),str(root/"kefi-wallet-pump-candidates.jsonl"),
                    str(root/"finalizer.sqlite3"),str(root/"source-spool-results"),
                    str(root/"canonical-proofs"),str(root/"promotion-results"),
                    str(work/"out/kefi-market-controls-v1/state.json"),
                    str(work/"out/kefi-market-controls-catchup-v1/state.json"),
                    str(work/"out/kefi-prospective-shadow-v1/checkpoint.json"),
                    str(work/"out/kefi-prospective-shadow-v1/start-receipt.json"),
                    str(work/"out/independent-v1/fingerprint-discovery-v1.json")]
    broad_pin=str(root)
    proof={"schema":"evidence-lake-precise-pin-plan-v1","generated_utc":utcnow(),
      "broad_pin_to_retain_until_atomic_replacement":broad_pin,
      "closed_partitions_seen":len(partitions),"processed_control_pairs":len(processed),
      "next_pending_control_pairs":pending_pairs[:5],
      "proposed_precise_raw_pins":live_partition_paths,
      "proposed_precise_metadata_pins":metadata_paths,
      "coverage_assertion":"active_dependency_set_subset_precise_pin_set",
      "coverage_proven":False,
      "blockers":["collector rolls partition paths dynamically; no durable pin-synchronizer is deployed",
                  "normal control service enumerates all sealed manifests", 
                  "broad source-root pin remains the only currently proven complete protection"],
      "decision":"RETAIN_BROAD_PIN"}
    out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_bytes(canon(proof))
    print(json.dumps({"status":"RETAIN_BROAD_PIN","output":str(out),"sha256":digest(out),"next_pending_pair":pending_pairs[0] if pending_pairs else None},sort_keys=True))

if __name__=="__main__": main()
