#!/usr/bin/env python3
"""Exploratory scanner for frozen prefixes of the exact discovery universe."""
from __future__ import annotations
import argparse,collections,datetime as dt,gzip,hashlib,itertools,json,math,os,pathlib,statistics
from typing import Any

SCHEMA="PUMP_EXACT_ALPHA_FINGERPRINT_SCAN_V1"
CHECKPOINTS=(3000,5000,10000)
PRIMARY="size_0.50_entry_25_sellable_500"
LABEL_KEYS={PRIMARY,"size_0.01_entry_25_sellable_500","size_0.10_entry_25_sellable_500",
 "size_1.00_entry_25_sellable_500","size_0.50_entry_50_sellable_500",
 "size_0.50_entry_100_sellable_500","size_0.50_entry_25_sellable_1000"}

def atomic(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp");tmp.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n");os.replace(tmp,path)
def load_rows(root,mints,exact_only=True):
 out=[]
 for path in sorted((root/"rows-v2").glob("*.jsonl.gz")):
  with gzip.open(path,"rt",encoding="utf-8") as h:
   for line in h:
    row=json.loads(line)
    if row["mint"] not in mints:continue
    if not exact_only or row["labels"].get(PRIMARY,{}).get("status")=="EXACT":
     row["labels"]={key:value for key,value in row["labels"].items() if key in LABEL_KEYS}
     out.append(row)
 return out
def metric(rows,key=PRIMARY):
 if len(rows)!=len({r["mint"] for r in rows}):raise RuntimeError("PSEUDOREPLICATION_BLOCKER: metric input contains duplicate mints")
 labels=[r["labels"][key] for r in rows if r["labels"].get(key,{}).get("status")=="EXACT"]
 vals=[x["returnPct"]/100 for x in labels];pnls=[x["pnlSol"] for x in labels]
 wins=[x for x in vals if x>0];loss=[x for x in vals if x<=0];equity=peak=dd=0
 for x in pnls:equity+=x;peak=max(peak,equity);dd=max(dd,peak-equity)
 return {"N":len(vals),"WR":len(wins)/len(vals) if vals else None,"PF":sum(wins)/abs(sum(loss)) if loss and sum(loss) else None,
  "expectancy":statistics.mean(vals) if vals else None,"median":statistics.median(vals) if vals else None,
  "actualPnlSol":sum(pnls),"normalizedPnlSolAt1Sol":sum(vals),"maxDrawdownSol":dd,
  "TP":sum(r["labels"][key]["outcome"]=="TP" for r in rows if r["labels"].get(key,{}).get("status")=="EXACT"),
  "SL":sum(r["labels"][key]["outcome"]=="SL" for r in rows if r["labels"].get(key,{}).get("status")=="EXACT"),
  "MAX_HOLD":sum(r["labels"][key]["outcome"]=="MAX_HOLD" for r in rows if r["labels"].get(key,{}).get("status")=="EXACT")}
def robust(rows,coverage_hours=None):
 normal=metric(rows); ordered=sorted(rows,key=lambda r:r["labels"][PRIMARY]["returnPct"],reverse=True)
 hours=collections.Counter(r["decisionTimestampUtc"][:13] for r in rows)
 mc=collections.Counter(
  "unavailable" if not isinstance(r["features"].get("marketCapSol"),(int,float))
  else "lt10" if r["features"]["marketCapSol"]<10
  else "10_30" if r["features"]["marketCapSol"]<30 else "gte30"
  for r in rows)
 activity=collections.Counter("low" if r["features"]["globalTrades60sPrior"]<100 else "mid" if r["features"]["globalTrades60sPrior"]<300 else "high" for r in rows)
 times=sorted(r["decisionUnixMs"] for r in rows);own_span=max((times[-1]-times[0])/3_600_000,1/60) if times else 0
 span=coverage_hours or own_span
 return {"normal0p5":normal,"tradeFrequencyPerHour":len({r['mint'] for r in rows})/span if span else None,
  "frequencyDenominator":"full_checkpoint_utc_span" if coverage_hours else "selected_rule_utc_span",
  "capacity":{"0p01":metric(rows,"size_0.01_entry_25_sellable_500"),"0p10":metric(rows,"size_0.10_entry_25_sellable_500"),"0p50":normal,"1p00":metric(rows,"size_1.00_entry_25_sellable_500")},
  "stress":{"entry50":metric(rows,"size_0.50_entry_50_sellable_500"),"entry100":metric(rows,"size_0.50_entry_100_sellable_500"),"sellable1000":metric(rows,"size_0.50_entry_25_sellable_1000")},
  "winnerRemoval":{"top5":metric(ordered[5:]),"top1Pct":metric(ordered[max(1,math.ceil(len(ordered)*.01)):])},
  "concentration":{"maxHourShare":max(hours.values(),default=0)/len(rows) if rows else None,"hourCounts":dict(hours),"mcBands":dict(mc),"activityRegimes":dict(activity)}}
def quantiles(values):
 s=sorted(set(values));
 if len(s)<5:return s
 return sorted(set(s[min(len(s)-1,int((len(s)-1)*q))] for q in (.1,.2,.3,.4,.5,.6,.7,.8,.9)))
def first_per_mint(rows):
 out={}
 for r in sorted(rows,key=lambda x:(x["decisionUnixMs"],x["mint"])):out.setdefault(r["mint"],r)
 return list(out.values())
def replay_sanity(rows):
 exact=[]
 for row in rows:
  cell=row.get("labels",{}).get(PRIMARY,{})
  if cell.get("status")=="EXACT":exact.append(cell)
 returns={round(float(cell["returnPct"]),12) for cell in exact if isinstance(cell.get("returnPct"),(int,float))}
 return {"exactCells":len(exact),
  "nonEmptyReplayCanonicalEvidenceCells":sum(bool(cell.get("replayCanonicalEvidenceIds")) for cell in exact),
  "distinctExactReturns":len(returns),
  "outcomes":dict(collections.Counter(cell.get("outcome","MISSING") for cell in exact))}
def require_discriminative_replay(rows):
 sanity=replay_sanity(rows)
 if (sanity["exactCells"]==0 or sanity["nonEmptyReplayCanonicalEvidenceCells"]==0
     or sanity["distinctExactReturns"]<=1):
  raise RuntimeError("NON_DISCRIMINATIVE_POST_ENTRY_REPLAY_BLOCKER:"+json.dumps(sanity,sort_keys=True))
 return sanity
def monotonicity(rows,key,direction):
 ordered=sorted(rows,key=lambda r:r["features"][key]);buckets=[]
 for i in range(5):
  part=ordered[len(ordered)*i//5:len(ordered)*(i+1)//5];buckets.append(metric(part)["expectancy"] if part else None)
 values=[x for x in buckets if x is not None]
 if direction=="le":values=list(reversed(values))
 improvements=sum(b>=a for a,b in zip(values,values[1:]))
 return {"bucketExpectancyLowToHigh":buckets,"expectedDirection":direction,"improvingSteps":improvements,"totalSteps":max(len(values)-1,0),"score":improvements/max(len(values)-1,1)}
def classify(report):
 m=report["economics"]["normal0p5"];s=report["economics"]["stress"];cap=report["economics"]["capacity"];rem=report["economics"]["winnerRemoval"]
 if m["N"]>=150 and (m["PF"] or 0)>=1.8 and m["expectancy"]>0 and (s["entry100"]["PF"] or 0)>=1.2 and (cap["1p00"]["PF"] or 0)>=1.2 and (rem["top1Pct"]["PF"] or 0)>=1.2:return "VERY_PROMISING"
 if m["N"]>=100 and (m["PF"] or 0)>=1.3 and m["expectancy"]>0 and (cap["1p00"]["PF"] or 0)>=1:return "PROMISING"
 if m["N"]>=50 and (m["PF"] or 0)>1:return "WEAK"
 return "NO_SIGNAL"
def main():
 p=argparse.ArgumentParser();p.add_argument("root",type=pathlib.Path);a=p.parse_args();status=json.loads((a.root/"status.json").read_text());available=status["discoveryMintsAvailable"]
 index=[json.loads(x) for x in (a.root/"discovery-mint-index.jsonl").read_text().splitlines() if x.strip()]
 completed=[]
 for n in CHECKPOINTS:
  dest=a.root/"scans"/f"N{n}.json"
  if available<n:continue
  prefix=index[:n];mints={r["mint"] for r in prefix};all_rows=load_rows(a.root,mints,False);rows=[r for r in all_rows if r["labels"].get(PRIMARY,{}).get("status")=="EXACT"]
  replay_health=require_discriminative_replay(rows)
  create_ms=sorted(int(r["createReceivedAtUnixNs"])//1_000_000 for r in prefix)
  coverage_hours=max((create_ms[-1]-create_ms[0])/3_600_000,1/60) if create_ms else 0
  utc_hours=len({dt.datetime.fromtimestamp(x/1000,dt.timezone.utc).strftime("%Y-%m-%dT%H") for x in create_ms})
  primary_status=collections.Counter(r["labels"].get(PRIMARY,{}).get("status","MISSING") for r in all_rows)
  executable_mints={r["mint"] for r in rows}
  prefix_hash=hashlib.sha256("\n".join(r["mint"] for r in prefix).encode()).hexdigest()
  partition_keys={r.get("partitionKey") for r in prefix};manifest_inputs=[]
  for path in sorted((a.root/"manifests-v2").glob("*.json")):
   value=json.loads(path.read_text())
   if value.get("partitionKey") in partition_keys:
    manifest_inputs.append([value.get("partitionKey"),value.get("sha256"),value.get("sourceFingerprintSha256")])
  source_fingerprint=hashlib.sha256(json.dumps({"prefixSha256":prefix_hash,"manifests":manifest_inputs,
    "scannerSha256":hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()},sort_keys=True,separators=(",",":")).encode()).hexdigest()
  if dest.exists():
   frozen=json.loads(dest.read_text())
   if frozen.get("sourceFingerprintSha256")!=source_fingerprint:
    raise RuntimeError(f"IMMUTABLE_CHECKPOINT_INPUT_MISMATCH:{dest}")
   continue
  by_state=collections.defaultdict(list)
  for r in rows:by_state[r["observationState"]].append(r)
  min_n=max(50,int(n*.02));candidates=[]
  for state,state_rows in by_state.items():
   keys=sorted({k for row in state_rows for k,v in row["features"].items()
    if isinstance(v,(int,float)) and k not in {"mintAgeMs"}})
   for key in keys:
    vals=[r["features"][key] for r in state_rows if isinstance(r["features"].get(key),(int,float)) and math.isfinite(float(r["features"][key]))]
    for threshold in quantiles(vals):
     for op in ("ge","le"):
      chosen=first_per_mint([r for r in state_rows if isinstance(r["features"].get(key),(int,float)) and (r["features"][key]>=threshold if op=="ge" else r["features"][key]<=threshold)])
      if len(chosen)<min_n:continue
      m=metric(chosen)
      candidates.append({"kind":"ONE_FEATURE","state":state,"clauses":[[key,op,threshold]],"rows":chosen,"score":(m["PF"] or 0)*math.sqrt(len(chosen))})
  candidates.sort(key=lambda x:x["score"],reverse=True);base=candidates[:20]
  for left,right in itertools.combinations(base[:12],2):
   if left["state"]!=right["state"]:continue
   clauses=left["clauses"]+right["clauses"]
   chosen=first_per_mint([r for r in by_state[left["state"]] if all((r["features"][k]>=t if o=="ge" else r["features"][k]<=t) for k,o,t in clauses)])
   if len(chosen)>=min_n:candidates.append({"kind":"TWO_FEATURE","state":left["state"],"clauses":clauses,"rows":chosen,"score":(metric(chosen)["PF"] or 0)*math.sqrt(len(chosen))})
  for group in itertools.combinations(base[:8],3):
   if len({x["state"] for x in group})!=1:continue
   clauses=sum((x["clauses"] for x in group),[])
   if len({x[0] for x in clauses})<3:continue
   state=group[0]["state"];chosen=first_per_mint([r for r in by_state[state] if all((r["features"][k]>=t if o=="ge" else r["features"][k]<=t) for k,o,t in clauses)])
   if len(chosen)>=max(300,min_n):candidates.append({"kind":"THREE_FEATURE","state":state,"clauses":clauses,"rows":chosen,"score":(metric(chosen)["PF"] or 0)*math.sqrt(len(chosen))})
  known=[]
  definitions=[("FAST100","FIXED_100MS",[["buyCount100ms","ge",2]]),("FIRST_IMPULSE","FIRST_IMPULSE_EVENT",[["buyCount","ge",1],["sellCount","le",0]]),("FIRST_IMPULSE_FAST100","FIXED_100MS",[["buyCount100ms","ge",2],["sellPressure","le",.1]]),("OLD_RANKER_COMPONENT_PROXY","FIXED_1000MS",[["buyCount","ge",2],["netBuySol","ge",0],["firstBuyerShareBps","le",5000]])]
  for name,state,clauses in definitions:
   chosen=first_per_mint([r for r in by_state.get(state,[]) if all((r["features"][k]>=t if o=="ge" else r["features"][k]<=t) for k,o,t in clauses)])
   known.append({"name":name,"state":state,"clauses":clauses,"economics":robust(chosen,coverage_hours)})
  candidates.sort(key=lambda x:x["score"],reverse=True);results=[];seen=set()
  for c in candidates:
   ident=json.dumps([c["state"],c["clauses"]],sort_keys=True)
   if ident in seen:continue
   seen.add(ident);econ=robust(c["rows"],coverage_hours);r={"rank":len(results)+1,"kind":c["kind"],"state":c["state"],"clauses":c["clauses"],"economics":econ}
   r["monotonicity"]=monotonicity(by_state[c["state"]],c["clauses"][0][0],c["clauses"][0][1]) if c["kind"]=="ONE_FEATURE" else None
   r["penalties"]={"tinyN":econ["normal0p5"]["N"]<100,"topWinnerDependent":(econ["winnerRemoval"]["top1Pct"]["PF"] or 0)<1,"dustSizeOnly":(econ["capacity"]["1p00"]["PF"] or 0)<1,"nonMonotonic":bool(r["monotonicity"] and r["monotonicity"]["score"]<.5)}
   r["classification"]=classify(r);results.append(r)
   if len(results)>=10:break
  ranking={"status":"INSUFFICIENT_RULES"}
  if base:
   state=base[0]["state"];rules=[x for x in base if x["state"]==state][:5];scored=[]
   weighted_rules=[(x,math.log1p(max(metric(x["rows"])["PF"] or 0,0))) for x in rules]
   for row in by_state[state]:
    score=sum(weight for x,weight in weighted_rules if all((row["features"][k]>=t if o=="ge" else row["features"][k]<=t) for k,o,t in x["clauses"]))
    scored.append((score,row))
   scored.sort(key=lambda x:x[0],reverse=True);ranking={"status":"EXPLORATORY_STATIC_RULE_SCORE","state":state,"components":[x["clauses"] for x in rules],"tails":{str(p):robust(first_per_mint([r for _,r in scored[:max(1,int(len(scored)*p))]]),coverage_hours) for p in (.20,.10,.05)}}
  best=results[0] if results else None
  report={"schema":SCHEMA,"checkpointN":n,"generatedAtUtc":dt.datetime.now(dt.timezone.utc).isoformat(),
   "sourceFingerprintSha256":source_fingerprint,
   "checkpointPrefixSha256":prefix_hash,"discoveryOnly":True,"validationRowsInspected":0,"holdoutRowsInspected":0,
   "population":{"exactAdmittedPrefixMints":n,"primaryExecutableMints":len(executable_mints),"primaryNonExecutableMints":n-len(executable_mints),
    "opportunityRows":len(all_rows),"primaryLabelStatusCounts":dict(primary_status),"normalSolPumpOnly":True,"unsupportedMayhemMixed":False,
    "utcStart":dt.datetime.fromtimestamp(create_ms[0]/1000,dt.timezone.utc).isoformat() if create_ms else None,
    "utcEnd":dt.datetime.fromtimestamp(create_ms[-1]/1000,dt.timezone.utc).isoformat() if create_ms else None,
    "coverageSpanHours":coverage_hours,"healthyExactLedgerUtcHoursRepresented":utc_hours},
   "sampleUnit":"unique admitted mint; deterministic earliest qualifying trigger; maximum one execution per mint",
   "postEntryReplaySanity":replay_health,
   "opportunityRowsAreNotIndependent":True,"economicLabelCellsAreNotIndependent":True,
   "primarySemantics":["0.50_SOL_entry25ms_from_decision_firstSellable500ms_from_decision_TP20_SL20_maxHold120s_from_decision",
    "1.00_SOL_entry25ms_from_decision_firstSellable500ms_from_decision_TP20_SL20_maxHold120s_from_decision"],
   "earlyDescriptiveVerdict":{"alphaAt0p5Appears":bool(best and (best["economics"]["normal0p5"]["PF"] or 0)>1 and best["economics"]["normal0p5"]["expectancy"]>0),
    "alphaAt1p0Appears":bool(best and (best["economics"]["capacity"]["1p00"]["PF"] or 0)>1 and best["economics"]["capacity"]["1p00"]["expectancy"]>0),
    "classification":"EXPLORATORY_ONLY_NO_PROMOTION"},
   "top10":results,"historicalFamilies":known,"staticRankingModel":ranking,"productionPromotionAuthorized":False,"liveSubmissions":0}
  atomic(dest,report);completed.append(str(dest))
 print(json.dumps({"exactAdmittedN":status["exactAdmittedN"],"completed":completed,"nextCheckpoint":next((n for n in CHECKPOINTS if available<n),None),"alphaScanRan":bool(completed)},indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
