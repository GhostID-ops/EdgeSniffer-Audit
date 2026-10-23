//! Direct Yellowstone fallback for the future-only route-init registry.
//!
//! This collector is deliberately separate from AllenHark. Rows retain their
//! own source clock; finalization prefers an AllenHark row for the identical
//! signature when both sources are present.

use anyhow::{Context, Result};
use futures::{stream, StreamExt};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    env,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::time::sleep;
use yellowstone_grpc_proto::geyser::{
    geyser_client::GeyserClient, subscribe_update::UpdateOneof, CommitmentLevel, SubscribeRequest,
    SubscribeRequestFilterTransactions,
};
use yellowstone_grpc_proto::tonic::{
    metadata::AsciiMetadataValue, transport::{Channel, ClientTlsConfig}, Request, Status,
};

const LAUNCHLAB_PROGRAM_ID: &str = "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj";
const PUMPSWAP_PROGRAM_ID: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const PUMP_PROGRAM_ID: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const SCHEMA: &str = "2CQ_ROUTE_INIT_ARRIVAL_CANDIDATE_V1";
const LAUNCHLAB_INITIALIZE: [u8; 8] = [175, 175, 109, 31, 13, 152, 155, 237];
const LAUNCHLAB_INITIALIZE_V2: [u8; 8] = [67, 153, 175, 39, 218, 16, 38, 32];
const LAUNCHLAB_INITIALIZE_TOKEN_2022: [u8; 8] = [37, 190, 126, 222, 44, 154, 171, 17];
const PUMPSWAP_CREATE_POOL: [u8; 8] = [233, 146, 209, 142, 207, 104, 64, 188];
const PUMP_MIGRATE: [u8; 8] = [155, 234, 231, 146, 236, 158, 162, 30];
const PUMP_MIGRATE_V2: [u8; 8] = [187, 203, 18, 31, 206, 237, 254, 41];

fn now_unix_ns() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos().min(i64::MAX as u128) as i64
}

fn hash(value: &[u8]) -> String {
    let mut digest = Sha256::new(); digest.update(value); format!("{:x}", digest.finalize())
}

fn append(root: &Path, received_ns: i64, row: &Value) -> Result<()> {
    let hour = (received_ns as i128 / 1_000_000_000i128 / 3600i128).max(0);
    fs::create_dir_all(root)?;
    let mut file = OpenOptions::new().create(true).append(true).open(root.join(format!("hour-{hour}.jsonl")))?;
    serde_json::to_writer(&mut file, row)?;
    file.write_all(b"\n")?;
    file.flush()?;
    Ok(())
}

fn health(path: &Path, value: &Value) -> Result<()> {
    let parent = path.parent().context("yellowstone health parent")?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_extension("json.tmp");
    let mut file = OpenOptions::new().create(true).truncate(true).write(true).open(&temporary)?;
    serde_json::to_writer_pretty(&mut file, value)?;
    file.write_all(b"\n")?; file.flush()?; fs::rename(temporary, path)?; Ok(())
}

fn request() -> SubscribeRequest {
    let filter = |program: &str| SubscribeRequestFilterTransactions {
        vote: Some(false), failed: Some(false), signature: None,
        account_include: vec![], account_exclude: vec![], account_required: vec![program.to_string()], token_accounts: None,
    };
    let pump_swap_migration_filter = SubscribeRequestFilterTransactions {
        vote: Some(false), failed: Some(false), signature: None,
        // Yellowstone's account_required is an AND constraint.  Canonical
        // Pump graduations have both programs in one transaction: the Pump
        // migrate outer instruction and the PumpSwap create_pool CPI.  This
        // avoids persisting every ordinary PumpSwap swap in an init registry.
        account_include: vec![], account_exclude: vec![],
        account_required: vec![PUMP_PROGRAM_ID.to_string(), PUMPSWAP_PROGRAM_ID.to_string()],
        token_accounts: None,
    };
    let mut transactions = HashMap::new();
    transactions.insert("launchlabRouteCandidates".to_string(), filter(LAUNCHLAB_PROGRAM_ID));
    transactions.insert("pumpSwapCanonicalMigrationCandidates".to_string(), pump_swap_migration_filter);
    SubscribeRequest {
        accounts: HashMap::new(), slots: HashMap::new(), transactions, transactions_status: HashMap::new(),
        blocks: HashMap::new(), blocks_meta: HashMap::new(), entry: HashMap::new(), accounts_data_slice: vec![],
        commitment: Some(CommitmentLevel::Processed as i32), ping: None, from_slot: None,
    }
}

async fn observe_once(endpoint: &str, token: AsciiMetadataValue, root: &Path, health_path: &Path, epoch: u64) -> Result<()> {
    let channel = Channel::from_shared(endpoint.to_string())?.tls_config(ClientTlsConfig::new().with_native_roots())?.connect().await?;
    let mut client = GeyserClient::with_interceptor(channel, move |mut req: Request<()>| {
        req.metadata_mut().insert("x-token", token.clone()); Ok::<_, Status>(req)
    }).max_decoding_message_size(64 * 1024 * 1024);
    let requests = stream::once(async { request() }).chain(stream::pending());
    let mut updates = client.subscribe(Request::new(requests)).await?.into_inner();
    health(health_path, &json!({"schema":"2CQ_YELLOWSTONE_ROUTE_INIT_OBSERVER_HEALTH_V1","active":true,"state":"CONNECTED_AWAITING_TRANSACTIONS","connectionEpoch":epoch,"observedTransactions":0,"candidateRecords":0}))?;
    let mut observed = 0u64; let mut candidates = 0u64;
    while let Some(update) = updates.next().await {
        let update = update?;
        let Some(UpdateOneof::Transaction(tx_update)) = update.update_oneof else { continue; };
        let Some(info) = tx_update.transaction else { continue; };
        let signature = bs58::encode(&info.signature).into_string();
        if signature.is_empty() { continue; }
        let Some(message) = info.transaction.as_ref().and_then(|transaction| transaction.message.as_ref()) else { continue; };
        let keys = message.account_keys.iter().map(|key| bs58::encode(key).into_string()).collect::<Vec<_>>();
        let mut refs = Vec::new();
        let launchlab_init_hint = message.instructions.iter().any(|instruction| {
            keys.get(instruction.program_id_index as usize).map(|program| program == LAUNCHLAB_PROGRAM_ID).unwrap_or(false)
                && (instruction.data.starts_with(&LAUNCHLAB_INITIALIZE)
                    || instruction.data.starts_with(&LAUNCHLAB_INITIALIZE_V2)
                    || instruction.data.starts_with(&LAUNCHLAB_INITIALIZE_TOKEN_2022))
        });
        if launchlab_init_hint { refs.push("LAUNCHLAB"); }
        // The combined account filter is only a coarse prefilter. The
        // published outer Pump migrate/migrate_v2 discriminators distinguish
        // actual pool migration from other two-program transactions.
        let pump_swap_migration_hint = keys.iter().any(|key| key == PUMPSWAP_PROGRAM_ID)
            && message.instructions.iter().any(|instruction| {
                keys.get(instruction.program_id_index as usize).map(|program| program == PUMP_PROGRAM_ID).unwrap_or(false)
                    && (instruction.data.starts_with(&PUMP_MIGRATE) || instruction.data.starts_with(&PUMP_MIGRATE_V2))
            });
        if pump_swap_migration_hint { refs.push("PUMPSWAP"); }
        if refs.is_empty() { continue; }
        let received_ns = now_unix_ns();
        let direct = message.instructions.iter().enumerate().filter_map(|(index, instruction)| {
            let program = keys.get(instruction.program_id_index as usize)?;
            if program != LAUNCHLAB_PROGRAM_ID && program != PUMPSWAP_PROGRAM_ID && program != PUMP_PROGRAM_ID { return None; }
            Some(json!({
                "instructionIndex":index,"programId":program,"dataSha256":hash(&instruction.data),
                "dataLength":instruction.data.len(),"accountCount":instruction.accounts.len(),
                "launchLabInitializeHint": program == LAUNCHLAB_PROGRAM_ID && (
                    instruction.data.starts_with(&LAUNCHLAB_INITIALIZE) ||
                    instruction.data.starts_with(&LAUNCHLAB_INITIALIZE_V2) ||
                    instruction.data.starts_with(&LAUNCHLAB_INITIALIZE_TOKEN_2022)
                ),
                "pumpSwapCreatePoolHint": program == PUMPSWAP_PROGRAM_ID && instruction.data.starts_with(&PUMPSWAP_CREATE_POOL),
                "pumpMigrationHint": program == PUMP_PROGRAM_ID && (
                    instruction.data.starts_with(&PUMP_MIGRATE) || instruction.data.starts_with(&PUMP_MIGRATE_V2)
                )
            }))
        }).collect::<Vec<_>>();
        let mut direct = direct;
        if pump_swap_migration_hint {
            direct.push(json!({"pumpSwapMigrationCandidateHint":true, "programId":PUMP_PROGRAM_ID}));
        }
        let evidence = json!({
            "schema":SCHEMA, "signature":signature, "receivedAtUnixNs":received_ns,
            "slot":tx_update.slot, "observedSlotTransactionOrdinal":info.index,
            "connectionEpoch":epoch, "sourceProvenance":"YELLOWSTONE_DIRECT_PROGRAM_FILTER_V1",
            "routeProgramReferences":refs, "directInstructionEvidence":direct,
        });
        let mut row = evidence; row["arrivalEvidenceHash"] = Value::String(hash(serde_json::to_string(&row)?.as_bytes()));
        append(root, received_ns, &row)?;
        observed = observed.saturating_add(1); candidates = candidates.saturating_add(1);
        health(health_path, &json!({"schema":"2CQ_YELLOWSTONE_ROUTE_INIT_OBSERVER_HEALTH_V1","active":true,"state":"ADVANCING","connectionEpoch":epoch,"observedTransactions":observed,"candidateRecords":candidates,"lastSlot":tx_update.slot,"lastReceiptUnixNs":received_ns}))?;
    }
    Ok(())
}

#[tokio::main(flavor = "multi_thread")]
async fn main() -> Result<()> {
    // This standalone binary links both the direct and transitive Rustls
    // providers. Select ring explicitly before creating the TLS channel.
    let _ = rustls::crypto::ring::default_provider().install_default();
    let endpoint = env::var("RUST_SIDECAR_GRPC_URL").or_else(|_| env::var("SHYFT_GRPC_URL")).context("RUST_SIDECAR_GRPC_URL/SHYFT_GRPC_URL")?;
    let token: AsciiMetadataValue = env::var("SHYFT_GRPC_X_TOKEN").context("SHYFT_GRPC_X_TOKEN")?.parse().context("invalid SHYFT_GRPC_X_TOKEN")?;
    let root = PathBuf::from(env::var("ROUTE_INIT_ARRIVAL_ROOT").context("ROUTE_INIT_ARRIVAL_ROOT")?);
    let health_path = PathBuf::from(env::var("YELLOWSTONE_ROUTE_INIT_ARRIVAL_HEALTH").unwrap_or_else(|_| root.join("yellowstone-observer-health.json").display().to_string()));
    let mut epoch = 0u64;
    loop { epoch = epoch.saturating_add(1); if let Err(error) = observe_once(&endpoint, token.clone(), &root, &health_path, epoch).await { eprintln!("yellowstone route-init observer epoch={epoch}: {error:#}"); sleep(Duration::from_millis(500)).await; } }
}
