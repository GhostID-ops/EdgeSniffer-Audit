//! Durable-reader adapter for the prospective frozen kEfi selection test.
//!
//! This process deliberately opens no Yellowstone stream. It consumes the
//! single canonical frame journal, resolves/caches Pool identity outside the
//! source receive path, and writes only outcome-blind pre-buy snapshots.

use anyhow::{bail, Context, Result};
use pumpfun_sidecar::{
    kefi_canonical_source_v1::{
        load_checkpoint, persist_checkpoint, read_next_batch_cursor,
    },
    kefi_frozen_selection_shadow_v1::{
        AuthoritativePoolIdentity, EventSide, FrozenSelectionShadow,
        FrozenSelectionShadowCheckpoint, PublicPoolEvent, SnapshotWriter,
    },
    kefi_pumpswap_semantics_v1::{
        cache_identity, decode_events, pool_is_explicitly_unusable, resolve_pool_identity_batch,
        PoolIdentity,
    },
};
use pump_exact_evidence_ledger::SourceReplayCursor;
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    env, fs,
    path::{Path, PathBuf},
};
use yellowstone_grpc_proto::geyser::subscribe_update::UpdateOneof;

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Binding {
    schema: String,
    canonical_root: String,
    source_instance_id: String,
    continuity_epoch: u64,
    tracked_wallet: String,
    frozen_feature_schema: Vec<String>,
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

fn load_or_bind(root: &Path, binding: &Binding) -> Result<()> {
    let path = root.join("run-binding.json");
    if path.exists() {
        let existing: Binding = serde_json::from_slice(&fs::read(&path)?)?;
        if existing.canonical_root != binding.canonical_root
            || existing.source_instance_id != binding.source_instance_id
            || existing.continuity_epoch != binding.continuity_epoch
            || existing.tracked_wallet != binding.tracked_wallet
            || existing.frozen_feature_schema != binding.frozen_feature_schema
        {
            bail!("SHADOW_OUTPUT_BINDING_MISMATCH")
        }
    } else {
        atomic_json(&path, binding)?;
    }
    Ok(())
}

fn load_cache(path: &Path) -> Result<BTreeMap<String, PoolIdentity>> {
    if !path.exists() {
        return Ok(BTreeMap::new());
    }
    let cache = serde_json::from_slice(&fs::read(path)?)?;
    Ok(cache)
}

fn load_shadow(
    path: &Path,
    source_instance_id: &str,
    continuity_epoch: u64,
    tracked_wallet: &str,
    after_sequence: u64,
) -> Result<FrozenSelectionShadow> {
    if !path.exists() {
        if after_sequence != 0 {
            bail!("SHADOW_STATE_CHECKPOINT_MISSING")
        }
        return FrozenSelectionShadow::new(
            source_instance_id.into(),
            continuity_epoch,
            tracked_wallet.into(),
            60,
        );
    }
    let checkpoint: FrozenSelectionShadowCheckpoint = serde_json::from_slice(&fs::read(path)?)?;
    if checkpoint.source_instance_id != source_instance_id
        || checkpoint.continuity_epoch != continuity_epoch
        || checkpoint.tracked_wallet != tracked_wallet
        || checkpoint.last_sequence != after_sequence
    {
        bail!("SHADOW_AND_CANONICAL_CHECKPOINT_MISMATCH")
    }
    FrozenSelectionShadow::resume(checkpoint)
}

fn load_or_rebuild_replay_cursor(
    path: &Path,
    source_instance_id: &str,
    continuity_epoch: u64,
    after_sequence: u64,
) -> Result<SourceReplayCursor> {
    if !path.exists() {
        return Ok(SourceReplayCursor::at_sequence(
            source_instance_id,
            continuity_epoch,
            after_sequence,
        ));
    }
    let cursor: SourceReplayCursor = serde_json::from_slice(&fs::read(path)?)?;
    // The durable consumer checkpoint is authoritative.  A cursor is only a
    // derived performance cache, so a torn/stale cursor is rebuilt exactly at
    // the committed sequence instead of either duplicating or skipping input.
    if cursor.schema != "KEFI_SOURCE_REPLAY_CURSOR_V1"
        || cursor.source_instance_id != source_instance_id
        || cursor.continuity_epoch != continuity_epoch
        || cursor.next_writer_sequence != after_sequence
    {
        return Ok(SourceReplayCursor::at_sequence(
            source_instance_id,
            continuity_epoch,
            after_sequence,
        ));
    }
    Ok(cursor)
}

fn public_event(
    input: &pumpfun_sidecar::kefi_canonical_source_v1::CanonicalInput,
    signature: &str,
    event: &pumpfun_sidecar::kefi_pumpswap_semantics_v1::Event,
) -> PublicPoolEvent {
    PublicPoolEvent {
        source_instance_id: input.source_instance_id.clone(),
        continuity_epoch: input.continuity_epoch,
        durable_sequence: input.durable_output_sequence,
        received_at_unix_ns: input.received_at_unix_ns,
        transaction_identity: signature.to_owned(),
        pool: event.pool.clone(),
        user: event.user.clone(),
        side: match event.side {
            "BUY" => EventSide::Buy,
            "SELL" => EventSide::Sell,
            _ => unreachable!("decoder side"),
        },
        pool_base_raw: event.pool_base,
        pool_quote_raw: event.pool_quote,
        pool_quote_delta_raw: event.pool_quote_delta,
        fee_tuple: (
            event.lp_fee_bps,
            event.protocol_fee_bps,
            event.coin_creator_bps,
            event.cashback_bps,
            event.buyback_bps,
        ),
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let canonical_root = PathBuf::from(required("KEFI_SELECTION_SHADOW_CANONICAL_ROOT")?);
    let source_instance_id = required("KEFI_SELECTION_SHADOW_SOURCE_INSTANCE_ID")?;
    let continuity_epoch: u64 = required("KEFI_SELECTION_SHADOW_CONTINUITY_EPOCH")?.parse()?;
    let tracked_wallet = required("KEFI_TRACKED_WALLET")?;
    let rpc_url = required("KEFI_POOL_IDENTITY_RPC_URL")?;
    let output_root = PathBuf::from(required("KEFI_SELECTION_SHADOW_OUTPUT_ROOT")?);
    let max_inputs: usize = env::var("KEFI_SELECTION_SHADOW_MAX_INPUTS")
        .unwrap_or_else(|_| "4096".into())
        .parse()?;
    // This is an offline reader on the 32 GiB VPS, not the live source hot
    // path. A 65k-frame cap keeps memory bounded while avoiding hundreds of
    // needless restart/checkpoint cycles for a sealed multi-million-frame run.
    if max_inputs == 0 || max_inputs > 65_536 {
        bail!("KEFI_SELECTION_SHADOW_MAX_INPUTS_INVALID")
    }
    fs::create_dir_all(&output_root)?;
    let binding = Binding {
        schema: "KEFI_FROZEN_SELECTION_SHADOW_BINDING_V1".into(),
        canonical_root: canonical_root.display().to_string(),
        source_instance_id: source_instance_id.clone(),
        continuity_epoch,
        tracked_wallet: tracked_wallet.clone(),
        frozen_feature_schema:
            pumpfun_sidecar::kefi_frozen_selection_shadow_v1::FROZEN_SELECTION_FEATURES
                .iter()
                .map(|name| (*name).to_owned())
                .collect(),
    };
    load_or_bind(&output_root, &binding)?;
    let checkpoint = output_root.join("consumer-checkpoint.json");
    let after = load_checkpoint(&checkpoint, &source_instance_id, continuity_epoch)?;
    let shadow_state_path = output_root.join("shadow-state.json");
    let pending_input_path = output_root.join("pending-input.json");
    if pending_input_path.exists() {
        bail!("SHADOW_PENDING_INPUT_REQUIRES_FRESH_COHORT")
    }
    let replay_cursor_path = output_root.join("replay-cursor.json");
    let replay_spool_root = output_root.join("replay-spool-v1");
    let replay_cursor = load_or_rebuild_replay_cursor(
        &replay_cursor_path,
        &source_instance_id,
        continuity_epoch,
        after,
    )?;
    let (inputs, replay_cursor) = read_next_batch_cursor(
        &canonical_root,
        &replay_spool_root,
        &source_instance_id,
        continuity_epoch,
        replay_cursor,
        max_inputs,
    )?;
    let http = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(10))
        .timeout(std::time::Duration::from_secs(30))
        .build()?;
    let cache_path = output_root.join("pool-identity-cache.json");
    let mut cache = load_cache(&cache_path)?;
    let mut shadow = load_shadow(
        &shadow_state_path,
        &source_instance_id,
        continuity_epoch,
        &tracked_wallet,
        after,
    )?;
    let mut writer = SnapshotWriter::open(&output_root)?;
    let pending_from = inputs.first().map(|input| input.durable_output_sequence);
    let pending_through = inputs.last().map(|input| input.durable_output_sequence);
    if let (Some(from), Some(through)) = (pending_from, pending_through) {
        // A batch remains all-or-nothing: a crash before this durable commit
        // leaves the existing fail-closed pending marker rather than allowing
        // a partly materialized prefix to masquerade as resumable evidence.
        atomic_json(
            &pending_input_path,
            &serde_json::json!({
                "schema":"KEFI_FROZEN_SELECTION_PENDING_BATCH_V2",
                "fromDurableOutputSequence":from,
                "throughDurableOutputSequence":through,
                "checkpointPolicy":"ATOMIC_BATCH_ONLY",
            }),
        )?;
    }
    let mut processed = 0_u64;
    let mut snapshots = 0_u64;
    let mut last_input = None;
    for input in inputs {
        let mut applied_public_state = false;
        if let Some(UpdateOneof::Transaction(transaction)) = input.update.update_oneof.as_ref() {
            let info = transaction
                .transaction
                .as_ref()
                .context("CANONICAL_TRANSACTION_INFO_MISSING")?;
            let meta = info.meta.as_ref().context("CANONICAL_FULL_META_MISSING")?;
            let decoded = match decode_events(meta) {
                Ok(events) => events,
                // The broad program filter can legitimately include a routed
                // or non-swap Pump/PumpSwap transaction. It has no public
                // pool transition for this study, but must still advance the
                // durable cursor rather than aborting the entire replay.
                Err(error) if error.to_string().contains("EMPTY_PUMPSWAP_TRANSACTION") => {
                    Vec::new()
                }
                Err(error) => bail!("PUMPSWAP_EVENT_DECODE_FAILED:{error:#}"),
            };
            if !decoded.is_empty() {
                let unresolved = decoded
                    .iter()
                    .map(|(_, event)| event.pool.clone())
                    .filter(|pool| !cache.contains_key(pool))
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect::<Vec<_>>();
                let mut excluded_pools = BTreeSet::new();
                for chunk in unresolved.chunks(100) {
                    for (pool, result) in
                        resolve_pool_identity_batch(&http, &rpc_url, chunk).await?
                    {
                        match result {
                            Ok(identity) => {
                                cache_identity(&mut cache, identity)?;
                            }
                            Err(error) if pool_is_explicitly_unusable(&error) => {
                                excluded_pools.insert(pool);
                            }
                            Err(error) => bail!("AUTHORITATIVE_POOL_IDENTITY_UNAVAILABLE:{pool}:{error}"),
                        }
                    }
                }
                let signature = bs58::encode(&info.signature).into_string();
                let events = decoded
                    .into_iter()
                    .filter(|(_, event)| !excluded_pools.contains(&event.pool))
                    .map(|(_, event)| {
                        let identity = cache
                            .get(&event.pool)
                            .context("POOL_IDENTITY_CACHE_MISSING")?;
                        Ok((
                            public_event(&input, &signature, &event),
                            AuthoritativePoolIdentity {
                                pool: identity.pool.clone(),
                                mint: identity.selection_mint.clone(),
                                layout: identity.layout.clone(),
                            },
                        ))
                    })
                    .collect::<Result<Vec<_>>>()?;
                if !events.is_empty() {
                    if let Some(snapshot) = shadow.observe_transaction(events)? {
                        writer.write(&snapshot)?;
                        snapshots += 1;
                    }
                    applied_public_state = true;
                }
            }
        }
        if !applied_public_state {
            shadow.observe_empty_frame(
                &input.source_instance_id,
                input.continuity_epoch,
                input.durable_output_sequence,
                input.received_at_unix_ns,
            )?;
        }
        last_input = Some(input);
        processed += 1;
    }
    atomic_json(&cache_path, &cache)?;
    if let Some(last_input) = last_input.as_ref() {
        atomic_json(&shadow_state_path, &shadow.checkpoint())?;
        persist_checkpoint(&checkpoint, last_input)?;
        atomic_json(&replay_cursor_path, &replay_cursor)?;
        fs::remove_file(&pending_input_path)?;
    }
    println!(
        "{}",
        serde_json::json!({"schema":"KEFI_FROZEN_SELECTION_SHADOW_CONSUMER_V1","processedInputs":processed,"snapshotsWritten":snapshots,"outcomesOrPnlAccessed":false})
    );
    Ok(())
}
