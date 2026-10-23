// Offline Pump bonding-curve frozen-selection cohort builder.
//
// This consumer is deliberately separate from the PumpSwap selection
// consumer. It reads the sealed canonical journal, accepts only exact Pump
// bonding-curve transitions, derives the canonical curve PDA from the mint,
// and writes outcome-blind snapshots. It performs no RPC, no PnL, and no
// systemd/live-service work.

use anyhow::{bail, Context, Result};
use pump_exact_evidence_ledger::SourceReplayCursor;
use pumpfun_sidecar::{
    kefi_canonical_source_v1::{load_checkpoint, persist_checkpoint, read_next_batch_cursor},
    kefi_frozen_pump_curve_transition_tape_v1::pump_curve_for_mint,
    kefi_frozen_selection_shadow_v1::{
        AuthoritativePoolIdentity, EventSide, FrozenSelectionShadow,
        FrozenSelectionShadowCheckpoint, PublicPoolEvent, SnapshotWriter,
    },
    kefi_pump_curve_semantics_v1::decode_exact_pump_transitions,
};
use serde::{Deserialize, Serialize};
use std::{env, fs, path::{Path, PathBuf}};
use yellowstone_grpc_proto::geyser::subscribe_update::UpdateOneof;

const MAX_INPUTS: usize = 65_536;

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Binding {
    schema: String,
    canonical_root: String,
    source_instance_id: String,
    continuity_epoch: u64,
    tracked_wallet: String,
    warmup_seconds: u64,
    venue: String,
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

fn atomic_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, serde_json::to_vec(value)?)?;
    fs::rename(temporary, path)?;
    Ok(())
}

fn atomic_bytes(path: &Path, bytes: &[u8]) -> Result<()> {
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, bytes)?;
    fs::rename(temporary, path)?;
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

fn load_shadow(
    path: &Path,
    source: &str,
    epoch: u64,
    wallet: &str,
    after: u64,
    warmup_seconds: u64,
) -> Result<FrozenSelectionShadow> {
    if !path.exists() {
        if after != 0 {
            bail!("PUMP_CURVE_SELECTION_STATE_CHECKPOINT_MISSING");
        }
        return FrozenSelectionShadow::new(
            source.to_owned(),
            epoch,
            wallet.to_owned(),
            warmup_seconds,
        );
    }
    let checkpoint: FrozenSelectionShadowCheckpoint = serde_json::from_slice(&fs::read(path)?)?;
    if checkpoint.source_instance_id != source
        || checkpoint.continuity_epoch != epoch
        || checkpoint.tracked_wallet != wallet
        || checkpoint.last_sequence != after
    {
        bail!("PUMP_CURVE_SELECTION_STATE_BINDING_MISMATCH");
    }
    FrozenSelectionShadow::resume(checkpoint)
}

fn event_for_transition(
    input: &pumpfun_sidecar::kefi_canonical_source_v1::CanonicalInput,
    signature: &str,
    transition: &pumpfun_sidecar::kefi_pump_curve_semantics_v1::ExactPumpTransition,
) -> Result<(PublicPoolEvent, AuthoritativePoolIdentity)> {
    let event = &transition.event;
    let curve = pump_curve_for_mint(&event.mint)?;
    let side = match event.side {
        "BUY" => EventSide::Buy,
        "SELL" => EventSide::Sell,
        _ => bail!("PUMP_CURVE_UNSUPPORTED_SIDE"),
    };
    Ok((
        PublicPoolEvent {
            source_instance_id: input.source_instance_id.clone(),
            continuity_epoch: input.continuity_epoch,
            durable_sequence: input.durable_output_sequence,
            received_at_unix_ns: input.received_at_unix_ns,
            transaction_identity: signature.to_owned(),
            pool: curve.clone(),
            user: event.user.clone(),
            side,
            // Pump's virtual reserves are the public curve state used for the
            // frozen candidate. No PumpSwap reserve layout is reused.
            pool_base_raw: event.virtual_token_reserves_raw,
            pool_quote_raw: event.virtual_sol_reserves_raw,
            pool_quote_delta_raw: event.sol_amount_raw,
            fee_tuple: (event.fee_bps, event.creator_fee_bps, 0, 0, 0),
        },
        AuthoritativePoolIdentity {
            pool: curve,
            mint: event.mint.clone(),
            layout: "PUMP_BONDING_CURVE_V1".to_owned(),
        },
    ))
}

#[tokio::main]
async fn main() -> Result<()> {
    let canonical_root = PathBuf::from(required("KEFI_PUMP_CURVE_SELECTION_CANONICAL_ROOT")?);
    let source = required("KEFI_PUMP_CURVE_SELECTION_SOURCE_INSTANCE_ID")?;
    let epoch: u64 = required("KEFI_PUMP_CURVE_SELECTION_CONTINUITY_EPOCH")?.parse()?;
    let wallet = required("KEFI_PUMP_CURVE_SELECTION_TRACKED_WALLET")?;
    let root = PathBuf::from(required("KEFI_PUMP_CURVE_SELECTION_OUTPUT_ROOT")?);
    let warmup_seconds: u64 = env::var("KEFI_PUMP_CURVE_SELECTION_WARMUP_SECONDS")
        .unwrap_or_else(|_| "60".into())
        .parse()?;
    let limit: usize = env::var("KEFI_PUMP_CURVE_SELECTION_MAX_INPUTS")
        .unwrap_or_else(|_| "65536".into())
        .parse()?;
    if limit == 0 || limit > MAX_INPUTS || warmup_seconds == 0 {
        bail!("KEFI_PUMP_CURVE_SELECTION_CONFIGURATION_INVALID");
    }
    fs::create_dir_all(&root)?;
    fs::create_dir_all(root.join("quarantine"))?;
    let binding = Binding {
        schema: "KEFI_FROZEN_PUMP_CURVE_SELECTION_COHORT_BINDING_V1".into(),
        canonical_root: canonical_root.display().to_string(),
        source_instance_id: source.clone(),
        continuity_epoch: epoch,
        tracked_wallet: wallet.clone(),
        warmup_seconds,
        venue: "PUMP_BONDING_CURVE".into(),
    };
    let binding_path = root.join("run-binding.json");
    if binding_path.exists() {
        let existing: Binding = serde_json::from_slice(&fs::read(&binding_path)?)?;
        if serde_json::to_vec(&existing)? != serde_json::to_vec(&binding)? {
            bail!("PUMP_CURVE_SELECTION_OUTPUT_BINDING_MISMATCH");
        }
    } else {
        atomic_json(&binding_path, &binding)?;
    }

    let checkpoint_path = root.join("consumer-checkpoint.json");
    let after = load_checkpoint(&checkpoint_path, &source, epoch)?;
    let cursor_path = root.join("replay-cursor.json");
    let cursor = load_cursor(&cursor_path, &source, epoch, after)?;
    let spool = root.join("replay-spool-v1");
    let (inputs, next_cursor) = read_next_batch_cursor(
        &canonical_root, &spool, &source, epoch, cursor, limit,
    )?;
    if inputs.is_empty() {
        println!("{{\"status\":\"SOURCE_TAIL\"}}");
        return Ok(());
    }

    let shadow_path = root.join("shadow-state.json");
    let pending_path = root.join("pending-input.json");
    if pending_path.exists() {
        bail!("PUMP_CURVE_SELECTION_PENDING_INPUT_REQUIRES_REVIEW");
    }
    let mut shadow = load_shadow(
        &shadow_path, &source, epoch, &wallet, after, warmup_seconds,
    )?;
    let mut writer = SnapshotWriter::open(&root)?;
    let first = inputs.first().context("NO_FIRST_INPUT")?.durable_output_sequence;
    let through = inputs.last().context("NO_LAST_INPUT")?.durable_output_sequence;
    atomic_json(
        &pending_path,
        &serde_json::json!({
            "schema":"KEFI_FROZEN_PUMP_CURVE_SELECTION_PENDING_BATCH_V1",
            "fromDurableOutputSequence":first,
            "throughDurableOutputSequence":through,
            "checkpointPolicy":"ATOMIC_BATCH_ONLY"
        }),
    )?;

    let mut snapshots = 0_u64;
    let mut quarantined = Vec::new();
    for input in &inputs {
        let mut applied = false;
        if let Some(UpdateOneof::Transaction(transaction)) = input.update.update_oneof.as_ref() {
            let signature = transaction
                .transaction
                .as_ref()
                .map(|info| bs58::encode(&info.signature).into_string())
                .unwrap_or_default();
            let result = transaction
                .transaction
                .as_ref()
                .context("PUMP_CURVE_TRANSACTION_INFO_MISSING")
                .and_then(decode_exact_pump_transitions);
            match result {
                Ok(transitions) if transitions.is_empty() => {}
                Ok(transitions) => {
                    let events = transitions
                        .iter()
                        .map(|transition| event_for_transition(input, &signature, transition))
                        .collect::<Result<Vec<_>>>();
                    match events.and_then(|events| shadow.observe_transaction(events)) {
                        Ok(Some(snapshot)) => {
                            writer.write(&snapshot)?;
                            snapshots += 1;
                            applied = true;
                        }
                        Ok(None) => applied = true,
                        Err(error) => quarantined.push(Quarantine {
                            schema: "KEFI_FROZEN_PUMP_CURVE_SELECTION_QUARANTINE_V1",
                            durable_output_sequence: input.durable_output_sequence,
                            source_frame_sha256: input.source_frame_sha256.clone(),
                            signature: signature.clone(),
                            slot: transaction.slot,
                            reason: error.to_string(),
                        }),
                    }
                }
                Err(error) => quarantined.push(Quarantine {
                    schema: "KEFI_FROZEN_PUMP_CURVE_SELECTION_QUARANTINE_V1",
                    durable_output_sequence: input.durable_output_sequence,
                    source_frame_sha256: input.source_frame_sha256.clone(),
                    signature,
                    slot: transaction.slot,
                    reason: error.to_string(),
                }),
            }
        }
        if !applied {
            shadow.observe_empty_frame(
                &input.source_instance_id,
                input.continuity_epoch,
                input.durable_output_sequence,
                input.received_at_unix_ns,
            )?;
        }
    }

    let quarantine_bytes = quarantined
        .iter()
        .map(|row| serde_json::to_string(row).unwrap() + "\n")
        .collect::<String>();
    atomic_bytes(
        &root.join("quarantine").join(format!("{first:020}-{through:020}.jsonl")),
        quarantine_bytes.as_bytes(),
    )?;
    atomic_json(&shadow_path, &shadow.checkpoint())?;
    persist_checkpoint(&checkpoint_path, inputs.last().context("NO_LAST_INPUT")?)?;
    atomic_json(&cursor_path, &next_cursor)?;
    fs::remove_file(&pending_path)?;
    println!(
        "{}",
        serde_json::json!({
            "schema":"KEFI_FROZEN_PUMP_CURVE_SELECTION_COHORT_CONSUMER_V1",
            "from":first,
            "through":through,
            "snapshotsWritten":snapshots,
            "quarantined":quarantined.len(),
            "outcomesOrPnlAccessed":false,
            "venue":"PUMP_BONDING_CURVE"
        })
    );
    Ok(())
}
