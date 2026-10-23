use std::{
    collections::{HashMap, VecDeque},
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
use pump_exact_evidence_ledger::{
    sha256_hex, EvidenceMetadata, FrameRecord, LedgerWriter, SCHEMA_VERSION,
};
use serde::Serialize;
use tokio::sync::{mpsc, Mutex};
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
const PUMPSWAP_PROGRAM: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const DEFAULT_SHARED_BURST_QUIET_SECONDS: i64 = 12;
const DEFAULT_SHARED_BURST_MAX_SECONDS: i64 = 120;

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

fn write_promotion_result(
    root: &PathBuf,
    group: &SharedBurstGroup,
    candidate: &TwoCqCandidate,
    status: &str,
    error: Option<String>,
) -> Result<()> {
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

fn promote_shared_burst_window(
    root: &PathBuf,
    group: &SharedBurstGroup,
    records: &[Arc<FrameRecord>],
) -> Result<()> {
    let window_root = root.join("shared-windows").join(&group.id);
    let partial_root = root
        .join("shared-windows")
        .join(format!(".partial-{}", group.id));
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

fn request() -> SubscribeRequest {
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
        from_slot: None,
    }
}

fn has_key(
    info: &yellowstone_grpc_proto::prelude::SubscribeUpdateTransactionInfo,
    key: &str,
) -> bool {
    let Some(transaction) = info.transaction.as_ref() else {
        return false;
    };
    let Some(message) = transaction.message.as_ref() else {
        return false;
    };
    if message
        .account_keys
        .iter()
        .any(|candidate| bs58::encode(candidate).into_string() == key)
    {
        return true;
    }
    let Some(meta) = info.meta.as_ref() else {
        return false;
    };
    meta.loaded_writable_addresses
        .iter()
        .chain(meta.loaded_readonly_addresses.iter())
        .any(|candidate| bs58::encode(candidate).into_string() == key)
}

fn write_health(path: &PathBuf, health: &Health) -> Result<()> {
    let tmp = path.with_extension("tmp");
    fs::write(&tmp, serde_json::to_vec_pretty(health)?)?;
    fs::rename(tmp, path)?;
    Ok(())
}

fn rolling_window_warmup_complete(
    started_at_unix_ns: Option<i64>,
    received_at_unix_ns: i64,
    window_seconds: Option<i64>,
) -> bool {
    match (started_at_unix_ns, window_seconds) {
        (_, None) => true,
        (Some(started_at), Some(seconds)) => {
            started_at <= received_at_unix_ns - seconds * 1_000_000_000
        }
        (None, Some(_)) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::rolling_window_warmup_complete;

    #[test]
    fn rolling_warmup_uses_epoch_start_not_trimmed_queue_front() {
        let start = 1_000_000_000_000_i64;
        assert!(!rolling_window_warmup_complete(
            Some(start),
            start + 119_999_999_999,
            Some(120)
        ));
        assert!(rolling_window_warmup_complete(
            Some(start),
            start + 120_000_000_000,
            Some(120)
        ));
        assert!(!rolling_window_warmup_complete(
            None,
            start + 120_000_000_000,
            Some(120)
        ));
        assert!(rolling_window_warmup_complete(None, start, None));
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
    let tracked_wallet_label =
        env::var("TRACKED_WALLET_LABEL").unwrap_or_else(|_| "two-cq".to_string());
    let candidate_file = env::var("TRACKED_WALLET_CANDIDATE_FILE")
        .unwrap_or_else(|_| "2cq-wallet-pump-candidates.jsonl".to_string());
    let candidate_schema = env::var("TRACKED_WALLET_CANDIDATE_SCHEMA")
        .unwrap_or_else(|_| "2CQ_FULL_META_WALLET_PUMP_CANDIDATE_V1".to_string());
    let candidate_source = env::var("TRACKED_WALLET_SOURCE")
        .unwrap_or_else(|_| "shyft-yellowstone-confirmed-full-meta".to_string());
    fs::create_dir_all(&root)?;
    let health_path = root.join("health.json");
    let rolling_window_seconds = env::var("TRACKED_WALLET_ROLLING_WINDOW_SECONDS")
        .ok()
        .map(|value| value.parse::<i64>())
        .transpose()
        .context("invalid TRACKED_WALLET_ROLLING_WINDOW_SECONDS")?;
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
        && (shared_burst_quiet_seconds <= 0
            || shared_burst_max_seconds < shared_burst_quiet_seconds)
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
    // In storage-bounded tracked-wallet mode, retain the full source stream
    // exactly once. Candidate requests later reference their strict receipt
    // range in this spool; they never clone overlapping 120-second vectors.
    let spool_root = if rolling_window_seconds.is_some() {
        root.join("source-spool")
    } else {
        root.clone()
    };
    let mut writer = Some(LedgerWriter::open(&spool_root)?);
    let mut rolling_records: VecDeque<Arc<FrameRecord>> = VecDeque::new();
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
        ..Health::default()
    };
    // The collector must never spend seconds compressing a dense PumpSwap
    // window. Promotion has one bounded background worker; excess requests are
    // recorded fail-closed instead of silently starving source ingestion.
    let (promotion_tx, promotion_rx) =
        mpsc::channel::<PromotionJob>(shared_burst_promotion_workers * 2);
    let (promotion_completion_tx, mut promotion_completion_rx) =
        mpsc::unbounded_channel::<PromotionCompletion>();
    let promotion_root = root.clone();
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
                    let result =
                        promote_shared_burst_window(&completion_root, &job.group, &job.records);
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
                        &root,
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

        let channel = match Channel::from_shared(endpoint.clone())?
            .tls_config(ClientTlsConfig::new().with_native_roots())?
            .connect()
            .await
        {
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
            request
                .metadata_mut()
                .insert("x-token", token_for_interceptor.clone());
            Ok::<_, Status>(request)
        })
        .max_decoding_message_size(128 * 1024 * 1024);
        let requests = stream::once(async { request() }).chain(stream::pending());
        let mut updates = match client.subscribe(Request::new(requests)).await {
            Ok(response) => response.into_inner(),
            Err(error) => {
                health.receive_errors += 1;
                eprintln!("yellowstone full-meta subscribe error: {error:#}");
                write_health(&health_path, &health)?;
                tokio::time::sleep(Duration::from_secs(1)).await;
                continue;
            }
        };

        loop {
            let update = match updates.next().await {
                Some(Ok(update)) => update,
                Some(Err(error)) => {
                    health.receive_errors += 1;
                    eprintln!("yellowstone full-meta receive error: {error:#}");
                    break;
                }
                None => {
                    health.receive_errors += 1;
                    eprintln!("yellowstone full-meta stream ended");
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
            let received_at = Utc::now();
            let raw = update.encode_to_vec();
            let Some(UpdateOneof::Transaction(transaction)) = update.update_oneof else {
                continue;
            };
            let Some(info) = transaction.transaction.as_ref() else {
                continue;
            };
            let signature = bs58::encode(&info.signature).into_string();
            let meta_complete = info.meta.as_ref().is_some_and(|meta| {
                !meta.log_messages.is_empty()
                    && !meta.pre_balances.is_empty()
                    && !meta.post_balances.is_empty()
            });
            let direct = has_key(info, PUMP_PROGRAM);
            let router = has_key(info, PUMP_CPI_ROUTER_PROGRAM);
            let pumpswap = has_key(info, PUMPSWAP_PROGRAM);
            let mut instruction_kinds = Vec::new();
            if direct {
                instruction_kinds.push("PUMP_FILTER_MATCH".to_string());
            }
            if router {
                instruction_kinds.push("PUMP_CPI_ROUTER_FILTER_MATCH".to_string());
            }
            if pumpswap {
                instruction_kinds.push("PUMPSWAP_FILTER_MATCH".to_string());
            }
            let raw_sha256 = sha256_hex(&raw);
            let evidence_id = sha256_hex(
                format!("full-meta|{signature}|{}|{raw_sha256}", transaction.slot).as_bytes(),
            );
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
                pump_instruction_coordinates: vec![format!(
                    "observed_transaction_index:{}",
                    info.index
                )],
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
            if has_key(info, &tracked_wallet) {
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
                    received_at_utc: received_at
                        .to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
                    received_at_unix_ns: received_ns,
                    continuity_epoch: health.current_epoch,
                    evidence_id,
                    rolling_window_promoted: false,
                    rolling_window_warmup_complete: warmup_complete,
                    rolling_window_promotion_state: if warmup_complete {
                        "SOURCE_SPOOL_PENDING".to_string()
                    } else {
                        "WARMUP_INCOMPLETE".to_string()
                    },
                    shared_burst_group_id: None,
                };
                if rolling_window_seconds.is_some() && warmup_complete {
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
            health.frames_received += 1;
            health.frames_meta_complete += u64::from(meta_complete);
            health.frames_meta_incomplete += u64::from(!meta_complete);
            health.direct_pump_frames += u64::from(direct);
            health.router_cpi_frames += u64::from(router);
            health.direct_pumpswap_frames += u64::from(pumpswap);
            health.latest_received_at_utc =
                Some(received_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true));
            health.latest_slot = Some(transaction.slot);
            pending_sync += 1;
            if pending_sync >= 256 || last_sync.elapsed() >= Duration::from_secs(1) {
                if let Some(writer) = writer.as_mut() {
                    writer.sync_buffered()?;
                }
                pending_sync = 0;
                last_sync = tokio::time::Instant::now();
                health.last_sync_at_utc =
                    Some(Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true));
                write_health(&health_path, &health)?;
            }
        }
        if let Some(writer) = writer.as_mut() {
            writer.sync_buffered()?;
        }
        health.last_sync_at_utc =
            Some(Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true));
        write_health(&health_path, &health)?;
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}
