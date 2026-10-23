//! Receive-only native ShredStream wallet receipt capture.
//!
//! This binary intentionally does not use RPC, submit transactions, classify
//! outcomes, or select a strategy. It records the first local observation of
//! each ShredStream entry message and the wallet fee-payer transactions found
//! in that message. The timestamp is taken immediately after the gRPC stream
//! yields and before entry/transaction decoding.

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
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::time::sleep;
use tonic::transport::Channel;

pub mod shredstream {
    tonic::include_proto!("shredstream");
}

use shredstream::{shredstream_proxy_client::ShredstreamProxyClient, SubscribeEntriesRequest};

const SCHEMA: &str = "NATIVE_SHREDSTREAM_WALLET_RECEIPT_V1";
const HEALTH_SCHEMA: &str = "NATIVE_SHREDSTREAM_WALLET_RECEIPT_HEALTH_V1";
const SOURCE: &str = "jito_shredstream_subscribe_entries";
const PUMP_FUN_PROGRAM: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const PUMPSWAP_PROGRAM: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const ROUTER_PROGRAM: &str = "93JqDj2DjxDYuKPY3Z1rUG3YzPJykyMsrisPnvWYeuq5";
// Verified in the finalized 2CQ wallet tape as the outer executor for the
// current routed Pump/PumpSwap transactions. Keep this explicit and narrow:
// route_candidate is a receive-only admission hint, not wallet attribution.
const TWO_CQ_EXECUTOR_PROGRAM: &str = "2eFuLSK4McKonBcB6XLax8gSgxwR56zNfrjHsvkNkftj";

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
    let hour = (received_at_ns as i128 / 1_000_000_000i128 / 3600i128).max(0);
    fs::create_dir_all(root).context("create native receipt root")?;
    let path = root.join(format!("hour-{hour}.jsonl"));
    let mut output = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .with_context(|| format!("open {}", path.display()))?;
    serde_json::to_writer(&mut output, row).context("serialize native receipt")?;
    output
        .write_all(b"\n")
        .context("append native receipt newline")?;
    output.flush().context("flush native receipt")?;
    Ok(())
}

fn write_health(path: &Path, value: &Value) -> Result<()> {
    let parent = path.parent().context("native receipt health parent")?;
    fs::create_dir_all(parent).context("create native receipt health parent")?;
    let temporary = path.with_extension("json.tmp");
    let mut output = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&temporary)
        .with_context(|| format!("open {}", temporary.display()))?;
    serde_json::to_writer_pretty(&mut output, value).context("serialize native receipt health")?;
    output
        .write_all(b"\n")
        .context("write native receipt health newline")?;
    output.flush().context("flush native receipt health")?;
    fs::rename(temporary, path).context("publish native receipt health")?;
    Ok(())
}

fn wallet_match_indices(transaction: &VersionedTransaction, wallet: &str) -> Vec<usize> {
    transaction
        .message
        .static_account_keys()
        .iter()
        .enumerate()
        .filter_map(|(index, key)| (key.to_string() == wallet).then_some(index))
        .collect()
}

fn route_candidate(transaction: &VersionedTransaction) -> bool {
    let keys = transaction
        .message
        .static_account_keys()
        .iter()
        .map(ToString::to_string);
    keys.into_iter()
        .any(|key| {
            key == PUMP_FUN_PROGRAM
                || key == PUMPSWAP_PROGRAM
                || key == ROUTER_PROGRAM
                || key == TWO_CQ_EXECUTOR_PROGRAM
        })
}

async fn observe_once(
    url: &str,
    wallet: &str,
    root: &Path,
    health_path: &Path,
    connection_epoch: u64,
    match_mode: &str,
) -> Result<()> {
    let channel = Channel::from_shared(url.to_string())?.connect().await?;
    let mut client = ShredstreamProxyClient::new(channel);
    let mut stream = client
        .subscribe_entries(SubscribeEntriesRequest {})
        .await?
        .into_inner();
    let monotonic_origin = Instant::now();
    eprintln!("native wallet receipt observer: connected epoch={connection_epoch} url={url}");
    write_health(
        health_path,
        &json!({
            "schema": HEALTH_SCHEMA,
            "source": SOURCE,
            "wallet": wallet,
            "matchMode": match_mode,
            "connectionEpoch": connection_epoch,
            "observedStreamMessages": 0,
            "decodedStreamMessages": 0,
            "observedWalletTransactions": 0,
            "lastObservedSlot": null,
            "lastNativeReceiveUnixNs": null,
            "lastNativeReceiveMonotonicNs": null,
            "active": true,
            "state": "CONNECTED_AWAITING_ENTRIES",
        }),
    )?;

    let mut observed_stream_messages = 0u64;
    let mut decoded_stream_messages = 0u64;
    let mut decoded_transactions = 0u64;
    let mut observed_wallet_transactions = 0u64;
    let mut observed_wallet_fee_payer_transactions = 0u64;

    while let Some(slot_entry) = stream.message().await? {
        // These are deliberately captured before bincode decoding. They are
        // entry-stream receipt clocks, not finalized RPC timestamps.
        let native_receive_unix_ns = now_unix_ns();
        let native_receive_monotonic_ns = monotonic_origin.elapsed().as_nanos();
        let entry_payload_sha256 = hex_digest(&slot_entry.entries);
        let entry_payload_bytes = slot_entry.entries.len();
        observed_stream_messages = observed_stream_messages.saturating_add(1);

        let entries = match bincode::deserialize::<Vec<SolanaEntry>>(&slot_entry.entries) {
            Ok(entries) => entries,
            Err(error) => {
                eprintln!(
                    "native wallet receipt observer: slot {} deserialize error: {error}",
                    slot_entry.slot
                );
                write_health(
                    health_path,
                    &json!({
                        "schema": HEALTH_SCHEMA,
                        "source": SOURCE,
                        "wallet": wallet,
                        "matchMode": match_mode,
                        "connectionEpoch": connection_epoch,
                        "observedStreamMessages": observed_stream_messages,
                        "decodedStreamMessages": decoded_stream_messages,
                        "decodedTransactions": decoded_transactions,
                        "observedWalletTransactions": observed_wallet_transactions,
                        "observedWalletFeePayerTransactions": observed_wallet_fee_payer_transactions,
                        "lastObservedSlot": slot_entry.slot,
                        "lastNativeReceiveUnixNs": native_receive_unix_ns,
                        "lastNativeReceiveMonotonicNs": native_receive_monotonic_ns.to_string(),
                        "lastDecodeError": error.to_string(),
                        "active": true,
                    }),
                )?;
                continue;
            }
        };
        decoded_stream_messages = decoded_stream_messages.saturating_add(1);
        let mut transaction_index = 0u64;
        for entry in entries {
            for transaction in entry.transactions {
                decoded_transactions = decoded_transactions.saturating_add(1);
                let wallet_indices = wallet_match_indices(&transaction, wallet);
                let wallet_is_fee_payer = wallet_indices.first() == Some(&0);
                let matches_mode = match match_mode {
                    "fee_payer" => wallet_is_fee_payer,
                    "any_static_account" => !wallet_indices.is_empty(),
                    "route_candidate" => route_candidate(&transaction),
                    // Completeness lane: retain every decoded transaction so
                    // finalized wallet signatures can prove whether the
                    // native stream delivered the transaction at all. This
                    // is receive-only diagnostics, not wallet attribution.
                    "all" => true,
                    _ => false,
                };
                if matches_mode {
                    let signature = transaction
                        .signatures
                        .first()
                        .map(ToString::to_string)
                        .unwrap_or_default();
                    if !signature.is_empty() {
                        let transaction_bytes = bincode::serialize(&transaction)
                            .context("serialize native wallet transaction")?;
                        let row = json!({
                            "schema": SCHEMA,
                            "wallet": wallet,
                            "matchMode": match_mode,
                            "signature": signature,
                            "slot": slot_entry.slot,
                            "transactionIndex": transaction_index,
                            "nativeReceiveUnixNs": native_receive_unix_ns,
                            "nativeReceiveMonotonicNs": native_receive_monotonic_ns.to_string(),
                            "nativeReceiveSource": SOURCE,
                            "nativeReceiveGranularity": "entry_stream_message",
                            "decodedAtUnixNs": now_unix_ns(),
                            "entryPayloadBytes": entry_payload_bytes,
                            "entryPayloadSha256": entry_payload_sha256,
                            "transactionSha256": hex_digest(&transaction_bytes),
                            "connectionEpoch": connection_epoch,
                            "walletIsFeePayer": wallet_is_fee_payer,
                            "walletStaticAccountIndices": wallet_indices,
                            "candidateMatch": match_mode == "route_candidate",
                        });
                        append_record(root, native_receive_unix_ns, &row)?;
                        observed_wallet_transactions =
                            observed_wallet_transactions.saturating_add(1);
                        if wallet_is_fee_payer {
                            observed_wallet_fee_payer_transactions =
                                observed_wallet_fee_payer_transactions.saturating_add(1);
                        }
                    }
                }
                transaction_index = transaction_index.saturating_add(1);
            }
        }
        write_health(
            health_path,
            &json!({
                "schema": HEALTH_SCHEMA,
                "source": SOURCE,
                "wallet": wallet,
                "matchMode": match_mode,
                "connectionEpoch": connection_epoch,
                "observedStreamMessages": observed_stream_messages,
                "decodedStreamMessages": decoded_stream_messages,
                "decodedTransactions": decoded_transactions,
                "observedWalletTransactions": observed_wallet_transactions,
                "observedWalletFeePayerTransactions": observed_wallet_fee_payer_transactions,
                "lastObservedSlot": slot_entry.slot,
                "lastNativeReceiveUnixNs": native_receive_unix_ns,
                "lastNativeReceiveMonotonicNs": native_receive_monotonic_ns.to_string(),
                "active": true,
            }),
        )?;
    }
    Ok(())
}

#[tokio::main(flavor = "multi_thread")]
async fn main() -> Result<()> {
    let url = env::var("NATIVE_SHREDSTREAM_GRPC_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:9999".to_string());
    let wallet = env::var("NATIVE_RECEIPT_WALLET").context("NATIVE_RECEIPT_WALLET")?;
    let match_mode = env::var("NATIVE_RECEIPT_MATCH_MODE")
        .unwrap_or_else(|_| "fee_payer".to_string());
    anyhow::ensure!(
        match_mode == "fee_payer"
            || match_mode == "any_static_account"
            || match_mode == "route_candidate"
            || match_mode == "all",
        "NATIVE_RECEIPT_MATCH_MODE must be fee_payer, any_static_account, route_candidate, or all"
    );
    let root = PathBuf::from(env::var("NATIVE_RECEIPT_ROOT").context("NATIVE_RECEIPT_ROOT")?);
    let health_path = PathBuf::from(
        env::var("NATIVE_RECEIPT_HEALTH")
            .unwrap_or_else(|_| root.join("observer-health.json").display().to_string()),
    );
    let reconnect_ms = env::var("NATIVE_RECEIPT_RECONNECT_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(500);
    let mut epoch = 0u64;
    loop {
        epoch = epoch.saturating_add(1);
        if let Err(error) =
            observe_once(&url, &wallet, &root, &health_path, epoch, &match_mode).await
        {
            eprintln!("native wallet receipt observer: epoch={epoch} ended: {error:#}");
            sleep(Duration::from_millis(reconnect_ms)).await;
        }
    }
}
