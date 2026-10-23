//! Receive-only Pump transaction evidence. No executor, keys, signing or RPC.
//! Reuses the existing full-meta recorder's Yellowstone/prost transport pattern.
use std::{collections::{HashMap, BTreeSet}, env, fs::OpenOptions, io::{BufWriter, Write}, time::{Duration, Instant, SystemTime, UNIX_EPOCH}};
use anyhow::Result;
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use futures::{stream, StreamExt};
use prost::Message;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tonic::{metadata::AsciiMetadataValue, transport::{Channel, ClientTlsConfig}, Request, Status};
use yellowstone_grpc_proto::{geyser::{geyser_client::GeyserClient, subscribe_update::UpdateOneof,
    SubscribeUpdate, SubscribeRequest, SubscribeRequestFilterSlots, SubscribeRequestFilterTransactions, SubscribeRequestFilterAccounts, SubscribeRequestPing, CommitmentLevel},
    solana::storage::confirmed_block as pb};

const PUMP: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
fn ns() -> u64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos() as u64 }
fn sha(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }
fn key(bytes: &[u8]) -> String { bs58::encode(bytes).into_string() }

fn request() -> SubscribeRequest {
    SubscribeRequest {
        transactions: HashMap::from([("pump_only_all_outcomes_v1".into(), SubscribeRequestFilterTransactions {
            vote: Some(false), failed: None, signature: None, account_include: vec![PUMP.into()],
            account_exclude: vec![], account_required: vec![], token_accounts: None,
        })]),
        slots: HashMap::from([("slot_lifecycle".into(), SubscribeRequestFilterSlots {
            filter_by_commitment: Some(false), interslot_updates: Some(true),
        })]), commitment: Some(CommitmentLevel::Processed as i32), ..Default::default()
    }
}

// Optional research-only context. Exact mint scope grows from native Pump tx
// metadata, never from teacher matching or later returns. No HTTP here.
fn context_request(mints: &BTreeSet<String>, fee: &str) -> SubscribeRequest {
    let mut r=request();
    r.accounts.insert("pump_owned_raw_context".into(),SubscribeRequestFilterAccounts {
        owner:vec![PUMP.into()],..Default::default()});
    let mut exact:Vec<String>=mints.iter().cloned().collect();
    exact.extend([fee.to_string(),"SysvarC1ock11111111111111111111111111111111".into()]);
    r.accounts.insert("pump_mints_fee_clock".into(),SubscribeRequestFilterAccounts {
        account:exact,..Default::default()});
    r
}

fn discovered_mints(tx:&Value) -> BTreeSet<String> {
    let mut result=BTreeSet::new();
    for field in ["preTokenBalances","postTokenBalances"] {
        if let Some(rows)=tx["meta"][field].as_array() {
            for row in rows {
                if let Some(m)=row["mint"].as_str() {
                    if bs58::decode(m).into_vec().map(|v|v.len()==32).unwrap_or(false){result.insert(m.into());}
                }
            }
        }
    }
    result
}

fn account_view(update:&SubscribeUpdate) -> Option<Value> {
    let UpdateOneof::Account(a)=update.update_oneof.as_ref()? else{return None;};
    let info=a.account.as_ref()?;
    Some(json!({"pubkey":key(&info.pubkey),"owner":key(&info.owner),"slot":a.slot,
        "is_startup":a.is_startup,"lamports":info.lamports,"executable":info.executable,
        "rent_epoch":info.rent_epoch,"write_version":info.write_version,
        "transaction_signature":info.txn_signature.as_ref().map(|v|key(v)),
        "data_base64":B64.encode(&info.data),"data_sha256":sha(&info.data)}))
}

fn token_balances(rows: &[pb::TokenBalance]) -> Value {
    json!(rows.iter().map(|r| json!({"accountIndex":r.account_index,"mint":r.mint,"owner":r.owner,
        "programId":r.program_id,"uiTokenAmount":r.ui_token_amount.as_ref().map(|a|json!({
            "amount":a.amount,"decimals":a.decimals,"uiAmountString":a.ui_amount_string}))})).collect::<Vec<_>>())
}

fn normalize(update: &SubscribeUpdate) -> Option<Value> {
    let UpdateOneof::Transaction(tx) = update.update_oneof.as_ref()? else { return None; };
    let info = tx.transaction.as_ref()?;
    let body = info.transaction.as_ref();
    let message = body.and_then(|t| t.message.as_ref());
    let meta = info.meta.as_ref().map(|m| json!({
        "err":m.err.as_ref().map(|e|json!({"protobuf_error_base64":B64.encode(&e.err)})),
        "fee":m.fee,"preBalances":m.pre_balances,"postBalances":m.post_balances,
        "preTokenBalances":token_balances(&m.pre_token_balances),"postTokenBalances":token_balances(&m.post_token_balances),
        "loadedAddresses":{"writable":m.loaded_writable_addresses.iter().map(|k|key(k)).collect::<Vec<_>>(),
                           "readonly":m.loaded_readonly_addresses.iter().map(|k|key(k)).collect::<Vec<_>>()},
        "innerInstructions":if m.inner_instructions_none { Value::Null } else {json!(m.inner_instructions.iter().map(|g|json!({
            "index":g.index,"instructions":g.instructions.iter().map(|i|json!({"programIdIndex":i.program_id_index,
                "accounts":i.accounts,"data":key(&i.data),"stackHeight":i.stack_height})).collect::<Vec<_>>()
        })).collect::<Vec<_>>())},
        "logMessages":if m.log_messages_none {Value::Null} else {json!(m.log_messages)},
        "computeUnitsConsumed":m.compute_units_consumed,"costUnits":m.cost_units,
        "returnData":m.return_data.as_ref().map(|d|json!({"programId":key(&d.program_id),"data":[B64.encode(&d.data),"base64"]}))
    }));
    Some(json!({"signature":key(&info.signature),"slot":tx.slot,"transactionIndex":info.index,
        "blockTime":Value::Null,"meta":meta,
        "transaction":body.map(|b|json!({"signatures":b.signatures.iter().map(|s|key(s)).collect::<Vec<_>>(),
            "message":message.map(|m|json!({"accountKeys":m.account_keys.iter().map(|k|key(k)).collect::<Vec<_>>(),
                "versioned":m.versioned,"recentBlockhash":key(&m.recent_blockhash),
                "header":m.header.as_ref().map(|h|json!({"numRequiredSignatures":h.num_required_signatures,
                    "numReadonlySignedAccounts":h.num_readonly_signed_accounts,"numReadonlyUnsignedAccounts":h.num_readonly_unsigned_accounts})),
                "addressTableLookups":m.address_table_lookups.iter().map(|a|json!({"accountKey":key(&a.account_key),
                    "writableIndexes":a.writable_indexes,"readonlyIndexes":a.readonly_indexes})).collect::<Vec<_>>(),
                "instructions":m.instructions.iter().map(|i|json!({"programIdIndex":i.program_id_index,
                    "accounts":i.accounts,"data":key(&i.data)})).collect::<Vec<_>>()
            }))}))}))
}

fn write(writer: &mut impl Write, row: &Value) -> Result<()> {
    serde_json::to_writer(&mut *writer, row)?; writer.write_all(b"\n")?; writer.flush()?; Ok(())
}

async fn capture() -> Result<()> {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let path = env::var("PUMP_EVIDENCE_OUTPUT")?;
    let seconds = env::var("PUMP_EVIDENCE_SECONDS").unwrap_or("120".into()).parse::<u64>()?.clamp(1, 3600);
    let endpoint = env::var("SHYFT_GRPC_URL")?;
    let token: AsciiMetadataValue = env::var("SHYFT_GRPC_X_TOKEN")?.parse()?;
    let fee=env::var("PUMP_CONTEXT_FEE_ACCOUNT").ok();
    if let Some(f)=&fee {anyhow::ensure!(bs58::decode(f).into_vec()?.len()==32,"invalid fee pubkey");}
    let mut mints=BTreeSet::new();let mut last_subscription=Instant::now();
    let file = OpenOptions::new().write(true).create_new(true).open(path)?;
    let mut writer = BufWriter::new(file);
    let start = Instant::now(); let session = format!("{}-{}", ns(), std::process::id());
    let mut filter_hash = sha(&fee.as_ref().map(|f|context_request(&mints,f)).unwrap_or_else(request).encode_to_vec());
    write(&mut writer, &json!({"kind":"SESSION_START","schema":"PUMP_NATIVE_FULL_META_V1","session_id":session,
        "available_at_ns":ns(),"subscription_sha256":filter_hash,"commitment":"processed","program":PUMP,
        "failed_filter":Value::Null,"teacher_filter":false,"raw_protobuf_retained":true,
        "raw_account_context":fee.is_some(),"fee_account_requested":fee,"dynamic_mint_limit":4096,
        "mint_discovery":"All pre/post token-balance mints in Pump-scope transactions, regardless of outcomes; not asserted trade mint",
        "clock_semantics":"received_at is application dequeue after transport decode, not NIC arrival; normalized_at is after view decode",
        "chain_execution_time":Value::Null,"capture_duration_seconds":seconds}))?;
    let mut connection = 0_u64; let mut sequence = 0_u64; let mut max_slot = 0_u64;
    while start.elapsed() < Duration::from_secs(seconds) {
        connection += 1;
        write(&mut writer,&json!({"kind":"CONNECT_ATTEMPT","connection_id":connection,"available_at_ns":ns(),"session_id":session}))?;
        let mut channel = Channel::from_shared(endpoint.clone())?.connect_timeout(Duration::from_secs(10));
        if endpoint.starts_with("https:") {channel=channel.tls_config(ClientTlsConfig::new().with_native_roots())?;}
        let channel = match channel.connect().await { Ok(c)=>c, Err(_)=>{
            write(&mut writer,&json!({"kind":"GAP","reason":"connect_error","connection_id":connection,"available_at_ns":ns()}))?;
            tokio::time::sleep(Duration::from_secs(1)).await; continue;
        }};
        let auth=token.clone();
        let mut client=GeyserClient::with_interceptor(channel,move |mut r:Request<()>|{
            r.metadata_mut().insert("x-token",auth.clone()); Ok::<_,Status>(r)
        }).max_decoding_message_size(128*1024*1024);
        let (sender, receiver)=tokio::sync::mpsc::channel(4);
        let subscription=fee.as_ref().map(|f|context_request(&mints,f)).unwrap_or_else(request);
        filter_hash=sha(&subscription.encode_to_vec());
        sender.send(subscription).await?;let mut subscribed_mints=mints.len();
        let requests=stream::unfold(receiver,|mut r|async move {r.recv().await.map(|item|(item,r))});
        let response=tokio::time::timeout(Duration::from_secs(10),client.subscribe(Request::new(requests))).await;
        let mut updates=match response {Ok(Ok(r))=>r.into_inner(),_=>{
            write(&mut writer,&json!({"kind":"GAP","reason":"subscribe_error","connection_id":connection,"available_at_ns":ns()}))?;
            tokio::time::sleep(Duration::from_secs(1)).await;continue;
        }};
        write(&mut writer,&json!({"kind":"CONNECTED","connection_id":connection,"available_at_ns":ns()}))?;
        loop {
            let remaining=Duration::from_secs(seconds).saturating_sub(start.elapsed());
            if remaining.is_zero(){break;}
            let wait=remaining.min(Duration::from_secs(20));
            let update=match tokio::time::timeout(wait,updates.next()).await {
                Ok(Some(Ok(u)))=>u,
                Err(_) if start.elapsed() >= Duration::from_secs(seconds)=>break,
                other=>{
                    let reason=if other.is_err() {"idle_timeout_or_duration_boundary"} else {"stream_error_or_eof"};
                    write(&mut writer,&json!({"kind":"GAP","reason":reason,"connection_id":connection,
                        "last_seen_slot":max_slot,"available_at_ns":ns()}))?;break;
                }
            };
            let received=ns(); let mono=start.elapsed().as_nanos() as u64; sequence+=1;
            if matches!(update.update_oneof.as_ref(),Some(UpdateOneof::Ping(_))){
                sender.send(SubscribeRequest{ping:Some(SubscribeRequestPing{id:1}),..Default::default()}).await?;
            }
            let raw=update.encode_to_vec();
            let tx=normalize(&update);
            let account=account_view(&update);
            if fee.is_some() {
                if let Some(t)=&tx {
                    for mint in discovered_mints(t) {
                        if !mints.contains(&mint) {
                            if mints.len()>=4096 {
                                write(&mut writer,&json!({"kind":"CONTEXT_SCOPE_LIMIT","mint":mint,"available_at_ns":ns()}))?;
                                continue;
                            }
                            mints.insert(mint.clone());
                            write(&mut writer,&json!({"kind":"MINT_DISCOVERED","mint":mint,"available_at_ns":ns(),
                                "source_signature":t["signature"],"session_id":session,"connection_id":connection}))?;
                        }
                    }
                }
            }
            let slot=match update.update_oneof.as_ref(){
                Some(UpdateOneof::Transaction(t))=>Some(t.slot),Some(UpdateOneof::Slot(s))=>Some(s.slot),Some(UpdateOneof::Account(a))=>Some(a.slot),_=>None};
            if let Some(s)=slot {max_slot=max_slot.max(s);}
            let kind=match update.update_oneof.as_ref(){Some(UpdateOneof::Transaction(_))=>"TRANSACTION",
                Some(UpdateOneof::Slot(_))=>"SLOT",Some(UpdateOneof::Account(_))=>"ACCOUNT",Some(UpdateOneof::Ping(_))=>"PING",_=>"OTHER"};
            let slot_state=if let Some(UpdateOneof::Slot(s))=update.update_oneof.as_ref(){Some(json!({
                "slot":s.slot,"parent":s.parent,"status":s.status,"dead_error":s.dead_error}))}else{None};
            write(&mut writer,&json!({"schema":"PUMP_NATIVE_FULL_META_V1","kind":kind,
                "session_id":session,"connection_id":connection,"sequence":sequence,"subscription_sha256":filter_hash,
                "received_at_unix_ns":received,"received_at_monotonic_ns":mono,"normalized_at_unix_ns":ns(),
                "provider_created_at":update.created_at.as_ref().map(|t|json!({"seconds":t.seconds,"nanos":t.nanos})),
                "chain_execution_timestamp":Value::Null,"commitment":"processed","finalized_at_ns":Value::Null,
                "slot":slot,"slot_state":slot_state,"raw_sha256":sha(&raw),"raw_protobuf_base64":B64.encode(raw),
                "account":account,"transaction":tx}))?;
            if let Some(f)=&fee {
                if mints.len()!=subscribed_mints && last_subscription.elapsed()>=Duration::from_millis(250) {
                    let subscription=context_request(&mints,f);filter_hash=sha(&subscription.encode_to_vec());
                    sender.send(subscription).await?;subscribed_mints=mints.len();last_subscription=Instant::now();
                    write(&mut writer,&json!({"kind":"CONTEXT_SUBSCRIPTION_REQUESTED","available_at_ns":ns(),
                        "session_id":session,"connection_id":connection,"mint_count":mints.len(),
                        "subscription_sha256":filter_hash,"server_activation_acknowledged":false}))?;
                }
            }
        }
    }
    write(&mut writer,&json!({"kind":"SESSION_END","session_id":session,"available_at_ns":ns(),
        "frames":sequence,"connections":connection,"last_seen_slot":max_slot,"complete_market_coverage":false}))?;
    writer.get_ref().sync_all()?;
    Ok(())
}

#[tokio::main]
async fn main() { if capture().await.is_err(){eprintln!("capture failed; inspect evidence stream (details redacted)");std::process::exit(1);} }

#[cfg(test)] mod tests {
    use super::*;
    use yellowstone_grpc_proto::geyser::{SubscribeUpdateTransaction,SubscribeUpdateTransactionInfo};
    #[test] fn tls_provider_is_explicit(){let _=rustls::crypto::ring::default_provider().install_default();
        assert!(rustls::crypto::CryptoProvider::get_default().is_some());}
    #[test] fn scope_has_no_teacher_success_or_mint_filter(){
        let r=request();let f=r.transactions.values().next().unwrap();
        assert_eq!(f.account_include,vec![PUMP.to_string()]);assert_eq!(f.failed,None);
        assert!(f.account_required.is_empty());assert_eq!(r.commitment,Some(CommitmentLevel::Processed as i32));
    }
    #[test] fn context_is_pump_plus_exact_mints_and_fee_not_universal_token_scope(){
        let m=BTreeSet::from([key(&[9;32])]);let r=context_request(&m,&key(&[8;32]));
        assert_eq!(r.accounts.len(),2);assert_eq!(r.accounts["pump_owned_raw_context"].owner,vec![PUMP.to_string()]);
        let f=&r.accounts["pump_mints_fee_clock"];assert!(f.owner.is_empty());assert_eq!(f.account.len(),3);
        assert_eq!(r.transactions,request().transactions);
    }
    #[test] fn mint_discovery_includes_failed_metadata_and_deduplicates(){
        let m=key(&[9;32]);let t=json!({"meta":{"err":{"failed":true},"preTokenBalances":[{"mint":m}],"postTokenBalances":[{"mint":m},{"mint":"bad"}]}});
        assert_eq!(discovered_mints(&t),BTreeSet::from([m]));
    }
    #[test] fn raw_account_identity_and_bytes_are_preserved(){
        use yellowstone_grpc_proto::geyser::{SubscribeUpdateAccount,SubscribeUpdateAccountInfo};
        let u=SubscribeUpdate{update_oneof:Some(UpdateOneof::Account(SubscribeUpdateAccount{slot:17,is_startup:false,
            account:Some(SubscribeUpdateAccountInfo{pubkey:vec![1;32],owner:vec![2;32],data:vec![3,4],
                write_version:7,txn_signature:Some(vec![5;64]),..Default::default()})})),..Default::default()};
        let a=account_view(&u).unwrap();assert_eq!(a["write_version"],7);assert_eq!(a["data_base64"],B64.encode([3,4]));
        assert_eq!(a["transaction_signature"],key(&[5;64]));assert_eq!(a["owner"],key(&[2;32]));
    }
    fn update(meta:Option<pb::TransactionStatusMeta>)->SubscribeUpdate {
        SubscribeUpdate{update_oneof:Some(UpdateOneof::Transaction(SubscribeUpdateTransaction{slot:10,
            transaction:Some(SubscribeUpdateTransactionInfo{signature:vec![1;64],index:7,meta,
                transaction:Some(pb::Transaction{signatures:vec![vec![1;64]],message:Some(pb::Message{
                    account_keys:vec![vec![2;32]],address_table_lookups:vec![pb::MessageAddressTableLookup{
                        account_key:vec![3;32],writable_indexes:vec![1],readonly_indexes:vec![2]}],..Default::default()})}),
                ..Default::default()})})),..Default::default()}
    }
    #[test] fn unknown_meta_is_not_success(){assert!(normalize(&update(None)).unwrap()["meta"].is_null());}
    #[test] fn failure_and_cpi_alt_context_survive(){
        let u=update(Some(pb::TransactionStatusMeta{err:Some(pb::TransactionError{err:vec![9]}),fee:5000,
            loaded_writable_addresses:vec![vec![4;32]],loaded_readonly_addresses:vec![vec![5;32]],
            inner_instructions:vec![pb::InnerInstructions{index:0,instructions:vec![pb::InnerInstruction{
                program_id_index:2,accounts:vec![0,1],data:vec![7],stack_height:Some(2)}]}],..Default::default()}));
        let v=normalize(&u).unwrap();assert!(!v["meta"]["err"].is_null());assert_eq!(v["meta"]["fee"],5000);
        assert_eq!(v["meta"]["innerInstructions"][0]["instructions"][0]["stackHeight"],2);
        assert_eq!(v["meta"]["loadedAddresses"]["writable"][0],key(&[4;32]));
        let bytes=u.encode_to_vec();assert_eq!(SubscribeUpdate::decode(bytes.as_slice()).unwrap(),u);
    }
    #[test] fn unrecorded_inner_and_logs_remain_null(){let v=normalize(&update(Some(pb::TransactionStatusMeta{
        inner_instructions_none:true,log_messages_none:true,..Default::default()}))).unwrap();
        assert!(v["meta"]["innerInstructions"].is_null());assert!(v["meta"]["logMessages"].is_null());}
}
