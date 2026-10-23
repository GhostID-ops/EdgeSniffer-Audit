//! Future-only route-init arrival capture for the 2CQ research lanes.
//!
//! The observer deliberately records a compact, immutable candidate before
//! finalization decides whether a transaction was a route initialization. It
//! never derives lifecycle time from RPC history and never writes outcomes.

use anyhow::{Context, Result};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use solana_entry::entry::Entry as SolanaEntry;
use solana_transaction::versioned::VersionedTransaction;
use std::{
    env,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::time::sleep;
use tonic::transport::Channel;

pub mod shredstream {
    tonic::include_proto!("shredstream");
}

use shredstream::{shredstream_proxy_client::ShredstreamProxyClient, SubscribeEntriesRequest};

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
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(i64::MAX as u128) as i64
}

fn hex_digest(value: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(value);
    format!("{:x}", digest.finalize())
}

fn append_record(root: &Path, received_at_ns: i64, row: &Value) -> Result<()> {
    // Hourly immutable partitions bound file size while preserving exact receipt time.
    let hour = (received_at_ns as i128 / 1_000_000_000i128 / 3600i128).max(0);
    let path = root.join(format!("hour-{hour}.jsonl"));
    fs::create_dir_all(root).context("create route-init arrival root")?;
    let mut output = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .with_context(|| format!("open {}", path.display()))?;
    serde_json::to_writer(&mut output, row).context("serialize route-init arrival")?;
    output.write_all(b"\n").context("append route-init arrival newline")?;
    output.flush().context("flush route-init arrival")?;
    Ok(())
}

fn write_health(path: &Path, value: &Value) -> Result<()> {
    let parent = path.parent().context("route-init health parent")?;
    fs::create_dir_all(parent).context("create route-init health parent")?;
    let temporary = path.with_extension("json.tmp");
    let mut output = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&temporary)
        .with_context(|| format!("open {}", temporary.display()))?;
    serde_json::to_writer_pretty(&mut output, value).context("serialize route-init health")?;
    output.write_all(b"\n").context("write route-init health newline")?;
    output.flush().context("flush route-init health")?;
    fs::rename(temporary, path).context("publish route-init health")?;
    Ok(())
}

fn route_program_references(tx: &VersionedTransaction) -> Vec<&'static str> {
    let keys = tx
        .message
        .static_account_keys()
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    let mut routes = Vec::new();
    let launchlab_init = tx.message.instructions().iter().any(|instruction| {
        keys.get(instruction.program_id_index as usize).map(|program| program == LAUNCHLAB_PROGRAM_ID).unwrap_or(false)
            && (instruction.data.starts_with(&LAUNCHLAB_INITIALIZE)
                || instruction.data.starts_with(&LAUNCHLAB_INITIALIZE_V2)
                || instruction.data.starts_with(&LAUNCHLAB_INITIALIZE_TOKEN_2022))
    });
    if launchlab_init {
        routes.push("LAUNCHLAB");
    }
    let migration = tx.message.instructions().iter().any(|instruction| {
        keys.get(instruction.program_id_index as usize).map(|program| program == PUMP_PROGRAM_ID).unwrap_or(false)
            && (instruction.data.starts_with(&PUMP_MIGRATE) || instruction.data.starts_with(&PUMP_MIGRATE_V2))
    });
    if keys.iter().any(|key| key == PUMPSWAP_PROGRAM_ID) && migration {
        routes.push("PUMPSWAP");
    }
    routes
}

fn compact_instruction_evidence(tx: &VersionedTransaction) -> Vec<Value> {
    let keys = tx
        .message
        .static_account_keys()
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    let mut compact = tx.message
        .instructions()
        .iter()
        .enumerate()
        .filter_map(|(index, instruction)| {
            let program_id = keys.get(instruction.program_id_index as usize)?;
            if program_id != LAUNCHLAB_PROGRAM_ID && program_id != PUMPSWAP_PROGRAM_ID && program_id != PUMP_PROGRAM_ID {
                return None;
            }
            Some(json!({
                "instructionIndex": index,
                "programId": program_id,
                "dataSha256": hex_digest(&instruction.data),
                "dataLength": instruction.data.len(),
                "accountCount": instruction.accounts.len(),
                "launchLabInitializeHint": program_id == LAUNCHLAB_PROGRAM_ID && (
                    instruction.data.starts_with(&LAUNCHLAB_INITIALIZE) ||
                    instruction.data.starts_with(&LAUNCHLAB_INITIALIZE_V2) ||
                    instruction.data.starts_with(&LAUNCHLAB_INITIALIZE_TOKEN_2022)
                ),
                "pumpSwapCreatePoolHint": program_id == PUMPSWAP_PROGRAM_ID && instruction.data.starts_with(&PUMPSWAP_CREATE_POOL),
                "pumpMigrationHint": program_id == PUMP_PROGRAM_ID && (
                    instruction.data.starts_with(&PUMP_MIGRATE) || instruction.data.starts_with(&PUMP_MIGRATE_V2)
                ),
            }))
        })
        .collect::<Vec<_>>();
    if keys.iter().any(|key| key == PUMPSWAP_PROGRAM_ID) && tx.message.instructions().iter().any(|instruction| {
        keys.get(instruction.program_id_index as usize).map(|program| program == PUMP_PROGRAM_ID).unwrap_or(false)
            && (instruction.data.starts_with(&PUMP_MIGRATE) || instruction.data.starts_with(&PUMP_MIGRATE_V2))
    }) {
        compact.push(json!({"pumpSwapMigrationCandidateHint": true, "programId": PUMP_PROGRAM_ID}));
    }
    compact
}

async fn observe_once(url: &str, root: &Path, health_path: &Path, connection_epoch: u64) -> Result<()> {
    let channel = Channel::from_shared(url.to_string())?.connect().await?;
    let mut client = ShredstreamProxyClient::new(channel);
    let mut stream = client
        .subscribe_entries(SubscribeEntriesRequest {})
        .await?
        .into_inner();
    eprintln!("route-init observer: connected epoch={connection_epoch} url={url}");
    write_health(health_path, &json!({
        "schema": "2CQ_ROUTE_INIT_ARRIVAL_OBSERVER_HEALTH_V1",
        "sourceProvenance": "ALLENHARK_SUBSCRIBE_ENTRIES_V1",
        "connectionEpoch": connection_epoch,
        "observedSlots": 0,
        "observedTransactions": 0,
        "candidateRecords": 0,
        "lastObservedSlot": null,
        "lastReceiptUnixNs": null,
        "active": true,
        "state": "CONNECTED_AWAITING_ENTRIES",
    }))?;

    let mut observed_slots = 0u64;
    let mut observed_transactions = 0u64;
    let mut candidate_records = 0u64;
    while let Some(slot_entry) = stream.message().await? {
        let entries = match bincode::deserialize::<Vec<SolanaEntry>>(&slot_entry.entries) {
            Ok(entries) => entries,
            Err(error) => {
                eprintln!("route-init observer: slot {} deserialize error: {error}", slot_entry.slot);
                continue;
            }
        };
        let mut tx_index = 0u64;
        for entry in entries {
            for transaction in entry.transactions {
                let routes = route_program_references(&transaction);
                if !routes.is_empty() {
                    let received_at_ns = now_unix_ns();
                    let signature = transaction
                        .signatures
                        .first()
                        .map(ToString::to_string)
                        .unwrap_or_default();
                    if !signature.is_empty() {
                        let instructions = compact_instruction_evidence(&transaction);
                        let evidence = json!({
                            "schema": SCHEMA,
                            "signature": signature,
                            "receivedAtUnixNs": received_at_ns,
                            "slot": slot_entry.slot,
                            "observedSlotTransactionOrdinal": tx_index,
                            "connectionEpoch": connection_epoch,
                            "sourceProvenance": "ALLENHARK_SUBSCRIBE_ENTRIES_STATIC_PROGRAM_REFERENCE_V1",
                            "routeProgramReferences": routes,
                            "directInstructionEvidence": instructions,
                        });
                        let immutable_hash = hex_digest(
                            serde_json::to_string(&evidence)
                                .context("canonicalize route-init arrival")?
                                .as_bytes(),
                        );
                        let mut row = evidence;
                        row["arrivalEvidenceHash"] = Value::String(immutable_hash);
                        append_record(root, received_at_ns, &row)?;
                        candidate_records = candidate_records.saturating_add(1);
                    }
                }
                tx_index = tx_index.saturating_add(1);
                observed_transactions = observed_transactions.saturating_add(1);
            }
        }
        observed_slots = observed_slots.saturating_add(1);
        write_health(health_path, &json!({
            "schema": "2CQ_ROUTE_INIT_ARRIVAL_OBSERVER_HEALTH_V1",
            "sourceProvenance": "ALLENHARK_SUBSCRIBE_ENTRIES_V1",
            "connectionEpoch": connection_epoch,
            "observedSlots": observed_slots,
            "observedTransactions": observed_transactions,
            "candidateRecords": candidate_records,
            "lastObservedSlot": slot_entry.slot,
            "lastReceiptUnixNs": now_unix_ns(),
            "active": true,
        }))?;
    }
    Ok(())
}

#[tokio::main(flavor = "multi_thread")]
async fn main() -> Result<()> {
    let url = env::var("ALLENHARK_SHREDSTREAM_GRPC_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:9999".to_string());
    let root = PathBuf::from(env::var("ROUTE_INIT_ARRIVAL_ROOT").context("ROUTE_INIT_ARRIVAL_ROOT")?);
    let health_path = PathBuf::from(env::var("ROUTE_INIT_ARRIVAL_HEALTH").unwrap_or_else(|_| {
        root.join("observer-health.json").display().to_string()
    }));
    let reconnect_ms = env::var("ROUTE_INIT_RECONNECT_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(500);
    let mut epoch = 0u64;
    loop {
        epoch = epoch.saturating_add(1);
        if let Err(error) = observe_once(&url, &root, &health_path, epoch).await {
            eprintln!("route-init observer: epoch={epoch} ended: {error:#}");
            sleep(Duration::from_millis(reconnect_ms)).await;
        }
    }
}
