use std::{
    collections::HashSet,
    env, fs,
    fs::{File, OpenOptions},
    io::{BufRead, BufReader, BufWriter, Write},
    path::{Path, PathBuf},
    time::Duration,
};

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use prost::Message;
use serde::Serialize;
use solana_entry::entry::Entry as SolanaEntry;
use solana_sdk::{message::VersionedMessage, pubkey::Pubkey};
use tokio::time::sleep;
use tonic::{codegen::http::uri::PathAndQuery, transport::Endpoint, Request};

const TWO_CQ_WALLET: &str = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG";

#[derive(Clone, PartialEq, Message)]
struct SubscribeEntriesRequest {}

#[derive(Clone, PartialEq, Message)]
struct ShredstreamEntry {
    #[prost(uint64, tag = "1")]
    slot: u64,
    #[prost(bytes = "vec", tag = "2")]
    entries: Vec<u8>,
}

#[derive(Clone)]
struct Config {
    endpoint: String,
    output: PathBuf,
    health: PathBuf,
    continuity: PathBuf,
    reconnect_ms: u64,
}

impl Config {
    fn from_env() -> Self {
        let root = PathBuf::from(
            env::var("ALLENHARK_2CQ_CAPTURE_FIRST_ROOT").unwrap_or_else(|_| {
                "/var/lib/pump-exact-evidence-ledger-v1/2cq-capture-first-v1".to_string()
            }),
        );
        Self {
            endpoint: env::var("ALLENHARK_SHREDSTREAM_GRPC_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:9999".to_string()),
            output: env::var("ALLENHARK_2CQ_CAPTURE_FIRST_OUTPUT")
                .map(PathBuf::from)
                .unwrap_or_else(|_| root.join("candidates.jsonl")),
            health: env::var("ALLENHARK_2CQ_CAPTURE_FIRST_HEALTH")
                .map(PathBuf::from)
                .unwrap_or_else(|_| root.join("health.json")),
            continuity: env::var("ALLENHARK_2CQ_CAPTURE_FIRST_CONTINUITY")
                .map(PathBuf::from)
                .unwrap_or_else(|_| root.join("continuity.jsonl")),
            reconnect_ms: env::var("ALLENHARK_RECONNECT_MS")
                .ok()
                .and_then(|value| value.parse().ok())
                .unwrap_or(500),
        }
    }
}

#[derive(Default, Serialize)]
#[serde(rename_all = "camelCase")]
struct Health {
    schema: &'static str,
    source: &'static str,
    process_instance: String,
    started_at_utc: String,
    updated_at_utc: String,
    connected: bool,
    connection_epoch: u64,
    reconnects: u64,
    stream_messages: u64,
    entries_seen: u64,
    transactions_seen: u64,
    candidates_written: u64,
    decode_failures: u64,
    duplicate_signatures: u64,
    first_slot: Option<u64>,
    last_slot: u64,
    last_receipt_at_utc: Option<String>,
    last_candidate_at_utc: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct CaptureFirstCandidate {
    schema: &'static str,
    source: &'static str,
    wallet: &'static str,
    signature: String,
    received_at_utc: String,
    received_at_unix_ns: i64,
    slot: u64,
    stream_sequence: u64,
    entry_index: usize,
    transaction_index_within_entry: usize,
    observed_slot_transaction_ordinal: u64,
    transaction_ordinal_semantics: &'static str,
    connection_epoch: u64,
    transaction_version: &'static str,
    static_account_keys: Vec<String>,
    address_table_lookups: Vec<AddressTableLookup>,
    loaded_addresses_available_at_arrival: bool,
    wallet_static_account_index: usize,
    wallet_is_signer: bool,
    wallet_is_writable: bool,
    wallet_is_explicit_account: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct AddressTableLookup {
    account_key: String,
    writable_indexes: Vec<u8>,
    readonly_indexes: Vec<u8>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Continuity<'a> {
    schema: &'static str,
    source: &'static str,
    process_instance: &'a str,
    event: &'static str,
    timestamp_utc: String,
    connection_epoch: u64,
    reconnects: u64,
    last_slot: u64,
    detail: Option<String>,
}

fn open_append(path: &Path) -> Result<BufWriter<File>> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    Ok(BufWriter::new(
        OpenOptions::new().create(true).append(true).open(path)?,
    ))
}

fn append_json<T: Serialize>(writer: &mut BufWriter<File>, value: &T) -> Result<()> {
    serde_json::to_writer(&mut *writer, value)?;
    writer.write_all(b"\n")?;
    writer.flush()?;
    Ok(())
}

fn atomic_json<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = path.with_extension("json.tmp");
    fs::write(&temporary, serde_json::to_vec_pretty(value)?)?;
    fs::rename(temporary, path)?;
    Ok(())
}

fn existing_signatures(path: &Path) -> Result<HashSet<String>> {
    if !path.exists() {
        return Ok(HashSet::new());
    }
    let mut signatures = HashSet::new();
    for line in BufReader::new(File::open(path)?).lines() {
        let line = line?;
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) {
            if let Some(signature) = value.get("signature").and_then(|value| value.as_str()) {
                signatures.insert(signature.to_string());
            }
        }
    }
    Ok(signatures)
}

fn static_keys(message: &VersionedMessage) -> &[Pubkey] {
    match message {
        VersionedMessage::Legacy(value) => &value.account_keys,
        VersionedMessage::V0(value) => &value.account_keys,
    }
}

fn account_flags(message: &VersionedMessage, account_index: usize) -> (bool, bool) {
    let header = match message {
        VersionedMessage::Legacy(value) => &value.header,
        VersionedMessage::V0(value) => &value.header,
    };
    let key_count = static_keys(message).len();
    let required = header.num_required_signatures as usize;
    let readonly_signed = header.num_readonly_signed_accounts as usize;
    let readonly_unsigned = header.num_readonly_unsigned_accounts as usize;
    let signer = account_index < required;
    let writable = if signer {
        account_index < required.saturating_sub(readonly_signed)
    } else {
        account_index < key_count.saturating_sub(readonly_unsigned)
    };
    (signer, writable)
}

fn version_and_lookups(message: &VersionedMessage) -> (&'static str, Vec<AddressTableLookup>) {
    match message {
        VersionedMessage::Legacy(_) => ("legacy", Vec::new()),
        VersionedMessage::V0(value) => (
            "v0",
            value
                .address_table_lookups
                .iter()
                .map(|lookup| AddressTableLookup {
                    account_key: lookup.account_key.to_string(),
                    writable_indexes: lookup.writable_indexes.clone(),
                    readonly_indexes: lookup.readonly_indexes.clone(),
                })
                .collect(),
        ),
    }
}

async fn consume_once(
    cfg: &Config,
    health: &mut Health,
    seen: &mut HashSet<String>,
) -> Result<()> {
    let channel = Endpoint::from_shared(cfg.endpoint.clone())?.connect().await?;
    let mut grpc = tonic::client::Grpc::new(channel);
    grpc.ready().await.context("AllenHark gRPC not ready")?;
    let path = PathAndQuery::from_static("/shredstream.ShredstreamProxy/SubscribeEntries");
    let codec = tonic_prost::ProstCodec::<SubscribeEntriesRequest, ShredstreamEntry>::default();
    let mut stream = grpc
        .server_streaming(Request::new(SubscribeEntriesRequest {}), path, codec)
        .await?
        .into_inner();

    health.connection_epoch += 1;
    health.connected = true;
    atomic_json(&cfg.health, health)?;
    let mut output = open_append(&cfg.output)?;
    let mut continuity = open_append(&cfg.continuity)?;
    append_json(
        &mut continuity,
        &Continuity {
            schema: "CAPTURE_FIRST_2CQ_CONTINUITY_V1",
            source: "allenhark-subscribe-entries",
            process_instance: &health.process_instance,
            event: "CONNECTED",
            timestamp_utc: Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
            connection_epoch: health.connection_epoch,
            reconnects: health.reconnects,
            last_slot: health.last_slot,
            detail: None,
        },
    )?;

    let wallet: Pubkey = TWO_CQ_WALLET.parse()?;
    let mut observed_slot = 0u64;
    let mut slot_ordinal = 0u64;
    while let Some(message) = stream.message().await? {
        let received_at: DateTime<Utc> = Utc::now();
        let received_ns = received_at
            .timestamp_nanos_opt()
            .context("receipt timestamp overflow")?;
        health.stream_messages += 1;
        health.first_slot.get_or_insert(message.slot);
        health.last_slot = message.slot;
        health.last_receipt_at_utc =
            Some(received_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true));
        if observed_slot != message.slot {
            observed_slot = message.slot;
            slot_ordinal = 0;
        }
        let entries: Vec<SolanaEntry> = match bincode::deserialize(&message.entries) {
            Ok(value) => value,
            Err(error) => {
                health.decode_failures += 1;
                atomic_json(&cfg.health, health)?;
                append_json(
                    &mut continuity,
                    &Continuity {
                        schema: "CAPTURE_FIRST_2CQ_CONTINUITY_V1",
                        source: "allenhark-subscribe-entries",
                        process_instance: &health.process_instance,
                        event: "ENTRY_DECODE_FAILURE",
                        timestamp_utc: received_at
                            .to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
                        connection_epoch: health.connection_epoch,
                        reconnects: health.reconnects,
                        last_slot: health.last_slot,
                        detail: Some(error.to_string()),
                    },
                )?;
                continue;
            }
        };
        health.entries_seen += entries.len() as u64;
        for (entry_index, entry) in entries.iter().enumerate() {
            for (transaction_index, transaction) in entry.transactions.iter().enumerate() {
                let current_ordinal = slot_ordinal;
                slot_ordinal += 1;
                health.transactions_seen += 1;
                let keys = static_keys(&transaction.message);
                let Some(wallet_index) = keys.iter().position(|key| key == &wallet) else {
                    continue;
                };
                let Some(raw_signature) = transaction.signatures.first() else {
                    continue;
                };
                let signature = raw_signature.to_string();
                if !seen.insert(signature.clone()) {
                    health.duplicate_signatures += 1;
                    continue;
                }
                let (wallet_is_signer, wallet_is_writable) =
                    account_flags(&transaction.message, wallet_index);
                let (transaction_version, address_table_lookups) =
                    version_and_lookups(&transaction.message);
                append_json(
                    &mut output,
                    &CaptureFirstCandidate {
                        schema: "CAPTURE_FIRST_2CQ_CANDIDATE_V1",
                        source: "allenhark-subscribe-entries",
                        wallet: TWO_CQ_WALLET,
                        signature,
                        received_at_utc: received_at
                            .to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
                        received_at_unix_ns: received_ns,
                        slot: message.slot,
                        stream_sequence: health.stream_messages,
                        entry_index,
                        transaction_index_within_entry: transaction_index,
                        observed_slot_transaction_ordinal: current_ordinal,
                        transaction_ordinal_semantics:
                            "ALLENHARK_OBSERVED_SLOT_ORDINAL_NOT_CANONICAL",
                        connection_epoch: health.connection_epoch,
                        transaction_version,
                        static_account_keys: keys.iter().map(ToString::to_string).collect(),
                        address_table_lookups,
                        loaded_addresses_available_at_arrival: false,
                        wallet_static_account_index: wallet_index,
                        wallet_is_signer,
                        wallet_is_writable,
                        wallet_is_explicit_account: true,
                    },
                )?;
                health.candidates_written += 1;
                health.last_candidate_at_utc = health.last_receipt_at_utc.clone();
            }
        }
        health.updated_at_utc = Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
        atomic_json(&cfg.health, health)?;
    }

    health.connected = false;
    append_json(
        &mut continuity,
        &Continuity {
            schema: "CAPTURE_FIRST_2CQ_CONTINUITY_V1",
            source: "allenhark-subscribe-entries",
            process_instance: &health.process_instance,
            event: "DISCONNECTED",
            timestamp_utc: Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
            connection_epoch: health.connection_epoch,
            reconnects: health.reconnects,
            last_slot: health.last_slot,
            detail: Some("gRPC stream ended".to_string()),
        },
    )?;
    atomic_json(&cfg.health, health)?;
    anyhow::bail!("AllenHark gRPC stream ended")
}

#[tokio::main]
async fn main() -> Result<()> {
    let cfg = Config::from_env();
    let started_at = Utc::now();
    let process_instance = format!("{}-{}", std::process::id(), started_at.timestamp_nanos_opt().unwrap_or_default());
    let mut health = Health {
        schema: "CAPTURE_FIRST_2CQ_HEALTH_V1",
        source: "allenhark-subscribe-entries",
        process_instance,
        started_at_utc: started_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
        ..Health::default()
    };
    let mut seen = existing_signatures(&cfg.output)?;
    loop {
        if let Err(error) = consume_once(&cfg, &mut health, &mut seen).await {
            health.connected = false;
            health.reconnects += 1;
            health.updated_at_utc = Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
            atomic_json(&cfg.health, &health)?;
            let mut continuity = open_append(&cfg.continuity)?;
            append_json(
                &mut continuity,
                &Continuity {
                    schema: "CAPTURE_FIRST_2CQ_CONTINUITY_V1",
                    source: "allenhark-subscribe-entries",
                    process_instance: &health.process_instance,
                    event: "RECONNECT_SCHEDULED",
                    timestamp_utc: Utc::now()
                        .to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
                    connection_epoch: health.connection_epoch,
                    reconnects: health.reconnects,
                    last_slot: health.last_slot,
                    detail: Some(error.to_string()),
                },
            )?;
            sleep(Duration::from_millis(cfg.reconnect_ms)).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use solana_sdk::{hash::Hash, message::Message, signature::Keypair, signer::Signer, system_instruction};

    #[test]
    fn wallet_presence_is_route_agnostic_and_flags_are_correct() {
        let wallet = Keypair::new();
        let recipient = Pubkey::new_unique();
        let instruction = system_instruction::transfer(&wallet.pubkey(), &recipient, 1);
        let message = VersionedMessage::Legacy(Message::new_with_blockhash(
            &[instruction],
            Some(&wallet.pubkey()),
            &Hash::new_unique(),
        ));
        let index = static_keys(&message)
            .iter()
            .position(|key| key == &wallet.pubkey())
            .expect("wallet key");
        assert_eq!(account_flags(&message, index), (true, true));
    }
}
