use std::{
    collections::{HashSet, VecDeque},
    fs::{self, File, OpenOptions},
    io::{BufReader, BufWriter, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
};

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Datelike, Timelike, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const SCHEMA_VERSION: &str = "PUMP_EXACT_EVIDENCE_LEDGER_V1";
pub const FRAME_MAGIC: &[u8; 8] = b"PEL1FRM\0";
const FRAME_PREFIX_BYTES: usize = 8;
const FRAME_DIGEST_BYTES: usize = 32;
const DEDUPE_WINDOW_IDS: usize = 500_000;

pub const SCHEMA_DOCUMENT: &str = r#"{
  "name":"PUMP_EXACT_EVIDENCE_LEDGER_V1",
  "frame":"u32le metadata_len,u32le raw_len,metadata_json,raw_payload,sha256(metadata_json||raw_payload)",
  "raw_formats":"yellowstone_subscribe_update_protobuf or solana_json_rpc_response, explicit per frame",
  "identity":"sha256(kind|slot|transaction_index|instruction_coordinates|signature_or_pubkey|write_version|raw_sha256)",
  "chain_order":"finalized getBlock signature index,outer_instruction_index,inner_instruction_index,stack_height",
  "local_order":"received_at_unix_ns,writer_sequence",
  "finality":"RabbitStream observations carry no success/failure authority; finalized getTransaction reconciliation is authoritative; failed transactions are retained and mutate zero replay state",
  "coverage":"provisional observation counters are separate from authoritative finalized/successful/failed/unresolved transaction counters",
  "partition":"UTC receive hour; zstd; atomic checksum-verified temporal close; exact replay completion is proven separately by a cross-partition reconciliation manifest",
  "raw_truth":"yellowstone SubscribeUpdate protobuf retained byte-for-byte",
  "unsupported":"coverage gaps are explicit counters and never silently replaced with market-cap proxy semantics"
}"#;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct EvidenceMetadata {
    pub schema_version: String,
    pub evidence_id: String,
    pub raw_sha256: String,
    pub kind: String,
    pub raw_format: String,
    pub received_at_utc: String,
    pub received_at_unix_ns: i64,
    pub decode_at_unix_ns: i64,
    pub writer_sequence: u64,
    pub slot: Option<u64>,
    pub transaction_index: Option<u64>,
    pub signature: Option<String>,
    pub account_pubkey: Option<String>,
    pub account_owner: Option<String>,
    pub write_version: Option<u64>,
    pub finalized: bool,
    pub successful: Option<bool>,
    pub failed: Option<bool>,
    pub unresolved: bool,
    pub pump_instruction_kinds: Vec<String>,
    pub pump_instruction_coordinates: Vec<String>,
    pub discovered_mints: Vec<String>,
    pub has_trade_event: bool,
    pub has_fee_evidence: bool,
    pub missing_raw_tx: bool,
    pub missing_trade_event: bool,
    pub missing_reserve_transition: bool,
    pub missing_canonical_ordering: bool,
    pub missing_fee_evidence: bool,
    pub decode_failed: bool,
    pub filters: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(default, rename_all = "camelCase")]
pub struct CoverageCounters {
    pub records: u64,
    pub relevant_transactions_observed: u64,
    pub provisional_transactions: u64,
    pub provisional_unresolved: u64,
    pub finalized: u64,
    pub successful: u64,
    pub failed: u64,
    pub unresolved: u64,
    pub missing_finality: u64,
    pub missing_raw_tx: u64,
    pub missing_trade_event: u64,
    pub missing_reserve_transition: u64,
    pub missing_canonical_ordering: u64,
    pub missing_fee_evidence: u64,
    pub decode_failures: u64,
    pub account_updates: u64,
    pub block_meta_updates: u64,
    pub slot_updates: u64,
    pub rpc_snapshots: u64,
    pub rpc_finalized_transactions: u64,
    pub rpc_finalized_blocks: u64,
    pub duplicates_suppressed: u64,
}

impl CoverageCounters {
    pub fn observe(&mut self, metadata: &EvidenceMetadata) {
        self.records += 1;
        match metadata.kind.as_str() {
            "transaction" => self.relevant_transactions_observed += 1,
            "account" => self.account_updates += 1,
            "block_meta" => self.block_meta_updates += 1,
            "slot" => self.slot_updates += 1,
            "rpc_account_snapshot" => self.rpc_snapshots += 1,
            "rpc_finalized_transaction" => self.rpc_finalized_transactions += 1,
            "rpc_finalized_block" => self.rpc_finalized_blocks += 1,
            _ => {}
        }
        let provisional_transaction = metadata.kind == "transaction" && !metadata.finalized;
        if provisional_transaction {
            self.provisional_transactions += 1;
            self.provisional_unresolved += u64::from(metadata.unresolved);
        }
        let authoritative_transaction = (metadata.kind == "transaction" && metadata.finalized)
            || metadata.kind == "rpc_finalized_transaction";
        if authoritative_transaction {
            self.finalized += u64::from(metadata.finalized);
            self.successful += u64::from(metadata.successful == Some(true));
            self.failed += u64::from(metadata.failed == Some(true));
            self.unresolved += u64::from(metadata.unresolved);
            self.missing_finality += u64::from(!metadata.finalized);
            self.missing_raw_tx += u64::from(metadata.missing_raw_tx);
            self.missing_trade_event += u64::from(metadata.missing_trade_event);
            self.missing_reserve_transition += u64::from(metadata.missing_reserve_transition);
            self.missing_canonical_ordering += u64::from(metadata.missing_canonical_ordering);
            self.missing_fee_evidence += u64::from(metadata.missing_fee_evidence);
        }
        self.decode_failures += u64::from(metadata.decode_failed);
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PartitionManifest {
    pub schema_version: String,
    pub schema_sha256: String,
    pub partition_key: String,
    #[serde(default)]
    pub complete_utc_hour: bool,
    #[serde(default)]
    pub temporal_hour_closed: bool,
    #[serde(default)]
    pub exact_replay_complete: bool,
    #[serde(default)]
    pub reconciliation_manifest: Option<String>,
    pub opened_at_utc: String,
    pub closed_at_utc: String,
    pub start_slot: Option<u64>,
    pub end_slot: Option<u64>,
    pub raw_frame_bytes: u64,
    pub compressed_bytes: u64,
    pub raw_sha256: String,
    pub compressed_sha256: String,
    pub coverage: CoverageCounters,
}

#[derive(Debug, Clone)]
pub struct FrameRecord {
    pub metadata: EvidenceMetadata,
    pub raw: Vec<u8>,
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub fn file_sha256(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1024 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

pub fn partition_key(timestamp: DateTime<Utc>) -> String {
    format!(
        "{:04}/{:02}/{:02}/{:02}",
        timestamp.year(),
        timestamp.month(),
        timestamp.day(),
        timestamp.hour()
    )
}

fn frame_bytes(metadata: &EvidenceMetadata, raw: &[u8]) -> Result<Vec<u8>> {
    let metadata_bytes = serde_json::to_vec(metadata)?;
    let metadata_len = u32::try_from(metadata_bytes.len()).context("metadata frame too large")?;
    let raw_len = u32::try_from(raw.len()).context("raw frame too large")?;
    let mut payload = Vec::with_capacity(metadata_bytes.len() + raw.len());
    payload.extend_from_slice(&metadata_bytes);
    payload.extend_from_slice(raw);
    let digest = Sha256::digest(&payload);
    let mut frame = Vec::with_capacity(FRAME_PREFIX_BYTES + payload.len() + FRAME_DIGEST_BYTES);
    frame.extend_from_slice(&metadata_len.to_le_bytes());
    frame.extend_from_slice(&raw_len.to_le_bytes());
    frame.extend_from_slice(&payload);
    frame.extend_from_slice(&digest);
    Ok(frame)
}

pub fn scan_and_recover_frames(path: &Path, truncate_tail: bool) -> Result<Vec<FrameRecord>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let mut file = OpenOptions::new()
        .read(true)
        .write(truncate_tail)
        .open(path)?;
    let file_len = file.metadata()?.len();
    let mut magic = [0u8; 8];
    if file_len < magic.len() as u64 {
        if truncate_tail {
            file.set_len(0)?;
            file.write_all(FRAME_MAGIC)?;
            file.sync_data()?;
            return Ok(Vec::new());
        }
        bail!("truncated frame header: {}", path.display());
    }
    file.read_exact(&mut magic)?;
    if &magic != FRAME_MAGIC {
        bail!("invalid frame magic: {}", path.display());
    }

    let mut records = Vec::new();
    let mut valid_end = FRAME_MAGIC.len() as u64;
    loop {
        let frame_start = file.stream_position()?;
        if frame_start == file_len {
            break;
        }
        let mut prefix = [0u8; FRAME_PREFIX_BYTES];
        if let Err(error) = file.read_exact(&mut prefix) {
            if truncate_tail && error.kind() == std::io::ErrorKind::UnexpectedEof {
                file.set_len(valid_end)?;
                break;
            }
            return Err(error.into());
        }
        let metadata_len = u32::from_le_bytes(prefix[..4].try_into().unwrap()) as usize;
        let raw_len = u32::from_le_bytes(prefix[4..].try_into().unwrap()) as usize;
        let body_len = metadata_len
            .checked_add(raw_len)
            .context("frame body length overflow")?;
        if body_len > 128 * 1024 * 1024 {
            if truncate_tail {
                file.set_len(valid_end)?;
                break;
            }
            bail!("frame exceeds safety limit at offset {frame_start}");
        }
        let mut body = vec![0u8; body_len];
        let mut digest = [0u8; FRAME_DIGEST_BYTES];
        if file.read_exact(&mut body).is_err() || file.read_exact(&mut digest).is_err() {
            if truncate_tail {
                file.set_len(valid_end)?;
                break;
            }
            bail!("truncated frame at offset {frame_start}");
        }
        if Sha256::digest(&body).as_slice() != digest {
            if truncate_tail {
                file.set_len(valid_end)?;
                break;
            }
            bail!("frame checksum mismatch at offset {frame_start}");
        }
        let metadata: EvidenceMetadata = serde_json::from_slice(&body[..metadata_len])
            .with_context(|| format!("metadata decode failed at offset {frame_start}"))?;
        records.push(FrameRecord {
            metadata,
            raw: body[metadata_len..].to_vec(),
        });
        valid_end = file.stream_position()?;
    }
    Ok(records)
}

struct OpenPartition {
    key: String,
    path: PathBuf,
    writer: BufWriter<File>,
    opened_at: DateTime<Utc>,
    start_slot: Option<u64>,
    end_slot: Option<u64>,
    coverage: CoverageCounters,
}

// A temporal seal is durable before compression begins. This lets the live
// writer advance into the next receive-hour while checksum/compression runs
// outside the canonical receipt path.
struct DetachedPartition {
    key: String,
    path: PathBuf,
    opened_at: DateTime<Utc>,
    start_slot: Option<u64>,
    end_slot: Option<u64>,
    coverage: CoverageCounters,
}

pub struct LedgerWriter {
    root: PathBuf,
    dedupe_path: PathBuf,
    dedupe_writer: BufWriter<File>,
    seen: HashSet<[u8; 32]>,
    seen_order: VecDeque<[u8; 32]>,
    partition: Option<OpenPartition>,
}

impl LedgerWriter {
    pub fn open(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref().to_path_buf();
        fs::create_dir_all(root.join("partitions"))?;
        fs::create_dir_all(root.join("state"))?;
        fs::write(root.join("schema-v1.json"), SCHEMA_DOCUMENT.as_bytes())?;
        let dedupe_path = root.join("state/evidence-ids.bin");
        let mut seen = HashSet::new();
        let mut seen_order = VecDeque::new();
        if dedupe_path.exists() {
            let mut bytes = fs::read(&dedupe_path)?;
            let valid_len = bytes.len() - (bytes.len() % 32);
            if valid_len != bytes.len() {
                bytes.truncate(valid_len);
                fs::write(&dedupe_path, &bytes)?;
            }
            let keep_from = bytes.len().saturating_sub(DEDUPE_WINDOW_IDS * 32);
            for chunk in bytes[keep_from..].chunks_exact(32) {
                let id = chunk.try_into().unwrap();
                if seen.insert(id) {
                    seen_order.push_back(id);
                }
            }
            if keep_from > 0 {
                fs::write(&dedupe_path, &bytes[keep_from..])?;
            }
        }
        let dedupe_writer = BufWriter::new(
            OpenOptions::new()
                .create(true)
                .append(true)
                .open(&dedupe_path)?,
        );
        let mut writer = Self {
            root,
            dedupe_path,
            dedupe_writer,
            seen,
            seen_order,
            partition: None,
        };
        writer.recover_partial_partitions()?;
        Ok(writer)
    }

    fn recover_partial_partitions(&mut self) -> Result<()> {
        let partition_root = self.root.join("partitions");
        if !partition_root.exists() {
            return Ok(());
        }
        let mut stack = vec![partition_root];
        while let Some(path) = stack.pop() {
            for entry in fs::read_dir(path)? {
                let entry = entry?;
                if entry.file_type()?.is_dir() {
                    stack.push(entry.path());
                } else if entry.file_name() == "evidence.frames.partial" {
                    for record in scan_and_recover_frames(&entry.path(), true)? {
                        if let Ok(id) = decode_id(&record.metadata.evidence_id) {
                            if self.remember_id(id) {
                                self.dedupe_writer.write_all(&id)?;
                            }
                        }
                    }
                }
            }
        }
        self.dedupe_writer.flush()?;
        Ok(())
    }

    fn open_partition(&mut self, key: &str, now: DateTime<Utc>) -> Result<()> {
        let dir = self.root.join("partitions").join(key);
        fs::create_dir_all(&dir)?;
        let path = dir.join("evidence.frames.partial");
        let compressed = dir.join("evidence.frames.zst");
        let manifest_path = dir.join("manifest.json");
        let seal_path = dir.join("temporal-seal.json");
        if seal_path.exists() {
            bail!("refusing to reopen temporally sealed partition {key}");
        }
        if manifest_path.exists() {
            let manifest: PartitionManifest = serde_json::from_slice(&fs::read(&manifest_path)?)?;
            if manifest.complete_utc_hour || manifest.temporal_hour_closed {
                bail!("refusing to reopen immutable temporally closed partition {key}");
            }
        }
        let mut opened_at = now;
        if !path.exists() && compressed.exists() {
            let manifest: PartitionManifest = serde_json::from_slice(&fs::read(&manifest_path)?)?;
            if manifest.complete_utc_hour || manifest.temporal_hour_closed {
                bail!("refusing to reopen immutable temporally closed partition {key}");
            }
            if file_sha256(&compressed)? != manifest.compressed_sha256 {
                bail!("cannot reopen partition with invalid compressed checksum {key}");
            }
            let mut decoder = zstd::Decoder::new(File::open(&compressed)?)?;
            let mut output = BufWriter::new(File::create(&path)?);
            std::io::copy(&mut decoder, &mut output)?;
            output.flush()?;
            if file_sha256(&path)? != manifest.raw_sha256 {
                bail!("cannot reopen partition with invalid raw checksum {key}");
            }
            opened_at = DateTime::parse_from_rfc3339(&manifest.opened_at_utc)?.with_timezone(&Utc);
        }
        let is_new = !path.exists() || path.metadata()?.len() == 0;
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .read(true)
            .open(&path)?;
        if is_new {
            file.write_all(FRAME_MAGIC)?;
            file.sync_data()?;
        } else {
            let _ = scan_and_recover_frames(&path, true)?;
            file.seek(SeekFrom::End(0))?;
        }
        let mut coverage = CoverageCounters::default();
        let mut start_slot = None;
        let mut end_slot = None;
        for record in scan_and_recover_frames(&path, false)? {
            coverage.observe(&record.metadata);
            if let Some(slot) = record.metadata.slot {
                start_slot = Some(start_slot.map_or(slot, |current: u64| current.min(slot)));
                end_slot = Some(end_slot.map_or(slot, |current: u64| current.max(slot)));
            }
        }
        self.partition = Some(OpenPartition {
            key: key.to_string(),
            path,
            writer: BufWriter::with_capacity(4 * 1024 * 1024, file),
            opened_at,
            start_slot,
            end_slot,
            coverage,
        });
        Ok(())
    }

    pub fn append(&mut self, timestamp: DateTime<Utc>, record: FrameRecord) -> Result<bool> {
        let id = decode_id(&record.metadata.evidence_id)?;
        if self.seen.contains(&id) {
            if let Some(partition) = self.partition.as_mut() {
                partition.coverage.duplicates_suppressed += 1;
            }
            return Ok(false);
        }
        let key = partition_key(timestamp);
        if self
            .partition
            .as_ref()
            .is_some_and(|partition| partition.key != key)
        {
            self.seal_current_async()?;
        }
        if self.partition.is_none() {
            self.open_partition(&key, timestamp)?;
        }
        let bytes = frame_bytes(&record.metadata, &record.raw)?;
        let partition = self.partition.as_mut().unwrap();
        partition.writer.write_all(&bytes)?;
        partition.writer.flush()?;
        partition.writer.get_ref().sync_data()?;
        partition.coverage.observe(&record.metadata);
        if let Some(slot) = record.metadata.slot {
            partition.start_slot = Some(
                partition
                    .start_slot
                    .map_or(slot, |current| current.min(slot)),
            );
            partition.end_slot = Some(partition.end_slot.map_or(slot, |current| current.max(slot)));
        }
        self.dedupe_writer.write_all(&id)?;
        self.dedupe_writer.flush()?;
        self.remember_id(id);
        self.compact_dedupe_if_needed()?;
        Ok(true)
    }

    /// Append a frame while keeping crash recovery intact, but defer the costly
    /// `sync_data` syscall to a bounded batch. Call `sync_buffered` at the end
    /// of each batch (or on a short timer) before treating the batch as durable.
    pub fn append_buffered(&mut self, timestamp: DateTime<Utc>, record: FrameRecord) -> Result<bool> {
        let id = decode_id(&record.metadata.evidence_id)?;
        if self.seen.contains(&id) {
            if let Some(partition) = self.partition.as_mut() {
                partition.coverage.duplicates_suppressed += 1;
            }
            return Ok(false);
        }
        let key = partition_key(timestamp);
        if self
            .partition
            .as_ref()
            .is_some_and(|partition| partition.key != key)
        {
            self.seal_current_async()?;
        }
        if self.partition.is_none() {
            self.open_partition(&key, timestamp)?;
        }
        let bytes = frame_bytes(&record.metadata, &record.raw)?;
        let partition = self.partition.as_mut().unwrap();
        partition.writer.write_all(&bytes)?;
        partition.coverage.observe(&record.metadata);
        if let Some(slot) = record.metadata.slot {
            partition.start_slot = Some(
                partition
                    .start_slot
                    .map_or(slot, |current| current.min(slot)),
            );
            partition.end_slot = Some(partition.end_slot.map_or(slot, |current| current.max(slot)));
        }
        self.dedupe_writer.write_all(&id)?;
        self.remember_id(id);
        self.compact_dedupe_if_needed()?;
        Ok(true)
    }

    /// Flush and atomically hand the buffered evidence to the kernel. This is
    /// intentionally separate from `append_buffered` for high-rate sources.
    pub fn sync_buffered(&mut self) -> Result<()> {
        if let Some(partition) = self.partition.as_mut() {
            partition.writer.flush()?;
            partition.writer.get_ref().sync_data()?;
        }
        self.dedupe_writer.flush()?;
        self.dedupe_writer.get_ref().sync_data()?;
        Ok(())
    }

    fn detach_current(&mut self) -> Result<Option<DetachedPartition>> {
        let Some(mut partition) = self.partition.take() else { return Ok(None); };
        partition.writer.flush()?;
        partition.writer.get_ref().sync_all()?;
        drop(partition.writer);
        let sealed_path = partition.path.with_file_name("evidence.frames.sealed");
        if sealed_path.exists() { bail!("sealed partition path already exists: {}", sealed_path.display()); }
        fs::rename(&partition.path, &sealed_path)?;
        let seal_path = sealed_path.with_file_name("temporal-seal.json");
        let seal_tmp = seal_path.with_extension("tmp");
        fs::write(&seal_tmp, serde_json::to_vec_pretty(&serde_json::json!({
            "partitionKey": partition.key,
            "openedAtUtc": partition.opened_at.to_rfc3339(),
            "sealedAtUtc": Utc::now().to_rfc3339(),
            "temporalHourClosed": true,
            "compressionState": "PENDING"
        }))?)?;
        fs::rename(seal_tmp, seal_path)?;
        Ok(Some(DetachedPartition {
            key: partition.key,
            path: sealed_path,
            opened_at: partition.opened_at,
            start_slot: partition.start_slot,
            end_slot: partition.end_slot,
            coverage: partition.coverage,
        }))
    }

    fn finish_detached_partition(partition: DetachedPartition, temporal_hour_closed: bool) -> Result<PartitionManifest> {
        let raw_sha256 = file_sha256(&partition.path)?;
        let raw_frame_bytes = partition.path.metadata()?.len();
        let compressed_tmp = partition.path.with_file_name("evidence.frames.zst.tmp");
        let compressed_final = partition.path.with_file_name("evidence.frames.zst");
        let mut input = BufReader::new(File::open(&partition.path)?);
        let output = BufWriter::new(File::create(&compressed_tmp)?);
        let mut encoder = zstd::Encoder::new(output, 9)?;
        std::io::copy(&mut input, &mut encoder)?;
        let mut output = encoder.finish()?;
        output.flush()?;
        output.get_ref().sync_all()?;

        let mut decoder = zstd::Decoder::new(File::open(&compressed_tmp)?)?;
        let mut decoded_hasher = Sha256::new();
        let mut buffer = vec![0u8; 1024 * 1024];
        let mut decoded_bytes = 0u64;
        loop {
            let read = decoder.read(&mut buffer)?;
            if read == 0 {
                break;
            }
            decoded_hasher.update(&buffer[..read]);
            decoded_bytes += read as u64;
        }
        let decoded_sha256: String = decoded_hasher
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        if decoded_bytes != raw_frame_bytes || decoded_sha256 != raw_sha256 {
            bail!(
                "compressed partition verification mismatch for {}",
                partition.key
            );
        }
        fs::rename(&compressed_tmp, &compressed_final)?;
        let compressed_sha256 = file_sha256(&compressed_final)?;
        let compressed_bytes = compressed_final.metadata()?.len();
        let manifest = PartitionManifest {
            schema_version: SCHEMA_VERSION.to_string(),
            schema_sha256: sha256_hex(SCHEMA_DOCUMENT.as_bytes()),
            partition_key: partition.key,
            // Raw receive-hour closure is not proof that delayed finality records
            // in the following partition have reconciled every observation.
            complete_utc_hour: false,
            temporal_hour_closed,
            exact_replay_complete: false,
            reconciliation_manifest: Some("reconciliation-manifest.json".to_string()),
            opened_at_utc: partition.opened_at.to_rfc3339(),
            closed_at_utc: Utc::now().to_rfc3339(),
            start_slot: partition.start_slot,
            end_slot: partition.end_slot,
            raw_frame_bytes,
            compressed_bytes,
            raw_sha256,
            compressed_sha256,
            coverage: partition.coverage,
        };
        let manifest_path = partition.path.with_file_name("manifest.json");
        let manifest_tmp = partition.path.with_file_name("manifest.json.tmp");
        fs::write(&manifest_tmp, serde_json::to_vec_pretty(&manifest)?)?;
        fs::rename(manifest_tmp, manifest_path)?;
        fs::remove_file(&partition.path)?;
        let seal_path = partition.path.with_file_name("temporal-seal.json");
        if seal_path.exists() { fs::remove_file(seal_path)?; }
        Ok(manifest)
    }

    /// Detach an hour atomically and compress it in a background thread. The
    /// source may open the next hour immediately; a late record into the seal
    /// remains a hard failure and is never redirected or reopened.
    fn seal_current_async(&mut self) -> Result<()> {
        let Some(partition) = self.detach_current()? else { return Ok(()); };
        std::thread::Builder::new().name(format!("ledger-close-{}", partition.key.replace('/', "-"))).spawn(move || {
            if let Err(error) = Self::finish_detached_partition(partition, true) {
                eprintln!("TEMPORAL_PARTITION_ASYNC_CLOSE_FAILED:{error:#}");
            }
        })?;
        Ok(())
    }

    pub fn finalize_current(
        &mut self,
        temporal_hour_closed: bool,
    ) -> Result<Option<PartitionManifest>> {
        let Some(partition) = self.detach_current()? else { return Ok(None); };
        Ok(Some(Self::finish_detached_partition(partition, temporal_hour_closed)?))
    }

    pub fn current_coverage(&self) -> CoverageCounters {
        self.partition
            .as_ref()
            .map(|p| p.coverage.clone())
            .unwrap_or_default()
    }

    pub fn dedupe_entries(&self) -> usize {
        self.seen.len()
    }

    pub fn dedupe_path(&self) -> &Path {
        &self.dedupe_path
    }

    fn remember_id(&mut self, id: [u8; 32]) -> bool {
        if !self.seen.insert(id) {
            return false;
        }
        self.seen_order.push_back(id);
        while self.seen_order.len() > DEDUPE_WINDOW_IDS {
            if let Some(expired) = self.seen_order.pop_front() {
                self.seen.remove(&expired);
            }
        }
        true
    }

    fn compact_dedupe_if_needed(&mut self) -> Result<()> {
        let size = self.dedupe_path.metadata()?.len() as usize;
        if size <= DEDUPE_WINDOW_IDS * 64 {
            return Ok(());
        }
        self.dedupe_writer.flush()?;
        let tmp = self.dedupe_path.with_extension("bin.tmp");
        {
            let mut writer = BufWriter::new(File::create(&tmp)?);
            for id in &self.seen_order {
                writer.write_all(id)?;
            }
            writer.flush()?;
            writer.get_ref().sync_all()?;
        }
        fs::rename(tmp, &self.dedupe_path)?;
        self.dedupe_writer = BufWriter::new(
            OpenOptions::new()
                .create(true)
                .append(true)
                .open(&self.dedupe_path)?,
        );
        Ok(())
    }
}

fn decode_id(value: &str) -> Result<[u8; 32]> {
    if value.len() != 64 {
        bail!("invalid evidence id length");
    }
    let mut output = [0u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?;
    }
    Ok(output)
}

fn hex_nibble(value: u8) -> Result<u8> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        b'A'..=b'F' => Ok(value - b'A' + 10),
        _ => bail!("invalid evidence id hex"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;
    use tempfile::tempdir;

    fn record(id_seed: &str, slot: u64, successful: bool) -> FrameRecord {
        let raw = format!("raw-{id_seed}").into_bytes();
        let raw_sha256 = sha256_hex(&raw);
        let evidence_id = sha256_hex(format!("{id_seed}|{raw_sha256}").as_bytes());
        FrameRecord {
            metadata: EvidenceMetadata {
                schema_version: SCHEMA_VERSION.to_string(),
                evidence_id,
                raw_sha256,
                kind: "transaction".to_string(),
                raw_format: "yellowstone_subscribe_update_protobuf".to_string(),
                received_at_utc: "2026-09-06T00:00:00Z".to_string(),
                received_at_unix_ns: 1,
                decode_at_unix_ns: 2,
                writer_sequence: slot,
                slot: Some(slot),
                transaction_index: Some(0),
                signature: Some(id_seed.to_string()),
                account_pubkey: None,
                account_owner: None,
                write_version: None,
                finalized: true,
                successful: Some(successful),
                failed: Some(!successful),
                unresolved: false,
                pump_instruction_kinds: vec!["BUY".to_string()],
                pump_instruction_coordinates: vec!["outer:0".to_string()],
                discovered_mints: vec![],
                has_trade_event: true,
                has_fee_evidence: true,
                missing_raw_tx: false,
                missing_trade_event: false,
                missing_reserve_transition: false,
                missing_canonical_ordering: false,
                missing_fee_evidence: false,
                decode_failed: false,
                filters: vec!["pumpTransactions".to_string()],
            },
            raw,
        }
    }

    #[test]
    fn truncated_tail_is_recovered() -> Result<()> {
        let dir = tempdir()?;
        let path = dir.path().join("frames.partial");
        fs::write(&path, FRAME_MAGIC)?;
        let first = record("one", 1, true);
        OpenOptions::new()
            .append(true)
            .open(&path)?
            .write_all(&frame_bytes(&first.metadata, &first.raw)?)?;
        OpenOptions::new()
            .append(true)
            .open(&path)?
            .write_all(&[9, 9, 9])?;
        let records = scan_and_recover_frames(&path, true)?;
        assert_eq!(records.len(), 1);
        assert_eq!(scan_and_recover_frames(&path, false)?.len(), 1);
        Ok(())
    }

    #[test]
    fn restart_deduplicates_and_recovers_ids() -> Result<()> {
        let dir = tempdir()?;
        let at = Utc.with_ymd_and_hms(2026, 9, 6, 1, 0, 0).unwrap();
        let item = record("same", 10, true);
        {
            let mut writer = LedgerWriter::open(dir.path())?;
            assert!(writer.append(at, item.clone())?);
        }
        let mut writer = LedgerWriter::open(dir.path())?;
        assert!(!writer.append(at, item)?);
        assert_eq!(writer.dedupe_entries(), 1);
        Ok(())
    }

    #[test]
    fn restart_reopens_incomplete_same_hour_without_overwrite() -> Result<()> {
        let dir = tempdir()?;
        let at = Utc.with_ymd_and_hms(2026, 9, 6, 1, 0, 0).unwrap();
        {
            let mut writer = LedgerWriter::open(dir.path())?;
            writer.append(at, record("one", 1, true))?;
            writer.finalize_current(false)?;
        }
        {
            let mut writer = LedgerWriter::open(dir.path())?;
            writer.append(at, record("two", 2, true))?;
            writer.finalize_current(false)?;
        }
        let partition = dir.path().join("partitions/2026/09/06/01");
        let manifest: PartitionManifest =
            serde_json::from_slice(&fs::read(partition.join("manifest.json"))?)?;
        assert_eq!(manifest.coverage.records, 2);
        assert_eq!(manifest.coverage.successful, 2);
        Ok(())
    }

    #[test]
    fn rollover_compresses_and_verifies() -> Result<()> {
        let dir = tempdir()?;
        let first_hour = Utc.with_ymd_and_hms(2026, 9, 6, 1, 59, 59).unwrap();
        let second_hour = Utc.with_ymd_and_hms(2026, 9, 6, 2, 0, 0).unwrap();
        let mut writer = LedgerWriter::open(dir.path())?;
        writer.append(first_hour, record("one", 1, true))?;
        writer.append(second_hour, record("two", 2, false))?;
        let old = dir.path().join("partitions/2026/09/06/01");
        for _ in 0..100 {
            if old.join("evidence.frames.zst").exists() { break; }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        assert!(old.join("evidence.frames.zst").exists());
        let manifest: PartitionManifest =
            serde_json::from_slice(&fs::read(old.join("manifest.json"))?)?;
        assert!(manifest.temporal_hour_closed);
        assert!(!manifest.complete_utc_hour);
        assert!(!manifest.exact_replay_complete);
        assert_eq!(manifest.coverage.successful, 1);
        Ok(())
    }

    #[test]
    fn async_rollover_refuses_a_late_record_for_the_sealed_hour() -> Result<()> {
        let dir = tempdir()?;
        let first_hour = Utc.with_ymd_and_hms(2026, 9, 6, 1, 59, 59).unwrap();
        let second_hour = Utc.with_ymd_and_hms(2026, 9, 6, 2, 0, 0).unwrap();
        let mut writer = LedgerWriter::open(dir.path())?;
        writer.append_buffered(first_hour, record("first", 1, true))?;
        writer.append_buffered(second_hour, record("second", 2, true))?;
        let late = writer.append_buffered(first_hour, record("late", 3, true));
        assert!(late.is_err());
        assert!(dir.path().join("partitions/2026/09/06/01/temporal-seal.json").exists()
            || dir.path().join("partitions/2026/09/06/01/manifest.json").exists());
        Ok(())
    }

    #[test]
    fn twenty_forced_hourly_transitions_detach_before_background_compression() -> Result<()> {
        let mut max_rollover = std::time::Duration::ZERO;
        for hour in 0..20u32 {
            let dir = tempdir()?;
            let first = Utc.with_ymd_and_hms(2026, 9, 7, hour, 59, 59).unwrap();
            let second = first + chrono::Duration::seconds(1);
            let mut writer = LedgerWriter::open(dir.path())?;
            // Burst before the transition exercises the same detachment path
            // without allowing compression to run on the append call.
            for index in 0..256u64 {
                writer.append_buffered(first, record(&format!("pre-{hour}-{index}"), index, true))?;
            }
            let started = std::time::Instant::now();
            writer.append_buffered(second, record(&format!("post-{hour}"), 10_000 + hour as u64, true))?;
            max_rollover = max_rollover.max(started.elapsed());
            let old = dir.path().join(format!("partitions/2026/09/07/{hour:02}"));
            assert!(old.join("temporal-seal.json").exists() || old.join("manifest.json").exists());
            assert!(dir.path().join(format!("partitions/2026/09/07/{:02}/evidence.frames.partial", hour + 1)).exists());
        }
        // The test data is intentionally small; this guards against restoring
        // synchronous checksum/compression work to the canonical append path.
        assert!(max_rollover < std::time::Duration::from_millis(250));
        Ok(())
    }

    #[test]
    fn failed_transactions_are_counted_but_not_successful() {
        let mut coverage = CoverageCounters::default();
        coverage.observe(&record("failed", 3, false).metadata);
        assert_eq!(coverage.failed, 1);
        assert_eq!(coverage.successful, 0);
    }

    #[test]
    fn provisional_transactions_do_not_claim_finalized_success() {
        let mut metadata = record("provisional", 4, true).metadata;
        metadata.finalized = false;
        let mut coverage = CoverageCounters::default();
        coverage.observe(&metadata);
        assert_eq!(coverage.provisional_transactions, 1);
        assert_eq!(coverage.successful, 0);
        assert_eq!(coverage.failed, 0);
        assert_eq!(coverage.missing_finality, 0);
        assert_eq!(coverage.provisional_unresolved, 0);
    }

    #[test]
    fn frame_checksum_detects_corruption() -> Result<()> {
        let dir = tempdir()?;
        let path = dir.path().join("frames.partial");
        let item = record("corrupt", 4, true);
        let mut bytes = FRAME_MAGIC.to_vec();
        bytes.extend(frame_bytes(&item.metadata, &item.raw)?);
        let middle = bytes.len() / 2;
        bytes[middle] ^= 0xff;
        fs::write(&path, bytes)?;
        assert!(scan_and_recover_frames(&path, false).is_err());
        Ok(())
    }
}
