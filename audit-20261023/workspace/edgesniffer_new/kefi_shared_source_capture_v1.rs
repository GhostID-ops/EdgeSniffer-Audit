//! Capture-only shared source for the prospective kEFi selection study.
//!
//! This process does exactly four things on the hot path: receive a filtered
//! Yellowstone frame, enforce a bounded handoff, append the original protobuf
//! bytes to the durable journal, and advance the source sequence.  It does no
//! RPC, decoding, pool resolution, scoring, or wallet-specific work.  Any
//! stream break or writer overload seals the run as invalid rather than
//! reconnecting and silently bridging an experiment.

use anyhow::{bail, Context, Result};
use chrono::Utc;
use futures::{stream, StreamExt};
use prost::Message;
use pump_exact_evidence_ledger::{
    sha256_hex, EvidenceMetadata, FrameRecord, LedgerWriter, SCHEMA_VERSION,
};
use serde::Serialize;
use std::{
    collections::HashMap,
    env, fs,
    path::PathBuf,
    process::Command,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::sync::mpsc;
use tonic::{
    metadata::AsciiMetadataValue,
    transport::{Channel, ClientTlsConfig},
    Request, Status,
};
use yellowstone_grpc_proto::geyser::{
    geyser_client::GeyserClient, subscribe_update::UpdateOneof, CommitmentLevel, SlotStatus,
    SubscribeRequest, SubscribeRequestFilterSlots, SubscribeRequestFilterTransactions,
    SubscribeUpdate,
};

const PUMP: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const ROUTER: &str = "6Vo3245eszAb5wuqEMw8mGdbfRUdKbHhDHP5LcaGuTAB";
const PUMPSWAP: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const MAX_MESSAGE_BYTES: usize = 128 * 1024 * 1024;
const QUEUE_ITEMS: usize = 4096;
const QUEUE_BYTES: u64 = 4 * 1024 * 1024 * 1024;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Health {
    schema: String,
    status: String,
    source_instance_id: String,
    continuity_epoch: String,
    durable_frames: u64,
    transaction_frames: u64,
    finalized_slot_frames: u64,
    raw_bytes: u64,
    raw_cap_bytes: u64,
    queued_bytes_peak: u64,
    max_message_bytes: u64,
    start_free_bytes: u64,
    current_free_bytes: u64,
    protected_reserve_bytes: u64,
    stop_reason: Option<String>,
}

enum Input {
    Frame(SubscribeUpdate, u64),
    Fatal(String),
}

fn free_bytes(path: &PathBuf) -> Result<u64> {
    let out = Command::new("df")
        .args([
            "-B1",
            "--output=avail",
            path.to_str().context("SOURCE_ROOT_PATH")?,
        ])
        .output()?;
    if !out.status.success() {
        bail!("SOURCE_DF_FAILED")
    }
    String::from_utf8(out.stdout)?
        .lines()
        .nth(1)
        .context("SOURCE_DF_OUTPUT")?
        .trim()
        .parse()
        .context("SOURCE_DF_VALUE")
}

fn atomic_json(path: &PathBuf, value: &impl Serialize) -> Result<()> {
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, serde_json::to_vec_pretty(value)?)?;
    fs::rename(temporary, path)?;
    Ok(())
}

fn subscription() -> SubscribeRequest {
    let mut transactions = HashMap::new();
    let mut slots = HashMap::new();
    transactions.insert(
        "sharedPumpSource".into(),
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: Some(false),
            signature: None,
            account_include: vec![PUMP.into(), ROUTER.into(), PUMPSWAP.into()],
            account_exclude: vec![],
            account_required: vec![],
            token_accounts: None,
        },
    );
    slots.insert(
        "finalizedProgress".into(),
        SubscribeRequestFilterSlots {
            filter_by_commitment: Some(true),
            interslot_updates: Some(false),
        },
    );
    SubscribeRequest {
        accounts: HashMap::new(),
        slots,
        transactions,
        transactions_status: HashMap::new(),
        blocks: HashMap::new(),
        blocks_meta: HashMap::new(),
        entry: HashMap::new(),
        accounts_data_slice: vec![],
        commitment: Some(CommitmentLevel::Finalized as i32),
        ping: None,
        from_slot: None,
    }
}

fn raw_frame(
    update: &SubscribeUpdate,
    source: &str,
    sequence: u64,
    raw: Vec<u8>,
) -> Result<FrameRecord> {
    let received = Utc::now();
    let (kind, slot, transaction_index, signature) = match update.update_oneof.as_ref() {
        Some(UpdateOneof::Transaction(transaction)) => {
            let info = transaction
                .transaction
                .as_ref()
                .context("SOURCE_TRANSACTION_INFO")?;
            (
                "transaction",
                Some(transaction.slot),
                Some(info.index),
                Some(bs58::encode(&info.signature).into_string()),
            )
        }
        Some(UpdateOneof::Slot(slot)) if slot.status == SlotStatus::SlotFinalized as i32 => {
            ("finalized_slot", Some(slot.slot), None, None)
        }
        _ => bail!("SOURCE_UNSUPPORTED_UPDATE_KIND"),
    };
    let raw_sha256 = sha256_hex(&raw);
    Ok(FrameRecord {
        metadata: EvidenceMetadata {
            schema_version: SCHEMA_VERSION.into(),
            evidence_id: sha256_hex(
                format!("shared-source|{source}|1|{sequence}|{raw_sha256}").as_bytes(),
            ),
            raw_sha256,
            kind: kind.into(),
            raw_format: "yellowstone_subscribe_update_protobuf_full_meta".into(),
            received_at_utc: received.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
            received_at_unix_ns: received.timestamp_nanos_opt().unwrap_or_default(),
            decode_at_unix_ns: received.timestamp_nanos_opt().unwrap_or_default(),
            writer_sequence: sequence,
            continuity_epoch: Some(1),
            source_instance_id: Some(source.into()),
            slot,
            transaction_index,
            signature,
            account_pubkey: None,
            account_owner: None,
            write_version: None,
            finalized: false,
            successful: Some(true),
            failed: Some(false),
            unresolved: true,
            pump_instruction_kinds: vec![],
            pump_instruction_coordinates: vec![],
            discovered_mints: vec![],
            has_trade_event: false,
            has_fee_evidence: false,
            missing_raw_tx: false,
            missing_trade_event: false,
            missing_reserve_transition: false,
            missing_canonical_ordering: true,
            missing_fee_evidence: false,
            decode_failed: false,
            filters: vec!["shared-public-pump-source-v1".into()],
        },
        raw,
    })
}

/// Yellowstone emits transport/ping and non-finalized slot updates on the same
/// subscription. They carry no public market transition for this finalized
/// study, so they are deliberately ignored before entering the bounded writer
/// queue. A transaction or finalized-slot frame is never filtered here.
fn is_captured_update(update: &SubscribeUpdate) -> bool {
    match update.update_oneof.as_ref() {
        Some(UpdateOneof::Transaction(_)) => true,
        Some(UpdateOneof::Slot(slot)) => slot.status == SlotStatus::SlotFinalized as i32,
        _ => false,
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    rustls::crypto::ring::default_provider()
        .install_default()
        .map_err(|_| anyhow::anyhow!("RUSTLS_PROVIDER"))?;
    let root = PathBuf::from(env::var("KEFI_SHARED_SOURCE_ROOT")?);
    let endpoint = env::var("CANONICAL_SHYFT_GRPC_ENDPOINT")
        .or_else(|_| env::var("YELLOWSTONE_FULL_META_ENDPOINT"))?;
    let token: AsciiMetadataValue = env::var("SHYFT_GRPC_X_TOKEN")?.parse()?;
    let max_seconds: u64 = env::var("KEFI_SHARED_SOURCE_MAX_SECONDS")?.parse()?;
    let raw_cap_bytes: u64 = env::var("KEFI_SHARED_SOURCE_RAW_CAP_BYTES")?.parse()?;
    let protected_reserve_bytes: u64 =
        env::var("KEFI_SHARED_SOURCE_PROTECTED_RESERVE_BYTES")?.parse()?;
    if max_seconds == 0 || raw_cap_bytes == 0 || root.exists() {
        bail!("SOURCE_CAPTURE_CONFIG_INVALID_OR_ROOT_NOT_FRESH")
    }
    fs::create_dir_all(&root)?;
    let start_free_bytes = free_bytes(&root)?;
    if start_free_bytes < protected_reserve_bytes.saturating_add(raw_cap_bytes) {
        bail!("SOURCE_CAPTURE_STARTUP_STORAGE_GUARD")
    }
    let source = format!(
        "kefi-shared-source-{}-{}",
        Utc::now().format("%Y%m%dT%H%M%SZ"),
        std::process::id()
    );
    atomic_json(
        &root.join("run-binding.json"),
        &serde_json::json!({
            "schema":"KEFI_SHARED_PUBLIC_SOURCE_BINDING_V1",
            "sourceInstanceId":source,
            "continuityEpoch":"1",
            "subscription":"filtered successful Pump/PumpSwap transactions plus finalized slot progress",
            "sourceCompleteness":"NOT_PROVEN_BY_FILTERED_STREAM_ALONE",
            "scientificAdmission":"PROHIBITED_UNTIL_SEPARATE_FINALIZED_RECONCILIATION"
        }),
    )?;
    let channel = Channel::from_shared(endpoint)?
        .tls_config(ClientTlsConfig::new().with_native_roots())?
        .connect_timeout(Duration::from_secs(10))
        .http2_keep_alive_interval(Duration::from_secs(20))
        .keep_alive_timeout(Duration::from_secs(10))
        .keep_alive_while_idle(true)
        .connect()
        .await?;
    let mut client = GeyserClient::with_interceptor(channel, move |mut request: Request<()>| {
        request.metadata_mut().insert("x-token", token.clone());
        Ok::<_, Status>(request)
    })
    .max_decoding_message_size(MAX_MESSAGE_BYTES);
    let mut updates = client
        .subscribe(Request::new(
            stream::once(async { subscription() }).chain(stream::pending()),
        ))
        .await?
        .into_inner();
    let (tx, mut rx) = mpsc::channel::<Input>(QUEUE_ITEMS);
    let queued_bytes = Arc::new(AtomicU64::new(0));
    let receiver_bytes = queued_bytes.clone();
    tokio::spawn(async move {
        loop {
            let update = match updates.next().await {
                Some(Ok(update)) => update,
                Some(Err(error)) => {
                    let _ = tx
                        .send(Input::Fatal(format!("SOURCE_STREAM_RESET:{error}")))
                        .await;
                    return;
                }
                None => {
                    let _ = tx.send(Input::Fatal("SOURCE_STREAM_ENDED".into())).await;
                    return;
                }
            };
            if !is_captured_update(&update) {
                continue;
            }
            let size = update.encoded_len() as u64;
            if size as usize > MAX_MESSAGE_BYTES {
                let _ = tx.send(Input::Fatal("SOURCE_MESSAGE_CAP".into())).await;
                return;
            }
            let before = receiver_bytes.fetch_add(size, Ordering::AcqRel);
            if before.saturating_add(size) > QUEUE_BYTES {
                receiver_bytes.fetch_sub(size, Ordering::AcqRel);
                let _ = tx
                    .send(Input::Fatal("SOURCE_WRITER_QUEUE_BYTE_CAP".into()))
                    .await;
                return;
            }
            if tx.send(Input::Frame(update, size)).await.is_err() {
                receiver_bytes.fetch_sub(size, Ordering::AcqRel);
                return;
            }
        }
    });
    let mut writer = LedgerWriter::open(&root)?;
    let mut health = Health {
        schema: "KEFI_SHARED_PUBLIC_SOURCE_HEALTH_V1".into(),
        status: "RUNNING".into(),
        source_instance_id: source.clone(),
        continuity_epoch: "1".into(),
        durable_frames: 0,
        transaction_frames: 0,
        finalized_slot_frames: 0,
        raw_bytes: 0,
        raw_cap_bytes,
        queued_bytes_peak: 0,
        max_message_bytes: 0,
        start_free_bytes,
        current_free_bytes: start_free_bytes,
        protected_reserve_bytes,
        stop_reason: None,
    };
    let started = tokio::time::Instant::now();
    let result: Result<()> = async {
        while started.elapsed() < Duration::from_secs(max_seconds) {
            let input = tokio::time::timeout(Duration::from_secs(30), rx.recv())
                .await
                .context("SOURCE_RECEIVE_TIMEOUT")?
                .context("SOURCE_RECEIVER_ENDED")?;
            match input {
                Input::Fatal(reason) => bail!("{reason}"),
                Input::Frame(update, size) => {
                    queued_bytes.fetch_sub(size, Ordering::AcqRel);
                    health.queued_bytes_peak = health
                        .queued_bytes_peak
                        .max(queued_bytes.load(Ordering::Acquire));
                    health.max_message_bytes = health.max_message_bytes.max(size);
                    let raw = update.encode_to_vec();
                    if health.raw_bytes.saturating_add(raw.len() as u64) > raw_cap_bytes {
                        bail!("SOURCE_RAW_CAP_REACHED")
                    }
                    if health.durable_frames % 64 == 0 {
                        health.current_free_bytes = free_bytes(&root)?;
                        if health.current_free_bytes
                            < protected_reserve_bytes
                                .saturating_add(raw_cap_bytes.saturating_sub(health.raw_bytes))
                        {
                            bail!("SOURCE_PROTECTED_RESERVE")
                        }
                    }
                    let frame = raw_frame(&update, &source, health.durable_frames + 1, raw)?;
                    match frame.metadata.kind.as_str() {
                        "transaction" => health.transaction_frames += 1,
                        "finalized_slot" => health.finalized_slot_frames += 1,
                        _ => unreachable!(),
                    }
                    health.raw_bytes += frame.raw.len() as u64;
                    writer.append_buffered(Utc::now(), frame)?;
                    health.durable_frames += 1;
                    if health.durable_frames % 128 == 0 {
                        writer.sync_buffered()?;
                        atomic_json(&root.join("health.json"), &health)?;
                    }
                }
            }
        }
        Ok(())
    }
    .await;
    writer.sync_buffered()?;
    writer.finalize_current(true)?;
    health.current_free_bytes = free_bytes(&root)?;
    health.status = if result.is_ok() {
        "SEALED_CLEAN".into()
    } else {
        "INVALIDATED".into()
    };
    health.stop_reason = result.as_ref().err().map(ToString::to_string);
    atomic_json(&root.join("health.json"), &health)?;
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_subscription_is_filtered_and_never_claims_market_completeness() {
        let request = subscription();
        assert!(request.blocks.is_empty());
        assert_eq!(request.commitment, Some(CommitmentLevel::Finalized as i32));
        assert!(request.transactions.contains_key("sharedPumpSource"));
        assert!(request.slots.contains_key("finalizedProgress"));
    }

    #[test]
    fn non_data_updates_do_not_enter_the_durable_journal() {
        assert!(!is_captured_update(&SubscribeUpdate::default()));
    }
}
