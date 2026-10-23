use std::{
    collections::{HashMap, HashSet},
    env, fs,
    fs::{File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc,
    },
    time::Duration,
};

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use chrono::{DateTime, Utc};
use fs2::FileExt;
use futures::{stream, StreamExt};
use prost::Message;
use pump_exact_evidence_ledger::{
    file_sha256, sha256_hex, CoverageCounters, EvidenceMetadata, FrameRecord, LedgerWriter,
    PartitionManifest, SCHEMA_DOCUMENT, SCHEMA_VERSION,
};
use serde::Serialize;
use serde_json::Value;
use sha2::Digest;
use tokio::sync::{mpsc, Mutex, OnceCell};
use tonic::{
    metadata::AsciiMetadataValue,
    transport::{Channel, ClientTlsConfig},
    Request, Status,
};
use yellowstone_grpc_proto::geyser::{
    geyser_client::GeyserClient, subscribe_update::UpdateOneof, CommitmentLevel, SubscribeRequest,
    SubscribeRequestFilterAccounts, SubscribeRequestFilterBlocksMeta, SubscribeRequestFilterSlots,
    SubscribeRequestFilterTransactions, SubscribeUpdate,
};

const PUMP_PROGRAM: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const PUMP_FEE_PROGRAM: &str = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ";
const PUMP_MAYHEM_PROGRAM: &str = "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e";
// CPI-only Pump CREATE_V2 transactions through this router do not satisfy the
// static Pump-program transaction filter. This additive filter preserves the
// existing Pump stream while making their receipt provenance observable.
const PUMP_CPI_ROUTER_PROGRAM: &str = "6Vo3245eszAb5wuqEMw8mGdbfRUdKbHhDHP5LcaGuTAB";
const BUY: [u8; 8] = [102, 6, 61, 18, 1, 218, 235, 234];
const BUY_EXACT_SOL_IN: [u8; 8] = [56, 252, 116, 8, 158, 223, 205, 95];
const BUY_V2: [u8; 8] = [184, 23, 238, 97, 103, 197, 211, 61];
const BUY_EXACT_QUOTE_IN_V2: [u8; 8] = [194, 171, 28, 70, 104, 77, 91, 47];
const SELL: [u8; 8] = [51, 230, 133, 164, 1, 127, 131, 173];
const SELL_V2: [u8; 8] = [93, 246, 130, 60, 231, 233, 64, 178];
const CREATE: [u8; 8] = [24, 30, 200, 40, 5, 28, 7, 119];
const CREATE_V2: [u8; 8] = [214, 144, 76, 236, 95, 139, 49, 180];
const TRADE_EVENT: [u8; 8] = [189, 219, 127, 211, 78, 230, 97, 238];
const TWO_CQ_WALLET: &str = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG";
const NATIVE_MINT: &str = "So11111111111111111111111111111111111111112";

#[derive(Clone)]
struct Config {
    root: PathBuf,
    endpoint: String,
    token: String,
    rpc_url: Option<String>,
    queue_capacity: usize,
    reconnect_ms: u64,
    stream_stale_ms: u64,
    health_stale_ms: u64,
    storage_device: PathBuf,
    two_cq_detector_path: Option<PathBuf>,
}

#[derive(Debug, PartialEq, Eq)]
enum CliCommand {
    Run,
    Verify(PathBuf),
    SchemaHash,
    Help,
}

#[derive(Clone)]
struct RuntimeIdentity {
    pid: u32,
    process_instance_uuid: String,
    startup_time_utc: String,
    endpoint_identity: String,
}

impl Config {
    fn from_env() -> Result<Self> {
        Ok(Self {
            root: PathBuf::from(
                env::var("PUMP_EXACT_LEDGER_ROOT")
                    .unwrap_or_else(|_| "/var/lib/pump-exact-evidence-ledger-v1".to_string()),
            ),
            endpoint: env::var("PUMP_EXACT_LEDGER_GRPC_URL")
                .or_else(|_| env::var("SHYFT_GRPC_URL"))
                .context("PUMP_EXACT_LEDGER_GRPC_URL/SHYFT_GRPC_URL missing")?,
            token: env::var("SHYFT_GRPC_X_TOKEN").context("SHYFT_GRPC_X_TOKEN missing")?,
            rpc_url: env::var("PUMP_EXACT_LEDGER_RPC_URL")
                .or_else(|_| env::var("SECURE_RPC_URL"))
                .ok()
                .filter(|value| !value.trim().is_empty()),
            queue_capacity: env::var("PUMP_EXACT_LEDGER_QUEUE_CAPACITY")
                .ok()
                .and_then(|value| value.parse().ok())
                .unwrap_or(8192),
            reconnect_ms: env::var("PUMP_EXACT_LEDGER_RECONNECT_MS")
                .ok()
                .and_then(|value| value.parse().ok())
                .unwrap_or(1000),
            stream_stale_ms: env::var("PUMP_EXACT_LEDGER_STREAM_STALE_MS")
                .ok()
                .and_then(|value| value.parse().ok())
                .unwrap_or(30_000),
            health_stale_ms: env::var("PUMP_EXACT_LEDGER_HEALTH_STALE_MS")
                .ok()
                .and_then(|value| value.parse().ok())
                .unwrap_or(90_000),
            storage_device: PathBuf::from(
                env::var("PUMP_EXACT_LEDGER_STORAGE_PATH").unwrap_or_else(|_| "/".to_string()),
            ),
            two_cq_detector_path: env::var("PUMP_EXACT_LEDGER_2CQ_DETECTOR_PATH")
                .ok()
                .filter(|value| !value.trim().is_empty())
                .map(PathBuf::from),
        })
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct TwoCqDetectedSwap {
    kind: &'static str,
    source: &'static str,
    detected_at_ms: i64,
    signature: String,
    wallet: &'static str,
    input_mint: &'static str,
    output_mint: String,
    amount_raw: Option<u64>,
    is_native_input: bool,
    counterparty_amount_raw: Option<u64>,
    source_ix_accounts: Vec<String>,
    source_ix_data_base64: String,
    source_pump_swap_instruction: Option<String>,
    source_pump_swap_accounts: Option<Vec<String>>,
    source_quote_mint: &'static str,
    source_quote_amount_raw: Option<u64>,
    source_slot: u64,
    source_transaction_index: u64,
}

#[derive(Default)]
struct RuntimeStats {
    received: AtomicU64,
    queued: AtomicU64,
    written: AtomicU64,
    duplicates: AtomicU64,
    reconnects: AtomicU64,
    queue_high_water: AtomicU64,
    rpc_snapshot_requests: AtomicU64,
    rpc_snapshot_failures: AtomicU64,
    rpc_finality_requests: AtomicU64,
    rpc_finality_failures: AtomicU64,
    rpc_block_requests: AtomicU64,
    rpc_block_failures: AtomicU64,
    last_raw_stream_update_unix_ms: AtomicU64,
    last_raw_stream_slot: AtomicU64,
    last_finalized_reconciliation_unix_ms: AtomicU64,
    last_partition_append_unix_ms: AtomicU64,
    reconciliation_backlog: AtomicU64,
    stream_connected: AtomicBool,
}

struct ReconciliationBacklogGuard(Arc<RuntimeStats>);

impl Drop for ReconciliationBacklogGuard {
    fn drop(&mut self) {
        let _ = self.0.reconciliation_backlog.fetch_update(
            Ordering::Relaxed,
            Ordering::Relaxed,
            |value| Some(value.saturating_sub(1)),
        );
    }
}

struct IngestItem {
    received_at: DateTime<Utc>,
    payload: IngestPayload,
}

enum IngestPayload {
    Yellowstone(Box<SubscribeUpdate>),
    RpcMintSnapshot(RpcMintSnapshot),
    RpcFinalizedTransaction(RpcFinalizedTransaction),
    RpcFinalizedBlock(RpcFinalizedBlock),
    Shutdown,
}

struct RpcMintSnapshot {
    mint: String,
    min_context_slot: u64,
    raw_json: Vec<u8>,
    context_slot: Option<u64>,
    owner: Option<String>,
    successful: bool,
}

struct RpcFinalizedTransaction {
    signature: String,
    source_slot: u64,
    canonical_transaction_index: Option<u64>,
    raw_json: Vec<u8>,
    finalized_slot: Option<u64>,
    successful: Option<bool>,
}

struct RpcFinalizedBlock {
    slot: u64,
    raw_json: Vec<u8>,
    successful: bool,
}

struct BlockIndex {
    signatures: Vec<String>,
}

#[derive(Debug, Clone)]
struct InstructionObservation {
    kind: &'static str,
    coordinate: String,
    mint: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct Health<'a> {
    schema_version: &'a str,
    at_utc: String,
    endpoint_label: String,
    endpoint_identity: &'a str,
    pid: u32,
    process_instance_uuid: &'a str,
    startup_time_utc: &'a str,
    process_alive: bool,
    evidence_flow_healthy: bool,
    evidence_flow_state: &'a str,
    stream_connected: bool,
    stream_stale_threshold_ms: u64,
    health_stale_threshold_ms: u64,
    last_raw_stream_update_utc: Option<String>,
    last_raw_stream_slot: Option<u64>,
    last_finalized_reconciliation_utc: Option<String>,
    last_partition_append_utc: Option<String>,
    last_exact_complete_partition_utc: Option<String>,
    raw_stream_age_ms: Option<u64>,
    finalized_reconciliation_age_ms: Option<u64>,
    partition_append_age_ms: Option<u64>,
    reconciliation_backlog: u64,
    received: u64,
    queued: u64,
    written: u64,
    duplicates_suppressed: u64,
    reconnects: u64,
    queue_capacity: usize,
    queue_high_water: u64,
    dedupe_entries: usize,
    current_partition_coverage: CoverageCounters,
    free_bytes: u64,
    estimated_bytes_per_day: Option<f64>,
    estimated_days_remaining: Option<f64>,
    storage_warning: Option<&'a str>,
    rpc_snapshot_requests: u64,
    rpc_snapshot_failures: u64,
    rpc_finality_requests: u64,
    rpc_finality_failures: u64,
    rpc_block_requests: u64,
    rpc_block_failures: u64,
}

#[tokio::main]
async fn main() -> Result<()> {
    match parse_cli(env::args().skip(1))? {
        CliCommand::Help => {
            print_help();
            return Ok(());
        }
        CliCommand::Verify(root) => {
            verify_partitions(&root)?;
            return Ok(());
        }
        CliCommand::SchemaHash => {
            println!("{}", sha256_hex(SCHEMA_DOCUMENT.as_bytes()));
            return Ok(());
        }
        CliCommand::Run => {}
    }
    let cfg = Config::from_env()?;
    fs::create_dir_all(&cfg.root)?;
    let _writer_lock = acquire_writer_lock(&cfg.root)?;
    let started = Utc::now();
    let identity = RuntimeIdentity {
        pid: std::process::id(),
        process_instance_uuid: process_instance_uuid(started),
        startup_time_utc: started.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
        endpoint_identity: endpoint_identity(&cfg.endpoint),
    };
    let stats = Arc::new(RuntimeStats::default());
    let (tx, rx) = mpsc::channel(cfg.queue_capacity.max(32));
    let (snapshot_tx, snapshot_rx) = mpsc::channel(1024);
    let (finality_tx, finality_rx) = mpsc::channel(8192);
    let writer_cfg = cfg.clone();
    let writer_stats = stats.clone();
    let writer_identity = identity.clone();
    let writer = tokio::task::spawn_blocking(move || {
        writer_loop(writer_cfg, writer_stats, rx, finality_tx, writer_identity)
    });
    let snapshot_task = tokio::spawn(snapshot_loop(
        cfg.clone(),
        stats.clone(),
        snapshot_rx,
        tx.clone(),
    ));
    let finality_task = tokio::spawn(finality_loop(
        cfg.clone(),
        stats.clone(),
        finality_rx,
        tx.clone(),
        snapshot_tx,
    ));

    eprintln!(
        "pump-exact-ledger: starting schema={} schemaHash={} root={} endpoint={}",
        SCHEMA_VERSION,
        sha256_hex(SCHEMA_DOCUMENT.as_bytes()),
        cfg.root.display(),
        endpoint_label(&cfg.endpoint)
    );
    let mut reconnect_delay = cfg.reconnect_ms.max(250);
    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {
                eprintln!("pump-exact-ledger: source stop requested; draining reconciliation workers");
                break;
            },
            result = collect_once(&cfg, stats.clone(), tx.clone()) => {
                stats.stream_connected.store(false, Ordering::Relaxed);
                stats.reconnects.fetch_add(1, Ordering::Relaxed);
                match result {
                    Ok(()) => eprintln!("pump-exact-ledger: stream ended; reconnecting"),
                    Err(error) => eprintln!("pump-exact-ledger: stream error: {error:#}; reconnecting"),
                }
                tokio::time::sleep(Duration::from_millis(reconnect_delay)).await;
                reconnect_delay = (reconnect_delay * 2).min(15_000);
            }
        }
        if tx.is_closed() {
            break;
        }
    }
    let _ = tx
        .send(IngestItem {
            received_at: Utc::now(),
            payload: IngestPayload::Shutdown,
        })
        .await;
    drop(tx);
    let _ = finality_task.await;
    let _ = snapshot_task.await;
    writer.await??;
    Ok(())
}

fn parse_cli<I, S>(args: I) -> Result<CliCommand>
where
    I: IntoIterator<Item = S>,
    S: Into<String>,
{
    let args: Vec<String> = args.into_iter().map(Into::into).collect();
    match args.as_slice() {
        [value] if value == "run" => Ok(CliCommand::Run),
        [value] if value == "schema-hash" => Ok(CliCommand::SchemaHash),
        [value] if value == "verify" => Ok(CliCommand::Verify(PathBuf::from(
            "/var/lib/pump-exact-evidence-ledger-v1",
        ))),
        [value, root] if value == "verify" => Ok(CliCommand::Verify(PathBuf::from(root))),
        [value] if value == "--help" || value == "-h" || value == "help" => Ok(CliCommand::Help),
        [] => bail!("explicit command required; use `run`, `verify`, or `schema-hash`"),
        _ => bail!("invalid command or arguments: {}", args.join(" ")),
    }
}

fn print_help() {
    println!(
        "pump-exact-evidence-ledger-v1\n\nUSAGE:\n  pump-exact-evidence-ledger-v1 run\n  pump-exact-evidence-ledger-v1 verify [LEDGER_ROOT]\n  pump-exact-evidence-ledger-v1 schema-hash"
    );
}

fn acquire_writer_lock(root: &Path) -> Result<File> {
    let state = root.join("state");
    fs::create_dir_all(&state)?;
    let path = state.join("writer.lock");
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .open(&path)
        .with_context(|| format!("open writer lock {}", path.display()))?;
    lock.try_lock_exclusive().with_context(|| {
        format!(
            "exclusive ledger writer lock unavailable for {}; another writer is active",
            root.display()
        )
    })?;
    Ok(lock)
}

fn process_instance_uuid(started: DateTime<Utc>) -> String {
    if let Ok(value) = env::var("INVOCATION_ID") {
        let compact = value.trim().replace('-', "");
        if compact.len() == 32 && compact.bytes().all(|value| value.is_ascii_hexdigit()) {
            return format!(
                "{}-{}-{}-{}-{}",
                &compact[0..8],
                &compact[8..12],
                &compact[12..16],
                &compact[16..20],
                &compact[20..32]
            );
        }
    }
    if let Ok(value) = fs::read_to_string("/proc/sys/kernel/random/uuid") {
        let value = value.trim();
        if !value.is_empty() {
            return value.to_string();
        }
    }
    let seed = format!(
        "{}:{}",
        std::process::id(),
        started.timestamp_nanos_opt().unwrap_or(0)
    );
    let hash = sha256_hex(seed.as_bytes());
    format!(
        "{}-{}-{}-{}-{}",
        &hash[0..8],
        &hash[8..12],
        &hash[12..16],
        &hash[16..20],
        &hash[20..32]
    )
}

fn endpoint_identity(endpoint: &str) -> String {
    let label = endpoint_label(endpoint);
    format!("{}#{}", label, &sha256_hex(endpoint.as_bytes())[..16])
}

async fn collect_once(
    cfg: &Config,
    stats: Arc<RuntimeStats>,
    tx: mpsc::Sender<IngestItem>,
) -> Result<()> {
    let token: AsciiMetadataValue = cfg.token.parse().context("invalid Shyft x-token")?;
    let channel = Channel::from_shared(cfg.endpoint.clone())?
        .tls_config(ClientTlsConfig::new().with_native_roots())?
        .connect()
        .await?;
    let mut client = GeyserClient::with_interceptor(channel, move |mut request: Request<()>| {
        request.metadata_mut().insert("x-token", token.clone());
        Ok::<_, Status>(request)
    })
    .max_decoding_message_size(128 * 1024 * 1024);
    let from_slot = resume_slot(&cfg.root).map(|slot| slot.saturating_sub(1));
    let request = build_request(from_slot);
    let requests = stream::once(async move { request }).chain(stream::pending());
    let mut updates = client.subscribe(Request::new(requests)).await?.into_inner();
    stats.stream_connected.store(true, Ordering::Relaxed);
    eprintln!("pump-exact-ledger: finalized subscription active fromSlot={from_slot:?}");
    loop {
        let update = match tokio::time::timeout(
            Duration::from_millis(cfg.stream_stale_ms.max(1_000)),
            updates.next(),
        )
        .await
        {
            Ok(Some(update)) => update,
            Ok(None) => break,
            Err(_) => anyhow::bail!(
                "evidence stream stale: no raw update for {}ms",
                cfg.stream_stale_ms
            ),
        };
        let update = update?;
        let received_at = Utc::now();
        stats
            .last_raw_stream_update_unix_ms
            .store(now_unix_ms(), Ordering::Relaxed);
        stats.received.fetch_add(1, Ordering::Relaxed);
        let depth = stats
            .queued
            .load(Ordering::Relaxed)
            .saturating_sub(stats.written.load(Ordering::Relaxed));
        stats.queue_high_water.fetch_max(depth, Ordering::Relaxed);
        tx.send(IngestItem {
            received_at,
            payload: IngestPayload::Yellowstone(Box::new(update)),
        })
        .await
        .context("ledger writer stopped")?;
        stats.queued.fetch_add(1, Ordering::Relaxed);
    }
    Ok(())
}

fn build_request(from_slot: Option<u64>) -> SubscribeRequest {
    let mut accounts = HashMap::new();
    for (label, owner) in [
        ("pumpProgramOwnedAccounts", PUMP_PROGRAM),
        ("pumpFeeConfigAccounts", PUMP_FEE_PROGRAM),
        ("pumpMayhemProgramOwnedAccounts", PUMP_MAYHEM_PROGRAM),
    ] {
        accounts.insert(
            label.to_string(),
            SubscribeRequestFilterAccounts {
                account: vec![],
                owner: vec![owner.to_string()],
                filters: vec![],
                nonempty_txn_signature: None,
                cuckoo_accounts_filter: None,
            },
        );
    }
    let mut transactions = HashMap::new();
    transactions.insert(
        "pumpTransactions".to_string(),
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: None,
            signature: None,
            // Yellowstone ORs values inside account_include. Keeping both
            // addresses in one named filter avoids provider-specific
            // multi-filter composition while retaining the Pump universe.
            account_include: vec![
                PUMP_PROGRAM.to_string(),
                PUMP_CPI_ROUTER_PROGRAM.to_string(),
            ],
            account_exclude: vec![],
            account_required: vec![],
            token_accounts: None,
        },
    );
    transactions.insert(
        "pumpMayhemTransactions".to_string(),
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: None,
            signature: None,
            account_include: vec![PUMP_MAYHEM_PROGRAM.to_string()],
            account_exclude: vec![],
            account_required: vec![],
            token_accounts: None,
        },
    );
    let mut slots = HashMap::new();
    slots.insert(
        "finalizedSlots".to_string(),
        SubscribeRequestFilterSlots {
            filter_by_commitment: Some(true),
            interslot_updates: Some(false),
        },
    );
    let mut blocks_meta = HashMap::new();
    blocks_meta.insert("blockMeta".to_string(), SubscribeRequestFilterBlocksMeta {});
    SubscribeRequest {
        accounts,
        slots,
        transactions,
        transactions_status: HashMap::new(),
        blocks: HashMap::new(),
        blocks_meta,
        entry: HashMap::new(),
        accounts_data_slice: vec![],
        commitment: Some(CommitmentLevel::Finalized as i32),
        ping: None,
        from_slot,
    }
}

fn writer_loop(
    cfg: Config,
    stats: Arc<RuntimeStats>,
    mut rx: mpsc::Receiver<IngestItem>,
    finality_tx: mpsc::Sender<(String, u64)>,
    identity: RuntimeIdentity,
) -> Result<()> {
    let mut writer = LedgerWriter::open(&cfg.root)?;
    let started = DateTime::parse_from_rfc3339(&identity.startup_time_utc)?.with_timezone(&Utc);
    let mut sequence = writer.dedupe_entries() as u64;
    let mut last_health = std::time::Instant::now() - Duration::from_secs(60);
    let mut finality_tx = Some(finality_tx);
    // Replace any prior instance's health atomically before network startup so
    // the watchdog can never attribute stale health to this systemd invocation.
    write_health(&cfg, &stats, &writer, started, &identity)?;
    loop {
        let item = match rx.try_recv() {
            Ok(item) => item,
            Err(tokio::sync::mpsc::error::TryRecvError::Empty) => {
                if last_health.elapsed() >= Duration::from_secs(10) {
                    write_health(&cfg, &stats, &writer, started, &identity)?;
                    last_health = std::time::Instant::now();
                }
                std::thread::sleep(Duration::from_millis(10));
                continue;
            }
            Err(tokio::sync::mpsc::error::TryRecvError::Disconnected) => break,
        };
        if matches!(&item.payload, IngestPayload::Shutdown) {
            finality_tx.take();
            continue;
        }
        sequence += 1;
        let (metadata, raw, raw_stream_record, two_cq_candidates) = match item.payload {
            IngestPayload::Yellowstone(update) => {
                let raw = update.encode_to_vec();
                let two_cq_candidates = extract_two_cq_candidates(&update, item.received_at);
                let metadata = summarize_update(
                    &update,
                    &raw,
                    item.received_at,
                    sequence,
                    !cfg.endpoint.contains("rabbitstream"),
                );
                if metadata.kind == "transaction" {
                    if let (Some(signature), Some(slot)) =
                        (metadata.signature.clone(), metadata.slot)
                    {
                        if let Some(finality_tx) = finality_tx.as_ref() {
                            if finality_tx.blocking_send((signature, slot)).is_ok() {
                                stats.reconciliation_backlog.fetch_add(1, Ordering::Relaxed);
                            }
                        }
                    }
                }
                (metadata, raw, true, two_cq_candidates)
            }
            IngestPayload::RpcMintSnapshot(snapshot) => {
                let metadata = summarize_rpc_snapshot(&snapshot, item.received_at, sequence);
                (metadata, snapshot.raw_json, false, Vec::new())
            }
            IngestPayload::RpcFinalizedTransaction(finalized) => {
                let metadata = summarize_rpc_finality(&finalized, item.received_at, sequence);
                (metadata, finalized.raw_json, false, Vec::new())
            }
            IngestPayload::RpcFinalizedBlock(block) => {
                let metadata = summarize_rpc_block(&block, item.received_at, sequence);
                (metadata, block.raw_json, false, Vec::new())
            }
            IngestPayload::Shutdown => unreachable!("shutdown handled before sequencing"),
        };
        let raw_stream_slot = raw_stream_record.then_some(metadata.slot).flatten();
        let finalized_reconciliation = metadata.kind == "rpc_finalized_transaction";
        if writer.append(item.received_at, FrameRecord { metadata, raw })? {
            stats.written.fetch_add(1, Ordering::Relaxed);
            let appended_at = now_unix_ms();
            stats
                .last_partition_append_unix_ms
                .store(appended_at, Ordering::Relaxed);
            if finalized_reconciliation {
                stats
                    .last_finalized_reconciliation_unix_ms
                    .store(appended_at, Ordering::Relaxed);
            }
            if let Some(slot) = raw_stream_slot {
                atomic_write(
                    &cfg.root.join("state/last-raw-stream-slot"),
                    slot.to_string().as_bytes(),
                )?;
                // Keep the legacy cursor synchronized for external readers.
                atomic_write(
                    &cfg.root.join("state/last-finalized-slot"),
                    slot.to_string().as_bytes(),
                )?;
                stats.last_raw_stream_slot.store(slot, Ordering::Relaxed);
            }
            if let Some(path) = cfg.two_cq_detector_path.as_deref() {
                if let Err(error) = append_two_cq_candidates(path, &two_cq_candidates) {
                    eprintln!(
                        "pump-exact-ledger: 2CQ detector append failed path={} error={error:#}",
                        path.display()
                    );
                }
            }
        } else {
            stats.duplicates.fetch_add(1, Ordering::Relaxed);
        }
        if last_health.elapsed() >= Duration::from_secs(30) {
            write_health(&cfg, &stats, &writer, started, &identity)?;
            last_health = std::time::Instant::now();
        }
    }
    writer.finalize_current(false)?;
    write_health(&cfg, &stats, &writer, started, &identity)?;
    Ok(())
}

fn append_two_cq_candidates(path: &Path, candidates: &[TwoCqDetectedSwap]) -> Result<()> {
    if candidates.is_empty() {
        return Ok(());
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    for candidate in candidates {
        let mut row = serde_json::to_vec(candidate)?;
        row.push(b'\n');
        file.write_all(&row)?;
    }
    file.flush()?;
    file.sync_data()?;
    Ok(())
}

fn resolved_transaction_keys(
    message: &yellowstone_grpc_proto::solana::storage::confirmed_block::Message,
    meta: &yellowstone_grpc_proto::solana::storage::confirmed_block::TransactionStatusMeta,
) -> Vec<String> {
    let mut keys = message
        .account_keys
        .iter()
        .map(|key| bs58::encode(key).into_string())
        .collect::<Vec<_>>();
    keys.extend(
        meta.loaded_writable_addresses
            .iter()
            .map(|key| bs58::encode(key).into_string()),
    );
    keys.extend(
        meta.loaded_readonly_addresses
            .iter()
            .map(|key| bs58::encode(key).into_string()),
    );
    keys
}

fn extract_two_cq_candidates(
    update: &SubscribeUpdate,
    received_at: DateTime<Utc>,
) -> Vec<TwoCqDetectedSwap> {
    let Some(UpdateOneof::Transaction(tx)) = update.update_oneof.as_ref() else {
        return Vec::new();
    };
    let Some(info) = tx.transaction.as_ref() else {
        return Vec::new();
    };
    let Some(meta) = info.meta.as_ref() else {
        return Vec::new();
    };
    let Some(message) = info.transaction.as_ref().and_then(|tx| tx.message.as_ref()) else {
        return Vec::new();
    };
    let keys = resolved_transaction_keys(message, meta);
    if !keys.iter().any(|key| key == TWO_CQ_WALLET) {
        return Vec::new();
    }
    let signature = bs58::encode(&info.signature).into_string();
    let mut candidates = Vec::new();
    let mut inspect = |program_id_index: u32, accounts: &[u8], data: &[u8]| {
        if keys.get(program_id_index as usize).map(String::as_str) != Some(PUMP_PROGRAM)
            || data.len() < 8
            || !matches!(&data[..8], value if value == BUY || value == BUY_EXACT_SOL_IN || value == BUY_V2 || value == BUY_EXACT_QUOTE_IN_V2)
        {
            return;
        }
        let source_accounts = accounts
            .iter()
            .filter_map(|index| keys.get(*index as usize).cloned())
            .collect::<Vec<_>>();
        if source_accounts.get(6).map(String::as_str) != Some(TWO_CQ_WALLET) {
            return;
        }
        let increased_mints = meta
            .post_token_balances
            .iter()
            .filter(|balance| balance.owner == TWO_CQ_WALLET)
            .filter_map(|post| {
                let post_amount = post.ui_token_amount.as_ref()?.amount.parse::<u128>().ok()?;
                let pre_amount = meta
                    .pre_token_balances
                    .iter()
                    .find(|pre| pre.account_index == post.account_index && pre.mint == post.mint)
                    .and_then(|pre| pre.ui_token_amount.as_ref())
                    .and_then(|amount| amount.amount.parse::<u128>().ok())
                    .unwrap_or(0);
                (post_amount > pre_amount && source_accounts.iter().any(|key| key == &post.mint))
                    .then(|| post.mint.clone())
            })
            .collect::<Vec<_>>();
        let mut unique_mints = increased_mints;
        unique_mints.sort();
        unique_mints.dedup();
        let output_mint = if unique_mints.len() == 1 {
            unique_mints.remove(0)
        } else if unique_mints.is_empty() {
            let Some(mint) = source_accounts.get(2).cloned() else {
                return;
            };
            mint
        } else {
            return;
        };
        let exact_sol_amount = (data[..8] == BUY_EXACT_SOL_IN)
            .then(|| data.get(8..16))
            .flatten()
            .and_then(|bytes| bytes.try_into().ok())
            .map(u64::from_le_bytes);
        candidates.push(TwoCqDetectedSwap {
            kind: "swap",
            source: "rust-exact-ledger-yellowstone-direct-v1",
            detected_at_ms: received_at.timestamp_millis(),
            signature: signature.clone(),
            wallet: TWO_CQ_WALLET,
            input_mint: NATIVE_MINT,
            output_mint,
            amount_raw: exact_sol_amount,
            is_native_input: true,
            counterparty_amount_raw: None,
            source_ix_accounts: source_accounts,
            source_ix_data_base64: BASE64_STANDARD.encode(data),
            source_pump_swap_instruction: None,
            source_pump_swap_accounts: None,
            source_quote_mint: NATIVE_MINT,
            source_quote_amount_raw: exact_sol_amount,
            source_slot: tx.slot,
            source_transaction_index: info.index,
        });
    };
    for instruction in &message.instructions {
        inspect(
            instruction.program_id_index,
            &instruction.accounts,
            &instruction.data,
        );
    }
    for group in &meta.inner_instructions {
        for instruction in &group.instructions {
            inspect(
                instruction.program_id_index,
                &instruction.accounts,
                &instruction.data,
            );
        }
    }
    candidates
}

fn summarize_update(
    update: &SubscribeUpdate,
    raw: &[u8],
    received_at: DateTime<Utc>,
    writer_sequence: u64,
    authoritative_finality: bool,
) -> EvidenceMetadata {
    let raw_sha256 = sha256_hex(raw);
    let decode_at = Utc::now();
    let mut metadata = EvidenceMetadata {
        schema_version: SCHEMA_VERSION.to_string(),
        evidence_id: String::new(),
        raw_sha256: raw_sha256.clone(),
        kind: "other".to_string(),
        raw_format: "yellowstone_subscribe_update_protobuf".to_string(),
        received_at_utc: received_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
        received_at_unix_ns: received_at.timestamp_nanos_opt().unwrap_or(i64::MAX),
        decode_at_unix_ns: decode_at.timestamp_nanos_opt().unwrap_or(i64::MAX),
        writer_sequence,
        slot: None,
        transaction_index: None,
        signature: None,
        account_pubkey: None,
        account_owner: None,
        write_version: None,
        finalized: authoritative_finality,
        successful: None,
        failed: None,
        unresolved: false,
        pump_instruction_kinds: vec![],
        pump_instruction_coordinates: vec![],
        discovered_mints: vec![],
        has_trade_event: false,
        has_fee_evidence: false,
        missing_raw_tx: false,
        missing_trade_event: false,
        missing_reserve_transition: false,
        missing_canonical_ordering: false,
        missing_fee_evidence: false,
        decode_failed: false,
        filters: update.filters.clone(),
    };
    match update.update_oneof.as_ref() {
        Some(UpdateOneof::Transaction(tx)) => summarize_transaction(tx, &mut metadata),
        Some(UpdateOneof::Account(account)) => {
            metadata.kind = "account".to_string();
            metadata.slot = Some(account.slot);
            if let Some(info) = account.account.as_ref() {
                metadata.account_pubkey = Some(bs58::encode(&info.pubkey).into_string());
                metadata.account_owner = Some(bs58::encode(&info.owner).into_string());
                metadata.write_version = Some(info.write_version);
                metadata.signature = info
                    .txn_signature
                    .as_ref()
                    .map(|value| bs58::encode(value).into_string());
            } else {
                metadata.unresolved = true;
                metadata.decode_failed = true;
            }
        }
        Some(UpdateOneof::BlockMeta(block)) => {
            metadata.kind = "block_meta".to_string();
            metadata.slot = Some(block.slot);
        }
        Some(UpdateOneof::Slot(slot)) => {
            metadata.kind = "slot".to_string();
            metadata.slot = Some(slot.slot);
        }
        Some(UpdateOneof::Ping(_)) | Some(UpdateOneof::Pong(_)) => {
            metadata.kind = "keepalive".to_string()
        }
        Some(_) => metadata.kind = "unexpected".to_string(),
        None => {
            metadata.unresolved = true;
            metadata.decode_failed = true;
        }
    }
    if metadata.kind == "transaction" && !authoritative_finality {
        metadata.successful = None;
        metadata.failed = None;
        metadata.unresolved = true;
        metadata.missing_trade_event = false;
        metadata.missing_reserve_transition = false;
        metadata.missing_fee_evidence = false;
    }
    finish_identity(&mut metadata, &raw_sha256);
    metadata
}

fn summarize_transaction(
    tx: &yellowstone_grpc_proto::geyser::SubscribeUpdateTransaction,
    metadata: &mut EvidenceMetadata,
) {
    metadata.kind = "transaction".to_string();
    metadata.slot = Some(tx.slot);
    let Some(info) = tx.transaction.as_ref() else {
        metadata.unresolved = true;
        metadata.missing_raw_tx = true;
        metadata.decode_failed = true;
        return;
    };
    metadata.transaction_index = Some(info.index);
    metadata.signature = Some(bs58::encode(&info.signature).into_string());
    let Some(meta) = info.meta.as_ref() else {
        metadata.unresolved = true;
        return;
    };
    metadata.successful = Some(meta.err.is_none());
    metadata.failed = Some(meta.err.is_some());
    let Some(message) = info.transaction.as_ref().and_then(|tx| tx.message.as_ref()) else {
        metadata.unresolved = true;
        metadata.missing_raw_tx = true;
        return;
    };
    let mut keys = message
        .account_keys
        .iter()
        .map(|key| bs58::encode(key).into_string())
        .collect::<Vec<_>>();
    keys.extend(
        meta.loaded_writable_addresses
            .iter()
            .map(|key| bs58::encode(key).into_string()),
    );
    keys.extend(
        meta.loaded_readonly_addresses
            .iter()
            .map(|key| bs58::encode(key).into_string()),
    );
    let mut observations = Vec::new();
    for (outer_index, instruction) in message.instructions.iter().enumerate() {
        observe_instruction(
            &keys,
            instruction.program_id_index,
            &instruction.accounts,
            &instruction.data,
            format!("outer:{outer_index}"),
            &mut observations,
        );
    }
    for group in &meta.inner_instructions {
        for (inner_index, instruction) in group.instructions.iter().enumerate() {
            observe_instruction(
                &keys,
                instruction.program_id_index,
                &instruction.accounts,
                &instruction.data,
                format!(
                    "outer:{}:inner:{}:stack:{}",
                    group.index,
                    inner_index,
                    instruction
                        .stack_height
                        .map(|value| value.to_string())
                        .unwrap_or_else(|| "unknown".to_string())
                ),
                &mut observations,
            );
        }
    }
    metadata.has_trade_event = observations.iter().any(|item| item.kind == "TRADE_EVENT")
        || meta.log_messages.iter().any(|line| {
            line.strip_prefix("Program data: ")
                .and_then(|encoded| BASE64_STANDARD.decode(encoded).ok())
                .is_some_and(|data| data.starts_with(&TRADE_EVENT))
        });
    metadata.pump_instruction_kinds = observations
        .iter()
        .filter(|item| item.kind != "TRADE_EVENT")
        .map(|item| item.kind.to_string())
        .collect();
    metadata.pump_instruction_coordinates = observations
        .iter()
        .map(|item| item.coordinate.clone())
        .collect();
    metadata.discovered_mints = observations
        .iter()
        .filter_map(|item| item.mint.clone())
        .collect();
    metadata.discovered_mints.sort();
    metadata.discovered_mints.dedup();
    let is_trade = observations.iter().any(|item| {
        matches!(
            item.kind,
            "BUY" | "BUY_EXACT_SOL_IN" | "BUY_V2" | "BUY_EXACT_QUOTE_IN_V2" | "SELL" | "SELL_V2"
        )
    });
    metadata.has_fee_evidence = metadata.has_trade_event
        || keys.iter().any(|key| key == PUMP_FEE_PROGRAM)
        || meta
            .log_messages
            .iter()
            .any(|line| line.to_ascii_lowercase().contains("fee"));
    let successful_trade = metadata.successful == Some(true) && is_trade;
    metadata.missing_trade_event = successful_trade && !metadata.has_trade_event;
    metadata.missing_reserve_transition = successful_trade && !metadata.has_trade_event;
    metadata.missing_fee_evidence = successful_trade && !metadata.has_fee_evidence;
}

fn observe_instruction(
    keys: &[String],
    program_id_index: u32,
    accounts: &[u8],
    data: &[u8],
    coordinate: String,
    output: &mut Vec<InstructionObservation>,
) {
    if keys.get(program_id_index as usize).map(String::as_str) != Some(PUMP_PROGRAM)
        || data.len() < 8
    {
        return;
    }
    let discriminator: [u8; 8] = data[..8].try_into().unwrap();
    let kind = match discriminator {
        BUY => "BUY",
        BUY_EXACT_SOL_IN => "BUY_EXACT_SOL_IN",
        BUY_V2 => "BUY_V2",
        BUY_EXACT_QUOTE_IN_V2 => "BUY_EXACT_QUOTE_IN_V2",
        SELL => "SELL",
        SELL_V2 => "SELL_V2",
        CREATE => "CREATE",
        CREATE_V2 => "CREATE_V2",
        TRADE_EVENT => "TRADE_EVENT",
        _ => "OTHER_PUMP_INSTRUCTION",
    };
    let mint = matches!(kind, "CREATE" | "CREATE_V2")
        .then(|| {
            accounts
                .first()
                .and_then(|index| keys.get(*index as usize))
                .cloned()
        })
        .flatten();
    output.push(InstructionObservation {
        kind,
        coordinate,
        mint,
    });
}

fn finish_identity(metadata: &mut EvidenceMetadata, raw_sha256: &str) {
    let identity = format!(
        "{}|{}|{}|{}|{}|{}|{}",
        metadata.kind,
        metadata
            .slot
            .map(|value| value.to_string())
            .unwrap_or_default(),
        metadata
            .transaction_index
            .map(|value| value.to_string())
            .unwrap_or_default(),
        metadata.pump_instruction_coordinates.join(","),
        metadata
            .signature
            .as_deref()
            .or(metadata.account_pubkey.as_deref())
            .unwrap_or(""),
        metadata
            .write_version
            .map(|value| value.to_string())
            .unwrap_or_default(),
        raw_sha256
    );
    metadata.evidence_id = sha256_hex(identity.as_bytes());
}

fn summarize_rpc_snapshot(
    snapshot: &RpcMintSnapshot,
    received_at: DateTime<Utc>,
    writer_sequence: u64,
) -> EvidenceMetadata {
    let raw_sha256 = sha256_hex(&snapshot.raw_json);
    let mut metadata = EvidenceMetadata {
        schema_version: SCHEMA_VERSION.to_string(),
        evidence_id: String::new(),
        raw_sha256: raw_sha256.clone(),
        kind: "rpc_account_snapshot".to_string(),
        raw_format: "solana_json_rpc_response".to_string(),
        received_at_utc: received_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
        received_at_unix_ns: received_at.timestamp_nanos_opt().unwrap_or(i64::MAX),
        decode_at_unix_ns: Utc::now().timestamp_nanos_opt().unwrap_or(i64::MAX),
        writer_sequence,
        slot: snapshot.context_slot.or(Some(snapshot.min_context_slot)),
        transaction_index: None,
        signature: None,
        account_pubkey: Some(snapshot.mint.clone()),
        account_owner: snapshot.owner.clone(),
        write_version: None,
        finalized: snapshot.successful,
        successful: Some(snapshot.successful),
        failed: Some(!snapshot.successful),
        unresolved: !snapshot.successful,
        pump_instruction_kinds: vec!["CREATE_MINT_FINALIZED_SNAPSHOT".to_string()],
        pump_instruction_coordinates: vec![],
        discovered_mints: vec![snapshot.mint.clone()],
        has_trade_event: false,
        has_fee_evidence: false,
        missing_raw_tx: false,
        missing_trade_event: false,
        missing_reserve_transition: false,
        missing_canonical_ordering: false,
        missing_fee_evidence: false,
        decode_failed: !snapshot.successful,
        filters: vec!["finalizedGetAccountInfo".to_string()],
    };
    finish_identity(&mut metadata, &raw_sha256);
    metadata
}

async fn snapshot_loop(
    cfg: Config,
    stats: Arc<RuntimeStats>,
    mut rx: mpsc::Receiver<(String, u64)>,
    writer_tx: mpsc::Sender<IngestItem>,
) {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(4))
        .build()
        .expect("RPC snapshot client");
    let mut seen = HashSet::new();
    let permits = Arc::new(tokio::sync::Semaphore::new(32));
    while let Some((mint, min_context_slot)) = rx.recv().await {
        let backlog_guard = ReconciliationBacklogGuard(stats.clone());
        if !seen.insert(mint.clone()) {
            continue;
        }
        let client = client.clone();
        let rpc_url = cfg.rpc_url.clone();
        let stats = stats.clone();
        let writer_tx = writer_tx.clone();
        let permits = permits.clone();
        tokio::spawn(async move {
            let _backlog_guard = backlog_guard;
            stats.rpc_snapshot_requests.fetch_add(1, Ordering::Relaxed);
            tokio::time::sleep(Duration::from_secs(12)).await;
            let Ok(_permit) = permits.acquire_owned().await else {
                return;
            };
            let value = rpc_retry(
                &client,
                rpc_url.as_deref(),
                || serde_json::json!({
                    "jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                    "params": [mint, {"encoding": "base64", "commitment": "finalized", "minContextSlot": min_context_slot}]
                }),
                |value| value.pointer("/result/value").is_some_and(|entry| !entry.is_null()),
            ).await;
            let successful = value
                .pointer("/result/value")
                .is_some_and(|entry| !entry.is_null());
            if !successful {
                stats.rpc_snapshot_failures.fetch_add(1, Ordering::Relaxed);
            }
            let snapshot = RpcMintSnapshot {
                mint,
                min_context_slot,
                raw_json: serde_json::to_vec(&value).unwrap_or_default(),
                context_slot: value
                    .pointer("/result/context/slot")
                    .and_then(Value::as_u64),
                owner: value
                    .pointer("/result/value/owner")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                successful,
            };
            let _ = writer_tx
                .send(IngestItem {
                    received_at: Utc::now(),
                    payload: IngestPayload::RpcMintSnapshot(snapshot),
                })
                .await;
        });
    }
}

async fn rpc_retry<F, P>(
    client: &reqwest::Client,
    rpc_url: Option<&str>,
    request: F,
    ready: P,
) -> Value
where
    F: Fn() -> Value,
    P: Fn(&Value) -> bool,
{
    let Some(rpc_url) = rpc_url else {
        return serde_json::json!({"error": "rpc_not_configured"});
    };
    let mut last = serde_json::json!({"error": "no_rpc_attempt"});
    for attempt in 1..=30 {
        if attempt > 1 {
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
        match client.post(rpc_url).json(&request()).send().await {
            Ok(response) => match response.json::<Value>().await {
                Ok(value) => {
                    let done = ready(&value);
                    last = value;
                    if done {
                        break;
                    }
                }
                Err(error) => {
                    last = serde_json::json!({"decodeError": error.to_string(), "attempt": attempt})
                }
            },
            Err(error) => {
                last = serde_json::json!({"transportError": error.to_string(), "attempt": attempt})
            }
        }
    }
    last
}

async fn finality_loop(
    cfg: Config,
    stats: Arc<RuntimeStats>,
    mut rx: mpsc::Receiver<(String, u64)>,
    writer_tx: mpsc::Sender<IngestItem>,
    snapshot_tx: mpsc::Sender<(String, u64)>,
) {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(6))
        .build()
        .expect("RPC finality client");
    let permits = Arc::new(tokio::sync::Semaphore::new(64));
    let block_cache: Arc<Mutex<HashMap<u64, Arc<OnceCell<BlockIndex>>>>> =
        Arc::new(Mutex::new(HashMap::new()));
    let mut seen = HashSet::new();
    while let Some((signature, source_slot)) = rx.recv().await {
        let backlog_guard = ReconciliationBacklogGuard(stats.clone());
        if !seen.insert(signature.clone()) {
            continue;
        }
        let client = client.clone();
        let rpc_url = cfg.rpc_url.clone();
        let stats = stats.clone();
        let writer_tx = writer_tx.clone();
        let snapshot_tx = snapshot_tx.clone();
        let permits = permits.clone();
        let block_cache = block_cache.clone();
        tokio::spawn(async move {
            let _backlog_guard = backlog_guard;
            stats.rpc_finality_requests.fetch_add(1, Ordering::Relaxed);
            tokio::time::sleep(Duration::from_secs(12)).await;
            let Ok(_permit) = permits.acquire_owned().await else {
                return;
            };
            let value = rpc_retry(
                &client,
                rpc_url.as_deref(),
                || serde_json::json!({
                    "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                    "params": [signature, {"encoding": "json", "commitment": "finalized", "maxSupportedTransactionVersion": 0}]
                }),
                |value| value.get("result").is_some_and(|entry| !entry.is_null()),
            ).await;
            let result = value.get("result").filter(|entry| !entry.is_null());
            let successful = result
                .and_then(|entry| entry.get("meta"))
                .map(|meta| meta.get("err").is_none_or(Value::is_null));
            if successful.is_none() {
                stats.rpc_finality_failures.fetch_add(1, Ordering::Relaxed);
            }
            if successful == Some(true) {
                if let Some(result) = result {
                    let finalized_slot = result
                        .get("slot")
                        .and_then(Value::as_u64)
                        .unwrap_or(source_slot);
                    for mint in rpc_finalized_create_mints(result) {
                        if snapshot_tx.send((mint, finalized_slot)).await.is_ok() {
                            stats.reconciliation_backlog.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                }
            }
            let block_cell = {
                let mut cache = block_cache.lock().await;
                if cache.len() > 10_000 {
                    cache.retain(|slot, _| slot.saturating_add(5_000) >= source_slot);
                }
                cache
                    .entry(source_slot)
                    .or_insert_with(|| Arc::new(OnceCell::new()))
                    .clone()
            };
            let block_index = block_cell
                .get_or_init(|| async {
                    stats.rpc_block_requests.fetch_add(1, Ordering::Relaxed);
                    let block_value = rpc_retry(
                        &client,
                        rpc_url.as_deref(),
                        || {
                            serde_json::json!({
                                "jsonrpc": "2.0", "id": 1, "method": "getBlock",
                                "params": [source_slot, {
                                    "commitment": "finalized",
                                    "transactionDetails": "signatures",
                                    "rewards": false,
                                    "maxSupportedTransactionVersion": 0
                                }]
                            })
                        },
                        |value| {
                            value
                                .pointer("/result/signatures")
                                .is_some_and(Value::is_array)
                        },
                    )
                    .await;
                    let signatures = block_value
                        .pointer("/result/signatures")
                        .and_then(Value::as_array)
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect::<Vec<_>>();
                    let block_successful = !signatures.is_empty();
                    if !block_successful {
                        stats.rpc_block_failures.fetch_add(1, Ordering::Relaxed);
                    }
                    let _ = writer_tx
                        .send(IngestItem {
                            received_at: Utc::now(),
                            payload: IngestPayload::RpcFinalizedBlock(RpcFinalizedBlock {
                                slot: source_slot,
                                raw_json: serde_json::to_vec(&block_value).unwrap_or_default(),
                                successful: block_successful,
                            }),
                        })
                        .await;
                    BlockIndex { signatures }
                })
                .await;
            let canonical_transaction_index = block_index
                .signatures
                .iter()
                .position(|value| value == &signature)
                .map(|index| index as u64);
            let finalized = RpcFinalizedTransaction {
                signature,
                source_slot,
                canonical_transaction_index,
                raw_json: serde_json::to_vec(&value).unwrap_or_default(),
                finalized_slot: result
                    .and_then(|entry| entry.get("slot"))
                    .and_then(Value::as_u64),
                successful,
            };
            let _ = writer_tx
                .send(IngestItem {
                    received_at: Utc::now(),
                    payload: IngestPayload::RpcFinalizedTransaction(finalized),
                })
                .await;
        });
    }
}

fn summarize_rpc_block(
    block: &RpcFinalizedBlock,
    received_at: DateTime<Utc>,
    writer_sequence: u64,
) -> EvidenceMetadata {
    let raw_sha256 = sha256_hex(&block.raw_json);
    let mut metadata = EvidenceMetadata {
        schema_version: SCHEMA_VERSION.to_string(),
        evidence_id: String::new(),
        raw_sha256: raw_sha256.clone(),
        kind: "rpc_finalized_block".to_string(),
        raw_format: "solana_json_rpc_response".to_string(),
        received_at_utc: received_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
        received_at_unix_ns: received_at.timestamp_nanos_opt().unwrap_or(i64::MAX),
        decode_at_unix_ns: Utc::now().timestamp_nanos_opt().unwrap_or(i64::MAX),
        writer_sequence,
        slot: Some(block.slot),
        transaction_index: None,
        signature: None,
        account_pubkey: None,
        account_owner: None,
        write_version: None,
        finalized: block.successful,
        successful: Some(block.successful),
        failed: None,
        unresolved: !block.successful,
        pump_instruction_kinds: vec!["FINALIZED_BLOCK_SIGNATURE_ORDER".to_string()],
        pump_instruction_coordinates: vec![],
        discovered_mints: vec![],
        has_trade_event: false,
        has_fee_evidence: false,
        missing_raw_tx: false,
        missing_trade_event: false,
        missing_reserve_transition: false,
        missing_canonical_ordering: !block.successful,
        missing_fee_evidence: false,
        decode_failed: !block.successful,
        filters: vec!["finalizedGetBlockSignatures".to_string()],
    };
    finish_identity(&mut metadata, &raw_sha256);
    metadata
}

fn rpc_finalized_create_mints(result: &Value) -> Vec<String> {
    let mut mints = rpc_finalized_observations(result)
        .into_iter()
        .filter(|item| matches!(item.kind, "CREATE" | "CREATE_V2"))
        .filter_map(|item| item.mint)
        .collect::<Vec<_>>();
    mints.sort();
    mints.dedup();
    mints
}

fn rpc_finalized_observations(result: &Value) -> Vec<InstructionObservation> {
    let Some(message) = result.pointer("/transaction/message") else {
        return Vec::new();
    };
    let mut keys = message
        .get("accountKeys")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|value| {
            value
                .as_str()
                .or_else(|| value.get("pubkey").and_then(Value::as_str))
                .map(str::to_string)
        })
        .collect::<Vec<_>>();
    if let Some(loaded) = result.pointer("/meta/loadedAddresses") {
        for side in ["writable", "readonly"] {
            keys.extend(
                loaded
                    .get(side)
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_string),
            );
        }
    }
    let mut instructions = message
        .get("instructions")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .enumerate()
        .map(|(index, instruction)| (format!("outer:{index}"), instruction))
        .collect::<Vec<_>>();
    if let Some(groups) = result
        .pointer("/meta/innerInstructions")
        .and_then(Value::as_array)
    {
        for group in groups {
            let outer = group.get("index").and_then(Value::as_u64).unwrap_or(0);
            instructions.extend(
                group
                    .get("instructions")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .enumerate()
                    .map(|(inner, instruction)| {
                        (
                            format!("outer:{outer}:inner:{inner}:stack:rpc"),
                            instruction,
                        )
                    }),
            );
        }
    }
    let mut observations = Vec::new();
    for (coordinate, instruction) in instructions {
        let Some(program_index) = instruction.get("programIdIndex").and_then(Value::as_u64) else {
            continue;
        };
        if keys.get(program_index as usize).map(String::as_str) != Some(PUMP_PROGRAM) {
            continue;
        }
        let Some(encoded) = instruction.get("data").and_then(Value::as_str) else {
            continue;
        };
        let Ok(data) = bs58::decode(encoded).into_vec() else {
            continue;
        };
        if data.len() < 8 {
            continue;
        }
        let discriminator: [u8; 8] = data[..8].try_into().unwrap();
        let kind = match discriminator {
            BUY => "BUY",
            BUY_EXACT_SOL_IN => "BUY_EXACT_SOL_IN",
            BUY_V2 => "BUY_V2",
            BUY_EXACT_QUOTE_IN_V2 => "BUY_EXACT_QUOTE_IN_V2",
            SELL => "SELL",
            SELL_V2 => "SELL_V2",
            CREATE => "CREATE",
            CREATE_V2 => "CREATE_V2",
            TRADE_EVENT => "TRADE_EVENT",
            _ => "OTHER_PUMP_INSTRUCTION",
        };
        let account_indices = instruction
            .get("accounts")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mint_account_position = match kind {
            "CREATE" | "CREATE_V2" => Some(0),
            "BUY_V2" | "BUY_EXACT_QUOTE_IN_V2" | "SELL_V2" => Some(1),
            "BUY" | "BUY_EXACT_SOL_IN" | "SELL" => Some(2),
            _ => None,
        };
        let mint = mint_account_position
            .and_then(|position| account_indices.get(position))
            .and_then(Value::as_u64)
            .and_then(|index| keys.get(index as usize))
            .cloned();
        observations.push(InstructionObservation {
            kind,
            coordinate,
            mint,
        });
    }
    observations
}

fn summarize_rpc_finality(
    finalized: &RpcFinalizedTransaction,
    received_at: DateTime<Utc>,
    writer_sequence: u64,
) -> EvidenceMetadata {
    let raw_sha256 = sha256_hex(&finalized.raw_json);
    let rpc_value = serde_json::from_slice::<Value>(&finalized.raw_json).unwrap_or_default();
    let result = rpc_value.get("result").filter(|value| !value.is_null());
    let observations = result.map(rpc_finalized_observations).unwrap_or_default();
    let is_trade = observations.iter().any(|item| {
        matches!(
            item.kind,
            "BUY" | "BUY_EXACT_SOL_IN" | "BUY_V2" | "BUY_EXACT_QUOTE_IN_V2" | "SELL" | "SELL_V2"
        )
    });
    let has_trade_event = result
        .and_then(|value| value.pointer("/meta/logMessages"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .any(|line| {
            line.strip_prefix("Program data: ")
                .and_then(|encoded| BASE64_STANDARD.decode(encoded).ok())
                .is_some_and(|data| data.starts_with(&TRADE_EVENT))
        });
    let mut discovered_mints = observations
        .iter()
        .filter_map(|item| item.mint.clone())
        .collect::<Vec<_>>();
    discovered_mints.sort();
    discovered_mints.dedup();
    let successful_trade = finalized.successful == Some(true) && is_trade;
    let mut metadata = EvidenceMetadata {
        schema_version: SCHEMA_VERSION.to_string(),
        evidence_id: String::new(),
        raw_sha256: raw_sha256.clone(),
        kind: "rpc_finalized_transaction".to_string(),
        raw_format: "solana_json_rpc_response".to_string(),
        received_at_utc: received_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
        received_at_unix_ns: received_at.timestamp_nanos_opt().unwrap_or(i64::MAX),
        decode_at_unix_ns: Utc::now().timestamp_nanos_opt().unwrap_or(i64::MAX),
        writer_sequence,
        slot: finalized.finalized_slot.or(Some(finalized.source_slot)),
        transaction_index: finalized.canonical_transaction_index,
        signature: Some(finalized.signature.clone()),
        account_pubkey: None,
        account_owner: None,
        write_version: None,
        finalized: finalized.successful.is_some(),
        successful: finalized.successful,
        failed: finalized.successful.map(|value| !value),
        unresolved: finalized.successful.is_none(),
        pump_instruction_kinds: observations
            .iter()
            .map(|item| item.kind.to_string())
            .collect(),
        pump_instruction_coordinates: observations
            .iter()
            .map(|item| item.coordinate.clone())
            .collect(),
        discovered_mints,
        has_trade_event,
        has_fee_evidence: !successful_trade || has_trade_event,
        missing_raw_tx: finalized.successful.is_none(),
        missing_trade_event: successful_trade && !has_trade_event,
        missing_reserve_transition: successful_trade && !has_trade_event,
        missing_canonical_ordering: finalized.canonical_transaction_index.is_none(),
        missing_fee_evidence: successful_trade && !has_trade_event,
        decode_failed: finalized.successful.is_none(),
        filters: vec!["finalizedGetTransaction".to_string()],
    };
    finish_identity(&mut metadata, &raw_sha256);
    metadata
}

fn write_health(
    cfg: &Config,
    stats: &RuntimeStats,
    writer: &LedgerWriter,
    started: DateTime<Utc>,
    identity: &RuntimeIdentity,
) -> Result<()> {
    let free_bytes = fs2::available_space(&cfg.storage_device).unwrap_or(0);
    let elapsed_days = (Utc::now() - started).num_milliseconds().max(1) as f64 / 86_400_000.0;
    let written_bytes = directory_size(&cfg.root.join("partitions")).unwrap_or(0);
    let bytes_per_day = estimate_closed_partition_bytes_per_day(&cfg.root)
        .or_else(|| (elapsed_days >= 0.01).then_some(written_bytes as f64 / elapsed_days));
    let days_remaining = bytes_per_day
        .filter(|rate| *rate > 0.0)
        .map(|rate| free_bytes as f64 / rate);
    let warning = match days_remaining {
        Some(days) if days <= 7.0 => Some("RETENTION_7_DAY_WARNING"),
        Some(days) if days <= 14.0 => Some("RETENTION_14_DAY_WARNING"),
        Some(days) if days <= 30.0 => Some("RETENTION_30_DAY_WARNING"),
        _ => None,
    };
    let now_ms = now_unix_ms();
    let raw_update_ms = nonzero(stats.last_raw_stream_update_unix_ms.load(Ordering::Relaxed));
    let finality_ms = nonzero(
        stats
            .last_finalized_reconciliation_unix_ms
            .load(Ordering::Relaxed),
    );
    let append_ms = nonzero(stats.last_partition_append_unix_ms.load(Ordering::Relaxed));
    let raw_age_ms = raw_update_ms.map(|value| now_ms.saturating_sub(value));
    let finality_age_ms = finality_ms.map(|value| now_ms.saturating_sub(value));
    let append_age_ms = append_ms.map(|value| now_ms.saturating_sub(value));
    let stream_connected = stats.stream_connected.load(Ordering::Relaxed);
    let evidence_flow_healthy = stream_connected
        && raw_age_ms.is_some_and(|age| age <= cfg.health_stale_ms)
        && append_age_ms.is_some_and(|age| age <= cfg.health_stale_ms);
    let health = Health {
        schema_version: SCHEMA_VERSION,
        at_utc: Utc::now().to_rfc3339(),
        endpoint_label: endpoint_label(&cfg.endpoint),
        endpoint_identity: &identity.endpoint_identity,
        pid: identity.pid,
        process_instance_uuid: &identity.process_instance_uuid,
        startup_time_utc: &identity.startup_time_utc,
        process_alive: true,
        evidence_flow_healthy,
        evidence_flow_state: if evidence_flow_healthy {
            "EVIDENCE_FLOW_HEALTHY"
        } else {
            "EVIDENCE_FLOW_UNHEALTHY"
        },
        stream_connected,
        stream_stale_threshold_ms: cfg.stream_stale_ms,
        health_stale_threshold_ms: cfg.health_stale_ms,
        last_raw_stream_update_utc: raw_update_ms.and_then(unix_ms_to_utc),
        last_raw_stream_slot: nonzero(stats.last_raw_stream_slot.load(Ordering::Relaxed)),
        last_finalized_reconciliation_utc: finality_ms.and_then(unix_ms_to_utc),
        last_partition_append_utc: append_ms.and_then(unix_ms_to_utc),
        last_exact_complete_partition_utc: newest_exact_complete_partition_utc(&cfg.root),
        raw_stream_age_ms: raw_age_ms,
        finalized_reconciliation_age_ms: finality_age_ms,
        partition_append_age_ms: append_age_ms,
        reconciliation_backlog: stats.reconciliation_backlog.load(Ordering::Relaxed),
        received: stats.received.load(Ordering::Relaxed),
        queued: stats.queued.load(Ordering::Relaxed),
        written: stats.written.load(Ordering::Relaxed),
        duplicates_suppressed: stats.duplicates.load(Ordering::Relaxed),
        reconnects: stats.reconnects.load(Ordering::Relaxed),
        queue_capacity: cfg.queue_capacity,
        queue_high_water: stats.queue_high_water.load(Ordering::Relaxed),
        dedupe_entries: writer.dedupe_entries(),
        current_partition_coverage: writer.current_coverage(),
        free_bytes,
        estimated_bytes_per_day: bytes_per_day,
        estimated_days_remaining: days_remaining,
        storage_warning: warning,
        rpc_snapshot_requests: stats.rpc_snapshot_requests.load(Ordering::Relaxed),
        rpc_snapshot_failures: stats.rpc_snapshot_failures.load(Ordering::Relaxed),
        rpc_finality_requests: stats.rpc_finality_requests.load(Ordering::Relaxed),
        rpc_finality_failures: stats.rpc_finality_failures.load(Ordering::Relaxed),
        rpc_block_requests: stats.rpc_block_requests.load(Ordering::Relaxed),
        rpc_block_failures: stats.rpc_block_failures.load(Ordering::Relaxed),
    };
    atomic_write(
        &cfg.root.join("health.json"),
        &serde_json::to_vec_pretty(&health)?,
    )?;
    if let Some(warning) = warning {
        eprintln!("pump-exact-ledger: {warning} daysRemaining={days_remaining:?}");
    }
    if !evidence_flow_healthy {
        eprintln!(
            "pump-exact-ledger: EVIDENCE_FLOW_UNHEALTHY streamConnected={} rawAgeMs={raw_age_ms:?} appendAgeMs={append_age_ms:?}",
            stream_connected
        );
    }
    Ok(())
}

fn now_unix_ms() -> u64 {
    Utc::now().timestamp_millis().max(0) as u64
}

fn nonzero(value: u64) -> Option<u64> {
    (value != 0).then_some(value)
}

fn resume_slot(root: &Path) -> Option<u64> {
    ["state/last-raw-stream-slot", "state/last-finalized-slot"]
        .iter()
        .find_map(|relative| {
            fs::read_to_string(root.join(relative))
                .ok()
                .and_then(|value| value.trim().parse::<u64>().ok())
        })
}

fn unix_ms_to_utc(value: u64) -> Option<String> {
    DateTime::<Utc>::from_timestamp_millis(value as i64).map(|value| value.to_rfc3339())
}

fn newest_exact_complete_partition_utc(root: &Path) -> Option<String> {
    let mut newest = None;
    let mut stack = vec![root.join("partitions")];
    while let Some(path) = stack.pop() {
        let Ok(entries) = fs::read_dir(path) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if entry.file_type().ok().is_some_and(|kind| kind.is_dir()) {
                stack.push(path);
                continue;
            }
            if entry.file_name() != "reconciliation-manifest.json" {
                continue;
            }
            let Ok(bytes) = fs::read(path) else {
                continue;
            };
            let Ok(value) = serde_json::from_slice::<Value>(&bytes) else {
                continue;
            };
            if value.get("exactReplayComplete").and_then(Value::as_bool) != Some(true) {
                continue;
            }
            let candidate = value
                .get("generatedAtUtc")
                .or_else(|| value.get("completedAtUtc"))
                .or_else(|| value.get("reconciledAtUtc"))
                .and_then(Value::as_str)
                .map(str::to_string);
            if candidate.as_ref() > newest.as_ref() {
                newest = candidate;
            }
        }
    }
    newest
}

fn directory_size(path: &Path) -> Result<u64> {
    if !path.exists() {
        return Ok(0);
    }
    let mut total = 0u64;
    let mut stack = vec![path.to_path_buf()];
    while let Some(path) = stack.pop() {
        for entry in fs::read_dir(path)? {
            let entry = entry?;
            let metadata = entry.metadata()?;
            if metadata.is_dir() {
                stack.push(entry.path());
            } else {
                total = total.saturating_add(metadata.len());
            }
        }
    }
    Ok(total)
}

fn estimate_closed_partition_bytes_per_day(root: &Path) -> Option<f64> {
    let mut compressed_bytes = 0u64;
    let mut elapsed_seconds = 0f64;
    let mut stack = vec![root.join("partitions")];
    while let Some(path) = stack.pop() {
        let entries = fs::read_dir(path).ok()?;
        for entry in entries.flatten() {
            let file_type = entry.file_type().ok()?;
            if file_type.is_dir() {
                stack.push(entry.path());
                continue;
            }
            if entry.file_name() != "manifest.json" {
                continue;
            }
            let manifest: PartitionManifest =
                serde_json::from_slice(&fs::read(entry.path()).ok()?).ok()?;
            if !(manifest.temporal_hour_closed || manifest.complete_utc_hour) {
                continue;
            }
            let opened = DateTime::parse_from_rfc3339(&manifest.opened_at_utc).ok()?;
            let closed = DateTime::parse_from_rfc3339(&manifest.closed_at_utc).ok()?;
            let seconds = (closed - opened).num_milliseconds() as f64 / 1000.0;
            if seconds > 0.0 {
                compressed_bytes = compressed_bytes.saturating_add(manifest.compressed_bytes);
                elapsed_seconds += seconds;
            }
        }
    }
    (elapsed_seconds >= 300.0).then_some(compressed_bytes as f64 * 86_400.0 / elapsed_seconds)
}

fn endpoint_label(endpoint: &str) -> String {
    endpoint
        .split("//")
        .nth(1)
        .unwrap_or(endpoint)
        .split('/')
        .next()
        .unwrap_or("unknown")
        .to_string()
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("tmp");
    fs::write(&tmp, bytes)?;
    fs::rename(tmp, path)?;
    Ok(())
}

fn verify_partitions(root: &Path) -> Result<()> {
    let mut manifests = Vec::new();
    let mut stack = vec![root.join("partitions")];
    while let Some(path) = stack.pop() {
        if !path.exists() {
            continue;
        }
        for entry in fs::read_dir(path)? {
            let entry = entry?;
            if entry.file_type()?.is_dir() {
                stack.push(entry.path());
            } else if entry.file_name() == "manifest.json" {
                manifests.push(entry.path());
            }
        }
    }
    manifests.sort();
    for path in &manifests {
        let manifest: PartitionManifest = serde_json::from_slice(&fs::read(path)?)?;
        let compressed = path.with_file_name("evidence.frames.zst");
        if file_sha256(&compressed)? != manifest.compressed_sha256 {
            anyhow::bail!("compressed checksum mismatch: {}", compressed.display());
        }
        let mut decoder = zstd::Decoder::new(fs::File::open(&compressed)?)?;
        let mut hasher = sha2::Sha256::new();
        let bytes = std::io::copy(&mut decoder, &mut HashWriter(&mut hasher))?;
        let raw_hash: String = hasher
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        if bytes != manifest.raw_frame_bytes || raw_hash != manifest.raw_sha256 {
            anyhow::bail!(
                "raw checksum mismatch after decompression: {}",
                compressed.display()
            );
        }
    }
    println!(
        "verifiedPartitions={} schemaHash={}",
        manifests.len(),
        sha256_hex(SCHEMA_DOCUMENT.as_bytes())
    );
    Ok(())
}

struct HashWriter<'a>(&'a mut sha2::Sha256);
impl std::io::Write for HashWriter<'_> {
    fn write(&mut self, buffer: &[u8]) -> std::io::Result<usize> {
        self.0.update(buffer);
        Ok(buffer.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use yellowstone_grpc_proto::{
        geyser::{
            subscribe_update::UpdateOneof, SubscribeUpdateTransaction,
            SubscribeUpdateTransactionInfo,
        },
        solana::storage::confirmed_block::{
            CompiledInstruction, Message as SolanaMessage, TokenBalance, Transaction,
            TransactionStatusMeta, UiTokenAmount,
        },
    };

    fn pubkey(value: &str) -> Vec<u8> {
        bs58::decode(value).into_vec().unwrap()
    }

    #[test]
    fn cli_requires_explicit_valid_run_mode() {
        assert!(parse_cli(Vec::<String>::new()).is_err());
        assert!(parse_cli(["--wat"]).is_err());
        assert!(parse_cli(["run", "unexpected"]).is_err());
        assert_eq!(parse_cli(["run"]).unwrap(), CliCommand::Run);
        assert_eq!(parse_cli(["--help"]).unwrap(), CliCommand::Help);
    }

    #[test]
    fn a_second_writer_for_the_same_root_fails_closed() {
        let dir = tempfile::tempdir().unwrap();
        let first = acquire_writer_lock(dir.path()).unwrap();
        assert!(acquire_writer_lock(dir.path()).is_err());
        drop(first);
        assert!(acquire_writer_lock(dir.path()).is_ok());
    }

    #[test]
    fn subscription_is_finalized_and_includes_failures() {
        let request = build_request(Some(42));
        assert_eq!(request.commitment, Some(CommitmentLevel::Finalized as i32));
        assert_eq!(request.from_slot, Some(42));
        let transactions = request.transactions.get("pumpTransactions").unwrap();
        assert_eq!(transactions.failed, None);
        assert_eq!(
            transactions.account_include,
            vec![
                PUMP_PROGRAM.to_string(),
                PUMP_CPI_ROUTER_PROGRAM.to_string(),
            ]
        );
        assert!(!request.transactions.contains_key("pumpCpiRouterTransactions"));
        assert!(request.accounts.contains_key("pumpFeeConfigAccounts"));
        assert!(request
            .accounts
            .contains_key("pumpMayhemProgramOwnedAccounts"));
        assert_eq!(
            request
                .transactions
                .get("pumpMayhemTransactions")
                .unwrap()
                .account_include,
            vec![PUMP_MAYHEM_PROGRAM.to_string()]
        );
        assert!(request.blocks_meta.contains_key("blockMeta"));
    }

    #[test]
    fn exact_ledger_emits_a_canonical_two_cq_buy_candidate() {
        let mint = "25nnDGFBzwr8R4UU2hqHhXoyKCshhRk4b32VAqBApump";
        let mut data = BUY_EXACT_SOL_IN.to_vec();
        data.extend_from_slice(&3_000_000_000u64.to_le_bytes());
        data.extend_from_slice(&50_000_000_000_000u64.to_le_bytes());
        let update = SubscribeUpdate {
            filters: vec!["pumpTransactions".to_string()],
            created_at: None,
            update_oneof: Some(UpdateOneof::Transaction(SubscribeUpdateTransaction {
                slot: 445_461_497,
                transaction: Some(SubscribeUpdateTransactionInfo {
                    signature: vec![7; 64],
                    is_vote: false,
                    index: 3,
                    transaction: Some(Transaction {
                        signatures: vec![vec![7; 64]],
                        message: Some(SolanaMessage {
                            account_keys: vec![
                                pubkey(PUMP_PROGRAM),
                                pubkey(mint),
                                pubkey(TWO_CQ_WALLET),
                            ],
                            instructions: vec![CompiledInstruction {
                                program_id_index: 0,
                                accounts: vec![1, 1, 1, 1, 1, 1, 2],
                                data: data.clone(),
                            }],
                            ..Default::default()
                        }),
                    }),
                    meta: Some(TransactionStatusMeta {
                        post_token_balances: vec![TokenBalance {
                            account_index: 1,
                            mint: mint.to_string(),
                            ui_token_amount: Some(UiTokenAmount {
                                amount: "123".to_string(),
                                ..Default::default()
                            }),
                            owner: TWO_CQ_WALLET.to_string(),
                            program_id: "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb".to_string(),
                        }],
                        ..Default::default()
                    }),
                }),
            })),
        };
        let detected = extract_two_cq_candidates(&update, Utc::now());
        assert_eq!(detected.len(), 1);
        assert_eq!(detected[0].output_mint, mint);
        assert_eq!(detected[0].amount_raw, Some(3_000_000_000));
        assert_eq!(
            detected[0].source_ix_data_base64,
            BASE64_STANDARD.encode(data)
        );
        assert_eq!(detected[0].source_transaction_index, 3);
    }

    #[test]
    fn exact_ledger_detector_matches_a_real_historical_two_cq_buy() {
        let raw = include_bytes!("../tests/fixtures/2cq-known-buy.protobuf");
        let update = SubscribeUpdate::decode(raw.as_slice()).unwrap();
        let detected = extract_two_cq_candidates(&update, Utc::now());
        assert_eq!(detected.len(), 1);
        assert_eq!(
            detected[0].signature,
            "KT4ct2kQyy3G9n2X6g1o6Gju3EBH9Co7mArMMdq7z1PgYyy7ioDFZdLzuJnD7S8FqUdCfdLrqEDH3MAwSWXMqNm"
        );
        assert_eq!(
            detected[0].output_mint,
            "6f82eroW981nNwUQ5hWoEi5cShXRT1FWsL8pABAjpump"
        );
        assert_eq!(detected[0].source_slot, 445_433_845);
        assert_eq!(detected[0].source_transaction_index, 0);
        assert!(detected[0]
            .source_ix_accounts
            .iter()
            .any(|account| account == TWO_CQ_WALLET));
    }

    #[tokio::test]
    async fn queue_is_bounded_and_applies_collector_only_backpressure() {
        let (tx, mut rx) = mpsc::channel::<u8>(1);
        tx.send(1).await.unwrap();
        assert!(tokio::time::timeout(Duration::from_millis(10), tx.send(2))
            .await
            .is_err());
        assert_eq!(rx.recv().await, Some(1));
        assert!(tx.send(2).await.is_ok());
    }

    #[tokio::test]
    async fn pending_stream_hits_the_stale_deadline() {
        let mut pending = stream::pending::<Result<SubscribeUpdate, Status>>();
        let result = tokio::time::timeout(Duration::from_millis(10), pending.next()).await;
        assert!(result.is_err());
    }

    #[test]
    fn reconciliation_backlog_guard_decrements_exactly_once() {
        let stats = Arc::new(RuntimeStats::default());
        stats.reconciliation_backlog.store(2, Ordering::Relaxed);
        {
            let _guard = ReconciliationBacklogGuard(stats.clone());
        }
        assert_eq!(stats.reconciliation_backlog.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn reconciliation_backlog_guard_cannot_underflow() {
        let stats = Arc::new(RuntimeStats::default());
        {
            let _guard = ReconciliationBacklogGuard(stats.clone());
        }
        assert_eq!(stats.reconciliation_backlog.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn raw_stream_cursor_takes_precedence_over_legacy_finalized_cursor() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir_all(dir.path().join("state")).unwrap();
        fs::write(dir.path().join("state/last-finalized-slot"), b"200").unwrap();
        fs::write(dir.path().join("state/last-raw-stream-slot"), b"150").unwrap();
        assert_eq!(resume_slot(dir.path()), Some(150));
    }

    #[test]
    fn raw_stream_cursor_falls_back_for_pre_upgrade_state() {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir_all(dir.path().join("state")).unwrap();
        fs::write(dir.path().join("state/last-finalized-slot"), b"200").unwrap();
        assert_eq!(resume_slot(dir.path()), Some(200));
    }

    #[test]
    fn failed_rpc_snapshot_is_explicitly_unresolved() {
        let snapshot = RpcMintSnapshot {
            mint: "mint".to_string(),
            min_context_slot: 10,
            raw_json: br#"{"error":"unavailable"}"#.to_vec(),
            context_slot: None,
            owner: None,
            successful: false,
        };
        let metadata = summarize_rpc_snapshot(&snapshot, Utc::now(), 1);
        assert!(metadata.unresolved);
        assert!(!metadata.finalized);
        assert_eq!(metadata.successful, Some(false));
        assert!(metadata.decode_failed);
    }

    #[test]
    fn retention_rate_uses_closed_compressed_partitions() {
        let dir = tempfile::tempdir().unwrap();
        let partition = dir.path().join("partitions/2026/09/06/05");
        fs::create_dir_all(&partition).unwrap();
        let manifest = PartitionManifest {
            schema_version: SCHEMA_VERSION.to_string(),
            schema_sha256: sha256_hex(SCHEMA_DOCUMENT.as_bytes()),
            partition_key: "2026/09/06/05".to_string(),
            complete_utc_hour: false,
            temporal_hour_closed: true,
            exact_replay_complete: false,
            reconciliation_manifest: Some("reconciliation-manifest.json".to_string()),
            opened_at_utc: "2026-09-06T05:50:00Z".to_string(),
            closed_at_utc: "2026-09-06T06:00:00Z".to_string(),
            start_slot: Some(1),
            end_slot: Some(2),
            raw_frame_bytes: 1_000,
            compressed_bytes: 100,
            raw_sha256: "raw".to_string(),
            compressed_sha256: "zst".to_string(),
            coverage: CoverageCounters::default(),
        };
        fs::write(
            partition.join("manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();
        let rate = estimate_closed_partition_bytes_per_day(dir.path()).unwrap();
        assert!((rate - 14_400.0).abs() < f64::EPSILON);
    }
}
