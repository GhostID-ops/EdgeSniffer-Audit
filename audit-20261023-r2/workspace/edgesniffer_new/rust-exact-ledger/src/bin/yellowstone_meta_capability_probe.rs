use std::{collections::HashMap, env, time::Duration};

use anyhow::{Context, Result};
use futures::{stream, StreamExt};
use serde::Serialize;
use tonic::{
    metadata::AsciiMetadataValue,
    transport::{Channel, ClientTlsConfig},
    Request, Status,
};
use yellowstone_grpc_proto::geyser::{
    geyser_client::GeyserClient, subscribe_update::UpdateOneof, CommitmentLevel, SubscribeRequest,
    SubscribeRequestFilterTransactions,
};

const PUMP_PROGRAM: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const PUMP_CPI_ROUTER_PROGRAM: &str = "6Vo3245eszAb5wuqEMw8mGdbfRUdKbHhDHP5LcaGuTAB";

#[derive(Default, Serialize)]
struct Counts {
    transactions: u64,
    meta_present: u64,
    log_messages_present: u64,
    inner_instructions_present: u64,
    loaded_addresses_present: u64,
    balances_present: u64,
    successful_or_error_present: u64,
}

#[derive(Serialize)]
struct Report {
    schema: &'static str,
    endpoint_label: String,
    commitment: String,
    duration_seconds: u64,
    total: Counts,
    direct_pump: Counts,
    router_cpi: Counts,
}

fn request(commitment: CommitmentLevel) -> SubscribeRequest {
    let mut transactions = HashMap::new();
    transactions.insert(
        "pumpTransactions".to_string(),
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: None,
            signature: None,
            account_include: vec![
                PUMP_PROGRAM.to_string(),
                PUMP_CPI_ROUTER_PROGRAM.to_string(),
            ],
            account_exclude: vec![],
            account_required: vec![],
            token_accounts: None,
        },
    );
    SubscribeRequest {
        accounts: HashMap::new(),
        slots: HashMap::new(),
        transactions,
        transactions_status: HashMap::new(),
        blocks: HashMap::new(),
        blocks_meta: HashMap::new(),
        entry: HashMap::new(),
        accounts_data_slice: vec![],
        commitment: Some(commitment as i32),
        ping: None,
        from_slot: None,
    }
}

fn count(
    target: &mut Counts,
    info: &yellowstone_grpc_proto::prelude::SubscribeUpdateTransactionInfo,
) {
    target.transactions += 1;
    let Some(meta) = info.meta.as_ref() else {
        return;
    };
    target.meta_present += 1;
    target.log_messages_present += u64::from(!meta.log_messages.is_empty());
    target.inner_instructions_present += u64::from(!meta.inner_instructions.is_empty());
    target.loaded_addresses_present += u64::from(
        !meta.loaded_writable_addresses.is_empty() || !meta.loaded_readonly_addresses.is_empty(),
    );
    target.balances_present +=
        u64::from(!meta.pre_balances.is_empty() && !meta.post_balances.is_empty());
    target.successful_or_error_present += 1;
}

fn has_key(
    info: &yellowstone_grpc_proto::prelude::SubscribeUpdateTransactionInfo,
    key: &str,
) -> bool {
    info.transaction
        .as_ref()
        .and_then(|transaction| transaction.message.as_ref())
        .is_some_and(|message| {
            message
                .account_keys
                .iter()
                .any(|candidate| bs58::encode(candidate).into_string() == key)
        })
}

#[tokio::main]
async fn main() -> Result<()> {
    let endpoint =
        env::var("YELLOWSTONE_PROBE_ENDPOINT").context("YELLOWSTONE_PROBE_ENDPOINT missing")?;
    let token: AsciiMetadataValue = env::var("SHYFT_GRPC_X_TOKEN")?
        .parse()
        .context("invalid x-token")?;
    let duration_seconds = env::var("YELLOWSTONE_PROBE_DURATION_SECONDS")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(60);
    let commitment_name =
        env::var("YELLOWSTONE_PROBE_COMMITMENT").unwrap_or_else(|_| "confirmed".to_string());
    let commitment = match commitment_name.as_str() {
        "finalized" => CommitmentLevel::Finalized,
        "processed" => CommitmentLevel::Processed,
        _ => CommitmentLevel::Confirmed,
    };
    let endpoint_label = endpoint
        .trim_start_matches("https://")
        .trim_start_matches("http://")
        .trim_end_matches('/')
        .to_string();
    let channel = Channel::from_shared(endpoint)?
        .tls_config(ClientTlsConfig::new().with_native_roots())?
        .connect()
        .await?;
    let mut client = GeyserClient::with_interceptor(channel, move |mut request: Request<()>| {
        request.metadata_mut().insert("x-token", token.clone());
        Ok::<_, Status>(request)
    })
    .max_decoding_message_size(128 * 1024 * 1024);
    let requests = stream::once(async move { request(commitment) }).chain(stream::pending());
    let mut updates = client.subscribe(Request::new(requests)).await?.into_inner();
    let deadline = tokio::time::Instant::now() + Duration::from_secs(duration_seconds);
    let mut report = Report {
        schema: "YELLOWSTONE_META_CAPABILITY_PROBE_V1",
        endpoint_label,
        commitment: commitment_name,
        duration_seconds,
        total: Counts::default(),
        direct_pump: Counts::default(),
        router_cpi: Counts::default(),
    };
    while tokio::time::Instant::now() < deadline {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        let Ok(Some(update)) = tokio::time::timeout(remaining, updates.next()).await else {
            break;
        };
        let update = update?;
        let Some(UpdateOneof::Transaction(transaction)) = update.update_oneof else {
            continue;
        };
        let Some(info) = transaction.transaction.as_ref() else {
            continue;
        };
        count(&mut report.total, info);
        if has_key(info, PUMP_CPI_ROUTER_PROGRAM) {
            count(&mut report.router_cpi, info);
        } else if has_key(info, PUMP_PROGRAM) {
            count(&mut report.direct_pump, info);
        }
    }
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}
