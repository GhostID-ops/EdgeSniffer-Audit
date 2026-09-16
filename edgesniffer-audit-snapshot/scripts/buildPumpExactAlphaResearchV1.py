#!/usr/bin/env python3
"""Build the immutable, discovery-only exact-alpha research table."""

from __future__ import annotations

import argparse, base64, bisect, collections, datetime as dt, gzip, hashlib, io, json, os, pathlib, sqlite3, statistics
from dataclasses import dataclass
from typing import Any

from exactCurveEconomicsV1 import (
    BorshReader, TRADE_EVENT_DISCRIMINATOR, BuyQuote, CurveState, ExactCurveError, SellQuote, apply_instruction,
    decode_trade_events, extract_pump_source_observations,
    quote_exact_quote_in_buy, quote_exact_token_sell,
)
from reconcilePumpExactEvidenceLedgerV1 import iter_frames, partition_data_path
from validatePumpExactEvidenceLedgerV1 import event_fees

SCHEMA = "PUMP_EXACT_ALPHA_RESEARCH_TABLE_V1"
IMPLEMENTATION_VERSION = 2
READINESS_IMPLEMENTATION_VERSION = 4
PRIOR_WALLET_INDEX_VERSION = 2
CHECKPOINTS_MS = (25, 50, 100, 250, 500, 1000, 2000, 5000, 10000)
ENTRY_DELAYS_MS = (25, 50, 100)
SELLABLE_MS = (500, 1000)
SIZES_SOL = (0.01, 0.10, 0.50, 1.00)
NETWORK_FEE = 5_000
TRADE_KINDS = {"BUY", "BUY_EXACT_SOL_IN", "BUY_V2", "BUY_EXACT_QUOTE_IN_V2", "SELL", "SELL_V2"}

@dataclass
class EventRow:
    signature: str; observation_evidence_id: str; canonical_evidence_id: str
    arrival_ms: int; slot: int; tx_index: int
    instruction_index: int; source: Any; event: Any


def retain_target_arrival(
    raw_arrival: dict[str, int],
    raw_evidence: dict[str, str],
    signature: str,
    observed_ms: int,
    evidence_id: str,
    related_mints: set[str],
) -> None:
    """Retain the first causal source observation only for target-mint evidence."""
    if observed_ms <= 0 or not evidence_id or not related_mints:
        return
    raw_arrival.setdefault(signature, observed_ms)
    raw_evidence.setdefault(signature, evidence_id)


def retain_pump_trade_arrival(
    raw_arrival: dict[str, int],
    raw_evidence: dict[str, str],
    signature: str,
    observed_ms: int,
    evidence_id: str,
    pump_instruction_kinds: set[str],
) -> None:
    """Retain Pump trade timing even when raw mint discovery is incomplete."""
    if (
        observed_ms <= 0
        or not evidence_id
        or not signature
        or not (pump_instruction_kinds & TRADE_KINDS)
    ):
        return
    raw_arrival.setdefault(signature, observed_ms)
    raw_evidence.setdefault(signature, evidence_id)


class PriorWalletIndex:
    """Disk-backed, availability-timestamped prior-wallet evidence."""

    def __init__(self, path: pathlib.Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database = sqlite3.connect(path)
        self.database.execute("PRAGMA journal_mode=WAL")
        self.database.execute("PRAGMA synchronous=FULL")
        self.database.execute(
            "CREATE TABLE IF NOT EXISTS observations("
            "wallet TEXT NOT NULL,mint TEXT NOT NULL,observed_ms INTEGER NOT NULL,"
            "evidence_id TEXT NOT NULL,source_partition TEXT NOT NULL,"
            "PRIMARY KEY(wallet,mint,source_partition))"
        )
        self.database.execute(
            "CREATE INDEX IF NOT EXISTS observations_lookup "
            "ON observations(wallet,observed_ms,mint)"
        )
        self.database.execute(
            "CREATE TABLE IF NOT EXISTS processed_partitions("
            "partition_key TEXT PRIMARY KEY,source_sha256 TEXT NOT NULL,"
            "malformed_count INTEGER NOT NULL DEFAULT 0)"
        )
        columns = {
            row[1] for row in self.database.execute("PRAGMA table_info(processed_partitions)")
        }
        if "malformed_count" not in columns:
            self.database.execute(
                "ALTER TABLE processed_partitions ADD COLUMN malformed_count INTEGER NOT NULL DEFAULT 0"
            )
        self.database.commit()

    def partition_is_current(self, partition_key: str, source_sha256: str) -> bool:
        row = self.database.execute(
            "SELECT source_sha256 FROM processed_partitions WHERE partition_key=?",
            (partition_key,),
        ).fetchone()
        return row is not None and row[0] == source_sha256

    def replace_partition(
        self,
        partition_key: str,
        source_sha256: str,
        observations: dict[tuple[str, str], tuple[int, str]],
        malformed_count: int,
    ) -> None:
        with self.database:
            self.database.execute(
                "DELETE FROM observations WHERE source_partition=?", (partition_key,)
            )
            self.database.executemany(
                "INSERT INTO observations VALUES (?,?,?,?,?)",
                (
                    (wallet, mint, value[0], value[1], partition_key)
                    for (wallet, mint), value in sorted(observations.items())
                ),
            )
            self.database.execute(
                "INSERT OR REPLACE INTO processed_partitions VALUES (?,?,?)",
                (partition_key, source_sha256, malformed_count),
            )

    def malformed_count(self) -> int:
        return int(
            self.database.execute(
                "SELECT COALESCE(SUM(malformed_count),0) FROM processed_partitions"
            ).fetchone()[0]
        )

    def prior_for(
        self, wallets: set[str], current_mint: str, decision_ms: int
    ) -> dict[str, tuple[int, str]]:
        result: dict[str, tuple[int, str]] = {}
        for wallet in wallets:
            row = self.database.execute(
                "SELECT observed_ms,evidence_id FROM observations "
                "WHERE wallet=? AND mint<>? AND observed_ms<? "
                "ORDER BY observed_ms,evidence_id LIMIT 1",
                (wallet, current_mint, decision_ms),
            ).fetchone()
            if row is not None:
                result[wallet] = (int(row[0]), str(row[1]))
        return result

    def close(self) -> None:
        self.database.close()

def atomic_json(path: pathlib.Path, value: Any, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists(): return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)

def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def snapshot_supply(raw: bytes) -> int | None:
    import base64
    try:
        value = json.loads(raw)["result"]["value"]
        data = base64.b64decode(value["data"][0])
        return int.from_bytes(data[36:44], "little") if len(data) >= 44 else None
    except Exception: return None

def resolve_reconciliation_input(root: pathlib.Path, relative: str) -> pathlib.Path:
    path = root / relative
    if path.exists():
        return path
    if path.name == "evidence.frames.partial":
        compressed = path.with_name("evidence.frames.zst")
        if compressed.exists():
            return compressed
    raise RuntimeError(f"missing reconciled evidence input {relative}")


def paths_for_report(root: pathlib.Path, report: dict[str, Any]) -> list[pathlib.Path]:
    hour=dt.datetime.fromisoformat(report["partitionHourUtc"].replace("Z","+00:00"))
    reconciliation=read_json(
        root/"partitions"/hour.strftime("%Y/%m/%d/%H")/"reconciliation-manifest.json"
    )
    if reconciliation.get("manifestSha256") != report.get("sourceReconciliationManifestSha256"):
        raise RuntimeError(f"readiness/reconciliation fingerprint mismatch {report['partitionKey']}")
    paths=[]
    prior=partition_data_path(root/"partitions"/(hour-dt.timedelta(hours=1)).strftime("%Y/%m/%d/%H"))
    if prior is not None:
        paths.append(prior)
    paths.extend(
        resolve_reconciliation_input(root,item["path"])
        for item in reconciliation.get("inputEvidence",[])
    )
    return list(dict.fromkeys(paths))


def update_prior_wallet_index(
    root: pathlib.Path, reports: Any, index: PriorWalletIndex
) -> dict[str, int]:
    refreshed = 0
    reused = 0
    for report in reports:
        partition_key = str(report["partitionKey"])
        reconciliation_sha = str(report.get("sourceReconciliationManifestSha256") or "")
        if not reconciliation_sha:
            raise RuntimeError(f"missing source fingerprint for {partition_key}")
        source_sha = hashlib.sha256(
            json.dumps(
                {
                    "indexVersion": PRIOR_WALLET_INDEX_VERSION,
                    "reconciliationManifestSha256": reconciliation_sha,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if index.partition_is_current(partition_key, source_sha):
            reused += 1
            continue
        hour = dt.datetime.fromisoformat(
            report["partitionHourUtc"].replace("Z", "+00:00")
        )
        target_path = partition_data_path(
            root / "partitions" / hour.strftime("%Y/%m/%d/%H")
        )
        if target_path is None:
            raise RuntimeError(f"missing partition data for {partition_key}")
        raw_arrival: dict[str, tuple[int, str]] = {}
        for metadata, _ in iter_frames(target_path, target_path.suffix == ".partial"):
            if metadata.get("kind") != "transaction" or not metadata.get("signature"):
                continue
            if not (set(metadata.get("pumpInstructionKinds") or []) & TRADE_KINDS):
                continue
            observed_ms = int(metadata.get("receivedAtUnixNs") or 0) // 1_000_000
            evidence_id = str(metadata.get("evidenceId") or "")
            if observed_ms <= 0 or not evidence_id:
                continue
            raw_arrival.setdefault(
                metadata["signature"],
                (
                    observed_ms,
                    evidence_id,
                ),
            )
        observations: dict[tuple[str, str], tuple[int, str]] = {}
        partition_malformed = 0
        for path in paths_for_report(root,report):
          for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            if metadata.get("kind") != "rpc_finalized_transaction":
                continue
            signature = metadata.get("signature")
            arrival = raw_arrival.get(signature)
            if arrival is None:
                continue
            try:
                transaction = json.loads(raw).get("result")
            except Exception:
                continue
            if not transaction or (transaction.get("meta") or {}).get("err") is not None:
                continue
            for line in (transaction.get("meta") or {}).get("logMessages") or []:
                marker = "Program data: "
                if marker not in line:
                    continue
                try:
                    payload = base64.b64decode(
                        line.split(marker, 1)[1].strip(), validate=True
                    )
                except Exception:
                    continue
                if not payload.startswith(TRADE_EVENT_DISCRIMINATOR):
                    continue
                try:
                    reader = BorshReader(payload)
                    reader.take(8)
                    mint = reader.pubkey()
                    reader.u64()
                    reader.u64()
                    reader.boolean()
                    wallet = reader.pubkey()
                except Exception:
                    partition_malformed += 1
                    continue
                key = (wallet, mint)
                current = (arrival[0], arrival[1])
                if key not in observations or current < observations[key]:
                    observations[key] = current
        index.replace_partition(
            partition_key, source_sha, observations, partition_malformed
        )
        refreshed += 1
    return {
        "refreshedPartitions": refreshed,
        "reusedPartitions": reused,
        "malformedEventIdentities": index.malformed_count(),
    }

def pair_events(tx: dict[str, Any], signature: str, observation_evidence_id: str,
                canonical_evidence_id: str, arrival_ms: int,
                slot: int, tx_index: int, targets: set[str]) -> list[EventRow]:
    events=decode_trade_events((tx.get("meta") or {}).get("logMessages") or [])
    rows=[]
    for mint in {e.mint for e in events}.intersection(targets):
        sources=extract_pump_source_observations(tx, signature, mint, arrival_ms, "immutable_raw_receive")
        sources.sort(key=lambda x:x.instruction_index)
        for event in [e for e in events if e.mint==mint]:
            idx=next((i for i,s in enumerate(sources) if (s.side=="buy")==event.is_buy),None)
            if idx is None: raise ExactCurveError(f"source/event pair missing {signature} {mint}")
            source=sources.pop(idx)
            rows.append(EventRow(signature,observation_evidence_id,canonical_evidence_id,
                arrival_ms,slot,tx_index,source.instruction_index,source,event))
    return rows

def state_before(events: list[EventRow], at_ms: int) -> tuple[CurveState, Any, str] | None:
    prior=[r for r in events if r.arrival_ms<=at_ms]
    if not prior: return None
    return prior[-1].event.post_state, prior[-1].event, prior[-1].canonical_evidence_id

def apply_group(state: CurveState, group: list[EventRow]) -> CurveState:
    trial=state
    for row in group:
        q=apply_instruction(trial,row.source.instruction,event_fees(row.event),
            cashback_bps=row.event.cashback_fee_basis_points,
            buyback_share_bps=row.event.buyback_fee_basis_points)
        trial=q.post_state
    return trial

def roi_for_sell(state: CurveState, inventory: int, fee_event: Any, cost: int) -> tuple[float,SellQuote]:
    q=quote_exact_token_sell(state,inventory,1,event_fees(fee_event),
        cashback_bps=fee_event.cashback_fee_basis_points,
        buyback_share_bps=fee_event.buyback_fee_basis_points)
    proceeds=q.immediate_quote_out-NETWORK_FEE
    return (proceeds-cost)*100/cost,q

def label(events: list[EventRow], decision_ms: int, size_sol: float, entry_delay: int,
          sellable_delay: int) -> dict[str,Any]:
    entry_ms=decision_ms+entry_delay
    base=state_before(events,entry_ms)
    if base is None: return {"status":"ENTRY_UNRESOLVED"}
    state,fee_event,_=base
    gross=int(round(size_sol*1_000_000_000))
    try: buy=quote_exact_quote_in_buy(state,gross,event_fees(fee_event))
    except Exception as e: return {"status":"ENTRY_UNSUPPORTED","reason":str(e)}
    inventory=buy.token_out; cost=gross+NETWORK_FEE; state=buy.post_state
    # Both latency assumptions are measured from the causal decision timestamp.
    # This keeps a 500 ms sellability boundary fixed under entry-delay stress.
    first_sellable=decision_ms+sellable_delay; max_hold=decision_ms+120_000
    grouped=collections.defaultdict(list)
    for row in events:
        if entry_ms < row.arrival_ms <= max_hold: grouped[(row.arrival_ms,row.slot,row.tx_index,row.signature)].append(row)
    counterfactual_failed=0; best=None; worst=None; outcome=None; exit_ms=None; exit_quote=None; trigger=None
    replay_observation_evidence=[]; replay_canonical_evidence=[]; exit_state_evidence_id=base[2]
    try:
        if first_sellable<=max_hold:
            current,exit_quote=roi_for_sell(state,inventory,fee_event,cost); best=worst=current
            if current>=20 or current<=-20: outcome="TP" if current>=20 else "SL"; exit_ms=first_sellable; trigger=current
    except Exception: pass
    for key,group in sorted(grouped.items()):
        if outcome: break
        replay_observation_evidence.extend(
            r.observation_evidence_id for r in group if r.observation_evidence_id
        )
        replay_canonical_evidence.extend(
            r.canonical_evidence_id for r in group if r.canonical_evidence_id
        )
        try:
            state=apply_group(state,sorted(group,key=lambda r:r.instruction_index)); fee_event=group[-1].event
            exit_state_evidence_id=group[-1].canonical_evidence_id
        except Exception: counterfactual_failed+=1
        if key[0] < first_sellable: continue
        try: current,q=roi_for_sell(state,inventory,fee_event,cost)
        except Exception: continue
        best=current if best is None else max(best,current); worst=current if worst is None else min(worst,current)
        if current>=20 or current<=-20:
            outcome="TP" if current>=20 else "SL"; exit_ms=key[0]; trigger=current; exit_quote=q
    if not outcome:
        try: trigger,exit_quote=roi_for_sell(state,inventory,fee_event,cost); outcome="MAX_HOLD"; exit_ms=max_hold
        except Exception as e: return {"status":"EXIT_UNRESOLVED","reason":str(e)}
    proceeds=exit_quote.immediate_quote_out-NETWORK_FEE
    return {"status":"EXACT","sizeSol":size_sol,"entryDelayMs":entry_delay,
        "firstSellableMs":sellable_delay,"firstSellableFrom":"decision","entryMs":entry_ms,
        "firstSellableAtMs":first_sellable,"maxHoldFrom":"decision","exitMs":exit_ms,"outcome":outcome,
        "returnPct":(proceeds-cost)*100/cost,"pnlSol":(proceeds-cost)/1e9,
        "tokensReceivedRaw":inventory,"grossBuyLamports":gross,
        "sellProceedsLamports":exit_quote.immediate_quote_out,"bestExecutableReturnPct":best,
        "worstExecutableReturnPct":worst,"triggerReturnPct":trigger,
        "counterfactualFailedTransactions":counterfactual_failed,
        "entryStateEvidenceId":base[2],"exitStateEvidenceId":exit_state_evidence_id,
        "replayObservationEvidenceIds":sorted(set(replay_observation_evidence)),
        "replayCanonicalEvidenceIds":sorted(set(replay_canonical_evidence)),
        "entryState":{"virtualSol":base[0].virtual_sol,"virtualToken":base[0].virtual_token,
                      "realSol":base[0].real_sol,"realToken":base[0].real_token},
        "ownBuyPostState":{"virtualSol":buy.post_state.virtual_sol,"virtualToken":buy.post_state.virtual_token,
                           "realSol":buy.post_state.real_sol,"realToken":buy.post_state.real_token}}

def feature_row(mint: str, create: dict[str,Any], events: list[EventRow], supply_observation: tuple[int,int,str],
                age: int, global_trade_ms:list[int], global_create_ms:list[int],
                global_observations:list[tuple[int,str]],
                prior_wallet_index:PriorWalletIndex,
                failed_observations:list[tuple[int,str]],
                observation_state: str | None = None) -> dict[str,Any] | None:
    create_ms=int(create["createReceivedAtUnixNs"])//1_000_000; decision=create_ms+age
    seen=[r for r in events if r.arrival_ms<=decision]
    if not seen: return None
    buys=[r for r in seen if r.event.is_buy]; sells=[r for r in seen if not r.event.is_buy]
    state=seen[-1].event.post_state
    supply,supply_observed_ms,supply_evidence_id=supply_observation
    supply_available=0<supply_observed_ms<=decision
    gross_buy=sum(r.event.immediate_quote_amount for r in buys); gross_sell=sum(r.event.immediate_quote_amount for r in sells)
    by_wallet=collections.Counter()
    for r in buys: by_wallet[r.event.user]+=r.event.immediate_quote_amount
    total=max(gross_buy,1); first=buys[0].event.immediate_quote_amount if buys else 0
    def count_recent(rows,window): return sum(r.arrival_ms>decision-window for r in rows)
    def global_count(values,window): return bisect.bisect_left(values,decision)-bisect.bisect_left(values,decision-window)
    failed_ms=sorted(item[0] for item in failed_observations)
    prior_wallets=prior_wallet_index.prior_for(set(by_wallet),mint,decision)
    recurrent_wallets=set(prior_wallets)
    global_evidence=[
      (observed_ms,evidence_id) for observed_ms,evidence_id in global_observations
      if decision-60_000 <= observed_ms < decision and evidence_id
    ]
    global_evidence.sort()
    global_evidence_digest=hashlib.sha256(
      "\n".join(f"{observed_ms}:{evidence_id}" for observed_ms,evidence_id in global_evidence).encode()
    ).hexdigest()
    recurrent_evidence_ids=sorted({
      prior_wallets[wallet][1] for wallet in recurrent_wallets if prior_wallets[wallet][1]
    })
    local_evidence_ids=sorted({
      r.observation_evidence_id for r in seen if r.observation_evidence_id
    })
    failed_evidence_ids=sorted({
      evidence_id for observed_ms,evidence_id in failed_observations
      if observed_ms <= decision and evidence_id
    })
    base_vsol=events[0].event.pre_state.virtual_sol
    features={
      "mintAgeMs":age,"buyCount":len(buys),"sellCount":len(sells),
      "buyCount25ms":count_recent(buys,25),"buyCount50ms":count_recent(buys,50),
      "buyCount100ms":count_recent(buys,100),"buyCount250ms":count_recent(buys,250),
      "buyCount500ms":count_recent(buys,500),"buyCount1000ms":count_recent(buys,1000),
      "sellCount100ms":count_recent(sells,100),"sellCount500ms":count_recent(sells,500),
      "sellCount1000ms":count_recent(sells,1000),"uniqueBuyers":len(by_wallet),
      "uniqueSellers":len({r.event.user for r in sells}),"grossBuySol":gross_buy/1e9,
      "grossSellSol":gross_sell/1e9,"netBuySol":(gross_buy-gross_sell)/1e9,
      "buyVelocity100":count_recent(buys,100)*10,"buyVelocity500":count_recent(buys,500)*2,
      "buyAcceleration100":count_recent(buys,100)-sum(decision-200<r.arrival_ms<=decision-100 for r in buys),
      "sellPressure":gross_sell/max(gross_buy,1),"firstBuyerShareBps":first*10000/total,
      "topBuyerShareBps":max(by_wallet.values(),default=0)*10000/total,
      "buyerHhi":sum((v/total)**2 for v in by_wallet.values()),
      "recurrentWalletShareBps":sum(v for w,v in by_wallet.items() if w in recurrent_wallets)*10000/total,
      "transactionDensity":len({r.signature for r in seen}),
      "sameSlotBuyCount":sum(r.slot==int(create.get("createSlot") or -1) for r in buys),
      "failedTransactionCount":bisect.bisect_right(failed_ms,decision),
      "earlyReserveMovementPct":(state.virtual_sol-base_vsol)*100/base_vsol,
      "marketCapSol":state.virtual_sol*supply/state.virtual_token/1e9 if supply_available else None,
      "marketCapAvailable":int(supply_available),
      "dominance":max(by_wallet.values(),default=0)/total,
      "globalTrades1sPrior":global_count(global_trade_ms,1000),
      "globalTrades5sPrior":global_count(global_trade_ms,5000),
      "globalTrades60sPrior":global_count(global_trade_ms,60000),
      "globalLaunches60sPrior":global_count(global_create_ms,60000),
    }
    state_name="FIRST_IMPULSE" if buys and not sells else ("FLOW_WITH_SELLS" if buys else "PRE_IMPULSE")
    labels={}
    for size in SIZES_SOL:
      for entry in ENTRY_DELAYS_MS:
        for sellable in SELLABLE_MS:
          key=f"size_{size:.2f}_entry_{entry}_sellable_{sellable}"
          labels[key]=label(events,decision,size,entry,sellable)
    return {"schema":SCHEMA,"mint":mint,"observationState":observation_state or f"FIXED_{age}MS","eventState":state_name,
      "decisionTimestampUtc":dt.datetime.fromtimestamp(decision/1000,dt.timezone.utc).isoformat(),
      "decisionUnixMs":decision,"createSignature":create.get("createSignature"),
      "createSlot":create.get("createSlot"),"features":features,"labels":labels,
      "evidenceIds":local_evidence_ids,
      "causalInputs":{
        "buyWallets":sorted(by_wallet),
        "createObservation":{"observedUnixMs":create_ms,
          "evidenceId":create.get("createObservationEvidenceId")},
        "supplyObservation":({"observedUnixMs":supply_observed_ms,
          "evidenceId":supply_evidence_id} if supply_available else None),
        "localObservationEvidenceIds":local_evidence_ids,
        "localObservations":[
          {"observedUnixMs":ms,"evidenceId":evidence_id}
          for ms,evidence_id in sorted({(r.arrival_ms,r.observation_evidence_id) for r in seen if r.observation_evidence_id})
        ],
        "globalPriorObservationCount":len(global_evidence),
        "globalPriorObservationDigestSha256":global_evidence_digest,
        "globalPriorObservationFirstUnixMs":global_evidence[0][0] if global_evidence else None,
        "globalPriorObservationLastUnixMs":global_evidence[-1][0] if global_evidence else None,
        "failedObservationEvidenceIds":failed_evidence_ids,
        "failedObservations":[
          {"observedUnixMs":ms,"evidenceId":evidence_id}
          for ms,evidence_id in sorted(set(failed_observations)) if ms<=decision and evidence_id
        ],
        "recurrentWalletPriorEvidenceIds":recurrent_evidence_ids,
        "recurrentWalletPriorObservations":[
          {"wallet":wallet,"observedUnixMs":prior_wallets[wallet][0],"evidenceId":prior_wallets[wallet][1]}
          for wallet in sorted(recurrent_wallets)
        ],
        "featureCutoffUnixMs":decision,
        "maxFeatureObservationUnixMs":max(
          [create_ms]+[r.arrival_ms for r in seen]
          +[ms for ms,_ in global_observations if decision-60_000<=ms<decision]
          +[ms for ms,_ in failed_observations if ms<=decision]
          +[prior_wallets[w][0] for w in recurrent_wallets]
          +([supply_observed_ms] if supply_available else []),
          default=None,
        ),
      },
      "availabilityCausalityPass":0<create_ms<=decision
        and bool(create.get("createObservationEvidenceId"))
        and all(0<r.arrival_ms<=decision and bool(r.observation_evidence_id) for r in seen)
        and all(ms<decision for ms,_ in global_observations if decision-60_000<=ms<decision)
        and all(ms<=decision for ms,_ in failed_observations if ms<=decision)
        and all(prior_wallets[w][0]<decision and bool(prior_wallets[w][1]) for w in recurrent_wallets)
        and (not supply_available or bool(supply_evidence_id)),
      "featureSemantics":"causal_realtime_observation_availability_only",
      "economicSemantics":"nonlinear_counterfactual_no_mc_proxy"}

def process_partition(root:pathlib.Path, report:dict[str,Any], selected:set[str], output:pathlib.Path,
                      prior_wallet_index:PriorWalletIndex, source_fingerprint:str)->dict[str,Any]:
    targets={r["mint"] for r in report["mints"] if r["mint"] in selected and r["exactAdmitted"]}
    paths=paths_for_report(root,report)
    raw_arrival={}; raw_evidence={}; global_trades=[]; global_creates=[]; global_observations=[]
    supplies={}; failed=collections.defaultdict(list)
    by_mint=collections.defaultdict(list); first_buy_ms={}
    creates={r["mint"]:r for r in report["mints"] if r["mint"] in targets}
    for path in paths:
      for md,raw in iter_frames(path,path.suffix==".partial"):
        kind=md.get("kind"); sig=md.get("signature")
        if kind=="transaction" and sig:
          ms=int(md.get("receivedAtUnixNs") or 0)//1_000_000
          if ms<=0: continue
          kinds=set(md.get("pumpInstructionKinds") or [])
          evidence_id=md.get("evidenceId") or ""
          if evidence_id and kinds&TRADE_KINDS:
            global_trades.append(ms); global_observations.append((ms,evidence_id))
            retain_pump_trade_arrival(raw_arrival,raw_evidence,sig,ms,evidence_id,kinds)
          if evidence_id and kinds&{"CREATE","CREATE_V2"}: global_creates.append(ms); global_observations.append((ms,evidence_id))
          related=targets.intersection(md.get("discoveredMints") or [])
          retain_target_arrival(raw_arrival,raw_evidence,sig,ms,evidence_id,related)
          if evidence_id and md.get("successful") is False:
            for mint in related: failed[mint].append((ms,evidence_id))
        elif kind=="rpc_account_snapshot" and md.get("accountPubkey") in targets:
          s=snapshot_supply(raw)
          if s:
            candidate=(s,int(md.get("receivedAtUnixNs") or 0)//1_000_000,md.get("evidenceId") or "")
            if candidate[1]<=0 or not candidate[2]: continue
            prior=supplies.get(md["accountPubkey"])
            if prior is None or candidate[1:]<prior[1:]: supplies[md["accountPubkey"]]=candidate
        elif kind=="rpc_finalized_transaction" and targets.intersection(md.get("discoveredMints") or []):
          try: tx=json.loads(raw).get("result")
          except Exception: continue
          if tx and (tx.get("meta") or {}).get("err") is None:
            arrival=raw_arrival.get(sig)
            if arrival is None: continue
            try: decoded=pair_events(tx,sig,raw_evidence.get(sig,""),md.get("evidenceId") or "",
              arrival,int(md.get("slot") or 0),int(md.get("transactionIndex") or 0),targets)
            except Exception: continue
            for row in decoded:
              create_ms=int(creates[row.event.mint]["createReceivedAtUnixNs"])//1_000_000
              if row.event.is_buy and row.event.mint not in first_buy_ms:first_buy_ms[row.event.mint]=row.arrival_ms
              first_ms=first_buy_ms.get(row.event.mint)
              cutoff=max(create_ms+130_000,(first_ms+120_000) if first_ms is not None else create_ms+130_000)
              if row.arrival_ms<=cutoff:by_mint[row.event.mint].append(row)
    for mint,create in creates.items():
      create["createObservationEvidenceId"]=raw_evidence.get(create.get("createSignature"),"")
    global_trades.sort();global_creates.sort();global_observations.sort(); exclusions=[]
    sol_quotes={"11111111111111111111111111111111","So11111111111111111111111111111111111111112"}
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_suffix(output.suffix+".tmp")
    built_mints=0;opportunity_rows=0;all_causal=True;label_status=collections.Counter()
    with temporary.open("wb") as raw:
      with gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=0) as compressed:
       with io.TextIOWrapper(compressed,encoding="utf-8") as f:
        for mint in sorted(targets,key=lambda m:(int(creates[m].get("createReceivedAtUnixNs") or 0),m)):
          all_events=sorted(by_mint[mint],key=lambda r:(r.arrival_ms,r.slot,r.tx_index,r.instruction_index))
          if any(row.event.mayhem_mode for row in all_events):
            exclusions.append({"mint":mint,"reason":"OUTSIDE_PRIMARY_POPULATION_MAYHEM"});continue
          if all_events and any(row.event.quote_mint not in sol_quotes for row in all_events):
            exclusions.append({"mint":mint,"reason":"OUTSIDE_PRIMARY_POPULATION_NON_SOL_QUOTED"});continue
          events=[row for row in all_events if row.event.quote_mint in sol_quotes]
          if not events: exclusions.append({"mint":mint,"reason":"FEATURE_EVENTS_UNRESOLVED"});continue
          if mint not in supplies: exclusions.append({"mint":mint,"reason":"SUPPLY_UNRESOLVED"});continue
          mint_rows=[]
          for age in CHECKPOINTS_MS:
            row=feature_row(mint,creates[mint],events,supplies[mint],age,global_trades,global_creates,
              global_observations,prior_wallet_index,sorted(failed[mint]))
            if row: mint_rows.append(row)
          first_buy=next((event for event in events if event.event.is_buy),None)
          if first_buy is not None:
            create_ms=int(creates[mint]["createReceivedAtUnixNs"])//1_000_000
            first_impulse_age=max(0,first_buy.arrival_ms-create_ms)
            row=feature_row(mint,creates[mint],events,supplies[mint],first_impulse_age,
              global_trades,global_creates,global_observations,prior_wallet_index,sorted(failed[mint]),"FIRST_IMPULSE_EVENT")
            if row: mint_rows.append(row)
          if not mint_rows:
            exclusions.append({"mint":mint,"reason":"NO_CAUSAL_FEATURE_STATE"});continue
          built_mints+=1
          for row in mint_rows:
            f.write(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n")
            opportunity_rows+=1
            all_causal=all_causal and row.get("availabilityCausalityPass") is True
            label_status.update(cell.get("status","MISSING") for cell in row["labels"].values())
    os.replace(temporary,output)
    return {"implementationVersion":IMPLEMENTATION_VERSION,"partitionKey":report["partitionKey"],
      "sourceFingerprintSha256":source_fingerprint,
      "sourceReadinessManifestSha256":report.get("sourceReconciliationManifestSha256"),
      "selectedMints":len(targets),"builtMints":built_mints,
      "opportunityRows":opportunity_rows,"economicLabelStatusCounts":dict(label_status),
      "allFeatureRowsAvailabilityCausal":all_causal,
      "populationScope":"exact-admitted normal SOL-quoted Pump launches; Mayhem and non-SOL quote excluded",
      "excluded":exclusions,"sha256":hashlib.sha256(output.read_bytes()).hexdigest(),"path":str(output)}

def main()->int:
    p=argparse.ArgumentParser();p.add_argument("root",type=pathlib.Path);p.add_argument("output",type=pathlib.Path);a=p.parse_args()
    report_paths=sorted((a.root/"readiness-v1/partitions").glob(f"*-v{READINESS_IMPLEMENTATION_VERSION}.json"))
    database=sqlite3.connect("");database.execute("PRAGMA temp_store=FILE");database.execute("PRAGMA journal_mode=OFF")
    database.execute("CREATE TABLE admitted(mint TEXT PRIMARY KEY, create_ns INTEGER, partition_key TEXT, row_json TEXT)")
    for path in report_paths:
      report=read_json(path)
      for row in report["mints"]:
        if not row["exactAdmitted"]: continue
        database.execute("INSERT OR IGNORE INTO admitted VALUES (?,?,?,?)",(
          row["mint"],int(row.get("createReceivedAtUnixNs") or 0),report["partitionKey"],
          json.dumps(row,sort_keys=True,separators=(",",":"))))
    database.commit();exact_n=int(database.execute("SELECT COUNT(*) FROM admitted").fetchone()[0])
    selected_rows=[json.loads(row[0])|{"partitionKey":row[1]} for row in database.execute(
      "SELECT row_json,partition_key FROM admitted ORDER BY create_ns,mint LIMIT 10000")]
    prior_wallet_index=PriorWalletIndex(a.output/"cache-v2/recurrent-wallet-observations.sqlite")
    wallet_index_status=update_prior_wallet_index(
      a.root,(read_json(path) for path in report_paths),prior_wallet_index)
    wallet_index_entries=[
      [PRIOR_WALLET_INDEX_VERSION,value.get("partitionKey"),value.get("sourceReconciliationManifestSha256")]
      for value in (read_json(path) for path in report_paths)
    ]
    wallet_index_input_sha=hashlib.sha256(json.dumps(
      wallet_index_entries,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    wallet_index_prefix_sha={entry[1]:hashlib.sha256(json.dumps(
      wallet_index_entries[:index+1],sort_keys=True,separators=(",",":")).encode()).hexdigest()
      for index,entry in enumerate(wallet_index_entries)}
    boundary={"schema":"PUMP_EXACT_ALPHA_BOUNDARIES_V1","frozenAtUtc":dt.datetime.now(dt.timezone.utc).isoformat(),
      "ordering":"createReceivedAtUnixNs,mint","discovery":{"startOrdinal":1,"endOrdinal":10000},
      "validation":{"startOrdinal":10001,"endOrdinal":20000,"inspectionAuthorized":False},
      "holdout":{"startOrdinal":20001,"endOrdinal":30000,"inspectionAuthorized":False},
      "futureProspective":{"startOrdinal":30001},"ledgerRoot":str(a.root)}
    atomic_json(a.output/"boundaries.json",boundary,immutable=True)
    selected={r["mint"] for r in selected_rows};selected_partitions={r["partitionKey"] for r in selected_rows};manifests=[]
    for stale_manifest in sorted((a.output/"manifests-v2").glob("*.json")):
      cached=read_json(stale_manifest)
      if cached.get("partitionKey") in selected_partitions: continue
      stale_row=a.output/"rows-v2"/f"{stale_manifest.stem}.jsonl.gz"
      stale_manifest.unlink();stale_row.unlink(missing_ok=True)
    manifest_stems={path.stem for path in (a.output/"manifests-v2").glob("*.json")}
    for stale_row in sorted((a.output/"rows-v2").glob("*.jsonl.gz")):
      if stale_row.name.removesuffix(".jsonl.gz") not in manifest_stems: stale_row.unlink()
    builder_sha=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
    index_tmp=a.output/"discovery-mint-index.jsonl.tmp";index_tmp.parent.mkdir(parents=True,exist_ok=True)
    with index_tmp.open("w",encoding="utf-8") as handle:
      for ordinal,row in enumerate(selected_rows,1):
        handle.write(json.dumps({"ordinal":ordinal,"mint":row["mint"],"partitionKey":row["partitionKey"],"createReceivedAtUnixNs":row.get("createReceivedAtUnixNs"),"createSignature":row.get("createSignature")},sort_keys=True)+"\n")
    os.replace(index_tmp,a.output/"discovery-mint-index.jsonl")
    for path in report_paths:
      report=read_json(path)
      if report["partitionKey"] not in selected_partitions: continue
      key=report["partitionKey"].replace("/","-"); out=a.output/"rows-v2"/f"{key}.jsonl.gz";manifest_path=a.output/"manifests-v2"/f"{key}.json"
      partition_selected=sorted(r["mint"] for r in selected_rows if r["partitionKey"]==report["partitionKey"])
      source_fingerprint=hashlib.sha256(json.dumps({"implementationVersion":IMPLEMENTATION_VERSION,
        "builderSha256":builder_sha,"sourceReconciliationManifestSha256":report.get("sourceReconciliationManifestSha256"),
        "priorWalletIndexInputSha256":wallet_index_prefix_sha[report["partitionKey"]],"selectedMints":partition_selected},sort_keys=True,separators=(",",":")).encode()).hexdigest()
      if out.exists() and manifest_path.exists():
        cached=read_json(manifest_path)
        if cached.get("sourceFingerprintSha256")==source_fingerprint and hashlib.sha256(out.read_bytes()).hexdigest()==cached.get("sha256"):
          manifests.append(cached);continue
      manifest=process_partition(a.root,report,selected,out,prior_wallet_index,source_fingerprint);atomic_json(manifest_path,manifest);manifests.append(manifest)
    prior_wallet_index.close()
    status={"schema":SCHEMA,"implementationVersion":IMPLEMENTATION_VERSION,
      "generatedAtUtc":dt.datetime.now(dt.timezone.utc).isoformat(),"exactAdmittedN":exact_n,
      "discoveryMintsAvailable":min(exact_n,10000),"validationRowsInspected":0,"holdoutRowsInspected":0,
      "N3000Ready":exact_n>=3000,"N5000Ready":exact_n>=5000,"N10000Ready":exact_n>=10000,
      "partitionManifests":manifests,"allFeatureRowsAvailabilityCausal":all(m.get("allFeatureRowsAvailabilityCausal") is True for m in manifests),
      "primaryEconomicCells":["size_0.50_entry_25_sellable_500","size_1.00_entry_25_sellable_500"],
      "robustnessEconomicCells":{"capacity":["size_0.01_entry_25_sellable_500","size_0.10_entry_25_sellable_500"],
        "timing":"all alternate entry/sellability cells"},
      "populationScope":"exact-admitted normal SOL-quoted Pump launches only",
      "excludedPopulations":["Mayhem","non-SOL-quoted including USDC"],
      "uniqueMintCountingRequired":True,"maxOneExecutionPerMint":True,"noMcProxy":True,"liveSubmissions":0}
    status["priorWalletIndex"]={**wallet_index_status,"implementationVersion":PRIOR_WALLET_INDEX_VERSION,"sourceFingerprintSha256":wallet_index_input_sha,
      "semantics":"full-realtime-source-universe; strictly prior observation; current mint excluded"}
    status["featureAvailabilityAudit"]={family:"REALTIME_OBSERVATION_AT_OR_BEFORE_DECISION" for family in (
      "transaction_counts","buy_sell_flow","unique_wallets","reserve_movement",
      "recurrent_wallets","market_regime_context","first_impulse","fast_flow")}
    atomic_json(a.output/"status.json",status);print(json.dumps(status,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
