//! Restart-safe Pump bonding-curve tape materializer.
//!
//! This is a separate consumer from the existing PumpSwap economics tape. It
//! never performs pool identity RPCs, never applies PumpSwap fee arithmetic,
//! and never emits PnL. Unsupported or ambiguous Pump transitions are written
//! to quarantine while the source checkpoint advances only after the complete
//! input batch has been durably written.

use anyhow::{Context, Result};
use pump_exact_evidence_ledger::SourceReplayCursor;
use pumpfun_sidecar::{
    kefi_canonical_source_v1::{load_checkpoint, persist_checkpoint, read_next_batch_cursor},
    kefi_frozen_pump_curve_transition_tape_v1::materialize_transaction,
};
use serde::{Deserialize, Serialize};
use std::{
    env, fs,
    path::{Path, PathBuf},
};
use yellowstone_grpc_proto::geyser::subscribe_update::UpdateOneof;

const MAX_INPUTS: usize = 65_536;

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Binding {
    schema: String,
    canonical_root: String,
    source_instance_id: String,
    continuity_epoch: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Quarantine {
    schema: &'static str,
    durable_output_sequence: u64,
    source_frame_sha256: String,
    signature: String,
    slot: u64,
    reason: String,
}

fn required(name: &str) -> Result<String> {
    env::var(name).with_context(|| format!("{name} required"))
}

fn atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, bytes)?;
    fs::rename(temporary, path)?;
    Ok(())
}

fn bind(root: &Path, value: &Binding) -> Result<()> {
    let path = root.join("run-binding.json");
    fs::create_dir_all(root)?;
    if path.exists() {
        let old: Binding = serde_json::from_slice(&fs::read(&path)?)?;
        if old.canonical_root != value.canonical_root
            || old.source_instance_id != value.source_instance_id
            || old.continuity_epoch != value.continuity_epoch
        {
            anyhow::bail!("PUMP_CURVE_TAPE_BINDING_MISMATCH")
        }
    } else {
        atomic(&path, &serde_json::to_vec(value)?)?;
    }
    Ok(())
}

fn load_cursor(path: &Path, source: &str, epoch: u64, after: u64) -> Result<SourceReplayCursor> {
    if !path.exists() {
        return Ok(SourceReplayCursor::at_sequence(source, epoch, after));
    }
    let cursor: SourceReplayCursor = serde_json::from_slice(&fs::read(path)?)?;
    if cursor.source_instance_id != source
        || cursor.continuity_epoch != epoch
        || cursor.next_writer_sequence != after
    {
        return Ok(SourceReplayCursor::at_sequence(source, epoch, after));
    }
    Ok(cursor)
}

fn write_cursor(path: &Path, cursor: &SourceReplayCursor) -> Result<()> {
    atomic(path, &serde_json::to_vec(cursor)?)
}

#[tokio::main]
async fn main() -> Result<()> {
    let canonical_root = PathBuf::from(required("KEFI_ECONOMICS_CANONICAL_ROOT")?);
    let source = required("KEFI_ECONOMICS_SOURCE_INSTANCE_ID")?;
    let epoch: u64 = required("KEFI_ECONOMICS_CONTINUITY_EPOCH")?.parse()?;
    let root = PathBuf::from(required("KEFI_PUMP_CURVE_TAPE_OUTPUT_ROOT")?);
    let limit: usize = env::var("KEFI_ECONOMICS_MAX_INPUTS")
        .unwrap_or_else(|_| "65536".into())
        .parse()?;
    if limit == 0 || limit > MAX_INPUTS {
        anyhow::bail!("KEFI_ECONOMICS_MAX_INPUTS_INVALID");
    }
    bind(
        &root,
        &Binding {
            schema: "KEFI_FROZEN_PUMP_CURVE_TAPE_BINDING_V1".into(),
            canonical_root: canonical_root.display().to_string(),
            source_instance_id: source.clone(),
            continuity_epoch: epoch,
        },
    )?;
    fs::create_dir_all(root.join("chunks"))?;
    fs::create_dir_all(root.join("quarantine"))?;

    let checkpoint_path = root.join("consumer-checkpoint.json");
    let after = load_checkpoint(&checkpoint_path, &source, epoch)?;
    let cursor_path = root.join("replay-cursor.json");
    let cursor = load_cursor(&cursor_path, &source, epoch, after)?;
    let spool = root.join("replay-spool-v1");
    let (inputs, next_cursor) =
        read_next_batch_cursor(&canonical_root, &spool, &source, epoch, cursor, limit)?;
    if inputs.is_empty() {
        println!("{{\"status\":\"SOURCE_TAIL\"}}");
        return Ok(());
    }

    let mut rows = Vec::new();
    let mut quarantined = Vec::new();
    for input in &inputs {
        let Some(UpdateOneof::Transaction(transaction_update)) = input.update.update_oneof.as_ref()
        else {
            continue;
        };
        let Some(info) = transaction_update.transaction.as_ref() else {
            quarantined.push(Quarantine {
                schema: "KEFI_FROZEN_PUMP_CURVE_TAPE_QUARANTINE_V1",
                durable_output_sequence: input.durable_output_sequence,
                source_frame_sha256: input.source_frame_sha256.clone(),
                signature: String::new(),
                slot: transaction_update.slot,
                reason: "PUMP_TRANSACTION_INFO_MISSING".into(),
            });
            continue;
        };
        match materialize_transaction(
            info,
            transaction_update.slot,
            &input.source_instance_id,
            input.continuity_epoch,
            input.durable_output_sequence,
            input.received_at_unix_ns,
            &input.source_frame_sha256,
        ) {
            Ok(mut materialized) => rows.append(&mut materialized),
            Err(error) => quarantined.push(Quarantine {
                schema: "KEFI_FROZEN_PUMP_CURVE_TAPE_QUARANTINE_V1",
                durable_output_sequence: input.durable_output_sequence,
                source_frame_sha256: input.source_frame_sha256.clone(),
                signature: bs58::encode(&info.signature).into_string(),
                slot: transaction_update.slot,
                reason: error.to_string(),
            }),
        }
    }

    let first = inputs
        .first()
        .context("NO_FIRST_INPUT")?
        .durable_output_sequence;
    let through = inputs
        .last()
        .context("NO_LAST_INPUT")?
        .durable_output_sequence;
    let rows_bytes = rows
        .iter()
        .map(|row| serde_json::to_string(row).unwrap() + "\n")
        .collect::<String>()
        .into_bytes();
    let quarantine_bytes = quarantined
        .iter()
        .map(|row| serde_json::to_string(row).unwrap() + "\n")
        .collect::<String>()
        .into_bytes();
    atomic(
        &root
            .join("chunks")
            .join(format!("{first:020}-{through:020}.jsonl")),
        &rows_bytes,
    )?;
    atomic(
        &root
            .join("quarantine")
            .join(format!("{first:020}-{through:020}.jsonl")),
        &quarantine_bytes,
    )?;
    persist_checkpoint(&checkpoint_path, inputs.last().context("NO_LAST_INPUT")?)?;
    write_cursor(&cursor_path, &next_cursor)?;
    println!("{{\"status\":\"BATCH_COMMITTED\",\"from\":{first},\"through\":{through},\"rows\":{},\"quarantined\":{}}}", rows.len(), quarantined.len());
    Ok(())
}
