use std::{
    collections::{HashMap, HashSet},
    env, fs,
    fs::{File, OpenOptions},
    io::{BufRead, BufReader, BufWriter, Write},
    path::{Path, PathBuf},
    time::Duration,
};

use anyhow::{Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use chrono::{DateTime, Utc};
use prost::Message;
use serde::Serialize;
use sha2::{Digest, Sha256};
use solana_entry::entry::Entry as SolanaEntry;
use solana_sdk::message::VersionedMessage;
use tokio::time::sleep;
use tonic::{codegen::http::uri::PathAndQuery, transport::Endpoint, Request};

const PUMP_PROGRAM: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const TWO_CQ_WALLET: &str = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG";
const NATIVE_MINT: &str = "So11111111111111111111111111111111111111112";
const BUY: [u8; 8] = [102, 6, 61, 18, 1, 218, 235, 234];
const BUY_EXACT_SOL_IN: [u8; 8] = [56, 252, 116, 8, 158, 223, 205, 95];
const BUY_V2: [u8; 8] = [184, 23, 238, 97, 103, 197, 211, 61];
const BUY_EXACT_QUOTE_IN_V2: [u8; 8] = [194, 171, 28, 70, 104, 77, 91, 47];
const SELL: [u8; 8] = [51, 230, 133, 164, 1, 127, 131, 173];
const SELL_V2: [u8; 8] = [93, 246, 130, 60, 231, 233, 64, 178];
const CREATE: [u8; 8] = [24, 30, 200, 40, 5, 28, 7, 119];
const CREATE_V2: [u8; 8] = [214, 144, 76, 236, 95, 139, 49, 180];
const ALL_TX_MAGIC: &[u8; 8] = b"AHATX01\n";
const ALL_TX_RECORD_BYTES: usize = 128;

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
    candidate_output: PathBuf,
    health: PathBuf,
    continuity: PathBuf,
    all_transaction_root: PathBuf,
    reconnect_ms: u64,
}

impl Config {
    fn from_env() -> Self {
        Self {
            endpoint: env::var("ALLENHARK_SHREDSTREAM_GRPC_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:9999".to_string()),
            output: env::var("ALLENHARK_PUMP_ARRIVAL_OUTPUT")
                .map(PathBuf::from)
                .unwrap_or_else(|_| PathBuf::from("/var/lib/pump-exact-evidence-ledger-v1/allenhark-arrivals-v1/pump-transactions.jsonl")),
            candidate_output: env::var("ALLENHARK_2CQ_DETECTOR_OUTPUT")
                .map(PathBuf::from)
                .unwrap_or_else(|_| PathBuf::from("/var/lib/pump-exact-evidence-ledger-v1/allenhark-arrivals-v1/2cq-detected-swaps.jsonl")),
            health: env::var("ALLENHARK_ARRIVAL_HEALTH_PATH")
                .map(PathBuf::from)
                .unwrap_or_else(|_| PathBuf::from("/var/lib/pump-exact-evidence-ledger-v1/allenhark-arrivals-v1/health.json")),
            continuity: env::var("ALLENHARK_CONTINUITY_OUTPUT")
                .map(PathBuf::from)
                .unwrap_or_else(|_| PathBuf::from("/var/lib/pump-exact-evidence-ledger-v1/allenhark-arrivals-v1/continuity.jsonl")),
            all_transaction_root: env::var("ALLENHARK_ALL_TRANSACTION_ROOT")
                .map(PathBuf::from)
                .unwrap_or_else(|_| PathBuf::from("/var/lib/pump-exact-evidence-ledger-v1/allenhark-arrivals-v1/all-transactions")),
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
    reconnects: u64,
    stream_messages: u64,
    transactions_seen: u64,
    all_transactions_persisted: u64,
    pump_transactions_written: u64,
    two_cq_candidates_written: u64,
    decode_failures: u64,
    duplicate_signatures: u64,
    last_slot: u64,
    last_receipt_at_utc: Option<String>,
    last_receipt_unix_ns: Option<i64>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PumpArrival<'a> {
    schema: &'static str,
    source: &'static str,
    received_at_utc: String,
    received_at_unix_ns: i64,
    slot: u64,
    stream_sequence: u64,
    entry_index: usize,
    transaction_index_within_entry: usize,
    observed_slot_transaction_ordinal: u64,
    signature: &'a str,
    pump_instruction_kinds: &'a [String],
    pump_instruction_discriminators_base64: &'a [String],
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct TwoCqCandidate<'a> {
    kind: &'static str,
    source: &'static str,
    detected_at_ms: i64,
    detected_at_unix_ns: i64,
    signature: &'a str,
    wallet: &'static str,
    input_mint: &'static str,
    output_mint: &'a str,
    amount_raw: Option<u64>,
    is_native_input: bool,
    counterparty_amount_raw: Option<u64>,
    source_ix_accounts: &'a [String],
    source_ix_data_base64: String,
    source_pump_swap_instruction: Option<&'a str>,
    source_pump_swap_accounts: Option<&'a [String]>,
    source_quote_mint: &'static str,
    source_quote_amount_raw: Option<u64>,
    source_slot: u64,
    source_transaction_index: u64,
    source_transaction_index_semantics: &'static str,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ContinuityEvent<'a> {
    schema: &'static str,
    source: &'static str,
    process_instance: &'a str,
    event: &'static str,
    timestamp_utc: String,
    timestamp_unix_ns: i64,
    reconnects: u64,
    last_slot: u64,
    detail: Option<String>,
}

fn instruction_kind(data: &[u8]) -> Option<&'static str> {
    let discriminator = data.get(..8)?;
    if discriminator == BUY {
        Some("BUY")
    } else if discriminator == BUY_EXACT_SOL_IN {
        Some("BUY_EXACT_SOL_IN")
    } else if discriminator == BUY_V2 {
        Some("BUY_V2")
    } else if discriminator == BUY_EXACT_QUOTE_IN_V2 {
        Some("BUY_EXACT_QUOTE_IN_V2")
    } else if discriminator == SELL {
        Some("SELL")
    } else if discriminator == SELL_V2 {
        Some("SELL_V2")
    } else if discriminator == CREATE {
        Some("CREATE")
    } else if discriminator == CREATE_V2 {
        Some("CREATE_V2")
    } else {
        Some("OTHER")
    }
}

fn is_buy(kind: &str) -> bool {
    matches!(
        kind,
        "BUY" | "BUY_EXACT_SOL_IN" | "BUY_V2" | "BUY_EXACT_QUOTE_IN_V2"
    )
}

fn exact_sol_amount(kind: &str, data: &[u8]) -> Option<u64> {
    (kind == "BUY_EXACT_SOL_IN")
        .then(|| data.get(8..16))
        .flatten()
        .and_then(|bytes| bytes.try_into().ok())
        .map(u64::from_le_bytes)
}

fn message_parts(
    message: &VersionedMessage,
) -> (
    &[solana_sdk::pubkey::Pubkey],
    &[solana_sdk::instruction::CompiledInstruction],
) {
    match message {
        VersionedMessage::Legacy(value) => (&value.account_keys, &value.instructions),
        VersionedMessage::V0(value) => (&value.account_keys, &value.instructions),
    }
}

fn append_line<T: Serialize>(writer: &mut BufWriter<File>, value: &T) -> Result<()> {
    serde_json::to_writer(&mut *writer, value)?;
    writer.write_all(b"\n")?;
    Ok(())
}

fn open_append(path: &Path) -> Result<BufWriter<File>> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    Ok(BufWriter::new(
        OpenOptions::new().create(true).append(true).open(path)?,
    ))
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

fn atomic_health<T: Serialize>(path: &Path, health: &T) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = path.with_extension("json.tmp");
    fs::write(&temporary, serde_json::to_vec_pretty(health)?)?;
    fs::rename(temporary, path)?;
    Ok(())
}

fn process_instance_hash(value: &str) -> u64 {
    let digest = Sha256::digest(value.as_bytes());
    u64::from_le_bytes(digest[..8].try_into().expect("sha256 prefix"))
}

fn all_transaction_writer(root: &Path, received_at: DateTime<Utc>) -> Result<BufWriter<File>> {
    let directory = root.join(received_at.format("%Y/%m/%d/%H").to_string());
    fs::create_dir_all(&directory)?;
    let path = directory.join("arrivals.bin.partial");
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .read(true)
        .open(&path)?;
    if file.metadata()?.len() == 0 {
        file.write_all(ALL_TX_MAGIC)?;
        file.sync_data()?;
    }
    Ok(BufWriter::with_capacity(1024 * 1024, file))
}

#[allow(clippy::too_many_arguments)]
fn append_all_transaction(
    writer: &mut BufWriter<File>,
    signature: &[u8],
    received_ns: i64,
    slot: u64,
    stream_sequence: u64,
    entry_index: usize,
    transaction_index: usize,
    observed_slot_ordinal: u64,
    connection_epoch: u64,
    process_hash: u64,
) -> Result<()> {
    if signature.len() != 64 {
        anyhow::bail!("unexpected Solana signature length {}", signature.len());
    }
    let mut record = [0u8; ALL_TX_RECORD_BYTES];
    record[..64].copy_from_slice(signature);
    record[64..72].copy_from_slice(&received_ns.to_le_bytes());
    record[72..80].copy_from_slice(&slot.to_le_bytes());
    record[80..88].copy_from_slice(&stream_sequence.to_le_bytes());
    record[88..92].copy_from_slice(&(entry_index as u32).to_le_bytes());
    record[92..96].copy_from_slice(&(transaction_index as u32).to_le_bytes());
    record[96..104].copy_from_slice(&observed_slot_ordinal.to_le_bytes());
    record[104..112].copy_from_slice(&connection_epoch.to_le_bytes());
    record[112..120].copy_from_slice(&process_hash.to_le_bytes());
    record[120..128].copy_from_slice(&1u64.to_le_bytes());
    writer.write_all(&record)?;
    Ok(())
}

async fn consume_once(
    cfg: &Config,
    health: &mut Health,
    seen: &mut HashSet<String>,
    slot_ordinals: &mut HashMap<u64, u64>,
) -> Result<()> {
    let channel = Endpoint::from_shared(cfg.endpoint.clone())?
        .connect()
        .await?;
    let mut grpc = tonic::client::Grpc::new(channel);
    grpc.ready().await.context("AllenHark gRPC not ready")?;
    let path = PathAndQuery::from_static("/shredstream.ShredstreamProxy/SubscribeEntries");
    let codec = tonic_prost::ProstCodec::<SubscribeEntriesRequest, ShredstreamEntry>::default();
    let mut stream = grpc
        .server_streaming(Request::new(SubscribeEntriesRequest {}), path, codec)
        .await?
        .into_inner();
    health.connected = true;
    atomic_health(&cfg.health, health)?;
    let mut output = open_append(&cfg.output)?;
    let mut candidates = open_append(&cfg.candidate_output)?;
    let mut continuity = open_append(&cfg.continuity)?;
    let mut all_transaction_hour = String::new();
    let mut all_transactions: Option<BufWriter<File>> = None;
    let process_hash = process_instance_hash(&health.process_instance);
    let connected_at = Utc::now();
    append_line(
        &mut continuity,
        &ContinuityEvent {
            schema: "ALLENHARK_ARRIVAL_CONTINUITY_V1",
            source: "allenhark-jito-shreds-grpc-v1",
            process_instance: &health.process_instance,
            event: "CONNECTED",
            timestamp_utc: connected_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
            timestamp_unix_ns: connected_at
                .timestamp_nanos_opt()
                .context("continuity timestamp overflow")?,
            reconnects: health.reconnects,
            last_slot: health.last_slot,
            detail: None,
        },
    )?;
    continuity.flush()?;

    while let Some(message) = stream.message().await? {
        let received_at: DateTime<Utc> = Utc::now();
        let received_ns = received_at
            .timestamp_nanos_opt()
            .context("receipt timestamp overflow")?;
        health.stream_messages += 1;
        health.last_slot = message.slot;
        health.last_receipt_at_utc =
            Some(received_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true));
        health.last_receipt_unix_ns = Some(received_ns);
        let entries: Vec<SolanaEntry> = match bincode::deserialize(&message.entries) {
            Ok(value) => value,
            Err(_) => {
                health.decode_failures += 1;
                continue;
            }
        };
        let slot_ordinal = slot_ordinals.entry(message.slot).or_insert(0);
        let receipt_hour = received_at.format("%Y/%m/%d/%H").to_string();
        if receipt_hour != all_transaction_hour {
            if let Some(writer) = all_transactions.as_mut() {
                writer.flush()?;
                writer.get_ref().sync_data()?;
            }
            all_transactions = Some(all_transaction_writer(
                &cfg.all_transaction_root,
                received_at,
            )?);
            all_transaction_hour = receipt_hour;
        }
        for (entry_index, entry) in entries.iter().enumerate() {
            for (tx_index, transaction) in entry.transactions.iter().enumerate() {
                health.transactions_seen += 1;
                let Some(raw_signature) = transaction.signatures.first() else {
                    *slot_ordinal += 1;
                    continue;
                };
                let signature = raw_signature.to_string();
                let current_ordinal = *slot_ordinal;
                *slot_ordinal += 1;
                append_all_transaction(
                    all_transactions
                        .as_mut()
                        .context("all-transaction writer unavailable")?,
                    raw_signature.as_ref(),
                    received_ns,
                    message.slot,
                    health.stream_messages,
                    entry_index,
                    tx_index,
                    current_ordinal,
                    health.reconnects,
                    process_hash,
                )?;
                health.all_transactions_persisted += 1;
                let (keys, instructions) = message_parts(&transaction.message);
                let mut pump = Vec::new();
                for instruction in instructions {
                    let Some(program) = keys.get(instruction.program_id_index as usize) else {
                        continue;
                    };
                    if program.to_string() == PUMP_PROGRAM {
                        let accounts = instruction
                            .accounts
                            .iter()
                            .filter_map(|index| keys.get(*index as usize).map(ToString::to_string))
                            .collect::<Vec<_>>();
                        let kind = instruction_kind(&instruction.data)
                            .unwrap_or("OTHER")
                            .to_string();
                        pump.push((kind, instruction.data.clone(), accounts));
                    }
                }
                if pump.is_empty() {
                    continue;
                }
                if !seen.insert(signature.clone()) {
                    health.duplicate_signatures += 1;
                    continue;
                }
                let kinds = pump.iter().map(|item| item.0.clone()).collect::<Vec<_>>();
                let discriminators = pump
                    .iter()
                    .map(|item| BASE64_STANDARD.encode(item.1.get(..8).unwrap_or(&item.1)))
                    .collect::<Vec<_>>();
                append_line(
                    &mut output,
                    &PumpArrival {
                        schema: "ALLENHARK_PUMP_ARRIVAL_V1",
                        source: "allenhark-jito-shreds-grpc-v1",
                        received_at_utc: received_at
                            .to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
                        received_at_unix_ns: received_ns,
                        slot: message.slot,
                        stream_sequence: health.stream_messages,
                        entry_index,
                        transaction_index_within_entry: tx_index,
                        observed_slot_transaction_ordinal: current_ordinal,
                        signature: &signature,
                        pump_instruction_kinds: &kinds,
                        pump_instruction_discriminators_base64: &discriminators,
                    },
                )?;
                health.pump_transactions_written += 1;

                for (kind, data, accounts) in &pump {
                    if !is_buy(kind) || accounts.get(6).map(String::as_str) != Some(TWO_CQ_WALLET) {
                        continue;
                    }
                    let Some(mint) = accounts.get(2) else {
                        continue;
                    };
                    let amount = exact_sol_amount(kind, data);
                    append_line(
                        &mut candidates,
                        &TwoCqCandidate {
                            kind: "swap",
                            source: "allenhark-jito-shreds-grpc-v1",
                            detected_at_ms: received_at.timestamp_millis(),
                            detected_at_unix_ns: received_ns,
                            signature: &signature,
                            wallet: TWO_CQ_WALLET,
                            input_mint: NATIVE_MINT,
                            output_mint: mint,
                            amount_raw: amount,
                            is_native_input: true,
                            counterparty_amount_raw: None,
                            source_ix_accounts: accounts,
                            source_ix_data_base64: BASE64_STANDARD.encode(data),
                            source_pump_swap_instruction: None,
                            source_pump_swap_accounts: None,
                            source_quote_mint: NATIVE_MINT,
                            source_quote_amount_raw: amount,
                            source_slot: message.slot,
                            source_transaction_index: current_ordinal,
                            source_transaction_index_semantics:
                                "ALLENHARK_OBSERVED_SLOT_ORDINAL_NOT_CANONICAL",
                        },
                    )?;
                    health.two_cq_candidates_written += 1;
                }
            }
        }
        output.flush()?;
        candidates.flush()?;
        if let Some(writer) = all_transactions.as_mut() {
            writer.flush()?;
        }
        health.updated_at_utc = Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
        atomic_health(&cfg.health, health)?;
        slot_ordinals.retain(|slot, _| *slot + 64 >= message.slot);
    }
    health.connected = false;
    let disconnected_at = Utc::now();
    append_line(
        &mut continuity,
        &ContinuityEvent {
            schema: "ALLENHARK_ARRIVAL_CONTINUITY_V1",
            source: "allenhark-jito-shreds-grpc-v1",
            process_instance: &health.process_instance,
            event: "DISCONNECTED",
            timestamp_utc: disconnected_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
            timestamp_unix_ns: disconnected_at
                .timestamp_nanos_opt()
                .context("continuity timestamp overflow")?,
            reconnects: health.reconnects,
            last_slot: health.last_slot,
            detail: Some("gRPC stream ended without an explicit error".to_string()),
        },
    )?;
    continuity.flush()?;
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    let cfg = Config::from_env();
    let started = Utc::now();
    let mut health = Health {
        schema: "ALLENHARK_ARRIVAL_OBSERVER_HEALTH_V1",
        source: "allenhark-jito-shreds-grpc-v1",
        process_instance: format!("{}-{}", std::process::id(), started.timestamp_millis()),
        started_at_utc: started.to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
        updated_at_utc: started.to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
        ..Health::default()
    };
    fs::create_dir_all(&cfg.all_transaction_root)?;
    atomic_health(
        &cfg.all_transaction_root.join("schema.json"),
        &serde_json::json!({
            "schema": "ALLENHARK_ALL_TRANSACTION_ARRIVAL_BINARY_V1",
            "magicAscii": "AHATX01\\n",
            "recordBytes": ALL_TX_RECORD_BYTES,
            "byteOrder": "little-endian",
            "fields": [
                {"name": "signature", "offset": 0, "bytes": 64, "type": "raw Solana signature"},
                {"name": "receivedAtUnixNs", "offset": 64, "bytes": 8, "type": "i64"},
                {"name": "slot", "offset": 72, "bytes": 8, "type": "u64"},
                {"name": "streamSequence", "offset": 80, "bytes": 8, "type": "u64"},
                {"name": "entryIndex", "offset": 88, "bytes": 4, "type": "u32"},
                {"name": "transactionIndexWithinEntry", "offset": 92, "bytes": 4, "type": "u32"},
                {"name": "observedSlotTransactionOrdinal", "offset": 96, "bytes": 8, "type": "u64"},
                {"name": "connectionEpoch", "offset": 104, "bytes": 8, "type": "u64"},
                {"name": "processInstanceHash", "offset": 112, "bytes": 8, "type": "u64"},
                {"name": "connectedAtCapture", "offset": 120, "bytes": 8, "type": "u64"}
            ],
            "captureBoundary": "recorded before Pump classification",
            "orderingSemantics": "observed shred entry order; canonical ordering comes only from finalized exact-ledger evidence"
        }),
    )?;
    let mut seen = existing_signatures(&cfg.output)?;
    let mut slot_ordinals = HashMap::new();
    loop {
        if let Err(error) = consume_once(&cfg, &mut health, &mut seen, &mut slot_ordinals).await {
            eprintln!("AllenHark arrival observer disconnected: {error:#}");
            let at = Utc::now();
            let mut continuity = open_append(&cfg.continuity)?;
            append_line(
                &mut continuity,
                &ContinuityEvent {
                    schema: "ALLENHARK_ARRIVAL_CONTINUITY_V1",
                    source: "allenhark-jito-shreds-grpc-v1",
                    process_instance: &health.process_instance,
                    event: "DISCONNECTED_ERROR",
                    timestamp_utc: at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
                    timestamp_unix_ns: at
                        .timestamp_nanos_opt()
                        .context("continuity timestamp overflow")?,
                    reconnects: health.reconnects,
                    last_slot: health.last_slot,
                    detail: Some(format!("{error:#}")),
                },
            )?;
            continuity.flush()?;
        }
        health.connected = false;
        health.reconnects += 1;
        health.updated_at_utc = Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
        atomic_health(&cfg.health, &health)?;
        sleep(Duration::from_millis(cfg.reconnect_ms)).await;
    }
}
