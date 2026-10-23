use std::{
    collections::{HashMap, HashSet, VecDeque},
    env,
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
    sync::Arc,
    time::Duration,
};

use anyhow::{Context, Result};
use chrono::{TimeZone, Utc};
use futures::{stream, StreamExt};
use prost::Message;
use pump_exact_evidence_ledger::{sha256_hex, EvidenceMetadata, FrameRecord, LedgerWriter, SCHEMA_VERSION};
use serde::Serialize;
use tokio::sync::{mpsc, Mutex};
use tonic::{metadata::AsciiMetadataValue, transport::{Channel, ClientTlsConfig}, Request, Status};
use yellowstone_grpc_proto::geyser::{
    geyser_client::GeyserClient, subscribe_update::UpdateOneof, CommitmentLevel, SubscribeRequest,
    SubscribeRequestFilterTransactions,
    SubscribeUpdate,
};

const PUMP_PROGRAM: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const PUMP_CPI_ROUTER_PROGRAM: &str = "6Vo3245eszAb5wuqEMw8mGdbfRUdKbHhDHP5LcaGuTAB";
const PUMPSWAP_PROGRAM: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const DEFAULT_SHARED_BURST_QUIET_SECONDS: i64 = 12;
const DEFAULT_SHARED_BURST_MAX_SECONDS: i64 = 120;
const DEFAULT_RECOVERY_OVERLAP_SLOTS: u64 = 64;
const DEFAULT_HTTP2_KEEPALIVE_SECONDS: u64 = 15;
const DEFAULT_INGEST_QUEUE_CAPACITY: usize = 32_768;
const DEFAULT_SYNC_BATCH_FRAMES: u64 = 2_048;
const DEFAULT_SYNC_INTERVAL_SECONDS: u64 = 2;
const DEFAULT_CAPTURE_RING_MAX_BYTES: u64 = 8 * 1024 * 1024 * 1024;
const CAPTURE_RING_SAFETY_SECONDS: i64 = 5;

#[derive(Default, Serialize)]
#[serde(rename_all = "camelCase")]
struct Health {
    schema: String,
    source: String,
    endpoint: String,
    started_at_utc: String,
    current_epoch: u64,
    reconnects: u64,
    receive_errors: u64,
    frames_received: u64,
    frames_meta_complete: u64,
    frames_meta_incomplete: u64,
    direct_pump_frames: u64,
    router_cpi_frames: u64,
    direct_pumpswap_frames: u64,
    latest_received_at_utc: Option<String>,
    latest_slot: Option<u64>,
    replay_requested_from_slot: Option<u64>,
    replayed_duplicates_discarded: u64,
    last_sync_at_utc: Option<String>,
    continuity_gaps: u64,
    tracked_wallet: String,
    tracked_wallet_label: String,
    tracked_wallet_pump_candidates: u64,
    rolling_window_promotions: u64,
    rolling_window_promotions_queued: u64,
    rolling_window_promotion_failures: u64,
    rolling_window_queue_full: u64,
    shared_burst_groups_completed: u64,
    shared_burst_groups_failed: u64,
    rolling_warmup_candidates_skipped: u64,
    two_cq_wallet_pump_candidates: u64,
    ingest_queue_peak: u64,
    capture_first_mode: bool,
    rolling_memory_bytes: u64,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct TwoCqCandidate {
    schema: String,
    source: String,
    tracked_wallet: String,
    tracked_wallet_label: String,
    signature: String,
    observed_slot: u64,
    observed_transaction_index: u64,
    received_at_utc: String,
    received_at_unix_ns: i64,
    continuity_epoch: u64,
    evidence_id: String,
    rolling_window_promoted: bool,
    rolling_window_warmup_complete: bool,
    rolling_window_promotion_state: String,
    shared_burst_group_id: Option<String>,
}

struct SharedBurstGroup {
    id: String,
    capture_start_unix_ns: i64,
    close_deadline_unix_ns: i64,
    hard_deadline_unix_ns: i64,
    candidates: Vec<TwoCqCandidate>,
}

struct PromotionJob {
    group: SharedBurstGroup,
    records: Vec<Arc<FrameRecord>>,
}

struct PromotionCompletion {
    group_id: String,
    candidate_count: usize,
    completed: bool,
}

fn write_promotion_result(root: &PathBuf, group: &SharedBurstGroup, candidate: &TwoCqCandidate, status: &str, error: Option<String>) -> Result<()> {
    let results = root.join("promotion-results");
    fs::create_dir_all(&results)?;
    let result_path = results.join(format!("{}.json", candidate.signature));
    let temporary = results.join(format!(".{}-result.tmp", candidate.signature));
    let payload = serde_json::json!({
        "schema": "TRACKED_WALLET_SHARED_BURST_PROMOTION_RESULT_V1",
        "signature": candidate.signature,
        "status": status,
        "completedAtUtc": Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
        "windowRoot": format!("shared-windows/{}", group.id),
        "sharedBurstGroupId": group.id,
        "error": error,
    });
    fs::write(&temporary, serde_json::to_vec_pretty(&payload)?)?;
    fs::rename(temporary, result_path)?;
    Ok(())
}

fn write_source_spool_request(root: &PathBuf, candidate: &TwoCqCandidate) -> Result<()> {
    let requests = root.join("source-spool-requests");
    fs::create_dir_all(&requests)?;
    let path = requests.join(format!("{}.json", candidate.signature));
    let temporary = requests.join(format!(".{}-request.tmp", candidate.signature));
    let payload = serde_json::json!({
        "schema": "TRACKED_WALLET_SOURCE_SPOOL_REQUEST_V1",
        "signature": candidate.signature,
        "candidate": candidate,
        "sourceSpoolRoot": "source-spool",
        "strictPreEntryStartUnixNs": candidate.received_at_unix_ns - 120 * 1_000_000_000_i64,
        "strictPreEntryEndUnixNs": candidate.received_at_unix_ns,
        "postEntryDataMustBeExcluded": true,
        "status": "PENDING_CLOSED_SOURCE_PARTITIONS",
    });
    fs::write(&temporary, serde_json::to_vec_pretty(&payload)?)?;
    fs::rename(temporary, path)?;
    Ok(())
}

fn promote_shared_burst_window(root: &PathBuf, group: &SharedBurstGroup, records: &[Arc<FrameRecord>]) -> Result<()> {
    let window_root = root.join("shared-windows").join(&group.id);
    let partial_root = root.join("shared-windows").join(format!(".partial-{}", group.id));
    if window_root.exists() || partial_root.exists() {
        anyhow::bail!("shared burst path already exists for {}", group.id);
    }
    let mut writer = LedgerWriter::open(&partial_root)?;
    for record in records {
        let timestamp = Utc.timestamp_nanos(record.metadata.received_at_unix_ns);
        writer.append_buffered(timestamp, (**record).clone())?;
    }
    writer.sync_buffered()?;
    writer.finalize_current(true)?;
    let manifest = serde_json::json!({
        "schema": "TRACKED_WALLET_SHARED_BURST_WINDOW_V1",
        "outcomeBlind": true,
        "sharedBurstGroupId": group.id,
        "candidates": group.candidates,
        "framesCopied": records.len(),
        "requestedCaptureStartNs": group.capture_start_unix_ns,
        "requestedCaptureEndNs": group.close_deadline_unix_ns,
        "actualFrameStartNs": records.first().map(|record| record.metadata.received_at_unix_ns),
        "actualFrameEndNs": records.last().map(|record| record.metadata.received_at_unix_ns),
        "rawTruth": "yellowstone_subscribe_update_protobuf_full_meta",
        "preEntryWindowSeconds": 120,
        "postEntryDataMustBeExcludedPerCandidate": true,
    });
    let temporary = partial_root.join("manifest.json.tmp");
    fs::write(&temporary, serde_json::to_vec_pretty(&manifest)?)?;
    fs::rename(temporary, partial_root.join("manifest.json"))?;
    // Only a fully sealed ledger plus manifest becomes visible to consumers.
    fs::rename(partial_root, window_root)?;
    Ok(())
}

fn request(from_slot: Option<u64>) -> SubscribeRequest {
    let mut transactions = HashMap::new();
    // Yellowstone evaluates account_include as OR within this one named filter.
    // PumpSwap is a separate program after Pump graduation, so it must be named
    // explicitly or direct graduated-token swaps never reach the wallet detector.
    transactions.insert(
        "pumpTransactions".to_string(),
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: None,
            signature: None,
            account_include: vec![
                PUMP_PROGRAM.to_string(),
                PUMP_CPI_ROUTER_PROGRAM.to_string(),
                PUMPSWAP_PROGRAM.to_string(),
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
        from_slot,
    }
}

// Shyft can accept a subscription and then reject a `from_slot` replay on the
// first receive with this provider-specific status. The preceding interval is
// already a hard scientific wall; retrying the same unavailable slot forever
// only turns one wall into a restart loop. Fall back once to a live stream.
fn replay_position_unavailable(error: &str) -> bool {
    let normalized = error.to_ascii_lowercase();
    normalized.contains("broadcast from") && normalized.contains("not available")
        || normalized.contains("operation was attempted past the valid range")
}

#[cfg(test)]
mod replay_recovery_tests {
    use super::replay_position_unavailable;

    #[test]
    fn recognizes_shyft_unavailable_replay_position() {
        assert!(replay_position_unavailable(
            "Operation was attempted past the valid range: broadcast from 123 is not available"
        ));
    }

    #[test]
    fn does_not_treat_generic_transport_error_as_replay_position_failure() {
        assert!(!replay_position_unavailable(
            "h2 protocol error: stream closed because of a broken pipe"
        ));
    }
}

fn has_key_bytes(
    info: &yellowstone_grpc_proto::prelude::SubscribeUpdateTransactionInfo,
    key: &[u8],
) -> bool {
    let Some(transaction) = info.transaction.as_ref() else { return false };
    let Some(message) = transaction.message.as_ref() else { return false };
    if message
        .account_keys
        .iter()
        .any(|candidate| candidate.as_slice() == key)
    {
        return true;
    }
    let Some(meta) = info.meta.as_ref() else { return false };
    meta.loaded_writable_addresses
        .iter()
        .chain(meta.loaded_readonly_addresses.iter())
        .any(|candidate| candidate.as_slice() == key)
}

fn write_health(path: &PathBuf, health: &Health) -> Result<()> {
    let tmp = path.with_extension("tmp");
    fs::write(&tmp, serde_json::to_vec_pretty(health)?)?;
    fs::rename(tmp, path)?;
    Ok(())
}

fn rolling_window_warmup_complete(started_at_unix_ns: Option<i64>, received_at_unix_ns: i64, window_seconds: Option<i64>) -> bool {
    match (started_at_unix_ns, window_seconds) {
        (_, None) => true,
        (Some(started_at), Some(seconds)) => started_at <= received_at_unix_ns - seconds * 1_000_000_000,
        (None, Some(_)) => false,
    }
}

// Keep a small pre-window anchor while a burst is active. The anchor proves the
// in-memory ring did not begin after the requested causal window; it is not
// emitted into the scientific window itself.
fn capture_retained_from(active_capture_start_unix_ns: Option<i64>, received_at_unix_ns: i64, window_seconds: i64) -> i64 {
    active_capture_start_unix_ns
        .map(|start| start.saturating_sub(CAPTURE_RING_SAFETY_SECONDS * 1_000_000_000_i64))
        .unwrap_or(received_at_unix_ns - (window_seconds + CAPTURE_RING_SAFETY_SECONDS) * 1_000_000_000_i64)
}

#[cfg(test)]
mod tests {
    use super::{capture_retained_from, rolling_window_warmup_complete};

    #[test]
    fn rolling_warmup_uses_epoch_start_not_trimmed_queue_front() {
        let start = 1_000_000_000_000_i64;
        assert!(!rolling_window_warmup_complete(Some(start), start + 119_999_999_999, Some(120)));
        assert!(rolling_window_warmup_complete(Some(start), start + 120_000_000_000, Some(120)));
        assert!(!rolling_window_warmup_complete(None, start + 120_000_000_000, Some(120)));
        assert!(rolling_window_warmup_complete(None, start, None));
    }

    #[test]
    fn active_burst_keeps_a_pre_window_anchor() {
        let second = 1_000_000_000_i64;
        assert_eq!(capture_retained_from(Some(1_000 * second), 1_100 * second, 120), 995 * second);
        assert_eq!(capture_retained_from(None, 1_100 * second, 120), 975 * second);
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let endpoint = env::var("YELLOWSTONE_FULL_META_ENDPOINT")
        .context("YELLOWSTONE_FULL_META_ENDPOINT missing")?;
    let token: AsciiMetadataValue = env::var("SHYFT_GRPC_X_TOKEN")?
        .parse()
        .context("invalid x-token")?;
    let root = PathBuf::from(
        env::var("YELLOWSTONE_FULL_META_ROOT").context("YELLOWSTONE_FULL_META_ROOT missing")?,
    );
    let default_two_cq_wallet = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG".to_string();
    // Keep the historical 2CQ defaults intact, while allowing a separately
    // named sibling lane to use the same outcome-blind full-meta transport.
    let tracked_wallet = env::var("TRACKED_WALLET")
        .or_else(|_| env::var("TWO_CQ_WALLET"))
        .unwrap_or_else(|_| default_two_cq_wallet.clone());
    let tracked_wallet_label = env::var("TRACKED_WALLET_LABEL")
        .unwrap_or_else(|_| "two-cq".to_string());
    let candidate_file = env::var("TRACKED_WALLET_CANDIDATE_FILE")
        .unwrap_or_else(|_| "2cq-wallet-pump-candidates.jsonl".to_string());
    let candidate_schema = env::var("TRACKED_WALLET_CANDIDATE_SCHEMA")
        .unwrap_or_else(|_| "2CQ_FULL_META_WALLET_PUMP_CANDIDATE_V1".to_string());
    let candidate_source = env::var("TRACKED_WALLET_SOURCE")
        .unwrap_or_else(|_| "shyft-yellowstone-confirmed-full-meta".to_string());
    // This is the receive hot path. Decode static base58 identifiers once and
    // compare canonical key bytes per frame instead of repeatedly converting
    // every account key back to base58 under a high-volume subscription.
    let tracked_wallet_bytes = bs58::decode(&tracked_wallet)
        .into_vec()
        .context("invalid TRACKED_WALLET base58")?;
    let pump_program_bytes = bs58::decode(PUMP_PROGRAM)
        .into_vec()
        .context("invalid Pump program base58")?;
    let router_program_bytes = bs58::decode(PUMP_CPI_ROUTER_PROGRAM)
        .into_vec()
        .context("invalid Pump router program base58")?;
    let pumpswap_program_bytes = bs58::decode(PUMPSWAP_PROGRAM)
        .into_vec()
        .context("invalid PumpSwap program base58")?;
    fs::create_dir_all(&root)?;
    let health_path = root.join("health.json");
    let rolling_window_seconds = env::var("TRACKED_WALLET_ROLLING_WINDOW_SECONDS")
        .ok()
        .map(|value| value.parse::<i64>())
        .transpose()
        .context("invalid TRACKED_WALLET_ROLLING_WINDOW_SECONDS")?;
    let capture_first_mode = env::var("TRACKED_WALLET_CAPTURE_FIRST")
        .ok()
        .is_some_and(|value| matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES"));
    if capture_first_mode && rolling_window_seconds.is_none() {
        anyhow::bail!("TRACKED_WALLET_CAPTURE_FIRST requires TRACKED_WALLET_ROLLING_WINDOW_SECONDS");
    }
    let capture_ring_max_bytes = env::var("TRACKED_WALLET_CAPTURE_RING_MAX_BYTES")
        .ok()
        .map(|value| value.parse::<u64>())
        .transpose()
        .context("invalid TRACKED_WALLET_CAPTURE_RING_MAX_BYTES")?
        .unwrap_or(DEFAULT_CAPTURE_RING_MAX_BYTES);
    if capture_first_mode && !(512 * 1024 * 1024..=16 * 1024 * 1024 * 1024).contains(&capture_ring_max_bytes) {
        anyhow::bail!("TRACKED_WALLET_CAPTURE_RING_MAX_BYTES must be 512MiB..16GiB");
    }
    let shared_burst_quiet_seconds = env::var("TRACKED_WALLET_SHARED_BURST_QUIET_SECONDS")
        .ok()
        .map(|value| value.parse::<i64>())
        .transpose()
        .context("invalid TRACKED_WALLET_SHARED_BURST_QUIET_SECONDS")?
        .unwrap_or(DEFAULT_SHARED_BURST_QUIET_SECONDS);
    let shared_burst_max_seconds = env::var("TRACKED_WALLET_SHARED_BURST_MAX_SECONDS")
        .ok()
        .map(|value| value.parse::<i64>())
        .transpose()
        .context("invalid TRACKED_WALLET_SHARED_BURST_MAX_SECONDS")?
        .unwrap_or(DEFAULT_SHARED_BURST_MAX_SECONDS);
    if rolling_window_seconds.is_some()
        && (shared_burst_quiet_seconds <= 0 || shared_burst_max_seconds < shared_burst_quiet_seconds)
    {
        anyhow::bail!("invalid shared burst timing configuration");
    }
    let shared_burst_promotion_workers = env::var("TRACKED_WALLET_SHARED_BURST_PROMOTION_WORKERS")
        .ok()
        .map(|value| value.parse::<usize>())
        .transpose()
        .context("invalid TRACKED_WALLET_SHARED_BURST_PROMOTION_WORKERS")?
        .unwrap_or(2);
    if !(1..=4).contains(&shared_burst_promotion_workers) {
        anyhow::bail!("shared burst promotion workers must be between 1 and 4");
    }
    let recovery_overlap_slots = env::var("YELLOWSTONE_RECOVERY_OVERLAP_SLOTS")
        .ok()
        .map(|value| value.parse::<u64>())
        .transpose()
        .context("invalid YELLOWSTONE_RECOVERY_OVERLAP_SLOTS")?
        .unwrap_or(DEFAULT_RECOVERY_OVERLAP_SLOTS);
    if !(16..=512).contains(&recovery_overlap_slots) {
        anyhow::bail!("YELLOWSTONE_RECOVERY_OVERLAP_SLOTS must be 16..=512");
    }
    // Never let synchronous hashing or filesystem durability work stop the
    // network reader. The FIFO preserves source order and each item carries
    // its original receive timestamp for scientific provenance.
    let ingest_queue_capacity = env::var("YELLOWSTONE_INGEST_QUEUE_CAPACITY")
        .ok()
        .map(|value| value.parse::<usize>())
        .transpose()
        .context("invalid YELLOWSTONE_INGEST_QUEUE_CAPACITY")?
        .unwrap_or(DEFAULT_INGEST_QUEUE_CAPACITY);
    if !(1_024..=65_536).contains(&ingest_queue_capacity) {
        anyhow::bail!("YELLOWSTONE_INGEST_QUEUE_CAPACITY must be 1024..=65536");
    }
    // Bounded group commit keeps the full-meta writer crash-safe without
    // forcing an fsync on the receive path for every small burst.
    let sync_batch_frames = env::var("YELLOWSTONE_SYNC_BATCH_FRAMES")
        .ok()
        .map(|value| value.parse::<u64>())
        .transpose()
        .context("invalid YELLOWSTONE_SYNC_BATCH_FRAMES")?
        .unwrap_or(DEFAULT_SYNC_BATCH_FRAMES);
    let sync_interval_seconds = env::var("YELLOWSTONE_SYNC_INTERVAL_SECONDS")
        .ok()
        .map(|value| value.parse::<u64>())
        .transpose()
        .context("invalid YELLOWSTONE_SYNC_INTERVAL_SECONDS")?
        .unwrap_or(DEFAULT_SYNC_INTERVAL_SECONDS);
    if !(256..=16_384).contains(&sync_batch_frames) || !(1..=10).contains(&sync_interval_seconds) {
        anyhow::bail!("invalid bounded group-commit configuration");
    }
    // Some Yellowstone providers reset an otherwise healthy subscription when
    // clients send frequent HTTP/2 PING frames. The external freshness watchdog
    // remains the liveness authority, so 0 deliberately disables only h2 PINGs
    // while retaining TCP keepalive and the same source/evidence semantics.
    let http2_keepalive_seconds = env::var("YELLOWSTONE_HTTP2_KEEPALIVE_SECONDS")
        .ok()
        .map(|value| value.parse::<u64>())
        .transpose()
        .context("invalid YELLOWSTONE_HTTP2_KEEPALIVE_SECONDS")?
        .unwrap_or(DEFAULT_HTTP2_KEEPALIVE_SECONDS);
    if http2_keepalive_seconds > 300 {
        anyhow::bail!("YELLOWSTONE_HTTP2_KEEPALIVE_SECONDS must be 0..=300");
    }
    // In storage-bounded tracked-wallet mode, retain the full source stream
    // exactly once. Candidate requests later reference their strict receipt
    // range in this spool; they never clone overlapping 120-second vectors.
    let spool_root = if rolling_window_seconds.is_some() { root.join("source-spool") } else { root.clone() };
    let mut writer = if capture_first_mode { None } else { Some(LedgerWriter::open(&spool_root)?) };
    let mut rolling_records: VecDeque<Arc<FrameRecord>> = VecDeque::new();
    let mut rolling_memory_bytes = 0_u64;
    let mut rolling_epoch_started_at_unix_ns: Option<i64> = None;
    let mut active_burst: Option<SharedBurstGroup> = None;
    let mut two_cq_candidates = OpenOptions::new()
        .create(true)
        .append(true)
        .open(root.join(candidate_file))?;
    let mut health = Health {
        schema: "YELLOWSTONE_FULL_META_PROSPECTIVE_LANE_V1".to_string(),
        source: "shyft-yellowstone-confirmed-full-meta".to_string(),
        endpoint: endpoint.clone(),
        started_at_utc: Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
        tracked_wallet: tracked_wallet.clone(),
        tracked_wallet_label: tracked_wallet_label.clone(),
        capture_first_mode,
        ..Health::default()
    };
    // Dedupe only the bounded replay overlap. A reconnect remains a hard
    // scientific wall, but exact replay duplicates must never be re-written.
    let mut replay_seen_by_slot: HashMap<u64, HashSet<String>> = HashMap::new();
    // The collector must never spend seconds compressing a dense PumpSwap
    // window. Promotion has one bounded background worker; excess requests are
    // recorded fail-closed instead of silently starving source ingestion.
    let (promotion_tx, promotion_rx) = mpsc::channel::<PromotionJob>(shared_burst_promotion_workers * 2);
    let (promotion_completion_tx, mut promotion_completion_rx) = mpsc::unbounded_channel::<PromotionCompletion>();
    // Sealing a dense 120-second window can involve hundreds of megabytes of
    // compression and metadata work. It must never contend with the tiny
    // source-health and candidate-journal writes that keep gRPC draining.
    // A separate promoted-evidence volume is therefore allowed operationally;
    // manifests/receipts remain canonical and consumers must follow them.
    let promotion_root = env::var("TRACKED_WALLET_PROMOTION_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| root.clone());
    fs::create_dir_all(&promotion_root)?;
    let promotion_rx = Arc::new(Mutex::new(promotion_rx));
    for _ in 0..shared_burst_promotion_workers {
        let promotion_root = promotion_root.clone();
        let promotion_rx = promotion_rx.clone();
        let promotion_completion_tx = promotion_completion_tx.clone();
        tokio::spawn(async move {
            loop {
                let job = { promotion_rx.lock().await.recv().await };
                let Some(job) = job else { break };
                let completion_root = promotion_root.clone();
                let completion_group_id = job.group.id.clone();
                let candidate_count = job.group.candidates.len();
                let completed = tokio::task::spawn_blocking(move || {
                    let result = promote_shared_burst_window(&completion_root, &job.group, &job.records);
                    let (status, error) = match &result {
                        Ok(()) => ("COMPLETE", None),
                        Err(error) => ("FAILED", Some(format!("{error:#}"))),
                    };
                    for candidate in &job.group.candidates {
                        let _ = write_promotion_result(
                            &completion_root,
                            &job.group,
                            candidate,
                            status,
                            error.clone(),
                        );
                    }
                    result.is_ok()
                })
                .await
                .unwrap_or(false);
                let _ = promotion_completion_tx.send(PromotionCompletion {
                    group_id: completion_group_id,
                    candidate_count,
                    completed,
                });
            }
        });
    }
    let mut pending_sync = 0_u64;
    let mut last_sync = tokio::time::Instant::now();
    let mut force_live_subscription = false;

    loop {
        health.current_epoch += 1;
        if health.current_epoch > 1 {
            health.reconnects += 1;
            health.continuity_gaps += 1;
            // A reconnect is a hard provenance wall for rolling evidence.
            // Never retain pre-reconnect frames for a later candidate window.
            rolling_records.clear();
            rolling_epoch_started_at_unix_ns = None;
            if let Some(group) = active_burst.take() {
                for candidate in &group.candidates {
                    let _ = write_promotion_result(
                        &promotion_root,
                        &group,
                        candidate,
                        "FAILED_CONTINUITY_GAP",
                        Some("source reconnect before shared burst was sealed".to_string()),
                    );
                }
                health.shared_burst_groups_failed += 1;
            }
        }
        write_health(&health_path, &health)?;

        // A provider-side HTTP/2 reset must either reconnect promptly or return
        // control to the outer loop.  An unbounded connect can otherwise leave
        // the process alive but source-silent until the external watchdog acts.
        let replay_from_slot = if force_live_subscription {
            force_live_subscription = false;
            None
        } else {
            health
                .latest_slot
                .map(|slot| slot.saturating_sub(recovery_overlap_slots))
        };
        health.replay_requested_from_slot = replay_from_slot;
        write_health(&health_path, &health)?;
        let mut channel_builder = Channel::from_shared(endpoint.clone())?
            .tls_config(ClientTlsConfig::new().with_native_roots())?
            .connect_timeout(Duration::from_secs(10))
            .tcp_keepalive(Some(Duration::from_secs(15)))
            .keep_alive_timeout(Duration::from_secs(5))
            .keep_alive_while_idle(true);
        if http2_keepalive_seconds > 0 {
            channel_builder = channel_builder
                .http2_keep_alive_interval(Duration::from_secs(http2_keepalive_seconds));
        }
        let channel = match channel_builder.connect().await {
            Ok(channel) => channel,
            Err(error) => {
                health.receive_errors += 1;
                eprintln!("yellowstone full-meta connect error: {error:#}");
                write_health(&health_path, &health)?;
                tokio::time::sleep(Duration::from_secs(1)).await;
                continue;
            }
        };
        let token_for_interceptor = token.clone();
        let mut client = GeyserClient::with_interceptor(channel, move |mut request: Request<()>| {
            request.metadata_mut().insert("x-token", token_for_interceptor.clone());
            Ok::<_, Status>(request)
        })
        .max_decoding_message_size(128 * 1024 * 1024);
        let requests = stream::once(async move { request(replay_from_slot) }).chain(stream::pending());
        let mut updates = match client.subscribe(Request::new(requests)).await {
            Ok(response) => response.into_inner(),
            Err(error) => {
                health.receive_errors += 1;
                eprintln!("yellowstone full-meta subscribe error: {error:#}");
                if replay_from_slot.is_some() && replay_position_unavailable(&format!("{error:#}")) {
                    force_live_subscription = true;
                    eprintln!("yellowstone full-meta replay position unavailable; falling back to a live subscription after this hard continuity wall");
                }
                write_health(&health_path, &health)?;
                tokio::time::sleep(Duration::from_secs(1)).await;
                continue;
            }
        };

        let (ingest_tx, mut ingest_rx) = mpsc::channel::<(
            chrono::DateTime<Utc>,
            std::result::Result<SubscribeUpdate, String>,
        )>(ingest_queue_capacity);
        tokio::spawn(async move {
            loop {
                let received_at = Utc::now();
                let next = match updates.next().await {
                    Some(Ok(update)) => Ok(update),
                    Some(Err(error)) => Err(format!("{error:#}")),
                    None => Err("yellowstone full-meta stream ended".to_string()),
                };
                let terminal = next.is_err();
                if ingest_tx.send((received_at, next)).await.is_err() {
                    break;
                }
                if terminal {
                    break;
                }
            }
        });

        loop {
            let (received_at, update) = match ingest_rx.recv().await {
                Some((received_at, Ok(update))) => {
                    health.ingest_queue_peak = health.ingest_queue_peak.max(ingest_rx.len() as u64);
                    (received_at, update)
                }
                Some((_, Err(error))) => {
                    health.receive_errors += 1;
                    eprintln!("yellowstone full-meta receive error: {error}");
                    if replay_from_slot.is_some() && replay_position_unavailable(&error) {
                        force_live_subscription = true;
                        eprintln!("yellowstone full-meta replay position unavailable; falling back to a live subscription after this hard continuity wall");
                    }
                    break;
                }
                None => {
                    health.receive_errors += 1;
                    eprintln!("yellowstone full-meta reader task ended");
                    break;
                }
            };
            while let Ok(completion) = promotion_completion_rx.try_recv() {
                if completion.completed {
                    health.rolling_window_promotions += completion.candidate_count as u64;
                    health.shared_burst_groups_completed += 1;
                } else {
                    health.rolling_window_promotion_failures += completion.candidate_count as u64;
                    health.shared_burst_groups_failed += 1;
                }
                eprintln!(
                    "tracked wallet shared burst promotion complete={} group={} candidates={}",
                    completion.completed, completion.group_id, completion.candidate_count
                );
            }
            let raw = update.encode_to_vec();
            let Some(UpdateOneof::Transaction(transaction)) = update.update_oneof else { continue };
            let Some(info) = transaction.transaction.as_ref() else { continue };
            let signature = bs58::encode(&info.signature).into_string();
            let meta_complete = info.meta.as_ref().is_some_and(|meta| {
                !meta.log_messages.is_empty()
                    && !meta.pre_balances.is_empty()
                    && !meta.post_balances.is_empty()
            });
            let direct = has_key_bytes(info, &pump_program_bytes);
            let router = has_key_bytes(info, &router_program_bytes);
            let pumpswap = has_key_bytes(info, &pumpswap_program_bytes);
            let mut instruction_kinds = Vec::new();
            if direct { instruction_kinds.push("PUMP_FILTER_MATCH".to_string()); }
            if router { instruction_kinds.push("PUMP_CPI_ROUTER_FILTER_MATCH".to_string()); }
            if pumpswap { instruction_kinds.push("PUMPSWAP_FILTER_MATCH".to_string()); }
            let raw_sha256 = sha256_hex(&raw);
            let evidence_id = sha256_hex(format!("full-meta|{signature}|{}|{raw_sha256}", transaction.slot).as_bytes());
            let seen_for_slot = replay_seen_by_slot.entry(transaction.slot).or_default();
            if !seen_for_slot.insert(evidence_id.clone()) {
                health.replayed_duplicates_discarded += 1;
                continue;
            }
            let oldest_retained_slot = transaction.slot.saturating_sub(recovery_overlap_slots * 2);
            replay_seen_by_slot.retain(|slot, _| *slot >= oldest_retained_slot);
            let received_ns = received_at.timestamp_nanos_opt().unwrap_or_default();
            let metadata = EvidenceMetadata {
                schema_version: SCHEMA_VERSION.to_string(),
                evidence_id: evidence_id.clone(),
                raw_sha256,
                kind: "transaction".to_string(),
                raw_format: "yellowstone_subscribe_update_protobuf_full_meta".to_string(),
                received_at_utc: received_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
                received_at_unix_ns: received_ns,
                decode_at_unix_ns: received_ns,
                writer_sequence: health.frames_received + 1,
                slot: Some(transaction.slot),
                transaction_index: None,
                signature: Some(signature.clone()),
                account_pubkey: None,
                account_owner: None,
                write_version: None,
                finalized: false,
                successful: None,
                failed: None,
                unresolved: true,
                pump_instruction_kinds: instruction_kinds,
                pump_instruction_coordinates: vec![format!("observed_transaction_index:{}", info.index)],
                discovered_mints: vec![],
                has_trade_event: false,
                has_fee_evidence: meta_complete,
                missing_raw_tx: false,
                missing_trade_event: false,
                missing_reserve_transition: !meta_complete,
                missing_canonical_ordering: true,
                missing_fee_evidence: !meta_complete,
                decode_failed: false,
                filters: vec!["pumpAndPumpSwapTransactions".to_string()],
            };
            let record = Arc::new(FrameRecord { metadata, raw });
            if let Some(window_seconds) = rolling_window_seconds {
                rolling_epoch_started_at_unix_ns.get_or_insert(received_ns);
                let _ = window_seconds;
            }
            if let Some(writer) = writer.as_mut() {
                writer.append_buffered(received_at, (*record).clone())?;
            }
            if capture_first_mode {
                rolling_memory_bytes = rolling_memory_bytes.saturating_add(record.raw.len() as u64);
                if rolling_memory_bytes > capture_ring_max_bytes {
                    anyhow::bail!("CAPTURE_FIRST_RING_CAPACITY_EXCEEDED bytes={rolling_memory_bytes} limit={capture_ring_max_bytes}");
                }
                rolling_records.push_back(record.clone());
                health.rolling_memory_bytes = rolling_memory_bytes;
            }
            if has_key_bytes(info, &tracked_wallet_bytes) {
                let warmup_complete = rolling_window_warmup_complete(
                    rolling_epoch_started_at_unix_ns,
                    received_ns,
                    rolling_window_seconds,
                );
                let mut candidate = TwoCqCandidate {
                    schema: candidate_schema.clone(),
                    source: candidate_source.clone(),
                    tracked_wallet: tracked_wallet.clone(),
                    tracked_wallet_label: tracked_wallet_label.clone(),
                    signature,
                    observed_slot: transaction.slot,
                    observed_transaction_index: info.index,
                    received_at_utc: received_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
                    received_at_unix_ns: received_ns,
                    continuity_epoch: health.current_epoch,
                    evidence_id,
                    rolling_window_promoted: false,
                    rolling_window_warmup_complete: warmup_complete,
                    rolling_window_promotion_state: if warmup_complete { "SOURCE_SPOOL_PENDING".to_string() } else { "WARMUP_INCOMPLETE".to_string() },
                    shared_burst_group_id: None,
                };
                if capture_first_mode && warmup_complete {
                    let pre_entry_start = received_ns - rolling_window_seconds.unwrap_or_default() * 1_000_000_000_i64;
                    let group = active_burst.get_or_insert_with(|| SharedBurstGroup {
                        id: format!("{}-{}", tracked_wallet_label, received_at.format("%Y%m%dT%H%M%S%3fZ")),
                        capture_start_unix_ns: pre_entry_start,
                        close_deadline_unix_ns: received_ns + shared_burst_quiet_seconds * 1_000_000_000_i64,
                        hard_deadline_unix_ns: received_ns + shared_burst_max_seconds * 1_000_000_000_i64,
                        candidates: Vec::new(),
                    });
                    group.capture_start_unix_ns = group.capture_start_unix_ns.min(pre_entry_start);
                    group.close_deadline_unix_ns = (received_ns + shared_burst_quiet_seconds * 1_000_000_000_i64)
                        .min(group.hard_deadline_unix_ns);
                    candidate.shared_burst_group_id = Some(group.id.clone());
                    candidate.rolling_window_promotion_state = "CAPTURE_FIRST_IN_MEMORY".to_string();
                    group.candidates.push(candidate.clone());
                } else if rolling_window_seconds.is_some() && warmup_complete {
                    write_source_spool_request(&root, &candidate)?;
                } else if rolling_window_seconds.is_some() {
                    health.rolling_warmup_candidates_skipped += 1;
                }
                writeln!(two_cq_candidates, "{}", serde_json::to_string(&candidate)?)?;
                two_cq_candidates.flush()?;
                health.tracked_wallet_pump_candidates += 1;
                if tracked_wallet == default_two_cq_wallet {
                    health.two_cq_wallet_pump_candidates += 1;
                }
            }
            if capture_first_mode {
                if active_burst.as_ref().is_some_and(|group| {
                    received_ns >= group.close_deadline_unix_ns || received_ns >= group.hard_deadline_unix_ns
                }) {
                    let group = active_burst.take().expect("checked active burst");
                    let records = rolling_records
                        .iter()
                        .filter(|record| {
                            let timestamp = record.metadata.received_at_unix_ns;
                            timestamp >= group.capture_start_unix_ns && timestamp <= group.close_deadline_unix_ns
                        })
                        .cloned()
                        .collect::<Vec<_>>();
                    let complete_pre_entry = rolling_records.front().is_some_and(|record| {
                        record.metadata.received_at_unix_ns <= group.capture_start_unix_ns
                    });
                    if !complete_pre_entry || records.is_empty() {
                        for candidate in &group.candidates {
                            write_promotion_result(&promotion_root, &group, candidate, "FAILED_INCOMPLETE_MEMORY_WINDOW", Some("capture-first ring did not retain the complete pre-entry interval".to_string()))?;
                        }
                        health.shared_burst_groups_failed += 1;
                    } else if let Err(send_error) = promotion_tx.try_send(PromotionJob { group, records }) {
                        let error = format!("capture-first promotion queue unavailable: {send_error}");
                        let job = send_error.into_inner();
                        for candidate in &job.group.candidates {
                            write_promotion_result(
                                &promotion_root,
                                &job.group,
                                candidate,
                                "FAILED_PROMOTION_QUEUE_FULL",
                                Some(error.clone()),
                            )?;
                        }
                        health.rolling_window_queue_full += 1;
                        health.shared_burst_groups_failed += 1;
                        eprintln!("{error}");
                    } else {
                        health.rolling_window_promotions_queued += 1;
                    }
                }
                let retained_from = capture_retained_from(
                    active_burst.as_ref().map(|group| group.capture_start_unix_ns),
                    received_ns,
                    rolling_window_seconds.unwrap_or_default(),
                );
                while rolling_records.front().is_some_and(|record| record.metadata.received_at_unix_ns < retained_from) {
                    if let Some(expired) = rolling_records.pop_front() {
                        rolling_memory_bytes = rolling_memory_bytes.saturating_sub(expired.raw.len() as u64);
                    }
                }
                health.rolling_memory_bytes = rolling_memory_bytes;
            }
            health.frames_received += 1;
            health.frames_meta_complete += u64::from(meta_complete);
            health.frames_meta_incomplete += u64::from(!meta_complete);
            health.direct_pump_frames += u64::from(direct);
            health.router_cpi_frames += u64::from(router);
            health.direct_pumpswap_frames += u64::from(pumpswap);
            health.latest_received_at_utc = Some(received_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true));
            health.latest_slot = Some(
                health
                    .latest_slot
                    .map_or(transaction.slot, |latest| latest.max(transaction.slot)),
            );
            pending_sync += 1;
            if pending_sync >= sync_batch_frames || last_sync.elapsed() >= Duration::from_secs(sync_interval_seconds) {
                if let Some(writer) = writer.as_mut() { writer.sync_buffered()?; }
                pending_sync = 0;
                last_sync = tokio::time::Instant::now();
                health.last_sync_at_utc = Some(Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true));
                write_health(&health_path, &health)?;
            }
        }
        if let Some(writer) = writer.as_mut() { writer.sync_buffered()?; }
        health.last_sync_at_utc = Some(Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true));
        write_health(&health_path, &health)?;
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}
