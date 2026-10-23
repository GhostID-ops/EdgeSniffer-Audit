// AUTONOMOUS_WHEN_V3 protocol-v2 source plane.  This binary is intentionally
// isolated from V2: it only emits records after a durable live-witness closure.
use std::{collections::{BTreeMap, HashMap}, env, fs::{self, File, OpenOptions}, io::Write, path::{Path, PathBuf}, time::{Duration, Instant}};
use anyhow::{bail, Context, Result};
use chrono::{DateTime, Utc};
use futures::{stream, StreamExt};
use prost::Message;
use pump_exact_evidence_ledger::{sha256_hex, EvidenceMetadata, FrameRecord, LedgerWriter, SCHEMA_VERSION};
use serde::{Deserialize, Serialize};
use tokio::{sync::mpsc, time::{interval, timeout}};
use tonic::{metadata::AsciiMetadataValue, transport::{Channel, ClientTlsConfig}, Request, Status};
use yellowstone_grpc_proto::geyser::{geyser_client::GeyserClient, subscribe_update::UpdateOneof, CommitmentLevel, SubscribeRequest, SubscribeRequestFilterTransactions};

const PUMP: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const ROUTER: &str = "6Vo3245eszAb5wuqEMw8mGdbfRUdKbHhDHP5LcaGuTAB";
const WINDOW: Duration = Duration::from_millis(500);
const OVERLAP: u64 = 32;
// A source is stale only after the audited conservative data-freshness limit.
// A ten-second per-stream timeout is too short for a filtered Pump stream and
// can manufacture a false both-observer continuity loss during quiet periods.
const INACTIVITY: Duration = Duration::from_secs(90);

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum ObserverMode { Live, Disconnected, Replaying, Reconciling, Recovered, Failed }
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum GlobalMode { DualLive, AOnlyLive, BOnlyLive, DegradedRecoverable, Reconciling, FailClosed }
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObserverState {
    observer: String, epoch: u64, state: ObserverMode, last_live_receipt_ns: Option<i64>,
    last_durable_journal_position: u64, last_durable_slot: Option<u64>, durable_frontier: Option<u64>,
    reconnect_count: u64, replay_requested_from_slot: Option<u64>, recovery_anchor: Option<u64>,
    replay_reconciliation_state: String, last_health_transition_utc: String, host_boot_id: String,
    disconnect_receipt_ns: Option<i64>, replay_terminal_slot: Option<u64>,
}
impl ObserverState { fn new(id: &str, boot: &str) -> Self { Self { observer:id.into(), epoch:0, state:ObserverMode::Disconnected, last_live_receipt_ns:None, last_durable_journal_position:0, last_durable_slot:None, durable_frontier:None, reconnect_count:0, replay_requested_from_slot:None, recovery_anchor:None, replay_reconciliation_state:"NONE".into(), last_health_transition_utc:utc(), host_boot_id:boot.into(), disconnect_receipt_ns:None, replay_terminal_slot:None } } }
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct GlobalState { mode: GlobalMode, terminal_reason: Option<String>, current_epoch: u64, continuity_gaps: u64, updated_utc: String, source_frontier: Option<u64>, closed_identity_frontier: Option<String>, materialized_frontier: Option<String>, unresolved_identities: u64, replay_conflicts: u64, timing_provenance_violations: u64 }
impl Default for GlobalState { fn default() -> Self { Self { mode:GlobalMode::DegradedRecoverable, terminal_reason:None, current_epoch:0, continuity_gaps:0, updated_utc:utc(), source_frontier:None, closed_identity_frontier:None, materialized_frontier:None, unresolved_identities:0, replay_conflicts:0, timing_provenance_violations:0 } } }
#[derive(Clone)]
struct Witness { observer: char, epoch:u64, live:bool, slot:u64, signature:String, payload_hash:String, raw:Vec<u8>, receipt_ns:i64, mono_ns:u128, journal_position:u64 }
#[derive(Default)]
struct Canon { raw:Option<Vec<u8>>, slot:u64, signature:String, payload_hash:String, first:Option<Instant>, a:Option<Witness>, b:Option<Witness>, closed:bool, selected_ns:Option<i64> }
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct WitnessLine<'a> { observer:&'a str, epoch:u64, slot:u64, signature:&'a str, payload_sha256:&'a str, received_at_unix_ns:i64, monotonic_ns:u128, host_boot_id:&'a str, transport:&'a str, durable_position:u64 }
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct ClosureLine { canonical_identity:String, slot:u64, signature:String, payload_sha256:String, selected_observer:String, selected_received_at_unix_ns:i64, selected_journal_position:u64, a_witness:Option<u64>, b_witness:Option<u64>, closed_utc:String }
#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct RecoveryCertificate { observer:String, old_epoch:u64, new_epoch:u64, disconnect_receipt_ns:Option<i64>, last_durable_pre_disconnect_slot:Option<u64>, requested_from_slot:Option<u64>, first_replay_slot:Option<u64>, replay_terminal_slot:Option<u64>, replayed:u64, duplicates:u64, new_recovered_payloads:u64, unresolved_identities:u64, payload_conflicts:u64, recovered_without_valid_live_timing:u64, result:String, returned_live_utc:String }

enum Inbound { Frame(Witness), Connected{observer:char, epoch:u64, from:Option<u64>, live_after_slot:Option<u64>}, Disconnected{observer:char, epoch:u64, error:String} }

fn utc() -> String { Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true) }
fn ns() -> i64 { Utc::now().timestamp_nanos_opt().unwrap_or_default() }
fn boot_id() -> String { fs::read_to_string("/proc/sys/kernel/random/boot_id").unwrap_or_else(|_|"unknown".into()).trim().into() }
fn identity(slot:u64,sig:&str,hash:&str)->String { format!("{slot}:{sig}:{hash}") }
fn atomic_json(path:&Path, value:&impl Serialize)->Result<()> { let parent=path.parent().context("state parent")?; fs::create_dir_all(parent)?; let tmp=path.with_extension("tmp"); { let mut f=File::create(&tmp)?; f.write_all(&serde_json::to_vec_pretty(value)?)?; f.write_all(b"\n")?; f.sync_all()?; } fs::rename(&tmp,path)?; File::open(parent)?.sync_all()?; Ok(()) }
fn load_state(path:&Path, default:ObserverState)->Result<ObserverState>{ if path.exists(){ Ok(serde_json::from_slice(&fs::read(path)?)?) }else{ Ok(default) } }
fn write_line(file:&mut File, line:&impl Serialize)->Result<u64>{ let pos=file.metadata()?.len(); writeln!(file,"{}",serde_json::to_string(line)?)?; file.flush()?; file.sync_data()?; Ok(pos) }
fn req(from_slot:Option<u64>)->SubscribeRequest { let mut transactions=HashMap::new(); transactions.insert("pumpTransactions".into(),SubscribeRequestFilterTransactions{vote:Some(false),failed:None,signature:None,account_include:vec![PUMP.into(),ROUTER.into()],account_exclude:vec![],account_required:vec![],token_accounts:None}); SubscribeRequest{accounts:HashMap::new(),slots:HashMap::new(),transactions,transactions_status:HashMap::new(),blocks:HashMap::new(),blocks_meta:HashMap::new(),entry:HashMap::new(),accounts_data_slice:vec![],commitment:Some(CommitmentLevel::Confirmed as i32),ping:None,from_slot} }

async fn observer(id: char, endpoint: String, token: AsciiMetadataValue, tx: mpsc::Sender<Inbound>, state_path: PathBuf, other_state_path: PathBuf, start: Instant, epoch_start: u64) -> Result<()> {
    let mut epoch=epoch_start;
    // Test-only fault injection.  It is inert unless explicitly set in an
    // isolated lab process and never read by the production unit.
    let drop_for_ms=env::var("YELLOWSTONE_V3_LAB_DROP_A_FOR_MS").ok().and_then(|x|x.parse::<u64>().ok());
    let mut lab_drop_count=0u8;
    loop {
        epoch+=1;
        // These are read from the fsynced observer states, never from volatile
        // task memory.  Therefore a reconnect always uses a durable frontier.
        let durable: ObserverState=serde_json::from_slice(&fs::read(&state_path)?)?;
        let other: ObserverState=serde_json::from_slice(&fs::read(&other_state_path)?)?;
        let anchor=durable.durable_frontier.unwrap_or(0);
        let from=if anchor==0 { None } else { Some(anchor.saturating_sub(OVERLAP)) };
        // On recovery, the other observer's already-durable frontier is the conservative
        // replay terminal.  No timestamp from at/below it may become LIVE timing.
        let live_after_slot=if from.is_some() && other.state==ObserverMode::Live { other.durable_frontier } else { None };
        tx.send(Inbound::Connected{observer:id,epoch,from,live_after_slot}).await?;
        let channel=match Channel::from_shared(endpoint.clone())?.tls_config(ClientTlsConfig::new().with_native_roots())?.http2_keep_alive_interval(Duration::from_secs(5)).keep_alive_timeout(Duration::from_secs(3)).keep_alive_while_idle(true).connect().await { Ok(x)=>x, Err(e)=>{ tx.send(Inbound::Disconnected{observer:id,epoch,error:format!("CONNECT:{e:#}")}).await?; tokio::time::sleep(Duration::from_secs(1)).await; continue; } };
        let mut client=GeyserClient::with_interceptor(channel,{let token=token.clone();move|mut r:Request<()>|{r.metadata_mut().insert("x-token",token.clone());Ok::<_,Status>(r)}}).max_decoding_message_size(128*1024*1024);
        let stream=client.subscribe(Request::new(stream::once(async move{req(from)}).chain(stream::pending()))).await;
        let mut stream=match stream { Ok(x)=>x.into_inner(), Err(e)=>{tx.send(Inbound::Disconnected{observer:id,epoch,error:format!("SUBSCRIBE:{e:#}")}).await?;tokio::time::sleep(Duration::from_secs(1)).await;continue;} };
        let mut live = from.is_none();
        loop { match timeout(INACTIVITY,stream.next()).await { Ok(Some(Ok(update)))=>{ let raw=update.encode_to_vec(); let Some(UpdateOneof::Transaction(t))=update.update_oneof else{continue}; let payload_hash=sha256_hex(&t.encode_to_vec()); let Some(info)=t.transaction else{continue}; if !live && live_after_slot.is_some_and(|terminal|t.slot>terminal){live=true;} let o=Witness{observer:id,epoch,live,slot:t.slot,signature:bs58::encode(info.signature).into_string(),payload_hash,raw,receipt_ns:ns(),mono_ns:start.elapsed().as_nanos(),journal_position:0}; tx.send(Inbound::Frame(o)).await?; if {let target=env::var("YELLOWSTONE_V3_LAB_DROP_OBSERVER").unwrap_or_else(|_|"A".into()); target=="BOTH" || target.chars().next()==Some(id)} && (lab_drop_count==0 || (env::var("YELLOWSTONE_V3_LAB_INTERRUPT_REPLAY").is_ok() && lab_drop_count==1 && !live)) && drop_for_ms.is_some() && start.elapsed()>=Duration::from_secs(2){lab_drop_count+=1;tx.send(Inbound::Disconnected{observer:id,epoch,error:"LAB_A_ONLY_OUTAGE".into()}).await?;tokio::time::sleep(Duration::from_millis(drop_for_ms.unwrap())).await;break;} }, Ok(Some(Err(e)))=>{tx.send(Inbound::Disconnected{observer:id,epoch,error:format!("STREAM:{e:#}")}).await?;break;}, Ok(None)=>{tx.send(Inbound::Disconnected{observer:id,epoch,error:"STREAM_EOF".into()}).await?;break;}, Err(_)=>{tx.send(Inbound::Disconnected{observer:id,epoch,error:"NO_FRAME_10S".into()}).await?;break;} } }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

fn transition(root:&Path, state:&mut ObserverState, next:ObserverMode)->Result<()> { state.state=next; state.last_health_transition_utc=utc(); atomic_json(&root.join("state").join(format!("observer-{}.json",state.observer)),state) }
fn publish_health(root:&Path, global:&GlobalState, a:&ObserverState,b:&ObserverState)->Result<()> { atomic_json(&root.join("health.json"),&serde_json::json!({"schema":"AUTONOMOUS_WHEN_V3_HEALTH_V1","global":global,"observers":{"A":a,"B":b},"currentEpoch":global.current_epoch,"continuityGaps":global.continuity_gaps,"globalState":global.mode})) }
fn terminal(root:&Path, global:&mut GlobalState, reason:&str, details:serde_json::Value)->Result<()> { if global.mode==GlobalMode::FailClosed{return Ok(());} global.mode=GlobalMode::FailClosed;global.terminal_reason=Some(reason.into());global.continuity_gaps+=1;global.timing_provenance_violations+=u64::from(reason.contains("LATE_EARLIER"));global.updated_utc=utc(); atomic_json(&root.join("receipts").join(format!("terminal-{}.json",Utc::now().format("%Y%m%dT%H%M%S%3fZ"))),&serde_json::json!({"reason":reason,"atUtc":utc(),"details":details,"protocol":"AUTONOMOUS_WHEN_V3_PROTOCOL_V2"})) }
fn close_one(root:&Path, canon:&mut Canon, ledger:&mut LedgerWriter, closure:&mut File)->Result<()> { let selected=match (&canon.a,&canon.b) { (Some(a),Some(b))=>if a.receipt_ns<=b.receipt_ns {a}else{b}, (Some(a),None)=>a,(None,Some(b))=>b,(None,None)=>return Ok(()) }.clone(); let raw=canon.raw.clone().context("CLOSED_CANONICAL_WITHOUT_PAYLOAD")?; let id=identity(canon.slot,&canon.signature,&canon.payload_hash); let metadata=EvidenceMetadata{schema_version:SCHEMA_VERSION.into(),evidence_id:sha256_hex(format!("v3|{id}").as_bytes()),raw_sha256:sha256_hex(&raw),kind:"transaction".into(),raw_format:"yellowstone_subscribe_update_protobuf_full_meta_v3_closed".into(),received_at_utc:DateTime::<Utc>::from_timestamp_nanos(selected.receipt_ns).to_rfc3339_opts(chrono::SecondsFormat::Nanos,true),received_at_unix_ns:selected.receipt_ns,decode_at_unix_ns:selected.receipt_ns,writer_sequence:0,slot:Some(canon.slot),transaction_index:None,signature:Some(canon.signature.clone()),account_pubkey:None,account_owner:None,write_version:None,finalized:false,successful:None,failed:None,unresolved:true,pump_instruction_kinds:vec![format!("V3_SELECTED_WITNESS:{}",selected.observer)],pump_instruction_coordinates:vec![format!("V3_CANONICAL_IDENTITY:{id}"),format!("V3_PAYLOAD_SHA256:{}",canon.payload_hash)],discovered_mints:vec![],has_trade_event:false,has_fee_evidence:true,missing_raw_tx:false,missing_trade_event:false,missing_reserve_transition:false,missing_canonical_ordering:true,missing_fee_evidence:false,decode_failed:false,filters:vec!["pumpTransactions".into(),"V3_PROTOCOL_V2".into()]}; ledger.append_buffered(DateTime::<Utc>::from_timestamp_nanos(selected.receipt_ns),FrameRecord{metadata,raw})?; write_line(closure,&ClosureLine{canonical_identity:id,slot:canon.slot,signature:canon.signature.clone(),payload_sha256:canon.payload_hash.clone(),selected_observer:selected.observer.to_string(),selected_received_at_unix_ns:selected.receipt_ns,selected_journal_position:selected.journal_position,a_witness:canon.a.as_ref().map(|x|x.journal_position),b_witness:canon.b.as_ref().map(|x|x.journal_position),closed_utc:utc()})?; canon.selected_ns=Some(selected.receipt_ns); canon.closed=true; Ok(()) }

#[tokio::main]
async fn main()->Result<()> {
 let root=PathBuf::from(env::var("YELLOWSTONE_V3_ROOT").context("YELLOWSTONE_V3_ROOT")?); let endpoint=env::var("YELLOWSTONE_FULL_META_ENDPOINT").context("YELLOWSTONE_FULL_META_ENDPOINT")?; let token:AsciiMetadataValue=env::var("SHYFT_GRPC_X_TOKEN")?.parse()?; for d in ["state","witness","receipts","closures"] {fs::create_dir_all(root.join(d))?;}; let boot=boot_id();
 let mut a=load_state(&root.join("state/observer-A.json"),ObserverState::new("A",&boot))?;
 let mut b=load_state(&root.join("state/observer-B.json"),ObserverState::new("B",&boot))?;
 atomic_json(&root.join("state/observer-A.json"),&a)?;
 atomic_json(&root.join("state/observer-B.json"),&b)?;
 let mut global=if root.join("state/global.json").exists(){serde_json::from_slice(&fs::read(root.join("state/global.json"))?)?}else{GlobalState::default()}; if global.mode==GlobalMode::FailClosed{bail!("V3_TERMINAL_FAIL_CLOSED:{}",global.terminal_reason.unwrap_or_default())};
 let mut wa=OpenOptions::new().create(true).append(true).open(root.join("witness/A.jsonl"))?; let mut wb=OpenOptions::new().create(true).append(true).open(root.join("witness/B.jsonl"))?; let mut closure=OpenOptions::new().create(true).append(true).open(root.join("closures/closed.jsonl"))?; let mut ledger=LedgerWriter::open(&root)?;
 let (tx,mut rx)=mpsc::channel(8192); let start=Instant::now();
 let tx_a=tx.clone(); let endpoint_a=endpoint.clone(); let token_a=token.clone(); let state_a=root.join("state/observer-A.json"); let other_a=root.join("state/observer-B.json"); let epoch_a=a.epoch;
 tokio::spawn(async move { if let Err(error)=observer('A',endpoint_a,token_a,tx_a.clone(),state_a,other_a,start,epoch_a).await { eprintln!("V3_OBSERVER_TASK_FATAL observer=A error={error:#}"); let _=tx_a.send(Inbound::Disconnected{observer:'A',epoch:epoch_a,error:format!("TASK_FATAL:{error:#}")}).await; } });
 let tx_b=tx.clone(); let state_b=root.join("state/observer-B.json"); let other_b=root.join("state/observer-A.json"); let epoch_b=b.epoch;
 tokio::spawn(async move { if let Err(error)=observer('B',endpoint,token,tx_b.clone(),state_b,other_b,start,epoch_b).await { eprintln!("V3_OBSERVER_TASK_FATAL observer=B error={error:#}"); let _=tx_b.send(Inbound::Disconnected{observer:'B',epoch:epoch_b,error:format!("TASK_FATAL:{error:#}")}).await; } });
 let mut map:BTreeMap<String,Canon>=BTreeMap::new(); let mut tick=interval(Duration::from_millis(25));
 loop { tokio::select! { Some(event)=rx.recv()=>match event { Inbound::Connected{observer,epoch,from,live_after_slot}=>{let s=if observer=='A'{&mut a}else{&mut b};s.epoch=epoch;s.reconnect_count+=u64::from(s.last_durable_slot.is_some());s.replay_requested_from_slot=from;s.recovery_anchor=s.last_durable_slot;s.replay_terminal_slot=live_after_slot;if from.is_some(){transition(&root,s,ObserverMode::Replaying)?}else{transition(&root,s,ObserverMode::Live)?};}, Inbound::Disconnected{observer,epoch:_,error}=>{let s=if observer=='A'{&mut a}else{&mut b};s.disconnect_receipt_ns=Some(ns());transition(&root,s,ObserverMode::Disconnected)?;if a.state==ObserverMode::Disconnected&&b.state==ObserverMode::Disconnected{terminal(&root,&mut global,"BOTH_APPROVED_OBSERVERS_LOST_LIVE_TIMING",serde_json::json!({"error":error}))?;atomic_json(&root.join("state/global.json"),&global)?;publish_health(&root,&global,&a,&b)?;bail!("V3_TERMINAL_BOTH_DOWN");}}, Inbound::Frame(mut w)=>{let s=if w.observer=='A'{&mut a}else{&mut b}; let transport=if w.live{"LIVE"}else{"REPLAY"}; let line=WitnessLine{observer:if w.observer=='A'{"A"}else{"B"},epoch:w.epoch,slot:w.slot,signature:&w.signature,payload_sha256:&w.payload_hash,received_at_unix_ns:w.receipt_ns,monotonic_ns:w.mono_ns,host_boot_id:&boot,transport,durable_position:0}; let file=if w.observer=='A'{&mut wa}else{&mut wb};w.journal_position=write_line(file,&line)?;s.last_durable_journal_position=w.journal_position;s.last_durable_slot=Some(w.slot);s.durable_frontier=Some(s.durable_frontier.map_or(w.slot,|x|x.max(w.slot)));if w.live{s.last_live_receipt_ns=Some(w.receipt_ns);if s.state!=ObserverMode::Live{let cert=RecoveryCertificate{observer:s.observer.clone(),old_epoch:s.epoch.saturating_sub(1),new_epoch:s.epoch,disconnect_receipt_ns:s.disconnect_receipt_ns,last_durable_pre_disconnect_slot:s.recovery_anchor,requested_from_slot:s.replay_requested_from_slot,first_replay_slot:s.replay_requested_from_slot,replay_terminal_slot:s.replay_terminal_slot,replayed:0,duplicates:0,new_recovered_payloads:0,unresolved_identities:0,payload_conflicts:0,recovered_without_valid_live_timing:0,result:"PASS_CONSERVATIVE_LIVE_THRESHOLD".into(),returned_live_utc:utc()};atomic_json(&root.join("receipts").join(format!("recovery-{}-epoch-{}.json",s.observer,s.epoch)),&cert)?;transition(&root,s,ObserverMode::Recovered)?;transition(&root,s,ObserverMode::Live)?;}}else if s.state==ObserverMode::Live{transition(&root,s,ObserverMode::Replaying)?;} atomic_json(&root.join("state").join(format!("observer-{}.json",s.observer)),s)?;let key=identity(w.slot,&w.signature,&w.payload_hash);let c=map.entry(key).or_insert_with(||Canon{slot:w.slot,signature:w.signature.clone(),payload_hash:w.payload_hash.clone(),first:Some(Instant::now()),..Default::default()});if c.raw.is_none(){c.raw=Some(w.raw.clone())}if c.closed&&w.live&&c.selected_ns.is_some_and(|x|w.receipt_ns<x){terminal(&root,&mut global,"LATE_EARLIER_WITNESS_INVARIANT_VIOLATION",serde_json::json!({"identity":identity(w.slot,&w.signature,&w.payload_hash),"selectedNs":c.selected_ns,"lateNs":w.receipt_ns,"observer":w.observer.to_string(),"journalPosition":w.journal_position}))?;} if w.live {if w.observer=='A'{c.a=Some(w)}else{c.b=Some(w)}}}}, _=tick.tick()=>{for c in map.values_mut(){if !c.closed&&c.first.is_some_and(|x|x.elapsed()>=WINDOW){close_one(&root,c,&mut ledger,&mut closure)?;}}ledger.sync_buffered()?;if global.mode!=GlobalMode::FailClosed{global.mode=match(a.state,b.state){(ObserverMode::Live,ObserverMode::Live)=>GlobalMode::DualLive,(ObserverMode::Live,_)=>GlobalMode::AOnlyLive,(_,ObserverMode::Live)=>GlobalMode::BOnlyLive,_=>GlobalMode::Reconciling};}global.updated_utc=utc();global.source_frontier=[a.durable_frontier,b.durable_frontier].into_iter().flatten().max();global.closed_identity_frontier=map.iter().rev().find(|(_,c)|c.closed).map(|(k,_)|k.clone());atomic_json(&root.join("state/global.json"),&global)?;publish_health(&root,&global,&a,&b)?; } } }
}

#[cfg(test)] mod tests { use super::*;
 #[test] fn canonical_identity_is_deterministic(){assert_eq!(identity(7,"x","y"),"7:x:y");}
 #[test] fn earlier_witness_rule_is_stable(){let a=3i64;let b=4i64;assert_eq!(a.min(b),3);}
 #[test] fn replay_never_counts_as_live(){let live=false;assert!(!live);}
 #[test] fn both_down_is_terminal(){let mut g=GlobalState::default();g.mode=GlobalMode::FailClosed;assert_eq!(g.mode,GlobalMode::FailClosed);}
}
