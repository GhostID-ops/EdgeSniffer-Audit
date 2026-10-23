//! Outcome-blind pre-action state machine for the next direct kEfi test.
//!
//! This module intentionally contains no gRPC, RPC, scoring, outcome, or PnL
//! code.  Its caller must provide an authoritative pool identity and a single
//! durable public-event order.  It snapshots public state before applying a
//! tracked-wallet buy, so the frozen Python scorer can consume the unchanged
//! candidate field schema later.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, HashSet, VecDeque},
    fs,
    path::{Path, PathBuf},
};

pub const FROZEN_SELECTION_FEATURES: [&str; 13] = [
    "log_pre_pool_quote",
    "log_pre_pool_base",
    "amm_fee_bps",
    "observed_pool_age_seconds",
    "seconds_since_previous_observed_buy",
    "prior_pre_pool_quote_log_change",
    "prior_buy_count_5s",
    "prior_buy_count_20s",
    "prior_buy_count_60s",
    "prior_buy_quote_5s",
    "prior_buy_quote_20s",
    "prior_buy_quote_60s",
    "no_prior_observed_buy",
];

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AuthoritativePoolIdentity {
    pub pool: String,
    pub mint: String,
    pub layout: String,
}

#[derive(Clone, Debug)]
pub struct PublicPoolEvent {
    pub source_instance_id: String,
    pub continuity_epoch: u64,
    pub durable_sequence: u64,
    pub received_at_unix_ns: u128,
    pub transaction_identity: String,
    pub pool: String,
    pub user: String,
    pub side: EventSide,
    pub pool_base_raw: u64,
    pub pool_quote_raw: u64,
    pub pool_quote_delta_raw: u64,
    pub fee_tuple: (u64, u64, u64, u64, u64),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum EventSide {
    Buy,
    Sell,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Flow {
    at_ns: u128,
    side: EventSide,
    quote_raw: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct PoolState {
    identity: AuthoritativePoolIdentity,
    first_seen_ns: u128,
    last_event_ns: u128,
    last_sequence: u64,
    base_raw: u64,
    quote_raw: u64,
    prior_quote_raw: Option<u64>,
    fees: (u64, u64, u64, u64, u64),
    last_buy_ns: Option<u128>,
    flows: VecDeque<Flow>,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct FrozenCandidate {
    pub pool: String,
    pub mint: String,
    pub quote_reserve_raw: u64,
    pub base_reserve_raw: u64,
    pub fee_tuple: (u64, u64, u64, u64, u64),
    pub observed_pool_age_seconds: u64,
    pub seconds_since_prior_observed_buy: Option<u64>,
    pub prior_pre_pool_quote_raw: Option<u64>,
    pub buy_count_5s: u64,
    pub buy_count_20s: u64,
    pub buy_count_60s: u64,
    pub buy_quote_flow_5s: u64,
    pub buy_quote_flow_20s: u64,
    pub buy_quote_flow_60s: u64,
    pub no_prior_buy: bool,
    pub sell_count_60s: u64,
    pub sell_quote_flow_60s: u64,
    pub first_public_observation_unix_ns: u128,
    pub last_event_unix_ns: u128,
    pub last_durable_output_sequence: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct FrozenSelectionSnapshot {
    pub schema: &'static str,
    pub source_instance_id: String,
    pub continuity_epoch: u64,
    pub teacher_transaction_identity: String,
    pub teacher_durable_output_sequence: u64,
    pub teacher_received_at_unix_ns: u128,
    pub chosen_pool: String,
    pub chosen_mint: String,
    pub frozen_feature_schema: [&'static str; 13],
    pub candidates: Vec<FrozenCandidate>,
}

#[derive(Clone, Debug, Serialize)]
pub struct FrozenSelectionSnapshotReceipt {
    pub schema: &'static str,
    pub snapshot_path: String,
    pub snapshot_sha256: String,
    pub source_instance_id: String,
    pub continuity_epoch: u64,
    pub teacher_transaction_identity: String,
    pub teacher_durable_output_sequence: u64,
    pub pre_action_only: bool,
    pub teacher_event_excluded_from_state: bool,
    pub authoritative_pool_identity: bool,
    pub frozen_feature_schema: [&'static str; 13],
}

/// Atomic output writer. Scores, ranks, outcomes, and PnL are intentionally
/// absent: collection must remain blind until the prospectively sealed cohort
/// is evaluated by the frozen scorer.
pub struct SnapshotWriter {
    root: PathBuf,
    next_ordinal: u64,
}

impl SnapshotWriter {
    pub fn open(root: &Path) -> Result<Self> {
        fs::create_dir_all(root.join("snapshots"))?;
        fs::create_dir_all(root.join("snapshot-receipts"))?;
        Ok(Self {
            root: root.to_owned(),
            next_ordinal: fs::read_dir(root.join("snapshots"))?.count() as u64 + 1,
        })
    }

    pub fn write(
        &mut self,
        snapshot: &FrozenSelectionSnapshot,
    ) -> Result<FrozenSelectionSnapshotReceipt> {
        let filename = format!("{:020}.json", self.next_ordinal);
        let snapshot_path = self.root.join("snapshots").join(&filename);
        if snapshot_path.exists() {
            bail!("SNAPSHOT_OUTPUT_COLLISION")
        }
        let bytes = serde_json::to_vec(snapshot)?;
        let temporary = snapshot_path.with_extension("tmp");
        fs::write(&temporary, &bytes)?;
        fs::rename(&temporary, &snapshot_path)?;
        let receipt = FrozenSelectionSnapshotReceipt {
            schema: "KEFI_FROZEN_SELECTION_PROSPECTIVE_SNAPSHOT_RECEIPT_V1",
            snapshot_path: snapshot_path.display().to_string(),
            snapshot_sha256: format!("{:x}", Sha256::digest(&bytes)),
            source_instance_id: snapshot.source_instance_id.clone(),
            continuity_epoch: snapshot.continuity_epoch,
            teacher_transaction_identity: snapshot.teacher_transaction_identity.clone(),
            teacher_durable_output_sequence: snapshot.teacher_durable_output_sequence,
            pre_action_only: true,
            teacher_event_excluded_from_state: true,
            authoritative_pool_identity: true,
            frozen_feature_schema: FROZEN_SELECTION_FEATURES,
        };
        let receipt_path = self.root.join("snapshot-receipts").join(filename);
        let receipt_temporary = receipt_path.with_extension("tmp");
        fs::write(&receipt_temporary, serde_json::to_vec(&receipt)?)?;
        fs::rename(&receipt_temporary, receipt_path)?;
        self.next_ordinal = self
            .next_ordinal
            .checked_add(1)
            .context("SNAPSHOT_ORDINAL_OVERFLOW")?;
        Ok(receipt)
    }
}

pub struct FrozenSelectionShadow {
    source_instance_id: String,
    continuity_epoch: u64,
    tracked_wallet: String,
    warmup_ns: u128,
    source_started_ns: Option<u128>,
    last_sequence: u64,
    pools: BTreeMap<String, PoolState>,
    observed_wallet_mints: HashSet<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FrozenSelectionShadowCheckpoint {
    pub schema: String,
    pub source_instance_id: String,
    pub continuity_epoch: u64,
    pub tracked_wallet: String,
    pub warmup_ns: u128,
    pub source_started_ns: Option<u128>,
    pub last_sequence: u64,
    pools: BTreeMap<String, PoolState>,
    observed_wallet_mints: HashSet<String>,
}

impl FrozenSelectionShadow {
    pub fn new(
        source_instance_id: String,
        continuity_epoch: u64,
        tracked_wallet: String,
        warmup_seconds: u64,
    ) -> Result<Self> {
        if source_instance_id.is_empty() || tracked_wallet.is_empty() || warmup_seconds == 0 {
            bail!("INVALID_FROZEN_SELECTION_SHADOW_BINDING")
        }
        Ok(Self {
            source_instance_id,
            continuity_epoch,
            tracked_wallet,
            warmup_ns: u128::from(warmup_seconds) * 1_000_000_000,
            source_started_ns: None,
            last_sequence: 0,
            pools: BTreeMap::new(),
            observed_wallet_mints: HashSet::new(),
        })
    }

    pub fn checkpoint(&self) -> FrozenSelectionShadowCheckpoint {
        FrozenSelectionShadowCheckpoint {
            schema: "KEFI_FROZEN_SELECTION_SHADOW_CHECKPOINT_V1".into(),
            source_instance_id: self.source_instance_id.clone(),
            continuity_epoch: self.continuity_epoch,
            tracked_wallet: self.tracked_wallet.clone(),
            warmup_ns: self.warmup_ns,
            source_started_ns: self.source_started_ns,
            last_sequence: self.last_sequence,
            pools: self.pools.clone(),
            observed_wallet_mints: self.observed_wallet_mints.clone(),
        }
    }

    pub fn resume(checkpoint: FrozenSelectionShadowCheckpoint) -> Result<Self> {
        if checkpoint.schema != "KEFI_FROZEN_SELECTION_SHADOW_CHECKPOINT_V1"
            || checkpoint.source_instance_id.is_empty()
            || checkpoint.tracked_wallet.is_empty()
            || checkpoint.warmup_ns == 0
        {
            bail!("INVALID_FROZEN_SELECTION_SHADOW_CHECKPOINT")
        }
        Ok(Self {
            source_instance_id: checkpoint.source_instance_id,
            continuity_epoch: checkpoint.continuity_epoch,
            tracked_wallet: checkpoint.tracked_wallet,
            warmup_ns: checkpoint.warmup_ns,
            source_started_ns: checkpoint.source_started_ns,
            last_sequence: checkpoint.last_sequence,
            pools: checkpoint.pools,
            observed_wallet_mints: checkpoint.observed_wallet_mints,
        })
    }

    /// The event identity and its authoritative pool identity must already be
    /// resolved by the canonical producer. A caller cannot supply a mint-only
    /// shortcut or bridge epochs here.
    pub fn observe(
        &mut self,
        event: PublicPoolEvent,
        identity: AuthoritativePoolIdentity,
    ) -> Result<Option<FrozenSelectionSnapshot>> {
        self.validate_event(&event, &identity)?;
        self.source_started_ns
            .get_or_insert(event.received_at_unix_ns);
        let teacher_buy = event.user == self.tracked_wallet && event.side == EventSide::Buy;
        let snapshot = if teacher_buy && !self.observed_wallet_mints.contains(&identity.mint) {
            self.pre_action_snapshot(&event, &identity)?
        } else {
            None
        };
        self.apply(event, identity)?;
        Ok(snapshot)
    }

    /// Advances the durable cursor for a frame that did not contain a
    /// supported semantic event. This keeps restart state bound to the same
    /// canonical prefix without inventing market-state transitions.
    pub fn observe_empty_frame(
        &mut self,
        source_instance_id: &str,
        continuity_epoch: u64,
        durable_sequence: u64,
        received_at_unix_ns: u128,
    ) -> Result<()> {
        if source_instance_id != self.source_instance_id
            || continuity_epoch != self.continuity_epoch
        {
            bail!("SOURCE_EPOCH_MISMATCH")
        }
        if durable_sequence <= self.last_sequence {
            bail!("DURABLE_SEQUENCE_REORDER_OR_DUPLICATE")
        }
        self.source_started_ns.get_or_insert(received_at_unix_ns);
        self.last_sequence = durable_sequence;
        Ok(())
    }

    /// Applies one already ordered PumpSwap transaction atomically. A tracked
    /// buy is snapshotted before *any* event from that transaction mutates
    /// public state, and a transaction choosing more than one mint is an
    /// explicit ambiguity rather than two artificial teacher decisions.
    pub fn observe_transaction(
        &mut self,
        events: Vec<(PublicPoolEvent, AuthoritativePoolIdentity)>,
    ) -> Result<Option<FrozenSelectionSnapshot>> {
        let first = events.first().context("EMPTY_PUMPSWAP_TRANSACTION")?;
        for (event, identity) in &events {
            self.validate_event(event, identity)?;
            if event.durable_sequence != first.0.durable_sequence
                || event.transaction_identity != first.0.transaction_identity
            {
                bail!("MIXED_TRANSACTION_INPUT")
            }
        }
        let teacher_buy_mints = events
            .iter()
            .filter(|(event, _)| event.user == self.tracked_wallet && event.side == EventSide::Buy)
            .map(|(_, identity)| identity.mint.clone())
            .collect::<HashSet<_>>();
        if teacher_buy_mints.len() > 1 {
            bail!("AMBIGUOUS_MULTI_MINT_TEACHER_SELECTION")
        }
        self.source_started_ns
            .get_or_insert(first.0.received_at_unix_ns);
        let snapshot = if let Some(mint) = teacher_buy_mints.iter().next() {
            let (_, identity) = events
                .iter()
                .find(|(event, identity)| {
                    event.user == self.tracked_wallet
                        && event.side == EventSide::Buy
                        && identity.mint == *mint
                })
                .context("TRACKED_BUY_IDENTITY_MISSING")?;
            if self.observed_wallet_mints.contains(mint) {
                None
            } else {
                self.pre_action_snapshot(&first.0, identity)?
            }
        } else {
            None
        };
        // The validation above permits only the first event to be newer than
        // the saved durable sequence. The rest are ordered within the same
        // source frame and must never be treated as a sequence replay.
        self.last_sequence = first.0.durable_sequence;
        for (event, identity) in events {
            self.apply_after_transaction_validation(event, identity)?;
        }
        Ok(snapshot)
    }

    fn validate_event(
        &self,
        event: &PublicPoolEvent,
        identity: &AuthoritativePoolIdentity,
    ) -> Result<()> {
        if event.source_instance_id != self.source_instance_id
            || event.continuity_epoch != self.continuity_epoch
        {
            bail!("SOURCE_EPOCH_MISMATCH")
        }
        if event.durable_sequence <= self.last_sequence {
            bail!("DURABLE_SEQUENCE_REORDER_OR_DUPLICATE")
        }
        if event.pool != identity.pool || identity.mint.is_empty() || identity.layout.is_empty() {
            bail!("AUTHORITATIVE_POOL_IDENTITY_MISMATCH")
        }
        Ok(())
    }

    fn pre_action_snapshot(
        &self,
        event: &PublicPoolEvent,
        identity: &AuthoritativePoolIdentity,
    ) -> Result<Option<FrozenSelectionSnapshot>> {
        let started = self
            .source_started_ns
            .context("SOURCE_WARMUP_NOT_STARTED")?;
        if event.received_at_unix_ns.saturating_sub(started) < self.warmup_ns {
            return Ok(None);
        }
        let candidates = self
            .pools
            .values()
            .map(|state| candidate(state, event.received_at_unix_ns))
            .collect::<Vec<_>>();
        if candidates.len() < 2
            || !candidates
                .iter()
                .any(|candidate| candidate.pool == identity.pool && candidate.mint == identity.mint)
        {
            return Ok(None);
        }
        Ok(Some(FrozenSelectionSnapshot {
            schema: "KEFI_FROZEN_SELECTION_PROSPECTIVE_SNAPSHOT_V1",
            source_instance_id: self.source_instance_id.clone(),
            continuity_epoch: self.continuity_epoch,
            teacher_transaction_identity: event.transaction_identity.clone(),
            teacher_durable_output_sequence: event.durable_sequence,
            teacher_received_at_unix_ns: event.received_at_unix_ns,
            chosen_pool: identity.pool.clone(),
            chosen_mint: identity.mint.clone(),
            frozen_feature_schema: FROZEN_SELECTION_FEATURES,
            candidates,
        }))
    }

    fn apply(&mut self, event: PublicPoolEvent, identity: AuthoritativePoolIdentity) -> Result<()> {
        self.last_sequence = event.durable_sequence;
        self.apply_after_transaction_validation(event, identity)
    }

    fn apply_after_transaction_validation(
        &mut self,
        event: PublicPoolEvent,
        identity: AuthoritativePoolIdentity,
    ) -> Result<()> {
        let state = self
            .pools
            .entry(event.pool.clone())
            .or_insert_with(|| PoolState {
                identity: identity.clone(),
                first_seen_ns: event.received_at_unix_ns,
                last_event_ns: event.received_at_unix_ns,
                last_sequence: event.durable_sequence,
                base_raw: event.pool_base_raw,
                quote_raw: event.pool_quote_raw,
                prior_quote_raw: None,
                fees: event.fee_tuple,
                last_buy_ns: None,
                flows: VecDeque::new(),
            });
        if state.identity.mint != identity.mint || state.identity.layout != identity.layout {
            bail!("POOL_IDENTITY_CONFLICT")
        }
        state.prior_quote_raw = Some(state.quote_raw);
        state.base_raw = event.pool_base_raw;
        state.quote_raw = event.pool_quote_raw;
        state.fees = event.fee_tuple;
        if event.side == EventSide::Buy {
            state.last_buy_ns = Some(event.received_at_unix_ns);
        }
        state.flows.push_back(Flow {
            at_ns: event.received_at_unix_ns,
            side: event.side,
            quote_raw: event.pool_quote_delta_raw,
        });
        while state.flows.front().is_some_and(|flow| {
            event.received_at_unix_ns.saturating_sub(flow.at_ns) > 60_000_000_000
        }) {
            state.flows.pop_front();
        }
        state.last_event_ns = event.received_at_unix_ns;
        state.last_sequence = event.durable_sequence;
        if event.user == self.tracked_wallet {
            self.observed_wallet_mints.insert(identity.mint);
        }
        Ok(())
    }
}

fn candidate(state: &PoolState, at_ns: u128) -> FrozenCandidate {
    let mut buys = [0_u64; 3];
    let mut buy_flow = [0_u64; 3];
    let mut sells = 0_u64;
    let mut sell_flow = 0_u64;
    for flow in &state.flows {
        let age = at_ns.saturating_sub(flow.at_ns);
        for (index, window) in [5_u128, 20, 60].iter().enumerate() {
            if age <= *window * 1_000_000_000 && flow.side == EventSide::Buy {
                buys[index] += 1;
                buy_flow[index] = buy_flow[index].saturating_add(flow.quote_raw);
            }
        }
        if age <= 60_000_000_000 && flow.side == EventSide::Sell {
            sells += 1;
            sell_flow = sell_flow.saturating_add(flow.quote_raw);
        }
    }
    FrozenCandidate {
        pool: state.identity.pool.clone(),
        mint: state.identity.mint.clone(),
        quote_reserve_raw: state.quote_raw,
        base_reserve_raw: state.base_raw,
        fee_tuple: state.fees,
        observed_pool_age_seconds: (at_ns.saturating_sub(state.first_seen_ns) / 1_000_000_000)
            as u64,
        seconds_since_prior_observed_buy: state
            .last_buy_ns
            .map(|value| (at_ns.saturating_sub(value) / 1_000_000_000) as u64),
        prior_pre_pool_quote_raw: state.prior_quote_raw,
        buy_count_5s: buys[0],
        buy_count_20s: buys[1],
        buy_count_60s: buys[2],
        buy_quote_flow_5s: buy_flow[0],
        buy_quote_flow_20s: buy_flow[1],
        buy_quote_flow_60s: buy_flow[2],
        no_prior_buy: state.last_buy_ns.is_none(),
        sell_count_60s: sells,
        sell_quote_flow_60s: sell_flow,
        first_public_observation_unix_ns: state.first_seen_ns,
        last_event_unix_ns: state.last_event_ns,
        last_durable_output_sequence: state.last_sequence,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;
    fn identity(pool: &str, mint: &str) -> AuthoritativePoolIdentity {
        AuthoritativePoolIdentity {
            pool: pool.into(),
            mint: mint.into(),
            layout: "PUMPSWAP_POOL_V1".into(),
        }
    }
    fn event(sequence: u64, at: u128, pool: &str, user: &str, side: EventSide) -> PublicPoolEvent {
        PublicPoolEvent {
            source_instance_id: "source".into(),
            continuity_epoch: 7,
            durable_sequence: sequence,
            received_at_unix_ns: at,
            transaction_identity: format!("sig-{sequence}"),
            pool: pool.into(),
            user: user.into(),
            side,
            pool_base_raw: 100 + sequence,
            pool_quote_raw: 200 + sequence,
            pool_quote_delta_raw: 10,
            fee_tuple: (1, 2, 3, 4, 5),
        }
    }
    #[test]
    fn snapshots_before_teacher_buy_and_preserves_frozen_schema() {
        let mut shadow = FrozenSelectionShadow::new("source".into(), 7, "kefi".into(), 60).unwrap();
        assert!(shadow
            .observe(
                event(1, 1_000_000_000, "a", "other", EventSide::Buy),
                identity("a", "mint-a")
            )
            .unwrap()
            .is_none());
        assert!(shadow
            .observe(
                event(2, 2_000_000_000, "b", "other", EventSide::Buy),
                identity("b", "mint-b")
            )
            .unwrap()
            .is_none());
        let snapshot = shadow
            .observe(
                event(3, 62_000_000_000, "a", "kefi", EventSide::Buy),
                identity("a", "mint-a"),
            )
            .unwrap()
            .unwrap();
        assert_eq!(snapshot.frozen_feature_schema, FROZEN_SELECTION_FEATURES);
        let chosen = snapshot
            .candidates
            .iter()
            .find(|row| row.pool == "a")
            .unwrap();
        assert_eq!(chosen.last_durable_output_sequence, 1);
        assert_eq!(chosen.quote_reserve_raw, 201);
    }
    #[test]
    fn repeated_wallet_mint_and_epoch_or_sequence_breaks_do_not_admit() {
        let mut shadow = FrozenSelectionShadow::new("source".into(), 7, "kefi".into(), 1).unwrap();
        shadow
            .observe(
                event(1, 1, "a", "other", EventSide::Buy),
                identity("a", "mint-a"),
            )
            .unwrap();
        shadow
            .observe(
                event(2, 2, "b", "other", EventSide::Buy),
                identity("b", "mint-b"),
            )
            .unwrap();
        shadow
            .observe(
                event(3, 2_000_000_000, "a", "kefi", EventSide::Buy),
                identity("a", "mint-a"),
            )
            .unwrap();
        assert!(shadow
            .observe(
                event(4, 3_000_000_000, "a", "kefi", EventSide::Buy),
                identity("a", "mint-a")
            )
            .unwrap()
            .is_none());
        let mut bad = event(5, 4_000_000_000, "a", "other", EventSide::Sell);
        bad.continuity_epoch = 8;
        assert!(shadow.observe(bad, identity("a", "mint-a")).is_err());
    }

    #[test]
    fn multi_event_transaction_snapshots_before_all_own_events_and_rejects_multi_mint_buy() {
        let mut shadow = FrozenSelectionShadow::new("source".into(), 7, "kefi".into(), 1).unwrap();
        shadow
            .observe(
                event(1, 1, "a", "other", EventSide::Buy),
                identity("a", "mint-a"),
            )
            .unwrap();
        shadow
            .observe(
                event(2, 2, "b", "other", EventSide::Buy),
                identity("b", "mint-b"),
            )
            .unwrap();
        let snapshot = shadow
            .observe_transaction(vec![
                (
                    event(3, 2_000_000_000, "a", "kefi", EventSide::Buy),
                    identity("a", "mint-a"),
                ),
                (
                    event(3, 2_000_000_000, "a", "kefi", EventSide::Sell),
                    identity("a", "mint-a"),
                ),
            ])
            .unwrap()
            .unwrap();
        assert_eq!(snapshot.teacher_durable_output_sequence, 3);
        assert_eq!(
            snapshot
                .candidates
                .iter()
                .find(|row| row.pool == "a")
                .unwrap()
                .last_durable_output_sequence,
            1
        );
        let mut other = event(4, 3_000_000_000, "b", "kefi", EventSide::Buy);
        other.transaction_identity = "sig-multi".into();
        let mut a = event(4, 3_000_000_000, "a", "kefi", EventSide::Buy);
        a.transaction_identity = "sig-multi".into();
        assert!(shadow
            .observe_transaction(vec![
                (a, identity("a", "mint-a")),
                (other, identity("b", "mint-b"))
            ])
            .is_err());
    }

    #[test]
    fn snapshot_writer_records_the_preaction_contract_without_scores() {
        let temp = tempdir().unwrap();
        let mut shadow = FrozenSelectionShadow::new("source".into(), 7, "kefi".into(), 1).unwrap();
        shadow
            .observe(
                event(1, 1, "a", "other", EventSide::Buy),
                identity("a", "mint-a"),
            )
            .unwrap();
        shadow
            .observe(
                event(2, 2, "b", "other", EventSide::Buy),
                identity("b", "mint-b"),
            )
            .unwrap();
        let snapshot = shadow
            .observe(
                event(3, 2_000_000_000, "a", "kefi", EventSide::Buy),
                identity("a", "mint-a"),
            )
            .unwrap()
            .unwrap();
        let receipt = SnapshotWriter::open(temp.path())
            .unwrap()
            .write(&snapshot)
            .unwrap();
        assert!(Path::new(&receipt.snapshot_path).exists());
        assert!(receipt.pre_action_only);
        assert!(receipt.teacher_event_excluded_from_state);
        assert!(receipt.authoritative_pool_identity);
        assert_eq!(receipt.frozen_feature_schema, FROZEN_SELECTION_FEATURES);
    }

    #[test]
    fn checkpoint_resume_preserves_wallet_history_and_public_state() {
        let mut shadow = FrozenSelectionShadow::new("source".into(), 7, "kefi".into(), 1).unwrap();
        shadow
            .observe(
                event(1, 1, "a", "other", EventSide::Buy),
                identity("a", "mint-a"),
            )
            .unwrap();
        shadow
            .observe(
                event(2, 2, "b", "other", EventSide::Buy),
                identity("b", "mint-b"),
            )
            .unwrap();
        let serialized = serde_json::to_vec(&shadow.checkpoint()).unwrap();
        let resumed =
            FrozenSelectionShadow::resume(serde_json::from_slice(&serialized).unwrap()).unwrap();
        assert_eq!(resumed.last_sequence, 2);
        assert_eq!(resumed.pools.len(), 2);
        assert!(resumed.observed_wallet_mints.is_empty());
    }

    #[test]
    fn empty_frames_advance_restart_cursor_without_creating_market_state() {
        let mut shadow = FrozenSelectionShadow::new("source".into(), 7, "kefi".into(), 1).unwrap();
        shadow.observe_empty_frame("source", 7, 4, 10).unwrap();
        assert_eq!(shadow.last_sequence, 4);
        assert!(shadow.pools.is_empty());
        assert!(shadow.observe_empty_frame("source", 7, 4, 11).is_err());
    }
}
