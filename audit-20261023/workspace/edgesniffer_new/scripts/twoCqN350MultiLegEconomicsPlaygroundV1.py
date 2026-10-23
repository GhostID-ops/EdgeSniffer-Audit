#!/usr/bin/env python3
"""Entertainment-only multi-leg exact-curve replay over frozen N350 development."""

from __future__ import annotations

import argparse, collections, datetime as dt, gzip, hashlib, json, math, os, pathlib, statistics
from typing import Any

import twoCqN350FrozenPolicyEconomicsSandboxV1 as base
from buildPumpExactAlphaResearchV1 import NETWORK_FEE, apply_group
from executableOpportunityCensusV1 import group_events, latest_actual_base, quote_sell
from exactCurveEconomicsV1 import quote_exact_quote_in_buy
from validatePumpExactEvidenceLedgerV1 import event_fees

SCHEMA = "2CQ_N350_MULTI_LEG_ECONOMICS_PLAYGROUND_V1"
LAMPORTS = 1_000_000_000


def sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def roi(state, inventory: int, fee_event, realized: int, committed: int) -> float:
    if inventory:
        proceeds = quote_sell(state, inventory, fee_event).immediate_quote_out - NETWORK_FEE
    else:
        proceeds = 0
    return (realized + proceeds - committed) * 100 / committed


def sell_fraction(state, inventory: int, fee_event, numerator: int, denominator: int):
    amount = inventory if numerator >= denominator else max(1, inventory * numerator // denominator)
    quote = quote_sell(state, amount, fee_event)
    return quote.post_state, inventory - amount, quote.immediate_quote_out - NETWORK_FEE, amount


def sell_for_net(state, inventory: int, fee_event, target_net: int):
    lo, hi, answer = 1, inventory, None
    while lo <= hi:
        mid = (lo + hi) // 2
        q = quote_sell(state, mid, fee_event)
        net = q.immediate_quote_out - NETWORK_FEE
        if net >= target_net:
            answer = (q, mid, net); hi = mid - 1
        else: lo = mid + 1
    if answer is None: return sell_fraction(state, inventory, fee_event, 1, 1)
    q, amount, net = answer
    return q.post_state, inventory - amount, net, amount


def simulate(decision: dict[str, Any], groups: list[dict[str, Any]], size: float,
             latency: dict[str, int], scenario: str, overlay_sol: float) -> dict[str, Any]:
    decision_ms = int(decision["decisionMs"]); entry_ms = decision_ms + latency["entryOffsetMs"]
    state, fee_event, future, _ = latest_actual_base(decision, groups, entry_ms)
    total_gross = int(round(size * LAMPORTS))
    initial_gross = total_gross // 2 if scenario == "TWO_STAGE_50_50_CONFIRM5" else total_gross
    try: buy = quote_exact_quote_in_buy(state, initial_gross, event_fees(fee_event))
    except Exception as e: return {"status":"EXCLUDED","reason":str(e)}
    state, inventory = buy.post_state, buy.token_out
    original_inventory = inventory
    committed = initial_gross + NETWORK_FEE
    realized = 0; buy_legs = 1; sell_legs = 0; failed = attempted = 0
    sellable = max(entry_ms, decision_ms + latency["firstSellableOffsetMs"])
    horizon = entry_ms + 120_000
    armed = False; high = -math.inf; ladder_done: set[int] = set(); added = scenario != "TWO_STAGE_50_50_CONFIRM5"
    pending: list[tuple[int,str,Any]] = []
    outcome = "MAX_HOLD"; trigger_ms = horizon

    def schedule(now: int, action: str, payload=None):
        if not any(a == action for _, a, _ in pending): pending.append((now + latency["sellLandingOffsetMs"], action, payload))

    points = [g for g in groups[future:] if entry_ms < g["arrivalMs"] <= horizon + latency["sellLandingOffsetMs"]]
    index = 0; now = entry_ms
    while now <= horizon + latency["sellLandingOffsetMs"] and (inventory or pending):
        next_group = points[index]["arrivalMs"] if index < len(points) else 10**30
        next_action = min((x[0] for x in pending), default=10**30)
        next_checkpoint = sellable if now < sellable else (horizon if now < horizon else 10**30)
        next_time = min(next_group, next_action, next_checkpoint)
        if next_time == 10**30: break
        now = next_time
        if next_group == now:
            group = points[index]; index += 1; attempted += 1
            try: state = apply_group(state, group["rows"]); fee_event = group["feeEvent"]
            except Exception: failed += 1
        for item in sorted([x for x in pending if x[0] == now]):
            pending.remove(item); action, payload = item[1], item[2]
            try:
                if action == "ADD":
                    q = quote_exact_quote_in_buy(state, total_gross - initial_gross, event_fees(fee_event))
                    state, inventory = q.post_state, inventory + q.token_out; committed += total_gross - initial_gross + NETWORK_FEE; buy_legs += 1; added = True
                elif action == "SELL_PRINCIPAL":
                    state, inventory, net, _ = sell_for_net(state, inventory, fee_event, max(0, committed-realized)); realized += net; sell_legs += 1
                else:
                    num, den = payload
                    state, inventory, net, _ = sell_fraction(state, inventory, fee_event, num, den); realized += net; sell_legs += 1
            except Exception as e: return {"status":"EXCLUDED","reason":f"OWN_ACTION:{e}"}
        if now < sellable or now > horizon or not inventory: continue
        try: current = roi(state, inventory, fee_event, realized, committed)
        except Exception: continue
        high = max(high, current)
        if scenario == "TWO_STAGE_50_50_CONFIRM5" and not added and current >= 5: schedule(now,"ADD")
        if current <= -20:
            schedule(now,"SELL_ALL",(1,1)); outcome="SL"; trigger_ms=now
        elif scenario in ("BASELINE_TP20_SL20_MAX120","TWO_STAGE_50_50_CONFIRM5") and current >= 20:
            schedule(now,"SELL_ALL",(1,1)); outcome="TP"; trigger_ms=now
        elif scenario == "RUNNER_80_TP20_TRAIL15":
            if not armed and current >= 20: schedule(now,"SELL_80",(4,5)); armed=True; high=current
            elif armed and current <= high-15: schedule(now,"SELL_REMAINDER",(1,1)); outcome="TRAIL"; trigger_ms=now
        elif scenario == "FREE_ROLL_PRINCIPAL_TP20_TP50":
            if not armed and current >= 20: schedule(now,"SELL_PRINCIPAL"); armed=True
            elif armed and current >= 50: schedule(now,"SELL_REMAINDER",(1,1)); outcome="TP50"; trigger_ms=now
        elif scenario == "LADDER_25_TP10_20_40_80":
            for threshold in (10,20,40,80):
                if current >= threshold and threshold not in ladder_done:
                    schedule(now,f"LADDER_{threshold}",(1, max(1, 4-len(ladder_done)))); ladder_done.add(threshold); break
    if inventory:
        try:
            state, inventory, net, _ = sell_fraction(state, inventory, fee_event, 1, 1); realized += net; sell_legs += 1
        except Exception as e: return {"status":"EXCLUDED","reason":f"FINAL_EXIT:{e}"}
    pnl = (realized - committed) / LAMPORTS - overlay_sol
    return {"status":"EXACT","pnlSol":pnl,"returnPct":pnl*100/(committed/LAMPORTS),"outcome":outcome,
            "buyLegs":buy_legs,"sellLegs":sell_legs,"committedSol":committed/LAMPORTS,
            "counterfactualAttemptedTransactions":attempted,"counterfactualFailedTransactions":failed,
            "counterfactualDivergenceRate":failed/attempted if attempted else 0.0,"triggerUnixMs":trigger_ms}


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exact=[r for r in rows if r["status"]=="EXACT"]; pn=[r["pnlSol"] for r in exact]; rr=[r["returnPct"] for r in exact]
    wins=[x for x in pn if x>0]; losses=[x for x in pn if x<=0]
    return {"requestedN":len(rows),"exactN":len(exact),"wins":len(wins),"winRate":len(wins)/len(exact) if exact else None,
            "profitFactor":sum(wins)/abs(sum(losses)) if losses and sum(losses) else None,
            "expectancyPct":statistics.fmean(rr) if rr else None,"pnlSol":sum(pn),
            "medianRoiPct":statistics.median(rr) if rr else None,"worstRoiPct":min(rr) if rr else None,
            "meanBuyLegs":statistics.fmean(r["buyLegs"] for r in exact) if exact else None,
            "meanSellLegs":statistics.fmean(r["sellLegs"] for r in exact) if exact else None}


def run(args):
    playground=json.loads(args.playground_contract.read_text()); baseline, rows, policy=base.load_frozen_inputs(args)
    if playground["classification"]!="NON_AUTHORITATIVE_DEVELOPMENT_PLAYGROUND" or playground["validationRowsPermitted"]!=0: raise RuntimeError("PLAYGROUND_GUARD")
    args.output.mkdir(parents=True,exist_ok=True)
    identity={"schema":SCHEMA+"_IDENTITY","classification":playground["classification"],"developmentSha256":sha(args.development),
              "policySha256":sha(args.policy),"playgroundContractSha256":sha(args.playground_contract),"validationRowsRead":0,"outcomesReadBeforeFreeze":0}
    atomic_json(args.output/"run-identity.json",identity)
    chosen=[(row,base.choose_states(row,policy,baseline["frozenInputs"]["blindBaselineSeed"])["FROZEN_MODEL_TOP1"]) for row in rows]
    paths,source=base.load_source_manifest(args.state,rows,args.evidence_root)
    targets={s["mint"] for _,s in chosen}; events,counters=base.collect_events(paths,targets)
    out=[]
    for row,state_row in chosen:
        ev=events.get(state_row["mint"],[]); matching=[e for e in ev if e.signature==state_row["stateSignature"] and e.instruction_index==int(state_row["stateInstructionIndex"])]
        if len(matching)!=1:
            for sc in playground["scenarios"]:
                for ln in playground["latencyScenarios"]:
                    for sz in playground["sizesSol"]: out.append({"eligibleOrdinal":row["eligibleOrdinal"],"scenario":sc,"latency":ln,"sizeSol":sz,"status":"EXCLUDED","reason":"STATE_EVENT_NOT_UNIQUELY_RECONSTRUCTED"})
            continue
        groups=group_events(ev); gi=next((i for i,g in enumerate(groups) if g["signature"]==state_row["stateSignature"]),None)
        decision={"decisionMs":int(row["decisionBoundary"]["arrivalUnixNs"])//1_000_000,"baseState":matching[0].event.post_state,"feeEvent":matching[0].event,"futureIndex":gi+1}
        for sc in playground["scenarios"]:
            for ln,lat in playground["latencyScenarios"].items():
                for sz in playground["sizesSol"]:
                    if sc=="TEACHER_ACTION_REPLAY": result={"status":"EXCLUDED","reason":"TEACHER_MULTI_LEG_INVENTORY_BASELINE_NOT_PRESENT_IN_FROZEN_CHOICE_SET"}
                    else: result=simulate(decision,groups,float(sz),lat,sc,float(playground["roundTripDeliveryOverlaySol"]))
                    result.update({"eligibleOrdinal":row["eligibleOrdinal"],"mint":state_row["mint"],"scenario":sc,"latency":ln,"sizeSol":float(sz)})
                    out.append(result)
    with gzip.open(args.output/"rows.jsonl.gz","wt") as f:
        for r in out:f.write(json.dumps(r,separators=(",",":"),sort_keys=True)+"\n")
    metrics={}
    for sc in playground["scenarios"]:
        for ln in playground["latencyScenarios"]:
            for sz in playground["sizesSol"]:
                key=f"{sc}|{ln}|{sz:.1f}"; metrics[key]=summary([r for r in out if r["scenario"]==sc and r["latency"]==ln and r["sizeSol"]==sz])
    report={"schema":SCHEMA+"_REPORT","classification":playground["classification"],"identity":identity,"population":{"developmentN":350},
            "metrics":metrics,"exclusions":dict(collections.Counter(r.get("reason") for r in out if r["status"]!="EXACT")),
            "integrity":{"validationRowsRead":0,"parameterSearches":0,"scenarioSelections":0,"modelRefits":0,"submissions":0,"authoritativeInfluencePermitted":False},
            "sourceEvidence":source,"collectionCounters":counters,"interpretation":playground["interpretation"]}
    atomic_json(args.output/"report.json",report)
    lines=["# 2CQ N350 Multi-Leg Economics Playground V1","","`NON_AUTHORITATIVE_DEVELOPMENT_PLAYGROUND`","",playground["interpretation"],"","| Scenario | Latency | Size | Exact N | WR | PF | Exp | PnL |","|---|---|---:|---:|---:|---:|---:|---:|"]
    for k,m in metrics.items():
        sc,ln,sz=k.split("|"); fmt=lambda x: "n/a" if x is None else f"{x:.3f}"
        lines.append(f"| {sc} | {ln} | {sz} | {m['exactN']} | {fmt(m['winRate'])} | {fmt(m['profitFactor'])} | {fmt(m['expectancyPct'])}% | {fmt(m['pnlSol'])} |")
    (args.output/"REPORT.md").write_text("\n".join(lines)+"\n")
    atomic_json(args.output/"manifest.json",{"schema":SCHEMA+"_MANIFEST","reportSha256":sha(args.output/"report.json"),"rowsSha256":sha(args.output/"rows.jsonl.gz"),"validationRowsRead":0})
    return report


def main():
    p=argparse.ArgumentParser(); p.add_argument("--playground-contract",type=pathlib.Path,required=True)
    p.add_argument("--contract",type=pathlib.Path,required=True); p.add_argument("--development",type=pathlib.Path,required=True)
    p.add_argument("--policy",type=pathlib.Path,required=True); p.add_argument("--state",type=pathlib.Path,required=True)
    p.add_argument("--evidence-root",type=pathlib.Path,required=True); p.add_argument("--output",type=pathlib.Path,required=True)
    r=run(p.parse_args()); print(json.dumps({"classification":r["classification"],"metrics":r["metrics"],"integrity":r["integrity"]},indent=2))
if __name__=="__main__":main()
