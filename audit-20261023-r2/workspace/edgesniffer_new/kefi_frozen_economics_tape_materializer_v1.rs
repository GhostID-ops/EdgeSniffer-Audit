//! Offline, fail-closed economics tape materializer for the sealed kEfi
//! selection source. It writes compact validated transition rows only; raw
//! Yellowstone frames remain solely in the sealed canonical source journal.

use anyhow::{bail, Context, Result};
use pumpfun_sidecar::{
    kefi_canonical_source_v1::{load_checkpoint, persist_checkpoint, read_next_batch_cursor},
    kefi_pumpswap_semantics_v1::{
        cache_identity, decode_exact_transitions, pool_is_explicitly_unusable,
        resolve_pool_identity_batch, PoolIdentity,
    },
    kefi_pump_curve_semantics_v1::decode_exact_pump_transitions,
};
use pump_exact_evidence_ledger::SourceReplayCursor;
use serde::{Deserialize, Serialize};
use solana_pubkey::Pubkey;
use std::{collections::{BTreeMap, BTreeSet}, env, fs, path::{Path, PathBuf}};
use yellowstone_grpc_proto::geyser::subscribe_update::UpdateOneof;

const WSOL: &str = "So11111111111111111111111111111111111111112";
const MAX_INPUTS: usize = 65_536;

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Binding { schema: String, canonical_root: String, source_instance_id: String, continuity_epoch: u64 }

#[derive(Serialize)]
struct Row {
    schema: &'static str,
    source_instance_id: String,
    continuity_epoch: u64,
    durable_output_sequence: u64,
    received_at_unix_ns: u128,
    source_frame_sha256: String,
    source_transaction_index: u64,
    event_index: usize,
    intrinsic_event_id: String,
    signature: String,
    slot: u64,
    pool: String,
    mint: String,
    quote_mint: String,
    side: String,
    amount_mode: String,
    instruction_coordinate: String,
    instruction_amount_raw: u64,
    instruction_limit_raw: u64,
    base_amount: u64,
    pool_base_raw: u64,
    pool_quote_raw: u64,
    pool_quote_delta: u64,
    curve_quote_raw: u64,
    user_quote_amount_raw: u64,
    lp_fee_bps: u64,
    lp_fee_raw: u64,
    protocol_fee_bps: u64,
    protocol_fee_raw: u64,
    coin_creator_bps: u64,
    coin_creator_fee_raw: u64,
    cashback_bps: u64,
    cashback_raw: u64,
    buyback_bps: u64,
    buyback_fee_raw: u64,
    virtual_quote_reserves_raw: i128,
    exact_transition_validated: bool,
    disposition: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Quarantine { schema: &'static str, durable_output_sequence: u64, source_frame_sha256: String, signature: String, slot: u64, reason: String }

fn required(name: &str) -> Result<String> { env::var(name).with_context(|| format!("{name} required")) }
fn atomic(path: &Path, bytes: &[u8]) -> Result<()> { let tmp=path.with_extension("tmp"); fs::write(&tmp, bytes)?; fs::rename(tmp,path)?; Ok(()) }
fn bind(root: &Path, value: &Binding) -> Result<()> {
    let path=root.join("run-binding.json"); fs::create_dir_all(root)?;
    if path.exists() { let old: Binding=serde_json::from_slice(&fs::read(&path)?)?;
        if old.canonical_root != value.canonical_root || old.source_instance_id != value.source_instance_id || old.continuity_epoch != value.continuity_epoch { bail!("ECONOMICS_TAPE_BINDING_MISMATCH") }
    } else { atomic(&path, &serde_json::to_vec(value)?)?; }
    Ok(())
}

fn load_cursor(path:&Path, source:&str, epoch:u64, after:u64)->Result<SourceReplayCursor>{
    if !path.exists(){return Ok(SourceReplayCursor::at_sequence(source,epoch,after))}
    let c:SourceReplayCursor=serde_json::from_slice(&fs::read(path)?)?;
    if c.source_instance_id!=source || c.continuity_epoch!=epoch || c.next_writer_sequence!=after { return Ok(SourceReplayCursor::at_sequence(source,epoch,after)) }
    Ok(c)
}
fn write_cursor(path:&Path,cursor:&SourceReplayCursor)->Result<()> { atomic(path,&serde_json::to_vec(cursor)?) }
fn load_cache(path:&Path)->Result<BTreeMap<String,PoolIdentity>>{if path.exists(){Ok(serde_json::from_slice(&fs::read(path)?)?)}else{Ok(BTreeMap::new())}}

fn validates(row:&Row)->bool {
    let fee_base=if row.side=="BUY" && row.amount_mode=="EXACT_QUOTE_IN" {row.user_quote_amount_raw} else {row.curve_quote_raw};
    let ceil=|bps:u64| fee_base.saturating_mul(bps).saturating_add(9_999)/10_000;
    let tolerance=if row.side=="BUY" && row.amount_mode=="EXACT_QUOTE_IN" {1}else{0};
    row.lp_fee_raw.abs_diff(ceil(row.lp_fee_bps))<=tolerance && row.protocol_fee_raw.abs_diff(ceil(row.protocol_fee_bps))<=tolerance
}

fn pump_curve_for_mint(mint: &str) -> Result<String> {
    let mint: Pubkey = mint.parse().context("PUMP_MINT_PUBKEY")?;
    let program: Pubkey = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P".parse().expect("valid Pump program id");
    Ok(Pubkey::find_program_address(&[b"bonding-curve", mint.as_ref()], &program).0.to_string())
}

#[tokio::main]
async fn main()->Result<()> {
    let canonical_root=PathBuf::from(required("KEFI_ECONOMICS_CANONICAL_ROOT")?);
    let source=required("KEFI_ECONOMICS_SOURCE_INSTANCE_ID")?;
    let epoch:u64=required("KEFI_ECONOMICS_CONTINUITY_EPOCH")?.parse()?;
    let rpc=required("KEFI_ECONOMICS_POOL_IDENTITY_RPC_URL")?;
    let root=PathBuf::from(required("KEFI_ECONOMICS_OUTPUT_ROOT")?);
    let limit:usize=env::var("KEFI_ECONOMICS_MAX_INPUTS").unwrap_or_else(|_|"65536".into()).parse()?;
    if limit==0 || limit>MAX_INPUTS {bail!("KEFI_ECONOMICS_MAX_INPUTS_INVALID")}
    bind(&root,&Binding{schema:"KEFI_FROZEN_ECONOMICS_TAPE_BINDING_V1".into(),canonical_root:canonical_root.display().to_string(),source_instance_id:source.clone(),continuity_epoch:epoch})?;
    fs::create_dir_all(root.join("chunks"))?; fs::create_dir_all(root.join("quarantine"))?;
    let checkpoint_path=root.join("consumer-checkpoint.json"); let after=load_checkpoint(&checkpoint_path,&source,epoch)?;
    let cursor_path=root.join("replay-cursor.json"); let cursor=load_cursor(&cursor_path,&source,epoch,after)?;
    let spool=root.join("replay-spool-v1"); let (inputs,next_cursor)=read_next_batch_cursor(&canonical_root,&spool,&source,epoch,cursor,limit)?;
    if inputs.is_empty(){println!("{{\"status\":\"SOURCE_TAIL\"}}");return Ok(())}
    let client=reqwest::Client::builder().connect_timeout(std::time::Duration::from_secs(10)).timeout(std::time::Duration::from_secs(30)).build()?;
    let cache_path=root.join("pool-identity-cache.json"); let mut cache=load_cache(&cache_path)?;
    let mut pools=BTreeSet::new();
    for input in &inputs { if let Some(UpdateOneof::Transaction(tx))=input.update.update_oneof.as_ref() { if let Some(info)=tx.transaction.as_ref(){ if let Ok(transitions)=decode_exact_transitions(info){ for item in transitions {pools.insert(item.event.pool);} } } } }
    let unresolved=pools.into_iter().filter(|pool|!cache.contains_key(pool)).collect::<Vec<_>>();
    for group in unresolved.chunks(100){for (pool,result) in resolve_pool_identity_batch(&client,&rpc,group).await? {match result {Ok(identity)=>{cache_identity(&mut cache,identity)?;},Err(error) if pool_is_explicitly_unusable(&error)=>{},Err(error)=>bail!("AUTHORITATIVE_POOL_IDENTITY_UNAVAILABLE:{pool}:{error}")}}}
    let mut rows=Vec::new();let mut quarantined=Vec::new(); let mut last=None;
    for input in &inputs { last=Some(input); if let Some(UpdateOneof::Transaction(tx))=input.update.update_oneof.as_ref(){let Some(info)=tx.transaction.as_ref() else {continue};let signature=bs58::encode(&info.signature).into_string();match decode_exact_transitions(info){Ok(transitions)=>for (event_index,item) in transitions.into_iter().enumerate(){let Some(identity)=cache.get(&item.event.pool) else {quarantined.push(Quarantine{schema:"KEFI_FROZEN_ECONOMICS_TAPE_QUARANTINE_V1",durable_output_sequence:input.durable_output_sequence,source_frame_sha256:input.source_frame_sha256.clone(),signature:signature.clone(),slot:tx.slot,reason:"POOL_IDENTITY_UNSUPPORTED_OR_UNAVAILABLE".into()});continue};if identity.quote_mint!=WSOL {quarantined.push(Quarantine{schema:"KEFI_FROZEN_ECONOMICS_TAPE_QUARANTINE_V1",durable_output_sequence:input.durable_output_sequence,source_frame_sha256:input.source_frame_sha256.clone(),signature:signature.clone(),slot:tx.slot,reason:"UNSUPPORTED_NON_WSOL_OR_REVERSED_POOL_LAYOUT".into()});continue};let e=item.event;let mut row=Row{schema:"KEFI_FROZEN_ECONOMICS_TAPE_ROW_V1",source_instance_id:input.source_instance_id.clone(),continuity_epoch:input.continuity_epoch,durable_output_sequence:input.durable_output_sequence,received_at_unix_ns:input.received_at_unix_ns,source_frame_sha256:input.source_frame_sha256.clone(),source_transaction_index:info.index as u64,event_index,intrinsic_event_id:format!("{}:{}:{}",signature,item.log_index,item.instruction_coordinate),signature:signature.clone(),slot:tx.slot,pool:e.pool,mint:identity.selection_mint.clone(),quote_mint:identity.quote_mint.clone(),side:e.side.into(),amount_mode:item.amount_mode.into(),instruction_coordinate:item.instruction_coordinate,instruction_amount_raw:item.instruction_amount_raw,instruction_limit_raw:item.instruction_limit_raw,base_amount:e.base_amount,pool_base_raw:e.pool_base,pool_quote_raw:e.pool_quote,pool_quote_delta:e.pool_quote_delta,curve_quote_raw:e.curve_quote_raw,user_quote_amount_raw:e.user_quote_amount_raw,lp_fee_bps:e.lp_fee_bps,lp_fee_raw:e.lp_fee_raw,protocol_fee_bps:e.protocol_fee_bps,protocol_fee_raw:e.protocol_fee_raw,coin_creator_bps:e.coin_creator_bps,coin_creator_fee_raw:e.coin_creator_fee_raw,cashback_bps:e.cashback_bps,cashback_raw:e.cashback_raw,buyback_bps:e.buyback_bps,buyback_fee_raw:e.buyback_fee_raw,virtual_quote_reserves_raw:e.virtual_quote_reserves_raw,exact_transition_validated:false,disposition:"EXACT_TRANSITION_VALIDATED"};row.exact_transition_validated=validates(&row);if row.exact_transition_validated{rows.push(row)}else{quarantined.push(Quarantine{schema:"KEFI_FROZEN_ECONOMICS_TAPE_QUARANTINE_V1",durable_output_sequence:input.durable_output_sequence,source_frame_sha256:input.source_frame_sha256.clone(),signature:signature.clone(),slot:tx.slot,reason:"PUMPSWAP_FEE_ARITHMETIC_MISMATCH".into()})}},Err(error)=>quarantined.push(Quarantine{schema:"KEFI_FROZEN_ECONOMICS_TAPE_QUARANTINE_V1",durable_output_sequence:input.durable_output_sequence,source_frame_sha256:input.source_frame_sha256.clone(),signature,slot:tx.slot,reason:error.to_string()})}}}
    let first=inputs.first().unwrap().durable_output_sequence;let through=inputs.last().unwrap().durable_output_sequence;
    let rows_bytes=rows.iter().map(|row|serde_json::to_string(row).unwrap()+"\n").collect::<String>().into_bytes();let q_bytes=quarantined.iter().map(|row|serde_json::to_string(row).unwrap()+"\n").collect::<String>().into_bytes();
    atomic(&root.join("chunks").join(format!("{first:020}-{through:020}.jsonl")),&rows_bytes)?;atomic(&root.join("quarantine").join(format!("{first:020}-{through:020}.jsonl")),&q_bytes)?;
    atomic(&cache_path,&serde_json::to_vec(&cache)?)?;persist_checkpoint(&checkpoint_path,last.context("NO_LAST_INPUT")?)?;write_cursor(&cursor_path,&next_cursor)?;
    println!("{{\"status\":\"BATCH_COMMITTED\",\"from\":{first},\"through\":{through},\"rows\":{},\"quarantined\":{}}}",rows.len(),quarantined.len());Ok(())
}
