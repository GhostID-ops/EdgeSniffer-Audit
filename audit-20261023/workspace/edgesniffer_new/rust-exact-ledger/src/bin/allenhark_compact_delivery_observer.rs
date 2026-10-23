use std::{
    env, fs,
    fs::{File, OpenOptions},
    io::{BufWriter, Write},
    path::{Path, PathBuf},
    time::Duration,
};

use anyhow::{Context, Result};
use bincode;
use chrono::{DateTime, Utc};
use prost::Message;
use serde::Serialize;
use sha2::{Digest, Sha256};
use solana_entry::entry::Entry as SolanaEntry;
use tokio::time::sleep;
use tonic::{codegen::http::uri::PathAndQuery, transport::Endpoint, Request};

const MAGIC: &[u8; 8] = b"AHCDV1\n\0";
const RECORD_BYTES: usize = 48;

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
        Self {
            endpoint: env::var("ALLENHARK_SHREDSTREAM_GRPC_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:9999".to_string()),
            output: PathBuf::from(
                env::var("ALLENHARK_COMPACT_OUTPUT").expect("ALLENHARK_COMPACT_OUTPUT"),
            ),
            health: PathBuf::from(
                env::var("ALLENHARK_COMPACT_HEALTH").expect("ALLENHARK_COMPACT_HEALTH"),
            ),
            continuity: PathBuf::from(
                env::var("ALLENHARK_COMPACT_CONTINUITY").expect("ALLENHARK_COMPACT_CONTINUITY"),
            ),
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
    entries_seen: u64,
    transactions_seen: u64,
    compact_records_written: u64,
    decode_failures: u64,
    first_slot: Option<u64>,
    last_slot: u64,
    last_receipt_at_utc: Option<String>,
    proxy_udp_drops_at_start: u64,
    proxy_udp_drops_current: u64,
    proxy_udp_drops_delta: u64,
    record_bytes: usize,
    estimated_bytes_written: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Continuity<'a> {
    schema: &'static str,
    event: &'static str,
    process_instance: &'a str,
    timestamp_utc: String,
    reconnects: u64,
    last_slot: u64,
    detail: Option<String>,
}

fn open_output(path: &Path) -> Result<BufWriter<File>> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .read(true)
        .open(path)?;
    if file.metadata()?.len() == 0 {
        file.write_all(MAGIC)?;
        file.sync_data()?;
    }
    Ok(BufWriter::with_capacity(1024 * 1024, file))
}

fn open_append(path: &Path) -> Result<BufWriter<File>> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    Ok(BufWriter::new(
        OpenOptions::new().create(true).append(true).open(path)?,
    ))
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

fn append_json<T: Serialize>(writer: &mut BufWriter<File>, value: &T) -> Result<()> {
    serde_json::to_writer(&mut *writer, value)?;
    writer.write_all(b"\n")?;
    writer.flush()?;
    Ok(())
}

fn proxy_udp_drops() -> u64 {
    ["/proc/net/udp", "/proc/net/udp6"]
        .iter()
        .filter_map(|path| fs::read_to_string(path).ok())
        .flat_map(|data| {
            data.lines()
                .skip(1)
                .filter_map(|line| {
                    let fields = line.split_whitespace().collect::<Vec<_>>();
                    let port = fields.get(1)?.rsplit(':').next()?;
                    if !port.eq_ignore_ascii_case("4E20") {
                        return None;
                    }
                    fields.last()?.parse::<u64>().ok()
                })
                .collect::<Vec<_>>()
        })
        .sum()
}

fn write_record(
    writer: &mut BufWriter<File>,
    signature: &[u8],
    received_ns: i64,
    slot: u64,
) -> Result<()> {
    if signature.len() != 64 {
        anyhow::bail!("unexpected signature length {}", signature.len());
    }
    let digest = Sha256::digest(signature);
    let mut record = [0u8; RECORD_BYTES];
    record[..32].copy_from_slice(&digest);
    record[32..40].copy_from_slice(&received_ns.to_le_bytes());
    record[40..48].copy_from_slice(&slot.to_le_bytes());
    writer.write_all(&record)?;
    Ok(())
}

async fn consume_once(cfg: &Config, health: &mut Health) -> Result<()> {
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
    atomic_json(&cfg.health, health)?;
    let mut output = open_output(&cfg.output)?;
    let mut continuity = open_append(&cfg.continuity)?;
    append_json(
        &mut continuity,
        &Continuity {
            schema: "ALLENHARK_COMPACT_DELIVERY_CONTINUITY_V1",
            event: "CONNECTED",
            process_instance: &health.process_instance,
            timestamp_utc: Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
            reconnects: health.reconnects,
            last_slot: health.last_slot,
            detail: None,
        },
    )?;

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
        let entries: Vec<SolanaEntry> = match bincode::deserialize(&message.entries) {
            Ok(value) => value,
            Err(_) => {
                health.decode_failures += 1;
                continue;
            }
        };
        health.entries_seen += entries.len() as u64;
        for entry in &entries {
            for transaction in &entry.transactions {
                health.transactions_seen += 1;
                let Some(signature) = transaction.signatures.first() else {
                    continue;
                };
                write_record(&mut output, signature.as_ref(), received_ns, message.slot)?;
                health.compact_records_written += 1;
            }
        }
        output.flush()?;
        health.proxy_udp_drops_current = proxy_udp_drops();
        health.proxy_udp_drops_delta = health
            .proxy_udp_drops_current
            .saturating_sub(health.proxy_udp_drops_at_start);
        health.estimated_bytes_written =
            health.compact_records_written * RECORD_BYTES as u64 + MAGIC.len() as u64;
        health.updated_at_utc = Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
        atomic_json(&cfg.health, health)?;
    }
    health.connected = false;
    append_json(
        &mut continuity,
        &Continuity {
            schema: "ALLENHARK_COMPACT_DELIVERY_CONTINUITY_V1",
            event: "DISCONNECTED",
            process_instance: &health.process_instance,
            timestamp_utc: Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
            reconnects: health.reconnects,
            last_slot: health.last_slot,
            detail: Some("gRPC stream ended".to_string()),
        },
    )?;
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    let cfg = Config::from_env();
    let started = Utc::now();
    let drops = proxy_udp_drops();
    let mut health = Health {
        schema: "ALLENHARK_COMPACT_DELIVERY_HEALTH_V1",
        source: "allenhark-jito-shreds-grpc-v1",
        process_instance: format!("{}-{}", std::process::id(), started.timestamp_millis()),
        started_at_utc: started.to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
        updated_at_utc: started.to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
        connected: false,
        reconnects: 0,
        stream_messages: 0,
        entries_seen: 0,
        transactions_seen: 0,
        compact_records_written: 0,
        decode_failures: 0,
        first_slot: None,
        last_slot: 0,
        last_receipt_at_utc: None,
        proxy_udp_drops_at_start: drops,
        proxy_udp_drops_current: drops,
        proxy_udp_drops_delta: 0,
        record_bytes: RECORD_BYTES,
        estimated_bytes_written: MAGIC.len() as u64,
    };
    atomic_json(&cfg.health, &health)?;
    loop {
        if let Err(error) = consume_once(&cfg, &mut health).await {
            health.connected = false;
            health.reconnects += 1;
            health.updated_at_utc = Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
            atomic_json(&cfg.health, &health)?;
            let mut continuity = open_append(&cfg.continuity)?;
            append_json(
                &mut continuity,
                &Continuity {
                    schema: "ALLENHARK_COMPACT_DELIVERY_CONTINUITY_V1",
                    event: "DISCONNECTED_ERROR",
                    process_instance: &health.process_instance,
                    timestamp_utc: Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
                    reconnects: health.reconnects,
                    last_slot: health.last_slot,
                    detail: Some(error.to_string()),
                },
            )?;
        }
        sleep(Duration::from_millis(cfg.reconnect_ms)).await;
    }
}
