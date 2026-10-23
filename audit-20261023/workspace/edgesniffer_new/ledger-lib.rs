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

// The takeover snapshot uses this file for the narrow source-replay API. The
// larger ledger submodules are supplied by the authoritative full checkout.
#[cfg(any())] pub mod block_order_verifier_v1;
#[cfg(any())] pub mod canonical_capture_run_v1;
#[cfg(any())] pub mod canonical_state_v1;
#[cfg(any())] pub mod filtered_identity_manifest_v1;
#[cfg(any())] pub mod finalized_slot_inventory_v1;
#[cfg(any())] pub mod forward_capture_session_v1;
#[cfg(any())] pub mod forward_cohort_receipts_v1;
#[cfg(any())] pub mod forward_new_pool_cohort_v1;
#[cfg(any())] pub mod pool_universe_bootstrap_v1;
#[cfg(any())] pub mod pump_instruction_path_decoder_v1;
#[cfg(any())] pub mod reliability;
#[cfg(any())] pub mod source_block_closure_v1;

pub const SCHEMA_VERSION: &str = "PUMP_EXACT_EVIDENCE_LEDGER_V1";
pub const FRAME_MAGIC: &[u8; 8] = b"PEL1FRM\0";
pub const FRAME_PREFIX_BYTES: usize = 8;
pub const FRAME_DIGEST_BYTES: usize = 32;
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
    /// Writer epoch is a local-provenance boundary.  A writer sequence is only
    /// total within an epoch, so scientific consumers must retain both.
    #[serde(default)]
    pub continuity_epoch: Option<u64>,
    /// Unique source-process identity.  Together with continuity epoch and
    /// writer sequence this is a durable total-order namespace across a
    /// collector restart; consumers must not assume an epoch counter survives
    /// a process replacement.
    #[serde(default)]
    pub source_instance_id: Option<String>,
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

/// Validate a ledger file a frame at a time without retaining its raw payloads.
///
/// Restart recovery must be able to inspect a multi-gigabyte open partition
/// without converting it into a multi-gigabyte in-memory vector.  Callers that
/// only need metadata (dedupe and coverage reconstruction) use this path.
fn scan_and_visit_frames<F>(path: &Path, truncate_tail: bool, mut visit: F) -> Result<()>
where
    F: FnMut(EvidenceMetadata, &[u8]) -> Result<()>,
{
    if !path.exists() {
        return Ok(());
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
            return Ok(());
        }
        bail!("truncated frame header: {}", path.display());
    }
    file.read_exact(&mut magic)?;
    if &magic != FRAME_MAGIC {
        bail!("invalid frame magic: {}", path.display());
    }

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
        visit(metadata, &body[metadata_len..])?;
        valid_end = file.stream_position()?;
    }
    Ok(())
}

pub fn scan_and_recover_frames(path: &Path, truncate_tail: bool) -> Result<Vec<FrameRecord>> {
    let mut records = Vec::new();
    scan_and_visit_frames(path, truncate_tail, |metadata, raw| {
        records.push(FrameRecord {
            metadata,
            raw: raw.to_vec(),
        });
        Ok(())
    })?;
    Ok(records)
}

/// Read a sealed (`.zst`) or open (`.partial`) partition without mutating it.
/// Consumers use this for restart recovery; every frame digest is verified
/// before it is exposed to a scientific state builder.
pub fn scan_partition_read_only(path: &Path) -> Result<Vec<FrameRecord>> {
    let bytes = if path.extension().is_some_and(|extension| extension == "zst") {
        zstd::stream::decode_all(BufReader::new(File::open(path)?))?
    } else {
        fs::read(path)?
    };
    if bytes.len() < FRAME_MAGIC.len() || &bytes[..FRAME_MAGIC.len()] != FRAME_MAGIC {
        bail!("invalid frame magic: {}", path.display());
    }
    let mut offset = FRAME_MAGIC.len();
    let mut output = Vec::new();
    while offset < bytes.len() {
        let frame_start = offset;
        if offset + FRAME_PREFIX_BYTES > bytes.len() {
            bail!("truncated frame prefix at {frame_start}");
        }
        let metadata_len =
            u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap()) as usize;
        let raw_len =
            u32::from_le_bytes(bytes[offset + 4..offset + 8].try_into().unwrap()) as usize;
        offset += FRAME_PREFIX_BYTES;
        let body_len = metadata_len
            .checked_add(raw_len)
            .context("frame body length overflow")?;
        let end = offset
            .checked_add(body_len + FRAME_DIGEST_BYTES)
            .context("frame length overflow")?;
        if body_len > 128 * 1024 * 1024 || end > bytes.len() {
            bail!("truncated or oversized frame at {frame_start}");
        }
        let body = &bytes[offset..offset + body_len];
        let digest = &bytes[offset + body_len..end];
        if Sha256::digest(body).as_slice() != digest {
            bail!("frame checksum mismatch at {frame_start}");
        }
        let metadata: EvidenceMetadata = serde_json::from_slice(&body[..metadata_len])?;
        output.push(FrameRecord {
            metadata,
            raw: body[metadata_len..].to_vec(),
        });
        offset = end;
    }
    Ok(output)
}

/// Return the exact ordered source records for one immutable source epoch after
/// a durable consumer checkpoint. Any missing writer sequence is a hard error.
pub fn scan_source_epoch_after(
    root: &Path,
    source_instance_id: &str,
    continuity_epoch: u64,
    after_writer_sequence: u64,
) -> Result<Vec<FrameRecord>> {
    let partitions = root.join("partitions");
    let mut paths = Vec::new();
    let mut stack = vec![partitions];
    while let Some(path) = stack.pop() {
        if !path.exists() {
            continue;
        }
        for entry in fs::read_dir(path)? {
            let entry = entry?;
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            let file = path
                .file_name()
                .and_then(|value| value.to_str())
                .unwrap_or_default();
            if file == "evidence.frames.partial" || file == "evidence.frames.zst" {
                paths.push(path);
            }
        }
    }
    paths.sort();
    let mut records = paths
        .into_iter()
        .map(|path| scan_partition_read_only(&path))
        .collect::<Result<Vec<_>>>()?
        .into_iter()
        .flatten()
        .filter(|record| {
            record.metadata.source_instance_id.as_deref() == Some(source_instance_id)
                && record.metadata.continuity_epoch == Some(continuity_epoch)
                && record.metadata.writer_sequence > after_writer_sequence
        })
        .collect::<Vec<_>>();
    records.sort_by_key(|record| record.metadata.writer_sequence);
    let mut expected = after_writer_sequence.saturating_add(1);
    for record in &records {
        if record.metadata.writer_sequence != expected {
            bail!(
                "canonical source sequence gap: expected {expected}, observed {}",
                record.metadata.writer_sequence
            );
        }
        expected = expected.saturating_add(1);
    }
    Ok(records)
}

/// Bounded variant for live consumers. It streams one frame at a time and
/// stops after `limit`, rather than decompressing an entire capture hour into
/// RAM. Sequence continuity is checked for every returned record; the next
/// invocation resumes from the returned tail.
pub fn scan_source_epoch_after_limited(
    root: &Path,
    source_instance_id: &str,
    continuity_epoch: u64,
    after_writer_sequence: u64,
    limit: usize,
) -> Result<Vec<FrameRecord>> {
    if limit == 0 {
        bail!("bounded source scan limit must be positive")
    }
    let mut paths = Vec::new();
    let mut stack = vec![root.join("partitions")];
    while let Some(path) = stack.pop() {
        if !path.exists() { continue; }
        for entry in fs::read_dir(path)? {
            let path = entry?.path();
            if path.is_dir() { stack.push(path); continue; }
            let name = path.file_name().and_then(|value| value.to_str()).unwrap_or_default();
            if name == "evidence.frames.partial" || name == "evidence.frames.zst" { paths.push(path); }
        }
    }
    paths.sort();
    let mut expected = after_writer_sequence.saturating_add(1);
    let mut output = Vec::with_capacity(limit);
    for path in paths {
        let file = File::open(&path)?;
        let mut reader: Box<dyn Read> = if path.extension().is_some_and(|extension| extension == "zst") {
            Box::new(zstd::stream::read::Decoder::new(BufReader::new(file))?)
        } else {
            Box::new(BufReader::new(file))
        };
        let mut magic = [0_u8; 8];
        reader.read_exact(&mut magic)?;
        if &magic != FRAME_MAGIC { bail!("invalid frame magic: {}", path.display()) }
        loop {
            let mut prefix = [0_u8; FRAME_PREFIX_BYTES];
            match reader.read_exact(&mut prefix) {
                Ok(()) => {},
                Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof => break,
                Err(error) => return Err(error.into()),
            }
            let metadata_len = u32::from_le_bytes(prefix[..4].try_into().unwrap()) as usize;
            let raw_len = u32::from_le_bytes(prefix[4..].try_into().unwrap()) as usize;
            let body_len = metadata_len.checked_add(raw_len).context("frame body length overflow")?;
            if body_len > 128 * 1024 * 1024 { bail!("oversized source frame") }
            let mut body = vec![0_u8; body_len];
            let mut digest = [0_u8; FRAME_DIGEST_BYTES];
            reader.read_exact(&mut body)?;
            reader.read_exact(&mut digest)?;
            if Sha256::digest(&body).as_slice() != digest { bail!("source frame checksum mismatch") }
            let metadata: EvidenceMetadata = serde_json::from_slice(&body[..metadata_len])?;
            if metadata.source_instance_id.as_deref() != Some(source_instance_id)
                || metadata.continuity_epoch != Some(continuity_epoch)
                || metadata.writer_sequence <= after_writer_sequence { continue; }
            if metadata.writer_sequence != expected { bail!("canonical source sequence gap: expected {expected}, observed {}", metadata.writer_sequence) }
            expected = expected.saturating_add(1);
            output.push(FrameRecord { metadata, raw: body[metadata_len..].to_vec() });
            if output.len() == limit { return Ok(output) }
        }
    }
    Ok(output)
}

/// A durable position in a derived, uncompressed replay cache.  The source
/// journal remains the evidence of record; this cursor only avoids repeatedly
/// decompressing immutable earlier frames while an offline consumer catches up.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SourceReplayCursor {
    pub schema: String,
    pub source_instance_id: String,
    pub continuity_epoch: u64,
    pub partition_relative_path: Option<String>,
    pub source_partition_sha256: Option<String>,
    pub spool_relative_path: Option<String>,
    pub spool_sha256: Option<String>,
    pub next_byte_offset: u64,
    pub next_writer_sequence: u64,
}

impl SourceReplayCursor {
    pub fn at_sequence(source_instance_id: &str, continuity_epoch: u64, sequence: u64) -> Self {
        Self {
            schema: "KEFI_SOURCE_REPLAY_CURSOR_V1".to_owned(),
            source_instance_id: source_instance_id.to_owned(),
            continuity_epoch,
            partition_relative_path: None,
            source_partition_sha256: None,
            spool_relative_path: None,
            spool_sha256: None,
            next_byte_offset: FRAME_MAGIC.len() as u64,
            next_writer_sequence: sequence,
        }
    }
}

#[derive(Debug, Clone)]
pub struct SourceReplayBatch {
    pub records: Vec<FrameRecord>,
    pub cursor: SourceReplayCursor,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SourceReplaySpoolManifest {
    schema: String,
    source_partition_relative_path: String,
    source_partition_sha256: String,
    source_partition_bytes: u64,
    spool_relative_path: String,
    spool_sha256: String,
    spool_bytes: u64,
    frame_count: u64,
}

fn source_partition_paths(root: &Path) -> Result<Vec<PathBuf>> {
    let mut paths = Vec::new();
    let mut stack = vec![root.join("partitions")];
    while let Some(path) = stack.pop() {
        if !path.exists() {
            continue;
        }
        for entry in fs::read_dir(path)? {
            let path = entry?.path();
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            let name = path.file_name().and_then(|value| value.to_str()).unwrap_or_default();
            if name == "evidence.frames.partial" || name == "evidence.frames.zst" {
                paths.push(path);
            }
        }
    }
    paths.sort();
    Ok(paths)
}

fn relative_source_partition(root: &Path, source: &Path) -> Result<String> {
    Ok(source
        .strip_prefix(root)
        .context("source partition escaped canonical root")?
        .to_string_lossy()
        .replace('\\', "/"))
}

fn atomic_json_file(path: &Path, value: &impl Serialize) -> Result<()> {
    let parent = path.parent().context("atomic JSON path has no parent")?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_extension("tmp");
    let mut file = File::create(&temporary)?;
    file.write_all(&serde_json::to_vec(value)?)?;
    file.sync_all()?;
    fs::rename(temporary, path)?;
    Ok(())
}

fn read_wire_frame<R: Read>(reader: &mut R) -> Result<Option<(FrameRecord, Vec<u8>)>> {
    let mut prefix = [0_u8; FRAME_PREFIX_BYTES];
    match reader.read_exact(&mut prefix) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(error) => return Err(error.into()),
    }
    let metadata_len = u32::from_le_bytes(prefix[..4].try_into().unwrap()) as usize;
    let raw_len = u32::from_le_bytes(prefix[4..].try_into().unwrap()) as usize;
    let body_len = metadata_len.checked_add(raw_len).context("frame body length overflow")?;
    if body_len > 128 * 1024 * 1024 {
        bail!("oversized source frame")
    }
    let mut body = vec![0_u8; body_len];
    let mut digest = [0_u8; FRAME_DIGEST_BYTES];
    reader.read_exact(&mut body)?;
    reader.read_exact(&mut digest)?;
    if Sha256::digest(&body).as_slice() != digest {
        bail!("source frame checksum mismatch")
    }
    let metadata: EvidenceMetadata = serde_json::from_slice(&body[..metadata_len])?;
    let mut wire = Vec::with_capacity(FRAME_PREFIX_BYTES + body_len + FRAME_DIGEST_BYTES);
    wire.extend_from_slice(&prefix);
    wire.extend_from_slice(&body);
    wire.extend_from_slice(&digest);
    Ok(Some((FrameRecord { metadata, raw: body[metadata_len..].to_vec() }, wire)))
}

fn ensure_source_replay_spool(
    root: &Path,
    spool_root: &Path,
    source: &Path,
) -> Result<SourceReplaySpoolManifest> {
    let source_relative = relative_source_partition(root, source)?;
    let source_bytes = source.metadata()?.len();
    let partitions_root = spool_root.join("partitions");
    if partitions_root.exists() {
        for entry in fs::read_dir(&partitions_root)? {
            let manifest_path = entry?.path().join("manifest.json");
            if !manifest_path.exists() {
                continue;
            }
            let manifest: SourceReplaySpoolManifest = serde_json::from_slice(&fs::read(&manifest_path)?)?;
            if manifest.source_partition_relative_path != source_relative {
                continue;
            }
            if manifest.schema != "KEFI_SOURCE_REPLAY_SPOOL_V1"
                || manifest.source_partition_bytes != source_bytes
                || !spool_root.join(&manifest.spool_relative_path).is_file()
            {
                bail!("SOURCE_REPLAY_SPOOL_BINDING_MISMATCH")
            }
            // The source partition was hash-verified while this immutable
            // cache was built.  Rehashing a multi-GiB sealed source on every
            // 65k-frame batch would recreate the very replay bottleneck this
            // cursor removes; size/binding drift fails closed instead.
            return Ok(manifest);
        }
    }
    let source_sha256 = file_sha256(source)?;
    let spool_dir = spool_root.join("partitions").join(&source_sha256);
    let manifest_path = spool_dir.join("manifest.json");

    fs::create_dir_all(&spool_dir)?;
    let spool_relative = format!("partitions/{source_sha256}/frames.raw");
    let spool_path = spool_root.join(&spool_relative);
    let temporary = spool_path.with_extension("raw.tmp");
    let source_file = File::open(source)?;
    let mut reader: Box<dyn Read> = if source.extension().is_some_and(|extension| extension == "zst") {
        Box::new(zstd::stream::read::Decoder::new(BufReader::new(source_file))?)
    } else {
        Box::new(BufReader::new(source_file))
    };
    let mut magic = [0_u8; 8];
    reader.read_exact(&mut magic)?;
    if &magic != FRAME_MAGIC {
        bail!("invalid frame magic: {}", source.display())
    }
    let mut output = BufWriter::with_capacity(4 * 1024 * 1024, File::create(&temporary)?);
    let mut hasher = Sha256::new();
    output.write_all(FRAME_MAGIC)?;
    hasher.update(FRAME_MAGIC);
    let mut frame_count = 0_u64;
    while let Some((_record, wire)) = read_wire_frame(&mut reader)? {
        output.write_all(&wire)?;
        hasher.update(&wire);
        frame_count = frame_count.checked_add(1).context("replay spool frame count overflow")?;
    }
    output.flush()?;
    output.get_ref().sync_all()?;
    let spool_bytes = output.get_ref().metadata()?.len();
    drop(output);
    fs::rename(&temporary, &spool_path)?;
    let manifest = SourceReplaySpoolManifest {
        schema: "KEFI_SOURCE_REPLAY_SPOOL_V1".to_owned(),
        source_partition_relative_path: source_relative,
        source_partition_sha256: source_sha256,
        source_partition_bytes: source_bytes,
        spool_relative_path: spool_relative,
        spool_sha256: hasher.finalize().iter().map(|byte| format!("{byte:02x}")).collect(),
        spool_bytes,
        frame_count,
    };
    atomic_json_file(&manifest_path, &manifest)?;
    Ok(manifest)
}

/// Read a bounded, exactly ordered batch through a durable partition-aware
/// cursor.  The first visit to a sealed partition builds a hash-bound
/// uncompressed replay cache; later calls seek directly to the next frame.
/// The cache is derived data and must never be mistaken for raw evidence.
pub fn scan_source_epoch_after_cursor(
    root: &Path,
    spool_root: &Path,
    source_instance_id: &str,
    continuity_epoch: u64,
    cursor: SourceReplayCursor,
    limit: usize,
) -> Result<SourceReplayBatch> {
    if limit == 0 {
        bail!("bounded source scan limit must be positive")
    }
    if cursor.schema != "KEFI_SOURCE_REPLAY_CURSOR_V1"
        || cursor.source_instance_id != source_instance_id
        || cursor.continuity_epoch != continuity_epoch
    {
        bail!("SOURCE_REPLAY_CURSOR_BINDING_MISMATCH")
    }
    let paths = source_partition_paths(root)?;
    let mut start = 0_usize;
    let mut offset = FRAME_MAGIC.len() as u64;
    if let Some(relative) = cursor.partition_relative_path.as_deref() {
        start = paths
            .iter()
            .position(|path| {
                relative_source_partition(root, path)
                    .map(|candidate| candidate == relative)
                    .unwrap_or(false)
            })
            .context("SOURCE_REPLAY_CURSOR_PARTITION_MISSING")?;
        offset = cursor.next_byte_offset;
    }
    let mut expected = cursor.next_writer_sequence.saturating_add(1);
    let mut output = Vec::with_capacity(limit);
    let mut next = cursor;
    for source in paths.into_iter().skip(start) {
        let source_relative = relative_source_partition(root, &source)?;
        let spool = ensure_source_replay_spool(root, spool_root, &source)?;
        let spool_path = spool_root.join(&spool.spool_relative_path);
        let mut file = File::open(&spool_path)?;
        let position = if next.partition_relative_path.as_deref() == Some(source_relative.as_str()) {
            offset
        } else {
            FRAME_MAGIC.len() as u64
        };
        if position < FRAME_MAGIC.len() as u64 || position > spool.spool_bytes {
            bail!("SOURCE_REPLAY_CURSOR_OFFSET_INVALID")
        }
        if position == FRAME_MAGIC.len() as u64 {
            let mut magic = [0_u8; 8];
            file.seek(SeekFrom::Start(0))?;
            file.read_exact(&mut magic)?;
            if &magic != FRAME_MAGIC {
                bail!("SOURCE_REPLAY_SPOOL_MAGIC_INVALID")
            }
        } else {
            file.seek(SeekFrom::Start(position))?;
        }
        loop {
            let Some((record, _wire)) = read_wire_frame(&mut file)? else { break; };
            let next_offset = file.stream_position()?;
            if record.metadata.source_instance_id.as_deref() != Some(source_instance_id)
                || record.metadata.continuity_epoch != Some(continuity_epoch)
                || record.metadata.writer_sequence <= next.next_writer_sequence
            {
                continue;
            }
            if record.metadata.writer_sequence != expected {
                bail!(
                    "canonical source sequence gap: expected {expected}, observed {}",
                    record.metadata.writer_sequence
                );
            }
            expected = expected.saturating_add(1);
            next = SourceReplayCursor {
                schema: "KEFI_SOURCE_REPLAY_CURSOR_V1".to_owned(),
                source_instance_id: source_instance_id.to_owned(),
                continuity_epoch,
                partition_relative_path: Some(source_relative.clone()),
                source_partition_sha256: Some(spool.source_partition_sha256.clone()),
                spool_relative_path: Some(spool.spool_relative_path.clone()),
                spool_sha256: Some(spool.spool_sha256.clone()),
                next_byte_offset: next_offset,
                next_writer_sequence: record.metadata.writer_sequence,
            };
            output.push(record);
            if output.len() == limit {
                return Ok(SourceReplayBatch { records: output, cursor: next });
            }
        }
        next.partition_relative_path = Some(source_relative);
        next.source_partition_sha256 = Some(spool.source_partition_sha256);
        next.spool_relative_path = Some(spool.spool_relative_path);
        next.spool_sha256 = Some(spool.spool_sha256);
        next.next_byte_offset = spool.spool_bytes;
        offset = FRAME_MAGIC.len() as u64;
    }
    Ok(SourceReplayBatch { records: output, cursor: next })
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

/// A durable recovery checkpoint for the one mutable receive-hour partition.
/// It is written only after the underlying frame file has been synced.  A
/// stale/torn checkpoint is harmless: the byte-length check rejects it and the
/// existing full scan remains the fail-closed fallback.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct OpenPartitionCheckpoint {
    schema: String,
    partition_key: String,
    raw_frame_bytes: u64,
    opened_at_utc: String,
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
    fn open_checkpoint_path(path: &Path) -> PathBuf {
        path.with_file_name("open-partition-checkpoint.json")
    }

    fn write_open_checkpoint(partition: &OpenPartition) -> Result<()> {
        let checkpoint_path = Self::open_checkpoint_path(&partition.path);
        let temporary = checkpoint_path.with_extension("json.tmp");
        let raw_frame_bytes = partition.writer.get_ref().metadata()?.len();
        let checkpoint = OpenPartitionCheckpoint {
            schema: "PUMP_EXACT_OPEN_PARTITION_CHECKPOINT_V1".to_string(),
            partition_key: partition.key.clone(),
            raw_frame_bytes,
            opened_at_utc: partition.opened_at.to_rfc3339(),
            start_slot: partition.start_slot,
            end_slot: partition.end_slot,
            coverage: partition.coverage.clone(),
        };
        let mut file = File::create(&temporary)?;
        file.write_all(&serde_json::to_vec(&checkpoint)?)?;
        file.sync_all()?;
        fs::rename(temporary, checkpoint_path)?;
        Ok(())
    }

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
                    scan_and_visit_frames(&entry.path(), true, |metadata, _raw| {
                        if let Ok(id) = decode_id(&metadata.evidence_id) {
                            if self.remember_id(id) {
                                self.dedupe_writer.write_all(&id)?;
                            }
                        }
                        Ok(())
                    })?;
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
        }
        let raw_frame_bytes = file.metadata()?.len();
        let checkpoint_path = Self::open_checkpoint_path(&path);
        let checkpoint = checkpoint_path
            .exists()
            .then(|| {
                fs::read(&checkpoint_path).ok().and_then(|bytes| {
                    serde_json::from_slice::<OpenPartitionCheckpoint>(&bytes).ok()
                })
            })
            .flatten()
            .filter(|checkpoint| checkpoint.schema == "PUMP_EXACT_OPEN_PARTITION_CHECKPOINT_V1")
            .filter(|checkpoint| checkpoint.partition_key == key)
            .filter(|checkpoint| checkpoint.raw_frame_bytes == raw_frame_bytes);
        let (opened_at, coverage, start_slot, end_slot) = if let Some(checkpoint) = checkpoint {
            let opened_at =
                DateTime::parse_from_rfc3339(&checkpoint.opened_at_utc)?.with_timezone(&Utc);
            (
                opened_at,
                checkpoint.coverage,
                checkpoint.start_slot,
                checkpoint.end_slot,
            )
        } else {
            let mut coverage = CoverageCounters::default();
            let mut start_slot = None;
            let mut end_slot = None;
            scan_and_visit_frames(&path, true, |metadata, _raw| {
                coverage.observe(&metadata);
                if let Some(slot) = metadata.slot {
                    start_slot = Some(start_slot.map_or(slot, |current: u64| current.min(slot)));
                    end_slot = Some(end_slot.map_or(slot, |current: u64| current.max(slot)));
                }
                Ok(())
            })?;
            (opened_at, coverage, start_slot, end_slot)
        };
        file.seek(SeekFrom::End(0))?;
        self.partition = Some(OpenPartition {
            key: key.to_string(),
            path,
            writer: BufWriter::with_capacity(4 * 1024 * 1024, file),
            opened_at,
            start_slot,
            end_slot,
            coverage,
        });
        if let Some(partition) = self.partition.as_ref() {
            Self::write_open_checkpoint(partition)?;
        }
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
    pub fn append_buffered(
        &mut self,
        timestamp: DateTime<Utc>,
        record: FrameRecord,
    ) -> Result<bool> {
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
        if let Some(partition) = self.partition.as_ref() {
            Self::write_open_checkpoint(partition)?;
        }
        Ok(())
    }

    fn detach_current(&mut self) -> Result<Option<DetachedPartition>> {
        let Some(mut partition) = self.partition.take() else {
            return Ok(None);
        };
        partition.writer.flush()?;
        partition.writer.get_ref().sync_all()?;
        drop(partition.writer);
        let checkpoint_path = Self::open_checkpoint_path(&partition.path);
        let sealed_path = partition.path.with_file_name("evidence.frames.sealed");
        if sealed_path.exists() {
            bail!(
                "sealed partition path already exists: {}",
                sealed_path.display()
            );
        }
        fs::rename(&partition.path, &sealed_path)?;
        if checkpoint_path.exists() {
            fs::remove_file(checkpoint_path)?;
        }
        let seal_path = sealed_path.with_file_name("temporal-seal.json");
        let seal_tmp = seal_path.with_extension("tmp");
        fs::write(
            &seal_tmp,
            serde_json::to_vec_pretty(&serde_json::json!({
                "partitionKey": partition.key,
                "openedAtUtc": partition.opened_at.to_rfc3339(),
                "sealedAtUtc": Utc::now().to_rfc3339(),
                "temporalHourClosed": true,
                "compressionState": "PENDING"
            }))?,
        )?;
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

    fn finish_detached_partition(
        partition: DetachedPartition,
        temporal_hour_closed: bool,
    ) -> Result<PartitionManifest> {
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
        if seal_path.exists() {
            fs::remove_file(seal_path)?;
        }
        Ok(manifest)
    }

    /// Detach an hour atomically and compress it in a background thread. The
    /// source may open the next hour immediately; a late record into the seal
    /// remains a hard failure and is never redirected or reopened.
    fn seal_current_async(&mut self) -> Result<()> {
        let Some(partition) = self.detach_current()? else {
            return Ok(());
        };
        std::thread::Builder::new()
            .name(format!("ledger-close-{}", partition.key.replace('/', "-")))
            .spawn(move || {
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
        let Some(partition) = self.detach_current()? else {
            return Ok(None);
        };
        Ok(Some(Self::finish_detached_partition(
            partition,
            temporal_hour_closed,
        )?))
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
                continuity_epoch: None,
                source_instance_id: None,
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
    fn continuity_epoch_round_trips_in_a_checksummed_frame() {
        let directory = tempdir().expect("tempdir");
        let mut writer = LedgerWriter::open(directory.path()).expect("writer");
        let mut frame = record("epoch", 99, true);
        frame.metadata.continuity_epoch = Some(42);
        frame.metadata.source_instance_id = Some("instance-test".to_string());
        writer
            .append(Utc.timestamp_nanos(1_000_000_000), frame)
            .expect("append");
        let partition = directory
            .path()
            .join("partitions/1970/01/01/00/evidence.frames.partial");
        let frames = scan_and_recover_frames(&partition, false).expect("read");
        assert_eq!(frames[0].metadata.continuity_epoch, Some(42));
        assert_eq!(
            frames[0].metadata.source_instance_id.as_deref(),
            Some("instance-test")
        );
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
            if old.join("evidence.frames.zst").exists() {
                break;
            }
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
        assert!(
            dir.path()
                .join("partitions/2026/09/06/01/temporal-seal.json")
                .exists()
                || dir
                    .path()
                    .join("partitions/2026/09/06/01/manifest.json")
                    .exists()
        );
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
                writer
                    .append_buffered(first, record(&format!("pre-{hour}-{index}"), index, true))?;
            }
            let started = std::time::Instant::now();
            writer.append_buffered(
                second,
                record(&format!("post-{hour}"), 10_000 + hour as u64, true),
            )?;
            max_rollover = max_rollover.max(started.elapsed());
            let old = dir.path().join(format!("partitions/2026/09/07/{hour:02}"));
            assert!(old.join("temporal-seal.json").exists() || old.join("manifest.json").exists());
            assert!(dir
                .path()
                .join(format!(
                    "partitions/2026/09/07/{:02}/evidence.frames.partial",
                    hour + 1
                ))
                .exists());
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

    #[test]
    fn canonical_epoch_scan_is_ordered_and_rejects_sequence_gaps() -> Result<()> {
        let dir = tempdir()?;
        let at = Utc.with_ymd_and_hms(2026, 10, 14, 20, 0, 0).unwrap();
        let mut writer = LedgerWriter::open(dir.path())?;
        let mut one = record("canonical-one", 1, true);
        one.metadata.source_instance_id = Some("instance-a".to_owned());
        one.metadata.continuity_epoch = Some(3);
        one.metadata.writer_sequence = 1;
        let mut two = record("canonical-two", 2, true);
        two.metadata.source_instance_id = Some("instance-a".to_owned());
        two.metadata.continuity_epoch = Some(3);
        two.metadata.writer_sequence = 2;
        writer.append(at, one)?;
        writer.append(at, two)?;
        let records = scan_source_epoch_after(dir.path(), "instance-a", 3, 0)?;
        assert_eq!(
            records
                .iter()
                .map(|record| record.metadata.writer_sequence)
                .collect::<Vec<_>>(),
            vec![1, 2]
        );
        let mut three = record("canonical-gap", 3, true);
        three.metadata.source_instance_id = Some("instance-a".to_owned());
        three.metadata.continuity_epoch = Some(3);
        three.metadata.writer_sequence = 4;
        writer.append(at, three)?;
        assert!(scan_source_epoch_after(dir.path(), "instance-a", 3, 0).is_err());
        Ok(())
    }
}
