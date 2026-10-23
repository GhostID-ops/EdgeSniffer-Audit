//! Restart-safe reader for the V8 canonical durable journal.
//!
//! This module deliberately has no Yellowstone client. A scientific consumer
//! either reads the V8 journal with this binding or refuses to start.

use anyhow::{bail, Context, Result};
use prost::Message;
use pump_exact_evidence_ledger::{
    canonical_state_v1::{hash_excluding_field, jcs_bytes},
    scan_source_epoch_after, scan_source_epoch_after_cursor, scan_source_epoch_after_limited,
    FrameRecord, SourceReplayCursor,
};
use serde::{Deserialize, Serialize};
use std::{fs, path::Path};
use yellowstone_grpc_proto::geyser::SubscribeUpdate;

#[derive(Clone, Debug)]
pub struct CanonicalInput {
    pub update: SubscribeUpdate,
    pub raw: Vec<u8>,
    pub received_at_unix_ns: u128,
    pub source_instance_id: String,
    pub continuity_epoch: u64,
    pub durable_output_sequence: u64,
    pub source_frame_sha256: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Checkpoint {
    pub schema: String,
    pub source_instance_id: String,
    pub continuity_epoch: String,
    pub durable_output_sequence: String,
    pub source_frame_sha256: String,
    pub checkpoint_sha256: String,
}

fn atomic_jcs_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let parent = path.parent().context("checkpoint has no parent")?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, jcs_bytes(&serde_json::to_value(value)?)?)?;
    fs::rename(temporary, path)?;
    Ok(())
}

pub fn load_checkpoint(
    path: &Path,
    source_instance_id: &str,
    continuity_epoch: u64,
) -> Result<u64> {
    if !path.exists() {
        return Ok(0);
    }
    let checkpoint: Checkpoint = serde_json::from_slice(&fs::read(path)?)?;
    if checkpoint.source_instance_id != source_instance_id
        || checkpoint.continuity_epoch.parse::<u64>()? != continuity_epoch
    {
        bail!("canonical consumer checkpoint belongs to a different source epoch")
    }
    if checkpoint.schema != "KEFI_CANONICAL_SOURCE_CONSUMER_CHECKPOINT_V2"
        || checkpoint.checkpoint_sha256 != hash_excluding_field(&checkpoint, "checkpointSha256")?
    {
        bail!("canonical consumer checkpoint integrity failure")
    }
    checkpoint
        .durable_output_sequence
        .parse()
        .context("invalid canonical checkpoint sequence")
}

pub fn read_next_batch(
    canonical_root: &Path,
    source_instance_id: &str,
    continuity_epoch: u64,
    after_sequence: u64,
) -> Result<Vec<CanonicalInput>> {
    let records = scan_source_epoch_after(
        canonical_root,
        source_instance_id,
        continuity_epoch,
        after_sequence,
    )?;
    let inputs = records
        .into_iter()
        .map(|record| decode_record(record, source_instance_id, continuity_epoch))
        .collect::<Result<Vec<_>>>()?;
    validate_contiguous_batch(&inputs, after_sequence)?;
    Ok(inputs)
}

pub fn read_next_batch_limited(
    canonical_root: &Path,
    source_instance_id: &str,
    continuity_epoch: u64,
    after_sequence: u64,
    limit: usize,
) -> Result<Vec<CanonicalInput>> {
    let records = scan_source_epoch_after_limited(
        canonical_root, source_instance_id, continuity_epoch, after_sequence, limit,
    )?;
    let inputs = records.into_iter().map(|record| decode_record(record, source_instance_id, continuity_epoch)).collect::<Result<Vec<_>>>()?;
    validate_contiguous_batch(&inputs, after_sequence)?;
    Ok(inputs)
}

/// Read through the derived replay cache rather than rescanning/decompressing
/// every earlier sealed source partition on each consumer invocation.
pub fn read_next_batch_cursor(
    canonical_root: &Path,
    spool_root: &Path,
    source_instance_id: &str,
    continuity_epoch: u64,
    cursor: SourceReplayCursor,
    limit: usize,
) -> Result<(Vec<CanonicalInput>, SourceReplayCursor)> {
    let after = cursor.next_writer_sequence;
    let batch = scan_source_epoch_after_cursor(
        canonical_root,
        spool_root,
        source_instance_id,
        continuity_epoch,
        cursor,
        limit,
    )?;
    let inputs = batch
        .records
        .into_iter()
        .map(|record| decode_record(record, source_instance_id, continuity_epoch))
        .collect::<Result<Vec<_>>>()?;
    validate_contiguous_batch(&inputs, after)?;
    Ok((inputs, batch.cursor))
}

/// A consumer never fills a sequence hole from another epoch or assumes that a
/// later record makes the missing record irrelevant.
pub fn validate_contiguous_batch(inputs: &[CanonicalInput], after_sequence: u64) -> Result<()> {
    let mut expected = after_sequence;
    for input in inputs {
        expected = expected
            .checked_add(1)
            .context("durable output sequence overflow")?;
        if input.durable_output_sequence != expected {
            bail!("canonical consumer sequence gap or reorder")
        }
        if input.source_frame_sha256.is_empty() {
            bail!("canonical consumer source frame hash missing")
        }
    }
    Ok(())
}

fn decode_record(
    record: FrameRecord,
    source_instance_id: &str,
    continuity_epoch: u64,
) -> Result<CanonicalInput> {
    let metadata = record.metadata;
    if metadata.source_instance_id.as_deref() != Some(source_instance_id)
        || metadata.continuity_epoch != Some(continuity_epoch)
    {
        bail!("canonical frame source binding mismatch")
    }
    let update = SubscribeUpdate::decode(record.raw.as_slice())
        .context("canonical raw frame is not a SubscribeUpdate")?;
    Ok(CanonicalInput {
        update,
        raw: record.raw,
        received_at_unix_ns: u128::try_from(metadata.received_at_unix_ns)
            .context("negative canonical receive timestamp")?,
        source_instance_id: source_instance_id.to_owned(),
        continuity_epoch,
        durable_output_sequence: metadata.writer_sequence,
        source_frame_sha256: metadata.raw_sha256,
    })
}

pub fn persist_checkpoint(path: &Path, input: &CanonicalInput) -> Result<()> {
    let mut checkpoint = Checkpoint {
        schema: "KEFI_CANONICAL_SOURCE_CONSUMER_CHECKPOINT_V2".to_owned(),
        source_instance_id: input.source_instance_id.clone(),
        continuity_epoch: input.continuity_epoch.to_string(),
        durable_output_sequence: input.durable_output_sequence.to_string(),
        source_frame_sha256: input.source_frame_sha256.clone(),
        checkpoint_sha256: String::new(),
    };
    checkpoint.checkpoint_sha256 = hash_excluding_field(&checkpoint, "checkpointSha256")?;
    atomic_jcs_json(path, &checkpoint)
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{TimeZone, Utc};
    use pump_exact_evidence_ledger::{
        sha256_hex, EvidenceMetadata, FrameRecord, LedgerWriter, SCHEMA_VERSION,
    };
    use tempfile::tempdir;
    use yellowstone_grpc_proto::geyser::SubscribeUpdate;

    fn frame(sequence: u64) -> FrameRecord {
        let raw = SubscribeUpdate::default().encode_to_vec();
        FrameRecord {
            metadata: EvidenceMetadata {
                schema_version: SCHEMA_VERSION.to_owned(),
                evidence_id: sha256_hex(format!("test|{sequence}").as_bytes()),
                raw_sha256: sha256_hex(&raw),
                kind: "transaction".to_owned(),
                raw_format: "yellowstone_subscribe_update_protobuf_full_meta".to_owned(),
                received_at_utc: String::new(),
                received_at_unix_ns: sequence as i64,
                decode_at_unix_ns: sequence as i64,
                writer_sequence: sequence,
                continuity_epoch: Some(2),
                source_instance_id: Some("source-a".to_owned()),
                slot: Some(sequence),
                transaction_index: None,
                signature: None,
                account_pubkey: None,
                account_owner: None,
                write_version: None,
                finalized: false,
                successful: None,
                failed: None,
                unresolved: true,
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
                filters: vec![],
            },
            raw,
        }
    }

    #[test]
    fn exact_source_records_restart_from_checkpoint_without_dupes_or_reorder() -> Result<()> {
        let root = tempdir()?;
        let at = Utc.with_ymd_and_hms(2026, 10, 14, 20, 0, 0).unwrap();
        let mut writer = LedgerWriter::open(root.path())?;
        writer.append(at, frame(1))?;
        writer.append(at, frame(2))?;
        let first = read_next_batch(root.path(), "source-a", 2, 0)?;
        assert_eq!(
            first
                .iter()
                .map(|item| item.durable_output_sequence)
                .collect::<Vec<_>>(),
            vec![1, 2]
        );
        let checkpoint = root.path().join("checkpoint.json");
        persist_checkpoint(&checkpoint, &first[1])?;
        assert_eq!(load_checkpoint(&checkpoint, "source-a", 2)?, 2);
        writer.append(at, frame(3))?;
        let resumed = read_next_batch(root.path(), "source-a", 2, 2)?;
        assert_eq!(
            resumed
                .iter()
                .map(|item| item.durable_output_sequence)
                .collect::<Vec<_>>(),
            vec![3]
        );
        assert!(load_checkpoint(&checkpoint, "source-b", 3).is_err());
        Ok(())
    }

    #[test]
    fn source_reader_refuses_sequence_gap_or_reorder() {
        let input = |sequence| CanonicalInput {
            update: SubscribeUpdate::default(),
            raw: vec![],
            received_at_unix_ns: 1,
            source_instance_id: "source-a".into(),
            continuity_epoch: 2,
            durable_output_sequence: sequence,
            source_frame_sha256: "hash".into(),
        };
        assert!(validate_contiguous_batch(&[input(2), input(4)], 1).is_err());
        assert!(validate_contiguous_batch(&[input(3), input(2)], 1).is_err());
    }

    #[test]
    fn cursor_reader_resumes_without_replaying_prior_frames() -> Result<()> {
        let root = tempdir()?;
        let spool = tempdir()?;
        let at = Utc.with_ymd_and_hms(2026, 10, 14, 20, 0, 0).unwrap();
        let mut writer = LedgerWriter::open(root.path())?;
        for sequence in 1..=5 {
            writer.append(at, frame(sequence))?;
        }
        let cursor = SourceReplayCursor::at_sequence("source-a", 2, 0);
        let (first, cursor) = read_next_batch_cursor(
            root.path(), spool.path(), "source-a", 2, cursor, 2,
        )?;
        assert_eq!(
            first.iter().map(|input| input.durable_output_sequence).collect::<Vec<_>>(),
            vec![1, 2]
        );
        let (second, cursor) = read_next_batch_cursor(
            root.path(), spool.path(), "source-a", 2, cursor, 2,
        )?;
        assert_eq!(
            second.iter().map(|input| input.durable_output_sequence).collect::<Vec<_>>(),
            vec![3, 4]
        );
        let (third, _) = read_next_batch_cursor(
            root.path(), spool.path(), "source-a", 2, cursor, 2,
        )?;
        assert_eq!(
            third.iter().map(|input| input.durable_output_sequence).collect::<Vec<_>>(),
            vec![5]
        );
        Ok(())
    }
}
