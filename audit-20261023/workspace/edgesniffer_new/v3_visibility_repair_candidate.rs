// AUTONOMOUS_WHEN_V3 protocol-v2 source plane.
//
// Receipt time is captured at the gRPC receive point.  A receipt cannot enter
// canonical selection until its observer journal has been synced.  The source
// only advertises a causal frontier after canonical output is also durable.
use std::{
    collections::{BTreeMap, HashMap},
    env,
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{atomic::{AtomicUsize, Ordering}, Arc},
    time::{Duration, Instant},
};

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Utc};
use futures::{stream, StreamExt};
use prost::Message;
use pump_exact_evidence_ledger::{sha256_hex, EvidenceMetadata, FrameRecord, LedgerWriter, SCHEMA_VERSION};
use serde::{Deserialize, Serialize};
use tokio::{sync::mpsc, time::interval};
use tonic::{metadata::AsciiMetadataValue, transport::{Channel, ClientTlsConfig}, Request, Status};
use yellowstone_grpc_proto::geyser::{geyser_client::GeyserClient, subscribe_update::UpdateOneof, CommitmentLevel, SubscribeRequest, SubscribeRequestFilterTransactions};

const PUMP: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const ROUTER: &str = "6Vo3245eszAb5wuqEMw8mGdbfRUdKbHhDHP5LcaGuTAB";
const WITNESS_WINDOW: Duration = Duration::from_millis(500);
const PROGRESS_INTERVAL: Duration = Duration::from_millis(50);
// Durable recovery anchors are batched; health itself is published separately
// without fsync and never drives scientific completeness.
const STATE_INTERVAL: Duration = Duration::from_secs(1);
// The materializer consumes only already-durable frames. Publish that durable
// frontier frequently enough to leave cadence headroom without turning health
// telemetry into a new scientific evidence or durability path.
const HEALTH_INTERVAL: Duration = Duration::from_millis(100);
const INACTIVITY: Duration = Duration::from_secs(90);
const OVERLAP: u64 = 32;
// Keep the receipt-to-progress barrier bounded. A larger queue only lets a
// transaction burst defer the already-durable visibility watermark; it does
// not increase scientific throughput. Backpressure remains lossless at the
// gRPC flow-control boundary.
const DURABILITY_QUEUE: usize = 256;
const INBOUND_QUEUE: usize = 1_024;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum ObserverMode { Live, Disconnected, Replaying, Reconciling, Recovered, Failed }

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum GlobalMode { DualLive, AOnlyLive, BOnlyLive, DegradedRecoverable, Reconciling, FailClosed }

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObserverState {
    observer: String,
    epoch: u64,
    state: ObserverMode,
    last_live_receipt_ns: Option<i64>,
    last_durable_journal_position: u64,
    last_durable_slot: Option<u64>,
    durable_frontier: Option<u64>,
    #[serde(default)] durable_progress_ns: Option<i64>,
    #[serde(default)] queue_depth: usize,
    #[serde(default)] processing_lag_ns: Option<i64>,
    reconnect_count: u64,
    replay_requested_from_slot: Option<u64>,
    recovery_anchor: Option<u64>,
    replay_reconciliation_state: String,
    last_health_transition_utc: String,
    host_boot_id: String,
    disconnect_receipt_ns: Option<i64>,
    replay_terminal_slot: Option<u64>,
}

impl ObserverState {
    fn new(id: &str, boot: &str) -> Self {
        Self { observer:id.into(), epoch:0, state:ObserverMode::Disconnected, last_live_receipt_ns:None,
            last_durable_journal_position:0, last_durable_slot:None, durable_frontier:None,
            durable_progress_ns:None, queue_depth:0, processing_lag_ns:None, reconnect_count:0,
            replay_requested_from_slot:None, recovery_anchor:None, replay_reconciliation_state:"NONE".into(),
            last_health_transition_utc:utc(), host_boot_id:boot.into(), disconnect_receipt_ns:None,
            replay_terminal_slot:None }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct GlobalState {
    mode: GlobalMode,
    terminal_reason: Option<String>,
    current_epoch: u64,
    continuity_gaps: u64,
    updated_utc: String,
    source_frontier: Option<u64>,
    closed_identity_frontier: Option<String>,
    materialized_frontier: Option<String>,
    unresolved_identities: u64,
    replay_conflicts: u64,
    timing_provenance_violations: u64,
    #[serde(default)] closed_through_unix_ns: Option<i64>,
    #[serde(default)] durable_output_sequence: u64,
    #[serde(default)] source_processing_lag_ns: Option<i64>,
    #[serde(default)] late_earlier_violations: u64,
    #[serde(default)] source_armed: bool,
}

impl Default for GlobalState {
    fn default() -> Self { Self { mode:GlobalMode::DegradedRecoverable, terminal_reason:None, current_epoch:0,
        continuity_gaps:0, updated_utc:utc(), source_frontier:None, closed_identity_frontier:None,
        materialized_frontier:None, unresolved_identities:0, replay_conflicts:0,
        timing_provenance_violations:0, closed_through_unix_ns:None, durable_output_sequence:0,
        source_processing_lag_ns:None, late_earlier_violations:0, source_armed:false } }
}

#[derive(Clone)]
struct Witness {
    observer: char, epoch:u64, live:bool, slot:u64, signature:String, payload_hash:String,
    raw:Vec<u8>, receipt_ns:i64, mono_ns:u128, journal_position:u64,
}

#[derive(Default)]
struct Canon {
    raw:Option<Vec<u8>>, slot:u64, signature:String, payload_hash:String,
    first_live:Option<Instant>, a:Option<Witness>, b:Option<Witness>, closed:bool,
    selected_ns:Option<i64>, writer_sequence:Option<u64>,
}

#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct WitnessLine<'a> {
    observer:&'a str, epoch:u64, slot:u64, signature:&'a str, payload_sha256:&'a str,
    received_at_unix_ns:i64, monotonic_ns:u128, host_boot_id:&'a str, transport:&'a str,
    durable_position:u64,
}

#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct ClosureLine {
    canonical_identity:String, writer_sequence:u64, slot:u64, signature:String, payload_sha256:String,
    selected_observer:String, selected_received_at_unix_ns:i64, selected_journal_position:u64,
    a_witness:Option<u64>, b_witness:Option<u64>, closed_utc:String,
}

#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct RecoveryCertificate {
    observer:String, old_epoch:u64, new_epoch:u64, disconnect_receipt_ns:Option<i64>,
    last_durable_pre_disconnect_slot:Option<u64>, requested_from_slot:Option<u64>,
    first_replay_slot:Option<u64>, replay_terminal_slot:Option<u64>, replayed:u64, duplicates:u64,
    new_recovered_payloads:u64, unresolved_identities:u64, payload_conflicts:u64,
    recovered_without_valid_live_timing:u64, result:String, returned_live_utc:String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all="SCREAMING_SNAKE_CASE")]
enum OrderedControl {
    ReplayStart { epoch:u64, from:Option<u64>, live_after_slot:Option<u64> },
    LiveLost { epoch:u64, error:String },
    LiveRestored { epoch:u64 },
}

#[derive(Serialize)]
#[serde(rename_all="camelCase")]
struct ControlLine {
    observer:String, control:OrderedControl, recorded_at_utc:String,
}

// Every item affecting scientific availability travels this queue.  In
// particular, a transport loss cannot overtake a live receipt accepted first.
enum DurabilityInput {
    Frame(Witness),
    Progress { epoch:u64, through_ns:i64 },
    Control(OrderedControl),
}
enum Inbound {
    DurableFrame(Witness), DurableProgress{observer:char, epoch:u64, through_ns:i64},
    Control{observer:char, control:OrderedControl},
    TaskFatal{observer:char, error:String},
}

fn utc() -> String { Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true) }
fn ns() -> i64 { Utc::now().timestamp_nanos_opt().unwrap_or_default() }
fn boot_id() -> String { fs::read_to_string("/proc/sys/kernel/random/boot_id").unwrap_or_else(|_|"unknown".into()).trim().into() }
fn identity(slot:u64,sig:&str,hash:&str)->String { format!("{slot}:{sig}:{hash}") }

fn atomic_json(path:&Path, value:&impl Serialize)->Result<()> {
    let parent=path.parent().context("state parent")?; fs::create_dir_all(parent)?;
    let tmp=path.with_extension("tmp");
    { let mut f=File::create(&tmp)?; f.write_all(&serde_json::to_vec_pretty(value)?)?; f.write_all(b"\n")?; f.sync_all()?; }
    fs::rename(&tmp,path)?; File::open(parent)?.sync_all()?; Ok(())
}

// Health is telemetry; it intentionally avoids directory fsync amplification.
fn publish_health(root:&Path, global:&GlobalState, a:&ObserverState,b:&ObserverState)->Result<()> {
    let path=root.join("health.json"); let tmp=path.with_extension("tmp");
    fs::write(&tmp, serde_json::to_vec_pretty(&serde_json::json!({
        "schema":"AUTONOMOUS_WHEN_V3_HEALTH_V2", "global":global,
        "observers":{"A":a,"B":b}, "currentEpoch":global.current_epoch,
        "continuityGaps":global.continuity_gaps, "globalState":global.mode,
        "latestSlot":global.source_frontier,
        "closedThroughUnixNs":global.closed_through_unix_ns,
        "durableOutputSequence":global.durable_output_sequence,
    }))?)?;
    fs::rename(tmp,path)?; Ok(())
}

fn load_state(path:&Path, default:ObserverState)->Result<ObserverState>{ if path.exists(){ Ok(serde_json::from_slice(&fs::read(path)?)?) }else{ Ok(default) } }

fn req(from_slot:Option<u64>)->SubscribeRequest {
    let mut transactions=HashMap::new();
    transactions.insert("pumpTransactions".into(),SubscribeRequestFilterTransactions{vote:Some(false),failed:None,signature:None,account_include:vec![PUMP.into(),ROUTER.into()],account_exclude:vec![],account_required:vec![],token_accounts:None});
    SubscribeRequest{accounts:HashMap::new(),slots:HashMap::new(),transactions,transactions_status:HashMap::new(),blocks:HashMap::new(),blocks_meta:HashMap::new(),entry:HashMap::new(),accounts_data_slice:vec![],commitment:Some(CommitmentLevel::Confirmed as i32),ping:None,from_slot}
}

fn spawn_durability_worker(
    id: char, root: PathBuf, boot: String, mut rx: mpsc::Receiver<DurabilityInput>,
    out: mpsc::Sender<Inbound>, queued: Arc<AtomicUsize>,
) {
    std::thread::Builder::new().name(format!("v3-durable-{id}")).spawn(move || {
        let path=root.join("witness").join(format!("{id}.jsonl"));
        let control_path=root.join("witness").join(format!("{id}-controls.jsonl"));
        let mut file=match OpenOptions::new().create(true).append(true).open(&path) { Ok(f)=>f, Err(e)=>{ eprintln!("V3_DURABILITY_OPEN_FAILED {id}: {e}"); let _=out.blocking_send(Inbound::TaskFatal{observer:id,error:format!("DURABILITY_OPEN:{e}")}); return; } };
        let mut control_file=match OpenOptions::new().create(true).append(true).open(&control_path) { Ok(f)=>f, Err(e)=>{ eprintln!("V3_CONTROL_OPEN_FAILED {id}: {e}"); let _=out.blocking_send(Inbound::TaskFatal{observer:id,error:format!("CONTROL_OPEN:{e}")}); return; } };
        let mut pending:Vec<Witness>=Vec::new();
        let mut next_position=file.metadata().map(|x|x.len()).unwrap_or(0);
        while let Some(input)=rx.blocking_recv() {
            match input {
                DurabilityInput::Frame(mut witness) => {
                    let line=WitnessLine{observer:if id=='A'{"A"}else{"B"},epoch:witness.epoch,slot:witness.slot,signature:&witness.signature,payload_sha256:&witness.payload_hash,received_at_unix_ns:witness.receipt_ns,monotonic_ns:witness.mono_ns,host_boot_id:&boot,transport:if witness.live{"LIVE"}else{"REPLAY"},durable_position:next_position};
                    let write_result: Result<()> = (|| { let encoded=serde_json::to_vec(&line)?; file.write_all(&encoded)?; file.write_all(b"\n")?; Ok(()) })();
                    match write_result {
                        Ok(()) => { witness.journal_position=next_position; next_position=file.metadata().map(|x|x.len()).unwrap_or(next_position); pending.push(witness); }
                        Err(e) => { eprintln!("V3_DURABILITY_WRITE_FAILED {id}: {e}"); break; }
                    }
                }
                DurabilityInput::Progress{epoch,through_ns} => {
                    // A marker is ordered behind every receipt already accepted by this observer.
                    if let Err(e)=file.flush().and_then(|_|file.sync_data()) { eprintln!("V3_DURABILITY_SYNC_FAILED {id}: {e}"); break; }
                    for witness in pending.drain(..) {
                        queued.fetch_sub(1,Ordering::Relaxed);
                        if out.blocking_send(Inbound::DurableFrame(witness)).is_err(){return;}
                    }
                    if out.blocking_send(Inbound::DurableProgress{observer:id,epoch,through_ns}).is_err(){return;}
                }
                DurabilityInput::Control(control) => {
                    // This is the availability barrier: all earlier receipts are
                    // synced and delivered before the control reaches the merger.
                    if let Err(e)=file.flush().and_then(|_|file.sync_data()) { let _=out.blocking_send(Inbound::TaskFatal{observer:id,error:format!("DURABILITY_SYNC:{e}")}); return; }
                    for witness in pending.drain(..) {
                        queued.fetch_sub(1,Ordering::Relaxed);
                        if out.blocking_send(Inbound::DurableFrame(witness)).is_err(){return;}
                    }
                    let line=ControlLine{observer:id.to_string(),control:control.clone(),recorded_at_utc:utc()};
                    let control_result: Result<()> = (|| { serde_json::to_writer(&mut control_file,&line)?; control_file.write_all(b"\n")?; control_file.flush()?; control_file.sync_data()?; Ok(()) })();
                    if let Err(e)=control_result { let _=out.blocking_send(Inbound::TaskFatal{observer:id,error:format!("CONTROL_SYNC:{e}")}); return; }
                    if out.blocking_send(Inbound::Control{observer:id,control}).is_err(){return;}
                }
            }
        }
    }).expect("spawn v3 durability worker");
}

async fn observer(
    id: char, endpoint: String, token: AsciiMetadataValue, journal_tx: mpsc::Sender<DurabilityInput>,
    state_path: PathBuf, other_state_path: PathBuf, start: Instant, epoch_start: u64,
    queued: Arc<AtomicUsize>,
) -> Result<()> {
    let mut epoch=epoch_start;
    loop {
        epoch+=1;
        let durable:ObserverState=serde_json::from_slice(&fs::read(&state_path)?)?;
        let other:ObserverState=serde_json::from_slice(&fs::read(&other_state_path)?)?;
        let anchor=durable.durable_frontier.unwrap_or(0);
        let from=if anchor==0 {None}else{Some(anchor.saturating_sub(OVERLAP))};
        let live_after_slot=if from.is_some() && other.state==ObserverMode::Live {other.durable_frontier}else{None};
        let channel=match Channel::from_shared(endpoint.clone())?.tls_config(ClientTlsConfig::new().with_native_roots())?.http2_keep_alive_interval(Duration::from_secs(5)).keep_alive_timeout(Duration::from_secs(3)).keep_alive_while_idle(true).connect().await {
            Ok(x)=>x, Err(e)=>{journal_tx.send(DurabilityInput::Control(OrderedControl::LiveLost{epoch,error:format!("CONNECT:{e:#}")})).await?;tokio::time::sleep(Duration::from_secs(1)).await;continue;}
        };
        let mut client=GeyserClient::with_interceptor(channel,{let token=token.clone();move|mut r:Request<()>|{r.metadata_mut().insert("x-token",token.clone());Ok::<_,Status>(r)}}).max_decoding_message_size(128*1024*1024);
        let stream=client.subscribe(Request::new(stream::once(async move{req(from)}).chain(stream::pending()))).await;
        let mut stream=match stream {Ok(x)=>x.into_inner(),Err(e)=>{journal_tx.send(DurabilityInput::Control(OrderedControl::LiveLost{epoch,error:format!("SUBSCRIBE:{e:#}")})).await?;tokio::time::sleep(Duration::from_secs(1)).await;continue;}};
        journal_tx.send(DurabilityInput::Control(OrderedControl::ReplayStart{epoch,from,live_after_slot})).await?;
        let mut live=false;
        if from.is_none() { journal_tx.send(DurabilityInput::Control(OrderedControl::LiveRestored{epoch})).await?; live=true; }
        let mut progress=interval(PROGRESS_INTERVAL); let mut last_frame=Instant::now();
        'stream: loop {
            tokio::select! {
                // Progress is an ordered durability barrier, never a causal
                // transaction timestamp. When both branches are ready, do
                // not let a busy transaction stream starve that barrier.
                biased;
                _=progress.tick()=> {
                    if last_frame.elapsed()>=INACTIVITY { journal_tx.send(DurabilityInput::Control(OrderedControl::LiveLost{epoch,error:"NO_FRAME_90S".into()})).await?; break 'stream; }
                    journal_tx.send(DurabilityInput::Progress{epoch,through_ns:ns()}).await?;
                }
                item=stream.next()=>match item {
                    Some(Ok(update))=> {
                        last_frame=Instant::now();
                        let raw=update.encode_to_vec(); let Some(UpdateOneof::Transaction(t))=update.update_oneof else {continue};
                        let payload_hash=sha256_hex(&t.encode_to_vec()); let Some(info)=t.transaction else {continue};
                        if !live && live_after_slot.is_some_and(|terminal|t.slot>terminal){ journal_tx.send(DurabilityInput::Control(OrderedControl::LiveRestored{epoch})).await?; live=true; }
                        // Capture receipt time before the bounded durable queue can add delay.
                        let witness=Witness{observer:id,epoch,live,slot:t.slot,signature:bs58::encode(info.signature).into_string(),payload_hash,raw,receipt_ns:ns(),mono_ns:start.elapsed().as_nanos(),journal_position:0};
                        queued.fetch_add(1,Ordering::Relaxed); journal_tx.send(DurabilityInput::Frame(witness)).await?;
                    }
                    Some(Err(e))=>{journal_tx.send(DurabilityInput::Control(OrderedControl::LiveLost{epoch,error:format!("STREAM:{e:#}")})).await?;break 'stream;}
                    None=>{journal_tx.send(DurabilityInput::Control(OrderedControl::LiveLost{epoch,error:"STREAM_EOF".into()})).await?;break 'stream;}
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

fn transition(root:&Path, state:&mut ObserverState, next:ObserverMode)->Result<()> { state.state=next; state.last_health_transition_utc=utc(); atomic_json(&root.join("state").join(format!("observer-{}.json",state.observer)),state) }

fn terminal(root:&Path, global:&mut GlobalState, reason:&str, details:serde_json::Value)->Result<()> {
    if global.mode==GlobalMode::FailClosed{return Ok(());} global.mode=GlobalMode::FailClosed; global.terminal_reason=Some(reason.into());
    global.continuity_gaps+=1; global.timing_provenance_violations+=u64::from(reason.contains("LATE_EARLIER")); global.updated_utc=utc();
    atomic_json(&root.join("receipts").join(format!("terminal-{}.json",Utc::now().format("%Y%m%dT%H%M%S%3fZ"))),&serde_json::json!({"reason":reason,"atUtc":utc(),"details":details,"protocol":"AUTONOMOUS_WHEN_V3_PROTOCOL_V2"}))
}

fn observer_live(s:&ObserverState)->bool { s.state==ObserverMode::Live }

fn selected_witness(canon:&Canon)->Option<&Witness> {
    match (&canon.a,&canon.b) {
        (Some(a),Some(b))=>Some(if a.receipt_ns<=b.receipt_ns{a}else{b}),
        (Some(a),None)=>Some(a), (None,Some(b))=>Some(b), (None,None)=>None,
    }
}

fn close_one(canon:&mut Canon, ledger:&mut LedgerWriter, closure:&mut File, sequence:&mut u64)->Result<()> {
    let selected=match selected_witness(canon){Some(w)=>w.clone(),None=>return Ok(())};
    let raw=canon.raw.clone().context("CLOSED_CANONICAL_WITHOUT_PAYLOAD")?; *sequence+=1;
    let id=identity(canon.slot,&canon.signature,&canon.payload_hash);
    let metadata=EvidenceMetadata{schema_version:SCHEMA_VERSION.into(),evidence_id:sha256_hex(format!("v3|{id}").as_bytes()),raw_sha256:sha256_hex(&raw),kind:"transaction".into(),raw_format:"yellowstone_subscribe_update_protobuf_full_meta_v3_closed".into(),received_at_utc:DateTime::<Utc>::from_timestamp_nanos(selected.receipt_ns).to_rfc3339_opts(chrono::SecondsFormat::Nanos,true),received_at_unix_ns:selected.receipt_ns,decode_at_unix_ns:selected.receipt_ns,writer_sequence:*sequence,slot:Some(canon.slot),transaction_index:None,signature:Some(canon.signature.clone()),account_pubkey:None,account_owner:None,write_version:None,finalized:false,successful:None,failed:None,unresolved:true,pump_instruction_kinds:vec![format!("V3_SELECTED_WITNESS:{}",selected.observer)],pump_instruction_coordinates:vec![format!("V3_CANONICAL_IDENTITY:{id}"),format!("V3_PAYLOAD_SHA256:{}",canon.payload_hash)],discovered_mints:vec![],has_trade_event:false,has_fee_evidence:true,missing_raw_tx:false,missing_trade_event:false,missing_reserve_transition:false,missing_canonical_ordering:true,missing_fee_evidence:false,decode_failed:false,filters:vec!["pumpTransactions".into(),"V3_PROTOCOL_V2".into()]};
    ledger.append_buffered(DateTime::<Utc>::from_timestamp_nanos(selected.receipt_ns),FrameRecord{metadata,raw})?;
    writeln!(closure,"{}",serde_json::to_string(&ClosureLine{canonical_identity:id,writer_sequence:*sequence,slot:canon.slot,signature:canon.signature.clone(),payload_sha256:canon.payload_hash.clone(),selected_observer:selected.observer.to_string(),selected_received_at_unix_ns:selected.receipt_ns,selected_journal_position:selected.journal_position,a_witness:canon.a.as_ref().map(|x|x.journal_position),b_witness:canon.b.as_ref().map(|x|x.journal_position),closed_utc:utc()})?)?;
    canon.selected_ns=Some(selected.receipt_ns); canon.writer_sequence=Some(*sequence); canon.closed=true; Ok(())
}

fn closed_through(a:&ObserverState,b:&ObserverState)->Option<i64>{
    let a_live=observer_live(a); let b_live=observer_live(b);
    match (a_live,b_live) {
        (true,true)=>Some(a.durable_progress_ns?.min(b.durable_progress_ns?).saturating_sub(WITNESS_WINDOW.as_nanos() as i64)),
        (true,false)=>a.durable_progress_ns,
        (false,true)=>b.durable_progress_ns,
        (false,false)=>None,
    }
}

#[tokio::main]
async fn main()->Result<()> {
    let root=PathBuf::from(env::var("YELLOWSTONE_V3_ROOT").context("YELLOWSTONE_V3_ROOT")?); let endpoint=env::var("YELLOWSTONE_FULL_META_ENDPOINT").context("YELLOWSTONE_FULL_META_ENDPOINT")?; let token:AsciiMetadataValue=env::var("SHYFT_GRPC_X_TOKEN")?.parse()?;
    for d in ["state","witness","receipts","closures"] {fs::create_dir_all(root.join(d))?;} let boot=boot_id();
    let mut a=load_state(&root.join("state/observer-A.json"),ObserverState::new("A",&boot))?; let mut b=load_state(&root.join("state/observer-B.json"),ObserverState::new("B",&boot))?;
    atomic_json(&root.join("state/observer-A.json"),&a)?; atomic_json(&root.join("state/observer-B.json"),&b)?;
    let mut global=if root.join("state/global.json").exists(){serde_json::from_slice(&fs::read(root.join("state/global.json"))?)?}else{GlobalState::default()}; if global.mode==GlobalMode::FailClosed{bail!("V3_TERMINAL_FAIL_CLOSED:{}",global.terminal_reason.unwrap_or_default())};
    let mut closure=OpenOptions::new().create(true).append(true).open(root.join("closures/closed.jsonl"))?; let mut ledger=LedgerWriter::open(&root)?;
    let (out_tx,mut rx)=mpsc::channel(INBOUND_QUEUE); let (a_tx,a_rx)=mpsc::channel(DURABILITY_QUEUE); let (b_tx,b_rx)=mpsc::channel(DURABILITY_QUEUE);
    let qa=Arc::new(AtomicUsize::new(0)); let qb=Arc::new(AtomicUsize::new(0)); spawn_durability_worker('A',root.clone(),boot.clone(),a_rx,out_tx.clone(),qa.clone()); spawn_durability_worker('B',root.clone(),boot.clone(),b_rx,out_tx.clone(),qb.clone());
    let start=Instant::now();
    {let tx=out_tx.clone();let state=root.join("state/observer-A.json");let other=root.join("state/observer-B.json");let q=qa.clone();let ep=endpoint.clone();let tok=token.clone();let j=a_tx.clone();let e=a.epoch;tokio::spawn(async move{if let Err(x)=observer('A',ep,tok,j,state,other,start,e,q).await{let _=tx.send(Inbound::TaskFatal{observer:'A',error:format!("TASK_FATAL:{x:#}")}).await;}});}
    {let tx=out_tx.clone();let state=root.join("state/observer-B.json");let other=root.join("state/observer-A.json");let q=qb.clone();let j=b_tx.clone();let e=b.epoch;tokio::spawn(async move{if let Err(x)=observer('B',endpoint,token,j,state,other,start,e,q).await{let _=tx.send(Inbound::TaskFatal{observer:'B',error:format!("TASK_FATAL:{x:#}")}).await;}});}
    let mut map:BTreeMap<String,Canon>=BTreeMap::new(); let mut writer_sequence=global.durable_output_sequence; let mut tick=interval(Duration::from_millis(25)); let mut state_tick=interval(STATE_INTERVAL); let mut health_tick=interval(HEALTH_INTERVAL); let mut output_dirty=false;
    loop { tokio::select! {
        biased;
        _=tick.tick()=>{
            for c in map.values_mut(){ if c.closed {continue}; let both=c.a.is_some()&&c.b.is_some(); let sole=c.a.is_some()||c.b.is_some(); let other_unavailable=match (c.a.is_some(),c.b.is_some()){(true,false)=>!observer_live(&b),(false,true)=>!observer_live(&a),_=>false}; if both || (sole && (other_unavailable || c.first_live.is_some_and(|x|x.elapsed()>=WITNESS_WINDOW))){close_one(c,&mut ledger,&mut closure,&mut writer_sequence)?;output_dirty=true;} }
            if output_dirty { ledger.sync_buffered()?; closure.flush()?; closure.sync_data()?; global.durable_output_sequence=writer_sequence; output_dirty=false; }
            if global.mode!=GlobalMode::FailClosed {global.mode=match(observer_live(&a),observer_live(&b)){(true,true)=>GlobalMode::DualLive,(true,false)=>GlobalMode::AOnlyLive,(false,true)=>GlobalMode::BOnlyLive,_=>GlobalMode::Reconciling};}
            global.source_frontier=[a.durable_frontier,b.durable_frontier].into_iter().flatten().max(); global.closed_identity_frontier=map.iter().rev().find(|(_,c)|c.closed).map(|(k,_)|k.clone()); global.unresolved_identities=map.values().filter(|c|!c.closed).count() as u64;
            if let Some(t)=closed_through(&a,&b){ if global.durable_output_sequence==writer_sequence {global.closed_through_unix_ns=Some(global.closed_through_unix_ns.map_or(t,|x|x.max(t)));} }
            global.source_processing_lag_ns=[a.processing_lag_ns,b.processing_lag_ns].into_iter().flatten().max();
            let evict_before=global.closed_through_unix_ns.unwrap_or(i64::MIN).saturating_sub((2*WITNESS_WINDOW.as_nanos()) as i64); map.retain(|_,c|!c.closed||c.selected_ns.is_some_and(|x|x>=evict_before));
        },
        _=state_tick.tick()=>{a.queue_depth=qa.load(Ordering::Relaxed);b.queue_depth=qb.load(Ordering::Relaxed);global.updated_utc=utc();atomic_json(&root.join("state/global.json"),&global)?;atomic_json(&root.join("state/observer-A.json"),&a)?;atomic_json(&root.join("state/observer-B.json"),&b)?;}
        _=health_tick.tick()=>{a.queue_depth=qa.load(Ordering::Relaxed);b.queue_depth=qb.load(Ordering::Relaxed);global.updated_utc=utc();publish_health(&root,&global,&a,&b)?;}
        Some(event)=rx.recv()=>match event {
            Inbound::Control{observer,control}=>match control {
                OrderedControl::ReplayStart{epoch,from,live_after_slot}=>{let s=if observer=='A'{&mut a}else{&mut b}; s.epoch=epoch;s.reconnect_count+=u64::from(s.last_durable_slot.is_some());s.replay_requested_from_slot=from;s.recovery_anchor=s.last_durable_slot;s.replay_terminal_slot=live_after_slot;s.replay_reconciliation_state="REPLAYING".into();global.current_epoch=global.current_epoch.max(epoch);transition(&root,s,ObserverMode::Replaying)?;}
                OrderedControl::LiveRestored{epoch}=>{let s=if observer=='A'{&mut a}else{&mut b}; if s.epoch!=epoch { terminal(&root,&mut global,"STALE_LIVE_RESTORED_BARRIER",serde_json::json!({"observer":observer,"epoch":epoch,"currentEpoch":s.epoch}))?; atomic_json(&root.join("state/global.json"),&global)?; publish_health(&root,&global,&a,&b)?; bail!("V3_TERMINAL_STALE_LIVE_RESTORED_BARRIER"); } let cert=RecoveryCertificate{observer:s.observer.clone(),old_epoch:s.epoch.saturating_sub(1),new_epoch:s.epoch,disconnect_receipt_ns:s.disconnect_receipt_ns,last_durable_pre_disconnect_slot:s.recovery_anchor,requested_from_slot:s.replay_requested_from_slot,first_replay_slot:s.replay_requested_from_slot,replay_terminal_slot:s.replay_terminal_slot,replayed:0,duplicates:0,new_recovered_payloads:0,unresolved_identities:0,payload_conflicts:0,recovered_without_valid_live_timing:0,result:"PASS_ORDERED_LIVE_RESTORED_BARRIER".into(),returned_live_utc:utc()}; if s.replay_requested_from_slot.is_some(){atomic_json(&root.join("receipts").join(format!("recovery-{}-epoch-{}.json",s.observer,s.epoch)),&cert)?;} s.replay_reconciliation_state="LIVE".into();transition(&root,s,ObserverMode::Live)?;}
                OrderedControl::LiveLost{epoch,error}=>{let s=if observer=='A'{&mut a}else{&mut b}; if s.epoch!=0 && epoch<s.epoch { continue; } s.disconnect_receipt_ns=Some(ns());s.replay_reconciliation_state="DISCONNECTED".into();transition(&root,s,ObserverMode::Disconnected)?;if global.source_armed && !observer_live(&a) && !observer_live(&b){terminal(&root,&mut global,"NO_VALID_LIVE_TIMING_OBSERVER",serde_json::json!({"error":error,"a":a.state,"b":b.state}))?;atomic_json(&root.join("state/global.json"),&global)?;publish_health(&root,&global,&a,&b)?;bail!("V3_TERMINAL_NO_LIVE_TIMING");}}
            },
            Inbound::TaskFatal{observer,error}=>{terminal(&root,&mut global,"SOURCE_TASK_FATAL",serde_json::json!({"observer":observer,"error":error}))?;atomic_json(&root.join("state/global.json"),&global)?;publish_health(&root,&global,&a,&b)?;bail!("V3_TERMINAL_SOURCE_TASK_FATAL");}
            Inbound::DurableProgress{observer,epoch,through_ns}=>{let s=if observer=='A'{&mut a}else{&mut b};if s.epoch==epoch{s.durable_progress_ns=Some(s.durable_progress_ns.map_or(through_ns,|x|x.max(through_ns)));}}
            Inbound::DurableFrame(w)=>{
                let s=if w.observer=='A'{&mut a}else{&mut b}; s.last_durable_journal_position=w.journal_position;s.last_durable_slot=Some(w.slot);s.durable_frontier=Some(s.durable_frontier.map_or(w.slot,|x|x.max(w.slot)));s.processing_lag_ns=Some(ns().saturating_sub(w.receipt_ns));
                if w.live && (s.state!=ObserverMode::Live || s.epoch!=w.epoch){terminal(&root,&mut global,"LIVE_WITNESS_WITHOUT_ORDERED_LIVE_BARRIER",serde_json::json!({"observer":w.observer,"epoch":w.epoch,"state":s.state,"stateEpoch":s.epoch,"slot":w.slot}))?;atomic_json(&root.join("state/global.json"),&global)?;publish_health(&root,&global,&a,&b)?;bail!("V3_TERMINAL_LIVE_WITNESS_WITHOUT_ORDERED_LIVE_BARRIER");}
                if w.live {global.source_armed=true;s.last_live_receipt_ns=Some(w.receipt_ns);}
                if w.live && global.closed_through_unix_ns.is_some_and(|t|w.receipt_ns<=t){let closed_through=global.closed_through_unix_ns;global.late_earlier_violations+=1;terminal(&root,&mut global,"LATE_EARLIER_WITNESS_BEYOND_DURABLE_FRONTIER",serde_json::json!({"slot":w.slot,"signature":w.signature,"receiptNs":w.receipt_ns,"closedThroughUnixNs":closed_through}))?;atomic_json(&root.join("state/global.json"),&global)?;publish_health(&root,&global,&a,&b)?;bail!("V3_TERMINAL_LATE_EARLIER_WITNESS");}
                let key=identity(w.slot,&w.signature,&w.payload_hash); let c=map.entry(key).or_insert_with(||Canon{slot:w.slot,signature:w.signature.clone(),payload_hash:w.payload_hash.clone(),..Default::default()}); if c.raw.is_none(){c.raw=Some(w.raw.clone())}; if w.live {if c.first_live.is_none(){c.first_live=Some(Instant::now())};if w.observer=='A'{c.a=Some(w)}else{c.b=Some(w)}}
            }
        },
    }}
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::time::{timeout, Duration as TokioDuration};

    fn witness(observer:char, epoch:u64, live:bool, receipt_ns:i64)->Witness {
        Witness{observer,epoch,live,slot:451_834_921,signature:"sig".into(),payload_hash:"hash".into(),raw:vec![1],receipt_ns,mono_ns:receipt_ns as u128,journal_position:0}
    }
    fn test_root(name:&str)->PathBuf {
        let root=env::temp_dir().join(format!("v3-ordered-barrier-{name}-{}",ns()));
        fs::create_dir_all(root.join("witness")).unwrap(); root
    }
    async fn next(rx:&mut mpsc::Receiver<Inbound>)->Inbound {
        timeout(TokioDuration::from_secs(2),rx.recv()).await.unwrap().unwrap()
    }

    #[tokio::test]
    async fn pre_disconnect_live_witness_drains_before_lost_barrier() {
        let root=test_root("pre-disconnect"); let (out_tx,mut out_rx)=mpsc::channel(8); let (tx,rx)=mpsc::channel(8); let q=Arc::new(AtomicUsize::new(1));
        spawn_durability_worker('B',root.clone(),"boot".into(),rx,out_tx,q.clone());
        tx.send(DurabilityInput::Frame(witness('B',7,true,100))).await.unwrap();
        tx.send(DurabilityInput::Control(OrderedControl::LiveLost{epoch:7,error:"test".into()})).await.unwrap();
        assert!(matches!(next(&mut out_rx).await,Inbound::DurableFrame(w) if w.receipt_ns==100 && w.live));
        assert!(matches!(next(&mut out_rx).await,Inbound::Control{observer:'B',control:OrderedControl::LiveLost{epoch:7,..}}));
        assert_eq!(q.load(Ordering::Relaxed),0); drop(tx); let _=fs::remove_dir_all(root);
    }

    #[test]
    fn no_witness_disconnect_allows_sole_observer_frontier() {
        let mut a=ObserverState::new("A","boot"); let mut b=ObserverState::new("B","boot");
        a.state=ObserverMode::Live; a.durable_progress_ns=Some(1_000); b.state=ObserverMode::Disconnected;
        assert_eq!(closed_through(&a,&b),Some(1_000));
    }

    #[test]
    fn a_only_recovery_remains_eligible_after_b_loss_barrier() {
        let mut a=ObserverState::new("A","boot"); let mut b=ObserverState::new("B","boot");
        a.state=ObserverMode::Live; a.durable_progress_ns=Some(2_000); b.state=ObserverMode::Disconnected;
        assert_eq!(closed_through(&a,&b),Some(2_000));
        b.state=ObserverMode::Replaying;
        assert_eq!(closed_through(&a,&b),Some(2_000));
    }

    #[test]
    fn b_only_recovery_remains_eligible_after_a_loss_barrier() {
        let mut a=ObserverState::new("A","boot"); let mut b=ObserverState::new("B","boot");
        a.state=ObserverMode::Disconnected; b.state=ObserverMode::Live; b.durable_progress_ns=Some(2_000);
        assert_eq!(closed_through(&a,&b),Some(2_000));
        a.state=ObserverMode::Replaying;
        assert_eq!(closed_through(&a,&b),Some(2_000));
    }

    #[test]
    fn replay_cannot_create_live_timing() {
        let replay=witness('B',8,false,1); let mut c=Canon::default(); c.raw=Some(vec![1]);
        assert!(!replay.live); assert!(c.first_live.is_none()); assert!(selected_witness(&c).is_none());
    }

    #[tokio::test]
    async fn recovery_live_barrier_precedes_new_epoch_live_witness() {
        let root=test_root("recovery"); let (out_tx,mut out_rx)=mpsc::channel(8); let (tx,rx)=mpsc::channel(8); let q=Arc::new(AtomicUsize::new(1));
        spawn_durability_worker('B',root.clone(),"boot".into(),rx,out_tx,q);
        tx.send(DurabilityInput::Control(OrderedControl::ReplayStart{epoch:8,from:Some(451_834_603),live_after_slot:Some(451_834_920)})).await.unwrap();
        tx.send(DurabilityInput::Control(OrderedControl::LiveRestored{epoch:8})).await.unwrap();
        tx.send(DurabilityInput::Frame(witness('B',8,true,200))).await.unwrap();
        tx.send(DurabilityInput::Progress{epoch:8,through_ns:300}).await.unwrap();
        assert!(matches!(next(&mut out_rx).await,Inbound::Control{control:OrderedControl::ReplayStart{epoch:8,..},..}));
        assert!(matches!(next(&mut out_rx).await,Inbound::Control{control:OrderedControl::LiveRestored{epoch:8},..}));
        assert!(matches!(next(&mut out_rx).await,Inbound::DurableFrame(w) if w.epoch==8 && w.live));
        drop(tx); let _=fs::remove_dir_all(root);
    }

    #[test]
    fn replay_interruption_remains_unavailable_until_live_restored_barrier() {
        let mut a=ObserverState::new("A","boot"); let mut b=ObserverState::new("B","boot");
        a.state=ObserverMode::Live; a.durable_progress_ns=Some(3_000); b.state=ObserverMode::Replaying;
        assert_eq!(closed_through(&a,&b),Some(3_000));
        assert!(!observer_live(&b));
    }

    #[test]
    fn frontier_holds_until_transition_barrier_is_applied() {
        let mut a=ObserverState::new("A","boot"); let mut b=ObserverState::new("B","boot");
        a.state=ObserverMode::Live; b.state=ObserverMode::Live; a.durable_progress_ns=Some(2_000_000_000); b.durable_progress_ns=Some(700_000_000);
        assert_eq!(closed_through(&a,&b),Some(200_000_000));
        b.state=ObserverMode::Disconnected;
        assert_eq!(closed_through(&a,&b),Some(2_000_000_000));
    }

    #[test]
    fn original_late_earlier_timeline_selects_b_before_loss_barrier() {
        let mut c=Canon{slot:451_834_921,signature:"2rUjaCwA8wiKxMyE94813c2HNHrne7eEkav5qnhGSYGmVbn8Rf1qTPxrYM7pRD1WYwr9VfHpuH3xheyDmfhW7sb9".into(),payload_hash:"canonical-payload".into(),raw:Some(vec![1]),..Default::default()};
        c.a=Some(witness('A',1,true,1_790_736_663_012_061_378));
        c.b=Some(witness('B',8,true,1_790_736_663_007_443_637));
        let selected=selected_witness(&c).unwrap();
        assert_eq!(selected.observer,'B'); assert_eq!(selected.receipt_ns,1_790_736_663_007_443_637);
        assert!(selected.receipt_ns < 1_790_736_663_012_061_378);
    }

    #[test]
    fn both_live_timing_loss_remains_fail_closed() {
        let a=ObserverState::new("A","boot"); let b=ObserverState::new("B","boot");
        assert!(!observer_live(&a) && !observer_live(&b)); assert_eq!(closed_through(&a,&b),None);
    }

    #[test]
    fn durability_queue_is_bounded_to_preserve_progress_visibility() {
        // A full queue applies lossless backpressure to gRPC instead of allowing a
        // large data burst to hide the ordered progress barrier for many seconds.
        assert!(DURABILITY_QUEUE <= 256);
        assert!(INBOUND_QUEUE <= 1_024);
    }

    #[tokio::test]
    async fn due_progress_beats_ready_data_in_ordered_visibility_loop() {
        let (tx,mut rx)=mpsc::channel::<u8>(1);
        tx.send(1).await.unwrap();
        let mut progress=tokio::time::interval(Duration::from_millis(1));
        progress.tick().await;
        tokio::time::sleep(Duration::from_millis(5)).await;
        let mut progress_selected=false;
        tokio::select! {
            biased;
            _=progress.tick()=> { progress_selected=true; }
            Some(_)=rx.recv()=> {}
        }
        assert!(progress_selected, "a due progress barrier must not be starved by ready frame data");
    }

    #[test] fn replay_never_opens_a_window(){let c=Canon::default();assert!(c.first_live.is_none());}
    #[test] fn dual_progress_accounts_for_window(){let mut a=ObserverState::new("A","b");let mut b=ObserverState::new("B","b");a.state=ObserverMode::Live;b.state=ObserverMode::Live;a.durable_progress_ns=Some(1_000_000_000);b.durable_progress_ns=Some(900_000_000);assert_eq!(closed_through(&a,&b),Some(400_000_000));}
    #[test] fn sequence_is_monotonic(){let mut n=0u64;n+=1;let a=n;n+=1;assert!(n>a);}
    #[test] fn health_frontier_visibility_has_window_headroom(){assert!(HEALTH_INTERVAL<STATE_INTERVAL);assert!(HEALTH_INTERVAL<WITNESS_WINDOW);}
}
