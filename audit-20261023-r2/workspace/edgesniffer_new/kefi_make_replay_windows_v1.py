#!/usr/bin/env python3
"""Create fail-closed paper-gate window identities from canonical replay output."""
from __future__ import annotations
import argparse, json
from collections import Counter
from pathlib import Path

def load_jsonl(p: Path):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--candidates",type=Path,required=True)
    ap.add_argument("--replay",type=Path,required=True)
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--summary",type=Path,required=True)
    ns=ap.parse_args()
    candidates=load_jsonl(ns.candidates)
    replay=load_jsonl(ns.replay)
    counts=Counter(r.get("target_signature") for r in replay)
    windows=[]; incomplete=0; missing_result=0
    for c in candidates:
        sig=c["signature"]
        result=ns.root/"source-spool-results"/(sig+".json")
        complete=False
        if result.exists():
            try:
                complete=json.loads(result.read_text(encoding="utf-8")).get("status")=="COMPLETE"
            except Exception:
                complete=False
        if not complete: missing_result += 1
        if complete and counts.get(sig,0)>0:
            windows.append({
                "window_id": sig,
                "target_signature": sig,
                "target_event_index": c.get("event_index",0),
                "window_complete": True,
                "fail_closed": False,
                "replay_event_count": counts[sig],
                "source_result_complete": True,
            })
        else:
            incomplete += 1
    ns.output.parent.mkdir(parents=True,exist_ok=True)
    ns.output.write_text("".join(json.dumps(x,sort_keys=True)+"\n" for x in windows),encoding="utf-8")
    summary={"schema":"KEFI_PROSPECTIVE_REPLAY_WINDOWS_V1","candidate_rows":len(candidates),"replay_rows":len(replay),"complete_windows":len(windows),"incomplete_windows":incomplete,"missing_or_incomplete_source_results":missing_result,"outcome_blind":True}
    ns.summary.write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(summary,sort_keys=True))
    return 0
if __name__=="__main__": raise SystemExit(main())
