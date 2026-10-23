use std::collections::{HashMap, HashSet, VecDeque};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::{
    parity_capsule::CandidateCapsule,
    parity_core::{
        stable_hash, CandidateFeatures, CanonicalState, ExecutionModel, MayhemEvidence,
        MayhemStatus, ProviderSource, Venue, PARITY_SCHEMA_VERSION,
    },
};

const LAMPORTS_PER_SOL: u64 = 1_000_000_000;
const PUMP_FUN_TRADE_FEE_BPS: u128 = 125;
const PUMPFUN_INITIAL_VIRTUAL_SOL_RESERVES: u64 = 30_000_000_000;
const PUMPFUN_INITIAL_VIRTUAL_TOKEN_RESERVES: u64 = 1_073_000_000_000_000;
const PUMPFUN_TOKEN_TOTAL_SUPPLY: u64 = 1_000_000_000_000_000;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ObservationSide {
    Create,
    Buy,
    Sell,
    State,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TeacherImpulseObservation {
    pub source: ProviderSource,
    #[serde(default)]
    pub observation_id: Option<String>,
    #[serde(default)]
    pub raw_record_identity: Option<String>,
    #[serde(default)]
    pub raw_record_hash: Option<String>,
    #[serde(default)]
    pub raw_record: Option<Value>,
    #[serde(default)]
    pub source_slot: Option<u64>,
    #[serde(default)]
    pub source_transaction_index: Option<u32>,
    #[serde(default)]
    pub provider_sequence: Option<String>,
    pub local_receive_ts_ms: u64,
    #[serde(default)]
    pub decode_complete_ts_ms: Option<u64>,
    #[serde(default)]
    pub transaction_signature: Option<String>,
    pub mint: String,
    #[serde(default)]
    pub token_program: Option<String>,
    #[serde(default)]
    pub source_ix_data_base64: Option<String>,
    #[serde(default)]
    pub creation_proven: Option<bool>,
    #[serde(default)]
    pub creation_ts_ms: Option<u64>,
    pub side: ObservationSide,
    #[serde(default)]
    pub quote_sol: Option<f64>,
    #[serde(default)]
    pub quote_lamports: Option<u64>,
    #[serde(default)]
    pub sell_quote_lamports: Option<u64>,
    #[serde(default)]
    pub sell_token_amount_raw: Option<u64>,
    #[serde(default)]
    pub wallet: Option<String>,
    #[serde(default)]
    pub canonical_state: Option<CanonicalState>,
    #[serde(default)]
    pub mayhem: Option<MayhemEvidence>,
    #[serde(default)]
    pub execution: Option<ExecutionModel>,
    #[serde(default)]
    pub extra: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TeacherImpulseEmitterConfig {
    pub strategy: String,
    pub window_ms: u64,
    pub max_mints: usize,
    pub sol_price_usd: f64,
    pub default_position_size_lamports: u64,
    pub default_priority_fee_lamports: u64,
    pub default_jito_tip_lamports: u64,
    pub default_slippage_bps: u64,
}

impl Default for TeacherImpulseEmitterConfig {
    fn default() -> Self {
        Self {
            strategy: PARITY_SCHEMA_VERSION.to_string(),
            window_ms: 60_000,
            max_mints: 4_096,
            sol_price_usd: 160.0,
            default_position_size_lamports: 1_000_000,
            default_priority_fee_lamports: 5_000,
            default_jito_tip_lamports: 10_000,
            default_slippage_bps: 500,
        }
    }
}

#[derive(Debug, Clone)]
struct FlowPoint {
    ts_ms: u64,
    source_slot: Option<u64>,
    source_transaction_index: Option<u32>,
    instruction_index: Option<u32>,
    side: ObservationSide,
    quote_sol: f64,
    quote_lamports: Option<u64>,
    sell_quote_lamports: Option<u64>,
    sell_token_amount_raw: Option<u64>,
    wallet: Option<String>,
}

#[derive(Debug, Clone)]
struct MintAccumulator {
    mint: String,
    token_program: Option<String>,
    source_ix_data_base64: Option<String>,
    creation_proven: Option<bool>,
    creation_ts_ms: Option<u64>,
    first_seen_ts_ms: u64,
    last_provider: ProviderSource,
    last_raw_record_identity: Option<String>,
    last_raw_record_hash: Option<String>,
    last_raw_record: Option<Value>,
    last_source_slot: Option<u64>,
    last_source_transaction_index: Option<u32>,
    last_provider_sequence: Option<String>,
    last_receive_ts_ms: u64,
    last_decode_complete_ts_ms: Option<u64>,
    last_transaction_signature: Option<String>,
    last_canonical_state: Option<CanonicalState>,
    last_mayhem: Option<MayhemEvidence>,
    last_execution: Option<ExecutionModel>,
    last_extra: Value,
    flows: VecDeque<FlowPoint>,
}

pub struct TeacherImpulseEmitter {
    config: TeacherImpulseEmitterConfig,
    order: VecDeque<String>,
    mints: HashMap<String, MintAccumulator>,
}

impl TeacherImpulseEmitter {
    pub fn new(config: TeacherImpulseEmitterConfig) -> Self {
        Self {
            config,
            order: VecDeque::new(),
            mints: HashMap::new(),
        }
    }

    pub fn ingest(&mut self, observation: TeacherImpulseObservation) -> Result<CandidateCapsule> {
        validate_observation(&observation)?;
        self.evict_if_needed(&observation.mint);

        let config = self.config.clone();
        let now = observation.local_receive_ts_ms;
        let mint = observation.mint.clone();
        let accumulator = self.mints.entry(mint.clone()).or_insert_with(|| {
            self.order.push_back(mint.clone());
            MintAccumulator {
                mint: mint.clone(),
                token_program: None,
                source_ix_data_base64: None,
                creation_proven: None,
                creation_ts_ms: None,
                first_seen_ts_ms: now,
                last_provider: observation.source.clone(),
                last_raw_record_identity: None,
                last_raw_record_hash: None,
                last_raw_record: None,
                last_source_slot: None,
                last_source_transaction_index: None,
                last_provider_sequence: None,
                last_receive_ts_ms: now,
                last_decode_complete_ts_ms: None,
                last_transaction_signature: None,
                last_canonical_state: None,
                last_mayhem: None,
                last_execution: None,
                last_extra: json!({}),
                flows: VecDeque::new(),
            }
        });

        accumulator.last_provider = observation.source;
        accumulator.last_raw_record_identity = observation.raw_record_identity;
        accumulator.last_raw_record_hash = observation.raw_record_hash;
        accumulator.last_raw_record = observation.raw_record;
        accumulator.last_source_slot = observation.source_slot;
        accumulator.last_source_transaction_index = observation.source_transaction_index;
        accumulator.last_provider_sequence = observation.provider_sequence;
        accumulator.last_receive_ts_ms = observation.local_receive_ts_ms;
        accumulator.last_decode_complete_ts_ms = observation.decode_complete_ts_ms;
        accumulator.last_transaction_signature = observation.transaction_signature;
        let instruction_index = observation
            .extra
            .get("instructionIndex")
            .and_then(|value| value.as_u64())
            .and_then(|value| u32::try_from(value).ok());
        accumulator.last_extra = normalize_extra(observation.extra);

        if observation.token_program.is_some() {
            accumulator.token_program = observation.token_program;
        }
        if observation.source_ix_data_base64.is_some() {
            accumulator.source_ix_data_base64 = observation.source_ix_data_base64;
        }
        if observation.creation_proven.is_some() {
            accumulator.creation_proven = observation.creation_proven;
        }
        if observation.creation_ts_ms.is_some() {
            accumulator.creation_ts_ms = observation.creation_ts_ms;
        }
        if observation.canonical_state.is_some() {
            accumulator.last_canonical_state = observation.canonical_state;
        }
        if observation.mayhem.is_some() {
            accumulator.last_mayhem = observation.mayhem;
        }
        if observation.execution.is_some() {
            accumulator.last_execution = observation.execution;
        }

        if matches!(
            observation.side,
            ObservationSide::Buy | ObservationSide::Sell | ObservationSide::Create
        ) {
            accumulator.flows.push_back(FlowPoint {
                ts_ms: now,
                source_slot: observation.source_slot,
                source_transaction_index: observation.source_transaction_index,
                instruction_index,
                side: observation.side,
                quote_sol: observation
                    .quote_sol
                    .or_else(|| {
                        observation
                            .quote_lamports
                            .or(observation.sell_quote_lamports)
                            .map(|lamports| lamports as f64 / LAMPORTS_PER_SOL as f64)
                    })
                    .unwrap_or(0.0)
                    .max(0.0),
                quote_lamports: observation.quote_lamports,
                sell_quote_lamports: observation.sell_quote_lamports,
                sell_token_amount_raw: observation.sell_token_amount_raw,
                wallet: observation.wallet,
            });
        }

        prune_old_flows(&mut accumulator.flows, now, self.config.window_ms);
        Ok(build_capsule(&config, accumulator))
    }

    fn evict_if_needed(&mut self, incoming_mint: &str) {
        if self.mints.contains_key(incoming_mint) {
            return;
        }
        while self.mints.len() >= self.config.max_mints {
            let Some(oldest) = self.order.pop_front() else {
                return;
            };
            self.mints.remove(&oldest);
        }
    }
}

fn build_capsule(
    config: &TeacherImpulseEmitterConfig,
    accumulator: &MintAccumulator,
) -> CandidateCapsule {
    let replay_state = replay_curve_state(&accumulator.flows, config.sol_price_usd);
    let derived_canonical_state = replay_state.as_ref().map(|state| {
        let state_identity = format!(
            "native-shredstream-replay:{}:{}:{}",
            accumulator.mint, state.virtual_sol_reserves, state.virtual_token_reserves
        );
        let state_hash = stable_hash(&json!({
            "mint": accumulator.mint,
            "virtualSolReserves": state.virtual_sol_reserves,
            "virtualTokenReserves": state.virtual_token_reserves,
            "tokenTotalSupply": PUMPFUN_TOKEN_TOTAL_SUPPLY,
            "marketCapUsd": state.market_cap_usd,
            "complete": false,
        }));
        CanonicalState {
            venue: Venue::PumpFun,
            source: accumulator.last_provider.clone(),
            state_identity,
            state_hash,
            observed_at_ms: accumulator.last_receive_ts_ms,
            source_slot: accumulator.last_source_slot,
            market_cap_usd: Some(state.market_cap_usd),
            curve_complete: Some(false),
            unavailable_reason: None,
        }
    });
    let canonical_state = accumulator
        .last_canonical_state
        .clone()
        .or(derived_canonical_state);
    let expected_tokens_out_raw = replay_state.as_ref().and_then(|state| {
        quote_buy_tokens_out_raw(
            state.virtual_sol_reserves as u128,
            state.virtual_token_reserves as u128,
            config.default_position_size_lamports as u128,
        )
    });
    let derived_min_tokens_out_raw = expected_tokens_out_raw.map(|expected| {
        expected.saturating_mul(10_000u128.saturating_sub(config.default_slippage_bps as u128))
            / 10_000u128
    });
    let raw_record_hash = accumulator
        .last_raw_record_hash
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| accumulator.last_raw_record.as_ref().map(stable_hash));
    let raw_record_identity = accumulator
        .last_raw_record_identity
        .clone()
        .or_else(|| Some(inferred_identity(accumulator, raw_record_hash.as_deref())));
    let execution = accumulator
        .last_execution
        .clone()
        .unwrap_or_else(|| ExecutionModel {
            position_size_lamports: config.default_position_size_lamports,
            priority_fee_lamports: config.default_priority_fee_lamports,
            jito_tip_lamports: config.default_jito_tip_lamports,
            other_tip_lamports: 0,
            slippage_bps: config.default_slippage_bps,
            expected_tokens_out_raw,
            min_tokens_out_raw: derived_min_tokens_out_raw,
        });
    let features = build_features(accumulator, canonical_state.as_ref());

    CandidateCapsule {
        schema_version: Some(PARITY_SCHEMA_VERSION.to_string()),
        strategy: Some(config.strategy.clone()),
        source: accumulator.last_provider.clone(),
        raw_record_identity,
        raw_record_hash,
        raw_record: accumulator.last_raw_record.clone(),
        source_slot: accumulator.last_source_slot,
        source_transaction_index: accumulator.last_source_transaction_index,
        provider_sequence: accumulator.last_provider_sequence.clone(),
        local_receive_ts_ms: accumulator.last_receive_ts_ms,
        decode_complete_ts_ms: accumulator.last_decode_complete_ts_ms,
        candidate_ready_ts_ms: Some(
            accumulator
                .last_decode_complete_ts_ms
                .unwrap_or(accumulator.last_receive_ts_ms)
                .max(accumulator.last_receive_ts_ms),
        ),
        transaction_signature: accumulator.last_transaction_signature.clone(),
        candidate: features,
        canonical_state,
        mayhem: accumulator.last_mayhem.clone().unwrap_or(MayhemEvidence {
            status: MayhemStatus::Unknown,
            reason: Some("mayhem_unproven_at_decision_boundary".to_string()),
            checked_at_ms: None,
        }),
        execution,
        extra_decision_fields: accumulator.last_extra.clone(),
    }
}

fn build_features(
    accumulator: &MintAccumulator,
    canonical_state: Option<&CanonicalState>,
) -> CandidateFeatures {
    let now = accumulator.last_receive_ts_ms;
    let buy_points: Vec<&FlowPoint> = accumulator
        .flows
        .iter()
        .filter(|point| point.side == ObservationSide::Buy)
        .collect();
    let sell_points: Vec<&FlowPoint> = accumulator
        .flows
        .iter()
        .filter(|point| point.side == ObservationSide::Sell)
        .collect();
    let buy_sol: f64 = buy_points.iter().map(|point| point.quote_sol).sum();
    let sell_sol: f64 = sell_points.iter().map(|point| point.quote_sol).sum();
    let net_buy_sol_60 = buy_sol - sell_sol;
    let sell_pressure_60 = if buy_sol + sell_sol > 0.0 {
        Some(sell_sol / (buy_sol + sell_sol))
    } else {
        None
    };
    let buy_count_60 = buy_points.len() as u32;
    let first_buy_sol = buy_points
        .iter()
        .min_by_key(|point| point.ts_ms)
        .map(|point| point.quote_sol);
    let largest_buy_sol = buy_points
        .iter()
        .map(|point| point.quote_sol)
        .reduce(f64::max);
    let dominant_buy_share_60 = largest_buy_sol
        .filter(|_| buy_sol > 0.0)
        .map(|largest| largest / buy_sol);
    let first_buyer_quote_share_bps = first_buy_sol
        .filter(|_| buy_sol > 0.0)
        .map(|first| ratio_to_bps(first / buy_sol));
    let largest_wallet_quote_share_bps = largest_buy_sol
        .filter(|_| buy_sol > 0.0)
        .map(|largest| ratio_to_bps(largest / buy_sol));
    let unique_wallets_60 = unique_buy_wallets(&buy_points);

    CandidateFeatures {
        mint: accumulator.mint.clone(),
        token_program: accumulator.token_program.clone(),
        source_ix_data_base64: accumulator.source_ix_data_base64.clone(),
        creation_proven: accumulator.creation_proven,
        creation_age_ms: accumulator
            .creation_ts_ms
            .or(Some(accumulator.first_seen_ts_ms))
            .and_then(|created| now.checked_sub(created)),
        market_cap_usd: canonical_state.and_then(|state| state.market_cap_usd),
        net_buy_sol_60: Some(net_buy_sol_60),
        buy_count_100ms: Some(count_buys_since(&buy_points, now, 100)),
        buy_count_250ms: Some(count_buys_since(&buy_points, now, 250)),
        buy_count_500ms: Some(count_buys_since(&buy_points, now, 500)),
        buy_count_1000ms: Some(count_buys_since(&buy_points, now, 1_000)),
        buy_count_60: Some(buy_count_60),
        sell_pressure_60,
        dominant_buy_share_60,
        unique_wallets_60: Some(unique_wallets_60),
        unique_per_buy_60: if buy_count_60 > 0 {
            Some(unique_wallets_60 as f64 / buy_count_60 as f64)
        } else {
            None
        },
        first_buyer_quote_share_bps,
        largest_wallet_quote_share_bps,
    }
}

#[derive(Debug, Clone)]
struct ReplayedCurveState {
    virtual_sol_reserves: u64,
    virtual_token_reserves: u64,
    market_cap_usd: f64,
}

fn replay_curve_state(
    flows: &VecDeque<FlowPoint>,
    sol_price_usd: f64,
) -> Option<ReplayedCurveState> {
    if !sol_price_usd.is_finite() || sol_price_usd <= 0.0 {
        return None;
    }
    let mut virtual_sol_reserves = PUMPFUN_INITIAL_VIRTUAL_SOL_RESERVES as u128;
    let mut virtual_token_reserves = PUMPFUN_INITIAL_VIRTUAL_TOKEN_RESERVES as u128;
    let mut ordered = flows.iter().collect::<Vec<_>>();
    ordered.sort_by_key(|point| {
        (
            point.source_slot.unwrap_or_default(),
            point.source_transaction_index.unwrap_or_default(),
            point.instruction_index.unwrap_or_default(),
            point.ts_ms,
        )
    });
    for point in ordered {
        match point.side {
            ObservationSide::Buy => {
                let quote = point.quote_lamports? as u128;
                let fee = quote.checked_mul(PUMP_FUN_TRADE_FEE_BPS)? / 10_000u128;
                let reserve_quote = quote.checked_sub(fee)?;
                let tokens_out =
                    quote_buy_tokens_out_raw(virtual_sol_reserves, virtual_token_reserves, quote)?;
                virtual_sol_reserves = virtual_sol_reserves.checked_add(reserve_quote)?;
                virtual_token_reserves = virtual_token_reserves.checked_sub(tokens_out)?;
            }
            ObservationSide::Sell => {
                let tokens_in = point.sell_token_amount_raw? as u128;
                if tokens_in == 0 {
                    return None;
                }
                let pre_sol = virtual_sol_reserves;
                let pre_tokens = virtual_token_reserves;
                let post_tokens = pre_tokens.checked_add(tokens_in)?;
                let invariant = pre_sol.checked_mul(pre_tokens)?;
                let post_sol = invariant / post_tokens;
                virtual_sol_reserves = post_sol;
                virtual_token_reserves = post_tokens;
            }
            ObservationSide::Create | ObservationSide::State | ObservationSide::Unknown => {}
        }
    }
    if virtual_sol_reserves == 0 || virtual_token_reserves == 0 {
        return None;
    }
    let market_cap_sol = (virtual_sol_reserves as f64 / LAMPORTS_PER_SOL as f64)
        * (PUMPFUN_TOKEN_TOTAL_SUPPLY as f64 / virtual_token_reserves as f64);
    let market_cap_usd = market_cap_sol * sol_price_usd;
    market_cap_usd.is_finite().then_some(ReplayedCurveState {
        virtual_sol_reserves: u64::try_from(virtual_sol_reserves).ok()?,
        virtual_token_reserves: u64::try_from(virtual_token_reserves).ok()?,
        market_cap_usd,
    })
}

fn quote_buy_tokens_out_raw(
    virtual_sol_reserves: u128,
    virtual_token_reserves: u128,
    sol_in_lamports: u128,
) -> Option<u128> {
    if virtual_sol_reserves == 0 || virtual_token_reserves == 0 || sol_in_lamports == 0 {
        return None;
    }
    let fee = (sol_in_lamports * PUMP_FUN_TRADE_FEE_BPS) / 10_000u128;
    let sol_after_fee = sol_in_lamports.checked_sub(fee)?;
    if sol_after_fee == 0 {
        return None;
    }
    let k = virtual_sol_reserves.checked_mul(virtual_token_reserves)?;
    let new_sol = virtual_sol_reserves.checked_add(sol_after_fee)?;
    let new_tokens = k / new_sol;
    if new_tokens >= virtual_token_reserves {
        return None;
    }
    Some(virtual_token_reserves - new_tokens)
}

pub fn parse_observation_line(line: &str, line_no: u64) -> Result<TeacherImpulseObservation> {
    serde_json::from_str(line)
        .with_context(|| format!("parse TeacherImpulseObservation line {line_no}"))
}

fn validate_observation(observation: &TeacherImpulseObservation) -> Result<()> {
    if observation.local_receive_ts_ms == 0 {
        anyhow::bail!("local_receive_ts_ms must be non-zero");
    }
    if observation.mint.trim().is_empty() {
        anyhow::bail!("mint is required");
    }
    Ok(())
}

fn prune_old_flows(flows: &mut VecDeque<FlowPoint>, now: u64, window_ms: u64) {
    while flows
        .front()
        .is_some_and(|point| point.ts_ms.saturating_add(window_ms) < now)
    {
        flows.pop_front();
    }
}

fn count_buys_since(points: &[&FlowPoint], now: u64, window_ms: u64) -> u32 {
    points
        .iter()
        .filter(|point| point.ts_ms.saturating_add(window_ms) >= now)
        .count() as u32
}

fn unique_buy_wallets(points: &[&FlowPoint]) -> u32 {
    points
        .iter()
        .filter_map(|point| point.wallet.as_deref())
        .collect::<HashSet<_>>()
        .len() as u32
}

fn ratio_to_bps(value: f64) -> u32 {
    (value.clamp(0.0, 1.0) * 10_000.0).round() as u32
}

fn normalize_extra(value: Value) -> Value {
    if value.is_null() {
        json!({})
    } else {
        value
    }
}

fn inferred_identity(accumulator: &MintAccumulator, raw_record_hash: Option<&str>) -> String {
    let source = serde_json::to_string(&accumulator.last_provider)
        .unwrap_or_else(|_| "\"unknown\"".to_string())
        .trim_matches('"')
        .to_string();
    format!(
        "source:{source}:slot:{}:tx_index:{}:sig:{}:mint:{}:raw:{}",
        accumulator
            .last_source_slot
            .map(|value| value.to_string())
            .unwrap_or_else(|| "unknown".to_string()),
        accumulator
            .last_source_transaction_index
            .map(|value| value.to_string())
            .unwrap_or_else(|| "unknown".to_string()),
        accumulator
            .last_transaction_signature
            .as_deref()
            .unwrap_or("unknown"),
        accumulator.mint,
        raw_record_hash.unwrap_or("")
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        parity_capsule::decision_input_from_capsule,
        parity_core::{evaluate_decision, DecisionStatus, ExecutionModel, Venue},
    };

    fn observation(
        ts: u64,
        side: ObservationSide,
        quote_sol: f64,
        wallet: &str,
    ) -> TeacherImpulseObservation {
        TeacherImpulseObservation {
            source: ProviderSource::ShredStream,
            observation_id: None,
            raw_record_identity: None,
            raw_record_hash: None,
            raw_record: Some(json!({
                "slot": 22,
                "ts": ts,
                "side": side,
                "wallet": wallet
            })),
            source_slot: Some(22),
            source_transaction_index: Some((ts - 1_000) as u32),
            provider_sequence: Some(format!("shred:22:{ts}")),
            local_receive_ts_ms: ts,
            decode_complete_ts_ms: Some(ts + 1),
            transaction_signature: Some(format!("sig{ts}")),
            mint: "MintEmitter111111111111111111111111111111111".to_string(),
            token_program: Some("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA".to_string()),
            source_ix_data_base64: Some("AQID".to_string()),
            creation_proven: Some(true),
            creation_ts_ms: Some(1_000),
            side: side.clone(),
            quote_sol: Some(quote_sol),
            quote_lamports: if side == ObservationSide::Buy {
                Some((quote_sol * LAMPORTS_PER_SOL as f64) as u64)
            } else {
                None
            },
            sell_quote_lamports: if side == ObservationSide::Sell {
                Some((quote_sol * LAMPORTS_PER_SOL as f64) as u64)
            } else {
                None
            },
            sell_token_amount_raw: if side == ObservationSide::Sell {
                Some(1_000_000)
            } else {
                None
            },
            wallet: Some(wallet.to_string()),
            canonical_state: Some(CanonicalState {
                venue: Venue::PumpFun,
                source: ProviderSource::Yellowstone,
                state_identity: "curve:emitter".to_string(),
                state_hash: format!("statehash{ts}"),
                observed_at_ms: ts,
                source_slot: Some(22),
                market_cap_usd: Some(2_400.0),
                curve_complete: Some(false),
                unavailable_reason: None,
            }),
            mayhem: Some(MayhemEvidence {
                status: MayhemStatus::Clean,
                reason: None,
                checked_at_ms: Some(ts),
            }),
            execution: None,
            extra: Value::Null,
        }
    }

    #[test]
    fn bounded_emitter_builds_candidate_capsule_from_flow() {
        let mut emitter = TeacherImpulseEmitter::new(TeacherImpulseEmitterConfig::default());
        emitter
            .ingest(observation(1_010, ObservationSide::Buy, 1.0, "w1"))
            .unwrap();
        emitter
            .ingest(observation(1_020, ObservationSide::Buy, 2.0, "w2"))
            .unwrap();
        let mut executable_observation = observation(1_030, ObservationSide::Buy, 3.0, "w3");
        executable_observation.execution = Some(ExecutionModel {
            position_size_lamports: 1_000_000,
            priority_fee_lamports: 5_000,
            jito_tip_lamports: 10_000,
            other_tip_lamports: 0,
            slippage_bps: 500,
            expected_tokens_out_raw: Some(10_000_000_000),
            min_tokens_out_raw: Some(9_500_000_000),
        });
        let capsule = emitter.ingest(executable_observation).unwrap();

        assert_eq!(capsule.candidate.buy_count_100ms, Some(3));
        assert_eq!(capsule.candidate.net_buy_sol_60, Some(6.0));
        assert_eq!(capsule.candidate.unique_wallets_60, Some(3));
        assert_eq!(
            capsule.candidate.largest_wallet_quote_share_bps,
            Some(5_000)
        );

        let input = decision_input_from_capsule(capsule).unwrap();
        let receipt = evaluate_decision(&input, &Default::default());
        assert_eq!(receipt.status, DecisionStatus::Accepted);
    }

    #[test]
    fn native_raw_flow_replay_supplies_executable_proof() {
        let mut emitter = TeacherImpulseEmitter::new(TeacherImpulseEmitterConfig::default());
        let mut first = observation(1_010, ObservationSide::Buy, 1.0, "w1");
        first.canonical_state = None;
        first.execution = None;
        let mut second = observation(1_020, ObservationSide::Buy, 1.0, "w2");
        second.canonical_state = None;
        second.execution = None;

        emitter.ingest(first).unwrap();
        let capsule = emitter.ingest(second).unwrap();
        assert!(capsule.canonical_state.is_some());
        assert!(capsule.candidate.market_cap_usd.is_some());
        assert!(capsule.execution.expected_tokens_out_raw.is_some());
        assert!(capsule.execution.min_tokens_out_raw.is_some());

        let input = decision_input_from_capsule(capsule).unwrap();
        let receipt = evaluate_decision(&input, &Default::default());
        assert_eq!(receipt.status, DecisionStatus::Accepted);
    }

    #[test]
    fn bounded_emitter_preserves_mayhem_missing_as_unavailable() {
        let mut emitter = TeacherImpulseEmitter::new(TeacherImpulseEmitterConfig::default());
        let mut obs = observation(1_010, ObservationSide::Buy, 2.0, "w1");
        obs.mayhem = None;
        let capsule = emitter.ingest(obs).unwrap();
        let input = decision_input_from_capsule(capsule).unwrap();
        let receipt = evaluate_decision(&input, &Default::default());
        assert_eq!(receipt.status, DecisionStatus::Unavailable);
        assert!(receipt
            .reason_codes
            .contains(&"mayhem_fail_closed".to_string()));
    }

    #[test]
    fn bounded_emitter_drops_old_flow_from_feature_window() {
        let config = TeacherImpulseEmitterConfig {
            window_ms: 100,
            ..TeacherImpulseEmitterConfig::default()
        };
        let mut emitter = TeacherImpulseEmitter::new(config);
        emitter
            .ingest(observation(1_000, ObservationSide::Buy, 10.0, "early"))
            .unwrap();
        let capsule = emitter
            .ingest(observation(1_250, ObservationSide::Buy, 1.0, "late"))
            .unwrap();

        assert_eq!(capsule.candidate.buy_count_60, Some(1));
        assert_eq!(capsule.candidate.net_buy_sol_60, Some(1.0));
        assert_eq!(capsule.candidate.first_buyer_quote_share_bps, Some(10_000));
    }
}
