use std::{collections::HashMap, env, fs::OpenOptions, io::Write, time::Duration};

use anyhow::{Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use chrono::Utc;
use futures::{stream, StreamExt};
use prost::Message;
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
const CREATE: [u8; 8] = [24, 30, 200, 40, 5, 28, 7, 119];
const CREATE_V2: [u8; 8] = [214, 144, 76, 236, 95, 139, 49, 180];

#[derive(Serialize)]
struct CapturedFrame {
    schema: &'static str,
    source: &'static str,
    received_at_utc: String,
    received_at_unix_ns: i64,
    signature: String,
    slot: u64,
    observed_transaction_index: u64,
    route_shape: &'static str,
    meta_complete: bool,
    raw_protobuf_base64: String,
}

#[derive(Default, Serialize)]
struct Summary {
    direct_pump: u64,
    router_cpi: u64,
    creates: u64,
    skipped_without_complete_meta: u64,
}

fn request() -> SubscribeRequest {
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
        commitment: Some(CommitmentLevel::Confirmed as i32),
        ping: None,
        from_slot: None,
    }
}

fn contains_key(
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

fn is_pump_create(info: &yellowstone_grpc_proto::prelude::SubscribeUpdateTransactionInfo) -> bool {
    let Some(transaction) = info.transaction.as_ref() else {
        return false;
    };
    let Some(message) = transaction.message.as_ref() else {
        return false;
    };
    let Some(meta) = info.meta.as_ref() else {
        return false;
    };
    let mut keys = message.account_keys.clone();
    keys.extend(meta.loaded_writable_addresses.iter().cloned());
    keys.extend(meta.loaded_readonly_addresses.iter().cloned());
    let matches = |program_index: u32, data: &[u8]| {
        keys.get(program_index as usize)
            .is_some_and(|key| bs58::encode(key).into_string() == PUMP_PROGRAM)
            && data
                .get(..8)
                .is_some_and(|value| value == CREATE || value == CREATE_V2)
    };
    message
        .instructions
        .iter()
        .any(|instruction| matches(instruction.program_id_index, &instruction.data))
        || meta.inner_instructions.iter().any(|group| {
            group
                .instructions
                .iter()
                .any(|instruction| matches(instruction.program_id_index, &instruction.data))
        })
}

#[tokio::main]
async fn main() -> Result<()> {
    let endpoint =
        env::var("YELLOWSTONE_SHADOW_ENDPOINT").context("YELLOWSTONE_SHADOW_ENDPOINT missing")?;
    let token: AsciiMetadataValue = env::var("SHYFT_GRPC_X_TOKEN")?
        .parse()
        .context("invalid x-token")?;
    let output =
        env::var("YELLOWSTONE_SHADOW_OUTPUT").context("YELLOWSTONE_SHADOW_OUTPUT missing")?;
    let direct_target = env::var("YELLOWSTONE_SHADOW_DIRECT_TARGET")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(40_u64);
    let router_target = env::var("YELLOWSTONE_SHADOW_ROUTER_TARGET")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(20_u64);
    let create_target = env::var("YELLOWSTONE_SHADOW_CREATE_TARGET")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(0_u64);
    let duration_seconds = env::var("YELLOWSTONE_SHADOW_DURATION_SECONDS")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(180_u64);
    let channel = Channel::from_shared(endpoint)?
        .tls_config(ClientTlsConfig::new().with_native_roots())?
        .connect()
        .await?;
    let mut client = GeyserClient::with_interceptor(channel, move |mut request: Request<()>| {
        request.metadata_mut().insert("x-token", token.clone());
        Ok::<_, Status>(request)
    })
    .max_decoding_message_size(128 * 1024 * 1024);
    let requests = stream::once(async { request() }).chain(stream::pending());
    let mut updates = client.subscribe(Request::new(requests)).await?.into_inner();
    let mut file = OpenOptions::new().create(true).append(true).open(output)?;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(duration_seconds);
    let mut summary = Summary::default();
    while tokio::time::Instant::now() < deadline
        && (summary.direct_pump < direct_target
            || summary.router_cpi < router_target
            || summary.creates < create_target)
    {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        let Ok(Some(update)) = tokio::time::timeout(remaining, updates.next()).await else {
            break;
        };
        let update = update?;
        let received_at = Utc::now();
        let raw = update.encode_to_vec();
        let Some(UpdateOneof::Transaction(transaction)) = update.update_oneof else {
            continue;
        };
        let Some(info) = transaction.transaction.as_ref() else {
            continue;
        };
        let meta_complete = info.meta.as_ref().is_some_and(|meta| {
            !meta.log_messages.is_empty()
                && !meta.pre_balances.is_empty()
                && !meta.post_balances.is_empty()
        });
        if !meta_complete {
            summary.skipped_without_complete_meta += 1;
            continue;
        }
        let route_shape = if is_pump_create(info) {
            if summary.creates >= create_target {
                continue;
            }
            summary.creates += 1;
            "PUMP_CREATE"
        } else if contains_key(info, PUMP_CPI_ROUTER_PROGRAM) {
            if summary.router_cpi >= router_target {
                continue;
            }
            summary.router_cpi += 1;
            "PUMP_CPI_ROUTER"
        } else if contains_key(info, PUMP_PROGRAM) {
            if summary.direct_pump >= direct_target {
                continue;
            }
            summary.direct_pump += 1;
            "DIRECT_PUMP"
        } else {
            continue;
        };
        let frame = CapturedFrame {
            schema: "YELLOWSTONE_FULL_META_SHADOW_CAPTURE_V1",
            source: "shyft-yellowstone-confirmed-full-meta",
            received_at_utc: received_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
            received_at_unix_ns: received_at.timestamp_nanos_opt().unwrap_or_default(),
            signature: bs58::encode(&info.signature).into_string(),
            slot: transaction.slot,
            observed_transaction_index: info.index,
            route_shape,
            meta_complete,
            raw_protobuf_base64: BASE64_STANDARD.encode(raw),
        };
        writeln!(file, "{}", serde_json::to_string(&frame)?)?;
        file.flush()?;
    }
    println!("{}", serde_json::to_string(&summary)?);
    Ok(())
}
